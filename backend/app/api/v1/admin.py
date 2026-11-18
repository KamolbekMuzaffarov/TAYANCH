from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import verify_chain, write_audit
from app.core.config import get_settings
from app.core.deps import current_user
from app.core.errors import AppError, forbidden
from app.core.permissions import require, scope_of
from app.core.security import hash_password
from app.db import get_db
from app.models import (Alert, AuditLog, ClinicalRuleVersion, DataImportRun, DataSource, Mother, Newborn, Pregnancy,
                        PrivacyEvent, RegionalIndicator, Role, ScopeType, User, UserScope)
from app.services import data_import, regional
from app.services.llm.privacy_gateway import PrivacyGateway

router = APIRouter(tags=["admin"])


# ------------------------------------------------------------------ Claude admin helper
def get_gateway(db: Session = Depends(get_db)) -> PrivacyGateway:
    return PrivacyGateway(db)


@router.post("/assistant/admin-help")
def assistant(payload: dict = Body(...), user: User = Depends(current_user), gw: PrivacyGateway = Depends(get_gateway)):
    """Non-clinical help only. The body must match one allowlisted schema exactly; anything else is blocked."""
    require(user, "assistant:use")
    return gw.handle(user, payload)


# ------------------------------------------------------------------ imports / data quality
@router.post("/imports/siat")
def import_siat(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "import:run")
    run = data_import.load_siat(db, user.id)
    n = regional.recompute_all(db)
    write_audit(db, actor=user, action="import.siat", resource_type="data_import_run", resource_id=run.id,
                request_id=request.state.request_id, detail={"rows": run.rows_loaded, "rejected": run.rows_rejected})
    return {"run_id": run.id, "status": run.status, "rows_read": run.rows_read, "rows_loaded": run.rows_loaded,
            "rows_rejected": run.rows_rejected, "issues": run.issues[:20], "risk_scores_recomputed": n}


@router.post("/imports/mics")
def import_mics(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "import:run")
    run = data_import.load_mics(db, user.id)
    n = regional.recompute_all(db)
    write_audit(db, actor=user, action="import.mics", resource_type="data_import_run", resource_id=run.id,
                request_id=request.state.request_id, detail={"rows": run.rows_loaded})
    return {"run_id": run.id, "status": run.status, "rows_loaded": run.rows_loaded, "risk_scores_recomputed": n}


@router.get("/data-quality")
def data_quality(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "import:run")
    runs = db.execute(select(DataImportRun).order_by(DataImportRun.created_at.desc()).limit(20)).scalars()
    latest = dict(db.execute(select(RegionalIndicator.source_key, func.max(RegionalIndicator.period_end))
                             .group_by(RegionalIndicator.source_key)).all())
    by_quality = db.execute(select(RegionalIndicator.source_key, RegionalIndicator.quality_status,
                                   RegionalIndicator.suppression_status, func.count())
                            .group_by(RegionalIndicator.source_key, RegionalIndicator.quality_status,
                                      RegionalIndicator.suppression_status)).all()
    return {"sources": [{"key": s.key, "name": s.name, "kind": s.kind, "license": s.license, "latest_period_end": latest.get(s.key),
                         "is_synthetic": s.is_synthetic} for s in db.execute(select(DataSource)).scalars()],
            "indicator_quality": [{"source": a, "quality": b, "suppression": c, "count": d} for a, b, c, d in by_quality],
            "import_runs": [{"id": r.id, "source": r.source_key, "status": r.status, "rows_read": r.rows_read,
                             "rows_loaded": r.rows_loaded, "rows_rejected": r.rows_rejected, "created_at": r.created_at}
                            for r in runs]}


# ------------------------------------------------------------------ reports (aggregate, small-cell suppressed)
def _registry_report(db: Session, user: User) -> list[dict]:
    s = scope_of(user)
    min_cell = get_settings().REPORT_MIN_CELL
    rows = db.execute(select(Mother.region_soato, Pregnancy.current_risk_level, func.count())
                      .join(Pregnancy, Pregnancy.mother_id == Mother.id).where(Pregnancy.status == "active")
                      .group_by(Mother.region_soato, Pregnancy.current_risk_level)).all()
    out = []
    for reg, level, n in rows:
        if not (s.national or reg in s.regions):
            continue
        suppressed = n < min_cell
        out.append({"region_soato": reg, "risk_level": level, "count": None if suppressed else n,
                    "suppressed": suppressed, "note": f"<{min_cell}" if suppressed else None})
    return out


@router.get("/reports/registry-summary")
def registry_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "report:read")
    alerts = db.execute(select(Alert.severity, func.count(),
                               func.avg(func.julianday(Alert.acknowledged_at) - func.julianday(Alert.created_at)) * 1440
                               if db.bind.dialect.name == "sqlite" else
                               func.avg(func.extract("epoch", Alert.acknowledged_at - Alert.created_at)) / 60)
                        .where(Alert.acknowledged_at.is_not(None)).group_by(Alert.severity)).all()
    return {"label": "Klinik registr (sintetik demo ma’lumot bo‘lishi mumkin — is_synthetic belgisi)",
            "active_pregnancies_by_region_and_risk": _registry_report(db, user),
            "alert_response_minutes": [{"severity": s, "acknowledged": n, "mean_minutes_to_ack": round(m or 0, 1)}
                                       for s, n, m in alerts],
            "small_cell_rule": f"counts < {get_settings().REPORT_MIN_CELL} are suppressed"}


@router.get("/reports/export.csv")
def export_csv(report: str = Query(..., pattern="^(registry_summary|regional_indicators)$"), request: Request = None,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "report:export")
    buf = io.StringIO()
    w = csv.writer(buf)
    if report == "registry_summary":
        w.writerow(["region_soato", "risk_level", "count", "suppressed"])
        rows = _registry_report(db, user)
        for r in rows:
            w.writerow([r["region_soato"], r["risk_level"], r["count"] if r["count"] is not None else f"<{get_settings().REPORT_MIN_CELL}", r["suppressed"]])
    else:
        w.writerow(["indicator_code", "region_soato", "period_start", "period_end", "rate", "unit", "ci_low", "ci_high",
                    "numerator", "denominator", "source", "quality_status", "suppression_status"])
        s = scope_of(user)
        rows = db.execute(select(RegionalIndicator).order_by(RegionalIndicator.indicator_code, RegionalIndicator.region_soato,
                                                             RegionalIndicator.period_end)).scalars()
        rows = [r for r in rows if s.national or r.region_soato in s.regions]
        for r in rows:
            w.writerow([r.indicator_code, r.region_soato, r.period_start, r.period_end,
                        r.rate if r.suppression_status != "suppressed" else "", r.unit, r.ci_low, r.ci_high,
                        r.numerator, r.denominator, r.source_key, r.quality_status, r.suppression_status])
    write_audit(db, actor=user, action="report.export", resource_type="report", resource_id=report,
                request_id=request.state.request_id, detail={"rows": len(rows), "format": "csv"})
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{report}.csv"'})


# ------------------------------------------------------------------ audit & privacy
@router.get("/audit-logs")
def audit_logs(action: str | None = None, limit: int = Query(100, le=500), offset: int = 0,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "audit:read")
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action == action)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(AuditLog.seq.desc()).offset(offset).limit(limit)).scalars()
    write_audit(db, actor=user, action="audit.read", resource_type="audit_log")
    return {"items": [{"seq": e.seq, "at": e.at, "actor_id": e.actor_id, "actor_role": e.actor_role, "action": e.action,
                       "resource_type": e.resource_type, "resource_id": e.resource_id, "outcome": e.outcome,
                       "purpose": e.purpose, "detail": e.detail, "hash": e.hash} for e in rows],
            "total": total, "limit": limit, "offset": offset}


@router.get("/audit-logs/verify")
def audit_verify(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "audit:read")
    return verify_chain(db)


@router.get("/privacy-events")
def privacy_events(limit: int = Query(100, le=500), user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "privacy:read")
    rows = db.execute(select(PrivacyEvent).order_by(PrivacyEvent.at.desc()).limit(limit)).scalars()
    return [{"at": e.at, "event_type": e.event_type, "feature": e.feature, "decision": e.decision, "reasons": e.reasons,
             "provider": e.provider, "model": e.model, "input_tokens": e.input_tokens, "output_tokens": e.output_tokens,
             "duration_ms": e.duration_ms, "status": e.status, "request_id": e.request_id} for e in rows]


@router.get("/clinical-rules")
def clinical_rules_list(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "rules:read")
    return [{"rule_id": r.rule_id, "version": r.version, "subject_type": r.subject_type, "severity": r.severity,
             "definition": r.definition, "approval_status": r.approval_status, "approved_by": r.approved_by,
             "clinical_reference": r.clinical_reference, "effective_from": r.effective_from, "active": r.active}
            for r in db.execute(select(ClinicalRuleVersion).order_by(ClinicalRuleVersion.subject_type, ClinicalRuleVersion.rule_id)).scalars()]


@router.get("/privacy/config")
def privacy_config(user: User = Depends(current_user)):
    require(user, "privacy:read")
    s = get_settings()
    return {"claude_enabled": s.CLAUDE_ENABLED, "claude_external_patient_data": s.CLAUDE_EXTERNAL_PATIENT_DATA,
            "claude_allow_aggregate_health_data": s.CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA,
            "claude_log_prompt_body": s.CLAUDE_LOG_PROMPT_BODY, "voice_provider": s.VOICE_PROVIDER,
            "voicelab_enabled": s.VOICELAB_ENABLED, "voice_store_raw_audio": s.VOICE_STORE_RAW_AUDIO,
            "voice_draft_ttl_minutes": s.VOICE_DRAFT_TTL_MINUTES, "log_clinical_payloads": s.LOG_CLINICAL_PAYLOADS,
            "session_replay_enabled": s.SESSION_REPLAY_ENABLED,
            "analytics_capture_for_clinical_pages": s.ANALYTICS_CAPTURE_FOR_CLINICAL_PAGES,
            "clinical_rules_allow_unapproved": s.CLINICAL_RULES_ALLOW_UNAPPROVED}


# ------------------------------------------------------------------ users
class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-z0-9_.]{3,64}$")
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=128)
    role: Role
    region_soato: str | None = None
    facility_id: str | None = None


@router.post("/users", status_code=201)
def create_user(body: UserCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "user:manage")
    s = scope_of(user)
    if user.role == Role.FACILITY_ADMIN:
        if body.facility_id not in s.facilities or body.role not in (Role.OBGYN, Role.NEONATOLOGIST, Role.LAB_OPERATOR):
            raise forbidden("Facility admins may only create clinical staff in their facility")
    if body.role == Role.NATIONAL_ADMIN:
        raise forbidden("National admins are provisioned out of band")
    if body.role in (Role.PATRONAGE_NURSE, Role.OVABMU_SPECIALIST, Role.FACILITY_MANAGER, Role.PENDING) or (
            user.role != Role.FACILITY_ADMIN and body.role in (Role.OBGYN, Role.NEONATOLOGIST)):
        # nurses and doctors are added only by the administrator of their own facility (POST /facility/staff)
        raise forbidden("Hamshira va shifokorlarni faqat muassasa administratori qoʻshadi")
    if db.execute(select(User.id).where(User.username == body.username)).first():
        raise AppError(409, "duplicate_username", "Username exists")
    u = User(username=body.username, full_name=body.full_name, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()
    if body.facility_id:
        db.add(UserScope(user_id=u.id, scope_type=ScopeType.FACILITY, facility_id=body.facility_id))
    if body.region_soato:
        db.add(UserScope(user_id=u.id, scope_type=ScopeType.REGION, region_soato=body.region_soato))
    write_audit(db, actor=user, action="user.create", resource_type="user", resource_id=u.id,
                request_id=request.state.request_id, detail={"role": body.role.value})
    return {"id": u.id, "username": u.username, "role": u.role.value}


_unused = (Newborn,)

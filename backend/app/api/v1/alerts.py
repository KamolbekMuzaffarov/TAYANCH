from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.deps import current_user
from app.core.errors import AppError, forbidden, not_found
from app.core.permissions import (assert_newborn_access, assert_pregnancy_access, require, scope_of)
from app.core.security import encrypt, utcnow
from app.db import get_db
from app.models import (Alert, AlertAction, AlertRecipient, CareAssignment, Facility, Newborn, Pregnancy, Referral, Role,
                        User)
from app.schemas import AlertOut, ReferralAction, ReferralCreate, ReferralOut, ResolveIn
from app.services import alert_engine

router = APIRouter(tags=["alerts"])


def _visible_alert_query(db: Session, user: User):
    s = scope_of(user)
    if user.role == Role.FACILITY_ADMIN:
        return select(Alert).where(Alert.facility_id.in_(s.facilities or {"-"}))
    recipient = select(AlertRecipient.alert_id).where(AlertRecipient.user_id == user.id)
    mothers = select(CareAssignment.mother_id).where(CareAssignment.user_id == user.id, CareAssignment.active.is_(True))
    conds = [Alert.id.in_(recipient), Alert.mother_id.in_(mothers)]
    if user.role == Role.NEONATOLOGIST:
        conds.append(Alert.newborn_id.is_not(None) & Alert.facility_id.in_(s.facilities or {"-"}))
    if user.role == Role.OVABMU_SPECIALIST:
        conds.append(Alert.id.in_(select(Referral.alert_id).where(Referral.to_facility_id.in_(s.facilities or {"-"}))))
    return select(Alert).where(or_(*conds))


def _get_alert(db: Session, user: User, alert_id: str) -> Alert:
    a = db.execute(_visible_alert_query(db, user).where(Alert.id == alert_id)).scalar_one_or_none()
    if a is None:
        raise not_found("alert")
    return a


@router.get("/alerts")
def list_alerts(status: str | None = Query(None, pattern="^(open|acknowledged|resolved)$"),
                severity: str | None = Query(None, pattern="^(red|yellow)$"), limit: int = Query(50, le=200), offset: int = 0,
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "alert:read")
    q = _visible_alert_query(db, user)
    if status:
        q = q.where(Alert.status == status)
    if severity:
        q = q.where(Alert.severity == severity)
    rows = list(db.execute(q.order_by(Alert.severity.asc(), Alert.created_at.desc())).scalars())
    return {"items": [AlertOut.model_validate(a).model_dump() for a in rows[offset:offset + limit]],
            "total": len(rows), "limit": limit, "offset": offset}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "alert:read")
    a = _get_alert(db, user, alert_id)
    actions = db.execute(select(AlertAction).where(AlertAction.alert_id == a.id).order_by(AlertAction.at)).scalars()
    return {"alert": AlertOut.model_validate(a).model_dump(),
            "actions": [{"action": x.action, "actor_id": x.actor_id, "at": x.at, "detail": x.detail} for x in actions]}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "alert:act")
    a = alert_engine.transition(db, _get_alert(db, user, alert_id), user, "acknowledged", request_id=request.state.request_id)
    return AlertOut.model_validate(a).model_dump()


@router.post("/alerts/{alert_id}/resolve")
def resolve(alert_id: str, body: ResolveIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "alert:act")
    a = alert_engine.transition(db, _get_alert(db, user, alert_id), user, "resolved", body.resolution_code,
                                request_id=request.state.request_id)
    return AlertOut.model_validate(a).model_dump()


@router.post("/alerts/escalate-overdue")
def escalate(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Normally run by the scheduler/worker; exposed for operators and demos."""
    if user.role not in (Role.NATIONAL_ADMIN, Role.FACILITY_ADMIN):
        raise forbidden()
    return {"escalated": alert_engine.escalate_overdue(db)}


# ------------------------------------------------------------------ referrals (OvaBMU)
@router.post("/referrals", status_code=201, response_model=ReferralOut)
def create_referral(body: ReferralCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "referral:create")
    to_fac = db.get(Facility, body.to_facility_id)
    if not to_fac or to_fac.facility_type not in ("ovabmu", "perinatal_center"):
        raise AppError(422, "invalid_target", "Referral target must be an OvaBMU / perinatal center")
    if body.subject_type == "pregnancy":
        p = assert_pregnancy_access(db, user, db.get(Pregnancy, body.pregnancy_id), "update")
        mother_id, newborn_id, from_fac, synthetic = p.mother_id, None, p.facility_id, p.is_synthetic
    else:
        nb = assert_newborn_access(db, user, db.get(Newborn, body.newborn_id), "update")
        mother_id, newborn_id, from_fac, synthetic = nb.mother_id, nb.id, nb.facility_id, nb.is_synthetic
    if body.alert_id:
        a = db.get(Alert, body.alert_id)
        if not a or a.subject_id not in (body.pregnancy_id, body.newborn_id):
            raise AppError(422, "invalid_alert", "alert_id does not belong to this subject")
        db.add(AlertAction(alert_id=a.id, action="referred", actor_id=user.id, at=utcnow(), detail={"to_facility_id": to_fac.id}))
    ref = Referral(subject_type=body.subject_type, mother_id=mother_id if body.subject_type == "pregnancy" else None,
                   newborn_id=newborn_id, pregnancy_id=body.pregnancy_id, alert_id=body.alert_id, from_facility_id=from_fac,
                   to_facility_id=to_fac.id, priority=body.priority, reason_code=body.reason_code, created_by=user.id,
                   is_synthetic=synthetic)
    db.add(ref)
    db.flush()
    write_audit(db, actor=user, action="referral.create", resource_type="referral", resource_id=ref.id,
                request_id=request.state.request_id, detail={"priority": body.priority, "reason_code": body.reason_code})
    return ref


@router.get("/referrals/queue")
def referral_queue(status: str | None = Query(None, pattern="^(sent|accepted|consulted|closed|declined)$"),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    """OvaBMU queue: referrals to the specialist's facility, emergency first. Clinicians see their own referrals."""
    require(user, "referral:read")
    if user.role == Role.OVABMU_SPECIALIST:
        q = select(Referral).where(Referral.to_facility_id.in_(scope_of(user).facilities or {"-"}))
    else:
        q = select(Referral).where(Referral.created_by == user.id)
    if status:
        q = q.where(Referral.status == status)
    pr = {"emergency": 0, "urgent": 1, "routine": 2}
    rows = sorted(db.execute(q).scalars(), key=lambda r: (r.status in ("closed", "declined"), pr[r.priority], r.created_at))
    return {"items": [ReferralOut.model_validate(r).model_dump() for r in rows], "total": len(rows)}


@router.post("/referrals/{referral_id}/actions", response_model=ReferralOut)
def referral_action(referral_id: str, body: ReferralAction, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    require(user, "referral:act")
    ref = db.get(Referral, referral_id)
    if not ref or ref.to_facility_id not in scope_of(user).facilities:
        raise not_found("referral")
    allowed = {"accept": {"sent"}, "decline": {"sent"}, "record_consultation": {"accepted", "consulted"},
               "close": {"accepted", "consulted"}}
    if ref.status not in allowed[body.action]:
        raise AppError(409, "invalid_transition", f"Cannot {body.action} a referral in status {ref.status}")
    now = utcnow()
    if body.action == "accept":
        ref.status, ref.accepted_by, ref.accepted_at = "accepted", user.id, now
        if ref.alert_id:
            a = db.get(Alert, ref.alert_id)
            if a and a.status == "open":
                alert_engine.transition(db, a, user, "acknowledged", request_id=request.state.request_id)
    elif body.action == "decline":
        ref.status = "declined"
    elif body.action == "record_consultation":
        if not body.consult_outcome:
            raise AppError(422, "consult_outcome_required", "consult_outcome is required")
        ref.status, ref.consult_outcome = "consulted", body.consult_outcome
        ref.consult_note_enc = encrypt(body.consult_note)
    else:
        ref.status, ref.closed_at = "closed", now
    write_audit(db, actor=user, action=f"referral.{body.action}", resource_type="referral", resource_id=ref.id,
                request_id=request.state.request_id, detail={"consult_outcome": body.consult_outcome} if body.consult_outcome else None)
    return ref

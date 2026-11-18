from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import statistics
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import verify_chain, write_audit
from app.core.config import get_settings
from app.core.deps import bearer, rate_limiter
from app.core.errors import AppError, forbidden, not_found
from app.core.security import decode_token, decrypt, encrypt, new_refresh_token, token_hash, utcnow
from app.db import SessionLocal, get_db
from app.models import (Alert, AlertAction, AuditLog, AuthSession, CareAssignment, ClinicalRuleVersion, Facility,
                        MaternalHistory, MaternalObservation, Mother, Newborn, NewbornObservation, Pregnancy, PrivacyEvent, Referral,
                        RegionalIndicator, RegionalRiskScore, RiskAssessment, Role, User)
from app.services import clinical_rules, live_engine, official_stats, regional, risk_engine, shadow_models, trend
from app.services.llm import privacy_gateway
from app.services.live_bus import bus, iso
from app.services.live_engine import geo, write_tx
from app.services.voice.live_parse import parse_visit

router = APIRouter(prefix="/live", tags=["live"])
log = logging.getLogger("perinatal.live")

PERSONAS = {"nurse": "demo.nurse", "specialist": "demo.specialist", "leader": "demo.leader", "admin": "demo.admin",
            "facility_admin": "demo.facility_admin"}
ROLE_PERSONA = {Role.PATRONAGE_NURSE: "nurse", Role.OVABMU_SPECIALIST: "specialist", Role.NATIONAL_ADMIN: "leader",
                Role.PENDING: "pending", Role.FACILITY_MANAGER: "facility_admin"}
ROLE_LABEL = {"nurse": "Patronaj hamshira", "specialist": "OvaBMU mutaxassisi", "leader": "Monitoring markazi",
              "admin": "Tizim boshqaruvchisi", "pending": "Rol berilishi kutilmoqda",
              "facility_admin": "Muassasa administratori"}

LIVE_ROLE_PERSONA = {"admin": "leader", "leader": "leader", "specialist": "specialist", "nurse": "nurse", "pending": "pending",
                     "facility_admin": "facility_admin"}
TASHKENT = timezone(timedelta(hours=5))
ZONES = ("red", "yellow", "green", "grey")


def local_date(dt: datetime | None = None):
    return (dt or utcnow()).astimezone(TASHKENT).date()


# ------------------------------------------------------------------ auth
def _authenticate(request: Request, raw: str | None, allow_pending: bool = False,
                  allow_facility_admin: bool = False) -> User:
    if not raw:
        raise AppError(401, "unauthorized", "Authentication required")
    try:
        payload = decode_token(raw)
    except jwt.PyJWTError:
        raise AppError(401, "unauthorized", "Invalid or expired token")
    if payload.get("typ") != "access":
        raise AppError(401, "unauthorized", "Invalid token type")
    return _session_user(request, payload.get("sub"), payload.get("sid"), allow_pending=allow_pending,
                         allow_facility_admin=allow_facility_admin)


def _session_user(request: Request, user_id: str | None, session_id: str | None, allow_pending: bool = False,
                  allow_facility_admin: bool = False) -> User:
    with SessionLocal() as db:
        sess = db.get(AuthSession, session_id) if session_id else None
        if sess is None or sess.revoked_at is not None or sess.expires_at < utcnow():
            raise AppError(401, "unauthorized", "Session revoked or expired")
        user = db.get(User, user_id) if user_id else None
        if user is None or not user.is_active or sess.user_id != user.id or user.account_status == "blocked":
            raise AppError(401, "unauthorized", "User inactive")
        _ = list(user.scopes)
        geo.load(db)
    request.state.user_id, request.state.session_id = user.id, sess.id
    if not allow_pending and role_of(user) == "pending":
        # registered, but no role from an admin yet: public pages and the "waiting" screen only
        _audit(user, "live.access", "user", user.id, request, {"reason": "account_pending"}, outcome="denied")
        raise AppError(403, "account_pending", "Hisobingizga hali rol berilmagan. Administrator tasdiqlashini kuting")
    if not allow_facility_admin and role_of(user) == "facility_admin":
        # a facility administrator manages staff accounts only: no map, no patient card, no live endpoint at all
        _audit(user, "live.access", "user", user.id, request, {"reason": "facility_admin_scope"}, outcome="denied")
        raise AppError(403, "forbidden", "Muassasa administratori uchun bu boʻlim yopiq")
    return user


def live_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    """Bearer header only. Tokens in the query string are NOT accepted here (they leak into logs and history)."""
    return _authenticate(request, creds.credentials if creds else None)


_TICKET_TTL = 60
_tickets: dict[str, tuple[str, str, float]] = {}
_tickets_lock = threading.Lock()


def _issue_ticket(user_id: str, session_id: str) -> str:
    ticket = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _tickets_lock:
        for k in [k for k, v in _tickets.items() if v[2] < now]:
            _tickets.pop(k, None)
        _tickets[hashlib.sha256(ticket.encode()).hexdigest()] = (user_id, session_id, now + _TICKET_TTL)
    return ticket


def _redeem_ticket(ticket: str) -> tuple[str, str] | None:
    with _tickets_lock:
        item = _tickets.pop(hashlib.sha256(ticket.encode()).hexdigest(), None)
    if item is None or item[2] < time.monotonic():
        return None
    return item[0], item[1]


def stream_user(request: Request, ticket: str | None = Query(None, max_length=128, description="single-use stream ticket"),
                token: str | None = Query(None, max_length=4096, deprecated=True,
                                          description="DEPRECATED: access token in the query string; use ?ticket="),
                creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if ticket:
        ids = _redeem_ticket(ticket)
        if ids is None:
            raise AppError(401, "unauthorized", "Stream ticket is invalid, expired or already used")
        request.state.auth_via = "ticket"
        return _session_user(request, *ids)
    if creds:
        request.state.auth_via = "bearer"
        return _authenticate(request, creds.credentials)
    request.state.auth_via = "query_token_deprecated"
    user = _authenticate(request, token)
    log.warning("deprecated ?token= used on the event stream; switch the client to POST /live/stream-ticket")
    return user


def _limit(user: User, bucket: str = "write", per_minute: int | None = None) -> None:
    """Per-user sliding-window limit for write (and other costly) live endpoints."""
    rate_limiter.hit(f"live:{bucket}:{user.id}", per_minute or get_settings().LIVE_WRITE_RATE_PER_MINUTE)


def role_of(user: User) -> str:
    """admin | leader | facility_admin | specialist | nurse | pending. Seeded users (live_role NULL) derive it from their role."""
    if (user.account_status or "active") == "pending":
        return "pending"
    return user.live_role if user.live_role in LIVE_ROLE_PERSONA else ROLE_PERSONA.get(user.role, "leader")


def persona_of(user: User) -> str:
    return LIVE_ROLE_PERSONA[role_of(user)]


def is_leader(user: User) -> bool:
    """The leader (monitoring centre) works with aggregates and record codes only - never with identities."""
    return persona_of(user) == "leader"


def _audit(user: User | None, action: str, resource_type: str, resource_id: str | None = None,
           request: Request | None = None, detail: dict | None = None, outcome: str = "success") -> None:
    try:
        with write_tx() as db:
            write_audit(db, actor=user, action=action, resource_type=resource_type, resource_id=resource_id,
                        request_id=getattr(request.state, "request_id", None) if request else None, detail=detail,
                        purpose="live_demo", outcome=outcome, commit=False)
    except Exception:
        pass


ROLE_ONLY_UZ = {"nurse": "Bu amal faqat patronaj hamshira uchun ochiq",
                "specialist": "Bu amal faqat OvaBMU mutaxassisi uchun ochiq"}


def _deny(user: User, request: Request, action: str, resource_type: str, resource_id: str | None, reason: str,
          *, status: int = 404, what: str = "resource", message: str | None = None) -> None:
    """Audit the refused attempt, then answer 403 (wrong role) or 404 (outside the caller's scope: no enumeration)."""
    _audit(user, action, resource_type, resource_id[:36] if resource_id else None, request, {"reason": reason},
           outcome="denied")
    if status == 403:
        raise forbidden(message or "Ruxsat yoʻq")
    raise not_found(what)


def _require_persona(user: User, request: Request, persona: str, action: str, resource_type: str,
                     resource_id: str | None = None) -> None:
    if persona_of(user) != persona:
        _deny(user, request, action, resource_type, resource_id, "role_not_allowed", status=403,
              message=ROLE_ONLY_UZ[persona])


def _nurse_mothers(db: Session, user: User) -> set[str]:
    return {m for m in db.execute(select(CareAssignment.mother_id).where(
        CareAssignment.user_id == user.id, CareAssignment.active.is_(True))).scalars() if m}


def _nurse_may_visit(db: Session, user: User, preg: Pregnancy) -> bool:
    """A nurse records visits for women assigned to her or living in her own district."""
    district = next((s.district_code for s in user.scopes if s.district_code), None)
    return preg.mother_id in _nurse_mothers(db, user) or bool(district and geo.fac_district.get(preg.facility_id) == district)


def _region_facilities(user: User) -> set[str]:
    region = _user_payload(user)["region_soato"]
    return {f for f, r in geo.fac_region.items() if r == region} if region else set()


def _has_alert(db: Session, subject_id: str) -> bool:
    return db.execute(select(Alert.id).where(Alert.subject_id == subject_id).limit(1)).first() is not None


def _specialist_alert(db: Session, user: User, request: Request, alert_id: str, action: str) -> None:
    """Only the specialist persona, only alerts of her region (404 otherwise). Raises; returns nothing."""
    _require_persona(user, request, "specialist", action, "alert", alert_id)
    geo.load(db)
    a = db.get(Alert, alert_id)
    if a is None:
        raise not_found("alert")
    if a.facility_id not in _region_facilities(user):
        _deny(user, request, action, "alert", alert_id, "outside_region", what="alert")


def _user_payload(user: User) -> dict:
    persona = persona_of(user)
    district = next((s.district_code for s in user.scopes if s.district_code), None)
    region = next((s.region_soato for s in user.scopes if s.region_soato), None)
    if region is None:
        fac = next((s.facility_id for s in user.scopes if s.facility_id), None)
        region = geo.fac_region.get(fac) if fac else None
    d = geo.districts.get(district) if district else None
    role = role_of(user)
    return {"id": user.id, "name": user.full_name, "persona": persona,
            "role_label": ROLE_LABEL["admin" if role == "admin" else persona],
            "district_code": district, "district_name": d["name"] if d else None, "region_soato": region,
            "region_name": geo.regions.get(region, "Oʻzbekiston") if region else "Oʻzbekiston",
            "role": role, "email": decrypt(user.email_enc), "phone": decrypt(user.phone_enc),
            "two_step": bool(user.two_step)}


def issue_session(db: Session, user: User, *, demo: bool = False) -> dict:
    """One session mechanism for demo personas and registered accounts: AuthSession row + JWT of the same lifetime."""
    s = get_settings()
    hours = max(12, s.LIVE_DEMO_SESSION_HOURS)
    now = utcnow()
    sess = AuthSession(user_id=user.id, refresh_token_hash=token_hash(new_refresh_token()), mfa_verified=True,
                       expires_at=now + timedelta(hours=hours))
    db.add(sess)
    db.flush()
    claims = {"sub": user.id, "sid": sess.id, "role": user.role.value, "typ": "access", "iat": now,
              "exp": now + timedelta(hours=hours), "jti": uuid.uuid4().hex}
    if demo:
        claims["demo"] = True
    return {"access_token": jwt.encode(claims, s.JWT_SECRET, algorithm=s.JWT_ALGORITHM), "token_type": "bearer",
            "expires_in": hours * 3600, "user": _user_payload(user)}


class LoginIn(BaseModel):
    model_config = {"extra": "forbid"}
    persona: Literal["nurse", "specialist", "leader", "admin", "facility_admin"]


@router.post("/login")
def login(body: LoginIn, request: Request):
    s = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    if s.is_production or not s.LIVE_DEMO_LOGIN:
        _audit(None, "auth.demo_login", "user", None, request, {"persona": body.persona, "reason": "disabled"}, outcome="denied")
        raise AppError(403, "forbidden", "Demo login is disabled (requires APP_ENV != production and LIVE_DEMO_LOGIN=1)")
    try:
        rate_limiter.hit(f"live:login:{client_ip}", s.LIVE_LOGIN_RATE_PER_MINUTE)
    except AppError:
        _audit(None, "auth.demo_login", "user", None, request, {"persona": body.persona, "reason": "rate_limited"}, outcome="denied")
        raise
    with write_tx() as db:
        geo.load(db)
        user = db.execute(select(User).where(User.username == PERSONAS[body.persona])).scalar_one_or_none()
        if user is None or not user.is_active:
            raise AppError(503, "not_seeded", "Demo foydalanuvchilar topilmadi: avval scripts/seed_national.py ni ishga tushiring")
        out = issue_session(db, user, demo=True)
        write_audit(db, actor=user, action="auth.demo_login", resource_type="user", resource_id=user.id,
                    request_id=request.state.request_id, detail={"persona": body.persona}, commit=False)
        return out


def _name(m: Mother | None) -> str:
    return (decrypt(m.full_name_enc) if m else None) or "—"


def _mahalla(m: Mother | None) -> str | None:
    a = decrypt(m.address_enc) if m else None
    return a.replace(" mahallasi", "") if a else None


def _age(m: Mother | None) -> int | None:
    return utcnow().year - m.birth_year if m and m.birth_year else None


def _latest_ra(db: Session, subject_id: str) -> RiskAssessment | None:
    return db.execute(select(RiskAssessment).where(RiskAssessment.subject_id == subject_id)
                      .order_by(RiskAssessment.created_at.desc()).limit(1)).scalar_one_or_none()


def _minutes(a: datetime, b: datetime) -> int:
    return max(0, int((b - a).total_seconds() // 60))


def _preg_brief(db: Session, preg: Pregnancy) -> dict:
    """Everything list views need about one pregnancy (4 small queries)."""
    mother = db.get(Mother, preg.mother_id)
    obs, hb, glucose = live_engine.load_history(db, preg.id)
    now = utcnow()
    visits = [live_engine.obs_dict(o) for o in obs]
    weeks = live_engine.weeks_of(preg, now)
    fresh = now - timedelta(days=get_settings().OBSERVATION_FRESHNESS_DAYS)
    facts = {"lab_hemoglobin_g_l": hb[-1][1] if hb and hb[-1][0] >= fresh else None,
             "lab_glucose_fasting_mmol_l": glucose[-1][1] if glucose and glucose[-1][0] >= fresh else None}
    return {"mother": mother, "obs": obs, "visits": visits, "hb": hb, "glucose": glucose, "weeks": weeks, "facts": facts,
            "district": geo.district_of_facility(preg.facility_id),
            "summary": trend.patient_summary(weeks, visits, [v for _, v in hb], now, facts["lab_glucose_fasting_mmol_l"]),
            "case_summary": trend.case_summary(weeks, visits[-1] if visits else None, facts)}


def _nb_brief(db: Session, nb: Newborn) -> dict:
    mother = db.get(Mother, nb.mother_id) if nb.mother_id else None
    obs = list(db.execute(select(NewbornObservation).where(NewbornObservation.newborn_id == nb.id)
                          .order_by(NewbornObservation.observed_at)).scalars())
    age_days = max(0, (utcnow() - nb.birth_datetime).days)
    last = live_engine.nb_obs_dict(obs[-1]) if obs else None
    meta = {"birth_weight_g": nb.birth_weight_g, "gestational_age_weeks": nb.gestational_age_weeks}
    mname = _name(mother)
    return {"mother": mother, "obs": obs, "age_days": age_days, "last": last,
            "name": f"{mname}ning chaqalogʻi" if mother else "Chaqaloq",
            "district": geo.district_of_facility(nb.facility_id), "summary": trend.newborn_summary(age_days, meta, last)}


def _alert_view(a: Alert) -> dict:
    return {"id": a.id, "severity": a.severity, "status": a.status, "created_at": iso(a.created_at),
            "acknowledged_at": iso(a.acknowledged_at), "resolved_at": iso(a.resolved_at),
            "resolution": live_engine.RESOLUTION_TEXT.get(a.resolution_code) if a.resolution_code else None,
            "resolution_code": a.resolution_code, "escalation_level": a.escalation_level, "sla_due_at": iso(a.sla_due_at)}


def _response_stats(db: Session, since: datetime, facility_ids: set[str] | None = None) -> tuple[int | None, int]:
    q = select(Alert.created_at, Alert.acknowledged_at, Alert.facility_id).where(
        Alert.acknowledged_at.is_not(None), Alert.acknowledged_at >= since, Alert.acknowledged_by.is_not(None))
    rows = [(c, a) for c, a, f in db.execute(q) if facility_ids is None or f in facility_ids]
    if not rows:
        return None, 0
    return round(statistics.median((a - c).total_seconds() / 60 for c, a in rows)), len(rows)


@router.get("/map")
def live_map(user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    now = utcnow()
    dist = {c: {"code": c, "name": d["name"], "region_soato": d["region_soato"], "pregnant": 0, "red": 0, "yellow": 0,
                "green": 0, "grey": 0, "open_alerts": 0, "overdue_alerts": 0, "newborn_alerts": 0, "status": "grey",
                "_newborns": 0, "_red_overdue": 0} for c, d in geo.districts.items()}
    q = (select(Pregnancy.facility_id, Pregnancy.current_risk_level, func.count()).where(Pregnancy.status == "active")
         .group_by(Pregnancy.facility_id, Pregnancy.current_risk_level))
    for fid, level, n in db.execute(q):
        d = dist.get(geo.fac_district.get(fid))
        if d is not None:
            d["pregnant"] += n
            d[level if level in ZONES else "grey"] += n
    newborns_total = 0
    for fid, n in db.execute(select(Newborn.facility_id, func.count()).group_by(Newborn.facility_id)):
        newborns_total += n
        d = dist.get(geo.fac_district.get(fid))
        if d is not None:
            d["_newborns"] += n
    waiting_m = waiting_n = overdue = 0
    for a in db.execute(select(Alert).where(Alert.status == "open")).scalars():
        od = live_engine.is_overdue(a, now)
        waiting_m += a.subject_type == "pregnancy"
        waiting_n += a.subject_type == "newborn"
        overdue += od
        d = dist.get(geo.fac_district.get(a.facility_id))
        if d is None:
            continue
        d["open_alerts"] += 1
        d["newborn_alerts"] += a.subject_type == "newborn"
        d["overdue_alerts"] += od
        d["_red_overdue"] += od and a.severity == "red"
    regions = {s: {"soato": s, "name": n, "pregnant": 0, "red": 0, "yellow": 0, "open_alerts": 0, "overdue_alerts": 0,
                   "official_status": "grey"} for s, n in geo.regions.items()}
    for d in dist.values():
        d["status"] = "red" if d["_red_overdue"] else "yellow" if d["open_alerts"] else \
            "green" if (d["pregnant"] or d["_newborns"]) else "grey"
        r = regions.get(d["region_soato"])
        if r:
            for k in ("pregnant", "red", "yellow", "open_alerts", "overdue_alerts"):
                r[k] += d[k]
    year = regional.latest_siat_year(db)
    for soato, status in db.execute(select(RegionalRiskScore.region_soato, RegionalRiskScore.status).where(
            RegionalRiskScore.layer == "overall_area_status", RegionalRiskScore.period_year == year)):
        if soato in regions:
            regions[soato]["official_status"] = status
    median, answered = _response_stats(db, now - timedelta(hours=24))
    if median is None:
        median, _ = _response_stats(db, now - timedelta(days=7))
    acknowledged = db.execute(select(func.count(Alert.id)).where(Alert.status == "acknowledged")).scalar() or 0
    totals = {"pregnant": sum(d["pregnant"] for d in dist.values()), **{z: sum(d[z] for d in dist.values()) for z in ZONES},
              "waiting_mothers": int(waiting_m), "waiting_newborns": int(waiting_n), "overdue": int(overdue),
              "waiting": int(waiting_m + waiting_n), "acknowledged": int(acknowledged),
              "median_response_min": median, "answered_24h": answered, "newborns": newborns_total}
    out_d = [{k: (int(v) if isinstance(v, bool) else v) for k, v in d.items() if not k.startswith("_")} for d in dist.values()]
    return {"generated_at": iso(now), "totals": totals, "districts": sorted(out_d, key=lambda d: d["code"]),
            "regions": sorted(regions.values(), key=lambda r: r["soato"])}


def _case_row(db: Session, a: Alert, now: datetime) -> dict:
    row = {"alert_id": a.id, "pregnancy_id": None, "newborn_id": None, "subject_type": a.subject_type, "code": None,
           "severity": a.severity, "status": a.status, "summary": "", "waiting_minutes": _minutes(a.created_at, a.acknowledged_at or now),
           "sla_due_at": iso(a.sla_due_at), "escalation_level": a.escalation_level, "overdue": live_engine.is_overdue(a, now),
           "responsible": live_engine.short_name(geo.user_name(a.acknowledged_by or a.assigned_to))
           or live_engine.specialist_label(a.region_soato)}
    if a.subject_type == "pregnancy":
        preg = db.get(Pregnancy, a.subject_id)
        if preg:
            b = _preg_brief(db, preg)
            row.update(pregnancy_id=preg.id, code=b["mother"].local_code if b["mother"] else None, summary=b["case_summary"])
    else:
        nb = db.get(Newborn, a.subject_id)
        if nb:
            b = _nb_brief(db, nb)
            row.update(newborn_id=nb.id, code=nb.local_code, summary=b["summary"])
    return row


@router.get("/districts/{code}")
def district_detail(code: str, request: Request, user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    d = geo.districts.get(code)
    if not d:
        raise not_found("district")
    now = utcnow()
    fac_ids = {f for f, c in geo.fac_district.items() if c == code}
    counts = {"pregnant": 0, "red": 0, "yellow": 0, "green": 0, "grey": 0, "newborns": 0}
    if fac_ids:
        for level, n in db.execute(select(Pregnancy.current_risk_level, func.count()).where(
                Pregnancy.status == "active", Pregnancy.facility_id.in_(fac_ids)).group_by(Pregnancy.current_risk_level)):
            counts["pregnant"] += n
            counts[level if level in ZONES else "grey"] += n
        counts["newborns"] = db.execute(select(func.count(Newborn.id)).where(Newborn.facility_id.in_(fac_ids))).scalar() or 0
    alerts = list(db.execute(select(Alert).where(Alert.facility_id.in_(fac_ids or {"-"}), Alert.status.in_(("open", "acknowledged")))
                             .order_by(Alert.created_at)).scalars())
    alerts.sort(key=lambda a: (a.status != "open", a.severity != "red", a.created_at))
    median, answered = _response_stats(db, now - timedelta(days=7), fac_ids)
    out = {"district": {"code": code, "name": d["name"], "region_soato": d["region_soato"],
                        "region_name": geo.regions.get(d["region_soato"])},
           "counts": counts,
           "response": {"open_alerts": sum(a.status == "open" for a in alerts),
                        "overdue_alerts": sum(live_engine.is_overdue(a, now) for a in alerts),
                        "median_response_min": median, "answered_7d": answered},
           "cases": [_case_row(db, a, now) for a in alerts[:40]],
           "nurses": max(1, round(counts["pregnant"] / 11)),
           "official": _official().district_block(code)}
    _audit(user, "live.district.read", "district", code, request)
    return out


def _official() -> official_stats.OfficialStats:
    return official_stats.stats.load(geo.districts)


@router.get("/official/districts")
def official_districts(indicator: str | None = Query(None, pattern=r"^[a-z0-9_]{3,40}$"),
                       year: int | None = Query(None, ge=2000, le=2100),
                       user: User = Depends(live_user), db: Session = Depends(get_db)):
    """Official SIAT district statistics mapped to the 199 map districts (published aggregates, no patient data)."""
    geo.load(db)
    try:
        return _official().districts_payload(indicator, year)
    except KeyError:
        raise AppError(422, "unknown_indicator", "Bunday koʻrsatkich yoʻq",
                       [{"available": [i["code"] for i in _official().available()]}])
    except ValueError:
        raise AppError(422, "unknown_year", "Bu yil uchun maʼlumot yoʻq", [{"years": _official().years(indicator or official_stats.DEFAULT_INDICATOR)}])


INDICATORS = [
    ("infant_mortality_rate", "Goʻdaklar oʻlimi (1000 tirik tugʻilishga)", "‰"),
    ("under5_mortality_rate", "5 yoshgacha bolalar oʻlimi (1000 tirik tugʻilishga)", "‰"),
    ("maternal_mortality_ratio", "Onalar oʻlimi (100 000 tirik tugʻilishga)", "100 ming tugʻilishga"),
    ("preterm_birth_pct", "Muddatidan oldin tugʻilganlar ulushi", "%"),
    ("births_total", "Tugʻilganlar soni", "nafar"),
    ("adolescent_birth_rate_15_17", "15–17 yoshli oʻsmirlar tugʻishi (1000 ayolga)", "‰"),
    ("abortions_per_100_births", "Abortlar (100 tugʻilishga)", "100 tugʻilishga"),
    ("obgyn_doctors", "Akusher-ginekologlar soni", "nafar"),
    ("doctors_per_10000", "Shifokorlar bilan taʼminlanish (10 000 aholiga)", "10 ming aholiga"),
    ("maternity_beds_per_10000_women", "Tugʻruq oʻrinlari (15–49 yoshli 10 000 ayolga)", "10 ming ayolga"),
    ("total_fertility_rate", "Tugʻilishning umumiy koeffitsiyenti", "bola"),
]


@router.get("/regions/{soato}/official")
def region_official(soato: str, user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    if soato not in geo.regions and soato != "1700":
        raise not_found("region")
    year = regional.latest_siat_year(db)
    codes = [c for c, _, _ in INDICATORS]
    series: dict[tuple[str, str], dict[int, float]] = {}
    for code, reg, start, rate in db.execute(select(RegionalIndicator.indicator_code, RegionalIndicator.region_soato,
                                                    RegionalIndicator.period_start, RegionalIndicator.rate).where(
            RegionalIndicator.source_key == "siat", RegionalIndicator.indicator_code.in_(codes),
            RegionalIndicator.region_soato.in_((soato, "1700")), RegionalIndicator.district_code.is_(None))):
        if rate is not None:
            series.setdefault((code, reg), {})[start.year] = round(rate, 2)
    items = []
    for code, label, unit in INDICATORS:
        own, nat = series.get((code, soato), {}), series.get((code, "1700"), {})
        if not own:
            continue
        y = year if year in own else max(own)
        items.append({"code": code, "label": label, "value": own[y], "national": nat.get(y), "unit": unit, "year": y,
                      "series": [{"year": k, "value": own[k]} for k in sorted(own)]})
    status = db.execute(select(RegionalRiskScore.status).where(
        RegionalRiskScore.region_soato == soato, RegionalRiskScore.layer == "overall_area_status",
        RegionalRiskScore.period_year == year)).scalars().first()
    layers = {l: s for l, s in db.execute(select(RegionalRiskScore.layer, RegionalRiskScore.status).where(
        RegionalRiskScore.region_soato == soato, RegionalRiskScore.period_year == year))}
    return {"region": {"soato": soato, "name": geo.regions.get(soato, "Oʻzbekiston")}, "year": year,
            "status": status or "grey", "layers": layers, "indicators": items,
            "source": "SIAT / Milliy statistika qoʻmitasi"}


# ------------------------------------------------------------------ real-time
@router.get("/events")
def events(limit: int = Query(30, ge=1, le=200), user: User = Depends(live_user)):
    return bus.recent(limit)


@router.post("/stream-ticket")
def stream_ticket(request: Request, user: User = Depends(live_user)):
    """Exchange the bearer token for a 60-second single-use ticket: `new EventSource('/stream?ticket=...')`."""
    _limit(user, "ticket")
    return {"ticket": _issue_ticket(user.id, request.state.session_id), "expires_in": _TICKET_TTL, "single_use": True}


@router.get("/stream")
async def stream(request: Request, user: User = Depends(stream_user)):
    async def gen():
        q = bus.subscribe()
        try:
            yield ": connected\n\n"
            last_ping = time.monotonic()
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=max(0.1, 15 - (time.monotonic() - last_ping)))
                    yield f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    pass
                if time.monotonic() - last_ping >= 15:  # keep-alive comment every 15 s
                    last_ping = time.monotonic()
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    if getattr(request.state, "auth_via", "") == "query_token_deprecated":
        headers |= {"Deprecation": "true", "Link": '</api/v1/live/stream-ticket>; rel="successor-version"'}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


def prefill_events(limit: int = 30) -> int:
    """First paint after a restart: rebuild the latest feed lines from stored alert history."""
    if not bus.is_empty():
        return 0
    with SessionLocal() as db:
        geo.load(db)
        items = []
        for a in db.execute(select(Alert).order_by(Alert.updated_at.desc()).limit(limit)).scalars():
            d = geo.district_of_facility(a.facility_id)
            base = dict(zone=a.severity, district_code=d["code"], district_name=d["name"], region_soato=d["region_soato"])
            colour = "qizil" if a.severity == "red" else "sariq"
            label = trend.RULE_UZ.get(a.rule_ids[0], (a.rule_ids[0], ""))[0] if a.rule_ids else "signal"
            who = " (chaqaloq)" if a.subject_type == "newborn" else ""
            items.append((a.created_at, dict(type="newborn" if a.subject_type == "newborn" else "alert", **base,
                                             text=f"{d['name']}: yangi {colour} signal{who} — {label[:1].lower() + label[1:]}")))
            if a.escalation_level:
                items.append((a.updated_at if a.status == "open" else a.created_at + timedelta(minutes=live_engine.sla_minutes(a.severity)),
                              dict(type="escalation", **base, text=f"{d['name']}: signal javobsiz qoldi — yuqori bosqichga oʻtkazildi ({a.escalation_level}-daraja)")))
            if a.acknowledged_at and a.acknowledged_by:
                mins = _minutes(a.created_at, a.acknowledged_at)
                items.append((a.acknowledged_at, dict(type="ack", **base, text=(
                    f"{d['name']}: signal qabul qilindi — {live_engine.short_name(geo.user_name(a.acknowledged_by))}, {mins} daqiqada javob berildi"))))
            if a.resolved_at and a.resolution_code:
                items.append((a.resolved_at, dict(type="resolved", **base, text=(
                    f"{d['name']}: signal yopildi — {live_engine.RESOLUTION_TEXT.get(a.resolution_code, a.resolution_code)}"))))
        items.sort(key=lambda x: x[0])
        for at, ev in items[-limit:]:
            bus.publish(at=at, live=False, **ev)
        return len(items[-limit:])


# ------------------------------------------------------------------ nurse
DUE_INTERVAL = {"red": 1, "yellow": 7, "green": 14, "grey": 0}
NURSE_TREND_REASON = {"T_BP_RISING": "Bosim {n} tashrifda oshib boryapti", "T_HB_FALLING": "Gemoglobin pasayib boryapti",
                      "T_WEIGHT_GAIN": "Vazn tez oshyapti"}


@router.get("/nurse/today")
def nurse_today(request: Request, user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    _require_persona(user, request, "nurse", "live.nurse.today", "user", user.id)
    nurse = user
    masked = False  # only the nurse herself reaches this point
    today = local_date()
    q = (select(Pregnancy).join(CareAssignment, CareAssignment.mother_id == Pregnancy.mother_id)
         .where(CareAssignment.user_id == nurse.id, CareAssignment.active.is_(True), Pregnancy.status == "active"))
    tasks, done = [], 0
    for preg in db.execute(q).scalars():
        b = _preg_brief(db, preg)
        zone = preg.current_risk_level
        last = b["obs"][-1] if b["obs"] else None
        days = (today - local_date(last.observed_at)).days if last else None
        done += bool(last and days == 0 and last.created_by == nurse.id)
        ra = _latest_ra(db, preg.id)
        triggered = ra.triggered_rules if ra else []
        t_ids = [t["rule_id"] for t in triggered if t["rule_id"].startswith("T_")]
        rule_hits = [t for t in triggered if not t["rule_id"].startswith("T_")]
        interval = DUE_INTERVAL.get(zone, 14)
        due = "overdue" if days is None or days > interval else "today" if days == interval else "soon"
        if zone in ("red", "yellow") and rule_hits:
            reason = trend.reasons_for(rule_hits, b["visits"][-1] if b["visits"] else None)[0]["text"]
        elif t_ids:
            n_bp = min(3, sum(1 for v in b["visits"] if v.get("systolic_bp")))
            reason = NURSE_TREND_REASON[t_ids[0]].format(n=n_bp)
        elif days is None:
            reason = "Birinchi tashrif kutilmoqda"
        elif zone == "grey":
            reason = f"Uzoq vaqt tashrif boʻlmagan ({days} kun)"
        elif due == "overdue":
            reason = f"Tashrif muddati oʻtgan ({days} kun oldin koʻrilgan)"
        elif due == "today":
            reason = "Bugun rejali tashrif"
        else:
            reason = f"Rejali tashrif {interval - days} kundan soʻng"
        code = b["mother"].local_code if b["mother"] else None
        tasks.append({"pregnancy_id": preg.id, "name": (code or "—") if masked else _name(b["mother"]),
                      "age": _age(b["mother"]), "weeks": b["weeks"],
                      "mahalla": None if masked else _mahalla(b["mother"]), "zone": zone, "reason": reason, "due": due,
                      "last_visit_days": days, "trend_flag": bool(t_ids), "newborn": False,
                      "code": b["mother"].local_code if b["mother"] else None})
    order = {"overdue": 2, "today": 3, "soon": 4}
    tasks.sort(key=lambda t: (0 if t["zone"] == "red" else 1 if t["zone"] == "yellow" else order[t["due"]],
                              -(t["last_visit_days"] or 0)))
    d = _user_payload(nurse)
    _audit(user, "live.nurse.today", "user", nurse.id, request)
    return {"nurse": {"name": nurse.full_name, "district_name": d["district_name"], "district_code": d["district_code"]},
            "date": today.isoformat(), "done_today": done, "tasks": tasks[:12]}


class VoiceIn(BaseModel):
    model_config = {"extra": "forbid"}
    text: str = Field(max_length=2000)


@router.post("/voice/parse")
def voice_parse(body: VoiceIn, user: User = Depends(live_user)):
    _limit(user)
    return parse_visit(body.text)  # transcript is neither stored nor logged


PROT = {"neg", "trace", "1+", "2+", "3+", "4+"}


class VisitIn(BaseModel):
    model_config = {"extra": "forbid"}
    pregnancy_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    systolic_bp: int | None = Field(None, ge=50, le=260)
    diastolic_bp: int | None = Field(None, ge=30, le=170)
    heart_rate: int | None = Field(None, ge=30, le=220)
    temperature_c: float | None = Field(None, ge=30, le=43)
    weight_kg: float | None = Field(None, ge=30, le=200)
    hemoglobin_g_l: float | None = Field(None, ge=30, le=200)
    glucose_mmol_l: float | None = Field(None, ge=1, le=35)
    proteinuria: str | None = None
    severe_headache: bool | None = False
    visual_disturbance: bool | None = False
    vaginal_bleeding: bool | None = False
    reduced_fetal_movements: bool | None = False
    convulsions: bool | None = False
    source: Literal["voice", "manual"] = "manual"

    @field_validator("proteinuria", mode="before")
    @classmethod
    def _prot(cls, v):
        if v in (None, "", "null"):
            return None
        if v not in PROT:
            raise ValueError("proteinuria must be one of neg|trace|1+|2+|3+|4+")
        return v


@router.post("/visits")
def record_visit(body: VisitIn, request: Request, user: User = Depends(live_user), rdb: Session = Depends(get_db)):
    _require_persona(user, request, "nurse", "observation.create", "pregnancy", body.pregnancy_id)
    _limit(user)
    geo.load(rdb)
    target = rdb.get(Pregnancy, body.pregnancy_id)
    if target is None or target.status != "active":
        raise not_found("pregnancy")
    if not _nurse_may_visit(rdb, user, target):
        _deny(user, request, "observation.create", "pregnancy", body.pregnancy_id, "not_assigned", what="pregnancy")
    rdb.rollback()  # release the read snapshot before the serialised write
    payload = body.model_dump(exclude={"pregnancy_id", "source"})
    for k in ("severe_headache", "visual_disturbance", "vaginal_bleeding", "reduced_fetal_movements", "convulsions"):
        payload[k] = bool(payload[k])
    measured = [k for k in ("systolic_bp", "diastolic_bp", "heart_rate", "temperature_c", "weight_kg", "hemoglobin_g_l",
                            "glucose_mmol_l", "proteinuria") if payload[k] is not None]
    if not measured and not any(payload[k] for k in trend.SYMPTOM_UZ):
        raise AppError(422, "empty_visit", "Kamida bitta koʻrsatkich yoki belgi kiriting")
    if payload["systolic_bp"] is not None and payload["diastolic_bp"] is not None and payload["systolic_bp"] <= payload["diastolic_bp"]:
        raise AppError(422, "invalid_bp", "Yuqori bosim pastki bosimdan katta boʻlishi kerak")
    with write_tx() as db:
        preg = db.get(Pregnancy, body.pregnancy_id)
        if preg is None or preg.status != "active":
            raise not_found("pregnancy")
        res = live_engine.record_visit(db, preg, payload, actor=user, source=body.source,
                                       request_id=request.state.request_id)
        d = res.decision
        od = live_engine.obs_dict(res.obs)
        has_alert = res.alert is not None and res.alert.status != "resolved"
        sla = live_engine.sla_minutes(res.alert.severity) if has_alert else None
        trend_only = d.rule_level == "green" and d.zone == "yellow"
        title, message = trend.visit_message(d.zone, has_alert=has_alert, recheck=res.recheck, trend_only=trend_only,
                                             sla_minutes=sla)
        lines = list(d.trend.lines)
        if not lines:
            bp = [o for o in live_engine.load_history(db, preg.id)[0] if o.systolic_bp][-3:]
            if len(bp) >= 2 and d.zone in ("red", "yellow") and payload["systolic_bp"] is not None:
                lines.append(f"Bosim {len(bp)} tashrifda " + " → ".join(str(o.systolic_bp) for o in bp))
        specialist = live_engine.specialist_label(db.get(Mother, preg.mother_id).region_soato)
        return {
            "zone": d.zone, "title": title, "message": message,
            "reasons": trend.reasons_for(d.triggered, od),
            "trend": lines,
            "action_card": trend.action_card([t["rule_id"] for t in d.triggered], d.zone),
            "alert": ({"id": res.alert.id, "sent_to": f"{live_engine.short_name(res.sent_to) or specialist} (OvaBMU)",
                       "sla_minutes": sla, "severity": res.alert.severity, "new": res.new_alert} if has_alert else None),
            "recheck_minutes": 15 if res.recheck else None,
            "next_visit_days": trend.NEXT_VISIT_DAYS.get(d.zone, 14),
            "shock_index": d.trend.shock_index, "visit_id": res.obs.id,
        }


# ------------------------------------------------------------------ specialist
@router.get("/inbox")
def inbox(request: Request, user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    _require_persona(user, request, "specialist", "live.inbox.read", "alert")
    now = utcnow()
    facs = _region_facilities(user)  # her region only; no scope = nothing
    alerts = sorted((a for a in db.execute(select(Alert).where(Alert.status.in_(("open", "acknowledged")))).scalars()
                     if a.facility_id in facs), key=lambda a: (a.severity != "red", a.created_at))
    masked = False
    day_start = datetime.combine(local_date(now), datetime.min.time(), TASHKENT).astimezone(timezone.utc)
    resolved_today = sum(1 for f in db.execute(select(Alert.facility_id).where(
        Alert.status == "resolved", Alert.resolved_at >= day_start)).scalars() if f in facs)
    items = []
    for a in alerts[:80]:
        item = {"alert_id": a.id, "severity": a.severity, "status": a.status, "subject_type": a.subject_type,
                "pregnancy_id": None, "newborn_id": None, "patient": None, "summary": "", "reasons": [],
                "created_at": iso(a.created_at), "sla_due_at": iso(a.sla_due_at),
                "waiting_minutes": _minutes(a.created_at, a.acknowledged_at or now), "escalation_level": a.escalation_level,
                "overdue": live_engine.is_overdue(a, now), "from": None}
        if a.subject_type == "pregnancy":
            preg = db.get(Pregnancy, a.subject_id)
            if preg is None:
                continue
            b = _preg_brief(db, preg)
            last = b["visits"][-1] if b["visits"] else None
            item.update(pregnancy_id=preg.id, summary=b["summary"],
                        patient={"name": b["mother"].local_code if masked else _name(b["mother"]),
                                 "age": _age(b["mother"]), "weeks": b["weeks"],
                                 "district_name": b["district"]["name"], "code": b["mother"].local_code},
                        reasons=[trend.reason_text(r, last) for r in a.rule_ids],
                        **{"from": live_engine.nurse_label(b["obs"][-1].created_by if b["obs"] else None, b["district"]["code"], preg.id[:2])})
        else:
            nb = db.get(Newborn, a.subject_id)
            if nb is None:
                continue
            b = _nb_brief(db, nb)
            item.update(newborn_id=nb.id, summary=b["summary"],
                        patient={"name": f"Chaqaloq {nb.local_code}" if masked else b["name"], "age": None, "weeks": None,
                                 "age_days": b["age_days"],
                                 "district_name": b["district"]["name"], "code": nb.local_code},
                        reasons=[trend.reason_text(r, b["last"]) for r in a.rule_ids],
                        **{"from": live_engine.nurse_label(b["obs"][-1].created_by if b["obs"] else None, b["district"]["code"], nb.id[:2])})
        items.append(item)
    _audit(user, "live.inbox.read", "alert", None, request, {"items": len(items)})
    return {"counts": {"waiting": sum(a.status == "open" for a in alerts),
                       "acknowledged": sum(a.status == "acknowledged" for a in alerts),
                       "resolved_today": resolved_today},
            "items": items}


def _visit_zone(rules, o: MaternalObservation) -> str:
    facts = {f: getattr(o, f) for f in risk_engine.OBS_FIELDS}
    facts["gestational_week"] = o.gestational_week
    return clinical_rules.evaluate(rules, "pregnancy", trend.derive_facts(facts)).level


_NO_ML = {"label": "Sinov rejimidagi model uchun maʼlumot yetarli emas (qarorga taʼsir qilmaydi)",
          "probability": None, "version": None, "status": "shadow"}


def _last_known(obs: list, field: str):
    return next((getattr(o, field) for o in reversed(obs) if getattr(o, field, None) is not None), None)


def _ml_label(db: Session, preg: Pregnancy, b: dict, ra: RiskAssessment | None) -> dict:
    """Display-only label of the shadow model ("sinov rejimi"). Never read by rules, zones or alerts; fails soft."""
    try:
        m, obs = b["mother"], b["obs"]
        res = shadow_models.maternal_v2(
            age_years=_age(m), systolic_bp=_last_known(obs, "systolic_bp"), diastolic_bp=_last_known(obs, "diastolic_bp"),
            glucose_mmol_l=b["glucose"][-1][1] if b["glucose"] else None,  # latest known value, display only
            temperature_c=_last_known(obs, "temperature_c"), heart_rate=_last_known(obs, "heart_rate"))
        if res and res.get("status") == "ok" and "high" in res["classes"]:
            p = res["probabilities"][res["classes"].index("high")]
            return {"label": f"Yuqori xavf ehtimoli {p:.2f} (sinov rejimidagi model, qarorga taʼsir qilmaydi)",
                    "probability": p, "version": res["version"], "model": res["model"], "status": "shadow",
                    "probabilities": dict(zip(res["classes"], res["probabilities"])),
                    "note": "BMI va HbA1c kiritilmagan; model xorijiy ochiq maʼlumotlarda oʻqitilgan, mahalliy validatsiya yoʻq"}
        if res and res.get("status") == "insufficient_inputs":
            return _NO_ML | {"version": res.get("version"), "model": shadow_models.MATERNAL_V2, "missing": res["missing"]}
    except Exception:  # noqa: BLE001 - soft fail, fall through to the stored baseline
        pass
    shadow = ra.ml_shadow if ra and ra.ml_shadow and ra.ml_shadow.get("status") == "ok" else None
    if shadow is None:
        try:
            facts = risk_engine.pregnancy_facts(db, preg)
            if facts.get("lab_glucose_fasting_mmol_l") is None and b["glucose"]:
                facts["lab_glucose_fasting_mmol_l"] = b["glucose"][-1][1]  # latest known value, display only
            shadow = risk_engine.ml_shadow(db, preg, facts)
        except Exception:  # noqa: BLE001
            shadow = None
    if shadow and shadow.get("status") == "ok" and "high" in shadow.get("classes", []):
        p = shadow["probabilities"][shadow["classes"].index("high")]
        return {"label": f"Yuqori xavf ehtimoli {p:.2f} (sinov rejimidagi model, qarorga taʼsir qilmaydi)",
                "probability": p, "version": shadow.get("version"), "status": "shadow"}
    return dict(_NO_ML)


def _deny_leader(user: User, request: Request, resource_type: str, resource_id: str) -> None:
    if is_leader(user):
        _audit(user, f"live.{'patient' if resource_type == 'pregnancy' else 'newborn'}.read", resource_type, resource_id,
               request, {"reason": "leader_identity_access"}, outcome="denied")
        raise forbidden("Monitoring markazi uchun bemor kartasi yopiq: faqat umumlashtirilgan maʼlumot va kodlar koʻrinadi")


def _card_scope(db: Session, user: User, request: Request, kind: str, subject_id: str, facility_id: str,
                mother_id: str | None) -> None:
    """Nurse: only her assigned patients. Specialist: patients of her region that have (or had) an alert. Else 404."""
    action = "live.patient.read" if kind == "pregnancy" else "live.newborn.read"
    what = "patient" if kind == "pregnancy" else "newborn"
    if persona_of(user) == "nurse":
        ok = bool(mother_id and mother_id in _nurse_mothers(db, user))
        if not ok and kind == "newborn":
            ok = db.execute(select(CareAssignment.id).where(
                CareAssignment.user_id == user.id, CareAssignment.active.is_(True),
                CareAssignment.newborn_id == subject_id).limit(1)).first() is not None
        if not ok:
            _deny(user, request, action, kind, subject_id, "not_assigned", what=what)
    else:  # specialist
        if facility_id not in _region_facilities(user):
            _deny(user, request, action, kind, subject_id, "outside_region", what=what)
        if not _has_alert(db, subject_id):
            _deny(user, request, action, kind, subject_id, "no_alert_for_patient", what=what)


@router.get("/patients/{pregnancy_id}")
def patient(pregnancy_id: str, request: Request, user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    _deny_leader(user, request, "pregnancy", pregnancy_id[:36])
    preg = db.get(Pregnancy, pregnancy_id)
    if preg is None:
        raise not_found("patient")
    _card_scope(db, user, request, "pregnancy", preg.id, preg.facility_id, preg.mother_id)
    b = _preg_brief(db, preg)
    m = b["mother"]
    ra = _latest_ra(db, preg.id)
    triggered = ra.triggered_rules if ra else []
    last = b["visits"][-1] if b["visits"] else None
    tr = trend.analyze(live_engine.points_of(b["obs"]), b["hb"])
    rules = clinical_rules.active_rules(db, "pregnancy", get_settings().CLINICAL_RULES_ALLOW_UNAPPROVED)
    ra_by_obs = {r.trigger_source_id: r.level for r in db.execute(select(RiskAssessment).where(
        RiskAssessment.subject_id == preg.id).order_by(RiskAssessment.created_at)).scalars() if r.trigger_source_id}
    nurse_uid = db.execute(select(CareAssignment.user_id).where(CareAssignment.mother_id == preg.mother_id,
                                                                CareAssignment.active.is_(True))).scalars().first()
    history = [trend.HISTORY_UZ.get(h, h) for h in db.execute(select(MaternalHistory.factor_code).where(
        MaternalHistory.mother_id == preg.mother_id, MaternalHistory.present.is_(True))).scalars()]
    visits = []
    for o in reversed(b["obs"]):
        od = live_engine.obs_dict(o)
        visits.append({"at": iso(o.observed_at), "week": int(o.gestational_week) if o.gestational_week is not None else None,
                       "sbp": o.systolic_bp, "dbp": o.diastolic_bp, "hr": o.heart_rate, "temperature_c": o.temperature_c,
                       "weight_kg": o.weight_kg, "proteinuria": o.proteinuria, "symptoms": trend.symptoms_of(od),
                       "zone": ra_by_obs.get(o.id) or _visit_zone(rules, o), "source": o.source,
                       "by": live_engine.nurse_label(o.created_by or nurse_uid, b["district"]["code"], preg.id[:2])})
    alerts = list(db.execute(select(Alert).where(Alert.subject_id == preg.id).order_by(Alert.created_at.desc())).scalars())
    out = {
        "patient": {"name": _name(m), "age": _age(m), "code": m.local_code, "weeks": b["weeks"],
                    "edd": preg.edd.isoformat() if preg.edd else None, "gravida": preg.gravida, "parity": preg.parity,
                    "district_name": b["district"]["name"], "district_code": b["district"]["code"], "mahalla": _mahalla(m),
                    "nurse": live_engine.nurse_label(nurse_uid, b["district"]["code"], preg.id[:2]).replace("Hamshira ", ""),
                    "doctor": live_engine.short_name(geo.user_name(preg.assigned_clinician_id)), "history": history},
        "pregnancy_id": preg.id, "zone": preg.current_risk_level, "summary": b["summary"],
        "reasons": trend.reasons_for(triggered, last),
        "trend": {"flags": tr.flags,
                  "bp": [{"at": iso(o.observed_at), "week": int(o.gestational_week) if o.gestational_week is not None else None,
                          "sbp": o.systolic_bp, "dbp": o.diastolic_bp} for o in b["obs"] if o.systolic_bp and o.diastolic_bp],
                  "hb": [{"at": iso(t), "week": _week_at(preg, t), "value": v} for t, v in b["hb"]],
                  "weight": [{"at": iso(o.observed_at), "week": int(o.gestational_week) if o.gestational_week is not None else None,
                              "value": o.weight_kg} for o in b["obs"] if o.weight_kg is not None]},
        "shock_index": tr.shock_index,
        "visits": visits,
        "alerts": [_alert_view(a) for a in alerts],
        "action_card": trend.action_card([t["rule_id"] for t in triggered], preg.current_risk_level),
        "ml_shadow": _ml_label(db, preg, b, ra),
    }
    _audit(user, "live.patient.read", "pregnancy", preg.id, request)
    return out


def _week_at(preg: Pregnancy, t: datetime) -> int | None:
    return max(0, (t.date() - preg.lmp_date).days // 7) if preg.lmp_date else None


@router.get("/newborns/{newborn_id}")
def newborn(newborn_id: str, request: Request, user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    _deny_leader(user, request, "newborn", newborn_id[:36])
    nb = db.get(Newborn, newborn_id)
    if nb is None:
        raise not_found("newborn")
    _card_scope(db, user, request, "newborn", nb.id, nb.facility_id, nb.mother_id)
    b = _nb_brief(db, nb)
    ra = _latest_ra(db, nb.id)
    triggered = ra.triggered_rules if ra else []
    rules = clinical_rules.active_rules(db, "newborn", get_settings().CLINICAL_RULES_ALLOW_UNAPPROVED)
    static = {"birth_weight_g": nb.birth_weight_g, "gestational_age_weeks": nb.gestational_age_weeks, "apgar_5": nb.apgar_5}
    visits = []
    for o in reversed(b["obs"]):
        od = live_engine.nb_obs_dict(o)
        visits.append({"at": iso(o.observed_at), "age_days": max(0, (o.observed_at - nb.birth_datetime).days),
                       "temperature_c": o.temperature_c, "hr": o.heart_rate, "rr": o.respiratory_rate, "spo2": o.spo2,
                       "weight_g": o.weight_g, "symptoms": trend.symptoms_of(od, trend.NB_SYMPTOM_UZ),
                       "zone": clinical_rules.evaluate(rules, "newborn", static | {k: od[k] for k in live_engine.NB_VITALS}).level,
                       "source": o.source, "by": live_engine.nurse_label(o.created_by, b["district"]["code"], nb.id[:2])})
    alerts = list(db.execute(select(Alert).where(Alert.subject_id == nb.id).order_by(Alert.created_at.desc())).scalars())
    out = {"patient": {"name": b["name"], "age_days": b["age_days"], "code": nb.local_code, "birth_weight_g": nb.birth_weight_g,
                       "gestational_age_weeks": nb.gestational_age_weeks, "apgar_5": nb.apgar_5, "sex": nb.sex,
                       "district_name": b["district"]["name"], "district_code": b["district"]["code"],
                       "mahalla": _mahalla(b["mother"])},
           "newborn_id": nb.id, "zone": nb.current_risk_level, "summary": b["summary"],
           "reasons": trend.reasons_for(triggered, b["last"]), "visits": visits, "alerts": [_alert_view(a) for a in alerts],
           "action_card": trend.action_card([t["rule_id"] for t in triggered], nb.current_risk_level, "newborn")}
    _audit(user, "live.newborn.read", "newborn", nb.id, request)
    return out


@router.post("/alerts/{alert_id}/ack")
def ack(alert_id: str, request: Request, user: User = Depends(live_user), rdb: Session = Depends(get_db)):
    _specialist_alert(rdb, user, request, alert_id, "alert.acknowledged")
    rdb.rollback()
    _limit(user)
    with write_tx() as db:
        a = db.get(Alert, alert_id)
        if a is None:
            raise not_found("alert")
        if a.status == "open":
            minutes = live_engine.acknowledge(db, a, user, request_id=request.state.request_id)
        else:  # idempotent
            minutes = _minutes(a.created_at, a.acknowledged_at or utcnow())
        return {"ok": True, "status": a.status, "response_minutes": minutes}


class DecideIn(BaseModel):
    model_config = {"extra": "forbid"}
    decision: Literal["refer", "recheck", "no_risk"]
    note: str | None = Field(None, max_length=1000)


DECISIONS = {"refer": ("referred", "Bemor perinatal markazga yoʻnaltirildi"),
             "recheck": ("clinically_managed", "Hamshiraga qayta tekshirish topshirildi"),
             "no_risk": ("false_positive", "Xavf tasdiqlanmadi, signal yopildi")}


@router.post("/alerts/{alert_id}/decide")
def decide(alert_id: str, body: DecideIn, request: Request, user: User = Depends(live_user),
           rdb: Session = Depends(get_db)):
    code, message = DECISIONS[body.decision]
    _specialist_alert(rdb, user, request, alert_id, "alert.resolved")
    rdb.rollback()
    _limit(user)
    with write_tx() as db:
        a = db.get(Alert, alert_id)
        if a is None:
            raise not_found("alert")
        if a.status == "resolved":
            return {"ok": True, "status": "resolved", "message": "Signal allaqachon yopilgan"}
        referral_id = None
        if body.decision == "refer":
            to_fac = db.execute(select(Facility).where(Facility.region_soato == a.region_soato,
                                                       Facility.facility_type == "ovabmu")).scalars().first()
            if to_fac is not None:
                now = utcnow()
                ref = Referral(subject_type=a.subject_type, mother_id=a.mother_id if a.subject_type == "pregnancy" else None,
                               newborn_id=a.newborn_id, pregnancy_id=a.subject_id if a.subject_type == "pregnancy" else None,
                               alert_id=a.id, from_facility_id=a.facility_id, to_facility_id=to_fac.id,
                               priority="emergency" if a.severity == "red" else "urgent",
                               reason_code=(a.rule_ids[0] if a.rule_ids else "clinical_review")[:60], status="accepted",
                               created_by=user.id, accepted_by=user.id, accepted_at=now,
                               consult_note_enc=encrypt(body.note), is_synthetic=a.is_synthetic)
                db.add(ref)
                db.flush()
                referral_id = ref.id
                db.add(AlertAction(alert_id=a.id, action="referred", actor_id=user.id, at=now, detail={"to_facility_id": to_fac.id}))
                write_audit(db, actor=user, action="referral.create", resource_type="referral", resource_id=ref.id,
                            request_id=request.state.request_id, detail={"priority": ref.priority, "reason_code": ref.reason_code},
                            commit=False)
        elif body.note:
            db.add(AlertAction(alert_id=a.id, action="note", actor_id=user.id, at=utcnow(), detail={"note_enc": encrypt(body.note)}))
        live_engine.resolve(db, a, user, code, request_id=request.state.request_id)
        return {"ok": True, "status": a.status, "message": message, "resolution_code": code, "referral_id": referral_id}


# ------------------------------------------------------------------ power-user
_search = {"built": 0.0, "rows": [], "lock": threading.Lock()}


def _fold(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch not in "ʻʼ'‘’`´ ").replace("-", "")


def _search_index(db: Session) -> list[dict]:
    with _search["lock"]:
        if _search["rows"] and time.time() - _search["built"] < 600:
            return _search["rows"]
        rows = [{"kind": "region", "id": s, "label": n, "sub": "Viloyat", "key": _fold(n)} for s, n in geo.regions.items()]
        rows += [{"kind": "district", "id": d["code"], "label": d["name"], "sub": geo.regions.get(d["region_soato"], ""),
                  "key": _fold(d["name"])} for d in geo.districts.values()]
        now = utcnow()
        for preg_id, lmp, fid, code, name_enc, mother_id in db.execute(
                select(Pregnancy.id, Pregnancy.lmp_date, Pregnancy.facility_id, Mother.local_code, Mother.full_name_enc, Mother.id)
                .join(Mother, Mother.id == Pregnancy.mother_id).where(Pregnancy.status == "active")):
            name = decrypt(name_enc) or "—"
            weeks = max(0, (now.date() - lmp).days // 7) if lmp else None
            d = geo.district_of_facility(fid)
            rows.append({"kind": "patient", "id": preg_id, "label": name, "sub": f"{code} · {d['name']} · {weeks} hafta",
                         "key": _fold(name) + "|" + _fold(code), "code_key": _fold(code), "fid": fid, "mother_id": mother_id,
                         "masked": {"label": code, "sub": f"{d['name']} · {weeks} hafta"}})
        _search["rows"], _search["built"] = rows, time.time()
        return rows


@router.get("/search")
def search(request: Request, q: str = Query("", max_length=80), user: User = Depends(live_user), db: Session = Depends(get_db)):
    geo.load(db)
    needle = _fold(q)
    if len(needle) < 2:
        return {"results": []}
    rows = _search_index(db)
    masked = is_leader(user)  # leader: patients are searchable by record code only and are shown as codes
    persona = persona_of(user)
    if persona == "nurse":      # only her assigned women
        mine = _nurse_mothers(db, user)
        allowed = lambda r: r["mother_id"] in mine  # noqa: E731
    elif persona == "specialist":  # only patients of her region that have (or had) an alert
        facs = _region_facilities(user)
        alerted = {sid for sid, f in db.execute(select(Alert.subject_id, Alert.facility_id).where(
            Alert.subject_type == "pregnancy")) if f in facs}
        allowed = lambda r: r["fid"] in facs and r["id"] in alerted  # noqa: E731
    else:
        allowed = lambda r: True  # noqa: E731
    hits = [r for r in rows if needle in (r["code_key"] if masked and r["kind"] == "patient" else r["key"])
            and (r["kind"] != "patient" or allowed(r))]
    rank = {"region": 0, "district": 1, "patient": 2}
    hits.sort(key=lambda r: (rank[r["kind"]], not r["key"].startswith(needle), r["label"]))
    _audit(user, "live.search", "search", None, request, {"hits": len(hits)})
    return {"results": [{"kind": r["kind"], "id": r["id"], **(r["masked"] if masked and r["kind"] == "patient"
                                                               else {"label": r["label"], "sub": r["sub"]})} for r in hits[:20]]}


APPROVAL_UZ = {"approved": "Tasdiqlangan", "demo_pending_clinical_approval": "Klinik kengash tasdigʻi kutilmoqda (demo)",
               trend.EARLY: "Erta ogohlantirish (zonani faqat sariqqacha koʻtaradi)"}


@router.get("/rules")
def rules(user: User = Depends(live_user), db: Session = Depends(get_db)):
    items = []
    latest: dict[str, ClinicalRuleVersion] = {}
    for r in db.execute(select(ClinicalRuleVersion).where(ClinicalRuleVersion.active.is_(True))).scalars():
        if r.rule_id not in latest or r.version > latest[r.rule_id].version:
            latest[r.rule_id] = r
    for r in latest.values():
        label, ref = trend.RULE_UZ.get(r.rule_id, (r.rule_id, r.clinical_reference))
        items.append({"rule_id": r.rule_id, "subject_type": r.subject_type, "severity": r.severity, "label": label,
                      "reference": ref, "reference_full": r.clinical_reference, "version": r.version,
                      "approval_status": r.approval_status, "approval_label": APPROVAL_UZ.get(r.approval_status, r.approval_status)})
    for t in trend.TREND_RULES:
        label, ref = trend.RULE_UZ[t["rule_id"]]
        items.append({**t, "label": label, "reference": ref, "reference_full": "Least-squares slope / change between visits",
                      "version": 1, "approval_status": trend.EARLY, "approval_label": APPROVAL_UZ[trend.EARLY]})
    sev = {"red": 0, "yellow": 1}
    items.sort(key=lambda i: (i["subject_type"] != "pregnancy", i["rule_id"].startswith("T_"), sev.get(i["severity"], 2), i["rule_id"]))
    return {"items": items}


ACTION_UZ = {
    "auth.demo_login": "Tizimga kirish (demo)", "auth.login": "Tizimga kirish", "observation.create": "Tashrif qayd etildi",
    "newborn_observation.create": "Chaqaloq koʻrigi qayd etildi", "alert.created": "Signal yaratildi",
    "alert.acknowledged": "Signal qabul qilindi", "alert.resolved": "Signal yopildi",
    "alert.escalated": "Signal yuqori bosqichga oʻtkazildi", "referral.create": "Yoʻllanma yaratildi",
    "live.patient.read": "Bemor kartasi ochildi", "live.newborn.read": "Chaqaloq kartasi ochildi",
    "live.inbox.read": "Signallar roʻyxati koʻrildi", "live.nurse.today": "Kunlik reja koʻrildi",
    "live.district.read": "Tuman maʼlumoti koʻrildi", "live.search": "Qidiruv", "live.audit.read": "Audit jurnali koʻrildi",
    "live.demo.scenario": "Demo ssenariy ishga tushirildi", "seed.national": "Sintetik maʼlumotlar yuklandi",
    "live.ai.brief": "AI qisqa tahlil soʻraldi (faqat agregatlar)", "live.ai.privacy": "Maxfiylik siyosati koʻrildi",
    "live.access": "Kirishga urinish", "account.start": "Bir martalik kod soʻraldi", "account.register": "Roʻyxatdan oʻtish",
    "account.login": "Tizimga kirish", "account.second_factor": "Ikkinchi bosqich tekshiruvi",
    "account.logout": "Tizimdan chiqish", "account.totp.setup": "Autentifikator ilovasi sozlandi",
    "account.totp.enable": "Autentifikator ilovasi yoqildi", "account.totp.disable": "Autentifikator ilovasi oʻchirildi",
    "account.passkey.add": "Kirish kaliti qoʻshildi", "account.passkey.delete": "Kirish kaliti oʻchirildi",
    "account.two_step": "Ikki bosqichli himoya oʻzgartirildi", "account.security.read": "Xavfsizlik sozlamalari koʻrildi",
    "account.google": "Google orqali kirish", "admin.overview.read": "Boshqaruv paneli koʻrildi",
    "admin.users.read": "Foydalanuvchilar roʻyxati koʻrildi", "admin.user.update": "Foydalanuvchi roli oʻzgartirildi",
    "admin.export": "Agregat hisobot yuklab olindi", "admin.audit.read": "Audit jurnali koʻrildi (admin)",
    "admin.access": "Boshqaruv boʻlimiga kirishga urinish",
}
RESOURCE_UZ = {"alert": "signal", "pregnancy": "homiladorlik", "newborn": "chaqaloq", "user": "foydalanuvchi",
               "referral": "yoʻllanma", "district": "tuman", "search": "qidiruv", "database": "baza", "session": "sessiya"}
_chain = {"at": 0.0, "ok": True}


def audit_payload(db: Session, limit: int) -> dict:
    geo.load(db)
    if time.time() - _chain["at"] > 20:
        with live_engine.write_lock:  # a consistent snapshot of the chain (no writer in between)
            _chain["ok"], _chain["at"] = bool(verify_chain(db)["valid"]), time.time()
    rows = list(db.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(limit)).scalars())
    items = [{"at": iso(e.at), "actor": geo.user_name(e.actor_id) or "Tizim", "action": ACTION_UZ.get(e.action, e.action),
              "resource": RESOURCE_UZ.get(e.resource_type, e.resource_type) + (f" #{e.resource_id[:8]}" if e.resource_id else ""),
              "action_code": e.action, "outcome": e.outcome, "seq": e.seq} for e in rows]
    return {"items": items, "chain_ok": _chain["ok"]}


@router.get("/audit")
def audit(request: Request, limit: int = Query(50, ge=1, le=500), user: User = Depends(live_user), db: Session = Depends(get_db)):
    return audit_payload(db, limit)


# ------------------------------------------------------------------ demo helper
class ScenarioIn(BaseModel):
    model_config = {"extra": "forbid"}
    name: Literal["preeclampsia", "reset"] = "preeclampsia"
    delay_seconds: float = Field(2.0, ge=0, le=60)


def _demo_pregnancy(db: Session) -> Pregnancy | None:
    """The stage patient: the nurse persona's woman with the rising-BP early warning."""
    if not geo.demo_nurse_id:
        return None
    q = (select(Pregnancy).join(CareAssignment, CareAssignment.mother_id == Pregnancy.mother_id)
         .where(CareAssignment.user_id == geo.demo_nurse_id, Pregnancy.status == "active"))
    for p in db.execute(q).scalars():
        if _name(db.get(Mother, p.mother_id)) == "Madina Tursunova":
            return p
    return None


def _run_preeclampsia(delay: float) -> None:
    time.sleep(delay)
    with write_tx() as db:
        preg = _demo_pregnancy(db)
        nurse = db.get(User, geo.demo_nurse_id) if geo.demo_nurse_id else None
        if preg is not None:
            live_engine.record_visit(db, preg, {"systolic_bp": 150, "diastolic_bp": 95, "heart_rate": 88,
                                                "severe_headache": True}, actor=nurse, source="voice")


def _reset_demo() -> dict:
    """Rehearsal helper: remove today's stage visits/alerts of the demo patient and re-assess her."""
    from sqlalchemy import delete
    from app.models import AlertRecipient, LabResult
    with write_tx() as db:
        preg = _demo_pregnancy(db)
        if preg is None:
            return {"ok": False}
        cutoff = utcnow() - timedelta(days=5)
        alert_ids = list(db.execute(select(Alert.id).where(Alert.subject_id == preg.id, Alert.created_at >= cutoff)).scalars())
        if alert_ids:
            db.execute(delete(Referral).where(Referral.alert_id.in_(alert_ids)))
            db.execute(delete(AlertRecipient).where(AlertRecipient.alert_id.in_(alert_ids)))
            db.execute(delete(AlertAction).where(AlertAction.alert_id.in_(alert_ids)))
            db.execute(delete(Alert).where(Alert.id.in_(alert_ids)))
        db.execute(delete(RiskAssessment).where(RiskAssessment.subject_id == preg.id, RiskAssessment.created_at >= cutoff))
        n = db.execute(delete(MaternalObservation).where(MaternalObservation.pregnancy_id == preg.id,
                                                         MaternalObservation.observed_at >= cutoff)).rowcount
        db.execute(delete(LabResult).where(LabResult.pregnancy_id == preg.id, LabResult.collected_at >= cutoff))
        db.flush()
        res = live_engine.assess_pregnancy(db, preg, db.get(Mother, preg.mother_id), with_ml=False)
        return {"ok": True, "removed_visits": n, "removed_alerts": len(alert_ids), "zone": res.decision.zone,
                "pregnancy_id": preg.id}


@router.post("/demo/scenario")
def demo_scenario(body: ScenarioIn, request: Request, user: User = Depends(live_user), db: Session = Depends(get_db)):
    if get_settings().is_production or not get_settings().LIVE_DEMO_LOGIN:
        raise AppError(403, "forbidden", "Demo helper is disabled")
    _limit(user)
    geo.load(db)
    _audit(user, "live.demo.scenario", "user", user.id, request, {"scenario": body.name})
    if body.name == "reset":
        return {"scheduled": False, "name": "reset", **_reset_demo()}
    preg = _demo_pregnancy(db)
    if preg is None:
        raise AppError(503, "not_seeded", "Demo bemor topilmadi")
    threading.Thread(target=_run_preeclampsia, args=(body.delay_seconds,), daemon=True).start()
    return {"ok": True, "scheduled": True, "name": body.name, "pregnancy_id": preg.id, "in_seconds": body.delay_seconds}


# ------------------------------------------------------------------ shadow models (cards only)
@router.get("/models")
def models(user: User = Depends(live_user)):
    return {"items": shadow_models.model_cards(),
            "note": "Barcha modellar sinov rejimida: zonalar va signallarni faqat tasdiqlangan klinik qoidalar belgilaydi."}


# ------------------------------------------------------------------ AI brief (k-anonymised aggregates only)
class BriefIn(BaseModel):
    model_config = {"extra": "forbid"}
    scope: Literal["national", "region", "district"] = "national"
    id: str | None = Field(None, max_length=16, pattern=r"^[A-Za-z0-9-]+$")


_brief_cache: dict[tuple[str, str | None], tuple[float, dict]] = {}
_OFFICIAL_RATES = ("infant_mortality_rate", "under5_mortality_rate", "maternal_mortality_ratio")


def _brief_raw(db: Session, scope: str, area_id: str | None) -> dict:
    """Aggregate numbers only: GROUP BY counts over the scope. No patient row, name or code is read into the result."""
    now = utcnow()
    if scope == "national":
        facs, name, area = None, "Oʻzbekiston", "1700"
    elif scope == "region":
        if area_id not in geo.regions:
            raise not_found("region")
        facs, name, area = {f for f, r in geo.fac_region.items() if r == area_id}, geo.regions[area_id], area_id
    else:
        if area_id not in geo.districts:
            raise not_found("district")
        facs, name = {f for f, c in geo.fac_district.items() if c == area_id}, geo.districts[area_id]["name"]
        area = _official().mapping.get(area_id)
    inside = (lambda f: True) if facs is None else (lambda f: f in facs)
    zones = {"pregnant": 0, "red": 0, "yellow": 0, "green": 0, "grey": 0, "newborns": 0}
    for fid, level, n in db.execute(select(Pregnancy.facility_id, Pregnancy.current_risk_level, func.count())
                                    .where(Pregnancy.status == "active").group_by(Pregnancy.facility_id, Pregnancy.current_risk_level)):
        if inside(fid):
            zones["pregnant"] += n
            zones[level if level in ZONES else "grey"] += n
    for fid, n in db.execute(select(Newborn.facility_id, func.count()).group_by(Newborn.facility_id)):
        if inside(fid):
            zones["newborns"] += n
    alerts = {"open": 0, "overdue": 0, "open_mothers": 0, "open_newborns": 0}
    rule_counts: dict[str, int] = {}
    for fid, status, subject, sev, due, esc, rule_ids in db.execute(
            select(Alert.facility_id, Alert.status, Alert.subject_type, Alert.severity, Alert.sla_due_at,
                   Alert.escalation_level, Alert.rule_ids).where(Alert.status.in_(("open", "acknowledged")))):
        if not inside(fid):
            continue
        for r in rule_ids or []:
            rule_counts[r] = rule_counts.get(r, 0) + 1
        if status == "open":
            alerts["open"] += 1
            alerts["open_mothers" if subject == "pregnancy" else "open_newborns"] += 1
            alerts["overdue"] += bool(esc) or bool(due and due < now)
    median, answered = _response_stats(db, now - timedelta(days=7), facs)
    st = _official()
    official, births = [], None
    if area:
        for code in _OFFICIAL_RATES:
            ys = [y for y in st.years(code) if st.value(code, area, y) is not None]
            if ys:
                y = ys[-1]
                official.append({"code": code, "year": y, "value": st.value(code, area, y), "national": st.value(code, "1700", y),
                                 "status": st.status(code, area, y)[0], "events": st.events(code, area, y)})
        by = [y for y in st.years("births_total") if st.value("births_total", area, y) is not None]
        births = st.value("births_total", area, by[-1]) if by else None
    return {"scope": scope, "area_name": name, "zones": zones, "alerts": alerts, "answered": answered,
            "median_response_min": median, "rule_counts": rule_counts, "official": official, "births": births}


def _known_areas() -> frozenset[str]:
    return frozenset({"Oʻzbekiston", *geo.regions.values(), *(d["name"] for d in geo.districts.values())})


def _store_privacy_event(ev: PrivacyEvent) -> None:
    with write_tx() as db:
        db.add(ev)


def brief_gateway() -> privacy_gateway.BriefGateway:
    return privacy_gateway.BriefGateway(event_sink=_store_privacy_event)


@router.post("/ai/brief")
def ai_brief(body: BriefIn, request: Request, user: User = Depends(live_user), db: Session = Depends(get_db),
             gateway: privacy_gateway.BriefGateway = Depends(brief_gateway)):
    """3-5 Uzbek sentences: what needs attention now. Only k-anonymised aggregates may reach the external model;
    without a key, on error or after 6 s the same aggregates produce a deterministic local text."""
    _limit(user, "ai", 20)
    geo.load(db)
    if body.scope != "national" and not body.id:
        raise AppError(422, "validation_error", "id is required for region and district scope")
    key = (body.scope, body.id if body.scope != "national" else None)
    hit = _brief_cache.get(key)
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1] | {"cached": True}
    raw = _brief_raw(db, body.scope, key[1])
    res = gateway.brief(user, raw, _known_areas())
    out = {"text": res["text"], "source": res["source"], "sent_fields": res["sent_fields"], "blocked": res["blocked"],
           "generated_at": iso(utcnow()), "scope": body.scope, "id": key[1], "area_name": raw["area_name"],
           "model": res["model"], "reasons": res["reasons"], "k_anonymity": privacy_gateway.K_ANON, "cached": False}
    _brief_cache[key] = (time.monotonic(), out)
    _audit(user, "live.ai.brief", "aggregate", key[1] or "1700", request,
           {"scope": body.scope, "source": res["source"], "blocked": res["blocked"]})
    return out


@router.get("/ai/privacy")
def ai_privacy(request: Request, user: User = Depends(live_user), db: Session = Depends(get_db)):
    """The exact outbound-data policy of the Privacy Gateway + the last 20 gateway decisions (metadata only)."""
    s = get_settings()
    _audit(user, "live.ai.privacy", "policy", None, request)
    return {**privacy_gateway.BRIEF_POLICY,
            "model": s.CLAUDE_MODEL, "provider": "Anthropic Claude API",
            "external_enabled": bool(s.CLAUDE_ENABLED and s.CLAUDE_API_KEY and s.CLAUDE_ALLOW_KANON_AGGREGATES),
            "mode": "claude-haiku" if (s.CLAUDE_ENABLED and s.CLAUDE_API_KEY and s.CLAUDE_ALLOW_KANON_AGGREGATES) else "local",
            "settings": {"CLAUDE_EXTERNAL_PATIENT_DATA": s.CLAUDE_EXTERNAL_PATIENT_DATA,
                         "CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA": s.CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA,
                         "CLAUDE_ALLOW_KANON_AGGREGATES": s.CLAUDE_ALLOW_KANON_AGGREGATES,
                         "CLAUDE_LOG_PROMPT_BODY": s.CLAUDE_LOG_PROMPT_BODY, "timeout_seconds": s.CLAUDE_BRIEF_TIMEOUT_SECONDS},
            "schema": privacy_gateway.AggregateBriefPayload.model_json_schema(),
            "decisions": privacy_gateway.gateway_decisions(db, 20)}

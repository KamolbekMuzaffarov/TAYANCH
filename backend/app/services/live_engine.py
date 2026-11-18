from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.security import utcnow
from app.db import SessionLocal
from app.models import (Alert, AlertAction, AlertRecipient, District, Facility, LabResult, MaternalObservation, Mother,
                        Newborn, NewbornObservation, Pregnancy, Region, RiskAssessment, Role, ScopeType, User, UserScope)
from app.services import alert_engine, clinical_rules, risk_engine, trend
from app.services.live_bus import bus

write_lock = threading.RLock()


@contextmanager
def write_tx():
    """Serialised short write transaction. Events queued in db.info['events'] are published after commit."""
    with write_lock:
        db = SessionLocal()
        try:
            yield db
            db.commit()
            events = db.info.pop("events", [])
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    for ev in events:
        bus.publish(**ev)


# ------------------------------------------------------------------ small static caches (master data)
class _Geo:
    def __init__(self):
        self.loaded = False
        self.districts: dict[str, dict] = {}
        self.regions: dict[str, str] = {}
        self.fac_district: dict[str, str | None] = {}
        self.fac_region: dict[str, str] = {}
        self.ova: dict[str, tuple[str, str]] = {}      # region -> (user_id, full_name)
        self.users: dict[str, tuple[str, str]] = {}    # id -> (full_name, role)
        self.demo_district: str | None = None
        self.demo_nurse_id: str | None = None

    def load(self, db: Session, force: bool = False) -> "_Geo":
        if self.loaded and not force:
            return self
        self.regions = {r.soato: trend.uz(r.name_uz) for r in db.execute(select(Region)).scalars()}
        self.districts = {c: {"code": c, "name": trend.uz(n), "region_soato": r} for c, n, r in
                          db.execute(select(District.code, District.name, District.region_soato))}
        self.fac_district, self.fac_region = {}, {}
        ova_fac = {}
        for fid, dc, rs, ft in db.execute(select(Facility.id, Facility.district_code, Facility.region_soato, Facility.facility_type)):
            self.fac_district[fid], self.fac_region[fid] = dc, rs
            if ft == "ovabmu":
                ova_fac[fid] = rs
        self.users = {u.id: (u.full_name, u.role.value) for u in db.execute(select(User)).scalars()}
        self.ova = {}
        q = (select(User.id, User.full_name, UserScope.facility_id).join(UserScope, UserScope.user_id == User.id)
             .where(User.role == Role.OVABMU_SPECIALIST, UserScope.scope_type == ScopeType.FACILITY).order_by(User.username))
        for uid, name, fid in db.execute(q):
            if fid in ova_fac and (ova_fac[fid] not in self.ova or name.startswith("Dr.")):
                self.ova.setdefault(ova_fac[fid], (uid, name))
        nurse = db.execute(select(User).where(User.username == "demo.nurse")).scalar_one_or_none()
        if nurse:
            self.demo_nurse_id = nurse.id
            self.demo_district = next((s.district_code for s in nurse.scopes if s.district_code), None)
            spec = db.execute(select(User).where(User.username == "demo.specialist")).scalar_one_or_none()
            if spec and self.demo_district:
                self.ova[self.districts[self.demo_district]["region_soato"]] = (spec.id, spec.full_name)
        self.loaded = bool(self.districts)
        return self

    def district_of_facility(self, facility_id: str) -> dict:
        code = self.fac_district.get(facility_id)
        d = self.districts.get(code) if code else None
        return d or {"code": None, "name": self.regions.get(self.fac_region.get(facility_id, ""), "—"),
                     "region_soato": self.fac_region.get(facility_id)}

    def user_name(self, user_id: str | None) -> str | None:
        u = self.users.get(user_id) if user_id else None
        return u[0] if u else None


geo = _Geo()

_NURSE_FIRST = ["Dilfuza", "Gulchehra", "Mavluda", "Nasiba", "Oydin", "Rayhon", "Saodat", "Zulfiya", "Hilola", "Munira",
                "Nodira", "Sarvinoz", "Umida", "Yulduz", "Lola", "Malika"]
_NURSE_LAST = ["Abdullayeva", "Boboyeva", "Eshmatova", "Gʻaniyeva", "Hamidova", "Joʻrayeva", "Mamatova", "Normatova",
               "Olimova", "Qosimova", "Rustamova", "Sobirova", "Toshpoʻlatova", "Usmonova", "Xoliqova", "Zokirova"]


def short_name(full: str | None) -> str | None:
    """'Dr. Nodira Yusupova' -> 'Dr. N. Yusupova'; 'Dilnoza Karimova' -> 'D. Karimova'."""
    if not full:
        return None
    prefix = ""
    parts = full.split()
    if parts and parts[0].rstrip(".").lower() == "dr":
        prefix, parts = "Dr. ", parts[1:]
    if len(parts) >= 2:
        return f"{prefix}{parts[0][0]}. {' '.join(parts[1:])}"
    return prefix + " ".join(parts)


def nurse_label(user_id: str | None, district_code: str | None, salt: str = "") -> str:
    """Who recorded a visit. Real nurse user if there is one, otherwise a stable synthetic nurse of the district."""
    u = geo.users.get(user_id) if user_id else None
    if u and u[1] == Role.PATRONAGE_NURSE.value:
        return "Hamshira " + short_name(u[0])
    h = sum(ord(c) for c in (district_code or "") + salt)
    return f"Hamshira {_NURSE_FIRST[h % len(_NURSE_FIRST)][0]}. {_NURSE_LAST[(h // 7) % len(_NURSE_LAST)]}"


def specialist_label(region_soato: str | None) -> str | None:
    o = geo.ova.get(region_soato or "")
    return short_name(o[1]) if o else None


# ------------------------------------------------------------------ decision (pure)
@dataclass
class Decision:
    zone: str                     # final zone shown to users
    rule_level: str               # zone from the clinical rules alone (drives alerts)
    triggered: list[dict]         # rule hits + early-warning entries
    missing: list[str]
    rule_set_version: str
    trend: trend.TrendResult
    facts: dict = field(default_factory=dict)


def decide(rules: list, facts: dict, points: list[trend.Point], hb: list[tuple[datetime, float]]) -> Decision:
    trend.derive_facts(facts)
    out = clinical_rules.evaluate(rules, "pregnancy", facts)
    tr = trend.analyze(points, hb)
    zone = out.level
    triggered = list(out.triggered)
    if tr.rule_ids:
        triggered += trend.trend_triggers(tr)
    if zone == "green" and tr.early_warning:
        zone = "yellow"  # early warning: at most yellow, never lowers
    return Decision(zone, out.level, triggered, out.missing, out.rule_set_version, tr, facts)


def obs_dict(o) -> dict:
    return {f: getattr(o, f) for f in risk_engine.OBS_FIELDS} | {"gestational_week": o.gestational_week, "observed_at": o.observed_at}


def load_history(db: Session, pregnancy_id: str):
    obs = list(db.execute(select(MaternalObservation).where(MaternalObservation.pregnancy_id == pregnancy_id)
                          .order_by(MaternalObservation.observed_at)).scalars())
    labs = list(db.execute(select(LabResult).where(LabResult.pregnancy_id == pregnancy_id)
                           .order_by(LabResult.collected_at)).scalars())
    hb = [(l.collected_at, risk_engine._hb_g_l(l)) for l in labs if l.test_code == "hemoglobin"]
    glucose = [(l.collected_at, l.value) for l in labs if l.test_code == "glucose_fasting"]
    return obs, hb, glucose


def points_of(obs: list) -> list[trend.Point]:
    return [trend.Point(at=o.observed_at, week=o.gestational_week, sbp=o.systolic_bp, dbp=o.diastolic_bp,
                        hr=o.heart_rate, weight=o.weight_kg) for o in obs]


def weeks_of(preg: Pregnancy, now: datetime | None = None) -> int | None:
    if not preg.lmp_date:
        return None
    return max(0, ((now or utcnow()).date() - preg.lmp_date).days // 7)


# ------------------------------------------------------------------ alert routing
def route_to_specialist(db: Session, alert: Alert) -> str | None:
    """Add the regional OvaBMU specialist as recipient/assignee of a NEW alert. Returns her display name."""
    geo.load(db)
    o = geo.ova.get(alert.region_soato)
    if not o:
        return None
    uid, name = o
    already = db.execute(select(AlertRecipient.id).where(AlertRecipient.alert_id == alert.id, AlertRecipient.user_id == uid)).first()
    if not already:
        db.add(AlertRecipient(alert_id=alert.id, user_id=uid, channel="in_app", delivered_at=utcnow(), delivery_status="delivered"))
    alert.assigned_to = alert.assigned_to or uid
    return name


def _supersede_yellow(db: Session, subject_type: str, subject_id: str, by_alert: Alert) -> None:
    now = utcnow()
    for a in db.execute(select(Alert).where(Alert.dedup_key == f"{subject_type}:{subject_id}:yellow",
                                            Alert.status.in_(alert_engine.OPEN_STATES))).scalars():
        a.status, a.resolved_at, a.resolution_code = "resolved", now, "duplicate"
        a.acknowledged_at = a.acknowledged_at or now
        db.add(AlertAction(alert_id=a.id, action="resolved", at=now,
                           detail={"resolution_code": "duplicate", "superseded_by": by_alert.id}))


@dataclass
class Assessed:
    decision: Decision
    assessment: RiskAssessment
    alert: Alert | None = None
    new_alert: bool = False
    recheck: bool = False
    sent_to: str | None = None
    obs: object | None = None
    district: dict | None = None


def _event(db: Session, **ev) -> None:
    db.info.setdefault("events", []).append(ev)


def assess_pregnancy(db: Session, preg: Pregnancy, mother: Mother, *, actor_id: str | None = None,
                     confirmed: bool = True, with_ml: bool = True) -> Assessed:
    s = get_settings()
    t0 = utcnow()
    facts = risk_engine.pregnancy_facts(db, preg)
    rules = clinical_rules.active_rules(db, "pregnancy", s.CLINICAL_RULES_ALLOW_UNAPPROVED)
    obs, hb, _ = load_history(db, preg.id)
    d = decide(rules, facts, points_of(obs), hb)
    shadow = risk_engine.ml_shadow(db, preg, facts) if with_ml else None
    ra = RiskAssessment(subject_type="pregnancy", subject_id=preg.id, level=d.zone, triggered_rules=d.triggered,
                        missing_inputs=d.missing, rule_set_version=d.rule_set_version, ml_shadow=shadow,
                        model_version=(shadow or {}).get("version"), trigger_source_id=facts.get("_observation_id"),
                        created_by=actor_id)
    db.add(ra)
    db.flush()
    preg.current_risk_level, preg.current_risk_at = d.zone, t0
    res = Assessed(d, ra, obs=obs[-1] if obs else None)
    # A single first borderline BP reading: re-measure in 15 min before anyone is alerted.
    rule_ids = {t["rule_id"] for t in d.triggered if not t["rule_id"].startswith("T_")}
    if d.rule_level == "yellow" and rule_ids == {"M_HYPERTENSION_GE20W"} and not confirmed:
        res.recheck = True
        return res
    if d.rule_level in ("red", "yellow"):
        alert = alert_engine.raise_alert(db, subject_type="pregnancy", subject=preg, assessment=ra,
                                         region_soato=mother.region_soato, is_synthetic=preg.is_synthetic)
        res.alert = alert
        if alert is not None:
            res.new_alert = alert.created_at >= t0
            res.sent_to = route_to_specialist(db, alert)
            if res.new_alert and alert.severity == "red":
                _supersede_yellow(db, "pregnancy", preg.id, alert)
    return res


VITALS = ["systolic_bp", "diastolic_bp", "heart_rate", "respiratory_rate", "temperature_c", "spo2", "weight_kg",
          "fetal_heart_rate", "proteinuria", "severe_headache", "visual_disturbance", "vaginal_bleeding",
          "reduced_fetal_movements", "convulsions"]


def record_visit(db: Session, preg: Pregnancy, payload: dict, *, actor: User | None, source: str = "manual",
                 confirmed: bool | None = None, with_ml: bool = True, request_id: str | None = None) -> Assessed:
    """THE code path for a home visit: store observation (+labs), assess, alert, queue live events."""
    geo.load(db)
    now = utcnow()
    mother = db.get(Mother, preg.mother_id)
    prev = db.execute(select(MaternalObservation).where(MaternalObservation.pregnancy_id == preg.id)
                      .order_by(MaternalObservation.observed_at.desc()).limit(1)).scalar_one_or_none()
    week = round((now.date() - preg.lmp_date).days / 7, 1) if preg.lmp_date else None
    obs = MaternalObservation(pregnancy_id=preg.id, observed_at=now, gestational_week=week, source=source,
                              created_by=actor.id if actor else None,
                              **{k: payload.get(k) for k in VITALS if payload.get(k) is not None})
    db.add(obs)
    if payload.get("hemoglobin_g_l") is not None:
        db.add(LabResult(pregnancy_id=preg.id, test_code="hemoglobin", value=float(payload["hemoglobin_g_l"]), unit="g/L",
                         collected_at=now, facility_id=preg.facility_id, created_by=actor.id if actor else None))
    if payload.get("glucose_mmol_l") is not None:
        db.add(LabResult(pregnancy_id=preg.id, test_code="glucose_fasting", value=float(payload["glucose_mmol_l"]),
                         unit="mmol/L", collected_at=now, facility_id=preg.facility_id, created_by=actor.id if actor else None))
    db.flush()
    if confirmed is None:  # confirmed = the previous measurement was already elevated (this one is the re-check)
        confirmed = bool(prev and ((prev.systolic_bp or 0) >= 140 or (prev.diastolic_bp or 0) >= 90))
    res = assess_pregnancy(db, preg, mother, actor_id=actor.id if actor else None, confirmed=confirmed, with_ml=with_ml)
    res.obs = obs
    write_audit(db, actor=actor, action="observation.create", resource_type="pregnancy", resource_id=preg.id,
                request_id=request_id, detail={"source": source, "zone": res.decision.zone}, commit=False)
    # live events
    dist = geo.district_of_facility(preg.facility_id)
    res.district = dist
    od = obs_dict(obs)
    base = dict(district_code=dist["code"], district_name=dist["name"], region_soato=dist["region_soato"])
    zone = res.decision.zone
    if res.new_alert and res.alert is not None:
        colour = "qizil" if res.alert.severity == "red" else "sariq"
        _event(db, type="alert", zone=res.alert.severity, **base,
               text=f"{dist['name']}: yangi {colour} signal — {trend.event_reason(res.decision.triggered, od, res.decision.facts)}")
    elif zone in ("red", "yellow"):
        what = "qayta oʻlchash kerak" if res.recheck else ("erta ogohlantirish" if res.decision.rule_level == "green" else "kuzatuv davom etmoqda")
        why = trend.event_reason([t for t in res.decision.triggered if not t["rule_id"].startswith("T_")] or res.decision.triggered,
                                 od, res.decision.facts)
        if res.decision.rule_level == "green" and res.decision.trend.lines:
            why = res.decision.trend.lines[0][:1].lower() + res.decision.trend.lines[0][1:]
        _event(db, type="visit", zone=zone, **base, text=f"{dist['name']}: tashrif — {why} ({what})")
    else:
        bp = f"bosim {obs.systolic_bp}/{obs.diastolic_bp}, " if obs.systolic_bp and obs.diastolic_bp else ""
        tail = "holat meʼyorda" if zone == "green" else "maʼlumot yetarli emas"
        _event(db, type="visit", zone=zone, **base, text=f"{dist['name']}: tashrif qayd etildi — {bp}{tail}")
    return res


# ------------------------------------------------------------------ newborn
NB_VITALS = ["temperature_c", "respiratory_rate", "heart_rate", "spo2", "weight_g", "jaundice", "poor_feeding",
             "convulsions", "severe_chest_indrawing"]


def nb_obs_dict(o) -> dict:
    return {f: getattr(o, f) for f in NB_VITALS} | {"observed_at": o.observed_at}


def assess_newborn(db: Session, nb: Newborn, *, actor_id: str | None = None) -> Assessed:
    s = get_settings()
    t0 = utcnow()
    facts = risk_engine.newborn_facts(db, nb)
    rules = clinical_rules.active_rules(db, "newborn", s.CLINICAL_RULES_ALLOW_UNAPPROVED)
    out = clinical_rules.evaluate(rules, "newborn", facts)
    ra = RiskAssessment(subject_type="newborn", subject_id=nb.id, level=out.level, triggered_rules=out.triggered,
                        missing_inputs=out.missing, rule_set_version=out.rule_set_version,
                        trigger_source_id=facts.get("_observation_id"), created_by=actor_id)
    db.add(ra)
    db.flush()
    nb.current_risk_level = out.level
    d = Decision(out.level, out.level, list(out.triggered), out.missing, out.rule_set_version, trend.TrendResult(), facts)
    res = Assessed(d, ra)
    if out.level in ("red", "yellow"):
        alert = alert_engine.raise_alert(db, subject_type="newborn", subject=nb, assessment=ra,
                                         region_soato=nb.region_soato, is_synthetic=nb.is_synthetic)
        res.alert = alert
        if alert is not None:
            res.new_alert = alert.created_at >= t0
            res.sent_to = route_to_specialist(db, alert)
            if res.new_alert and alert.severity == "red":
                _supersede_yellow(db, "newborn", nb.id, alert)
    return res


def record_newborn_obs(db: Session, nb: Newborn, payload: dict, *, actor: User | None, source: str = "manual") -> Assessed:
    geo.load(db)
    obs = NewbornObservation(newborn_id=nb.id, observed_at=utcnow(), source=source, created_by=actor.id if actor else None,
                             **{k: payload.get(k) for k in NB_VITALS if payload.get(k) is not None})
    db.add(obs)
    db.flush()
    res = assess_newborn(db, nb, actor_id=actor.id if actor else None)
    res.obs = obs
    write_audit(db, actor=actor, action="newborn_observation.create", resource_type="newborn", resource_id=nb.id,
                detail={"source": source, "zone": res.decision.zone}, commit=False)
    dist = geo.district_of_facility(nb.facility_id)
    res.district = dist
    base = dict(district_code=dist["code"], district_name=dist["name"], region_soato=dist["region_soato"])
    od = nb_obs_dict(obs)
    if res.new_alert and res.alert is not None:
        colour = "qizil" if res.alert.severity == "red" else "sariq"
        _event(db, type="newborn", zone=res.alert.severity, **base,
               text=f"{dist['name']}: yangi {colour} signal (chaqaloq) — {trend.event_reason(res.decision.triggered, od)}")
    else:
        t = f"harorat {trend.num(obs.temperature_c)} °C, " if obs.temperature_c is not None else ""
        tail = {"green": "holat meʼyorda", "grey": "maʼlumot yetarli emas"}.get(res.decision.zone, "kuzatuv davom etmoqda")
        _event(db, type="newborn", zone=res.decision.zone, **base, text=f"{dist['name']}: chaqaloq koʻrigi — {t}{tail}")
    return res


# ------------------------------------------------------------------ alert state changes (+ events)
RESOLUTION_TEXT = {
    "referred": "bemor perinatal markazga yoʻnaltirildi",
    "clinically_managed": "nazoratga olindi, qayta tekshirish topshirildi",
    "false_positive": "xavf tasdiqlanmadi",
    "data_entry_error": "maʼlumot kiritish xatosi tuzatildi",
    "patient_transferred": "bemor boshqa muassasaga oʻtkazildi",
    "duplicate": "takroriy signal birlashtirildi",
}


def _alert_base(alert: Alert) -> dict:
    d = geo.district_of_facility(alert.facility_id)
    return dict(district_code=d["code"], district_name=d["name"], region_soato=d["region_soato"] or alert.region_soato)


def acknowledge(db: Session, alert: Alert, user: User, request_id: str | None = None) -> int:
    """Existing lifecycle transition + live event. Returns response time in minutes."""
    geo.load(db)
    alert_engine.transition(db, alert, user, "acknowledged", request_id=request_id)
    minutes = max(0, round((alert.acknowledged_at - alert.created_at).total_seconds() / 60))
    base = _alert_base(alert)
    _event(db, type="ack", zone=alert.severity, **base,
           text=f"{base['district_name']}: signal qabul qilindi — {short_name(user.full_name)}, {minutes} daqiqada javob berildi")
    return minutes


def resolve(db: Session, alert: Alert, user: User, resolution_code: str, request_id: str | None = None) -> None:
    geo.load(db)
    alert_engine.transition(db, alert, user, "resolved", resolution_code=resolution_code, request_id=request_id)
    base = _alert_base(alert)
    _event(db, type="resolved", zone=alert.severity, **base,
           text=f"{base['district_name']}: signal yopildi — {RESOLUTION_TEXT.get(resolution_code, resolution_code)}")


def escalation_tick() -> list[str]:
    """Scheduler tick: existing SLA escalation + one live event per escalated alert."""
    with write_tx() as db:
        geo.load(db)
        ids = alert_engine.escalate_overdue(db)
        s = get_settings()
        for aid in ids:
            a = db.get(Alert, aid)
            base = _alert_base(a)
            mins = s.ALERT_SLA_MINUTES_RED if a.severity == "red" else s.ALERT_SLA_MINUTES_YELLOW
            _event(db, type="escalation", zone=a.severity, **base,
                   text=f"{base['district_name']}: signal {mins} daqiqa javobsiz qoldi — yuqori bosqichga oʻtkazildi ({a.escalation_level}-daraja)")
        return ids


def is_overdue(alert: Alert, now: datetime | None = None) -> bool:
    """Unacknowledged and past its deadline (the SLA clock restarts on escalation, so level > 0 also counts)."""
    return alert.status == "open" and (alert.escalation_level > 0 or alert.sla_due_at < (now or utcnow()))


def sla_minutes(severity: str) -> int:
    s = get_settings()
    return s.ALERT_SLA_MINUTES_RED if severity == "red" else s.ALERT_SLA_MINUTES_YELLOW

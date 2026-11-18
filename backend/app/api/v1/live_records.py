from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.core.security import decrypt, encrypt, keyed_hash, utcnow
from app.db import get_db
from app.models import (Alert, LabResult, MaternalHistory, MaternalObservation, Mother, Pregnancy, User,
                        VisitDetail)
from app.services import live_engine, trend
from app.services.live_bus import iso
from app.services.live_engine import geo, write_tx

from app.api.v1 import live

router = APIRouter(prefix="/live/records", tags=["live-records"])

LABS: dict[str, tuple[str, str, float, float, float | None, float | None]] = {
    "hemoglobin": ("Gemoglobin", "g/L", 30, 200, 110, None),
    "erythrocytes": ("Eritrotsitlar", "10¹²/L", 1, 8, 3.5, 5.0),
    "hematocrit": ("Gematokrit", "%", 10, 65, 33, 44),
    "leukocytes": ("Leykotsitlar", "10⁹/L", 0.5, 60, 4, 15),
    "platelets": ("Trombotsitlar", "10⁹/L", 5, 1000, 150, 400),
    "esr": ("ECHT (SOE)", "mm/soat", 1, 150, None, 45),
    "glucose_fasting": ("Qand (och qoringa)", "mmol/L", 1, 35, None, 5.1),
    "hba1c": ("Glikirlangan gemoglobin (HbA1c)", "%", 3, 20, None, 6.0),
    "ogtt_1h": ("GTT 1-soat", "mmol/L", 1, 40, None, 10.0),
    "ogtt_2h": ("GTT 2-soat", "mmol/L", 1, 40, None, 8.5),
    "alt": ("ALT", "U/L", 1, 3000, None, 35),
    "ast": ("AST", "U/L", 1, 3000, None, 35),
    "bilirubin_total": ("Umumiy bilirubin", "µmol/L", 1, 500, None, 21),
    "creatinine": ("Kreatinin", "µmol/L", 10, 1500, None, 90),
    "urea": ("Mochevina", "mmol/L", 0.5, 60, 2.5, 7.0),
    "total_protein": ("Umumiy oqsil", "g/L", 20, 120, 60, 80),
    "fibrinogen": ("Fibrinogen", "g/L", 0.5, 12, 2, 6),
    "ferritin": ("Ferritin", "ng/mL", 1, 3000, 30, None),
    "tsh": ("TTG (qalqonsimon bez)", "mIU/L", 0.01, 100, 0.1, 3.0),
    "urine_protein": ("Siydikda oqsil", "g/L", 0, 20, None, 0.3),
    "urine_leukocytes": ("Siydikda leykotsit", "ko'rish maydonida", 0, 200, None, 5),
}

DIAGNOSES = {
    "O10": "Avvaldan mavjud gipertenziya", "O13": "Homiladorlik gipertenziyasi", "O14": "Preeklampsiya",
    "O15": "Eklampsiya", "O24.0": "Avvaldan mavjud qandli diabet", "O24.4": "Homiladorlik qandli diabeti",
    "O99.0": "Homiladorlik anemiyasi", "O23": "Siydik-tanosil yo'llari infeksiyasi", "O20": "Erta muddatda qon ketish",
    "O21": "Og'ir qusish (giperemezis)", "O44": "Platsenta oldinda kelishi", "O45": "Platsenta ko'chishi",
    "O36.5": "Homila o'sishining sekinlashuvi", "O40": "Ko'p suvlilik", "O41.0": "Kam suvlilik", "O30": "Ko'p homilalik",
    "O60.0": "Muddatidan oldin tug'ruq xavfi", "O32": "Homilaning noto'g'ri joylashuvi", "O34.2": "Kesar kesishdan keyingi chandiq",
    "O99.2": "Endokrin kasallik (qalqonsimon bez)", "O99.4": "Yurak-qon tomir kasalligi", "O98": "Yuqumli kasallik",
    "O99.3": "Ruhiy holat buzilishi",
}

COMPLAINTS = {
    "nausea": "Ko'ngil aynishi, qusish", "abdominal_pain": "Qorin og'rig'i", "headache": "Bosh og'rig'i",
    "dizziness": "Bosh aylanishi", "swelling": "Shish (oyoq, yuz, qo'l)", "weakness": "Holsizlik",
    "breathless": "Hansirash", "urinary_pain": "Siydik chiqarishda og'riq", "discharge": "G'ayritabiiy ajralma",
    "back_pain": "Bel og'rig'i", "insomnia": "Uyqusizlik", "no_appetite": "Ishtaha yo'qligi", "itching": "Teri qichishi",
    "fever": "Isitma", "contractions": "Qorin qotishi", "palpitations": "Yurak tez urishi",
}

CAUSES = {
    "poor_diet": "Ovqatlanish yetishmasligi", "heavy_work": "Og'ir jismoniy mehnat", "family_stress": "Oilaviy stress",
    "violence_signs": "Zo'ravonlik belgilari", "short_interval": "Tug'ruqlar oralig'i 2 yildan kam",
    "chronic_disease": "Surunkali kasallik", "prior_complication": "Oldingi homiladorlikda asorat",
    "age_risk": "Yosh: 18 dan kichik yoki 35 dan katta", "smoking": "Chekish yoki passiv chekish",
    "late_registration": "Kech ro'yxatga olingan", "infection": "Infeksiya", "hereditary": "Irsiy moyillik",
    "low_income": "Moddiy qiyinchilik", "far_facility": "Shifoxona uzoqda",
}

EPDS = [
    "Kula olish va narsalarning quvnoq tomonini ko'ra olish qiyinlashdimi?",
    "Kelajakka quvonch bilan qaray olmayapsizmi?",
    "Ishlar yomon ketganda o'zingizni keraksiz ayblaysizmi?",
    "Sababsiz xavotir yoki tashvish bo'ladimi?",
    "Sababsiz qo'rquv yoki vahima bo'ladimi?",
    "Ishlar boshingizdan oshib ketgandek tuyuladimi?",
    "Ko'nglingiz cho'kkani uchun uxlay olmaysizmi?",
    "O'zingizni qayg'uli yoki baxtsiz his qilasizmi?",
    "Ko'nglingiz cho'kkani uchun yig'laysizmi?",
    "O'zingizga zarar yetkazish haqida fikr kelganmi?",
]
EPDS_OPTIONS = ["Yo'q", "Kamdan-kam", "Ba'zan", "Ko'pincha"]
STRESS = ["Yaxshi", "Biroz tashvishli", "Stressda", "Juda og'ir"]
MOOD_TEXT = {"ok": "Kayfiyat me'yorda", "watch": "Kayfiyatni kuzatish kerak",
             "urgent": "Depressiya xavfi: bugun shifokor yoki psixologga yo'naltiring"}


def lab_flag(code: str, value: float) -> str | None:
    _, _, _, _, lo, hi = LABS[code]
    if lo is not None and value < lo:
        return "low"
    if hi is not None and value > hi:
        return "high"
    return None


def mood_of(answers: list[int] | None, stress: int | None) -> tuple[int | None, str | None]:
    if answers is None:
        if stress is None:
            return None, None
        return None, "urgent" if stress >= 3 else "watch" if stress >= 2 else "ok"
    score = sum(answers)
    flag = "urgent" if answers[9] >= 1 or score >= 13 else "watch" if score >= 10 or (stress or 0) >= 2 else "ok"
    return score, flag


class LabIn(BaseModel):
    model_config = {"extra": "forbid"}
    code: str
    value: float

    @field_validator("code")
    @classmethod
    def _known(cls, v):
        if v not in LABS:
            raise ValueError("unknown lab code")
        return v


def _codes(allowed: dict):
    def check(v: list[str]) -> list[str]:
        bad = [c for c in v if c not in allowed]
        if bad:
            raise ValueError("unknown code")
        return list(dict.fromkeys(v))
    return check


class DetailsIn(BaseModel):
    model_config = {"extra": "forbid"}
    pregnancy_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    gestational_week: float | None = Field(None, ge=2, le=44)
    labs: list[LabIn] = Field(default_factory=list, max_length=len(LABS))
    diagnoses: list[str] = Field(default_factory=list, max_length=12)
    epds: list[int] | None = Field(None, min_length=10, max_length=10)
    stress_level: int | None = Field(None, ge=0, le=3)
    complaints: list[str] = Field(default_factory=list, max_length=len(COMPLAINTS))
    complaint_note: str | None = Field(None, max_length=600)
    causes: list[str] = Field(default_factory=list, max_length=len(CAUSES))
    cause_note: str | None = Field(None, max_length=600)

    @field_validator("diagnoses")
    @classmethod
    def _dx(cls, v):
        return _codes(DIAGNOSES)(v)

    @field_validator("complaints")
    @classmethod
    def _cmp(cls, v):
        return _codes(COMPLAINTS)(v)

    @field_validator("causes")
    @classmethod
    def _cau(cls, v):
        return _codes(CAUSES)(v)

    @field_validator("epds")
    @classmethod
    def _epds(cls, v):
        if v is not None and any(x not in (0, 1, 2, 3) for x in v):
            raise ValueError("epds answers must be 0..3")
        return v


@router.get("/catalog")
def catalog(user: User = Depends(live.live_user)):
    return {"labs": [{"code": c, "label": l, "unit": u, "min": mn, "max": mx, "normal_low": lo, "normal_high": hi}
                     for c, (l, u, mn, mx, lo, hi) in LABS.items()],
            "diagnoses": [{"code": c, "label": l} for c, l in DIAGNOSES.items()],
            "complaints": [{"code": c, "label": l} for c, l in COMPLAINTS.items()],
            "causes": [{"code": c, "label": l} for c, l in CAUSES.items()],
            "epds": {"questions": EPDS, "options": EPDS_OPTIONS, "period": "Oxirgi 7 kun ichida"},
            "stress": STRESS}


@router.post("/details")
def save_details(body: DetailsIn, request: Request, user: User = Depends(live.live_user), rdb: Session = Depends(get_db)):
    live._require_persona(user, request, "nurse", "visit_detail.create", "pregnancy", body.pregnancy_id)
    live._limit(user)
    geo.load(rdb)
    target = rdb.get(Pregnancy, body.pregnancy_id)
    if target is None or target.status != "active":
        raise not_found("pregnancy")
    if not live._nurse_may_visit(rdb, user, target):
        live._deny(user, request, "visit_detail.create", "pregnancy", body.pregnancy_id, "not_assigned", what="pregnancy")
    for lab in body.labs:
        _, _, mn, mx, _, _ = LABS[lab.code]
        if not mn <= lab.value <= mx:
            raise AppError(422, "lab_out_of_range", f"{LABS[lab.code][0]}: qiymat {mn}–{mx} oralig'ida bo'lishi kerak")
    score, mood = mood_of(body.epds, body.stress_level)
    empty = not (body.labs or body.diagnoses or body.complaints or body.causes or body.complaint_note or body.cause_note
                 or mood or body.gestational_week)
    if empty:
        raise AppError(422, "empty_details", "Kamida bitta maʼlumot kiriting")
    rdb.rollback()
    with write_tx() as db:
        preg = db.get(Pregnancy, body.pregnancy_id)
        if preg is None or preg.status != "active":
            raise not_found("pregnancy")
        mother = db.get(Mother, preg.mother_id)
        now = utcnow()
        if body.gestational_week is not None:
            preg.lmp_date = now.date() - timedelta(days=round(body.gestational_week * 7))
            preg.edd = preg.lmp_date + timedelta(days=280)
        week = round((now.date() - preg.lmp_date).days / 7, 1) if preg.lmp_date else None
        for lab in body.labs:
            db.add(LabResult(pregnancy_id=preg.id, test_code=lab.code, value=float(lab.value), unit=LABS[lab.code][1],
                             collected_at=now, facility_id=preg.facility_id, created_by=user.id))
        d = VisitDetail(pregnancy_id=preg.id, mother_id=preg.mother_id, facility_id=preg.facility_id, recorded_at=now,
                        gestational_week=week, diagnoses=body.diagnoses, epds_answers=body.epds, epds_score=score,
                        mood_flag=mood, stress_level=body.stress_level, complaints=body.complaints,
                        complaint_note_enc=encrypt(body.complaint_note or None), causes=body.causes,
                        cause_note_enc=encrypt(body.cause_note or None), is_synthetic=preg.is_synthetic, created_by=user.id)
        db.add(d)
        db.flush()
        zone, alert = preg.current_risk_level, None
        if any(lab.code in ("hemoglobin", "glucose_fasting") for lab in body.labs) or body.gestational_week is not None:
            res = live_engine.assess_pregnancy(db, preg, mother, actor_id=user.id)
            zone = res.decision.zone
            if res.alert is not None and res.alert.status != "resolved":
                alert = {"id": res.alert.id, "severity": res.alert.severity, "new": res.new_alert}
        live.write_audit(db, actor=user, action="visit_detail.create", resource_type="pregnancy", resource_id=preg.id,
                         request_id=request.state.request_id,
                         detail={"labs": len(body.labs), "diagnoses": len(body.diagnoses), "mood": mood}, commit=False)
        flags = [{"code": l.code, "label": LABS[l.code][0], "value": l.value, "unit": LABS[l.code][1],
                  "flag": lab_flag(l.code, l.value)} for l in body.labs]
        return {"ok": True, "detail_id": d.id, "weeks": int(week) if week is not None else None, "zone": zone,
                "alert": alert, "labs": flags, "abnormal": [f for f in flags if f["flag"]],
                "mood": {"flag": mood, "score": score, "text": MOOD_TEXT.get(mood)} if mood else None}


def _history(db: Session, mother: Mother, current: Pregnancy | None) -> dict:
    """Read-only clinical history across all pregnancies of one woman."""
    pregs = sorted(mother.pregnancies, key=lambda p: p.created_at, reverse=True)
    preg_ids = [p.id for p in pregs]
    details = list(db.execute(select(VisitDetail).where(VisitDetail.mother_id == mother.id)
                              .order_by(VisitDetail.recorded_at.desc())).scalars())
    labs = list(db.execute(select(LabResult).where(LabResult.pregnancy_id.in_(preg_ids or ["-"]))
                           .order_by(LabResult.collected_at.desc())).scalars())
    obs = list(db.execute(select(MaternalObservation).where(MaternalObservation.pregnancy_id.in_(preg_ids or ["-"]))
                          .order_by(MaternalObservation.observed_at.desc()).limit(8)).scalars())
    seen_dx: dict[str, dict] = {}
    for d in details:
        for c in d.diagnoses or []:
            seen_dx.setdefault(c, {"code": c, "label": DIAGNOSES.get(c, c), "first_at": iso(d.recorded_at),
                                   "district_name": geo.district_of_facility(d.facility_id)["name"]})
            seen_dx[c]["first_at"] = iso(d.recorded_at)
    latest_lab: dict[str, dict] = {}
    for l in labs:
        if l.test_code in latest_lab:
            latest_lab[l.test_code]["series"].append({"at": iso(l.collected_at), "value": l.value})
            continue
        meta = LABS.get(l.test_code)
        latest_lab[l.test_code] = {"code": l.test_code, "label": meta[0] if meta else l.test_code, "unit": l.unit,
                                   "value": l.value, "at": iso(l.collected_at),
                                   "flag": lab_flag(l.test_code, l.value) if meta else None,
                                   "series": [{"at": iso(l.collected_at), "value": l.value}]}
    history = [trend.HISTORY_UZ.get(h, h) for h in db.execute(select(MaternalHistory.factor_code).where(
        MaternalHistory.mother_id == mother.id, MaternalHistory.present.is_(True))).scalars()]
    return {
        "pregnancies": [{"id": p.id, "status": p.status, "weeks": live_engine.weeks_of(p) if p.status == "active" else None,
                         "zone": p.current_risk_level, "edd": p.edd.isoformat() if p.edd else None,
                         "district_name": geo.district_of_facility(p.facility_id)["name"],
                         "current": bool(current and p.id == current.id)} for p in pregs],
        "diagnoses": list(seen_dx.values()),
        "labs": sorted(latest_lab.values(), key=lambda x: list(LABS).index(x["code"]) if x["code"] in LABS else 99),
        "mood": [{"at": iso(d.recorded_at), "flag": d.mood_flag, "score": d.epds_score, "text": MOOD_TEXT.get(d.mood_flag),
                  "stress": STRESS[d.stress_level] if d.stress_level is not None else None}
                 for d in details if d.mood_flag][:6],
        "complaints": [{"at": iso(d.recorded_at), "items": [COMPLAINTS.get(c, c) for c in d.complaints or []],
                        "note": decrypt(d.complaint_note_enc)} for d in details if d.complaints or d.complaint_note_enc][:6],
        "causes": [{"at": iso(d.recorded_at), "items": [CAUSES.get(c, c) for c in d.causes or []],
                    "note": decrypt(d.cause_note_enc)} for d in details if d.causes or d.cause_note_enc][:6],
        "visits": [{"at": iso(o.observed_at), "week": int(o.gestational_week) if o.gestational_week is not None else None,
                    "sbp": o.systolic_bp, "dbp": o.diastolic_bp, "hr": o.heart_rate, "weight_kg": o.weight_kg,
                    "district_name": geo.district_of_facility(
                        next((p.facility_id for p in pregs if p.id == o.pregnancy_id), mother.facility_id))["name"]}
                   for o in obs],
        "risk_history": history,
    }


@router.get("/patients/{pregnancy_id}")
def patient_record(pregnancy_id: str, request: Request, user: User = Depends(live.live_user), db: Session = Depends(get_db)):
    geo.load(db)
    live._deny_leader(user, request, "pregnancy", pregnancy_id[:36])
    preg = db.get(Pregnancy, pregnancy_id)
    if preg is None:
        raise not_found("patient")
    live._card_scope(db, user, request, "pregnancy", preg.id, preg.facility_id, preg.mother_id)
    out = _history(db, db.get(Mother, preg.mother_id), preg)
    live._audit(user, "live.record.read", "pregnancy", preg.id, request)
    return out


class LookupIn(BaseModel):
    model_config = {"extra": "forbid"}
    pinfl: str = Field(pattern=r"^\d{14}$")
    reason: Literal["emergency", "moved", "referral", "consultation"]


REASON_UZ = {"emergency": "Shoshilinch holat", "moved": "Ayol boshqa tumanga ko'chib kelgan",
             "referral": "Yo'llanma bilan kelgan", "consultation": "Maslahat (konsultatsiya)"}


@router.post("/lookup")
def lookup(body: LookupIn, request: Request, user: User = Depends(live.live_user), db: Session = Depends(get_db)):
    """Break-the-glass read of one woman's history by JSHSHIR. The caller sees it read-only; every lookup is audited."""
    persona = live.persona_of(user)
    if persona not in ("nurse", "specialist") or live.role_of(user) == "pending":
        live._deny(user, request, "live.pinfl_lookup", "mother", None, "role_not_allowed", status=403,
                   message="JSHSHIR bo'yicha qidirish faqat hamshira va shifokor uchun ochiq")
    live._limit(user, "lookup", 10)
    geo.load(db)
    m = db.execute(select(Mother).where(Mother.pinfl_hash == keyed_hash(body.pinfl))).scalar_one_or_none()
    if m is None:
        live._audit(user, "live.pinfl_lookup", "mother", None, request, {"reason": body.reason, "found": False})
        raise AppError(404, "not_found", "Bu JSHSHIR bo'yicha ro'yxatdagi ayol topilmadi")
    pregs = sorted(m.pregnancies, key=lambda p: p.created_at)
    current = next((p for p in reversed(pregs) if p.status == "active"), pregs[-1] if pregs else None)
    own_district = next((s.district_code for s in user.scopes if s.district_code), None)
    home = geo.district_of_facility(m.facility_id)
    cross = home["code"] != own_district
    live._audit(user, "live.pinfl_lookup", "mother", m.id, request,
                {"reason": body.reason, "found": True, "cross_district": cross})
    open_alerts = db.execute(select(Alert.severity).where(Alert.mother_id == m.id, Alert.status.in_(("open", "acknowledged")))).scalars().all()
    return {"patient": {"name": live._name(m), "age": live._age(m), "code": m.local_code,
                        "home_district": home["name"], "home_region": geo.regions.get(home["region_soato"], ""),
                        "zone": current.current_risk_level if current else "grey",
                        "weeks": live_engine.weeks_of(current) if current and current.status == "active" else None,
                        "open_alerts": len(open_alerts), "synthetic": m.is_synthetic},
            "cross_district": cross, "reason": REASON_UZ[body.reason],
            "notice": "Bu ko'rish qayd etildi: kim, qachon va nima sababdan ochgani audit jurnalida saqlanadi.",
            **_history(db, m, current)}

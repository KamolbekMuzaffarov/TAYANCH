from __future__ import annotations

import argparse
import json
import random
import secrets
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, insert, select  # noqa: E402

from app.core.audit import write_audit  # noqa: E402
from app.core.config import BACKEND_DIR, get_settings  # noqa: E402
from app.core.security import encrypt, hash_password, utcnow  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (Alert, AlertAction, AlertRecipient, CareAssignment, ClinicalRuleVersion, District, Facility,  # noqa: E402
                        LabResult, MaternalHistory, MaternalObservation, Mother, Newborn, NewbornObservation, Pregnancy,
                        RegionalIndicator, Region, RiskAssessment, Role, ScopeType, User, UserScope)
from app.services import clinical_rules, data_import, live_engine, regional, trend  # noqa: E402
from app.services.uz_names import (DOCTOR_FIRST, FIRST_NAMES, HISTORY_CODES, LAST_NAMES, MAHALLAS,  # noqa: E402
                                   district_uz)
from scripts.seed import create_facilities, register_model  # noqa: E402

LIVE_DB = BACKEND_DIR / "perinatal_live.db"
DEMO_DISTRICT = "1726-GB004"  # Yunusobod, Toshkent shahri
NOW = utcnow()
SLA_RED, SLA_YELLOW = get_settings().ALERT_SLA_MINUTES_RED, get_settings().ALERT_SLA_MINUTES_YELLOW


def uid() -> str:
    return str(uuid.uuid4())


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def at_days_ago(rnd: random.Random, days: float) -> datetime:
    if days < 1:
        return NOW - timedelta(days=days)
    # whole calendar days back in Tashkent time (UTC+5), during working hours
    day = (NOW + timedelta(hours=5)).date() - timedelta(days=int(days))
    local = datetime(day.year, day.month, day.day, rnd.randint(8, 16), rnd.randint(0, 59), rnd.randint(0, 59), tzinfo=NOW.tzinfo)
    return local - timedelta(hours=5)


# ------------------------------------------------------------------ master data additions
def rename_districts(db) -> int:
    n = 0
    for d in db.execute(select(District)).scalars():
        new = district_uz(d.name)
        if new != d.name:
            d.name, n = new, n + 1
    db.commit()
    return n


def seed_live_rules(db) -> int:
    n = 0
    for r in trend.LIVE_RULES:
        if db.execute(select(ClinicalRuleVersion).where(ClinicalRuleVersion.rule_id == r["rule_id"])).first():
            continue
        db.add(ClinicalRuleVersion(rule_id=r["rule_id"], version=1, subject_type=r["subject_type"], severity=r["severity"],
                                   definition=r["definition"], approval_status=clinical_rules.DEMO,
                                   clinical_reference=r["ref"], effective_from=date.today()))
        n += 1
    db.commit()
    return n


def district_facilities(db) -> dict[str, Facility]:
    out = {}
    for d in db.execute(select(District)).scalars():
        f = Facility(code=f"LIVE-{d.code}", name=f"{d.name} — oilaviy poliklinika (DEMO)", facility_type="polyclinic",
                     level="district", region_soato=d.region_soato, district_code=d.code, lat=d.centroid_lat,
                     lon=d.centroid_lon, is_synthetic=True, notes="Synthetic demo facility - not a real institution record")
        db.add(f)
        out[d.code] = f
    # facility-type variety for the "Muassasalar" list: one emergency station per region (no patients are bound to it)
    for r in db.execute(select(Region)).scalars():
        db.add(Facility(code=f"DEMO-{r.soato}-TTY", name=f"Tez tibbiy yordam stansiyasi — {r.name_uz} (DEMO)",
                        facility_type="emergency", level="regional", region_soato=r.soato, is_synthetic=True,
                        notes="Synthetic demo facility - not a real institution record"))
    db.commit()
    return out


def create_users(db, rnd: random.Random, regions: list[Region]) -> dict:
    pw = hash_password(secrets.token_urlsafe(24))  # nobody logs in with a password: demo personas use /live/login
    facs = {f.code: f for f in db.execute(select(Facility)).scalars()}
    doctors, ovas = {}, {}

    def add(username, name, role, scope: dict) -> User:
        u = User(username=username, full_name=name, password_hash=pw, role=role)
        db.add(u)
        db.flush()
        db.add(UserScope(user_id=u.id, **scope))
        return u

    names = rnd.sample([(f, l) for f in DOCTOR_FIRST for l in LAST_NAMES[:30]], 2 * len(regions))
    for i, r in enumerate(regions):
        f1, l1 = names[2 * i]
        f2, l2 = names[2 * i + 1]
        doctors[r.soato] = add(f"dr.{r.soato}", f"Dr. {f1} {l1}", Role.OBGYN,
                               dict(scope_type=ScopeType.FACILITY, facility_id=facs[f"DEMO-{r.soato}-MAT"].id))
        if r.soato == DEMO_DISTRICT[:4]:
            ovas[r.soato] = add("demo.specialist", "Dr. Nodira Yusupova", Role.OVABMU_SPECIALIST,
                                dict(scope_type=ScopeType.FACILITY, facility_id=facs[f"DEMO-{r.soato}-OVA"].id))
        else:
            ovas[r.soato] = add(f"ova.{r.soato}", f"Dr. {f2} {l2}", Role.OVABMU_SPECIALIST,
                                dict(scope_type=ScopeType.FACILITY, facility_id=facs[f"DEMO-{r.soato}-OVA"].id))
    nurse = add("demo.nurse", "Dilnoza Karimova", Role.PATRONAGE_NURSE,
                dict(scope_type=ScopeType.DISTRICT, district_code=DEMO_DISTRICT, region_soato=DEMO_DISTRICT[:4]))
    db.add(UserScope(user_id=nurse.id, scope_type=ScopeType.FACILITY, facility_id=facs[f"LIVE-{DEMO_DISTRICT}"].id))
    leader = add("demo.leader", "Sogʻliqni saqlash vazirligi — monitoring markazi", Role.NATIONAL_ADMIN,
                 dict(scope_type=ScopeType.NATIONAL))
    db.commit()
    from app.services.schema import ensure_demo_admin, ensure_demo_facility_admin, ensure_schema
    ensure_schema(engine)  # no-op on a fresh file: create_all() already made the account tables and columns
    ensure_demo_admin(db)  # demo persona "admin": Admin — tizim boshqaruvchisi
    ensure_demo_facility_admin(db)  # demo persona "facility_admin": administrator of the demo nurse's polyclinic
    return {"doctors": doctors, "ovas": ovas, "nurse": nurse, "leader": leader}


# ------------------------------------------------------------------ cohort generation
PROFILES = [("normal", 73.0), ("bp_trend", 4.0), ("ght", 5.0), ("pe", 1.6), ("anaemia", 7.5), ("severe_anaemia", 0.25),
            ("hyper", 3.2), ("stale", 4.2), ("danger", 0.75), ("hb_trend", 0.5)]
ALERTING = {"ght", "pe", "anaemia", "severe_anaemia", "hyper", "danger"}


def gen_case(rnd: random.Random, profile: str) -> dict:
    """One synthetic pregnancy: demographics + visit list (oldest -> newest) + labs."""
    late = profile in ("bp_trend", "ght", "pe", "danger")
    weeks_now = rnd.uniform(24, 39.5) if late else rnd.uniform(8, 40)
    if profile in ("anaemia", "severe_anaemia", "hb_trend"):
        weeks_now = rnd.uniform(18, 39.5)
    if profile == "stale":
        last_ago = rnd.uniform(35, 75)
        weeks_now = max(weeks_now, 14)
    elif profile in ALERTING:
        last_ago = rnd.uniform(0.035, 7.0)
    else:
        last_ago = rnd.uniform(0.05, 16.5)
    n = rnd.randint(2, 6)
    if profile in ("bp_trend", "anaemia", "severe_anaemia", "hb_trend", "pe"):
        n = max(n, 3)
    ago = [last_ago]
    while len(ago) < n:
        nxt = ago[-1] + rnd.uniform(14, 28)
        if weeks_now - nxt / 7 < 6:
            break
        ago.append(nxt)
    ago.reverse()  # oldest first
    base_s, base_d = clamp(rnd.gauss(110, 7), 98, 123), clamp(rnd.gauss(70, 5), 60, 80)
    base_hr = rnd.uniform(68, 86)
    w0, wk_gain = clamp(rnd.gauss(60, 8), 45, 92), rnd.uniform(0.30, 0.45)
    last_s = last_d = None
    if profile == "bp_trend":
        last_s, last_d, slope_s, slope_d = rnd.uniform(128, 137), rnd.uniform(80, 87), rnd.uniform(2.6, 5.0), rnd.uniform(1.3, 2.4)
    elif profile == "ght":
        last_s, last_d, slope_s, slope_d = rnd.uniform(141, 156), rnd.uniform(90, 102), rnd.uniform(3, 6), rnd.uniform(1.5, 3)
    elif profile == "pe":
        last_s, last_d, slope_s, slope_d = rnd.uniform(146, 172), rnd.uniform(94, 112), rnd.uniform(4, 8), rnd.uniform(2, 4)
    danger = rnd.choices(["bleeding", "severe_htn", "movements", "convulsions", "fever"], weights=[35, 25, 25, 5, 10])[0] \
        if profile == "danger" else None
    visits = []
    for i, a in enumerate(ago):
        is_last = i == len(ago) - 1
        wk = round(weeks_now - a / 7, 1)
        back = (a - last_ago) / 7  # weeks before the last visit
        if last_s is not None:
            s = last_s - slope_s * back + (0 if is_last else rnd.gauss(0, 1.3))
            d = last_d - slope_d * back + (0 if is_last else rnd.gauss(0, 1.0))
            if not is_last:
                s, d = clamp(s, base_s - 4, 138), clamp(d, base_d - 3, 88)
        else:
            s, d = clamp(base_s + rnd.gauss(0, 3.5), 96, 129), clamp(base_d + rnd.gauss(0, 3), 58, 84)
        s, d = int(round(s)), int(round(d))
        hr = int(clamp(base_hr + rnd.gauss(0, 4), 60, min(96, 0.87 * s)))
        v = {"days_ago": a, "gestational_week": wk, "systolic_bp": s, "diastolic_bp": d, "heart_rate": hr,
             "temperature_c": round(rnd.uniform(36.3, 37.0), 1), "spo2": rnd.randint(96, 99),
             "weight_kg": round(w0 + max(0.0, wk - 10) * wk_gain + rnd.gauss(0, 0.25), 1),
             "fetal_heart_rate": rnd.randint(125, 155) if wk >= 20 else None, "proteinuria": "neg",
             "severe_headache": False, "visual_disturbance": False, "vaginal_bleeding": False,
             "reduced_fetal_movements": False, "convulsions": False}
        if is_last:
            if profile == "ght":
                v["proteinuria"] = rnd.choice(["neg", "neg", "trace", "1+"])
            elif profile == "pe":
                v["proteinuria"] = rnd.choice(["2+", "2+", "3+"])
                v["severe_headache"] = rnd.random() < 0.6
                v["visual_disturbance"] = rnd.random() < 0.25
                if len(visits) >= 1:  # rapid weight gain (oedema)
                    gap_w = (visits[-1]["days_ago"] - a) / 7
                    v["weight_kg"] = round(visits[-1]["weight_kg"] + rnd.uniform(1.15, 1.8) * gap_w, 1)
            elif danger == "bleeding":
                v["vaginal_bleeding"] = True
                if rnd.random() < 0.5:
                    v["systolic_bp"], v["diastolic_bp"], v["heart_rate"] = rnd.randint(92, 104), rnd.randint(56, 66), rnd.randint(102, 118)
            elif danger == "severe_htn":
                v["systolic_bp"], v["diastolic_bp"] = rnd.randint(162, 178), rnd.randint(106, 116)
                v["severe_headache"] = rnd.random() < 0.5
            elif danger == "movements":
                v["reduced_fetal_movements"] = True
            elif danger == "convulsions":
                v["convulsions"], v["systolic_bp"], v["diastolic_bp"] = True, rnd.randint(158, 176), rnd.randint(104, 114)
            elif danger == "fever":
                v["temperature_c"], v["heart_rate"] = round(rnd.uniform(38.1, 39.2), 1), rnd.randint(88, 98)
        visits.append(v)
    # labs: (visit index, code, value)
    labs = []
    k = len(visits)
    if profile in ("anaemia", "severe_anaemia", "hb_trend"):
        start = rnd.uniform(104, 114)
        end = rnd.uniform(78, 98) if profile == "anaemia" else rnd.uniform(60, 69) if profile == "severe_anaemia" else rnd.uniform(100, 104)
        if profile == "hb_trend":
            start = end + rnd.uniform(11, 16)
        idx = list(range(max(0, k - 3), k))
        for j, vi in enumerate(idx):
            frac = j / max(1, len(idx) - 1)
            labs.append((vi, "hemoglobin", round(start + (end - start) * frac)))
    else:
        hb_base = rnd.uniform(101, 109) if rnd.random() < 0.30 else clamp(rnd.gauss(119, 6), 110, 136)
        for vi in sorted({0, k - 1} | set(range(0, k, 2))):
            labs.append((vi, "hemoglobin", round(clamp(hb_base + rnd.gauss(0, 1.8), 100, 140))))
    labs.append((0, "glucose_fasting", round(rnd.uniform(3.9, 4.9), 1)))
    if profile == "hyper":
        labs.append((k - 1, "glucose_fasting", round(rnd.uniform(5.2, 6.9), 1)))
    elif visits[-1]["gestational_week"] >= 24 and k > 1:
        labs.append((k - 1, "glucose_fasting", round(rnd.uniform(3.9, 5.0), 1)))
    gravida = rnd.choices([1, 2, 3, 4, 5, 6], weights=[30, 30, 22, 11, 5, 2])[0]
    age = int(clamp(rnd.gauss(27, 5.2), 18, 44))
    hist = []
    if gravida > 1 and rnd.random() < 0.16:
        hist.append(rnd.choice(HISTORY_CODES))
    if profile in ("pe", "ght", "bp_trend") and gravida > 1 and rnd.random() < 0.35:
        hist.append(rnd.choice(["previous_preeclampsia", "previous_gestational_hypertension", "chronic_hypertension"]))
    if profile in ("anaemia", "severe_anaemia") and rnd.random() < 0.4:
        hist.append("anaemia_history")
    if profile == "hyper" and rnd.random() < 0.3:
        hist.append("diabetes")
    return {"profile": profile, "name": f"{rnd.choice(FIRST_NAMES)} {rnd.choice(LAST_NAMES)}", "age": age,
            "weeks_now": weeks_now, "gravida": gravida, "parity": rnd.randint(0, gravida - 1) if gravida > 1 else 0,
            "mahalla": rnd.choice(MAHALLAS), "visits": visits, "labs": labs, "history": sorted(set(hist))}


def _v(days_ago, week, s, d, hr, wt, **kw) -> dict:
    v = {"days_ago": days_ago, "gestational_week": week, "systolic_bp": s, "diastolic_bp": d, "heart_rate": hr,
         "temperature_c": 36.6, "spo2": 98, "weight_kg": wt, "fetal_heart_rate": 140 if week >= 20 else None,
         "proteinuria": "neg", "severe_headache": False, "visual_disturbance": False, "vaginal_bleeding": False,
         "reduced_fetal_movements": False, "convulsions": False}
    v.update(kw)
    return v


def crafted_cases() -> list[dict]:
    """The nurse persona's women (hand-crafted demo cases)."""
    def case(name, age, weeks, g, p, mahalla, visits, labs, history=(), profile="crafted", alert_mode=None):
        return {"profile": profile, "name": name, "age": age, "weeks_now": weeks + 0.3, "gravida": g, "parity": p,
                "mahalla": mahalla, "visits": visits, "labs": labs, "history": list(history), "crafted": True,
                "alert_mode": alert_mode}
    return [
        # (a) stage scenario: BP 118/76 -> 126/80 -> 134/84, still below every threshold -> early warning only
        case("Madina Tursunova", 27, 32, 2, 1, "Bogʻishamol",
             [_v(35, 27.3, 118, 76, 78, 66.0), _v(21, 29.3, 126, 80, 80, 66.9), _v(7, 31.3, 134, 84, 82, 67.8)],
             [(0, "hemoglobin", 116), (2, "hemoglobin", 114), (0, "glucose_fasting", 4.5)],
             history=["previous_gestational_hypertension"]),
        # (b) haemoglobin falling 112 -> 101 -> 94
        case("Zilola Rahimova", 31, 28, 3, 2, "Navroʻz",
             [_v(41, 22.4, 108, 68, 76, 61.2), _v(23, 25.0, 110, 70, 78, 62.3), _v(5, 27.6, 106, 66, 80, 63.1)],
             [(0, "hemoglobin", 112), (1, "hemoglobin", 101), (2, "hemoglobin", 94), (0, "glucose_fasting", 4.3)],
             history=["anaemia_history", "short_birth_interval"], alert_mode="acked"),
        # (c) overdue visit
        case("Shahnoza Abdullayeva", 24, 22, 1, 0, "Istiqlol",
             [_v(47, 15.6, 104, 64, 74, 55.8), _v(19, 19.6, 106, 66, 76, 57.0)],
             [(0, "hemoglobin", 121), (0, "glucose_fasting", 4.4)]),
        case("Barno Xolmatova", 36, 26, 4, 3, "Guliston",
             [_v(68, 16.6, 116, 74, 78, 70.4), _v(40, 20.6, 118, 76, 80, 71.9)],
             [(0, "hemoglobin", 113), (0, "glucose_fasting", 4.8)], history=["previous_cesarean"]),
        # raised fasting glucose, already seen by the specialist
        case("Gulnora Saidova", 34, 30, 3, 2, "Tinchlik",
             [_v(36, 25.1, 112, 72, 76, 72.5), _v(20, 27.4, 114, 72, 78, 73.4), _v(4, 29.7, 116, 74, 78, 74.2)],
             [(0, "hemoglobin", 118), (0, "glucose_fasting", 4.9), (2, "glucose_fasting", 5.6)],
             history=["diabetes"], alert_mode="resolved"),
        # due today / healthy
        case("Nigora Ismoilova", 29, 36, 2, 1, "Bunyodkor",
             [_v(42, 30.3, 110, 70, 76, 68.0), _v(28, 32.3, 112, 70, 78, 68.9), _v(14, 34.3, 110, 72, 78, 69.7)],
             [(0, "hemoglobin", 117), (2, "hemoglobin", 115), (0, "glucose_fasting", 4.4)]),
        case("Feruza Yoʻldosheva", 22, 16, 1, 0, "Bogʻishamol",
             [_v(24, 12.9, 102, 62, 72, 53.1), _v(3, 15.9, 104, 64, 74, 53.9)],
             [(0, "hemoglobin", 124), (0, "glucose_fasting", 4.2)]),
        case("Dildora Nazarova", 26, 25, 2, 1, "Mustaqillik",
             [_v(38, 19.9, 108, 68, 74, 59.4), _v(24, 21.9, 110, 70, 76, 60.2), _v(10, 23.9, 108, 68, 76, 61.0)],
             [(0, "hemoglobin", 119), (2, "hemoglobin", 117), (0, "glucose_fasting", 4.6)]),
        case("Mohira Qodirova", 30, 12, 2, 1, "Navroʻz",
             [_v(5, 11.6, 106, 66, 74, 62.4)], [(0, "hemoglobin", 122), (0, "glucose_fasting", 4.5)]),
        case("Sevara Aliyeva", 28, 38, 2, 1, "Yoshlik",
             [_v(34, 33.4, 114, 72, 78, 71.0), _v(20, 35.4, 116, 74, 80, 71.8), _v(6, 37.4, 114, 74, 80, 72.5)],
             [(0, "hemoglobin", 116), (2, "hemoglobin", 114), (0, "glucose_fasting", 4.7)], history=["previous_cesarean"]),
        case("Kamola Ergasheva", 33, 20, 3, 2, "Istiqlol",
             [_v(40, 14.6, 110, 70, 76, 64.2), _v(12, 18.6, 112, 72, 76, 65.3)],
             [(0, "hemoglobin", 120), (0, "glucose_fasting", 4.6)]),
    ]


def evaluate_case(rules, c: dict) -> live_engine.Decision:
    """Same decision function as the live code path, on the in-memory history."""
    visits = c["visits"]
    last = visits[-1]
    facts: dict = {}
    if NOW - last["at"] <= timedelta(days=get_settings().OBSERVATION_FRESHNESS_DAYS):
        facts = {k: last.get(k) for k in live_engine.VITALS}
        facts["gestational_week"] = last["gestational_week"]
        facts["_observation_id"] = last["id"]
    else:
        facts["gestational_week"] = round(c["weeks_now"], 1)
    hb = []
    for vi, code, val in c["labs"]:
        t = visits[vi]["at"] - timedelta(minutes=50)
        fresh = NOW - t <= timedelta(days=get_settings().OBSERVATION_FRESHNESS_DAYS)
        if code == "hemoglobin":
            hb.append((t, float(val)))
            if fresh:
                facts["lab_hemoglobin_g_l"] = float(val)  # labs are ordered oldest -> newest per code
        elif fresh:
            facts["lab_glucose_fasting_mmol_l"] = float(val)
    pts = [trend.Point(at=v["at"], week=v["gestational_week"], sbp=v["systolic_bp"], dbp=v["diastolic_bp"],
                       hr=v["heart_rate"], weight=v["weight_kg"]) for v in visits]
    return live_engine.decide(rules, facts, pts, hb)


class Rows:
    """Row buffers for bulk inserts."""
    def __init__(self):
        self.t: dict = {m: [] for m in (Mother, Pregnancy, MaternalObservation, LabResult, MaternalHistory, Newborn,
                                        NewbornObservation, CareAssignment, RiskAssessment, Alert, AlertAction,
                                        AlertRecipient)}

    def add(self, model, **row):
        self.t[model].append(row)

    def flush(self, db):
        for model, rows in self.t.items():
            for i in range(0, len(rows), 2000):
                db.execute(insert(model), rows[i:i + 2000])
        db.commit()


def response_minutes(rnd: random.Random) -> float:
    return rnd.triangular(3, 25, 9)


def add_alert(rows: Rows, rnd: random.Random, *, subject_type, subject_id, mother_id, newborn_id, facility_id, region,
              severity, rule_ids, ra_id, created, mode, ova_id, doctor_id) -> dict:
    """mode: resolved | acked | open | overdue"""
    aid = uid()
    sla = SLA_RED if severity == "red" else SLA_YELLOW
    row = dict(id=aid, subject_type=subject_type, subject_id=subject_id, mother_id=mother_id, newborn_id=newborn_id,
               facility_id=facility_id, region_soato=region, severity=severity, rule_ids=sorted(rule_ids),
               reason_codes=sorted(rule_ids), risk_assessment_id=ra_id, dedup_key=f"{subject_type}:{subject_id}:{severity}",
               status="open", assigned_to=ova_id, sla_due_at=created + timedelta(minutes=sla), escalation_level=0,
               occurrences=1, acknowledged_at=None, acknowledged_by=None, resolved_at=None, resolved_by=None,
               resolution_code=None, is_synthetic=True, created_at=created, updated_at=created)
    rows.add(AlertAction, id=uid(), alert_id=aid, action="created", actor_id=None, at=created, detail={"rule_ids": sorted(rule_ids)})
    for u in {ova_id, doctor_id} - {None}:
        rows.add(AlertRecipient, id=uid(), alert_id=aid, user_id=u, channel="in_app", delivered_at=created, delivery_status="delivered")
    if mode == "overdue":
        esc = created + timedelta(minutes=sla)
        row.update(escalation_level=1, sla_due_at=esc + timedelta(minutes=sla), updated_at=esc)
        rows.add(AlertAction, id=uid(), alert_id=aid, action="escalated", actor_id=None, at=esc, detail={"level": 1, "recipients": 1})
    elif mode in ("acked", "resolved"):
        ack = created + timedelta(minutes=response_minutes(rnd))
        ack = min(ack, NOW - timedelta(seconds=30))
        row.update(status="acknowledged", acknowledged_at=ack, acknowledged_by=ova_id, updated_at=ack)
        rows.add(AlertAction, id=uid(), alert_id=aid, action="acknowledged", actor_id=ova_id, at=ack, detail=None)
        avail = (NOW - ack).total_seconds() / 60 - 2
        if mode == "resolved" and avail > 12:
            res = ack + timedelta(minutes=rnd.uniform(10, min(420, avail)))
            code = rnd.choices(["clinically_managed", "referred", "false_positive", "data_entry_error"], weights=[60, 30, 6, 4])[0]
            row.update(status="resolved", resolved_at=res, resolved_by=ova_id, resolution_code=code, updated_at=res)
            rows.add(AlertAction, id=uid(), alert_id=aid, action="resolved", actor_id=ova_id, at=res, detail={"resolution_code": code})
    rows.add(Alert, **row)
    return row


def aged_mode(rnd: random.Random, created: datetime) -> str:
    age_h = (NOW - created).total_seconds() / 3600
    if age_h < 3:
        return "acked"
    return "resolved" if rnd.random() < 0.93 else "acked"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pregnancies", type=int, default=4000)
    ap.add_argument("--newborns", type=int, default=300)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--force", action="store_true", help="allow a DATABASE_URL other than perinatal_live.db")
    args = ap.parse_args()
    s = get_settings()
    if s.is_production:
        sys.exit("refusing to seed synthetic data in production/staging")
    if not s.DATABASE_URL.endswith("perinatal_live.db") and not args.force:
        sys.exit(f"DATABASE_URL is {s.DATABASE_URL!r}; expected .../perinatal_live.db (use --force to override)")
    t_start = time.time()
    if s.DATABASE_URL.endswith("perinatal_live.db"):
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(LIVE_DB) + suffix)
            if p.exists():
                p.unlink()
    rnd = random.Random(args.seed)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    summary = data_import.load_all(db)
    summary["districts_renamed"] = rename_districts(db)
    create_facilities(db)
    summary["rules_added"] = clinical_rules.seed_rules(db) + seed_live_rules(db)
    summary["risk_scores"] = regional.recompute_all(db)
    register_model(db)
    regions = list(db.execute(select(Region).order_by(Region.soato)).scalars())
    dfac = district_facilities(db)
    users = create_users(db, rnd, regions)
    nurse = users["nurse"]
    rules = clinical_rules.active_rules(db, "pregnancy", True)
    nb_rules = clinical_rules.active_rules(db, "newborn", True)

    # ---- how many pregnancies per district: SIAT births (latest year) by region, mild random weights inside
    year = regional.latest_siat_year(db)
    births = {r.region_soato: r.rate for r in db.execute(select(RegionalIndicator).where(
        RegionalIndicator.indicator_code == "births_total", RegionalIndicator.source_key == "siat",
        RegionalIndicator.period_start == date(year, 1, 1), RegionalIndicator.region_soato != "1700")).scalars()}
    total_births = sum(births.values())
    districts = list(db.execute(select(District).order_by(District.code)).scalars())
    per_district: dict[str, int] = {}
    for r in regions:
        ds = [d for d in districts if d.region_soato == r.soato]
        target = round(args.pregnancies * births.get(r.soato, total_births / len(regions)) / total_births)
        w = {d.code: rnd.uniform(0.65, 1.45) * (1.5 if "shahri" in d.name else 1.0) for d in ds}
        sw = sum(w.values())
        for d in ds:
            per_district[d.code] = max(6, round(target * w[d.code] / sw))
    region_of = {d.code: d.region_soato for d in districts}

    # ---- generate cases
    cases: list[dict] = []
    names, weights = [p for p, _ in PROFILES], [w for _, w in PROFILES]
    crafted = crafted_cases()
    for code, n in per_district.items():
        own = crafted if code == DEMO_DISTRICT else []
        for c in own:
            c["district"] = code
            cases.append(c)
        for _ in range(max(0, n - len(own))):
            c = gen_case(rnd, rnd.choices(names, weights=weights)[0])
            c["district"] = code
            cases.append(c)
    # ---- choose the alerts that stay open (never in the nurse persona's district)
    pool_red = [c for c in cases if c["profile"] in ("pe", "danger") and c["district"] != DEMO_DISTRICT]
    pool_yel = [c for c in cases if c["profile"] in ("ght", "anaemia", "hyper") and c["district"] != DEMO_DISTRICT]
    rnd.shuffle(pool_red)
    rnd.shuffle(pool_yel)
    used_d: set[str] = set()

    def take(pool, k, mode, lo_min, hi_min):
        got = 0
        for c in pool:
            if got >= k:
                break
            if c["district"] in used_d or "alert_mode" in c:
                continue
            used_d.add(c["district"])
            c["alert_mode"] = mode
            c["visits"][-1]["days_ago"] = rnd.uniform(lo_min, hi_min) / 1440
            got += 1
    take(pool_red, 4, "overdue", SLA_RED + 8, SLA_RED + 26)   # escalated once, second deadline still running
    take(pool_red, 3, "open", 3, min(22, SLA_RED - 6))
    take(pool_yel, 6, "open", 10, min(200, SLA_YELLOW - 30))

    # ---- materialise rows
    rows = Rows()
    zone_count = {"red": 0, "yellow": 0, "green": 0, "grey": 0}
    early = 0
    open_alerts = overdue_alerts = 0
    madina_id = None
    for i, c in enumerate(cases, start=1):
        reg = region_of[c["district"]]
        fac = dfac[c["district"]]
        doctor, ova = users["doctors"][reg], users["ovas"][reg]
        for v in c["visits"]:
            v["at"] = at_days_ago(rnd, v["days_ago"])
            v["id"] = uid()
        c["visits"].sort(key=lambda v: v["at"])
        d = evaluate_case(rules, c)
        if c["profile"] == "severe_anaemia" and d.rule_level != "red":
            raise RuntimeError("rule engine disagrees with the generator (severe anaemia)")
        zone_count[d.zone] += 1
        early += 1 if d.rule_level == "green" and d.zone == "yellow" else 0
        mid, pid = uid(), uid()
        lmp = (NOW - timedelta(days=round(c["weeks_now"] * 7))).date()
        by = nurse.id if c.get("crafted") else None
        rows.add(Mother, id=mid, local_code=f"M-{i:04d}", full_name_enc=encrypt(c["name"]),
                 address_enc=encrypt(f"{c['mahalla']} mahallasi"), birth_year=NOW.year - c["age"],
                 birth_date_enc=None, phone_enc=None, pinfl_enc=None, pinfl_hash=None,
                 region_soato=reg, district_code=c["district"], facility_id=fac.id, legal_basis="healthcare_provision",
                 is_synthetic=True, created_by=None)
        rows.add(Pregnancy, id=pid, mother_id=mid, lmp_date=lmp, edd=lmp + timedelta(days=280), gravida=c["gravida"],
                 parity=c["parity"], plurality=1, status="active", facility_id=fac.id, assigned_clinician_id=doctor.id,
                 current_risk_level=d.zone, current_risk_at=c["visits"][-1]["at"], is_synthetic=True)
        if c.get("crafted"):
            rows.add(CareAssignment, id=uid(), user_id=nurse.id, mother_id=mid, newborn_id=None, active=True)
            if c["name"] == "Madina Tursunova":
                madina_id = pid
        for code_ in c["history"]:
            rows.add(MaternalHistory, id=uid(), mother_id=mid, factor_code=code_, present=True, recorded_by=None)
        for v in c["visits"]:
            rows.add(MaternalObservation, id=v["id"], pregnancy_id=pid, observed_at=v["at"], source="manual",
                     respiratory_rate=None, voice_draft_id=None, idempotency_key=None, created_by=by,
                     created_at=v["at"], updated_at=v["at"],
                     **{k: v.get(k) for k in live_engine.VITALS if k != "respiratory_rate"},
                     gestational_week=v["gestational_week"])
        for vi, code_, val in c["labs"]:
            t = c["visits"][vi]["at"] - timedelta(minutes=50)
            rows.add(LabResult, id=uid(), pregnancy_id=pid, test_code=code_, value=float(val),
                     unit="g/L" if code_ == "hemoglobin" else "mmol/L", collected_at=t, facility_id=fac.id, status="final",
                     idempotency_key=None, created_by=None, created_at=t, updated_at=t)
        ra_id = uid()
        last_at = c["visits"][-1]["at"]
        rows.add(RiskAssessment, id=ra_id, subject_type="pregnancy", subject_id=pid, level=d.zone, triggered_rules=d.triggered,
                 missing_inputs=d.missing, rule_set_version=d.rule_set_version, ml_shadow=None, model_version=None,
                 trigger_source_id=d.facts.get("_observation_id"), created_by=None, created_at=last_at, updated_at=last_at)
        if d.rule_level in ("red", "yellow"):
            sev = d.rule_level
            rule_ids = [t["rule_id"] for t in d.triggered if t["severity"] == sev] or [t["rule_id"] for t in d.triggered]
            created = last_at + timedelta(seconds=20)
            mode = c.get("alert_mode") or aged_mode(rnd, created)
            if mode == "overdue" and sev != "red":
                mode = "open"
            a = add_alert(rows, rnd, subject_type="pregnancy", subject_id=pid, mother_id=mid, newborn_id=None,
                          facility_id=fac.id, region=reg, severity=sev, rule_ids=rule_ids, ra_id=ra_id, created=created,
                          mode=mode, ova_id=ova.id, doctor_id=doctor.id)
            open_alerts += a["status"] == "open"
            overdue_alerts += a["status"] == "open" and a["escalation_level"] > 0

    # ---- newborns (last 28 days), spread over district facilities proportionally to the cohort
    codes = list(per_district)
    nb_open = {"red": 1, "yellow": 1}
    nb_zone = {"red": 0, "yellow": 0, "green": 0, "grey": 0}
    n_cases = len(cases)
    for j in range(1, args.newborns + 1):
        dcode = rnd.choices(codes, weights=[per_district[c] for c in codes])[0]
        reg, fac = region_of[dcode], dfac[dcode]
        doctor, ova = users["doctors"][reg], users["ovas"][reg]
        age_d = rnd.uniform(0.2, 28)
        born = NOW - timedelta(days=age_d)
        kind = rnd.choices(["term", "late_preterm", "very_preterm"], weights=[86, 11, 3])[0]
        if kind == "term":
            ga = round(rnd.uniform(37.3, 41.2), 1)
            bw = int(clamp(rnd.gauss(3350, 400), 2550, 4500)) if rnd.random() > 0.05 else rnd.randint(2100, 2490)
        elif kind == "late_preterm":
            ga, bw = round(rnd.uniform(32.2, 36.8), 1), rnd.randint(1700, 2700)
        else:
            ga, bw = round(rnd.uniform(28, 31.8), 1), rnd.randint(1000, 1490)
        r5 = rnd.random()
        ap5 = rnd.randint(8, 10) if r5 > 0.05 else rnd.randint(4, 6) if r5 > 0.01 else rnd.randint(2, 3)
        mid, pid, nid = uid(), uid(), uid()
        mname = f"{rnd.choice(FIRST_NAMES)} {rnd.choice(LAST_NAMES)}"
        lmp = (born - timedelta(days=round(ga * 7))).date()
        rows.add(Mother, id=mid, local_code=f"M-{n_cases + j:04d}", full_name_enc=encrypt(mname),
                 address_enc=encrypt(f"{rnd.choice(MAHALLAS)} mahallasi"), birth_year=NOW.year - int(clamp(rnd.gauss(27, 5), 18, 43)),
                 birth_date_enc=None, phone_enc=None, pinfl_enc=None, pinfl_hash=None, region_soato=reg, district_code=dcode,
                 facility_id=fac.id, legal_basis="healthcare_provision", is_synthetic=True, created_by=None)
        g = rnd.randint(1, 4)
        rows.add(Pregnancy, id=pid, mother_id=mid, lmp_date=lmp, edd=lmp + timedelta(days=280), gravida=g, parity=g,
                 plurality=1, status="delivered", facility_id=fac.id, assigned_clinician_id=doctor.id,
                 current_risk_level="grey", current_risk_at=None, is_synthetic=True)
        # home / ward checks
        n_obs = rnd.randint(1, 3)
        last_ago = min(age_d * 0.9, rnd.uniform(0.03, 6.5)) if rnd.random() > 0.08 or age_d < 8 else rnd.uniform(7.5, min(20, age_d))
        want_open = None
        if nb_open and dcode != DEMO_DISTRICT and dcode not in used_d and kind == "term" and ap5 >= 8 and bw >= 2500:
            want_open = "red" if nb_open.get("red") else "yellow"
            last_ago = rnd.uniform(4, 20) / 1440 if want_open == "red" else rnd.uniform(15, 150) / 1440
        obs_times = sorted({last_ago} | {min(age_d * 0.95, last_ago + rnd.uniform(1, 4) * k) for k in range(1, n_obs)}, reverse=True)
        last_obs = None
        for k, ago in enumerate(obs_times):
            is_last = k == len(obs_times) - 1
            t = NOW - timedelta(days=ago)
            o = {"temperature_c": round(rnd.uniform(36.5, 37.3), 1), "heart_rate": rnd.randint(115, 160),
                 "respiratory_rate": rnd.randint(35, 58), "spo2": rnd.randint(94, 99),
                 "weight_g": int(bw * (0.94 + 0.012 * max(0, age_d - ago))) if ago < age_d else bw,
                 "jaundice": False, "poor_feeding": False, "convulsions": False, "severe_chest_indrawing": False}
            if is_last:
                x = rnd.random()
                if want_open == "red":
                    o["temperature_c"] = round(rnd.uniform(35.3, 35.9), 1)
                elif want_open == "yellow":
                    o["jaundice"] = True
                elif x < 0.02:
                    o["temperature_c"] = round(rnd.uniform(35.2, 35.9), 1)
                elif x < 0.07:
                    o["temperature_c"] = round(rnd.uniform(36.0, 36.4), 1)
                elif x < 0.13:
                    o["jaundice"] = True
                elif x < 0.16:
                    o["respiratory_rate"] = rnd.randint(60, 68)
                elif x < 0.175:
                    o["poor_feeding"] = True
                elif x < 0.19:
                    o["temperature_c"] = round(rnd.uniform(37.5, 38.2), 1)
            oid = uid()
            rows.add(NewbornObservation, id=oid, newborn_id=nid, observed_at=t, source="manual", voice_draft_id=None,
                     idempotency_key=None, created_by=None, created_at=t, updated_at=t, **o)
            last_obs = (oid, t, o)
        facts = {"birth_weight_g": bw, "gestational_age_weeks": ga, "apgar_5": ap5}
        if last_obs and NOW - last_obs[1] <= timedelta(days=7):
            facts.update(last_obs[2])
        out = clinical_rules.evaluate(nb_rules, "newborn", facts)
        nb_zone[out.level] += 1
        rows.add(Newborn, id=nid, local_code=f"N-{j:04d}", mother_id=mid, pregnancy_id=pid, birth_datetime=born,
                 sex=rnd.choice("FM"), gestational_age_weeks=ga, birth_weight_g=bw, apgar_1=max(1, ap5 - rnd.randint(0, 2)),
                 apgar_5=ap5, facility_id=fac.id, region_soato=reg, status="in_facility" if age_d < 3 else "discharged",
                 current_risk_level=out.level, is_synthetic=True, created_by=None)
        ra_id = uid()
        ra_at = last_obs[1] if last_obs else born
        rows.add(RiskAssessment, id=ra_id, subject_type="newborn", subject_id=nid, level=out.level, triggered_rules=out.triggered,
                 missing_inputs=out.missing, rule_set_version=out.rule_set_version, ml_shadow=None, model_version=None,
                 trigger_source_id=last_obs[0] if last_obs else None, created_by=None, created_at=ra_at, updated_at=ra_at)
        if out.level in ("red", "yellow"):
            sev = out.level
            rule_ids = [t["rule_id"] for t in out.triggered if t["severity"] == sev]
            created = ra_at + timedelta(seconds=20)
            if want_open and nb_open.get(sev):
                mode = "open"
                nb_open.pop(sev)
                used_d.add(dcode)
            else:
                # static findings (weight / gestational age) were answered around birth
                static_only = all(r in ("N_VERY_LOW_BIRTH_WEIGHT", "N_LOW_BIRTH_WEIGHT", "N_VERY_PRETERM", "N_PRETERM",
                                        "N_APGAR5_VERY_LOW", "N_APGAR5_LOW") for r in rule_ids)
                if static_only:
                    created = born + timedelta(minutes=rnd.randint(20, 90))
                created = min(created, NOW - timedelta(minutes=45))
                mode = aged_mode(rnd, created)
            a = add_alert(rows, rnd, subject_type="newborn", subject_id=nid, mother_id=mid, newborn_id=nid, facility_id=fac.id,
                          region=reg, severity=sev, rule_ids=rule_ids, ra_id=ra_id, created=created, mode=mode,
                          ova_id=ova.id, doctor_id=doctor.id)
            open_alerts += a["status"] == "open"
            overdue_alerts += a["status"] == "open" and a["escalation_level"] > 0

    rows.flush(db)
    write_audit(db, actor=None, action="seed.national", resource_type="database", resource_id="perinatal_live",
                detail={"pregnancies": len(cases), "newborns": args.newborns, "synthetic": True})
    summary.update({
        "pregnancies": len(cases), "districts_covered": len({c["district"] for c in cases}),
        "min_per_district": min(per_district.values()), "max_per_district": max(per_district.values()),
        "zones": zone_count, "early_warning_yellow": early, "newborns": args.newborns, "newborn_zones": nb_zone,
        "observations": len(rows.t[MaternalObservation]), "labs": len(rows.t[LabResult]),
        "alerts_total": len(rows.t[Alert]), "alerts_open": int(open_alerts), "alerts_overdue": int(overdue_alerts),
        "alerts_acknowledged": sum(1 for a in rows.t[Alert] if a["status"] == "acknowledged"),
        "users": db.execute(select(func.count(User.id))).scalar(), "demo_district": DEMO_DISTRICT,
        "madina_pregnancy_id": madina_id, "siat_year": year, "seconds": round(time.time() - t_start, 1),
        "database": s.DATABASE_URL,
    })
    db.close()
    print(json.dumps(summary, indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()

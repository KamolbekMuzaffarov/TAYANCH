from __future__ import annotations

import argparse
import json
import random
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import BACKEND_DIR, get_settings  # noqa: E402
from app.core.security import encrypt, hash_password, utcnow  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (CareAssignment, District, Facility, LabResult, MaternalObservation, ModelVersion, Mother,  # noqa: E402
                        Newborn, NewbornObservation, Pregnancy, Region, Role, ScopeType, User, UserScope)
from app.services import clinical_rules, data_import, regional, risk_engine  # noqa: E402

DEMO_USERS = [
    # username, role, scope
    ("national.admin", Role.NATIONAL_ADMIN, ("national", None)),
    ("auditor", Role.AUDITOR, ("national", None)),
    ("data.steward", Role.DATA_STEWARD, ("national", None)),
    ("qr.region.admin", Role.REGIONAL_ADMIN, ("region", "1735")),
    ("qr.facility.admin", Role.FACILITY_ADMIN, ("facility", "DEMO-1735-MAT")),
    ("qr.obgyn", Role.OBGYN, ("facility", "DEMO-1735-MAT")),
    ("qr.neonatologist", Role.NEONATOLOGIST, ("facility", "DEMO-1735-MAT")),
    ("qr.lab", Role.LAB_OPERATOR, ("facility", "DEMO-1735-MAT")),
    ("qr.ovabmu", Role.OVABMU_SPECIALIST, ("facility", "DEMO-1735-OVA")),
    ("an.obgyn", Role.OBGYN, ("facility", "DEMO-1703-MAT")),
    ("an.ovabmu", Role.OVABMU_SPECIALIST, ("facility", "DEMO-1703-OVA")),
]


def create_facilities(db) -> None:
    for r in db.execute(select(Region)).scalars():
        dist = db.execute(select(District).where(District.region_soato == r.soato).order_by(District.name)).scalars().first()
        for code, name, ftype, level in [
            (f"DEMO-{r.soato}-OVA", f"OvaBMU hududiy markazi — {r.name_uz} (DEMO)", "ovabmu", "regional"),
            (f"DEMO-{r.soato}-MAT", f"Tuman tug‘ruq kompleksi — {dist.name if dist else r.name_uz} (DEMO)", "maternity", "district"),
        ]:
            if db.execute(select(Facility).where(Facility.code == code)).scalar_one_or_none():
                continue
            db.add(Facility(code=code, name=name, facility_type=ftype, level=level, region_soato=r.soato,
                            district_code=dist.code if dist and level == "district" else None,
                            lat=dist.centroid_lat if dist else None, lon=dist.centroid_lon if dist else None, is_synthetic=True,
                            notes="Synthetic demo facility - not a real institution record"))
    db.commit()


def create_users(db, password: str) -> list[str]:
    created = []
    for username, role, (stype, sval) in DEMO_USERS:
        if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
            continue
        u = User(username=username, full_name=f"DEMO {username}", password_hash=hash_password(password), role=role)
        db.add(u)
        db.flush()
        if stype == "national":
            db.add(UserScope(user_id=u.id, scope_type=ScopeType.NATIONAL))
        elif stype == "region":
            db.add(UserScope(user_id=u.id, scope_type=ScopeType.REGION, region_soato=sval))
        else:
            fac = db.execute(select(Facility).where(Facility.code == sval)).scalar_one()
            db.add(UserScope(user_id=u.id, scope_type=ScopeType.FACILITY, facility_id=fac.id))
        created.append(username)
    db.commit()
    return created


def synthetic_registry(db, n_mothers: int = 36, seed: int = 7) -> dict:
    rnd = random.Random(seed)
    users = {u.username: u for u in db.execute(select(User)).scalars()}
    facs = {f.code: f for f in db.execute(select(Facility)).scalars()}
    plan = [("qr.obgyn", "DEMO-1735-MAT")] * (n_mothers // 2) + [("an.obgyn", "DEMO-1703-MAT")] * (n_mothers - n_mothers // 2)
    stats = {"mothers": 0, "newborns": 0}
    now = utcnow()
    for i, (uname, fcode) in enumerate(plan, start=1):
        clin, fac = users[uname], facs[fcode]
        byear = rnd.randint(1985, 2007)
        m = Mother(local_code=f"M-DEMO{i:04d}", full_name_enc=encrypt(f"DEMO Ayol {i:03d}"),
                   birth_date_enc=encrypt(f"{byear}-01-01"), birth_year=byear, region_soato=fac.region_soato,
                   district_code=fac.district_code, facility_id=fac.id, is_synthetic=True, created_by=clin.id)
        db.add(m)
        db.flush()
        db.add(CareAssignment(user_id=clin.id, mother_id=m.id))
        weeks = rnd.randint(10, 39)
        lmp = (now - timedelta(weeks=weeks)).date()
        p = Pregnancy(mother_id=m.id, lmp_date=lmp, edd=lmp + timedelta(days=280), gravida=rnd.randint(1, 5),
                      parity=0, facility_id=fac.id, assigned_clinician_id=clin.id, is_synthetic=True)
        p.parity = rnd.randint(0, p.gravida - 1)
        db.add(p)
        db.flush()
        profile = rnd.choices(["normal", "htn", "severe", "anaemia", "stale"], weights=[55, 15, 8, 12, 10])[0]
        days_ago = 45 if profile == "stale" else rnd.randint(0, 6)
        sbp, dbp = rnd.randint(100, 128), rnd.randint(62, 82)
        if profile == "htn":
            sbp, dbp = rnd.randint(140, 155), rnd.randint(90, 100)
        if profile == "severe":
            sbp, dbp = rnd.randint(160, 175), rnd.randint(105, 115)
        db.add(MaternalObservation(pregnancy_id=p.id, observed_at=now - timedelta(days=days_ago, hours=rnd.randint(0, 8)),
                                   gestational_week=weeks, systolic_bp=sbp, diastolic_bp=dbp, heart_rate=rnd.randint(70, 98),
                                   temperature_c=round(rnd.uniform(36.2, 37.1), 1), spo2=rnd.randint(96, 99),
                                   fetal_heart_rate=rnd.randint(125, 155) if weeks >= 20 else None,
                                   proteinuria="2+" if profile == "severe" else "neg", created_by=clin.id))
        hb = rnd.randint(64, 95) if profile == "anaemia" else rnd.randint(105, 130)
        db.add(LabResult(pregnancy_id=p.id, test_code="hemoglobin", value=hb, unit="g/L",
                         collected_at=now - timedelta(days=days_ago + 1), facility_id=fac.id, created_by=clin.id))
        db.add(LabResult(pregnancy_id=p.id, test_code="glucose_fasting", value=round(rnd.uniform(4.0, 5.6), 1), unit="mmol/L",
                         collected_at=now - timedelta(days=days_ago + 1), facility_id=fac.id, created_by=clin.id))
        db.flush()
        risk_engine.assess_pregnancy(db, p, actor_id=clin.id)
        stats["mothers"] += 1
    # newborns at the Karakalpakstan demo maternity
    neo = users["qr.neonatologist"]
    fac = facs["DEMO-1735-MAT"]
    for j in range(1, 13):
        ga = round(rnd.choice([rnd.uniform(38, 41), rnd.uniform(38, 41), rnd.uniform(33, 36.9), rnd.uniform(29, 31.9)]), 1)
        bw = int(ga * 85 + rnd.randint(-300, 250))
        nb = Newborn(local_code=f"NB-DEMO{j:04d}", birth_datetime=now - timedelta(hours=rnd.randint(2, 70)), sex=rnd.choice("FM"),
                     gestational_age_weeks=ga, birth_weight_g=max(900, bw), apgar_1=rnd.randint(5, 9), apgar_5=rnd.randint(6, 10),
                     facility_id=fac.id, region_soato=fac.region_soato, is_synthetic=True, created_by=neo.id)
        db.add(nb)
        db.flush()
        db.add(NewbornObservation(newborn_id=nb.id, observed_at=now - timedelta(hours=1),
                                  temperature_c=round(rnd.choice([rnd.uniform(36.5, 37.2)] * 4 + [rnd.uniform(35.6, 36.4)]), 1),
                                  heart_rate=rnd.randint(118, 165), respiratory_rate=rnd.randint(38, 64), spo2=rnd.randint(91, 99),
                                  created_by=neo.id))
        db.flush()
        risk_engine.assess_newborn(db, nb, actor_id=neo.id)
        stats["newborns"] += 1
    db.commit()
    return stats


def register_model(db) -> None:
    s = get_settings()
    card = Path(s.MODEL_DIR) / "maternal_baseline_uci.model_card.json"
    if not card.exists():
        return
    meta = json.loads(card.read_text(encoding="utf-8"))
    if db.execute(select(ModelVersion).where(ModelVersion.name == "maternal_baseline_uci", ModelVersion.version == meta["version"])).first():
        return
    db.add(ModelVersion(name="maternal_baseline_uci", version=meta["version"], status="shadow",
                        purpose="Prototype maternal risk decision-support (shadow mode, never drives alerts)",
                        training_dataset="UCI Maternal Health Risk (id 863), deduplicated", artifact_path=str(card.with_suffix("")),
                        metrics={k: v for k, v in meta["metrics"].items() if k != "report"}))
    db.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-synthetic", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    if not s.is_production:
        Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        summary = data_import.load_all(db)
        create_facilities(db)
        summary["rules_added"] = clinical_rules.seed_rules(db)
        summary["risk_scores"] = regional.recompute_all(db)
        register_model(db)
        if not s.is_production and not args.no_synthetic:
            password = s.seed_demo_password or secrets.token_urlsafe(12)
            created = create_users(db, password)
            if created:
                cred = BACKEND_DIR / ".seed_credentials.txt"
                cred.write_text("# DEMO ONLY - local dev users (synthetic data). Do not reuse.\n"
                                f"password={password}\nusers={','.join(created)}\n", encoding="utf-8")
                summary["demo_users"] = len(created)
                summary["credentials_file"] = str(cred)
            if not db.execute(select(Mother.id).where(Mother.is_synthetic.is_(True))).first():
                summary.update(synthetic_registry(db))
        print(json.dumps(summary, indent=1, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()

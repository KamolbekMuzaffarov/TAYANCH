from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import utcnow
from app.models import LabResult, MaternalObservation, Mother, Newborn, NewbornObservation, Pregnancy, RiskAssessment
from app.services import alert_engine, clinical_rules

log = logging.getLogger("perinatal.risk")

OBS_FIELDS = ["systolic_bp", "diastolic_bp", "heart_rate", "respiratory_rate", "temperature_c", "spo2", "weight_kg",
              "fetal_heart_rate", "proteinuria", "reduced_fetal_movements", "vaginal_bleeding", "severe_headache",
              "visual_disturbance", "convulsions"]
NB_OBS_FIELDS = ["temperature_c", "respiratory_rate", "heart_rate", "spo2", "weight_g", "jaundice", "poor_feeding",
                 "convulsions", "severe_chest_indrawing"]


def _hb_g_l(lab: LabResult) -> float:
    return lab.value * 10 if lab.unit.lower() in ("g/dl",) else lab.value


def pregnancy_facts(db: Session, preg: Pregnancy) -> dict:
    s = get_settings()
    since = utcnow() - timedelta(days=s.OBSERVATION_FRESHNESS_DAYS)
    facts: dict = {}
    obs = db.execute(select(MaternalObservation).where(MaternalObservation.pregnancy_id == preg.id,
                                                       MaternalObservation.observed_at >= since)
                     .order_by(MaternalObservation.observed_at.desc()).limit(1)).scalar_one_or_none()
    if obs:
        for f in OBS_FIELDS:
            facts[f] = getattr(obs, f)
        facts["gestational_week"] = obs.gestational_week
        facts["_observation_id"] = obs.id
    if facts.get("gestational_week") is None and preg.lmp_date:
        facts["gestational_week"] = round((utcnow().date() - preg.lmp_date).days / 7, 1)
    for code, key, conv in [("hemoglobin", "lab_hemoglobin_g_l", _hb_g_l), ("glucose_fasting", "lab_glucose_fasting_mmol_l", lambda l: l.value)]:
        lab = db.execute(select(LabResult).where(LabResult.pregnancy_id == preg.id, LabResult.test_code == code,
                                                 LabResult.collected_at >= since)
                         .order_by(LabResult.collected_at.desc()).limit(1)).scalar_one_or_none()
        if lab:
            facts[key] = conv(lab)
    return facts


def newborn_facts(db: Session, nb: Newborn) -> dict:
    s = get_settings()
    facts = {"birth_weight_g": nb.birth_weight_g, "gestational_age_weeks": nb.gestational_age_weeks, "apgar_5": nb.apgar_5}
    since = utcnow() - timedelta(days=min(s.OBSERVATION_FRESHNESS_DAYS, 7))
    obs = db.execute(select(NewbornObservation).where(NewbornObservation.newborn_id == nb.id, NewbornObservation.observed_at >= since)
                     .order_by(NewbornObservation.observed_at.desc()).limit(1)).scalar_one_or_none()
    if obs:
        for f in NB_OBS_FIELDS:
            facts[f] = getattr(obs, f)
        facts["_observation_id"] = obs.id
    return facts


# ------------------------------------------------------------------ ML shadow
_model_cache: dict = {}


def _load_model():
    s = get_settings()
    path = Path(s.MODEL_DIR) / "maternal_baseline_uci.joblib"
    if not path.exists():
        return None
    if "m" not in _model_cache:
        try:
            import joblib
            _model_cache["m"] = joblib.load(path)
        except Exception as e:  # noqa: BLE001 - incompatible pickle (other Python/sklearn): fail soft, once
            log.warning("ml shadow model could not be loaded (%s); retrain with scripts/train_baseline_model.py", type(e).__name__)
            _model_cache["m"] = None
    return _model_cache["m"]


def ml_shadow(db: Session, preg: Pregnancy, facts: dict) -> dict | None:
    """Prototype model trained on a non-Uzbek public dataset. Stored for evaluation only; never shown as a decision."""
    try:
        bundle = _load_model()
        if bundle is None:
            return None
        mother = db.get(Mother, preg.mother_id)
        age = (utcnow().year - mother.birth_year) if mother and mother.birth_year else None
        glucose = facts.get("lab_glucose_fasting_mmol_l")
        temp_c = facts.get("temperature_c")
        row = [age, facts.get("systolic_bp"), facts.get("diastolic_bp"), glucose,
               temp_c * 9 / 5 + 32 if temp_c is not None else None, facts.get("heart_rate")]
        if any(v is None for v in row):
            return {"status": "insufficient_inputs"}
        proba = bundle["model"].predict_proba([row])[0]
        return {"status": "ok", "mode": "shadow", "classes": list(bundle["classes"]),
                "probabilities": [round(float(p), 3) for p in proba], "version": bundle["version"]}
    except Exception as e:  # noqa: BLE001 - ML failure must never block rules
        log.warning("ml shadow failed: %s", type(e).__name__)
        return {"status": "error"}


# ------------------------------------------------------------------ orchestration
def assess_pregnancy(db: Session, preg: Pregnancy, actor_id: str | None = None) -> RiskAssessment:
    s = get_settings()
    facts = pregnancy_facts(db, preg)
    rules = clinical_rules.active_rules(db, "pregnancy", s.CLINICAL_RULES_ALLOW_UNAPPROVED)
    out = clinical_rules.evaluate(rules, "pregnancy", facts)
    shadow = ml_shadow(db, preg, facts)
    ra = RiskAssessment(subject_type="pregnancy", subject_id=preg.id, level=out.level, triggered_rules=out.triggered,
                        missing_inputs=out.missing, rule_set_version=out.rule_set_version, ml_shadow=shadow,
                        model_version=(shadow or {}).get("version"), trigger_source_id=facts.get("_observation_id"),
                        created_by=actor_id)
    db.add(ra)
    db.flush()
    preg.current_risk_level, preg.current_risk_at = out.level, utcnow()
    mother = db.get(Mother, preg.mother_id)
    alert_engine.raise_alert(db, subject_type="pregnancy", subject=preg, assessment=ra, region_soato=mother.region_soato,
                             is_synthetic=preg.is_synthetic)
    db.commit()
    return ra


def assess_newborn(db: Session, nb: Newborn, actor_id: str | None = None) -> RiskAssessment:
    s = get_settings()
    facts = newborn_facts(db, nb)
    rules = clinical_rules.active_rules(db, "newborn", s.CLINICAL_RULES_ALLOW_UNAPPROVED)
    out = clinical_rules.evaluate(rules, "newborn", facts)
    ra = RiskAssessment(subject_type="newborn", subject_id=nb.id, level=out.level, triggered_rules=out.triggered,
                        missing_inputs=out.missing, rule_set_version=out.rule_set_version,
                        trigger_source_id=facts.get("_observation_id"), created_by=actor_id)
    db.add(ra)
    db.flush()
    nb.current_risk_level = out.level
    alert_engine.raise_alert(db, subject_type="newborn", subject=nb, assessment=ra, region_soato=nb.region_soato,
                             is_synthetic=nb.is_synthetic)
    db.commit()
    return ra

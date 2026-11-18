from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClinicalRuleVersion

DEMO = "demo_pending_clinical_approval"

SEED_RULES: list[dict] = [
    # ---------------- maternal (subject: pregnancy)
    {"rule_id": "M_SEVERE_HYPERTENSION", "subject_type": "pregnancy", "severity": "red",
     "definition": {"any": [{"field": "systolic_bp", "op": ">=", "value": 160}, {"field": "diastolic_bp", "op": ">=", "value": 110}]},
     "ref": "ACOG Practice Bulletin No. 222 (2020): severe-range blood pressure >=160/110 mmHg"},
    {"rule_id": "M_HYPERTENSION_GE20W", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"all": [{"field": "gestational_week", "op": ">=", "value": 20},
                            {"any": [{"field": "systolic_bp", "op": ">=", "value": 140}, {"field": "diastolic_bp", "op": ">=", "value": 90}]}]},
     "ref": "ACOG Practice Bulletin No. 222 (2020); WHO recommendations for prevention and treatment of pre-eclampsia (2011)"},
    {"rule_id": "M_PREECLAMPSIA_SEVERE_FEATURES", "subject_type": "pregnancy", "severity": "red",
     "definition": {"all": [{"any": [{"field": "systolic_bp", "op": ">=", "value": 140}, {"field": "diastolic_bp", "op": ">=", "value": 90}]},
                            {"any": [{"field": "proteinuria", "op": "in", "value": ["2+", "3+", "4+"]},
                                     {"field": "severe_headache", "op": "==", "value": True},
                                     {"field": "visual_disturbance", "op": "==", "value": True}]}]},
     "ref": "ACOG Practice Bulletin No. 222 (2020): hypertension with proteinuria or severe features"},
    {"rule_id": "M_CONVULSIONS", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "convulsions", "op": "==", "value": True}, "ref": "WHO MCPC (2017): eclampsia danger sign"},
    {"rule_id": "M_VAGINAL_BLEEDING", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "vaginal_bleeding", "op": "==", "value": True}, "ref": "WHO MCPC (2017): vaginal bleeding danger sign"},
    {"rule_id": "M_HYPOTENSION", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "systolic_bp", "op": "<", "value": 90}, "ref": "MEOWS red trigger (Singh et al., Anaesthesia 2012)"},
    {"rule_id": "M_HEART_RATE_ABNORMAL", "subject_type": "pregnancy", "severity": "red",
     "definition": {"any": [{"field": "heart_rate", "op": ">", "value": 120}, {"field": "heart_rate", "op": "<", "value": 40}]},
     "ref": "MEOWS red trigger (Singh et al., Anaesthesia 2012)"},
    {"rule_id": "M_RESP_RATE_ABNORMAL", "subject_type": "pregnancy", "severity": "red",
     "definition": {"any": [{"field": "respiratory_rate", "op": ">", "value": 30}, {"field": "respiratory_rate", "op": "<", "value": 10}]},
     "ref": "MEOWS red trigger (Singh et al., Anaesthesia 2012)"},
    {"rule_id": "M_LOW_SPO2", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "spo2", "op": "<", "value": 95}, "ref": "MEOWS red trigger SpO2 <95% (Singh et al., 2012)"},
    {"rule_id": "M_FEVER", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"field": "temperature_c", "op": ">=", "value": 38.0}, "ref": "MEOWS trigger temperature >=38 C"},
    {"rule_id": "M_HYPOTHERMIA", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "temperature_c", "op": "<", "value": 35.0}, "ref": "MEOWS red trigger temperature <35 C"},
    {"rule_id": "M_SEVERE_ANAEMIA", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "lab_hemoglobin_g_l", "op": "<", "value": 70}, "ref": "WHO (2011) haemoglobin thresholds: severe anaemia in pregnancy <70 g/L"},
    {"rule_id": "M_MODERATE_ANAEMIA", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"all": [{"field": "lab_hemoglobin_g_l", "op": ">=", "value": 70}, {"field": "lab_hemoglobin_g_l", "op": "<", "value": 100}]},
     "ref": "WHO (2011) haemoglobin thresholds: moderate anaemia 70-99 g/L"},
    {"rule_id": "M_HYPERGLYCAEMIA", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"field": "lab_glucose_fasting_mmol_l", "op": ">=", "value": 5.1},
     "ref": "WHO (2013) diagnostic criteria for hyperglycaemia first detected in pregnancy: fasting >=5.1 mmol/L"},
    {"rule_id": "M_FETAL_HR_ABNORMAL", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"any": [{"field": "fetal_heart_rate", "op": "<", "value": 110}, {"field": "fetal_heart_rate", "op": ">", "value": 160}]},
     "ref": "FIGO consensus guidelines on intrapartum fetal monitoring (2015): normal baseline 110-160 bpm"},
    {"rule_id": "M_REDUCED_FETAL_MOVEMENTS", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"field": "reduced_fetal_movements", "op": "==", "value": True}, "ref": "RCOG Green-top Guideline No. 57"},
    # ---------------- newborn
    {"rule_id": "N_MODERATE_HYPOTHERMIA", "subject_type": "newborn", "severity": "red",
     "definition": {"field": "temperature_c", "op": "<", "value": 36.0}, "ref": "WHO Thermal protection of the newborn (1997): moderate hypothermia 32.0-35.9 C"},
    {"rule_id": "N_MILD_HYPOTHERMIA", "subject_type": "newborn", "severity": "yellow",
     "definition": {"all": [{"field": "temperature_c", "op": ">=", "value": 36.0}, {"field": "temperature_c", "op": "<", "value": 36.5}]},
     "ref": "WHO Thermal protection of the newborn (1997): mild hypothermia 36.0-36.4 C"},
    {"rule_id": "N_FEVER", "subject_type": "newborn", "severity": "yellow",
     "definition": {"field": "temperature_c", "op": ">=", "value": 37.5}, "ref": "WHO IMCI young infant: temperature >=37.5 C"},
    {"rule_id": "N_HYPOXAEMIA", "subject_type": "newborn", "severity": "red",
     "definition": {"field": "spo2", "op": "<", "value": 90}, "ref": "WHO Oxygen therapy for children (2016): SpO2 <90%"},
    {"rule_id": "N_FAST_BREATHING", "subject_type": "newborn", "severity": "yellow",
     "definition": {"field": "respiratory_rate", "op": ">=", "value": 60}, "ref": "WHO IMCI young infant: fast breathing >=60/min"},
    {"rule_id": "N_SEVERE_CHEST_INDRAWING", "subject_type": "newborn", "severity": "red",
     "definition": {"field": "severe_chest_indrawing", "op": "==", "value": True}, "ref": "WHO IMCI young infant danger sign"},
    {"rule_id": "N_HEART_RATE_ABNORMAL", "subject_type": "newborn", "severity": "red",
     "definition": {"any": [{"field": "heart_rate", "op": "<", "value": 100}, {"field": "heart_rate", "op": ">", "value": 180}]},
     "ref": "WHO Pocket book of hospital care for children (2013): neonatal heart rate"},
    {"rule_id": "N_DANGER_SIGNS", "subject_type": "newborn", "severity": "red",
     "definition": {"any": [{"field": "convulsions", "op": "==", "value": True}, {"field": "poor_feeding", "op": "==", "value": True}]},
     "ref": "WHO IMCI young infant danger signs (convulsions, not feeding well)"},
    {"rule_id": "N_JAUNDICE", "subject_type": "newborn", "severity": "yellow",
     "definition": {"field": "jaundice", "op": "==", "value": True}, "ref": "WHO IMCI young infant: jaundice"},
    {"rule_id": "N_VERY_LOW_BIRTH_WEIGHT", "subject_type": "newborn", "severity": "red",
     "definition": {"field": "birth_weight_g", "op": "<", "value": 1500}, "ref": "WHO definition: very low birth weight <1500 g"},
    {"rule_id": "N_LOW_BIRTH_WEIGHT", "subject_type": "newborn", "severity": "yellow",
     "definition": {"all": [{"field": "birth_weight_g", "op": ">=", "value": 1500}, {"field": "birth_weight_g", "op": "<", "value": 2500}]},
     "ref": "WHO definition: low birth weight <2500 g"},
    {"rule_id": "N_VERY_PRETERM", "subject_type": "newborn", "severity": "red",
     "definition": {"field": "gestational_age_weeks", "op": "<", "value": 32}, "ref": "WHO preterm birth: very preterm <32 weeks"},
    {"rule_id": "N_PRETERM", "subject_type": "newborn", "severity": "yellow",
     "definition": {"all": [{"field": "gestational_age_weeks", "op": ">=", "value": 32}, {"field": "gestational_age_weeks", "op": "<", "value": 37}]},
     "ref": "WHO preterm birth: <37 completed weeks"},
    {"rule_id": "N_APGAR5_VERY_LOW", "subject_type": "newborn", "severity": "red",
     "definition": {"field": "apgar_5", "op": "<", "value": 4}, "ref": "ACOG/AAP Committee Opinion No. 644 (2015)"},
    {"rule_id": "N_APGAR5_LOW", "subject_type": "newborn", "severity": "yellow",
     "definition": {"all": [{"field": "apgar_5", "op": ">=", "value": 4}, {"field": "apgar_5", "op": "<", "value": 7}]},
     "ref": "ACOG/AAP Committee Opinion No. 644 (2015)"},
]

# Inputs that must be present & fresh for a GREEN status (otherwise GREY).
REQUIRED_INPUTS = {
    "pregnancy": ["systolic_bp", "diastolic_bp"],
    "newborn": ["temperature_c", "heart_rate"],
}


def seed_rules(db: Session, approved_by: str | None = None) -> int:
    n = 0
    for r in SEED_RULES:
        exists = db.execute(select(ClinicalRuleVersion).where(ClinicalRuleVersion.rule_id == r["rule_id"],
                                                             ClinicalRuleVersion.version == 1)).scalar_one_or_none()
        if exists:
            continue
        db.add(ClinicalRuleVersion(rule_id=r["rule_id"], version=1, subject_type=r["subject_type"], severity=r["severity"],
                                   definition=r["definition"], approval_status="approved" if approved_by else DEMO,
                                   approved_by=approved_by, clinical_reference=r["ref"], effective_from=date(2026, 11, 17)))
        n += 1
    db.commit()
    return n


def _cond(node: dict, facts: dict[str, Any]) -> bool:
    if "all" in node:
        return all(_cond(c, facts) for c in node["all"])
    if "any" in node:
        return any(_cond(c, facts) for c in node["any"])
    v = facts.get(node["field"])
    if v is None:
        return False
    op, target = node["op"], node["value"]
    try:
        return {"<": lambda: v < target, "<=": lambda: v <= target, ">": lambda: v > target, ">=": lambda: v >= target,
                "==": lambda: v == target, "in": lambda: v in target}[op]()
    except (TypeError, KeyError):
        return False


@dataclass
class RuleOutcome:
    level: str
    triggered: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rule_set_version: str = ""


def active_rules(db: Session, subject_type: str, allow_unapproved: bool) -> list[ClinicalRuleVersion]:
    q = select(ClinicalRuleVersion).where(ClinicalRuleVersion.subject_type == subject_type, ClinicalRuleVersion.active.is_(True))
    rules = list(db.execute(q).scalars())
    latest: dict[str, ClinicalRuleVersion] = {}
    for r in rules:
        if r.approval_status == "retired":
            continue
        if r.approval_status != "approved" and not allow_unapproved:
            continue
        if r.rule_id not in latest or r.version > latest[r.rule_id].version:
            latest[r.rule_id] = r
    return sorted(latest.values(), key=lambda r: r.rule_id)


def evaluate(rules: list[ClinicalRuleVersion], subject_type: str, facts: dict[str, Any]) -> RuleOutcome:
    triggered = [{"rule_id": r.rule_id, "version": r.version, "severity": r.severity,
                  "approval_status": r.approval_status, "reference": r.clinical_reference}
                 for r in rules if _cond(r.definition, facts)]
    missing = [f for f in REQUIRED_INPUTS[subject_type] if facts.get(f) is None]
    if any(t["severity"] == "red" for t in triggered):
        level = "red"
    elif triggered:
        level = "yellow"
    elif missing or not rules:
        level = "grey"  # never green without sufficient fresh data
    else:
        level = "green"
    version = "rules:" + ",".join(f"{r.rule_id}@{r.version}" for r in rules)
    import hashlib
    return RuleOutcome(level, triggered, missing, "rs-" + hashlib.sha1(version.encode()).hexdigest()[:10])

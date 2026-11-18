from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime

# ------------------------------------------------------------------ text helpers
_APOS_AFTER_OG = re.compile(r"(?<=[oOgG])[‘’'`´ʼ]")
_APOS_OTHER = re.compile(r"[‘’'`´]")


def uz(text: str | None) -> str | None:
    """Normalise apostrophes: oʻ / gʻ use U+02BB, every other apostrophe becomes U+02BC."""
    if text is None:
        return None
    return _APOS_OTHER.sub("ʼ", _APOS_AFTER_OG.sub("ʻ", text))


def num(v: float | int | None, digits: int = 1) -> str:
    """Uzbek decimal comma, no trailing zeros."""
    if v is None:
        return "—"
    if isinstance(v, int) or float(v).is_integer():
        return str(int(v))
    return f"{v:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


SYMPTOM_UZ = {
    "severe_headache": "Bosh ogʻrigʻi",
    "visual_disturbance": "Koʻrish xiralashgan",
    "vaginal_bleeding": "Qon ketishi",
    "reduced_fetal_movements": "Homila kam qimirlayapti",
    "convulsions": "Tutqanoq",
}
NB_SYMPTOM_UZ = {
    "jaundice": "Sariqlik",
    "poor_feeding": "Yaxshi emmayapti",
    "convulsions": "Tutqanoq",
    "severe_chest_indrawing": "Koʻkrak qafasi ichga tortilyapti",
}
HISTORY_UZ = {
    "previous_cesarean": "Oldingi kesarcha kesish",
    "previous_preeclampsia": "Oldingi homiladorlikda preeklampsiya",
    "previous_gestational_hypertension": "Oldingi homiladorlikda yuqori bosim",
    "chronic_hypertension": "Surunkali gipertoniya",
    "diabetes": "Qandli diabet",
    "multiple_pregnancy": "Koʻp homilali homiladorlik",
    "anaemia_history": "Kamqonlik anamnezi",
    "short_birth_interval": "Tugʻruqlar orasi 2 yildan kam",
    "grand_multiparity": "5 va undan ortiq tugʻruq",
    "previous_stillbirth": "Oldingi oʻlik tugʻilish",
    "previous_preterm_birth": "Oldingi muddatidan oldin tugʻruq",
    "kidney_disease": "Buyrak kasalligi",
    "thyroid_disease": "Qalqonsimon bez kasalligi",
}

# ------------------------------------------------------------------ rules: Uzbek label + short reference
RULE_UZ: dict[str, tuple[str, str]] = {
    "M_SEVERE_HYPERTENSION": ("Juda yuqori bosim (160/110 va undan yuqori)", "ACOG 2020"),
    "M_HYPERTENSION_GE20W": ("Yuqori bosim (140/90 va undan yuqori, 20 haftadan keyin)", "ACOG 2020; JSST 2011"),
    "M_PREECLAMPSIA_SEVERE_FEATURES": ("Yuqori bosim va xavfli belgilar (preeklampsiya gumoni)", "ACOG 2020"),
    "M_CONVULSIONS": ("Tutqanoq (eklampsiya xavfi)", "JSST MCPC 2017"),
    "M_VAGINAL_BLEEDING": ("Qon ketishi", "JSST MCPC 2017"),
    "M_HYPOTENSION": ("Past bosim (90 dan past)", "MEOWS 2012"),
    "M_HEART_RATE_ABNORMAL": ("Puls meʼyordan tashqari (120 dan yuqori yoki 40 dan past)", "MEOWS 2012"),
    "M_RESP_RATE_ABNORMAL": ("Nafas soni meʼyordan tashqari", "MEOWS 2012"),
    "M_LOW_SPO2": ("Qonda kislorod past (95% dan kam)", "MEOWS 2012"),
    "M_FEVER": ("Isitma (38 °C va undan yuqori)", "MEOWS 2012"),
    "M_HYPOTHERMIA": ("Tana harorati past (35 °C dan kam)", "MEOWS 2012"),
    "M_SEVERE_ANAEMIA": ("Ogʻir kamqonlik (gemoglobin 70 g/l dan past)", "JSST 2011"),
    "M_MODERATE_ANAEMIA": ("Oʻrtacha kamqonlik (gemoglobin 70–99 g/l)", "JSST 2011"),
    "M_HYPERGLYCAEMIA": ("Qonda qand yuqori (och qoringa 5,1 va undan yuqori)", "JSST 2013"),
    "M_FETAL_HR_ABNORMAL": ("Homila yurak urishi meʼyordan tashqari (110–160 dan chetda)", "FIGO 2015"),
    "M_REDUCED_FETAL_MOVEMENTS": ("Homila kam qimirlayapti", "RCOG GTG 57"),
    # live-layer rules (same DSL, same versioned table)
    "M_SHOCK_INDEX_RED": ("Shok indeksi juda yuqori (1,7 va undan yuqori)", "CRADLE (Nathan 2015)"),
    "M_SHOCK_INDEX_YELLOW": ("Shok indeksi yuqori (0,9 va undan yuqori)", "CRADLE (Nathan 2015)"),
    "M_TACHYCARDIA_MILD": ("Puls tezlashgan (100–120)", "MEOWS 2012"),
    "M_MEOWS_TWO_YELLOW": ("Bir vaqtda ikki ogohlantiruvchi belgi", "MEOWS 2012"),
    # newborn
    "N_MODERATE_HYPOTHERMIA": ("Chaqaloq sovqotgan (36,0 °C dan past)", "JSST 1997"),
    "N_MILD_HYPOTHERMIA": ("Chaqaloq harorati biroz past (36,0–36,4 °C)", "JSST 1997"),
    "N_FEVER": ("Chaqaloqda isitma (37,5 °C va undan yuqori)", "JSST IMCI"),
    "N_HYPOXAEMIA": ("Kislorod yetishmayapti (SpO2 90% dan kam)", "JSST 2016"),
    "N_FAST_BREATHING": ("Tez nafas (daqiqasiga 60 va undan koʻp)", "JSST IMCI"),
    "N_SEVERE_CHEST_INDRAWING": ("Koʻkrak qafasining ichga tortilishi", "JSST IMCI"),
    "N_HEART_RATE_ABNORMAL": ("Chaqaloq yurak urishi meʼyordan tashqari", "JSST 2013"),
    "N_DANGER_SIGNS": ("Xavf belgilari: tutqanoq yoki yaxshi emmayapti", "JSST IMCI"),
    "N_JAUNDICE": ("Sariqlik", "JSST IMCI"),
    "N_VERY_LOW_BIRTH_WEIGHT": ("Juda kam vazn bilan tugʻilgan (1500 g dan kam)", "JSST"),
    "N_LOW_BIRTH_WEIGHT": ("Kam vazn bilan tugʻilgan (2500 g dan kam)", "JSST"),
    "N_VERY_PRETERM": ("Juda erta tugʻilgan (32 haftadan oldin)", "JSST"),
    "N_PRETERM": ("Muddatidan oldin tugʻilgan (37 haftadan oldin)", "JSST"),
    "N_APGAR5_VERY_LOW": ("Apgar 5-daqiqada juda past (4 dan kam)", "ACOG/AAP 2015"),
    "N_APGAR5_LOW": ("Apgar 5-daqiqada past (4–6)", "ACOG/AAP 2015"),
    # early-warning trend flags (never red)
    "T_BP_RISING": ("Bosim tashrifdan tashrifga oshib boryapti (erta ogohlantirish)", "Trend tahlili"),
    "T_HB_FALLING": ("Gemoglobin pasayib boryapti (erta ogohlantirish)", "Trend tahlili"),
    "T_WEIGHT_GAIN": ("Vazn haftasiga 1 kg dan koʻp oshyapti (erta ogohlantirish)", "Trend tahlili"),
}
TREND_RULES = [
    {"rule_id": "T_BP_RISING", "subject_type": "pregnancy", "severity": "yellow"},
    {"rule_id": "T_HB_FALLING", "subject_type": "pregnancy", "severity": "yellow"},
    {"rule_id": "T_WEIGHT_GAIN", "subject_type": "pregnancy", "severity": "yellow"},
]
EARLY = "erta ogohlantirish"

# Extra rules of the live layer. Stored in clinical_rule_versions next to the seed rules, so they are
# versioned, listed in /rules and evaluated by the same engine. They use two derived facts
# (see derive_facts): `shock_index` and `meows_yellow_count`.
LIVE_RULES: list[dict] = [
    {"rule_id": "M_SHOCK_INDEX_RED", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "shock_index", "op": ">=", "value": 1.7},
     "ref": "Nathan HL et al., BJOG 2015 (CRADLE): shock index >=1.7 indicates urgent need for intervention"},
    {"rule_id": "M_SHOCK_INDEX_YELLOW", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"all": [{"field": "shock_index", "op": ">=", "value": 0.9}, {"field": "shock_index", "op": "<", "value": 1.7}]},
     "ref": "Nathan HL et al., BJOG 2015 (CRADLE): shock index >=0.9 warrants attention / referral"},
    {"rule_id": "M_TACHYCARDIA_MILD", "subject_type": "pregnancy", "severity": "yellow",
     "definition": {"all": [{"field": "heart_rate", "op": ">=", "value": 100}, {"field": "heart_rate", "op": "<=", "value": 120}]},
     "ref": "MEOWS yellow trigger: heart rate 100-120 (Singh et al., Anaesthesia 2012)"},
    {"rule_id": "M_MEOWS_TWO_YELLOW", "subject_type": "pregnancy", "severity": "red",
     "definition": {"field": "meows_yellow_count", "op": ">=", "value": 2},
     "ref": "MEOWS: one red or two simultaneous yellow triggers require urgent review (Singh et al., Anaesthesia 2012)"},
]


def derive_facts(facts: dict) -> dict:
    """Add `shock_index` and `meows_yellow_count` (vital-sign yellows of the same observation)."""
    sbp, dbp, hr = facts.get("systolic_bp"), facts.get("diastolic_bp"), facts.get("heart_rate")
    temp, rr, week = facts.get("temperature_c"), facts.get("respiratory_rate"), facts.get("gestational_week")
    si = round(hr / sbp, 2) if hr and sbp else None
    yellows = 0
    severe = (sbp is not None and sbp >= 160) or (dbp is not None and dbp >= 110)
    if not severe and (week is None or week >= 20) and ((sbp is not None and sbp >= 140) or (dbp is not None and dbp >= 90)):
        yellows += 1
    if temp is not None and temp >= 38.0:
        yellows += 1
    if hr is not None and (100 <= hr <= 120 or 40 <= hr < 50):
        yellows += 1
    elif si is not None and 0.9 <= si < 1.7:  # do not count tachycardia twice
        yellows += 1
    if rr is not None and 21 <= rr <= 30:
        yellows += 1
    facts["shock_index"] = si
    facts["meows_yellow_count"] = yellows
    return facts


def shock_index_zone(si: float | None) -> str | None:
    if si is None:
        return None
    return "red" if si >= 1.7 else "yellow" if si >= 0.9 else "green"


# ------------------------------------------------------------------ trend analysis
@dataclass
class Point:
    """One visit, reduced to what the trend needs."""
    at: datetime
    week: float | None = None
    sbp: int | None = None
    dbp: int | None = None
    hr: int | None = None
    weight: float | None = None


@dataclass
class TrendResult:
    flags: list[dict] = field(default_factory=list)       # [{text, kind}] - shown to the specialist
    lines: list[str] = field(default_factory=list)        # short one-liners for the nurse
    rule_ids: list[str] = field(default_factory=list)     # T_* early-warning ids
    early_warning: bool = False                           # may raise green -> yellow
    shock_index: float | None = None
    bp_slope: float | None = None                         # mmHg / week (systolic)


def _slope(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-9:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def _per_day(points: list[Point]) -> list[Point]:
    """Keep the last measurement of each calendar day (a re-measurement is not a new trend point)."""
    out: dict = {}
    for p in sorted(points, key=lambda p: p.at):
        out[p.at.date()] = p
    return list(out.values())


def _weeks_axis(pts: list[Point]) -> list[float]:
    t0 = pts[0].at
    return [(p.at - t0).total_seconds() / 604800 for p in pts]


def _arrow(values: list) -> str:
    return " → ".join(num(v) for v in values)


def analyze(points: list[Point], hb: list[tuple[datetime, float]], now: datetime | None = None) -> TrendResult:
    res = TrendResult()
    pts = _per_day(points)
    last = pts[-1] if pts else None
    if last and last.hr and last.sbp:
        res.shock_index = round(last.hr / last.sbp, 2)

    # --- blood pressure: slope over the last 3-5 visits
    bp = [p for p in pts if p.sbp is not None and p.dbp is not None][-5:]
    if len(bp) >= 3:
        xs = _weeks_axis(bp)
        span = xs[-1] - xs[0]
        s_sl, d_sl = _slope(xs, [p.sbp for p in bp]), _slope(xs, [p.dbp for p in bp])
        res.bp_slope = round(s_sl, 1) if s_sl is not None else None
        ls, ld = bp[-1].sbp, bp[-1].dbp
        if span >= 2 and s_sl is not None and d_sl is not None:
            s_rise, d_rise = ls - bp[0].sbp, ld - bp[0].dbp
            sys_up = s_sl >= 2.0 and s_rise >= 10 and ls >= 120
            dia_up = d_sl >= 1.5 and d_rise >= 8 and ld >= 78
            if sys_up or dia_up:
                if ls < 140 and ld < 90:
                    lim_s, lim_d, lim = 140, 90, "140/90"
                else:
                    lim_s, lim_d, lim = 160, 110, "160/110"
                cands = []
                if sys_up and ls < lim_s:
                    cands.append((lim_s - ls) / s_sl)
                if dia_up and ld < lim_d:
                    cands.append((lim_d - ld) / d_sl)
                weeks_to = max(1, math.ceil(min(cands))) if cands else None
                if weeks_to is not None and weeks_to <= 6:
                    rate = num(round(s_sl if sys_up else d_sl))
                    res.flags.append({"kind": "bp", "text": (
                        f"Bosim haftasiga ~{rate} mm ga oshyapti ({_arrow([p.sbp for p in bp[-3:]])}); "
                        f"shu surʼatda {weeks_to} haftada {lim} dan oshadi")})
                    res.lines.append(f"Bosim {len(bp[-3:])} tashrifda {_arrow([p.sbp for p in bp[-3:]])}")
                    res.rule_ids.append("T_BP_RISING")
                    if ls < 140 and ld < 90:
                        res.early_warning = True

    # --- haemoglobin: drop over the last (up to 3) labs
    hbs = sorted(hb, key=lambda x: x[0])[-3:]
    if len(hbs) >= 2:
        first, lastv = hbs[0][1], hbs[-1][1]
        if first - lastv >= 10 and lastv < 105:
            res.flags.append({"kind": "hb", "text": f"Gemoglobin pasayyapti: {_arrow([round(v) for _, v in hbs])} g/l"})
            res.lines.append(f"Gemoglobin {_arrow([round(v) for _, v in hbs])}")
            res.rule_ids.append("T_HB_FALLING")
            if lastv >= 100:  # below 100 the anaemia rule already speaks
                res.early_warning = True

    # --- weight gain between the last two visits (>= 5 days apart)
    w = [p for p in pts if p.weight is not None]
    if len(w) >= 2:
        a, b = w[-2], w[-1]
        days = (b.at - a.at).total_seconds() / 86400
        if days >= 5:
            gain = (b.weight - a.weight) / (days / 7)
            if gain > 1.0:
                res.flags.append({"kind": "weight", "text": (
                    f"Vazn haftasiga ~{num(round(gain, 1))} kg ga oshyapti ({num(a.weight)} → {num(b.weight)} kg) — "
                    "shish bor-yoʻqligini tekshiring")})
                res.lines.append(f"Vazn haftasiga ~{num(round(gain, 1))} kg oshgan")
                res.rule_ids.append("T_WEIGHT_GAIN")
                res.early_warning = True

    # --- shock index (information; the zone itself comes from the M_SHOCK_INDEX_* rules)
    z = shock_index_zone(res.shock_index)
    if z in ("yellow", "red"):
        res.flags.append({"kind": "shock", "text": (
            f"Shok indeksi {num(res.shock_index, 2)} (puls / yuqori bosim) — "
            + ("shoshilinch yordam kerak" if z == "red" else "eʼtibor talab qiladi"))})
    return res


def trend_triggers(res: TrendResult) -> list[dict]:
    """Early-warning entries stored next to rule hits in risk_assessments.triggered_rules."""
    return [{"rule_id": rid, "version": 1, "severity": "yellow", "approval_status": EARLY,
             "reference": "Trend analysis (least-squares slope / change between visits); early warning only"}
            for rid in res.rule_ids]


# ------------------------------------------------------------------ reasons
def reason_text(rule_id: str, obs: dict | None = None, facts: dict | None = None) -> str:
    """Short Uzbek reason. `obs` = the triggering observation (dict of fields), may be None."""
    obs = obs or {}
    if rule_id == "M_PREECLAMPSIA_SEVERE_FEATURES":
        parts = []
        if obs.get("severe_headache"):
            parts.append("bosh ogʻrigʻi")
        if obs.get("visual_disturbance"):
            parts.append("koʻrish xiralashishi")
        if obs.get("proteinuria") in ("2+", "3+", "4+"):
            parts.append(f"siydikda oqsil {obs['proteinuria']}")
        if parts:
            return "Yuqori bosim va " + ", ".join(parts)
    return RULE_UZ.get(rule_id, (rule_id, ""))[0]


def reasons_for(triggered: list[dict], obs: dict | None = None) -> list[dict]:
    order = {"red": 0, "yellow": 1}
    out = [{"code": t["rule_id"], "text": reason_text(t["rule_id"], obs), "severity": t["severity"],
            "reference": RULE_UZ.get(t["rule_id"], ("", t.get("reference", "")))[1] or t.get("reference", "")}
           for t in triggered]
    return sorted(out, key=lambda r: (order.get(r["severity"], 2), r["code"].startswith("T_"), r["code"]))


def event_reason(triggered: list[dict], obs: dict | None, facts: dict | None = None) -> str:
    """Very short lower-case reason for the live feed, with the key value."""
    obs, facts = obs or {}, facts or {}
    ids = [t["rule_id"] for t in sorted(triggered, key=lambda t: t["severity"] != "red")]
    bp = f"bosim {obs.get('systolic_bp')}/{obs.get('diastolic_bp')}" if obs.get("systolic_bp") and obs.get("diastolic_bp") else None
    for rid in ids:
        if rid == "M_CONVULSIONS":
            return "tutqanoq"
        if rid == "M_VAGINAL_BLEEDING":
            return "qon ketishi"
        if rid == "M_PREECLAMPSIA_SEVERE_FEATURES" and bp:
            extra = "bosh ogʻrigʻi" if obs.get("severe_headache") else "koʻrish xiralashishi" if obs.get("visual_disturbance") \
                else f"siydikda oqsil {obs.get('proteinuria')}"
            return f"{bp}, {extra}"
        if rid in ("M_SEVERE_HYPERTENSION", "M_HYPERTENSION_GE20W", "M_HYPOTENSION") and bp:
            return bp
        if rid in ("M_SEVERE_ANAEMIA", "M_MODERATE_ANAEMIA") and facts.get("lab_hemoglobin_g_l") is not None:
            return f"gemoglobin {num(facts['lab_hemoglobin_g_l'])} g/l"
        if rid == "M_HYPERGLYCAEMIA" and facts.get("lab_glucose_fasting_mmol_l") is not None:
            return f"qand {num(facts['lab_glucose_fasting_mmol_l'])} mmol/l"
        if rid in ("M_HEART_RATE_ABNORMAL", "M_TACHYCARDIA_MILD") and obs.get("heart_rate"):
            return f"puls {obs['heart_rate']}"
        if rid in ("M_SHOCK_INDEX_RED", "M_SHOCK_INDEX_YELLOW") and facts.get("shock_index"):
            return f"shok indeksi {num(facts['shock_index'], 2)}"
        if rid in ("M_FEVER", "M_HYPOTHERMIA", "N_FEVER", "N_MODERATE_HYPOTHERMIA", "N_MILD_HYPOTHERMIA") and obs.get("temperature_c"):
            return f"harorat {num(obs['temperature_c'])} °C"
        if rid == "M_REDUCED_FETAL_MOVEMENTS":
            return "homila kam qimirlayapti"
    if ids:
        label = RULE_UZ.get(ids[0], (ids[0], ""))[0]
        return label[:1].lower() + label[1:]
    return "koʻrsatkichlar oʻzgargan"


# ------------------------------------------------------------------ action cards (safe nursing actions, no drugs)
_CARDS: dict[str, dict] = {
    "convulsions": {"title": "Tutqanoq — hozir nima qilish kerak", "steps": [
        "Darhol 103 ga qoʻngʻiroq qiling va yordam chaqiring",
        "Ayolni chap yonboshiga yotqizing, boshi ostiga yumshoq narsa qoʻying",
        "Ogʻziga hech narsa solmang, kuch bilan ushlab turmang",
        "Atrofdagi qattiq va oʻtkir buyumlarni olib qoʻying",
        "Ayolni bir daqiqa ham yolgʻiz qoldirmang, tutqanoq vaqtini belgilab qoʻying"]},
    "bleeding": {"title": "Qon ketishi — hozir nima qilish kerak", "steps": [
        "Darhol 103 ga qoʻngʻiroq qiling, transport tayyorlang",
        "Ayolni yotqizing, oyoqlarini biroz koʻtaring, issiq oʻrab qoʻying",
        "Ovqat va ichimlik bermang",
        "Har 5 daqiqada puls va bosimni oʻlchang",
        "Qon miqdorini (prokladka soni) belgilab boring, ayolni yolgʻiz qoldirmang"]},
    "severe_bp": {"title": "Hozir nima qilish kerak", "steps": [
        "Ayolni chap yonboshiga yotqizing, tinch va yorugʻligi past joyda boʻlsin",
        "Ayolning yonida qoling, yolgʻiz qoldirmang",
        "15 daqiqadan soʻng bosimni qayta oʻlchang",
        "Bosh ogʻrigʻi, koʻrish xiralashishi, qorin yuqorisida ogʻriq bor-yoʻqligini soʻrang",
        "Transportni tayyorlab turing — mutaxassis qarorini kuting"]},
    "bp_yellow": {"title": "Bosim yuqori — nima qilish kerak", "steps": [
        "Ayol 15 daqiqa tinch oʻtirsin, soʻng bosimni qayta oʻlchang",
        "Xavf belgilarini soʻrang: bosh ogʻrigʻi, koʻrish xiralashishi, shish",
        "Imkon boʻlsa siydikda oqsilni tekshiring",
        "Xavf belgilari paydo boʻlsa darhol qoʻngʻiroq qilishini tushuntiring"]},
    "shock": {"title": "Puls va bosim xavotirli — nima qilish kerak", "steps": [
        "Ayolni yotqizing, oyoqlarini biroz koʻtaring, issiq oʻrab qoʻying",
        "Qon ketishi, qorin ogʻrigʻi, isitma bor-yoʻqligini tekshiring",
        "5–10 daqiqadan soʻng puls va bosimni qayta oʻlchang",
        "Ayolni yolgʻiz qoldirmang, transportni tayyorlab turing"]},
    "fever": {"title": "Isitma — nima qilish kerak", "steps": [
        "Ayolga koʻproq suyuqlik iching deb tavsiya qiling, yengil kiyintiring",
        "30 daqiqadan soʻng haroratni qayta oʻlchang",
        "Qorin ogʻrigʻi, hidli ajralma, siyishda ogʻriq bor-yoʻqligini soʻrang",
        "Homila qimirlashini soʻrang; holat yomonlashsa darhol xabar bering"]},
    "anaemia": {"title": "Kamqonlik — nima qilish kerak", "steps": [
        "Holsizlik, bosh aylanishi, nafas qisishi bor-yoʻqligini soʻrang",
        "Temirga boy ovqatlanishni tushuntiring (goʻsht, jigar, dukkaklilar, koʻkatlar)",
        "Shifokor tayinlagan preparatlarni muntazam ichayotganini aniqlang",
        "Gemoglobinni qayta topshirish sanasini belgilang"]},
    "glucose": {"title": "Qand yuqori — nima qilish kerak", "steps": [
        "Tahlil och qoringa topshirilganini aniqlang",
        "Shirinlik va shirin ichimliklarni cheklashni tushuntiring",
        "Koʻp chanqash, tez-tez siyish bor-yoʻqligini soʻrang",
        "Shifokor koʻrigi va qayta tahlil sanasini belgilang"]},
    "fetal": {"title": "Homila harakati — nima qilish kerak", "steps": [
        "Ayolni chap yonboshiga yotqizing",
        "2 soat davomida homila harakatlarini sanashni tushuntiring (10 tadan kam boʻlsa — xavf belgisi)",
        "Imkon boʻlsa homila yurak urishini eshiting",
        "Harakat sezilmasa kutmasdan tugʻruq muassasasiga yoʻllang"]},
    "trend": {"title": "Erta ogohlantirish — nima qilish kerak", "steps": [
        "Keyingi tashrifni 1 hafta ichida rejalashtiring",
        "Bosim va vaznni har tashrifda oʻlchab yozing",
        "Xavf belgilarini tushuntiring: bosh ogʻrigʻi, koʻrish xiralashishi, shish, qon ketishi",
        "Belgilar paydo boʻlsa darhol qoʻngʻiroq qilishini ayting"]},
    "routine": {"title": "Rejali kuzatuv", "steps": [
        "Bosim, puls va vaznni oʻlchab yozing",
        "Homila qimirlashini va umumiy ahvolni soʻrang",
        "Xavf belgilarini eslatib oʻting",
        "Keyingi tashrif sanasini belgilang"]},
    "nodata": {"title": "Maʼlumot yetarli emas", "steps": [
        "Ayolga qoʻngʻiroq qiling va tashrif vaqtini kelishing",
        "Tashrifda bosim, puls va vaznni oʻlchang",
        "Oxirgi tahlillar natijasini soʻrang"]},
    # newborn
    "nb_cold": {"title": "Chaqaloq sovqotgan — hozir nima qilish kerak", "steps": [
        "Chaqaloqni onaning koʻkragiga teri-teriga qoʻying, ustidan issiq yoping",
        "Boshiga qalpoqcha kiydiring, xona issiq boʻlsin, yelvizak boʻlmasin",
        "Koʻkrak suti bilan emizishni davom ettiring",
        "30 daqiqadan soʻng haroratni qayta oʻlchang"]},
    "nb_breath": {"title": "Nafas olish qiyin — hozir nima qilish kerak", "steps": [
        "Darhol 103 ga qoʻngʻiroq qiling",
        "Chaqaloqni issiq tuting (teri-teriga), boshini biroz orqaga egib yotqizing",
        "Nafas sonini 1 daqiqa davomida sanang",
        "Chaqaloqni yolgʻiz qoldirmang, transportni tayyorlang"]},
    "nb_danger": {"title": "Xavf belgisi — hozir nima qilish kerak", "steps": [
        "Darhol 103 ga qoʻngʻiroq qiling, transportni tayyorlang",
        "Chaqaloqni issiq tuting (teri-teriga)",
        "Ema olsa — koʻkrak suti bering; majburlab ichirmang",
        "Chaqaloqni yolgʻiz qoldirmang"]},
    "nb_fever": {"title": "Chaqaloqda isitma — nima qilish kerak", "steps": [
        "Ortiqcha oʻralgan boʻlsa yengillashtiring",
        "Tez-tez emizing",
        "30 daqiqadan soʻng haroratni qayta oʻlchang",
        "Harorat tushmasa yoki chaqaloq lanj boʻlsa darhol xabar bering"]},
    "nb_jaundice": {"title": "Sariqlik — nima qilish kerak", "steps": [
        "Sariqlik qayergacha tarqalganini koʻring (kaft va tovonlar — xavf belgisi)",
        "Kuniga kamida 8–12 marta emizishni tushuntiring",
        "Chaqaloq lanj boʻlsa yoki emmasa darhol xabar bering",
        "Shifokor koʻrigini shu kunga belgilang"]},
    "nb_small": {"title": "Kam vaznli chaqaloq — nima qilish kerak", "steps": [
        "Chaqaloqni teri-teriga (kenguru usuli) issiq tuting",
        "Har 2–3 soatda emizing",
        "Harorat va vaznni har tashrifda oʻlchang",
        "Xavf belgilarini onaga tushuntiring: emmaslik, sovqotish, tez nafas"]},
    "nb_routine": {"title": "Rejali kuzatuv", "steps": [
        "Harorat, nafas va vaznni oʻlchang",
        "Emizishni kuzating, kindikni koʻring",
        "Onaga xavf belgilarini eslatib oʻting"]},
}
_CARD_ORDER: list[tuple[str, set[str]]] = [
    ("convulsions", {"M_CONVULSIONS"}),
    ("bleeding", {"M_VAGINAL_BLEEDING"}),
    ("severe_bp", {"M_SEVERE_HYPERTENSION", "M_PREECLAMPSIA_SEVERE_FEATURES"}),
    ("shock", {"M_HYPOTENSION", "M_SHOCK_INDEX_RED", "M_HEART_RATE_ABNORMAL", "M_RESP_RATE_ABNORMAL", "M_LOW_SPO2",
               "M_HYPOTHERMIA"}),
    ("severe_bp", {"M_MEOWS_TWO_YELLOW"}),
    ("anaemia", {"M_SEVERE_ANAEMIA"}),
    ("bp_yellow", {"M_HYPERTENSION_GE20W"}),
    ("shock", {"M_SHOCK_INDEX_YELLOW", "M_TACHYCARDIA_MILD"}),
    ("fever", {"M_FEVER"}),
    ("fetal", {"M_REDUCED_FETAL_MOVEMENTS", "M_FETAL_HR_ABNORMAL"}),
    ("anaemia", {"M_MODERATE_ANAEMIA", "T_HB_FALLING"}),
    ("glucose", {"M_HYPERGLYCAEMIA"}),
    ("trend", {"T_BP_RISING", "T_WEIGHT_GAIN"}),
    ("nb_breath", {"N_SEVERE_CHEST_INDRAWING", "N_HYPOXAEMIA"}),
    ("nb_danger", {"N_DANGER_SIGNS", "N_HEART_RATE_ABNORMAL", "N_APGAR5_VERY_LOW"}),
    ("nb_cold", {"N_MODERATE_HYPOTHERMIA", "N_MILD_HYPOTHERMIA"}),
    ("nb_breath", {"N_FAST_BREATHING"}),
    ("nb_fever", {"N_FEVER"}),
    ("nb_jaundice", {"N_JAUNDICE"}),
    ("nb_small", {"N_VERY_LOW_BIRTH_WEIGHT", "N_LOW_BIRTH_WEIGHT", "N_VERY_PRETERM", "N_PRETERM", "N_APGAR5_LOW"}),
]


def action_card(rule_ids: list[str], zone: str, subject_type: str = "pregnancy") -> dict:
    ids = set(rule_ids)
    for key, members in _CARD_ORDER:
        if ids & members:
            return dict(_CARDS[key])
    if subject_type == "newborn":
        return dict(_CARDS["nb_routine"])
    return dict(_CARDS["nodata" if zone == "grey" else "routine"])


# ------------------------------------------------------------------ summaries
def symptoms_of(obs: dict, table: dict = SYMPTOM_UZ) -> list[str]:
    return [label for key, label in table.items() if obs.get(key)]


def patient_summary(weeks: int | None, visits: list[dict], hb: list[float], now: datetime,
                    glucose: float | None = None) -> str:
    """One line for the specialist: '32 hafta. Bosim 120 → 135 → 150. Bugun bosh ogʻrigʻi.'
    `visits`: oldest -> newest dicts with observed_at, systolic_bp, diastolic_bp, heart_rate + symptom flags."""
    parts = [f"{weeks} hafta." if weeks is not None else "Muddat nomaʼlum."]
    bp = [v for v in visits if v.get("systolic_bp") and v.get("diastolic_bp")]
    if len(bp) >= 2 and (max(v["systolic_bp"] for v in bp[-3:]) >= 130 or abs(bp[-1]["systolic_bp"] - bp[-3:][0]["systolic_bp"]) >= 10):
        parts.append(f"Bosim {_arrow([v['systolic_bp'] for v in bp[-3:]])} (oxirgisi {bp[-1]['systolic_bp']}/{bp[-1]['diastolic_bp']}).")
    elif bp:
        parts.append(f"Bosim {bp[-1]['systolic_bp']}/{bp[-1]['diastolic_bp']}.")
    else:
        parts.append("Bosim oʻlchanmagan.")
    if hb and hb[-1] < 110:
        parts.append(f"Gemoglobin {_arrow([round(v) for v in hb[-3:]])} g/l.")
    if glucose is not None and glucose >= 5.1:
        parts.append(f"Qand {num(glucose)} mmol/l.")
    if visits:
        last = visits[-1]
        days = (now.date() - last["observed_at"].date()).days
        sym = symptoms_of(last)
        if last.get("proteinuria") in ("1+", "2+", "3+", "4+"):
            sym.append(f"siydikda oqsil {last['proteinuria']}")
        if sym:
            text = ", ".join(sym)
            text = text[:1].lower() + text[1:]
            parts.append(("Bugun " if days <= 0 else "Oxirgi tashrifda ") + text + ".")
        if days > 30:
            parts.append(f"Oxirgi tashrif {days} kun oldin.")
    else:
        parts.append("Tashrif qayd etilmagan.")
    return " ".join(parts)


def case_summary(weeks: int | None, last: dict | None, facts: dict | None = None) -> str:
    """Shorter variant for lists: '32 hafta. Bosim 150/95, bosh ogʻrigʻi.'"""
    head = f"{weeks} hafta." if weeks is not None else ""
    if not last:
        return (head + " Tashrif qayd etilmagan.").strip()
    bits = []
    if last.get("systolic_bp") and last.get("diastolic_bp"):
        bits.append(f"Bosim {last['systolic_bp']}/{last['diastolic_bp']}")
    for s in symptoms_of(last):
        bits.append(s[:1].lower() + s[1:] if bits else s)
    facts = facts or {}
    if facts.get("lab_hemoglobin_g_l") is not None and facts["lab_hemoglobin_g_l"] < 100:
        bits.append(("gemoglobin " if bits else "Gemoglobin ") + f"{num(facts['lab_hemoglobin_g_l'])} g/l")
    if facts.get("lab_glucose_fasting_mmol_l") is not None and facts["lab_glucose_fasting_mmol_l"] >= 5.1:
        bits.append(("qand " if bits else "Qand ") + f"{num(facts['lab_glucose_fasting_mmol_l'])} mmol/l")
    return (head + " " + ", ".join(bits) + ".").strip()


def newborn_summary(age_days: int, nb: dict, last: dict | None) -> str:
    """'3 kunlik. 2100 g, 35 hafta. Harorat 35,8 °C.'"""
    parts = [f"{age_days} kunlik."]
    meta = []
    if nb.get("birth_weight_g"):
        meta.append(f"{nb['birth_weight_g']} g")
    if nb.get("gestational_age_weeks"):
        meta.append(f"{num(math.floor(nb['gestational_age_weeks']))} haftada tugʻilgan")
    if meta:
        parts.append(", ".join(meta) + ".")
    if last:
        bits = []
        if last.get("temperature_c") is not None:
            bits.append(f"Harorat {num(last['temperature_c'])} °C")
        if last.get("respiratory_rate") is not None and last["respiratory_rate"] >= 60:
            bits.append(f"nafas {last['respiratory_rate']}/daq")
        for s in symptoms_of(last, NB_SYMPTOM_UZ):
            bits.append(s[:1].lower() + s[1:] if bits else s)
        if bits:
            parts.append(", ".join(bits) + ".")
    else:
        parts.append("Koʻrik qayd etilmagan.")
    return " ".join(parts)


# ------------------------------------------------------------------ nurse-facing result wording
def visit_message(zone: str, *, has_alert: bool, recheck: bool, trend_only: bool, sla_minutes: int | None) -> tuple[str, str]:
    if zone == "red":
        return ("Shifokor chaqirildi",
                f"Ayolning yonida qoling. Mutaxassis {sla_minutes or 30} daqiqa ichida javob beradi.")
    if zone == "yellow" and recheck:
        return ("Qayta oʻlchang", "Ayol 15 daqiqa tinch oʻtirsin, soʻng bosimni qayta oʻlchang. Natija tasdiqlansa mutaxassisga xabar ketadi.")
    if zone == "yellow" and trend_only:
        return ("Erta ogohlantirish", "Koʻrsatkichlar hozircha meʼyorda, lekin oʻzgarish surʼati eʼtibor talab qiladi. Keyingi tashrifni 1 hafta ichida belgilang.")
    if zone == "yellow":
        hours = round((sla_minutes or 240) / 60)
        return ("Mutaxassisga xabar yuborildi", f"Mutaxassis {hours} soat ichida javob beradi. Ayolga xavf belgilarini tushuntiring.")
    if zone == "grey":
        return ("Maʼlumot yetarli emas", "Xulosa uchun bosimni oʻlchab kiriting.")
    return ("Hammasi joyida", "Koʻrsatkichlar meʼyorda. Keyingi tashrif 2 haftadan soʻng.")


NEXT_VISIT_DAYS = {"red": 1, "yellow": 7, "green": 14, "grey": 3}

"""Privacy Gateway - the ONLY path to Claude.

Frontend -> Backend -> PrivacyGateway -> allowlist schema + detectors -> ClaudeProvider

* Typed, closed request schemas per feature (extra fields => rejected).
* No patient context fields exist in any schema; free text is screened and BLOCKED (not redacted)
  on any identifier / clinical signal. Detector failure => fail closed.
* Aggregate clinical-registry data is never sent; only official *published* statistics
  (SIAT / MICS, unsuppressed, region level) can be explained, and the gateway itself loads them.
* Logs: request id, feature, decision, reasons (detector names), provider, model, tokens, status, duration.
  Never the prompt or response text.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import PrivacyEvent, Region, RegionalIndicator, User
from app.services.llm.claude_provider import ClaudeProvider
from app.services.voice.parser import parse as clinical_value_parser

log = logging.getLogger("perinatal.llm")

DISCLAIMER = {"uz": "Ma’muriy yordam. Tibbiy qaror yoki tashxis emas.",
              "uz-cyrl": "Маъмурий ёрдам. Тиббий қарор ёки ташхис эмас.",
              "ru": "Административная помощь. Не является медицинским решением или диагнозом.",
              "en": "Administrative help only. Not a medical decision or diagnosis."}

Lang = Literal["uz", "uz-cyrl", "ru", "en"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AdminHelpRequest(_Strict):
    feature: Literal["admin_help"]
    topic: Literal["system_usage", "reports_howto", "data_quality", "user_roles", "privacy_policy", "translation", "other_admin"]
    question: str = Field(min_length=3, max_length=600)
    language: Lang = "uz"


class DevDocsHelpRequest(_Strict):
    feature: Literal["dev_docs_help"]
    question: str = Field(min_length=3, max_length=600)
    language: Lang = "en"


class PublicStatsExplainRequest(_Strict):
    feature: Literal["public_stats_explain"]
    indicator_code: str = Field(pattern=r"^[a-z0-9_]{3,60}$")
    region_soato: str = Field(pattern=r"^\d{4}$")
    year: int | None = Field(default=None, ge=2000, le=2100)
    language: Lang = "uz"


class SyntheticDemoRequest(_Strict):
    feature: Literal["synthetic_demo_content"]
    kind: Literal["training_scenario", "form_walkthrough"]
    language: Lang = "uz"


AssistantRequest = Annotated[Union[AdminHelpRequest, DevDocsHelpRequest, PublicStatsExplainRequest, SyntheticDemoRequest],
                             Field(discriminator="feature")]
_adapter = TypeAdapter(AssistantRequest)

# ------------------------------------------------------------------ detectors (block, never redact)
_DETECTORS: list[tuple[str, re.Pattern]] = [
    ("pinfl_or_long_number", re.compile(r"\d{6,}")),
    ("phone", re.compile(r"(\+?998)?[\s-]?\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")),
    ("passport", re.compile(r"\b[a-zA-Z]{2}\s?\d{7}\b")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("date", re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b(19|20)\d{2}-\d{2}-\d{2}\b")),
    ("uuid_or_record_id", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-|\b(?:M|NB|P)-[A-Z0-9]{4,}\b", re.I)),
    ("vital_pair", re.compile(r"\b\d{2,3}\s*/\s*\d{2,3}\b")),
    ("person_name_marker", re.compile(r"\b[\w']+(ovna|evna|ovich|evich|ова|ева|овна|евна|ович|евич)\b|\b(qizi|o'g'li|o‘g‘li|қизи|ўғли)\b", re.I)),
    # a patient word followed by a capitalised token (likely a name), e.g. "bemor Karimova", "пациентка Иванова"
    ("patient_reference", re.compile(r"(?i:bemor\w*|patient\w*|пациент\w*|бемор\w*|ayol|аёл|женщин\w*|woman)\s+[A-ZА-ЯЎҚҒҲЁ][a-zа-яўқғҳё'‘]{2,}")),
    ("capitalised_full_name", re.compile(r"\b[A-ZА-ЯЎҚҒҲЁ][a-zа-яўқғҳё'‘]{2,}\s+[A-ZА-ЯЎҚҒҲЁ][a-zа-яўқғҳё'‘]{2,}(ova|eva|ov|ev|ова|ева|ов|ев)\b")),
    ("clinical_content", re.compile(r"(tashxis|diagnoz|diagnos|диагноз|ташхис|preeklamps|eklamps|преэклампс|эклампс|anemi|анеми|"
                                    r"qon\s+ket|кровотеч|davolash|лечени|dori\s+|препарат|dozasi|доз[аы]|triage|prescri|"
                                    r"gemoglobin|гемоглобин|homiladorlik\s+muddati|срок\s+беремен|apgar|ctg|ктг|transkript|transcript)", re.I)),
    ("address", re.compile(r"\b(ko'chasi|kuchasi|mahalla|mahallasi|улица|ул\.|дом\s+\d|uy\s+\d|kvartira|кв\.)", re.I)),
]


def screen_text(text: str) -> list[str]:
    """Return detector names that fired. Any exception propagates (caller fails closed)."""
    hits = [name for name, pat in _DETECTORS if pat.search(text)]
    if clinical_value_parser(text, "pregnancy") or clinical_value_parser(text, "newborn"):
        hits.append("clinical_measurement")
    return sorted(set(hits))


# ------------------------------------------------------------------ prompts (fixed templates)
_SYSTEM = ("You are the administrative help assistant of 'Perinatal AI', a maternal and newborn health monitoring "
           "platform in Uzbekistan. You help staff with software usage, reports, data-quality concepts, roles and "
           "privacy rules, and explain PUBLISHED official statistics. You never give diagnoses, triage, treatment, "
           "dosing, or patient-specific advice; if asked, say that clinical decisions belong to the responsible "
           "clinician and the approved protocols. Never ask for or accept patient details. Answer concisely in the "
           "requested language ({lang}).")
_LANG_NAMES = {"uz": "Uzbek (Latin script)", "uz-cyrl": "Uzbek (Cyrillic script)", "ru": "Russian", "en": "English"}


class PrivacyGateway:
    def __init__(self, db: Session, provider: ClaudeProvider | None = None):
        self.db = db
        self.s = get_settings()
        self._provider = provider

    @property
    def provider(self) -> ClaudeProvider:
        if self._provider is None:
            self._provider = ClaudeProvider()
        return self._provider

    def _event(self, user: User, feature: str | None, decision: str, reasons: list[str], request_id: str, **kw) -> None:
        self.db.add(PrivacyEvent(user_id=user.id, event_type=f"llm_{decision}", feature=feature, decision=decision,
                                 reasons=reasons, request_id=request_id, **kw))
        self.db.commit()
        log.info("llm request_id=%s feature=%s decision=%s reasons=%s status=%s", request_id, feature, decision,
                 ",".join(reasons), kw.get("status"))

    def _block(self, user, feature, reasons, request_id, http=422):
        self._event(user, feature, "blocked", reasons, request_id)
        raise AppError(http, "privacy_blocked",
                       "So‘rov maxfiylik qoidalari bo‘yicha bloklandi: bemorga oid yoki klinik ma’lumot Claude’ga yuborilmaydi. "
                       "Savolni shaxssiz, umumiy shaklda bering.",
                       [{"detector": r} for r in reasons])

    def handle(self, user: User, raw: dict) -> dict:
        request_id = uuid.uuid4().hex[:16]
        feature = raw.get("feature") if isinstance(raw, dict) else None
        if not self.s.CLAUDE_ENABLED:
            raise AppError(503, "assistant_disabled", "Assistant is disabled")
        # 1) strict allowlist schema
        try:
            req = _adapter.validate_python(raw)
        except Exception as e:  # pydantic.ValidationError
            reasons = ["schema_violation"]
            errs = getattr(e, "errors", lambda: [])()
            if any(err.get("type") == "extra_forbidden" for err in errs):
                reasons = ["unexpected_field"]
            self._block(user, feature if isinstance(feature, str) else None, reasons, request_id)
        # 2) build a patient-free prompt; screening failures fail closed
        try:
            user_text = self._build(req)
            reasons = screen_text(user_text) if isinstance(req, (AdminHelpRequest, DevDocsHelpRequest)) else []
        except AppError:
            raise
        except Exception:  # noqa: BLE001
            self._block(user, req.feature, ["detector_error_fail_closed"], request_id)
        if reasons:
            self._block(user, req.feature, reasons, request_id)
        # 3) outbound call (only now)
        res = self.provider.complete(_SYSTEM.format(lang=_LANG_NAMES[req.language]), user_text)
        self._event(user, req.feature, "allowed", [], request_id, provider=self.provider.name, model=res.model,
                    input_tokens=res.input_tokens, output_tokens=res.output_tokens, duration_ms=res.duration_ms,
                    status=res.status)
        if res.status != "ok":
            raise AppError(503, "assistant_unavailable", f"Assistant unavailable ({res.status})")
        return {"request_id": request_id, "feature": req.feature, "answer": res.text,
                "label": "administrative_help_only", "disclaimer": DISCLAIMER[req.language]}

    def _build(self, req) -> str:
        if isinstance(req, AdminHelpRequest):
            return f"Topic: {req.topic}\nQuestion: {req.question}"
        if isinstance(req, DevDocsHelpRequest):
            return f"Developer documentation question about the platform's software stack:\n{req.question}"
        if isinstance(req, SyntheticDemoRequest):
            return (f"Write a short, clearly fictional training {req.kind.replace('_', ' ')} for staff using the platform. "
                    "Label it SYNTHETIC/DEMO. Do not include real names, real facilities, dates or identifiers. "
                    "Do not include clinical treatment instructions.")
        # public statistics: gateway loads the published value itself (official SIAT/MICS, region level only)
        region = self.db.get(Region, req.region_soato) if req.region_soato != "1700" else None
        if req.region_soato != "1700" and region is None:
            raise AppError(422, "invalid_region", "Unknown region code")
        q = select(RegionalIndicator).where(RegionalIndicator.indicator_code == req.indicator_code,
                                            RegionalIndicator.region_soato == req.region_soato,
                                            RegionalIndicator.district_code.is_(None),
                                            RegionalIndicator.source_key.in_(["siat", "mics6"]),
                                            RegionalIndicator.is_synthetic.is_(False))
        rows = list(self.db.execute(q.order_by(RegionalIndicator.period_end.desc())).scalars())
        if req.year:
            rows = [r for r in rows if r.period_end.year == req.year]
        if not rows:
            raise AppError(404, "not_found", "No published statistic for this indicator/region/year")
        r = rows[0]
        if r.suppression_status != "none" or r.rate is None:
            raise AppError(422, "privacy_blocked", "Small-cell or suppressed estimate cannot be sent")
        if not self.s.CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA and r.source_key not in ("siat", "mics6"):
            raise AppError(422, "privacy_blocked", "Aggregate health data is not allowed")
        place = region.name_en if region else "Republic of Uzbekistan"
        ci = f" (95% CI {r.ci_low}-{r.ci_high})" if r.ci_low is not None else ""
        return (f"Explain in plain language, for a regional health manager, what this PUBLISHED official statistic means "
                f"and its limitations. Do not speculate about individual patients.\nIndicator: {req.indicator_code} "
                f"({r.methodology})\nPlace: {place}\nPeriod: {r.period_start.year}-{r.period_end.year}\n"
                f"Value: {r.rate} {r.unit}{ci}\nSource: {r.source_key} {r.source_version or ''}")


# ====================================================================== live "AI brief": k-anonymised aggregates only
# The external model is hosted outside Uzbekistan, so it may only ever see this closed schema: counts and rates of a
# public geography. No names, record codes, dates of birth, free text or per-patient rows can be expressed in it.
K_ANON = 5
SMALL = "<5"
MASKED = "masked"  # complementary (secondary) suppression: withheld so that a "<5" cell cannot be recomputed from a total
BRIEF_FEATURE = "live_ai_brief"
Cell = Union[Annotated[int, Field(ge=K_ANON, le=50_000_000)], Literal["<5", "masked"]]
Zone = Literal["red", "yellow", "green", "grey"]
RuleCategory = Literal["blood_pressure", "bleeding_or_shock", "hb_low", "fever_or_cold", "vital_signs", "blood_sugar",
                       "fetal_signs", "newborn_signs", "early_trend", "other"]
OfficialCode = Literal["infant_mortality_rate", "under5_mortality_rate", "maternal_mortality_ratio"]
CATEGORY_UZ = {"blood_pressure": "qon bosimi koʻtarilishi", "bleeding_or_shock": "qon ketishi yoki shok belgilari",
               "hb_low": "kamqonlik", "fever_or_cold": "tana harorati oʻzgarishi", "vital_signs": "puls va nafas koʻrsatkichlari",
               "blood_sugar": "qondagi qand miqdori", "fetal_signs": "homila holati belgilari", "newborn_signs": "chaqaloq xavf belgilari",
               "early_trend": "erta ogohlantirish (salbiy dinamika)", "other": "boshqa sabablar"}
OFFICIAL_UZ = {"infant_mortality_rate": "goʻdaklar oʻlimi (1000 tirik tugʻilishga)",
               "under5_mortality_rate": "5 yoshgacha bolalar oʻlimi (1000 tirik tugʻilishga)",
               "maternal_mortality_ratio": "onalar oʻlimi (100 000 tirik tugʻilishga)"}


class ZoneCounts(_Strict):
    pregnant: Cell
    red: Cell
    yellow: Cell
    green: Cell
    grey: Cell
    newborns: Cell


class AlertCounts(_Strict):
    open: Cell
    overdue: Cell
    open_mothers: Cell
    open_newborns: Cell


class ResponseStats(_Strict):
    answered: Cell
    median_response_min: int | None = Field(default=None, ge=0, le=100_000)


class RuleCategoryCount(_Strict):
    category: RuleCategory
    alerts: Cell


class OfficialRate(_Strict):
    code: OfficialCode
    year: int = Field(ge=2000, le=2100)
    value: float = Field(ge=0, le=100_000)
    national: float | None = Field(default=None, ge=0, le=100_000)
    status: Zone


class AggregateBriefPayload(_Strict):
    """Everything that may be sent to the external model for one brief. extra=forbid: nothing else can be added."""
    scope: Literal["national", "region", "district"]
    area_name: str = Field(min_length=2, max_length=60)  # public geography only; checked against the gazetteer
    zones: ZoneCounts
    alerts: AlertCounts
    response: ResponseStats
    top_rule_categories: list[RuleCategoryCount] = Field(default_factory=list, max_length=6)
    official: list[OfficialRate] = Field(default_factory=list, max_length=3)
    births_thousand: float | None = Field(default=None, ge=0, le=100_000)


_COUNT_KEYS = {"pregnant", "red", "yellow", "green", "grey", "newborns", "open", "overdue", "open_mothers",
               "open_newborns", "answered", "alerts"}
_NON_COUNT_INT_KEYS = {"median_response_min", "year"}


def kcell(n: int | None) -> int | str:
    """k-anonymity: every cell below 5 (including 0) leaves the gateway as the string "<5"."""
    return int(n) if n is not None and int(n) >= K_ANON else SMALL


def complementary_suppress(cells: dict, total: str, parts: tuple[str, ...], prefer: tuple[str, ...]) -> dict:
    """Secondary suppression inside one breakdown (total = sum of parts), applied AFTER kcell().

    If the total is published and exactly one part is "<5", that part equals total minus the other parts, so one more
    part (the first still-published one in `prefer`) is withheld as "masked". With two or more suppressed parts only
    their sum can be derived - except when that sum pins every one of them (all 0 or all 4): then a further part is
    withheld as well. `raw` true values are never needed here: the decision uses only what would be published."""
    if cells.get(total) in (SMALL, MASKED):
        return cells
    hidden = [p for p in parts if cells[p] in (SMALL, MASKED)]
    if not hidden:
        return cells
    remainder = cells[total] - sum(cells[p] for p in parts if p not in hidden)
    pinned = len(hidden) == 1 or remainder <= 0 or remainder >= (K_ANON - 1) * len(hidden)
    if pinned and not any(cells[p] == MASKED for p in parts):
        victim = next((p for p in prefer if p not in hidden), None)
        if victim is None:
            cells[total] = MASKED
        else:
            cells[victim] = MASKED
    return cells


def rule_category(rule_id: str) -> str:
    r = rule_id.upper()
    if r.startswith("T_"):
        return "early_trend"
    for keys, cat in ((("HYPERTENSION", "PREECLAMPSIA", "CONVULSION"), "blood_pressure"),
                      (("BLEEDING", "SHOCK", "HYPOTENSION"), "bleeding_or_shock"), (("ANAEMIA", "ANEMIA"), "hb_low"),
                      (("FEVER", "HYPOTHERMIA"), "fever_or_cold"), (("GLYC",), "blood_sugar"), (("FETAL",), "fetal_signs"),
                      (("HEART_RATE", "TACHY", "RESP", "SPO2", "HYPOX", "BREATH", "CHEST", "MEOWS"), "vital_signs")):
        if any(k in r for k in keys):
            return cat
    return "newborn_signs" if r.startswith("N_") else "other"


def build_brief_payload(raw: dict) -> AggregateBriefPayload:
    """raw = plain aggregate numbers computed by the live layer (never rows). Applies k-anonymity and the closed schema."""
    cats: dict[str, int] = {}
    for rule_id, n in (raw.get("rule_counts") or {}).items():
        c = rule_category(str(rule_id))
        cats[c] = cats.get(c, 0) + int(n)
    top = [{"category": c, "alerts": n} for c, n in sorted(cats.items(), key=lambda kv: -kv[1]) if n >= K_ANON][:5]
    answered = int(raw.get("answered") or 0)
    official = []
    for o in raw.get("official") or []:
        # a rate computed from fewer than 5 events would reveal the small count: drop it
        if o.get("value") is None or int(o.get("events") or 0) < K_ANON:
            continue
        official.append({"code": o["code"], "year": int(o["year"]), "value": round(float(o["value"]), 1),
                         "national": round(float(o["national"]), 1) if o.get("national") is not None else None,
                         "status": o.get("status") or "grey"})
    births = raw.get("births")
    z, a = raw.get("zones") or {}, raw.get("alerts") or {}
    zones = complementary_suppress({k: kcell(z.get(k)) for k in ("pregnant", "red", "yellow", "green", "grey", "newborns")},
                                   "pregnant", ("red", "yellow", "green", "grey"), ("green", "grey", "yellow", "red"))
    alerts = complementary_suppress({k: kcell(a.get(k)) for k in ("open", "overdue", "open_mothers", "open_newborns")},
                                    "open", ("open_mothers", "open_newborns"), ("open_mothers", "open_newborns"))
    return AggregateBriefPayload.model_validate({
        "scope": raw.get("scope"), "area_name": raw.get("area_name"),
        "zones": zones, "alerts": alerts,
        "response": {"answered": kcell(answered),
                     "median_response_min": raw.get("median_response_min") if answered >= K_ANON else None},
        "top_rule_categories": top, "official": official[:3],
        "births_thousand": round(float(births) / 1000, 1) if births is not None and float(births) >= 1000 else None})


def small_cell_violations(obj, path: str = "") -> list[str]:
    """Independent numeric guard, run on the serialised payload right before the outbound call (defence in depth)."""
    bad: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                bad += small_cell_violations(v, p)
            elif k in _COUNT_KEYS:
                if not (v in (SMALL, MASKED) or (isinstance(v, int) and not isinstance(v, bool) and v >= K_ANON)):
                    bad.append(p)
            elif isinstance(v, int) and not isinstance(v, bool) and k not in _NON_COUNT_INT_KEYS:
                bad.append(p)  # an integer outside the declared count cells is not expected at all
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += small_cell_violations(v, f"{path}[{i}]")
    return bad


def sent_fields(obj, path: str = "") -> list[str]:
    out: list[str] = []
    for k, v in obj.items():
        p = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            out += sent_fields(v, p)
        elif isinstance(v, list):
            if v:
                out.append(f"{p}[{len(v)}]")
        elif v is not None:
            out.append(p)
    return out


def _c(v, unit: str = "nafar") -> str:
    if v == MASKED:
        return "soni maxfiylik uchun koʻrsatilmaydi"
    return f"5 {unit}dan kam" if v == SMALL else f"{v} {unit}"


def local_brief(p: AggregateBriefPayload) -> str:
    """Deterministic Uzbek brief built from the SAME k-anonymised payload (used without a key or when the call fails)."""
    z, a, r = p.zones, p.alerts, p.response
    s = [f"{p.area_name}: kuzatuvda {_c(z.pregnant)} homilador ayol bor; qizil zonada {_c(z.red)}, "
         f"sariq zonada {_c(z.yellow)}."]
    if a.overdue not in (SMALL, MASKED):
        s.append(f"Eng muhimi — {a.overdue} ta signal belgilangan muddatda javobsiz qolgan, ularni birinchi navbatda koʻrib chiqish kerak.")
    elif a.open not in (SMALL, MASKED):
        s.append(f"Hozir {a.open} ta signal mutaxassis javobini kutmoqda; muddati oʻtgan signallar kam sonli.")
    else:
        s.append("Javob kutayotgan va muddati oʻtgan signallar kam sonli — tezkor vaziyat nazorat ostida.")
    if r.median_response_min is not None:
        tail = "bu 30 daqiqalik meʼyordan yuqori, sababini aniqlash zarur" if r.median_response_min > 30 else "bu meʼyor doirasida"
        s.append(f"Signallarga median javob vaqti {r.median_response_min} daqiqa — {tail}.")
    if p.top_rule_categories:
        t = p.top_rule_categories[0]
        s.append(f"Signallarning eng koʻp uchraydigan sababi — {CATEGORY_UZ[t.category]} ({t.alerts} ta).")
    worst = next((o for o in p.official if o.status == "red"), None) or next((o for o in p.official if o.status == "yellow"), None)
    if worst is not None and worst.national is not None:
        s.append(f"Rasmiy statistika ({worst.year}-yil): {OFFICIAL_UZ[worst.code]} {str(worst.value).replace('.', ',')} — "
                 f"respublika darajasidan ({str(worst.national).replace('.', ',')}) yuqori, bu hududga doimiy eʼtibor kerak.")
    elif p.official:
        o = p.official[0]
        s.append(f"Rasmiy statistika ({o.year}-yil): {OFFICIAL_UZ[o.code]} {str(o.value).replace('.', ',')}.")
    return " ".join(s[:5])


_BRIEF_SYSTEM = (
    "You write a short operational brief for a health-system manager in Uzbekistan. The input is JSON produced by a "
    "maternal and newborn monitoring platform and contains ONLY de-identified aggregate counts and rates for a public "
    "geographic area - there is no patient-level information and you must not ask for any. The value \"<5\" is a "
    "suppressed small number: describe it only as 'kam sonli' and never guess it. The value \"masked\" is a number withheld "
    "for privacy (complementary suppression): do not mention, estimate or derive it. Zones: red = urgent, yellow = needs "
    "review. 'overdue' alerts are unanswered past their deadline (30 minutes for red). 'official' items are published "
    "national statistics with a status relative to the national level.\n"
    "Write 3-5 plain sentences in Uzbek (Latin script, with oʻ and gʻ) saying what needs attention now and why, most "
    "urgent first. Use only numbers that appear in the input. No diagnosis, treatment or patient-specific advice. "
    "No headings, lists or markdown.")

BRIEF_POLICY = {
    "feature": BRIEF_FEATURE, "k_anonymity": K_ANON,
    "sent": ["Hudud nomi (ommaviy geografiya: respublika, viloyat yoki tuman)",
             "Zonalar boʻyicha homiladorlar soni (qizil / sariq / yashil / kulrang) va chaqaloqlar soni",
             "Ochiq va muddati oʻtgan signallar soni",
             "Signallarga median javob vaqti (daqiqa) va javob berilgan signallar soni",
             "Signal sabablari toifalari boʻyicha sonlar (10 ta yopiq toifa)",
             "Rasmiy SIAT koʻrsatkichlari (eʼlon qilingan statistika) va tugʻilishlar soni (ming)"],
    "never_sent": ["F.I.Sh., JSHSHIR, pasport, telefon, manzil, mahalla", "Bemor kodi yoki har qanday yozuv identifikatori",
                   "Tugʻilgan sana va boshqa sanalar", "Alohida bemor koʻrsatkichlari (bosim, puls, tahlillar)",
                   "Erkin matn, ovozli yozuv yoki transkript", "Bemorlar roʻyxati yoki qatorlari",
                   "5 dan kichik har qanday son (\"<5\" koʻrinishida yuboriladi)"],
    "enforcement": ["Yopiq sxema (pydantic, extra=forbid): sxemada yoʻq maydon yuborilmaydi",
                    "k-anonimlik: 5 dan kichik katak \"<5\" ga almashtiriladi; 5 dan kam hodisaga asoslangan koeffitsiyent yuborilmaydi",
                    "Ikkilamchi bostirish: taqsimotda bitta katak \"<5\" boʻlsa, uni jamidan ayirib topib boʻlmasligi uchun "
                    "yana bir katak \"masked\" qilinadi (zonalar: jami = qizil+sariq+yashil+kulrang; signallar: ochiq = onalar+chaqaloqlar)",
                    "Yuborishdan oldin mustaqil sonli tekshiruv (small-cell guard)",
                    "Mavjud detektorlar (ism, telefon, sana, identifikator, klinik matn) — topilsa soʻrov bloklanadi",
                    "Hudud nomi faqat rasmiy hududlar roʻyxatidan",
                    "Har bir qaror privacy_events jadvaliga yoziladi (faqat metamaʼlumot, matn saqlanmaydi)",
                    "CLAUDE_EXTERNAL_PATIENT_DATA=true boʻlsa tizim ishga tushmaydi"],
    "processing": {"patient_data": "Faqat mahalliy maʼlumotlar bazasida (Oʻzbekiston hududida) saqlanadi va qayta ishlanadi",
                   "external_model": "Anthropic Claude API — qayta ishlash Oʻzbekistondan tashqarida; shu sababli faqat "
                                     "k-anonim agregatlar yuboriladi, bemor darajasidagi maʼlumot texnik jihatdan bloklangan",
                   "fallback": "Kalit boʻlmasa yoki 6 soniyada javob kelmasa — mahalliy (deterministik) matn, tashqariga hech narsa yuborilmaydi"},
}


def gateway_decisions(db: Session, limit: int = 20) -> list[dict]:
    """Metadata only - prompts and answers are never stored."""
    rows = db.execute(select(PrivacyEvent).where(PrivacyEvent.event_type.like("llm_%"))
                      .order_by(PrivacyEvent.at.desc()).limit(limit)).scalars()
    return [{"at": e.at.isoformat().replace("+00:00", "Z") if e.at else None, "feature": e.feature, "decision": e.decision,
             "reasons": e.reasons or [], "model": e.model, "status": e.status, "duration_ms": e.duration_ms,
             "input_tokens": e.input_tokens, "output_tokens": e.output_tokens, "request_id": e.request_id} for e in rows]


class BriefGateway:
    """Aggregate-only path used by the live layer. Never raises for privacy reasons: it blocks and answers locally."""

    def __init__(self, provider: ClaudeProvider | None = None, event_sink=None):
        self.s = get_settings()
        self._provider = provider
        self._sink = event_sink  # callable(PrivacyEvent) - the live layer serialises SQLite writes

    def _event(self, user: User | None, decision: str, reasons: list[str], request_id: str, **kw) -> None:
        ev = PrivacyEvent(user_id=getattr(user, "id", None), event_type=f"llm_{decision}", feature=BRIEF_FEATURE,
                          decision=decision, reasons=reasons, request_id=request_id, **kw)
        try:
            if self._sink is not None:
                self._sink(ev)
        except Exception:  # noqa: BLE001
            log.warning("privacy event could not be stored request_id=%s", request_id)
        log.info("llm request_id=%s feature=%s decision=%s reasons=%s status=%s", request_id, BRIEF_FEATURE, decision,
                 ",".join(reasons), kw.get("status"))

    def check(self, payload: AggregateBriefPayload, known_areas: frozenset[str] | set[str]) -> tuple[list[str], dict, str]:
        """Returns (blocking reasons, wire dict, wire text). Any exception => fail closed at the caller."""
        wire = payload.model_dump(mode="json", exclude_none=True)
        reasons: list[str] = []
        if small_cell_violations(wire):
            reasons.append("small_cell")
        if payload.area_name not in known_areas:
            reasons.append("unknown_area_name")
        # the detectors see everything except the whitelisted public place name
        reasons += screen_text(json.dumps({k: v for k, v in wire.items() if k != "area_name"}, ensure_ascii=False))
        return sorted(set(reasons)), wire, json.dumps(wire, ensure_ascii=False)

    def brief(self, user: User | None, raw: dict, known_areas: frozenset[str] | set[str]) -> dict:
        request_id = uuid.uuid4().hex[:16]
        out = {"text": "", "source": "local", "sent_fields": [], "blocked": False, "request_id": request_id,
               "model": None, "reasons": []}
        try:
            payload = build_brief_payload(raw)
        except Exception:  # noqa: BLE001 - schema violation: nothing leaves, generic local answer
            self._event(user, "blocked", ["schema_violation"], request_id)
            return out | {"text": "Qisqa tahlil uchun maʼlumot yetarli emas. Koʻrsatkichlarni xaritadan koʻring.",
                          "blocked": True, "reasons": ["schema_violation"]}
        out["text"] = local_brief(payload)
        try:
            reasons, wire, text = self.check(payload, known_areas)
        except Exception:  # noqa: BLE001
            reasons, wire, text = ["detector_error_fail_closed"], {}, ""
        if not self.s.CLAUDE_ALLOW_KANON_AGGREGATES or self.s.CLAUDE_EXTERNAL_PATIENT_DATA:
            reasons = sorted(set(reasons + ["aggregates_disabled"]))
        if reasons:
            self._event(user, "blocked", reasons, request_id)
            return out | {"blocked": True, "reasons": reasons}
        if not self.s.CLAUDE_ENABLED or (self._provider is None and not self.s.CLAUDE_API_KEY):
            self._event(user, "local", ["no_api_key" if self.s.CLAUDE_ENABLED else "assistant_disabled"], request_id,
                        status="not_sent")
            return out | {"reasons": ["no_api_key" if self.s.CLAUDE_ENABLED else "assistant_disabled"]}
        provider = self._provider or ClaudeProvider()
        timeout = float(self.s.CLAUDE_BRIEF_TIMEOUT_SECONDS)
        pool = ThreadPoolExecutor(max_workers=1)
        try:  # hard wall-clock cap on top of the SDK timeout
            res = pool.submit(provider.complete, _BRIEF_SYSTEM, text, 1500, timeout, 0).result(timeout=timeout + 0.5)
        except Exception:  # noqa: BLE001 - timeout or unexpected provider failure
            res = None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        status = res.status if res is not None else "timeout"
        answer = (res.text or "").strip() if res is not None else ""
        if status == "ok" and not answer:
            status = "empty"
        self._event(user, "allowed", [], request_id, provider=getattr(provider, "name", None),
                    model=(res.model if res is not None else provider.model), status=status,
                    input_tokens=res.input_tokens if res is not None else None,
                    output_tokens=res.output_tokens if res is not None else None,
                    duration_ms=res.duration_ms if res is not None else int(timeout * 1000))
        fields = sent_fields(wire)
        if status != "ok":
            return out | {"sent_fields": fields, "reasons": [f"fallback_{status}"], "model": provider.model}
        return out | {"text": answer, "source": "claude-haiku" if "haiku" in provider.model else "claude",
                      "sent_fields": fields, "model": res.model}

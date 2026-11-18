from __future__ import annotations

import csv
import difflib
import logging
import re
import threading
from collections import defaultdict
from pathlib import Path

from app.core.config import PROJECT_DIR

log = logging.getLogger("perinatal.official")

CSV_PATH = PROJECT_DIR / "data" / "processed" / "ml" / "siat_district_perinatal.csv"
SOURCE = "SIAT / Milliy statistika qoʻmitasi"
POOL_YEARS = 3
MIN_BIRTHS = 1000
MIN_EXPECTED = 5.0
RED_RATIO, YELLOW_RATIO = 1.25, 1.10
METHOD = (f"Koʻrsatkich = hodisalar / tirik tugʻilganlar. Rang {POOL_YEARS} yillik jamlangan maʼlumot boʻyicha "
          f"respublika darajasi bilan solishtiriladi: kutilgan hodisalar soni {MIN_EXPECTED:g} dan kam yoki tugʻilishlar "
          f"{MIN_BIRTHS} dan kam boʻlsa — kulrang (baholash uchun yetarli emas); nisbat ≥ {RED_RATIO} va 95 % Puasson "
          f"ishonch oraligʻi 1 dan yuqori boʻlsa — qizil; nisbat ≥ {YELLOW_RATIO} — sariq; aks holda yashil. "
          "Sonlar (tugʻilishlar, aholi) ranglanmaydi. Bitta kam uchraydigan hodisa hech qachon rang bermaydi.")

# code -> (label, unit, kind, numerator SIAT id, denominator SIAT id, multiplier)
INDICATORS: dict[str, dict] = {
    "infant_mortality_rate": dict(label="Goʻdaklar oʻlimi (1000 tirik tugʻilishga)", unit="‰", kind="rate", num=616, den=223, mult=1000),
    "under5_mortality_rate": dict(label="5 yoshgacha bolalar oʻlimi (1000 tirik tugʻilishga)", unit="‰", kind="rate", num=503, den=223, mult=1000),
    "maternal_mortality_ratio": dict(label="Onalar oʻlimi (100 000 tirik tugʻilishga)", unit="100 ming tugʻilishga", kind="rate", num=663, den=223, mult=100000),
    "births_total": dict(label="Tugʻilganlar soni", unit="nafar", kind="count", num=223),
    "infant_deaths": dict(label="Goʻdaklar oʻlimi soni", unit="nafar", kind="count", num=616),
    "under5_deaths": dict(label="5 yoshgacha oʻlgan bolalar soni", unit="nafar", kind="count", num=503),
    "maternal_deaths": dict(label="Onalar oʻlimi soni", unit="nafar", kind="count", num=663),
    "early_births": dict(label="Erta tugʻruqlar soni (SIAT 2.01.01.0034)", unit="nafar", kind="count", num=576),
    "population_thousand": dict(label="Doimiy aholi soni", unit="ming kishi", kind="count", num=246),
    "doctors_per_10000": dict(label="Shifokorlar (10 000 aholiga)", unit="10 ming aholiga", kind="ratio", num=2846, den=246, mult=10),
}
DEFAULT_INDICATOR = "infant_mortality_rate"
_IDS = {v["num"] for v in INDICATORS.values()} | {v["den"] for v in INDICATORS.values() if "den" in v}

# ------------------------------------------------------------------ name normalisation / matching
_APOS = re.compile(r"[ʻʼ'‘’`´]")
_KIND_CITY = re.compile(r"(\bshahri\b|shahri$|\bshahar\b|\bsh\.?$|\bcity\b|city$|\bг\.)", re.I)
_SUFFIX = re.compile(r"(\btumani\b|\btuman\b|\bshahri\b|shahri$|\bshahar\b|\bsh\.?$|\bdistrict\b|\bcity\b|city$|\brayon\b|район|города?|\bг\.)", re.I)
_CYR = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
                      "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
                      "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "",
                      "э": "e", "ю": "yu", "я": "ya", "ў": "o", "қ": "k", "ғ": "g", "ҳ": "h"})
# spelling variants that survive normalisation (ours -> SIAT), checked by hand
_ALIAS = {"sergeli": "sirgali", "boz": "boston"}  # Boʻz tumani was renamed Boʻston


def norm_name(name: str) -> tuple[str, str]:
    """-> (folded key, kind 'city'|'district'). Folds Uzbek/Russian/English spelling differences."""
    s = (name or "").strip().lower()
    kind = "city" if _KIND_CITY.search(s) else "district"
    s = _SUFFIX.sub(" ", _APOS.sub("", s)).translate(_CYR)
    s = (s.replace("kh", "h").replace("x", "h").replace("q", "k").replace("dj", "j").replace("dzh", "j")
         .replace("yu", "u").replace("yo", "o").replace("ya", "a"))
    s = re.sub(r"[^a-z]", "", s)
    s = re.sub(r"(.)\1+", r"\1", s)  # doubled letters
    return _ALIAS.get(s, s), kind


def _region_pool(soato: str) -> str:
    # our geoBoundaries layer files several Tashkent-city districts under Tashkent region: match them as one pool
    return "1726+1727" if soato in ("1726", "1727") else soato


def match_districts(ours: dict[str, dict], siat: dict[str, dict]) -> tuple[dict[str, str], list[dict], list[dict]]:
    """ours: code -> {name, region_soato}; siat: code -> {name_uz, name_en, region}. Returns (our_code -> siat_code,
    unmatched_ours, unmatched_siat). Exact normalised name first, then fuzzy (>= 0.78) inside the same region and kind."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    s_keys = {c: ({norm_name(s["name_uz"])[0], norm_name(s["name_en"])[0]}, norm_name(s["name_uz"])[1], _region_pool(s["region"]))
              for c, s in siat.items()}
    o_keys = {c: (*norm_name(o["name"]), _region_pool(o["region_soato"])) for c, o in ours.items()}
    for same_region in (True, False):  # second pass: exact name anywhere (boundary files disagree on the region)
        for oc, (ok, okind, oreg) in sorted(o_keys.items()):
            if oc in mapping:
                continue
            hits = [sc for sc, (keys, skind, sreg) in s_keys.items()
                    if sc not in used and ok in keys and skind == okind and (sreg == oreg) == same_region]
            if len(hits) == 1:
                mapping[oc] = hits[0]
                used.add(hits[0])
    cands = []
    for oc, (ok, okind, oreg) in o_keys.items():
        if oc in mapping:
            continue
        for sc, (keys, skind, sreg) in s_keys.items():
            if sc in used or sreg != oreg or skind != okind:
                continue
            score = max(difflib.SequenceMatcher(None, ok, k).ratio() for k in keys)
            if score >= 0.78:
                cands.append((score, oc, sc))
    for score, oc, sc in sorted(cands, reverse=True):
        if oc not in mapping and sc not in used:
            mapping[oc] = sc
            used.add(sc)
    un_ours = [{"code": c, "name": ours[c]["name"], "region_soato": ours[c]["region_soato"]} for c in sorted(ours) if c not in mapping]
    un_siat = [{"siat_code": c, "name": siat[c]["name_uz"], "region_soato": siat[c]["region"]} for c in sorted(siat) if c not in used]
    return mapping, un_ours, un_siat


# ------------------------------------------------------------------ statistics
def poisson_ci(k: float, conf: float = 0.95) -> tuple[float, float]:
    """Exact (Garwood) interval for a Poisson count."""
    from scipy.stats import chi2
    a = 1 - conf
    lo = 0.0 if k <= 0 else chi2.ppf(a / 2, 2 * k) / 2
    hi = chi2.ppf(1 - a / 2, 2 * (k + 1)) / 2
    return float(lo), float(hi)


def classify(events: float | None, births: float | None, national_rate: float | None) -> tuple[str, dict]:
    """national_rate = events per birth (pooled). Returns (status, explanation numbers)."""
    if events is None or births is None or national_rate is None or births <= 0:
        return "grey", {"reason": "no_data"}
    expected = national_rate * births
    info = {"events_pooled": int(events), "births_pooled": int(births), "expected": round(expected, 1)}
    if births < MIN_BIRTHS or expected < MIN_EXPECTED:
        return "grey", info | {"reason": "small_numbers"}
    ratio = events / expected
    lo, hi = poisson_ci(events)
    info |= {"ratio": round(ratio, 2), "ratio_ci95": [round(lo / expected, 2), round(hi / expected, 2)]}
    if ratio >= RED_RATIO and lo / expected > 1.0:
        return "red", info
    if ratio >= YELLOW_RATIO:
        return "yellow", info
    return "green", info


class OfficialStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.loaded = False
        self.values: dict[tuple[int, str], dict[int, float]] = {}  # (siat_id, area code) -> {year: value}
        self.siat_districts: dict[str, dict] = {}
        self.mapping: dict[str, str] = {}       # our district code -> SIAT 7-digit code
        self.unmatched_ours: list[dict] = []
        self.unmatched_siat: list[dict] = []
        self.ours: dict[str, dict] = {}
        self._cache: dict[tuple[str, int], dict] = {}

    # ---- loading
    def load(self, districts: dict[str, dict], force: bool = False) -> "OfficialStats":
        """districts: live_engine.geo.districts (code -> {code, name, region_soato})."""
        if self.loaded and not force:
            return self
        with self._lock:
            if self.loaded and not force:
                return self
            values: dict[tuple[int, str], dict[int, float]] = defaultdict(dict)
            siat: dict[str, dict] = {}
            if CSV_PATH.exists():
                with CSV_PATH.open(encoding="utf-8", newline="") as f:
                    for r in csv.DictReader(f):
                        try:
                            sid, year, val = int(r["siat_id"]), int(r["year"]), float(r["value"])
                        except (TypeError, ValueError):
                            continue
                        if r["level"] == "district":
                            siat.setdefault(r["code"], {"name_uz": r["name_uz"], "name_en": r["name_en"], "region": r["region_code"]})
                        if sid in _IDS:
                            values[(sid, r["code"])][year] = val
            else:
                log.warning("official district statistics file is missing: %s", CSV_PATH.name)
            self.values, self.siat_districts, self.ours = dict(values), siat, dict(districts)
            self.mapping, self.unmatched_ours, self.unmatched_siat = match_districts(self.ours, siat)
            self._cache.clear()
            self.loaded = True
            log.info("official stats: %d SIAT districts, matched %d of %d map districts, unmatched map=%d siat=%d",
                     len(siat), len(self.mapping), len(self.ours), len(self.unmatched_ours), len(self.unmatched_siat))
            for u in self.unmatched_ours:
                log.info("official stats: no SIAT match for %s (%s, %s)", u["code"], u["name"], u["region_soato"])
        return self

    # ---- primitives
    def _v(self, sid: int, area: str | None, year: int) -> float | None:
        return self.values.get((sid, area), {}).get(year) if area else None

    def _pooled(self, sid: int, area: str | None, year: int) -> float | None:
        vals = [self._v(sid, area, y) for y in range(year - POOL_YEARS + 1, year + 1)]
        return sum(vals) if all(v is not None for v in vals) else None

    def years(self, code: str) -> list[int]:
        ind = INDICATORS[code]
        ys = set(self.values.get((ind["num"], "1700"), {}))
        if "den" in ind:
            ys &= set(self.values.get((ind["den"], "1700"), {}))
        return sorted(ys)

    def value(self, code: str, area: str | None, year: int) -> float | None:
        ind = INDICATORS[code]
        n = self._v(ind["num"], area, year)
        if n is None:
            return None
        if ind["kind"] == "count":
            return round(n, 1) if code == "population_thousand" else int(n)
        d = self._v(ind["den"], area, year)
        return round(n / d * ind["mult"], 2) if d else None

    def events(self, code: str, area: str | None, year: int) -> int | None:
        ind = INDICATORS[code]
        n = self._v(ind["num"], area, year)
        return int(n) if n is not None and ind["kind"] != "ratio" and code != "population_thousand" else None

    def status(self, code: str, area: str | None, year: int) -> tuple[str, dict]:
        ind = INDICATORS[code]
        if ind["kind"] != "rate":
            return "grey", {"reason": "not_rated"}
        nat_e, nat_b = self._pooled(ind["num"], "1700", year), self._pooled(ind["den"], "1700", year)
        return classify(self._pooled(ind["num"], area, year), self._pooled(ind["den"], area, year),
                        nat_e / nat_b if nat_e is not None and nat_b else None)

    # ---- API payloads
    def districts_payload(self, code: str | None, year: int | None) -> dict:
        code = code or DEFAULT_INDICATOR
        if code not in INDICATORS:
            raise KeyError(code)
        years = self.years(code)
        if not years:
            return {"indicator": {"code": code, "label": INDICATORS[code]["label"], "unit": INDICATORS[code]["unit"]},
                    "year": None, "available_indicators": self.available(), "years": [], "districts": [], "matched": 0,
                    "unmatched": len(self.ours), "source": SOURCE, "method": METHOD}
        if year is None:
            year = years[-1]
        if year not in years:
            raise ValueError(year)
        key = (code, year)
        if key not in self._cache:
            ind = INDICATORS[code]
            national = self.value(code, "1700", year)
            rows = []
            for oc in sorted(self.ours):
                o, sc = self.ours[oc], self.mapping.get(oc)
                st, info = self.status(code, sc, year) if sc else ("grey", {"reason": "unmatched"})
                rows.append({"code": oc, "name": o["name"], "region_soato": o["region_soato"], "value": self.value(code, sc, year),
                             "national": national, "status": st, "siat_code": sc, "detail": info})
            self._cache[key] = {"indicator": {"code": code, "label": ind["label"], "unit": ind["unit"], "kind": ind["kind"]},
                                "year": year, "available_indicators": self.available(), "years": years, "districts": rows,
                                "matched": len(self.mapping), "unmatched": len(self.ours) - len(self.mapping),
                                "unmatched_districts": self.unmatched_ours, "pooled_years": POOL_YEARS,
                                "source": SOURCE, "method": METHOD}
        return self._cache[key]

    def available(self) -> list[dict]:
        return [{"code": c, "label": i["label"], "unit": i["unit"], "kind": i["kind"]} for c, i in INDICATORS.items()
                if self.values.get((i["num"], "1700"))]

    def area_block(self, area: str | None, codes: tuple[str, ...] = ("births_total", "infant_deaths", "infant_mortality_rate",
                                                                    "under5_mortality_rate", "maternal_deaths",
                                                                    "maternal_mortality_ratio"), series_years: int = 6) -> dict | None:
        """Latest values + short series for one SIAT area code (district 7-digit, region 4-digit or '1700')."""
        if not area:
            return None
        items = []
        for code in codes:
            ind = INDICATORS[code]
            ys = [y for y in self.years(code) if self.value(code, area, y) is not None]
            if not ys:
                continue
            y = ys[-1]
            st, info = self.status(code, area, y)
            items.append({"code": code, "label": ind["label"], "unit": ind["unit"], "kind": ind["kind"], "year": y,
                          "value": self.value(code, area, y), "national": self.value(code, "1700", y), "status": st,
                          "detail": info, "series": [{"year": k, "value": self.value(code, area, k)} for k in ys[-series_years:]]})
        if not items:
            return None
        return {"siat_code": area, "year": max(i["year"] for i in items), "indicators": items, "source": SOURCE, "method": METHOD}

    def district_block(self, our_code: str) -> dict:
        sc = self.mapping.get(our_code)
        block = self.area_block(sc)
        if block is None:
            return {"matched": False, "siat_code": sc, "indicators": [], "source": SOURCE,
                    "note": "Bu tuman uchun rasmiy statistika bilan moslik topilmadi"}
        s = self.siat_districts.get(sc, {})
        return {"matched": True, "siat_name": s.get("name_uz"), **block}


stats = OfficialStats()

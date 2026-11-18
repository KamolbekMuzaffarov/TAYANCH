from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Region, RegionalIndicator, RegionalRiskScore
from app.services.data_import import mics_direction

METHOD = "stat-signal-v0.1"
APPROVAL = "prototype_pending_statistical_clinical_approval"
NATIONAL = "1700"
SURVEY_STALE_AFTER_YEARS = 5
Z = 1.96

# (indicator, layer, kind) — kind: siat_count (numerator/denominator, Poisson) | survey (MICS CI)
COMPONENTS = [
    ("maternal_mortality_ratio", "maternal_area_risk", "siat_count_pooled3"),
    ("mics_anc_skilled_any", "maternal_area_risk", "survey"),
    ("mics_caesarean_rate", "maternal_area_risk", "context"),
    ("infant_mortality_rate", "newborn_area_risk", "siat_count"),
    ("under5_mortality_rate", "newborn_area_risk", "siat_count"),
    ("preterm_birth_pct", "newborn_area_risk", "siat_count"),
    ("mics_low_birthweight_reported", "newborn_area_risk", "survey"),
    ("mics_stunting_u5", "newborn_area_risk", "survey"),
    ("mics_early_breastfeeding_1h", "newborn_area_risk", "survey"),
    ("mics_anc_4plus", "healthcare_response_risk", "survey"),
    ("mics_anc_content_bp_urine_blood", "healthcare_response_risk", "survey"),
    ("mics_institutional_delivery", "healthcare_response_risk", "survey"),
    ("mics_skilled_birth_attendance", "healthcare_response_risk", "survey"),
    ("mics_weighed_at_birth", "healthcare_response_risk", "survey"),
    ("doctors_per_10000", "healthcare_response_risk", "context"),
    ("maternity_beds_per_10000_women", "healthcare_response_risk", "context"),
]
LAYERS = ["maternal_area_risk", "newborn_area_risk", "healthcare_response_risk"]


def byar_ci(x: float) -> tuple[float, float]:
    """95% CI for a Poisson count (Byar's approximation)."""
    if x <= 0:
        return 0.0, 3.689
    lo = x * (1 - 1 / (9 * x) - Z / (3 * math.sqrt(x))) ** 3
    x1 = x + 1
    hi = x1 * (1 - 1 / (9 * x1) + Z / (3 * math.sqrt(x1))) ** 3
    return lo, hi


@dataclass
class Comp:
    indicator: str
    layer: str
    status: str
    value: float | None = None
    national: float | None = None
    ci: tuple | None = None
    detail: str = ""
    source: str = ""
    period: str = ""

    def as_dict(self) -> dict:
        return {"indicator": self.indicator, "status": self.status, "value": self.value, "national": self.national,
                "ci": list(self.ci) if self.ci else None, "detail": self.detail, "source": self.source, "period": self.period}


def _ind(db: Session, code: str, region: str, year: int | None = None, source: str | None = None) -> RegionalIndicator | None:
    q = select(RegionalIndicator).where(RegionalIndicator.indicator_code == code, RegionalIndicator.region_soato == region,
                                        RegionalIndicator.district_code.is_(None))
    if year is not None:
        q = q.where(func.extract("year", RegionalIndicator.period_end) == year) if db.bind.dialect.name != "sqlite" \
            else q.where(func.strftime("%Y", RegionalIndicator.period_end) == str(year))
    if source:
        q = q.where(RegionalIndicator.source_key == source)
    return db.execute(q.order_by(RegionalIndicator.period_end.desc()).limit(1)).scalar_one_or_none()


def _counts(db: Session, code: str, region: str, years: list[int]) -> tuple[float, float] | None:
    num = den = 0.0
    for y in years:
        r = _ind(db, code, region, y, "siat")
        if r is None or r.numerator is None or not r.denominator:
            return None
        num += r.numerator
        den += r.denominator
    return num, den


def _winsorised_phi(z: list[float]) -> float:
    """Multiplicative over-dispersion factor from 10%-winsorised squared z-scores (min 1)."""
    if len(z) < 5:
        return 1.0
    zs = sorted(z)
    k = max(1, int(round(0.1 * len(zs))))
    lo, hi = zs[k], zs[-k - 1]
    w = [min(max(v, lo), hi) for v in zs]
    return max(1.0, sum(v * v for v in w) / len(w))


_PHI_CACHE: dict = {}


def _phi(db: Session, code: str, years: tuple[int, ...], kind: str) -> float:
    key = (id(db), code, years, kind)
    if key in _PHI_CACHE:
        return _PHI_CACHE[key]
    nat = _counts(db, code, NATIONAL, list(years))
    zs = []
    for reg in db.execute(select(Region.soato)).scalars():
        cur = _counts(db, code, reg, list(years))
        if not cur or not nat or cur[1] <= 0:
            continue
        if kind == "level":
            p0 = nat[0] / nat[1]
            if p0 > 0:
                zs.append((cur[0] / cur[1] - p0) / math.sqrt(p0 / cur[1]))
        else:
            base = _counts(db, code, reg, [years[0] - 3, years[0] - 2, years[0] - 1])
            if base and base[0] >= 5 and cur[0] >= 5:
                zs.append(math.log((cur[0] / cur[1]) / (base[0] / base[1])) / math.sqrt(1 / cur[0] + 1 / base[0]))
    _PHI_CACHE[key] = _winsorised_phi(zs)
    return _PHI_CACHE[key]


def _siat_component(db: Session, code: str, layer: str, region: str, year: int, pooled: bool) -> Comp:
    mult = {"maternal_mortality_ratio": 100000, "preterm_birth_pct": 100}.get(code, 1000)
    years = [year - 2, year - 1, year] if pooled else [year]
    reg, nat = _counts(db, code, region, years), _counts(db, code, NATIONAL, years)
    period = f"{years[0]}-{years[-1]}"
    if reg is None or nat is None:
        return Comp(code, layer, "insufficient", detail="missing numerator/denominator", source="siat", period=period)
    x, d = reg
    p0 = nat[0] / nat[1]
    nat_rate = p0 * mult
    phi = _phi(db, code, tuple(years), "level")
    se = math.sqrt(phi * p0 / d) if p0 > 0 else None
    z = ((x / d) - p0) / se if se else 0.0
    rate = x / d * mult
    # over-dispersed 95% interval around the observed rate (for display)
    half = Z * math.sqrt(phi * max(x, 1)) / d * mult
    ci = (round(max(0.0, rate - half), 2), round(rate + half, 2))
    if z > Z:
        status = "worse_significant"
    elif z < -Z:
        status = "better_significant"
    else:
        status = "not_different"
    base_years = [years[0] - 3, years[0] - 2, years[0] - 1]
    base = _counts(db, code, region, base_years)
    trend = ""
    if base and base[0] >= 5 and x >= 5:
        rr = (x / d) / (base[0] / base[1])
        phi_t = _phi(db, code, tuple(years), "trend")
        zt = math.log(rr) / math.sqrt(phi_t * (1 / x + 1 / base[0]))
        trend = f"; RR vs {base_years[0]}-{base_years[-1]} = {rr:.2f} (z={zt:.2f}, phi={phi_t:.1f})"
        if zt > Z:
            status = "worse_significant+worsening" if status == "worse_significant" else "worsening_trend"
    elif x < 5:
        trend = "; too few events for trend test"
    return Comp(code, layer, status, round(rate, 2), round(nat_rate, 2), ci,
                f"events={int(x)}, denominator={int(d)}, z={z:.2f}, phi={phi:.1f}{trend}", "siat", period)


def _survey_component(db: Session, code: str, layer: str, region: str, year: int, direction: str) -> Comp:
    r, n = _ind(db, code, region, source="mics6"), _ind(db, code, NATIONAL, source="mics6")
    if r is None or n is None or r.rate is None or r.suppression_status == "suppressed":
        return Comp(code, layer, "insufficient", detail="missing or suppressed (n<25)", source="mics6")
    period = f"{r.period_start.year}-{r.period_end.year}"
    if year - r.period_end.year > SURVEY_STALE_AFTER_YEARS:
        return Comp(code, layer, "insufficient", r.rate, n.rate, (r.ci_low, r.ci_high), "stale survey estimate", "mics6", period)
    if direction == "higher_better":
        status = "worse_significant" if r.ci_high < n.rate else ("better_significant" if r.ci_low > n.rate else "not_different")
    else:
        status = "worse_significant" if r.ci_low > n.rate else ("better_significant" if r.ci_high < n.rate else "not_different")
    note = "low precision (n 25-49)" if r.quality_status == "low_precision" else ""
    return Comp(code, layer, status, r.rate, n.rate, (r.ci_low, r.ci_high), f"n={r.n_unweighted} {note}".strip(), "mics6", period)


def _context_component(db: Session, code: str, layer: str, region: str, year: int) -> Comp:
    r = _ind(db, code, region)
    n = _ind(db, code, NATIONAL)
    if r is None:
        return Comp(code, layer, "context", detail="not available")
    return Comp(code, layer, "context", r.rate, n.rate if n else None, None,
                "context only - no validated threshold, not used for colour", r.source_key,
                f"{r.period_start.year}-{r.period_end.year}")


def layer_status(comps: list[Comp]) -> str:
    scored = [c for c in comps if c.status != "context"]
    usable = [c for c in scored if c.status != "insufficient"]
    if not scored or len(usable) * 2 < len(scored):
        return "grey"
    worse = [c for c in usable if c.status.startswith("worse_significant")]
    if len(worse) >= 2 or any(c.status == "worse_significant+worsening" for c in usable):
        return "red"
    if worse or any(c.status == "worsening_trend" for c in usable):
        return "yellow"
    return "green"


def overall(statuses: list[str]) -> str:
    for s in ("red", "yellow", "grey"):
        if s in statuses:
            return s
    return "green"


def latest_siat_year(db: Session) -> int:
    r = _ind(db, "infant_mortality_rate", NATIONAL, source="siat")
    return r.period_end.year if r else 2025


def compute_region(db: Session, region: str, year: int, directions: dict[str, str] | None = None) -> dict:
    directions = directions or mics_direction()
    comps: list[Comp] = []
    for code, layer, kind in COMPONENTS:
        if kind.startswith("siat_count"):
            comps.append(_siat_component(db, code, layer, region, year, pooled=kind.endswith("pooled3")))
        elif kind == "survey":
            comps.append(_survey_component(db, code, layer, region, year, directions.get(code, "higher_better")))
        else:
            comps.append(_context_component(db, code, layer, region, year))
    layers = {L: layer_status([c for c in comps if c.layer == L]) for L in LAYERS}
    layers["overall_area_status"] = overall(list(layers.values()))
    return {"region_soato": region, "year": year, "layers": layers, "components": [c.as_dict() | {"layer": c.layer} for c in comps]}


def recompute_all(db: Session, year: int | None = None) -> int:
    _PHI_CACHE.clear()
    year = year or latest_siat_year(db)
    directions = mics_direction()
    db.execute(delete(RegionalRiskScore).where(RegionalRiskScore.period_year == year, RegionalRiskScore.methodology_version == METHOD))
    n = 0
    for reg in db.execute(select(Region.soato)).scalars():
        res = compute_region(db, reg, year, directions)
        for layer, status in res["layers"].items():
            comps = [c for c in res["components"] if layer == "overall_area_status" or c["layer"] == layer]
            db.add(RegionalRiskScore(region_soato=reg, layer=layer, period_year=year, status=status,
                                     components=comps if layer != "overall_area_status" else [],
                                     methodology_version=METHOD, approval_status=APPROVAL))
            n += 1
    db.commit()
    return n


def stale_after_years() -> int:
    return get_settings().REGION_STALE_AFTER_YEARS

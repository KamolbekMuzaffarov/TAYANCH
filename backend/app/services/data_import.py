from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import utcnow
from app.models import DataImportRun, DataSource, District, Region, RegionalIndicator

RATE_PAIRS = {  # rate indicator -> (numerator count indicator, multiplier)
    "infant_mortality_rate": ("infant_deaths", 1000),
    "under5_mortality_rate": ("under5_deaths", 1000),
    "maternal_mortality_ratio": ("maternal_deaths", 100000),
}
LAYER_OF = {
    "infant_mortality_rate": "newborn", "under5_mortality_rate": "newborn", "maternal_mortality_ratio": "maternal",
    "preterm_birth_pct": "newborn",
}


def _dir() -> Path:
    return Path(get_settings().DATA_DIR)


def load_sources(db: Session) -> int:
    meta = json.loads((_dir() / "sources.json").read_text(encoding="utf-8"))
    n = 0
    for s in meta["sources"]:
        row = db.execute(select(DataSource).where(DataSource.key == s["key"])).scalar_one_or_none() or DataSource(key=s["key"])
        row.name, row.kind, row.url = s["name"], s["kind"], s.get("url")
        row.license, row.citation, row.notes = s.get("license"), s.get("citation"), s.get("note")
        db.add(row)
        n += 1
    if not db.execute(select(DataSource).where(DataSource.key == "synthetic_registry")).scalar_one_or_none():
        db.add(DataSource(key="synthetic_registry", name="Sintetik demo registr (DEMO)", kind="synthetic", is_synthetic=True,
                          notes="Generated test patients. Not real Uzbekistan data."))
    db.commit()
    return n


def load_geography(db: Session) -> tuple[int, int]:
    regions = json.loads((_dir() / "regions.geojson").read_text(encoding="utf-8"))
    for f in regions["features"]:
        p = f["properties"]
        r = db.get(Region, p["soato"]) or Region(soato=p["soato"])
        r.iso_code, r.name_uz, r.name_uzc, r.name_ru, r.name_en = p["iso"], p["name_uz"], p["name_uzc"], p["name_ru"], p["name_en"]
        r.geometry, r.geometry_source = f["geometry"], "geoBoundaries gbOpen ADM1 (ODbL) - not an official boundary"
        db.add(r)
    db.flush()
    districts = json.loads((_dir() / "districts.geojson").read_text(encoding="utf-8"))
    for f in districts["features"]:
        p = f["properties"]
        d = db.get(District, p["district_code"]) or District(code=p["district_code"])
        d.region_soato, d.name, d.code_source, d.is_official_code = p["region_soato"], p["name"], p["code_source"], False
        d.centroid_lon, d.centroid_lat = p["centroid"]
        d.geometry, d.previous_codes = f["geometry"], [p["geoboundaries_id"]]
        db.add(d)
    db.commit()
    return len(regions["features"]), len(districts["features"])


def _run(db: Session, key: str, user_id: str | None) -> DataImportRun:
    run = DataImportRun(source_key=key, status="running", started_by=user_id, issues=[])
    db.add(run)
    db.flush()
    return run


def load_siat(db: Session, user_id: str | None = None) -> DataImportRun:
    run = _run(db, "siat", user_id)
    sources = json.loads((_dir() / "sources.json").read_text(encoding="utf-8"))
    siat_meta = next(s for s in sources["sources"] if s["key"] == "siat")["indicators"]
    valid_regions = {r.soato for r in db.execute(select(Region)).scalars()} | {"1700"}
    rows = list(csv.DictReader((_dir() / "siat_indicators.csv").open(encoding="utf-8")))
    run.rows_read = len(rows)
    db.execute(delete(RegionalIndicator).where(RegionalIndicator.source_key == "siat"))
    by_key: dict[tuple, float] = {}
    issues = []
    loaded = 0
    for r in rows:
        if r["region_soato"] not in valid_regions:
            issues.append({"type": "invalid_region_code", "code": r["region_soato"]})
            continue
        try:
            v = float(r["value"])
            y = int(r["year"])
        except ValueError:
            issues.append({"type": "invalid_value", "indicator": r["indicator_code"]})
            continue
        if v < 0:
            issues.append({"type": "negative_value", "indicator": r["indicator_code"], "region": r["region_soato"], "year": y})
            continue
        by_key[(r["indicator_code"], r["region_soato"], y)] = v
    for (code, reg, y), v in by_key.items():
        num = den = None
        quality = "ok"
        if code in RATE_PAIRS:
            cnt, mult = RATE_PAIRS[code]
            num, den = by_key.get((cnt, reg, y)), by_key.get(("births_total", reg, y))
            if num is not None and den and v > 0 and abs(num / den * mult - v) / v > 0.10:
                quality = "check"  # published rate disagrees with SIAT count series by >10%
                issues.append({"type": "rate_count_mismatch", "indicator": code, "region": reg, "year": y,
                               "published": v, "recomputed": round(num / den * mult, 2)})
        m = siat_meta.get(code, {})
        upd = m.get("updated")
        db.add(RegionalIndicator(
            indicator_code=code, layer=LAYER_OF.get(code, _layer_from_csv(rows, code)), region_soato=reg,
            period_start=date(y, 1, 1), period_end=date(y, 12, 31), numerator=num, denominator=den, rate=v,
            unit=_unit_from_csv(rows, code), source_key="siat", source_version=m.get("siat_code"),
            source_updated_at=datetime.fromisoformat(upd) if upd else None, quality_status=quality,
            methodology=(f"SIAT {m.get('siat_code')} {m.get('name_en', '')}".strip()
                         + ("; rate = published SIAT value, numerator/denominator = SIAT count series (zones use counts)"
                            if code in RATE_PAIRS else ""))))
        loaded += 1
    # derived: preterm births per 100 live births
    for (code, reg, y), v in list(by_key.items()):
        if code == "preterm_births" and ("births_total", reg, y) in by_key:
            births = by_key[("births_total", reg, y)]
            if births > 0:
                db.add(RegionalIndicator(indicator_code="preterm_birth_pct", layer="newborn", region_soato=reg,
                                         period_start=date(y, 1, 1), period_end=date(y, 12, 31), numerator=v, denominator=births,
                                         rate=round(100 * v / births, 3), unit="percent", source_key="siat",
                                         source_version="derived:2.01.01.0034/2.01.01.0001", quality_status="ok",
                                         methodology="Derived: SIAT premature births / SIAT number of births x 100"))
                loaded += 1
    rejected = sum(1 for i in issues if i["type"] != "rate_count_mismatch")
    run.rows_loaded, run.rows_rejected, run.issues = loaded, rejected, issues[:200]
    run.status, run.finished_at = "success", utcnow()
    db.commit()
    return run


_csv_cache: dict = {}


def _layer_from_csv(rows, code):
    if code not in _csv_cache:
        r = next(x for x in rows if x["indicator_code"] == code)
        _csv_cache[code] = (r["layer"], r["unit"])
    return _csv_cache[code][0]


def _unit_from_csv(rows, code):
    _layer_from_csv(rows, code)
    return _csv_cache[code][1]


def load_mics(db: Session, user_id: str | None = None) -> DataImportRun:
    run = _run(db, "mics6", user_id)
    rows = list(csv.DictReader((_dir() / "mics6_region_indicators.csv").open(encoding="utf-8")))
    run.rows_read = len(rows)
    db.execute(delete(RegionalIndicator).where(RegionalIndicator.source_key == "mics6"))
    loaded, issues = 0, []
    for r in rows:
        if not r["value"]:
            issues.append({"type": "empty_estimate", "indicator": r["indicator_code"], "region": r["region_soato"]})
            continue
        n = int(r["n_unweighted"])
        sup = r["suppression_status"]
        db.add(RegionalIndicator(
            indicator_code=r["indicator_code"], layer=r["layer"], region_soato=r["region_soato"],
            period_start=date.fromisoformat(r["period_start"]), period_end=date.fromisoformat(r["period_end"]),
            numerator=float(r["numerator_weighted"]), denominator=float(r["weighted_n"]),
            rate=None if sup == "suppressed" else float(r["value"]), unit="percent",
            ci_low=None if sup == "suppressed" else float(r["ci_low"]), ci_high=None if sup == "suppressed" else float(r["ci_high"]),
            n_unweighted=n, source_key="mics6", source_version="UZB_2021_MICS_v01_M",
            quality_status="low_precision" if sup == "low_precision" else "ok", suppression_status=sup,
            methodology=f"{r['description']}. Weighted; Taylor-linearised SE (stratum/PSU); direction={r['direction']}"))
        loaded += 1
    run.rows_loaded, run.rows_rejected, run.issues = loaded, len(issues), issues
    run.status, run.finished_at = "success", utcnow()
    db.commit()
    return run


def load_all(db: Session, user_id: str | None = None) -> dict:
    load_sources(db)
    nreg, ndist = load_geography(db)
    s = load_siat(db, user_id)
    m = load_mics(db, user_id)
    return {"regions": nreg, "districts": ndist, "siat_rows": s.rows_loaded, "siat_rejected": s.rows_rejected,
            "mics_rows": m.rows_loaded}


def mics_direction() -> dict[str, str]:
    rows = csv.DictReader((_dir() / "mics6_region_indicators.csv").open(encoding="utf-8"))
    return {r["indicator_code"]: r["direction"] for r in rows}

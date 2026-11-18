from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import current_user
from app.core.errors import not_found
from app.core.permissions import assert_region, can_see_region, require, scope_of
from app.db import get_db
from app.models import (Alert, DataSource, District, Facility, Newborn, Pregnancy, Region, RegionalIndicator,
                        RegionalRiskScore, User)
from app.services import regional

router = APIRouter(tags=["geography"])
NATIONAL = "1700"
LAYER_KEYS = ["maternal_area_risk", "newborn_area_risk", "healthcare_response_risk", "overall_area_status"]
STATUS_LABEL = {"red": "Qizil — jiddiy signal", "yellow": "Sariq — kuzatuv talab", "green": "Yashil — barqaror",
                "grey": "Kulrang — ma’lumot yetarli emas"}


def _region_out(r: Region, with_geometry: bool = False) -> dict:
    d = {"soato": r.soato, "iso_code": r.iso_code, "name_uz": r.name_uz, "name_uzc": r.name_uzc, "name_ru": r.name_ru,
         "name_en": r.name_en}
    if with_geometry:
        d["geometry"] = r.geometry
    return d


@router.get("/regions")
def list_regions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "aggregate:read")
    return [_region_out(r) for r in db.execute(select(Region).order_by(Region.soato)).scalars() if can_see_region(user, r.soato)]


@router.get("/regions/{soato}/districts")
def list_districts(soato: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "aggregate:read")
    assert_region(user, soato)
    rows = db.execute(select(District).where(District.region_soato == soato).order_by(District.name)).scalars()
    return [{"code": d.code, "name": d.name, "region_soato": d.region_soato, "is_official_code": d.is_official_code,
             "code_source": d.code_source, "centroid": [d.centroid_lon, d.centroid_lat]} for d in rows]


@router.get("/regions/map-risk")
def map_risk(year: int | None = None, layer: str = Query("overall_area_status", pattern="|".join(LAYER_KEYS)),
             include_geometry: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """GeoJSON FeatureCollection with the selected area layer. Separate from individual patient risk."""
    require(user, "aggregate:read")
    year = year or regional.latest_siat_year(db)
    scores = {(s.region_soato, s.layer): s for s in db.execute(
        select(RegionalRiskScore).where(RegionalRiskScore.period_year == year)).scalars()}
    feats = []
    for r in db.execute(select(Region).order_by(Region.soato)).scalars():
        if not can_see_region(user, r.soato):
            continue
        sc = scores.get((r.soato, layer))
        status = sc.status if sc else "grey"
        feats.append({"type": "Feature", "geometry": r.geometry if include_geometry else None, "properties": {
            **_region_out(r), "layer": layer, "status": status, "status_label": STATUS_LABEL[status],
            "layers": {k: (scores[(r.soato, k)].status if (r.soato, k) in scores else "grey") for k in LAYER_KEYS},
            "methodology_version": sc.methodology_version if sc else None,
            "approval_status": sc.approval_status if sc else None}})
    sources = [{"key": s.key, "name": s.name, "kind": s.kind, "license": s.license}
               for s in db.execute(select(DataSource).where(DataSource.kind.in_(["official_statistics", "household_survey", "geography"]))).scalars()]
    return {"type": "FeatureCollection", "year": year, "layer": layer, "features": feats,
            "legend": STATUS_LABEL, "sources": sources,
            "methodology": {"version": regional.METHOD, "approval_status": regional.APPROVAL,
                            "summary": regional.__doc__.strip().splitlines()[0]},
            "note": "Hududiy holat individual bemor holatidan mustaqil. Yashil hududda ham qizil bemor bo‘lishi mumkin."}


@router.get("/regions/{soato}/overview")
def region_overview(soato: str, year: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "aggregate:read")
    assert_region(user, soato)
    r = db.get(Region, soato)
    if not r:
        raise not_found("region")
    year = year or regional.latest_siat_year(db)
    result = regional.compute_region(db, soato, year)
    return {"region": _region_out(r), **result,
            "methodology": {"version": regional.METHOD, "approval_status": regional.APPROVAL}}


@router.get("/districts/{code}/overview")
def district_overview(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "aggregate:read")
    d = db.get(District, code)
    if not d:
        raise not_found("district")
    assert_region(user, d.region_soato)
    return {"district": {"code": d.code, "name": d.name, "region_soato": d.region_soato, "is_official_code": d.is_official_code},
            "official_statistics": [], "status": "grey", "status_label": STATUS_LABEL["grey"],
            "reason": "Rasmiy tuman kesimidagi perinatal indikatorlar hali ulanmagan (SIAT faqat viloyat kesimida). "
                      "Sun’iy qiymat qo‘yilmaydi."}


@router.get("/indicators")
def indicators(region_soato: str = NATIONAL, code: str | None = None, source: str | None = None,
               year_from: int | None = None, year_to: int | None = None, limit: int = Query(500, le=2000),
               offset: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Time series with full provenance (numerator, denominator, CI, period, source, quality, suppression)."""
    require(user, "aggregate:read")
    if region_soato != NATIONAL:
        assert_region(user, region_soato)
    q = select(RegionalIndicator).where(RegionalIndicator.region_soato == region_soato, RegionalIndicator.district_code.is_(None))
    if code:
        q = q.where(RegionalIndicator.indicator_code == code)
    if source:
        q = q.where(RegionalIndicator.source_key == source)
    rows = [r for r in db.execute(q.order_by(RegionalIndicator.indicator_code, RegionalIndicator.period_end)).scalars()
            if (year_from is None or r.period_end.year >= year_from) and (year_to is None or r.period_end.year <= year_to)]
    out = [{"indicator_code": r.indicator_code, "layer": r.layer, "period_start": r.period_start, "period_end": r.period_end,
            "numerator": r.numerator, "denominator": r.denominator,
            "rate": r.rate if r.suppression_status != "suppressed" else None, "unit": r.unit, "ci_low": r.ci_low,
            "ci_high": r.ci_high, "n_unweighted": r.n_unweighted, "source": r.source_key, "source_version": r.source_version,
            "source_updated_at": r.source_updated_at, "quality_status": r.quality_status,
            "suppression_status": r.suppression_status, "methodology": r.methodology, "is_synthetic": r.is_synthetic}
           for r in rows]
    return {"items": out[offset:offset + limit], "total": len(out), "limit": limit, "offset": offset}


@router.get("/facilities")
def list_facilities(region_soato: str | None = None, facility_type: str | None = None, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    require(user, "aggregate:read") if user.role.value not in ("lab_operator",) else None
    q = select(Facility)
    if region_soato:
        q = q.where(Facility.region_soato == region_soato)
    if facility_type:
        q = q.where(Facility.facility_type == facility_type)
    s = scope_of(user)
    return [{"id": f.id, "code": f.code, "name": f.name, "facility_type": f.facility_type, "level": f.level,
             "region_soato": f.region_soato, "district_code": f.district_code, "lat": f.lat, "lon": f.lon,
             "is_synthetic": f.is_synthetic}
            for f in db.execute(q.order_by(Facility.region_soato, Facility.name)).scalars()
            if s.national or f.region_soato in s.regions or f.id in s.facilities or user.role.value in ("obgyn", "neonatologist")]


@router.get("/dashboard/national")
def national_dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """KPI tiles. Official statistics and the (synthetic/demo) clinical registry are reported separately."""
    require(user, "aggregate:read")
    year = regional.latest_siat_year(db)
    def nat(code):
        r = db.execute(select(RegionalIndicator).where(RegionalIndicator.indicator_code == code, RegionalIndicator.region_soato == NATIONAL,
                                                       RegionalIndicator.source_key == "siat")
                       .order_by(RegionalIndicator.period_end.desc()).limit(1)).scalar_one_or_none()
        return {"value": r.rate, "unit": r.unit, "year": r.period_end.year, "source": "SIAT"} if r else None
    zone_counts = dict(db.execute(select(RegionalRiskScore.status, func.count()).where(
        RegionalRiskScore.period_year == year, RegionalRiskScore.layer == "overall_area_status").group_by(RegionalRiskScore.status)).all())
    s = scope_of(user)
    visible = lambda col: True if s.national else col.in_(s.regions or {"-"})  # noqa: E731
    preg_q = select(Pregnancy.current_risk_level, func.count()).where(Pregnancy.status == "active")
    registry = {
        "label": "Klinik registr (DEMO — sintetik ma’lumot)" ,
        "active_pregnancies_by_risk": dict(db.execute(preg_q.group_by(Pregnancy.current_risk_level)).all()) if s.national else None,
        "newborns_by_risk": dict(db.execute(select(Newborn.current_risk_level, func.count()).group_by(Newborn.current_risk_level)).all()) if s.national else None,
        "open_alerts": db.execute(select(func.count()).select_from(Alert).where(Alert.status != "resolved", visible(Alert.region_soato))).scalar(),
    }
    return {"official_statistics": {"year": year, "infant_mortality_rate": nat("infant_mortality_rate"),
                                    "under5_mortality_rate": nat("under5_mortality_rate"),
                                    "maternal_mortality_ratio": nat("maternal_mortality_ratio"),
                                    "births_total": nat("births_total"), "preterm_birth_pct": nat("preterm_birth_pct")},
            "area_status_counts": zone_counts, "clinical_registry": registry}

from __future__ import annotations

import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import rate_limiter
from app.core.errors import AppError
from app.core.security import utcnow
from app.db import get_db
from app.models import Alert, Pregnancy
from app.services import official_stats
from app.services.live_bus import iso
from app.services.live_engine import geo

router = APIRouter(prefix="/public", tags=["public"])
TTL = 60
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()
TOTAL_CODES = ("births_total", "infant_mortality_rate", "under5_mortality_rate", "maternal_mortality_ratio",
               "population_thousand", "doctors_per_10000")


def _gate(request: Request, response: Response) -> None:
    ip = request.client.host if request.client else "unknown"
    rate_limiter.hit(f"public:{ip}", get_settings().PUBLIC_RATE_PER_MINUTE)
    response.headers["Cache-Control"] = f"public, max-age={TTL}"


def _cached(key: tuple, build):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < TTL:
        return hit[1]
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < TTL:
            return hit[1]
        if len(_cache) > 400:
            _cache.clear()
        value = build()
        _cache[key] = (time.monotonic(), value)
        return value


def clear_cache() -> None:
    _cache.clear()


def _stats(db: Session) -> official_stats.OfficialStats:
    geo.load(db)
    return official_stats.stats.load(geo.districts)


def _mask(n: int | None) -> int | None:
    """Small-cell rule (k = REPORT_MIN_CELL): 1..k-1 is withheld (null); 0 and >= k are published."""
    if n is None:
        return None
    return None if 0 < n < get_settings().REPORT_MIN_CELL else int(n)


def _overview(db: Session) -> dict:
    st = _stats(db)
    now = utcnow()
    indicators, births, births_year = [], None, None
    for code in TOTAL_CODES:
        years = [y for y in st.years(code) if st.value(code, "1700", y) is not None]
        if not years:
            continue
        ind, y = official_stats.INDICATORS[code], years[-1]
        indicators.append({"code": code, "label": ind["label"], "unit": ind["unit"], "value": st.value(code, "1700", y), "year": y})
        if code == "births_total":
            births, births_year = st.value(code, "1700", y), y
    pregnant = db.execute(select(func.count(Pregnancy.id)).where(Pregnancy.status == "active")).scalar() or 0
    waiting = db.execute(select(func.count(Alert.id)).where(Alert.status == "open")).scalar() or 0
    since = now - timedelta(hours=24)
    mins = sorted((a - c).total_seconds() / 60 for c, a in db.execute(
        select(Alert.created_at, Alert.acknowledged_at).where(Alert.acknowledged_at.is_not(None),
                                                               Alert.acknowledged_at >= since,
                                                               Alert.acknowledged_by.is_not(None))))
    k = get_settings().REPORT_MIN_CELL
    median = round(mins[len(mins) // 2]) if len(mins) >= k else None  # a median of a handful of cases is not published
    live = {"pregnant": _mask(pregnant), "waiting": _mask(waiting), "median_response_min": median,
            "answered_24h": _mask(len(mins))}
    return {"totals": {"regions": len(geo.regions), "districts": len(geo.districts), "births_year": births_year,
                       "births": births, "indicators": indicators},
            "live": live, "masked": [n for n, v in live.items() if v is None], "small_cell_rule": f"1..{k - 1} -> null",
            "generated_at": iso(now), "source": official_stats.SOURCE, "demo": not get_settings().is_production}


def _official_map(db: Session, indicator: str | None, year: int | None) -> dict:
    st = _stats(db)
    code = indicator or official_stats.DEFAULT_INDICATOR
    if code not in official_stats.INDICATORS:
        raise AppError(422, "unknown_indicator", "Bunday koʻrsatkich yoʻq", [{"available": [i["code"] for i in st.available()]}])
    years = st.years(code)
    ind = official_stats.INDICATORS[code]
    base = {"indicator": {"code": code, "label": ind["label"], "unit": ind["unit"], "kind": ind["kind"]},
            "available_indicators": st.available(), "years": years, "level": "region",
            "source": official_stats.SOURCE, "method": official_stats.METHOD}
    if not years:
        return {**base, "year": None, "regions": []}
    if year is None:
        year = years[-1]
    if year not in years:
        raise AppError(422, "unknown_year", "Bu yil uchun maʼlumot yoʻq", [{"years": years}])
    national = st.value(code, "1700", year)
    regions = [{"soato": soato, "name": name, "value": st.value(code, soato, year), "national": national,
                "status": st.status(code, soato, year)[0]} for soato, name in sorted(geo.regions.items())]
    return {**base, "year": year, "regions": regions}


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: None = Depends(_gate)):
    return _cached(("overview",), lambda: _overview(db))


@router.get("/official-map")
def official_map(indicator: str | None = Query(None, pattern=r"^[a-z0-9_]{3,40}$"),
                 year: int | None = Query(None, ge=2000, le=2100), db: Session = Depends(get_db),
                 _: None = Depends(_gate)):
    return _cached(("map", indicator, year), lambda: _official_map(db, indicator, year))

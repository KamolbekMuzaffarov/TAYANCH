from __future__ import annotations

import csv
import io
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1 import live
from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.deps import rate_limiter
from app.core.errors import AppError
from app.core.security import decrypt, utcnow
from app.db import get_db
from app.models import (Alert, AlertAction, AuthSession, Facility, MaternalObservation, Newborn, NewbornObservation, Pregnancy,
                        Role, ScopeType, User, UserScope)
from app.services import live_engine, trend
from app.services.live_bus import iso
from app.services.live_engine import geo, write_tx

router = APIRouter(prefix="/admin", tags=["admin-console"])
ZONES = ("red", "yellow", "green", "grey")
ROLE_ENUM = {"admin": Role.NATIONAL_ADMIN, "leader": Role.REGIONAL_ADMIN, "pending": Role.PENDING}
OVERVIEW_TTL = 15
_overview_cache: dict[int, tuple[float, dict]] = {}
_overview_lock = threading.Lock()


def admin_user(request: Request, user: User = Depends(live.live_user)) -> User:
    if live.role_of(user) != "admin":
        live._audit(user, "admin.access", "user", user.id, request, {"reason": "role_not_allowed"}, outcome="denied")
        raise AppError(403, "forbidden", "Bu boʻlim faqat tizim boshqaruvchisi (admin) uchun ochiq")
    return user


def _audit(user: User, action: str, request: Request, resource_type: str = "report", resource_id: str | None = None,
           detail: dict | None = None) -> None:
    live._audit(user, action, resource_type, resource_id, request, detail)


def clear_cache() -> None:
    _overview_cache.clear()


# ------------------------------------------------------------------ overview
def _median(xs: list[float]) -> int | None:
    return round(statistics.median(xs)) if xs else None


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _build_overview(db: Session, days: int) -> dict:
    geo.load(db)
    s = get_settings()
    now = utcnow()
    today = live.local_date(now)
    since = now - timedelta(days=days)
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    sla = {"red": s.ALERT_SLA_MINUTES_RED, "yellow": s.ALERT_SLA_MINUTES_YELLOW}
    reg = {so: {"soato": so, "name": n, "pregnant": 0, "red": 0, "yellow": 0, "open_alerts": 0, "overdue_alerts": 0,
                "_resp": [], "_in_sla": 0} for so, n in geo.regions.items()}
    kpis = {"pregnant": 0, "newborns": 0, "red": 0, "yellow": 0, "green": 0, "grey": 0, "open_alerts": 0, "overdue_alerts": 0}

    for fid, level, n in db.execute(select(Pregnancy.facility_id, Pregnancy.current_risk_level, func.count())
                                    .where(Pregnancy.status == "active")
                                    .group_by(Pregnancy.facility_id, Pregnancy.current_risk_level)):
        zone = level if level in ZONES else "grey"
        kpis["pregnant"] += n
        kpis[zone] += n
        r = reg.get(geo.fac_region.get(fid))
        if r:
            r["pregnant"] += n
            if zone in ("red", "yellow"):
                r[zone] += n
    kpis["newborns"] = db.execute(select(func.count(Newborn.id))).scalar() or 0

    by_day = {d: {"date": d, "red": 0, "yellow": 0, "resolved": 0} for d in dates}
    resp_day: dict[str, list[float]] = defaultdict(list)
    resp, in_sla = [], 0
    reasons, resolutions = Counter(), Counter()
    q = select(Alert.created_at, Alert.acknowledged_at, Alert.acknowledged_by, Alert.resolved_at, Alert.severity,
               Alert.status, Alert.region_soato, Alert.rule_ids, Alert.resolution_code, Alert.escalation_level,
               Alert.sla_due_at)
    for created, acked, acked_by, resolved, sev, status, region, rule_ids, res_code, level, due in db.execute(q):
        r = reg.get(region)
        if status == "open":
            overdue = level > 0 or due < now
            kpis["open_alerts"] += 1
            kpis["overdue_alerts"] += overdue
            if r:
                r["open_alerts"] += 1
                r["overdue_alerts"] += overdue
        if created >= since:
            d = by_day.get(live.local_date(created).isoformat())
            if d and sev in ("red", "yellow"):
                d[sev] += 1
            for rule in rule_ids or []:
                reasons[rule] += 1
        if resolved and resolved >= since:
            d = by_day.get(live.local_date(resolved).isoformat())
            if d:
                d["resolved"] += 1
            if res_code:
                resolutions[res_code] += 1
        if acked and acked_by and acked >= since:  # answered by a person (not auto-closed)
            mins = (acked - created).total_seconds() / 60
            ok = mins <= sla.get(sev, s.ALERT_SLA_MINUTES_YELLOW)
            resp.append(mins)
            in_sla += ok
            resp_day[live.local_date(acked).isoformat()].append(mins)
            if r:
                r["_resp"].append(mins)
                r["_in_sla"] += ok

    visits_day = {d: 0 for d in dates}
    voice = total_obs = visits_7d = 0
    week = now - timedelta(days=7)
    for at, source in db.execute(select(MaternalObservation.observed_at, MaternalObservation.source)
                                 .where(MaternalObservation.observed_at >= min(since, week))):
        visits_7d += at >= week
        if at >= since:
            total_obs += 1
            voice += source == "voice"
            d = live.local_date(at).isoformat()
            if d in visits_day:
                visits_day[d] += 1
    for (at,) in db.execute(select(NewbornObservation.observed_at).where(NewbornObservation.observed_at >= min(since, week))):
        visits_7d += at >= week
        if at >= since:
            d = live.local_date(at).isoformat()
            if d in visits_day:
                visits_day[d] += 1

    users = pending = 0
    for active, status, role in db.execute(select(User.is_active, User.account_status, User.live_role)):
        if status == "pending" or role == "pending":
            pending += 1
        elif active and status != "blocked":
            users += 1
    p90 = round(sorted(resp)[min(len(resp) - 1, int(0.9 * len(resp)))]) if resp else None
    kpis.update({"median_response_min": _median(resp), "p90_response_min": p90,
                 "answered_in_sla_pct": _pct(in_sla, len(resp)), "visits_7d": int(visits_7d),
                 "voice_share_pct": _pct(voice, total_obs) or 0.0, "users": users, "pending_users": pending})
    kpis = {k: (int(v) if isinstance(v, bool) else v) for k, v in kpis.items()}
    by_region = [{"soato": r["soato"], "name": r["name"], "pregnant": r["pregnant"], "red": r["red"], "yellow": r["yellow"],
                  "open_alerts": int(r["open_alerts"]), "overdue_alerts": int(r["overdue_alerts"]),
                  "median_response_min": _median(r["_resp"]), "answered_in_sla_pct": _pct(r["_in_sla"], len(r["_resp"]))}
                 for r in sorted(reg.values(), key=lambda x: x["soato"])]
    res_label = {k: v[:1].upper() + v[1:] for k, v in live_engine.RESOLUTION_TEXT.items()}
    return {"generated_at": iso(now), "days": days, "kpis": kpis,
            "labels": {"pregnant": "Homilador ayollar", "newborns": "Chaqaloqlar", "red": "Qizil hudud", "yellow": "Sariq hudud",
                       "green": "Yashil hudud", "grey": "Maʼlumot yetarli emas", "open_alerts": "Javob kutayotgan signallar",
                       "overdue_alerts": "Muddati oʻtgan signallar", "median_response_min": "Oʻrtacha (median) javob vaqti, daqiqa",
                       "p90_response_min": "Javob vaqti, 90-protsentil, daqiqa",
                       "answered_in_sla_pct": "Belgilangan muddatda javob berilgan, %", "visits_7d": "Soʻnggi 7 kundagi tashriflar",
                       "voice_share_pct": "Ovoz bilan kiritilgan tashriflar, %", "users": "Faol foydalanuvchilar",
                       "pending_users": "Rol kutayotgan foydalanuvchilar"},
            "series": {"alerts_by_day": [by_day[d] for d in dates],
                       "response_by_day": [{"date": d, "median_min": _median(resp_day.get(d, []))} for d in dates],
                       "visits_by_day": [{"date": d, "count": visits_day[d]} for d in dates]},
            "by_region": by_region,
            "top_reasons": [{"code": c, "label": trend.RULE_UZ.get(c, (c, ""))[0], "count": n} for c, n in reasons.most_common(10)],
            "resolutions": [{"code": c, "label": res_label.get(c, c), "count": n} for c, n in resolutions.most_common()]}


def overview_data(db: Session, days: int) -> dict:
    hit = _overview_cache.get(days)
    if hit and time.monotonic() - hit[0] < OVERVIEW_TTL:
        return hit[1]
    with _overview_lock:
        hit = _overview_cache.get(days)
        if hit and time.monotonic() - hit[0] < OVERVIEW_TTL:
            return hit[1]
        data = _build_overview(db, days)
        _overview_cache[days] = (time.monotonic(), data)
        return data


@router.get("/overview")
def overview(request: Request, days: int = Query(30, ge=1, le=90), user: User = Depends(admin_user),
             db: Session = Depends(get_db)):
    data = overview_data(db, days)
    _audit(user, "admin.overview.read", request, detail={"days": days})
    return data


# ------------------------------------------------------------------ users
def _user_item(u: User, fac_names: dict[str, str] | None = None) -> dict:
    p = live._user_payload(u)
    fac_id = next((sc.facility_id for sc in u.scopes if sc.facility_id), None)
    status = "blocked" if (u.account_status == "blocked" or not u.is_active) else \
        "pending" if p["role"] == "pending" else "active"
    return {"id": u.id, "name": u.full_name, "email": p["email"], "phone": p["phone"], "role": p["role"], "status": status,
            "two_step": bool(u.two_step), "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at),
            "region_name": p["region_name"] if p["region_soato"] else None, "district_name": p["district_name"],
            "region_soato": p["region_soato"], "district_code": p["district_code"],
            "facility_id": fac_id, "facility_name": (fac_names or {}).get(fac_id) if fac_id else None,
            "registered": bool(u.email_hash or u.phone_hash or u.google_sub_hash), "demo": u.username.startswith("demo.")}


@router.get("/users")
def users(request: Request, status: Literal["active", "pending", "blocked"] | None = None,
          user: User = Depends(admin_user), db: Session = Depends(get_db)):
    geo.load(db)
    fac_names = {i: trend.uz(n) for i, n in db.execute(select(Facility.id, Facility.name))}
    items = [_user_item(u, fac_names) for u in db.execute(select(User).order_by(User.created_at.desc())).scalars()]
    if status:
        items = [i for i in items if i["status"] == status]
    items.sort(key=lambda i: (i["status"] != "pending", not i["registered"]))  # waiting accounts first
    _audit(user, "admin.users.read", request, "user", detail={"rows": len(items), "filter": status})
    return {"items": items}


class UserUpdate(BaseModel):
    """The system admin gives NON-clinical roles only. `nurse` / `specialist` are not accepted here (422): nurses and
    doctors are added exclusively by the administrator of their own facility through POST /facility/staff."""
    model_config = {"extra": "forbid"}
    role: Literal["admin", "leader", "facility_admin", "pending"] | None = None
    status: Literal["active", "blocked"] | None = None
    region_soato: str | None = Field(None, max_length=8)
    district_code: str | None = Field(None, max_length=32)
    facility_id: str | None = Field(None, max_length=36)  # required with role = facility_admin


@router.post("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, request: Request, user: User = Depends(admin_user)):
    from app.api.v1 import facility as fac_api  # late import: facility.py depends on this module
    live._limit(user, "admin")
    with write_tx() as db:
        geo.load(db)
        u = db.get(User, user_id) if len(user_id) <= 36 else None
        if u is None:
            raise AppError(404, "not_found", "Foydalanuvchi topilmadi")
        if u.username.startswith("demo."):
            raise AppError(409, "demo_account", "Namoyish hisoblarini oʻzgartirib boʻlmaydi")
        role = body.role or live.role_of(u)
        if u.id == user.id and (role != "admin" or body.status == "blocked"):
            raise AppError(422, "self_change", "Oʻz admin huquqingizni olib tashlay olmaysiz yoki oʻzingizni bloklay olmaysiz")
        region, district = body.region_soato or None, body.district_code or None
        if district:
            d = geo.districts.get(district)
            if d is None:
                raise AppError(422, "unknown_district", "Bunday tuman yoʻq")
            if region and region != d["region_soato"]:
                raise AppError(422, "district_region_mismatch", "Tuman tanlangan viloyatga tegishli emas")
            region = d["region_soato"]
        if region and region not in geo.regions:
            raise AppError(422, "unknown_region", "Bunday viloyat yoʻq")
        if body.role == "facility_admin":
            target = db.get(Facility, body.facility_id) if body.facility_id else None
            if target is None:
                raise AppError(422, "facility_required", "Muassasa administratori uchun muassasani tanlang")
            fac_api.appoint(db, user, target, u, request)
        elif body.role is not None:
            fac_api.release_admin(db, user, u, request)  # a facility admin who gets another role frees the facility
            u.role, u.live_role = ROLE_ENUM[role], role
            if u.account_status != "blocked":
                u.account_status = "pending" if role == "pending" else "active"
            # scope: leader = a region or the whole country; admin = country; pending = nothing
            for sc in list(u.scopes):
                db.delete(sc)
            db.flush()
            if role == "leader" and region:
                db.add(UserScope(user_id=u.id, scope_type=ScopeType.REGION, region_soato=region))
            elif role in ("leader", "admin"):
                db.add(UserScope(user_id=u.id, scope_type=ScopeType.NATIONAL))
        if body.status == "blocked":
            u.account_status, u.is_active = "blocked", False
            for sess in db.execute(select(AuthSession).where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None))).scalars():
                sess.revoked_at = utcnow()  # a blocked account loses every open session at once
        elif body.status == "active":
            u.is_active, u.locked_until, u.failed_logins = True, None, 0
            u.account_status = "pending" if u.live_role == "pending" else "active"
        db.flush()
        db.refresh(u)
        geo.users[u.id] = (u.full_name, u.role.value)
        write_audit(db, actor=user, action="admin.user.update", resource_type="user", resource_id=u.id,
                    request_id=request.state.request_id, purpose="admin",
                    detail={"role": live.role_of(u), "status": u.account_status, "region_soato": region,
                            "district_code": district}, commit=False)
        clear_cache()
        return _user_item(u, {i: trend.uz(n) for i, n in db.execute(select(Facility.id, Facility.name))})


# ------------------------------------------------------------------ CSV export (aggregates, small cells masked)
def _mask_rows(rows: list[dict], cols: tuple[str, ...], group: str | None = None) -> list[dict]:
    """Primary suppression: 1..k-1 -> "<k". Complementary: when exactly one cell of a column is suppressed inside a
    group (it could be recomputed from the group total published in another export), the smallest remaining
    non-zero cell of that column in the group is withheld too ("masked")."""
    k = get_settings().REPORT_MIN_CELL
    small = f"<{k}"
    out = [dict(r) for r in rows]
    for col in cols:
        groups: dict = defaultdict(list)
        for r in out:
            if isinstance(r[col], int) and 0 < r[col] < k:
                r[col] = small
            groups[r.get(group) if group else None].append(r)
        for members in groups.values():
            hidden = [r for r in members if r[col] == small]
            if len(hidden) == 1 and len(members) > 1:
                rest = [r for r in members if isinstance(r[col], int) and r[col] > 0]
                if rest:
                    min(rest, key=lambda r: r[col])[col] = "masked"
    return out


def _export_rows(db: Session, kind: str) -> tuple[list[str], list[dict]]:
    geo.load(db)
    now = utcnow()
    if kind == "regions":
        cols = ("pregnant", "red", "yellow", "open_alerts", "overdue_alerts")
        rows = [{k: r[k] for k in ("soato", "name", *cols, "median_response_min", "answered_in_sla_pct")}
                for r in overview_data(db, 30)["by_region"]]
        return ["soato", "name", *cols, "median_response_min", "answered_in_sla_pct"], _mask_rows(rows, cols)
    if kind == "districts":
        cols = ("pregnant", "red", "yellow", "open_alerts", "overdue_alerts")
        dist = {c: {"code": c, "name": d["name"], "region_soato": d["region_soato"],
                    "region_name": geo.regions.get(d["region_soato"]), **{k: 0 for k in cols}} for c, d in geo.districts.items()}
        for fid, level, n in db.execute(select(Pregnancy.facility_id, Pregnancy.current_risk_level, func.count())
                                        .where(Pregnancy.status == "active")
                                        .group_by(Pregnancy.facility_id, Pregnancy.current_risk_level)):
            d = dist.get(geo.fac_district.get(fid))
            if d:
                d["pregnant"] += n
                if level in ("red", "yellow"):
                    d[level] += n
        for fid, level, due in db.execute(select(Alert.facility_id, Alert.escalation_level, Alert.sla_due_at)
                                          .where(Alert.status == "open")):
            d = dist.get(geo.fac_district.get(fid))
            if d:
                d["open_alerts"] += 1
                d["overdue_alerts"] += int(level > 0 or due < now)
        rows = sorted(dist.values(), key=lambda r: r["code"])
        return ["code", "name", "region_soato", "region_name", *cols], _mask_rows(rows, cols, "region_soato")
    counts: Counter = Counter()
    for region, sev, status in db.execute(select(Alert.region_soato, Alert.severity, Alert.status)
                                          .where(Alert.created_at >= now - timedelta(days=30))):
        counts[(region, sev, status)] += 1
    rows = [{"region_soato": r, "region_name": geo.regions.get(r), "severity": sev, "status": st, "count": n,
             "_g": f"{sev}/{st}"} for (r, sev, st), n in sorted(counts.items())]
    rows = _mask_rows(rows, ("count",), "_g")
    return ["region_soato", "region_name", "severity", "status", "count"], rows


@router.get("/export.csv")
def export_csv(request: Request, kind: Literal["regions", "districts", "alerts"] = "regions",
               user: User = Depends(admin_user), db: Session = Depends(get_db)):
    live._limit(user, "export", 20)
    header, rows = _export_rows(db, kind)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(["" if r[h] is None else r[h] for h in header])
    _audit(user, "admin.export", request, "report", kind, {"rows": len(rows), "format": "csv", "kind": kind})
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="perinatal_{kind}.csv"'})


@router.get("/audit")
def audit(request: Request, limit: int = Query(100, ge=1, le=500), user: User = Depends(admin_user),
          db: Session = Depends(get_db)):
    out = live.audit_payload(db, limit)
    _audit(user, "admin.audit.read", request, "report", "audit", {"rows": len(out["items"])})
    return out


_unused = (AlertAction, rate_limiter, decrypt)

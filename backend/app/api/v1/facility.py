from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1 import account, live
from app.api.v1.console import admin_user, clear_cache
from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.deps import bearer, rate_limiter
from app.core.errors import AppError
from app.core.security import decrypt, encrypt, hash_password, utcnow
from app.db import get_db
from app.models import Alert, AuthSession, Facility, Pregnancy, Role, ScopeType, User, UserScope
from app.services import trend
from app.services.live_bus import iso
from app.services.live_engine import geo, write_tx

admin_router = APIRouter(prefix="/admin", tags=["admin-console"])
router = APIRouter(prefix="/facility", tags=["facility"])

STAFF_ROLES = ("nurse", "specialist")
STAFF_ENUM = {"nurse": Role.PATRONAGE_NURSE, "specialist": Role.OVABMU_SPECIALIST}
WINDOW = 600
UNAVAILABLE = "Bu manzil bilan xodim qoʻshib boʻlmaydi. Boshqa manzil kiriting yoki tizim boshqaruvchisiga murojaat qiling"


# ------------------------------------------------------------------ helpers shared with the admin console
def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _log(db: Session, actor: User, action: str, request: Request, resource_type: str, resource_id: str | None,
         detail: dict | None = None, outcome: str = "success") -> None:
    write_audit(db, actor=actor, action=action, resource_type=resource_type, resource_id=resource_id,
                request_id=_rid(request), detail=detail, purpose="facility_admin", outcome=outcome, commit=False)


def _is_demo(u: User) -> bool:
    return u.username.startswith("demo.")


def _revoke_sessions(db: Session, u: User) -> None:
    for sess in db.execute(select(AuthSession).where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None))).scalars():
        sess.revoked_at = utcnow()


def _set_scopes(db: Session, u: User, scopes: list[dict]) -> None:
    for sc in db.execute(select(UserScope).where(UserScope.user_id == u.id)).scalars():
        db.delete(sc)
    db.flush()
    for sc in scopes:
        db.add(UserScope(user_id=u.id, **sc))
    db.flush()
    db.refresh(u)
    geo.users[u.id] = (u.full_name, u.role.value)


def _to_pending(db: Session, u: User) -> None:
    """Detach an account from every facility: no role, no scope, no open session. A blocked account stays blocked."""
    u.role, u.live_role = Role.PENDING, "pending"
    if u.account_status != "blocked" and u.is_active:
        u.account_status = "pending"
    _revoke_sessions(db, u)
    _set_scopes(db, u, [])


def _contact(u: User) -> tuple[str | None, str | None]:
    return decrypt(u.email_enc), decrypt(u.phone_enc)


def _identifier(raw: str) -> tuple[str, str]:
    channel = "email" if "@" in (raw or "") else "phone"
    return channel, account.normalise(channel, raw)


def _new_account(db: Session, channel: str, identifier: str, full_name: str) -> User:
    """A pre-approved account bound to one e-mail address / phone number. No password: the person signs in with the
    normal one-time-code flow of /account/start (mode login), which proves they own that address."""
    u = User(username=f"u.{secrets.token_hex(10)}", full_name=full_name.strip()[:200] or "Foydalanuvchi",
             password_hash=hash_password(secrets.token_urlsafe(24)), role=Role.PENDING, live_role="pending",
             account_status="pending", two_step=False)
    if channel == "email":
        u.email_enc, u.email_hash = encrypt(identifier), account.ident_hash("email", identifier)
    else:
        u.phone_enc, u.phone_hash = encrypt(identifier), account.ident_hash("phone", identifier)
    db.add(u)
    db.flush()
    return u


def _admin_brief(u: User | None) -> dict | None:
    if u is None:
        return None
    email, phone = _contact(u)
    return {"id": u.id, "name": u.full_name, "email": email, "phone": phone, "demo": _is_demo(u),
            "status": "blocked" if (u.account_status == "blocked" or not u.is_active) else "active"}


def release_admin(db: Session, actor: User, u: User, request: Request) -> Facility | None:
    """`u` stops being the administrator of whatever facility they had (the caller decides their next role)."""
    fac = db.execute(select(Facility).where(Facility.admin_user_id == u.id)).scalar_one_or_none()
    if fac is not None:
        fac.admin_user_id = None
        db.flush()
        _log(db, actor, "admin.facility.admin.demote", request, "user", u.id, {"facility_id": fac.id, "reason": "role_change"})
    return fac


def appoint(db: Session, actor: User, fac: Facility, target: User, request: Request) -> None:
    """Make `target` THE administrator of `fac`. The previous administrator (if any) is demoted to `pending`."""
    if _is_demo(target):
        raise AppError(409, "demo_account", "Namoyish hisoblarini oʻzgartirib boʻlmaydi")
    if target.id == actor.id or live.role_of(target) == "admin":
        raise AppError(422, "self_change", "Tizim boshqaruvchisini muassasa administratori qilib boʻlmaydi")
    if target.account_status == "blocked" or not target.is_active:
        raise AppError(409, "account_blocked", "Bu hisob bloklangan. Avval blokdan chiqaring")
    other = db.execute(select(Facility).where(Facility.admin_user_id == target.id)).scalar_one_or_none()
    if other is not None and other.id != fac.id:
        raise AppError(409, "admin_elsewhere", "Bu foydalanuvchi boshqa muassasaning administratori. Avval u yerdan boʻshating")
    previous = db.get(User, fac.admin_user_id) if fac.admin_user_id else None
    if previous is not None and previous.id != target.id:
        if _is_demo(previous):
            raise AppError(409, "demo_account", "Namoyish muassasasining administratorini almashtirib boʻlmaydi")
        fac.admin_user_id = None
        db.flush()
        _to_pending(db, previous)
        _log(db, actor, "admin.facility.admin.demote", request, "user", previous.id,
             {"facility_id": fac.id, "reason": "replaced", "replaced_by": target.id})
    target.role, target.live_role, target.account_status = Role.FACILITY_MANAGER, "facility_admin", "active"
    _set_scopes(db, target, [dict(scope_type=ScopeType.FACILITY, facility_id=fac.id)])
    fac.admin_user_id = target.id
    db.flush()
    _log(db, actor, "admin.facility.admin.appoint", request, "user", target.id,
         {"facility_id": fac.id, "previous_user_id": previous.id if previous and previous.id != target.id else None})
    clear_cache()


# ------------------------------------------------------------------ system admin: facilities and their administrators
def _staff_users(db: Session, facility_ids: set[str] | None = None) -> dict[str, list[User]]:
    """facility id -> nurses and doctors bound to it (the administrator is not staff)."""
    q = (select(User, UserScope.facility_id).join(UserScope, UserScope.user_id == User.id)
         .where(UserScope.scope_type == ScopeType.FACILITY, UserScope.facility_id.is_not(None)))
    if facility_ids is not None:
        q = q.where(UserScope.facility_id.in_(facility_ids or {"-"}))
    out: dict[str, list[User]] = {}
    for u, fid in db.execute(q).unique():
        if u.live_role in STAFF_ROLES or (u.live_role is None and live.ROLE_PERSONA.get(u.role) in STAFF_ROLES):
            out.setdefault(fid, []).append(u)
    return out


def _facility_item(f: Facility, admin: User | None, staff_count: int) -> dict:
    d = geo.districts.get(f.district_code) if f.district_code else None
    return {"id": f.id, "name": trend.uz(f.name), "type": f.facility_type, "level": f.level,
            "region_soato": f.region_soato, "region": geo.regions.get(f.region_soato),
            "district_code": f.district_code, "district": d["name"] if d else None,
            "facility_admin": _admin_brief(admin), "staff_count": staff_count}


def _fold(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


@admin_router.get("/facilities")
def facilities(request: Request, q: str = Query("", max_length=80), region_soato: str | None = Query(None, max_length=8),
               type: str | None = Query(None, max_length=40, pattern=r"^[a-z_]{2,40}$"),
               user: User = Depends(admin_user), db: Session = Depends(get_db)):
    geo.load(db)
    query = select(Facility)
    if region_soato:
        query = query.where(Facility.region_soato == region_soato)
    if type:
        query = query.where(Facility.facility_type == type)
    rows = list(db.execute(query.order_by(Facility.region_soato, Facility.name)).scalars())
    needle = _fold(q)
    if needle:
        rows = [f for f in rows if needle in _fold(trend.uz(f.name))
                or needle in _fold((geo.districts.get(f.district_code or "") or {}).get("name", ""))]
    staff = _staff_users(db)
    admins = {u.id: u for u in db.execute(select(User).where(User.id.in_([f.admin_user_id for f in rows if f.admin_user_id] or ["-"]))).scalars()}
    items = [_facility_item(f, admins.get(f.admin_user_id), len(staff.get(f.id, []))) for f in rows]
    items.sort(key=lambda i: (i["facility_admin"] is None, i["region"] or "", i["name"]))  # facilities with an admin first
    types = sorted({t for (t,) in db.execute(select(Facility.facility_type).distinct())})
    live._audit(user, "admin.facilities.read", "facility", None, request, {"rows": len(items)})
    return {"items": items, "types": types, "total": len(items)}


class AppointIn(BaseModel):
    model_config = {"extra": "forbid"}
    user_id: str | None = Field(None, min_length=8, max_length=36)
    identifier: str | None = Field(None, min_length=3, max_length=254)
    full_name: str | None = Field(None, min_length=2, max_length=200)


@admin_router.post("/facilities/{facility_id}/admin")
def appoint_admin(facility_id: str, body: AppointIn, request: Request, user: User = Depends(admin_user)):
    live._limit(user, "admin")
    if bool(body.user_id) == bool(body.identifier):
        raise AppError(422, "target_required", "Mavjud foydalanuvchini tanlang yoki ism va manzilni kiriting")
    with write_tx() as db:
        geo.load(db)
        fac = db.get(Facility, facility_id) if len(facility_id) <= 36 else None
        if fac is None:
            raise AppError(404, "not_found", "Muassasa topilmadi")
        created = False
        if body.user_id:
            target = db.get(User, body.user_id)
            if target is None:
                raise AppError(404, "not_found", "Foydalanuvchi topilmadi")
        else:
            channel, identifier = _identifier(body.identifier)
            target = account._find(db, channel, identifier)
            if target is None:
                if not (body.full_name or "").strip():
                    raise AppError(422, "name_required", "Ism va familiyani kiriting")
                target, created = _new_account(db, channel, identifier, body.full_name), True
                _log(db, user, "admin.account.create", request, "user", target.id, {"channel": channel, "for": "facility_admin"})
        appoint(db, user, fac, target, request)
        db.refresh(fac)
        return {**_facility_item(fac, target, len(_staff_users(db, {fac.id}).get(fac.id, []))), "created": created}


@admin_router.delete("/facilities/{facility_id}/admin")
def remove_admin(facility_id: str, request: Request, user: User = Depends(admin_user)):
    live._limit(user, "admin")
    with write_tx() as db:
        geo.load(db)
        fac = db.get(Facility, facility_id) if len(facility_id) <= 36 else None
        if fac is None:
            raise AppError(404, "not_found", "Muassasa topilmadi")
        previous = db.get(User, fac.admin_user_id) if fac.admin_user_id else None
        if previous is not None:
            if _is_demo(previous):
                raise AppError(409, "demo_account", "Namoyish muassasasining administratorini olib tashlab boʻlmaydi")
            fac.admin_user_id = None
            db.flush()
            _to_pending(db, previous)
            _log(db, user, "admin.facility.admin.demote", request, "user", previous.id, {"facility_id": fac.id, "reason": "removed"})
            clear_cache()
        return _facility_item(fac, None, len(_staff_users(db, {fac.id}).get(fac.id, [])))


# ------------------------------------------------------------------ facility administrator: own staff only
def facility_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    user = live._authenticate(request, creds.credentials if creds else None, allow_facility_admin=True)
    if live.role_of(user) != "facility_admin":
        live._audit(user, "facility.access", "user", user.id, request, {"reason": "role_not_allowed"}, outcome="denied")
        raise AppError(403, "forbidden", "Bu boʻlim faqat muassasa administratori uchun ochiq")
    return user


def _own(db: Session, user: User) -> Facility:
    """The caller's facility - taken from the database, never from the request."""
    fac = db.execute(select(Facility).where(Facility.admin_user_id == user.id)).scalar_one_or_none()
    if fac is None:
        raise AppError(403, "forbidden", "Sizga muassasa biriktirilmagan")
    return fac


def _staff_item(u: User) -> dict:
    email, phone = _contact(u)
    district = next((s.district_code for s in u.scopes if s.district_code), None)
    d = geo.districts.get(district) if district else None
    return {"id": u.id, "name": u.full_name, "role": live.ROLE_PERSONA.get(u.role, "nurse") if u.live_role is None else u.live_role,
            "contact": account.mask("email", email) or account.mask("phone", phone),
            "channel": "email" if email else "phone" if phone else None,
            "status": "blocked" if (u.account_status == "blocked" or not u.is_active) else "active",
            "district_code": district, "district_name": d["name"] if d else None,
            "last_login_at": iso(u.last_login_at), "created_at": iso(u.created_at), "demo": _is_demo(u)}


def _staff(db: Session, fac: Facility) -> list[User]:
    return [u for u in _staff_users(db, {fac.id}).get(fac.id, []) if u.id != fac.admin_user_id]


def _member(db: Session, fac: Facility, user: User, request: Request, staff_id: str, action: str) -> User:
    """A staff member of the caller's facility, or 404 - also for an account that exists elsewhere (no enumeration)."""
    u = next((x for x in _staff(db, fac) if x.id == staff_id), None) if len(staff_id) <= 36 else None
    if u is None:
        _log(db, user, action, request, "user", staff_id[:36], {"reason": "outside_facility"}, outcome="denied")
        db.commit()
        raise AppError(404, "not_found", "Xodim topilmadi")
    if _is_demo(u):
        raise AppError(409, "demo_account", "Namoyish hisoblarini oʻzgartirib boʻlmaydi")
    return u


def _district_for(fac: Facility, role: str, district_code: str | None) -> str | None:
    """Nurses work in one district of the facility's region (default: the facility's own district)."""
    if role != "nurse":
        return None
    code = district_code or fac.district_code
    if not code:
        raise AppError(422, "district_required", "Hamshira uchun tumanni tanlang")
    d = geo.districts.get(code)
    if d is None or d["region_soato"] != fac.region_soato:
        raise AppError(422, "district_region_mismatch", "Tuman muassasa joylashgan viloyatga tegishli emas")
    return code


def _bind(db: Session, fac: Facility, u: User, role: str, district: str | None) -> None:
    u.role, u.live_role = STAFF_ENUM[role], role
    scopes = [dict(scope_type=ScopeType.FACILITY, facility_id=fac.id)]
    if role == "nurse":
        scopes.insert(0, dict(scope_type=ScopeType.DISTRICT, district_code=district, region_soato=fac.region_soato))
    else:
        scopes.insert(0, dict(scope_type=ScopeType.REGION, region_soato=fac.region_soato))
    _set_scopes(db, u, scopes)


def _counts(db: Session, fac: Facility, staff: list[User]) -> dict:
    k = get_settings().REPORT_MIN_CELL

    def safe(n: int) -> int | None:  # aggregate only; small cells are withheld like everywhere else
        return None if 0 < n < k else n
    items = [_staff_item(u) for u in staff]
    pregnant = db.execute(select(func.count(Pregnancy.id)).where(Pregnancy.facility_id == fac.id, Pregnancy.status == "active")).scalar() or 0
    open_alerts = db.execute(select(func.count(Alert.id)).where(Alert.facility_id == fac.id, Alert.status == "open")).scalar() or 0
    return {"nurses": sum(i["role"] == "nurse" for i in items), "specialists": sum(i["role"] == "specialist" for i in items),
            "blocked": sum(i["status"] == "blocked" for i in items), "staff": len(items),
            "pregnant": safe(pregnant), "open_alerts": safe(open_alerts)}


@router.get("/me")
def me(request: Request, user: User = Depends(facility_user), db: Session = Depends(get_db)):
    geo.load(db)
    fac = _own(db, user)
    d = geo.districts.get(fac.district_code) if fac.district_code else None
    districts = sorted(({"code": c, "name": x["name"]} for c, x in geo.districts.items() if x["region_soato"] == fac.region_soato),
                       key=lambda x: x["name"])
    live._audit(user, "facility.me.read", "facility", fac.id, request)
    return {"facility": {"id": fac.id, "name": trend.uz(fac.name), "type": fac.facility_type, "level": fac.level,
                         "region_soato": fac.region_soato, "region_name": geo.regions.get(fac.region_soato),
                         "district_code": fac.district_code, "district_name": d["name"] if d else None},
            "admin": {"id": user.id, "name": user.full_name},
            "counts": _counts(db, fac, _staff(db, fac)), "districts": districts}


@router.get("/staff")
def staff_list(request: Request, user: User = Depends(facility_user), db: Session = Depends(get_db)):
    geo.load(db)
    fac = _own(db, user)
    items = sorted((_staff_item(u) for u in _staff(db, fac)), key=lambda i: (i["status"] == "blocked", i["role"], i["name"]))
    live._audit(user, "facility.staff.read", "facility", fac.id, request, {"rows": len(items)})
    return {"items": items}


class StaffIn(BaseModel):
    model_config = {"extra": "forbid"}
    full_name: str = Field(min_length=2, max_length=200)
    identifier: str = Field(min_length=3, max_length=254)
    role: Literal["nurse", "specialist"]
    district_code: str | None = Field(None, max_length=32)


class ClaimIn(BaseModel):
    model_config = {"extra": "forbid"}
    identifier: str = Field(min_length=3, max_length=254)
    role: Literal["nurse", "specialist"] = "nurse"
    district_code: str | None = Field(None, max_length=32)


def _attach(body: StaffIn | ClaimIn, request: Request, user: User, *, create: bool) -> dict:
    live._limit(user, "facility")
    rate_limiter.hit(f"facility:add:{user.id}", 40, WINDOW)  # also bounds probing of who has an account
    channel, identifier = _identifier(body.identifier)
    with write_tx() as db:
        geo.load(db)
        fac = _own(db, user)
        district = _district_for(fac, body.role, body.district_code)
        u = account._find(db, channel, identifier)
        created = False
        if u is None:
            if not create:  # claim: same answer whether the account does not exist or is not waiting
                raise AppError(404, "not_found", "Bu manzil bilan rol kutayotgan hisob topilmadi")
            u, created = _new_account(db, channel, identifier, body.full_name), True
        elif any(x.id == u.id for x in _staff(db, fac)):
            raise AppError(409, "already_staff", "Bu xodim allaqachon roʻyxatda")
        elif live.role_of(u) != "pending" or u.account_status == "blocked" or not u.is_active or _is_demo(u):
            # an account that already has a role somewhere is never taken over; say as little as possible about it
            _log(db, user, "facility.staff.add", request, "facility", fac.id, {"reason": "identifier_unavailable"}, outcome="denied")
            db.commit()
            if not create:
                raise AppError(404, "not_found", "Bu manzil bilan rol kutayotgan hisob topilmadi")
            raise AppError(409, "identifier_unavailable", UNAVAILABLE)
        u.account_status, u.is_active = "active", True
        _bind(db, fac, u, body.role, district)
        _log(db, user, "facility.staff.add" if create else "facility.staff.claim", request, "user", u.id,
             {"facility_id": fac.id, "role": body.role, "district_code": district, "created": created, "channel": channel})
        clear_cache()
        return {"item": _staff_item(u), "created": created, "sign_in": account.mask(channel, identifier)}


@router.post("/staff")
def staff_add(body: StaffIn, request: Request, user: User = Depends(facility_user)):
    return _attach(body, request, user, create=True)


@router.post("/staff/claim")
def staff_claim(body: ClaimIn, request: Request, user: User = Depends(facility_user)):
    return _attach(body, request, user, create=False)


class StaffUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["active", "blocked"] | None = None
    role: Literal["nurse", "specialist"] | None = None
    district_code: str | None = Field(None, max_length=32)


@router.post("/staff/{staff_id}")
def staff_update(staff_id: str, body: StaffUpdate, request: Request, user: User = Depends(facility_user)):
    live._limit(user, "facility")
    with write_tx() as db:
        geo.load(db)
        fac = _own(db, user)
        u = _member(db, fac, user, request, staff_id, "facility.staff.update")
        current = _staff_item(u)
        role = body.role or current["role"]
        if body.role is not None or body.district_code is not None:
            district = _district_for(fac, role, body.district_code or (current["district_code"] if role == "nurse" else None))
            _bind(db, fac, u, role, district)
        if body.status == "blocked":
            u.account_status, u.is_active = "blocked", False
            _revoke_sessions(db, u)  # a blocked account loses every open session at once
        elif body.status == "active":
            u.account_status, u.is_active, u.locked_until, u.failed_logins = "active", True, None, 0
        db.flush()
        db.refresh(u)
        item = _staff_item(u)
        _log(db, user, "facility.staff.update", request, "user", u.id,
             {"facility_id": fac.id, "role": item["role"], "status": item["status"], "district_code": item["district_code"]})
        clear_cache()
        return item


@router.delete("/staff/{staff_id}")
def staff_remove(staff_id: str, request: Request, user: User = Depends(facility_user)):
    """Detach the account from the facility. Visits, assignments and every other clinical record stay untouched."""
    live._limit(user, "facility")
    with write_tx() as db:
        geo.load(db)
        fac = _own(db, user)
        u = _member(db, fac, user, request, staff_id, "facility.staff.remove")
        _to_pending(db, u)
        _log(db, user, "facility.staff.remove", request, "user", u.id, {"facility_id": fac.id})
        clear_cache()
        return {"ok": True, "counts": _counts(db, fac, _staff(db, fac))}

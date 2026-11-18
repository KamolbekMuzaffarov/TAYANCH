from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.errors import forbidden, not_found
from app.models import CareAssignment, Facility, Mother, Newborn, Pregnancy, Referral, Role, ScopeType, User

R = Role
PERMISSIONS: dict[str, set[Role]] = {
    "aggregate:read": {R.NATIONAL_ADMIN, R.REGIONAL_ADMIN, R.DISTRICT_ADMIN, R.FACILITY_ADMIN, R.DATA_STEWARD,
                       R.OBGYN, R.NEONATOLOGIST, R.OVABMU_SPECIALIST, R.PATRONAGE_NURSE},
    "mother:create": {R.OBGYN},
    "mother:read": {R.OBGYN, R.OVABMU_SPECIALIST, R.NEONATOLOGIST, R.NATIONAL_ADMIN},
    "mother:update": {R.OBGYN},
    "observation:create": {R.OBGYN, R.OVABMU_SPECIALIST, R.PATRONAGE_NURSE},
    "lab:create": {R.LAB_OPERATOR, R.OBGYN},
    "newborn:create": {R.NEONATOLOGIST, R.OBGYN},
    "newborn:read": {R.NEONATOLOGIST, R.OVABMU_SPECIALIST, R.OBGYN, R.NATIONAL_ADMIN},
    "newborn_observation:create": {R.NEONATOLOGIST, R.OVABMU_SPECIALIST},
    "alert:read": {R.OBGYN, R.NEONATOLOGIST, R.OVABMU_SPECIALIST, R.FACILITY_ADMIN},
    "alert:act": {R.OBGYN, R.NEONATOLOGIST, R.OVABMU_SPECIALIST},
    "referral:create": {R.OBGYN, R.NEONATOLOGIST},
    "referral:read": {R.OBGYN, R.NEONATOLOGIST, R.OVABMU_SPECIALIST},
    "referral:act": {R.OVABMU_SPECIALIST},
    "voice:use": {R.OBGYN, R.NEONATOLOGIST, R.OVABMU_SPECIALIST, R.PATRONAGE_NURSE},
    "assistant:use": set(Role) - {R.PENDING, R.FACILITY_MANAGER},
    "import:run": {R.DATA_STEWARD, R.NATIONAL_ADMIN},
    "report:read": {R.NATIONAL_ADMIN, R.REGIONAL_ADMIN, R.DISTRICT_ADMIN, R.FACILITY_ADMIN, R.DATA_STEWARD},
    "report:export": {R.NATIONAL_ADMIN, R.REGIONAL_ADMIN, R.DISTRICT_ADMIN, R.DATA_STEWARD},
    "audit:read": {R.AUDITOR, R.NATIONAL_ADMIN},
    "user:manage": {R.NATIONAL_ADMIN, R.FACILITY_ADMIN},
    "rules:read": {R.NATIONAL_ADMIN, R.DATA_STEWARD, R.OBGYN, R.NEONATOLOGIST, R.OVABMU_SPECIALIST, R.AUDITOR},
    "privacy:read": {R.NATIONAL_ADMIN, R.AUDITOR},
}


def require(user: User, permission: str) -> None:
    if user.role not in PERMISSIONS.get(permission, set()):
        raise forbidden()


@dataclass
class ScopeSet:
    national: bool
    regions: set[str]
    districts: set[str]
    facilities: set[str]


def scope_of(user: User) -> ScopeSet:
    s = ScopeSet(False, set(), set(), set())
    for sc in user.scopes:
        if sc.scope_type == ScopeType.NATIONAL:
            s.national = True
        elif sc.scope_type == ScopeType.REGION and sc.region_soato:
            s.regions.add(sc.region_soato)
        elif sc.scope_type == ScopeType.DISTRICT and sc.district_code:
            s.districts.add(sc.district_code)
        elif sc.scope_type == ScopeType.FACILITY and sc.facility_id:
            s.facilities.add(sc.facility_id)
    return s


def can_see_region(user: User, region_soato: str) -> bool:
    s = scope_of(user)
    return s.national or region_soato in s.regions or bool(s.districts and any(d.startswith(region_soato) for d in s.districts))


def assert_region(user: User, region_soato: str) -> None:
    if not can_see_region(user, region_soato):
        raise forbidden()


def facility_in_scope(db: Session, user: User, facility_id: str) -> bool:
    s = scope_of(user)
    if s.national or facility_id in s.facilities:
        return True
    fac = db.get(Facility, facility_id)
    if not fac:
        return False
    return fac.region_soato in s.regions or (fac.district_code is not None and fac.district_code in s.districts)


def _assigned(db: Session, user: User, *, mother_id: str | None = None, newborn_id: str | None = None) -> bool:
    q = select(exists().where(CareAssignment.user_id == user.id, CareAssignment.active.is_(True),
                              (CareAssignment.mother_id == mother_id) if mother_id else (CareAssignment.newborn_id == newborn_id)))
    return bool(db.execute(q).scalar())


def _referred_to_my_facility(db: Session, user: User, *, mother_id: str | None = None, newborn_id: str | None = None) -> bool:
    fac_ids = scope_of(user).facilities
    if not fac_ids:
        return False
    cond = (Referral.mother_id == mother_id) if mother_id else (Referral.newborn_id == newborn_id)
    q = select(exists().where(cond, Referral.to_facility_id.in_(fac_ids), Referral.status.in_(["sent", "accepted", "consulted"])))
    return bool(db.execute(q).scalar())


def assert_mother_access(db: Session, user: User, mother: Mother | None, action: str = "read") -> Mother:
    """Clinical record access. Admin/auditor roles get no clinical access by default."""
    if mother is None:
        raise not_found("patient")
    require(user, f"mother:{action}")
    ok = False
    if user.role == Role.OBGYN:
        ok = _assigned(db, user, mother_id=mother.id) or (action == "read" and _referred_to_my_facility(db, user, mother_id=mother.id))
    elif user.role == Role.OVABMU_SPECIALIST:
        ok = _referred_to_my_facility(db, user, mother_id=mother.id)
    elif user.role == Role.NEONATOLOGIST:
        ok = bool(db.execute(select(exists().where(Newborn.mother_id == mother.id,
                                                    Newborn.facility_id.in_(scope_of(user).facilities or {"-"})))).scalar())
    elif user.role == Role.NATIONAL_ADMIN:
        ok = user.clinical_record_access
    if not ok:
        raise not_found("patient")  # do not reveal that the record exists
    return mother


def assert_pregnancy_access(db: Session, user: User, preg: Pregnancy | None, action: str = "read") -> Pregnancy:
    if preg is None:
        raise not_found("pregnancy")
    assert_mother_access(db, user, db.get(Mother, preg.mother_id), "read" if action == "read" else "update")
    return preg


def assert_newborn_access(db: Session, user: User, nb: Newborn | None, action: str = "read") -> Newborn:
    if nb is None:
        raise not_found("newborn")
    require(user, "newborn:read")
    ok = False
    if user.role == Role.NEONATOLOGIST:
        ok = nb.facility_id in scope_of(user).facilities or _assigned(db, user, newborn_id=nb.id)
    elif user.role == Role.OVABMU_SPECIALIST:
        ok = _referred_to_my_facility(db, user, newborn_id=nb.id)
    elif user.role == Role.OBGYN:
        ok = action == "read" and nb.mother_id is not None and _assigned(db, user, mother_id=nb.mother_id)
    elif user.role == Role.NATIONAL_ADMIN:
        ok = user.clinical_record_access and action == "read"
    if not ok:
        raise not_found("newborn")
    return nb


def assert_lab_write(db: Session, user: User, preg: Pregnancy | None) -> Pregnancy:
    """Lab operators may submit results for pregnancies at their facility but cannot read the record."""
    if preg is None:
        raise not_found("pregnancy")
    require(user, "lab:create")
    if user.role == Role.LAB_OPERATOR:
        if preg.facility_id not in scope_of(user).facilities:
            raise not_found("pregnancy")
        return preg
    return assert_pregnancy_access(db, user, preg, "update")

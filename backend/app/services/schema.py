from __future__ import annotations

import logging
import secrets

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Facility, LoginChallenge, PasskeyCredential, Role, ScopeType, User, UserScope, VisitDetail

log = logging.getLogger("perinatal.schema")
DEMO_ADMIN = "demo.admin"
DEMO_ADMIN_NAME = "Admin — tizim boshqaruvchisi"
DEMO_FACILITY_ADMIN = "demo.facility_admin"
DEMO_FACILITY_ADMIN_NAME = "Muassasa administratori (namoyish)"
_NEW_USER_COLUMNS = ("account_status", "live_role", "email_enc", "email_hash", "phone_enc", "phone_hash",
                     "google_sub_hash", "two_step", "totp_secret_enc", "totp_enabled", "totp_last_counter",
                     "recovery_codes", "last_login_at")


def ensure_schema(engine: Engine) -> list[str]:
    """Idempotent. Returns what was added (table / column names)."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return []  # database not seeded yet: the seed script creates everything
    added: list[str] = []
    for model in (PasskeyCredential, LoginChallenge, VisitDetail):
        if not insp.has_table(model.__tablename__):
            model.__table__.create(engine, checkfirst=True)
            added.append(model.__tablename__)
    have = {c["name"] for c in insp.get_columns("users")}
    with engine.begin() as con:
        for name in _NEW_USER_COLUMNS:
            if name not in have:
                col = User.__table__.c[name]
                con.execute(text(f"ALTER TABLE users ADD COLUMN {name} {col.type.compile(engine.dialect)}"))
                added.append(f"users.{name}")
        for name in ("email_hash", "phone_hash", "google_sub_hash"):
            con.execute(text(f"CREATE INDEX IF NOT EXISTS ix_users_{name} ON users ({name})"))
        if insp.has_table("facilities"):
            if "admin_user_id" not in {c["name"] for c in insp.get_columns("facilities")}:
                con.execute(text("ALTER TABLE facilities ADD COLUMN admin_user_id VARCHAR(36)"))
                added.append("facilities.admin_user_id")
            # one administrator per facility (single column) and one facility per administrator (unique index)
            con.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_facilities_admin_user_id ON facilities (admin_user_id)"))
    if added:
        log.info("account schema: added %s", ", ".join(added))
    return added


def ensure_demo_admin(db: Session) -> User | None:
    """Demo persona "admin" for /live/login (only meaningful where the other demo personas exist)."""
    if db.execute(select(User.id).where(User.username == "demo.leader")).first() is None:
        return None
    u = db.execute(select(User).where(User.username == DEMO_ADMIN)).scalar_one_or_none()
    if u is None:
        u = User(username=DEMO_ADMIN, full_name=DEMO_ADMIN_NAME, role=Role.NATIONAL_ADMIN, live_role="admin",
                 password_hash=hash_password(secrets.token_urlsafe(24)))  # nobody signs in with this password
        db.add(u)
        db.flush()
        db.add(UserScope(user_id=u.id, scope_type=ScopeType.NATIONAL))
        db.commit()
    return u


def ensure_demo_facility_admin(db: Session) -> User | None:
    """Demo persona "facility_admin" for /live/login: the administrator of the demo nurse's polyclinic."""
    nurse = db.execute(select(User).where(User.username == "demo.nurse")).scalar_one_or_none()
    fac_id = next((sc.facility_id for sc in nurse.scopes if sc.facility_id), None) if nurse else None
    fac = db.get(Facility, fac_id) if fac_id else None
    if fac is None:
        return None
    u = db.execute(select(User).where(User.username == DEMO_FACILITY_ADMIN)).scalar_one_or_none()
    if u is None:
        u = User(username=DEMO_FACILITY_ADMIN, full_name=DEMO_FACILITY_ADMIN_NAME, role=Role.FACILITY_MANAGER,
                 live_role="facility_admin", account_status="active",
                 password_hash=hash_password(secrets.token_urlsafe(24)))  # nobody signs in with this password
        db.add(u)
        db.flush()
        db.add(UserScope(user_id=u.id, scope_type=ScopeType.FACILITY, facility_id=fac.id))
    u.full_name = DEMO_FACILITY_ADMIN_NAME
    if fac.admin_user_id is None:
        fac.admin_user_id = u.id
    db.commit()
    return u


DEMO_PINFL_PREFIX = "000000000"


def ensure_demo_pinfl(db: Session, per_district: int = 3) -> int:
    """Gives a few synthetic mothers obviously fake identifiers (start with nine zeros, never a real JSHSHIR)
    so the cross-district lookup can be demonstrated. Only synthetic records are touched; idempotent."""
    from app.core.security import encrypt, keyed_hash
    from app.models import Mother

    if db.execute(select(Mother.id).where(Mother.pinfl_hash.is_not(None), Mother.is_synthetic.is_(True)).limit(1)).first():
        return 0
    rows = db.execute(select(Mother).where(Mother.is_synthetic.is_(True), Mother.pinfl_hash.is_(None))
                      .order_by(Mother.district_code, Mother.local_code)).scalars()
    seen: dict[str, int] = {}
    n = 0
    for m in rows:
        k = m.district_code or "-"
        if seen.get(k, 0) >= per_district:
            continue
        seen[k] = seen.get(k, 0) + 1
        n += 1
        pinfl = f"{DEMO_PINFL_PREFIX}{n:05d}"
        m.pinfl_enc, m.pinfl_hash = encrypt(pinfl), keyed_hash(pinfl)
    db.commit()
    if n:
        log.info("demo identifiers: %d synthetic mothers", n)
    return n

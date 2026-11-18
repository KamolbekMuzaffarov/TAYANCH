from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import IdMixin, TimestampMixin, UTCDateTime


class Role(str, enum.Enum):
    NATIONAL_ADMIN = "national_admin"
    REGIONAL_ADMIN = "regional_admin"
    DISTRICT_ADMIN = "district_admin"
    FACILITY_ADMIN = "facility_admin"
    OBGYN = "obgyn"
    NEONATOLOGIST = "neonatologist"
    OVABMU_SPECIALIST = "ovabmu_specialist"
    LAB_OPERATOR = "lab_operator"
    AUDITOR = "auditor"
    DATA_STEWARD = "data_steward"
    PATRONAGE_NURSE = "patronage_nurse"  # home-visiting nurse (live layer)
    # Facility administrator of the live layer: manages the staff accounts of exactly one facility and sees
    # staff + aggregate counts only. Deliberately a separate role from FACILITY_ADMIN, which has clinical
    # permissions (alerts) in the older API - this one has no permission there at all.
    FACILITY_MANAGER = "facility_manager"
    PENDING = "pending"  # self-registered, no role assigned by an admin yet: no permission anywhere


class ScopeType(str, enum.Enum):
    NATIONAL = "national"
    REGION = "region"
    DISTRICT = "district"
    FACILITY = "facility"


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(300))
    # Explicit, audited permission for a national admin to open individual clinical records.
    clinical_record_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_logins: Mapped[int] = mapped_column(default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # ---- self-service accounts (docs/AUTH_ADMIN_CONTRACT.md). All nullable: seeded users keep NULLs.
    account_status: Mapped[str | None] = mapped_column(String(12))  # NULL/active | pending | blocked
    live_role: Mapped[str | None] = mapped_column(String(20))  # admin|leader|facility_admin|specialist|nurse|pending; NULL = from `role`
    email_enc: Mapped[str | None] = mapped_column(String(600))
    email_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    phone_enc: Mapped[str | None] = mapped_column(String(300))
    phone_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    google_sub_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    two_step: Mapped[bool | None] = mapped_column(Boolean)
    totp_secret_enc: Mapped[str | None] = mapped_column(String(300))
    totp_enabled: Mapped[bool | None] = mapped_column(Boolean)
    totp_last_counter: Mapped[int | None] = mapped_column(Integer)
    recovery_codes: Mapped[list | None] = mapped_column(JSON)  # keyed hashes only
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    scopes: Mapped[list["UserScope"]] = relationship(back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class UserScope(IdMixin, Base):
    __tablename__ = "user_scopes"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_type: Mapped[ScopeType] = mapped_column(Enum(ScopeType, native_enum=False, length=16), nullable=False)
    region_soato: Mapped[str | None] = mapped_column(String(8))
    district_code: Mapped[str | None] = mapped_column(String(32))
    facility_id: Mapped[str | None] = mapped_column(ForeignKey("facilities.id"))
    user: Mapped[User] = relationship(back_populates="scopes")


class AuthSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"),)


class PasskeyCredential(IdMixin, TimestampMixin, Base):
    """WebAuthn public-key credential (public key only; the private key never leaves the authenticator)."""
    __tablename__ = "passkey_credentials"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_id: Mapped[str] = mapped_column(String(700), nullable=False, unique=True)  # base64url
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    alg: Mapped[int] = mapped_column(Integer, nullable=False)  # COSE: -7 ES256, -257 RS256
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    rp_id: Mapped[str] = mapped_column(String(120), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class LoginChallenge(IdMixin, Base):
    """Short-lived sign-in state: one-time code, second-factor ticket or WebAuthn challenge. Secrets are hashed."""
    __tablename__ = "login_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # sha256 of the public handle
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # otp|ticket|pk_reg|pk_login
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    identifier_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    channel: Mapped[str | None] = mapped_column(String(8))
    mode: Mapped[str | None] = mapped_column(String(10))
    code_hash: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)  # encrypted identifier / name, WebAuthn challenge, rp id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

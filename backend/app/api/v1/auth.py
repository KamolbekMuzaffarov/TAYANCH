from __future__ import annotations

from datetime import timedelta

import pyotp
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.deps import current_user, rate_limiter
from app.core.errors import AppError
from app.core.permissions import PERMISSIONS
from app.core.security import (create_access_token, decrypt, new_refresh_token, token_hash, utcnow, verify_password)
from app.db import get_db
from app.models import AuthSession, User
from app.schemas import LoginIn, MeOut, RefreshIn, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])
LOCK_AFTER = 5


def _issue(db: Session, user: User, mfa_ok: bool) -> TokenOut:
    s = get_settings()
    refresh = new_refresh_token()
    sess = AuthSession(user_id=user.id, refresh_token_hash=token_hash(refresh), mfa_verified=mfa_ok,
                       expires_at=utcnow() + timedelta(days=s.REFRESH_TOKEN_DAYS))
    db.add(sess)
    db.flush()
    return TokenOut(access_token=create_access_token(user.id, sess.id, user.role.value), refresh_token=refresh,
                    expires_in=s.ACCESS_TOKEN_MINUTES * 60)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    client = request.client.host if request.client else "unknown"
    rate_limiter.hit(f"login:{client}", s.LOGIN_RATE_LIMIT_PER_MINUTE)
    rate_limiter.hit(f"login-user:{body.username.lower()}", s.LOGIN_RATE_LIMIT_PER_MINUTE)
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    generic = AppError(401, "invalid_credentials", "Login yoki parol noto‘g‘ri / Invalid credentials")
    if user is None or not user.is_active:
        raise generic
    if user.locked_until and user.locked_until > utcnow():
        raise AppError(423, "account_locked", "Account temporarily locked")
    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= LOCK_AFTER:
            user.locked_until, user.failed_logins = utcnow() + timedelta(minutes=15), 0
        write_audit(db, actor=user, action="auth.login", resource_type="user", resource_id=user.id, outcome="failure")
        raise generic
    mfa_ok = not user.mfa_enabled
    if user.mfa_enabled:
        secret = decrypt(user.mfa_secret_enc)
        if not body.otp or not secret or not pyotp.TOTP(secret).verify(body.otp, valid_window=1):
            write_audit(db, actor=user, action="auth.mfa", resource_type="user", resource_id=user.id, outcome="failure")
            raise AppError(401, "mfa_required", "Valid one-time code required")
        mfa_ok = True
    user.failed_logins, user.locked_until = 0, None
    tok = _issue(db, user, mfa_ok)
    write_audit(db, actor=user, action="auth.login", resource_type="user", resource_id=user.id,
                request_id=request.state.request_id)
    return tok


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    sess = db.execute(select(AuthSession).where(AuthSession.refresh_token_hash == token_hash(body.refresh_token))).scalar_one_or_none()
    if sess is None or sess.revoked_at is not None or sess.expires_at < utcnow():
        raise AppError(401, "unauthorized", "Invalid refresh token")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise AppError(401, "unauthorized", "User inactive")
    sess.revoked_at = utcnow()  # rotation: old refresh token is single-use
    tok = _issue(db, user, sess.mfa_verified)
    db.commit()
    return tok


@router.post("/logout", status_code=204)
def logout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sess = db.get(AuthSession, request.state.session_id)
    sess.revoked_at = utcnow()
    write_audit(db, actor=user, action="auth.logout", resource_type="session", resource_id=sess.id)


@router.post("/logout-all", status_code=204)
def logout_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for sess in db.execute(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))).scalars():
        sess.revoked_at = utcnow()
    write_audit(db, actor=user, action="auth.logout_all", resource_type="user", resource_id=user.id)


me_router = APIRouter(tags=["auth"])


@me_router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)):
    perms = sorted(p for p, roles in PERMISSIONS.items() if user.role in roles)
    return MeOut(id=user.id, username=user.username, full_name=user.full_name, role=user.role.value,
                 mfa_enabled=user.mfa_enabled, permissions=perms,
                 scopes=[{"scope_type": s.scope_type.value, "region_soato": s.region_soato, "district_code": s.district_code,
                          "facility_id": s.facility_id} for s in user.scopes])

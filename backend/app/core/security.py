from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- tokens
def create_access_token(user_id: str, session_id: str, role: str) -> str:
    s = get_settings()
    now = utcnow()
    payload = {"sub": user_id, "sid": session_id, "role": role, "typ": "access", "iat": now,
               "exp": now + timedelta(minutes=s.ACCESS_TOKEN_MINUTES), "jti": uuid.uuid4().hex}
    return jwt.encode(payload, s.JWT_SECRET, algorithm=s.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.JWT_SECRET, algorithms=[s.JWT_ALGORITHM])


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- encryption
def _fernet() -> Fernet:
    s = get_settings()
    key = s.ENCRYPTION_KEY
    if not key:  # dev/test only (production config validator forbids empty key)
        key = base64.urlsafe_b64encode(hashlib.sha256(("enc:" + s.JWT_SECRET).encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def keyed_hash(value: str) -> str:
    """Deterministic HMAC for duplicate detection (e.g. PINFL) without storing the plaintext."""
    s = get_settings()
    return hmac.new(("idx:" + s.JWT_SECRET).encode(), value.strip().encode(), hashlib.sha256).hexdigest()

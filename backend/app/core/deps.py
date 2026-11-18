from __future__ import annotations

import time
from collections import defaultdict, deque

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import decode_token, utcnow
from app.db import get_db
from app.models import AuthSession, User

bearer = HTTPBearer(auto_error=False)


def current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise AppError(401, "unauthorized", "Authentication required")
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise AppError(401, "unauthorized", "Invalid or expired token")
    if payload.get("typ") != "access":
        raise AppError(401, "unauthorized", "Invalid token type")
    sess = db.get(AuthSession, payload.get("sid"))
    if sess is None or sess.revoked_at is not None or sess.expires_at < utcnow():
        raise AppError(401, "unauthorized", "Session revoked or expired")
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise AppError(401, "unauthorized", "User inactive")
    if (user.account_status or "active") != "active" or user.live_role == "pending":
        raise AppError(403, "account_pending", "Hisob hali tasdiqlanmagan yoki bloklangan")
    if user.live_role == "facility_admin":
        # facility administrators manage staff accounts only (/facility/*): no clinical or registry API
        raise AppError(403, "forbidden", "Muassasa administratori uchun bu boʻlim yopiq")
    if user.mfa_enabled and not sess.mfa_verified:
        raise AppError(401, "mfa_required", "MFA verification required")
    request.state.user_id = user.id
    request.state.session_id = sess.id
    return user


class RateLimiter:
    """In-process sliding window. Production: back with Redis (REDIS_URL) behind the same interface."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_s: int = 60) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            raise AppError(429, "rate_limited", "Too many requests, try again later")
        q.append(now)

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = RateLimiter()

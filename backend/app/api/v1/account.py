from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from datetime import timedelta
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
import jwt
import pyotp
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.v1 import live
from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.deps import bearer, rate_limiter
from app.core.errors import AppError
from app.core.security import decrypt, encrypt, hash_password, keyed_hash, utcnow
from app.db import get_db
from app.models import AuthSession, LoginChallenge, PasskeyCredential, Role, ScopeType, User, UserScope
from app.services import delivery, webauthn
from app.services.live_bus import iso
from app.services.live_engine import geo, write_tx

router = APIRouter(prefix="/account", tags=["account"])
log = logging.getLogger("perinatal.account")

WINDOW = 600  # seconds, for the sign-in rate limits
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]{1,64}@[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$")
BAD_CODE = "Kod notoʻgʻri yoki muddati oʻtgan"
RP_NAME = "Perinatal AI"


# ------------------------------------------------------------------ small helpers
def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def normalise(channel: str, identifier: str) -> str:
    """E-mail -> lower case; Uzbek phone -> +998XXXXXXXXX. 422 on a malformed value (format only, no lookup)."""
    raw = (identifier or "").strip()
    if channel == "email":
        v = raw.lower()
        if len(v) <= 254 and EMAIL_RE.match(v):
            return v
        raise AppError(422, "invalid_identifier", "Elektron pochta manzili notoʻgʻri")
    digits = re.sub(r"[\s\-().]", "", raw)
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.isdigit():
        if len(digits) == 9:
            digits = "998" + digits
        if len(digits) == 12 and digits.startswith("998"):
            return "+" + digits
    raise AppError(422, "invalid_identifier", "Telefon raqami +998XXXXXXXXX koʻrinishida boʻlishi kerak")


def mask(channel: str, value: str | None) -> str | None:
    if not value:
        return None
    if channel == "email":
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}"
    return f"{value[:6]}***{value[-2:]}"


def ident_hash(channel: str, value: str) -> str:
    return keyed_hash(f"{channel}:{value}")


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _code_hash(token: str, code: str) -> str:
    return keyed_hash(f"otp:{token}:{code}")


def _eq(a: str | None, b: str | None) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def _audit(db: Session, user: User | None, action: str, request: Request, detail: dict | None = None,
           outcome: str = "success") -> None:
    write_audit(db, actor=user, action=action, resource_type="user", resource_id=user.id if user else None,
                request_id=_rid(request), detail=detail, purpose="account", outcome=outcome, commit=False)


def _audit_now(user: User | None, action: str, request: Request, detail: dict | None = None,
               outcome: str = "success") -> None:
    try:
        with write_tx() as db:
            _audit(db, user, action, request, detail, outcome)
    except Exception:  # noqa: BLE001 - an audit write must never turn a refusal into a 500
        pass


def account_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    """Any signed-in account, including `pending` ones (they may manage their own security and wait)."""
    return live._authenticate(request, creds.credentials if creds else None, allow_pending=True,
                              allow_facility_admin=True)


def _locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > utcnow())


def _usable(user: User | None) -> bool:
    return bool(user and user.is_active and user.account_status != "blocked" and not _locked(user))


def _fail(db: Session, user: User | None) -> None:
    if user is None:
        return
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= get_settings().ACCOUNT_LOCK_AFTER:
        user.locked_until, user.failed_logins = utcnow() + timedelta(minutes=15), 0


def _new_challenge(db: Session, kind: str, *, user_id: str | None = None, token: str | None = None, **kw) -> str:
    token = token or secrets.token_urlsafe(32)
    now = utcnow()
    db.add(LoginChallenge(token_hash=_th(token), kind=kind, user_id=user_id, created_at=now,
                          expires_at=now + timedelta(seconds=get_settings().OTP_TTL_SECONDS), **kw))
    return token


def _get_challenge(db: Session, token: str | None, kind: str) -> LoginChallenge | None:
    if not token or not isinstance(token, str) or len(token) > 200:
        return None
    ch = db.execute(select(LoginChallenge).where(LoginChallenge.token_hash == _th(token),
                                                 LoginChallenge.kind == kind)).scalar_one_or_none()
    if ch is None or ch.consumed_at is not None or ch.expires_at < utcnow():
        return None
    return ch


def _sweep(db: Session) -> None:
    db.execute(delete(LoginChallenge).where(LoginChallenge.expires_at < utcnow() - timedelta(hours=1)))


def _passkeys(db: Session, user_id: str) -> list[PasskeyCredential]:
    return list(db.execute(select(PasskeyCredential).where(PasskeyCredential.user_id == user_id)
                           .order_by(PasskeyCredential.created_at)).scalars())


def _passkey_items(db: Session, user_id: str) -> list[dict]:
    return [{"id": p.id, "label": p.label, "created_at": iso(p.created_at)} for p in _passkeys(db, user_id)]


def _methods(db: Session, user: User) -> list[str]:
    out = ["totp"] if user.totp_enabled and user.totp_secret_enc else []
    if _passkeys(db, user.id):
        out.append("passkey")
    return out


def _finish(db: Session, user: User, request: Request, method: str, *, strong: bool = False) -> dict:
    """After a verified first factor: a session, or a second-factor ticket when two-step protection is on.
    `strong` = the first factor was a passkey with user verification (possession + biometrics / device PIN)."""
    methods = _methods(db, user)
    if user.two_step and methods and not strong:
        ticket = _new_challenge(db, "ticket", user_id=user.id, payload={"first": method})
        _audit(db, user, "account.login", request, {"method": method, "step": "first_factor_ok"})
        return {"second_factor_required": True, "ticket": ticket, "methods": methods}
    return _session(db, user, request, method)


def _session(db: Session, user: User, request: Request, method: str) -> dict:
    geo.load(db)
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, utcnow()
    db.flush()
    out = live.issue_session(db, user)
    _audit(db, user, "account.login", request, {"method": method, "role": live.role_of(user)})
    return out


# ------------------------------------------------------------------ registration
def _bootstrap_admin(db: Session, channel: str, identifier: str) -> bool:
    """Contract: the first account registering with ADMIN_BOOTSTRAP_IDENTIFIER becomes the admin; when that is
    unset and this is not production, the very first registered account does."""
    s = get_settings()
    boot = s.ADMIN_BOOTSTRAP_IDENTIFIER.strip()
    if boot:
        try:
            want = normalise("email" if "@" in boot else "phone", boot)
        except AppError:
            return False
        return _eq(want, identifier) and (channel == "email") == ("@" in boot)
    if s.is_production:
        return False
    n = db.execute(select(func.count(User.id)).where(or_(User.email_hash.is_not(None), User.phone_hash.is_not(None),
                                                         User.google_sub_hash.is_not(None)))).scalar() or 0
    return n == 0


def _create_user(db: Session, request: Request, *, channel: str, identifier: str, full_name: str,
                 google_sub: str | None = None) -> User:
    admin = _bootstrap_admin(db, channel, identifier)
    u = User(username=f"u.{secrets.token_hex(10)}", full_name=full_name.strip()[:200] or "Foydalanuvchi",
             password_hash=hash_password(secrets.token_urlsafe(24)),  # password sign-in is not offered for these accounts
             role=Role.NATIONAL_ADMIN if admin else Role.PENDING, live_role="admin" if admin else "pending",
             account_status="active" if admin else "pending", two_step=False)
    if channel == "email":
        u.email_enc, u.email_hash = encrypt(identifier), ident_hash("email", identifier)
    else:
        u.phone_enc, u.phone_hash = encrypt(identifier), ident_hash("phone", identifier)
    if google_sub:
        u.google_sub_hash = keyed_hash(f"google:{google_sub}")
    db.add(u)
    db.flush()
    if admin:
        db.add(UserScope(user_id=u.id, scope_type=ScopeType.NATIONAL))
        db.flush()
        db.refresh(u)
    geo.users[u.id] = (u.full_name, u.role.value)
    _audit(db, u, "account.register", request, {"channel": "google" if google_sub else channel,
                                                "role": "admin" if admin else "pending", "bootstrap_admin": admin})
    return u


def _find(db: Session, channel: str, identifier: str) -> User | None:
    col = User.email_hash if channel == "email" else User.phone_hash
    return db.execute(select(User).where(col == ident_hash(channel, identifier))).scalars().first()


# ------------------------------------------------------------------ one-time code
class StartIn(BaseModel):
    model_config = {"extra": "forbid"}
    mode: Literal["register", "login"]
    channel: Literal["email", "phone"]
    identifier: str = Field(min_length=3, max_length=254)
    full_name: str | None = Field(None, min_length=2, max_length=200)


@router.post("/start")
def start(body: StartIn, request: Request, tasks: BackgroundTasks):
    s = get_settings()
    identifier = normalise(body.channel, body.identifier)
    if body.mode == "register" and not (body.full_name or "").strip():
        raise AppError(422, "name_required", "Ism va familiyangizni kiriting")
    ih = ident_hash(body.channel, identifier)
    rate_limiter.hit(f"acct:start:ip:{_ip(request)}", s.OTP_START_PER_IP, WINDOW)
    rate_limiter.hit(f"acct:start:id:{ih}", s.OTP_START_PER_IDENTIFIER, WINDOW)
    provider = delivery.provider_for(body.channel)
    if provider is None and s.is_production:
        raise AppError(503, "delivery_unavailable", "Kod yuborish xizmati sozlanmagan")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    with write_tx() as db:
        _sweep(db)
        user = _find(db, body.channel, identifier)
        # real challenge: an existing usable account (sign-in, also when "register" names an existing account)
        # or a new registration. Everything else gets a challenge that can never verify - same response shape.
        real = _usable(user) if user is not None else body.mode == "register"
        for old in db.execute(select(LoginChallenge).where(LoginChallenge.identifier_hash == ih, LoginChallenge.kind == "otp",
                                                           LoginChallenge.consumed_at.is_(None))).scalars():
            old.consumed_at = utcnow()  # only the newest code is valid
        token = secrets.token_urlsafe(32)
        payload = None
        if real and user is None:
            payload = {"identifier_enc": encrypt(identifier), "full_name_enc": encrypt(body.full_name.strip())}
        _new_challenge(db, "otp", token=token, user_id=user.id if (real and user) else None, identifier_hash=ih,
                       channel=body.channel, mode=("login" if user else "register") if real else "void",
                       code_hash=_code_hash(token, code) if real else None, payload=payload)
        _audit(db, user if real else None, "account.start", request, {"channel": body.channel, "mode": body.mode})
    if provider is not None and real:
        tasks.add_task(delivery.deliver, provider, identifier, code)
    return {"challenge_id": token, "expires_in": s.OTP_TTL_SECONDS, "sent_to": mask(body.channel, identifier),
            "dev_code": code if provider is None else None}


class VerifyIn(BaseModel):
    model_config = {"extra": "forbid"}
    challenge_id: str = Field(min_length=8, max_length=200)
    code: str = Field(min_length=4, max_length=12)


@router.post("/verify")
def verify(body: VerifyIn, request: Request):
    s = get_settings()
    rate_limiter.hit(f"acct:verify:ip:{_ip(request)}", s.ACCOUNT_VERIFY_PER_IP, WINDOW)
    code = body.code.strip()
    with write_tx() as db:
        ch = _get_challenge(db, body.challenge_id, "otp")
        ok, user = False, None
        if ch is not None:
            ch.attempts += 1
            user = db.get(User, ch.user_id) if ch.user_id else None
            # constant-time; a "void" challenge (unknown account) is compared against a value nobody can produce
            expected = ch.code_hash or keyed_hash("void:" + secrets.token_hex(16))
            ok = _eq(expected, _code_hash(body.challenge_id, code)) and ch.code_hash is not None \
                and ch.attempts <= s.OTP_MAX_ATTEMPTS and (user is None or _usable(user))
            if ch.attempts >= s.OTP_MAX_ATTEMPTS and not ok:
                ch.consumed_at = utcnow()
        else:
            _eq(keyed_hash("void"), _code_hash(body.challenge_id, code))
        if ok:
            ch.consumed_at = utcnow()
            if user is None:  # registration
                p = ch.payload or {}
                identifier, name = decrypt(p.get("identifier_enc")), decrypt(p.get("full_name_enc"))
                user = _find(db, ch.channel, identifier) or _create_user(
                    db, request, channel=ch.channel, identifier=identifier, full_name=name or "Foydalanuvchi")
            return _finish(db, user, request, f"code_{ch.channel}")
        _fail(db, user)
        _audit(db, user, "account.login", request, {"method": "code", "reason": "bad_code"}, outcome="failure")
    raise AppError(400, "invalid_code", BAD_CODE)


# ------------------------------------------------------------------ TOTP
def _totp_ok(user: User, code: str) -> bool:
    """RFC 6238, +-1 step, single use (a counter that was already accepted is refused: no replay)."""
    secret = decrypt(user.totp_secret_enc)
    code = re.sub(r"\s", "", code or "")
    if not secret or not re.fullmatch(r"\d{6}", code):
        return False
    totp = pyotp.TOTP(secret)
    now = int(time.time()) // 30
    hit = None
    for counter in (now - 1, now, now + 1):
        if hmac.compare_digest(totp.generate_otp(counter), code):  # no early exit
            hit = counter if hit is None else hit
    if hit is None or hit <= (user.totp_last_counter or 0):
        return False
    user.totp_last_counter = hit
    return True


def _recovery_ok(user: User, code: str) -> bool:
    h = keyed_hash("recovery:" + re.sub(r"[\s\-]", "", (code or "").lower()))
    codes = list(user.recovery_codes or [])
    hit = [c for c in codes if hmac.compare_digest(c, h)]
    if not hit:
        return False
    user.recovery_codes = [c for c in codes if c != hit[0]]  # single use
    return True


class SecondIn(BaseModel):
    model_config = {"extra": "forbid"}
    ticket: str = Field(min_length=8, max_length=200)
    method: Literal["totp", "recovery"] = "totp"
    code: str = Field(min_length=4, max_length=40)


@router.post("/second")
def second(body: SecondIn, request: Request):
    s = get_settings()
    rate_limiter.hit(f"acct:verify:ip:{_ip(request)}", s.ACCOUNT_VERIFY_PER_IP, WINDOW)
    with write_tx() as db:
        ch = _get_challenge(db, body.ticket, "ticket")
        user = db.get(User, ch.user_id) if ch is not None else None
        ok = False
        if ch is not None and user is not None:
            ch.attempts += 1
            if ch.attempts <= s.OTP_MAX_ATTEMPTS and _usable(user):
                ok = _totp_ok(user, body.code) if body.method == "totp" and user.totp_enabled else \
                    _recovery_ok(user, body.code) if body.method == "recovery" else False
            if ch.attempts >= s.OTP_MAX_ATTEMPTS and not ok:
                ch.consumed_at = utcnow()
        if ok:
            ch.consumed_at = utcnow()
            _audit(db, user, "account.second_factor", request, {"method": body.method})
            return _session(db, user, request, f"{(ch.payload or {}).get('first', 'code')}+{body.method}")
        _fail(db, user)
        _audit(db, user, "account.second_factor", request, {"method": body.method, "reason": "bad_code"}, outcome="failure")
    raise AppError(400, "invalid_code", BAD_CODE)


class CodeIn(BaseModel):
    model_config = {"extra": "forbid"}
    code: str = Field(min_length=4, max_length=40)


@router.post("/totp/setup")
def totp_setup(request: Request, user: User = Depends(account_user)):
    rate_limiter.hit(f"acct:sec:{user.id}", 30, WINDOW)
    with write_tx() as db:
        u = db.get(User, user.id)
        if u.totp_enabled:
            raise AppError(409, "totp_already_enabled", "Autentifikator ilovasi allaqachon yoqilgan. Avval uni oʻchiring")
        secret = pyotp.random_base32()
        u.totp_secret_enc, u.totp_last_counter = encrypt(secret), None
        _audit(db, u, "account.totp.setup", request)
        label = decrypt(u.email_enc) or decrypt(u.phone_enc) or u.full_name
    uri = f"otpauth://totp/{quote(RP_NAME)}:{quote(label)}?secret={secret}&issuer={quote(RP_NAME)}&algorithm=SHA1&digits=6&period=30"
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/totp/enable")
def totp_enable(body: CodeIn, request: Request, user: User = Depends(account_user)):
    rate_limiter.hit(f"acct:sec:{user.id}", 30, WINDOW)
    with write_tx() as db:
        u = db.get(User, user.id)
        if u.totp_enabled:
            raise AppError(409, "totp_already_enabled", "Autentifikator ilovasi allaqachon yoqilgan")
        if u.totp_secret_enc and _totp_ok(u, body.code):
            codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(8)]
            u.recovery_codes = [keyed_hash("recovery:" + c.replace("-", "")) for c in codes]
            u.totp_enabled, u.two_step = True, True  # an enabled authenticator switches two-step protection on
            _audit(db, u, "account.totp.enable", request, {"two_step": True})
            return {"ok": True, "recovery_codes": codes, "two_step": True}
        _audit(db, u, "account.totp.enable", request, {"reason": "bad_code"}, outcome="failure")
    raise AppError(400, "invalid_code", BAD_CODE)


@router.post("/totp/disable")
def totp_disable(body: CodeIn, request: Request, user: User = Depends(account_user)):
    rate_limiter.hit(f"acct:sec:{user.id}", 30, WINDOW)
    with write_tx() as db:
        u = db.get(User, user.id)
        if u.totp_enabled and (_totp_ok(u, body.code) or _recovery_ok(u, body.code)):
            u.totp_enabled, u.totp_secret_enc, u.totp_last_counter, u.recovery_codes = False, None, None, None
            if not _passkeys(db, u.id):
                u.two_step = False  # nothing left to ask for as a second step
            _audit(db, u, "account.totp.disable", request, {"two_step": bool(u.two_step)})
            return {"ok": True, "two_step": bool(u.two_step)}
        _audit(db, u, "account.totp.disable", request, {"reason": "bad_code"}, outcome="failure")
    raise AppError(400, "invalid_code", BAD_CODE)


# ------------------------------------------------------------------ passkeys (WebAuthn)
def allowed_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in get_settings().CORS_ORIGINS.split(",") if o.strip() and "*" not in o}


def _origin(request: Request) -> tuple[str, str]:
    """(origin, rp id). The origin must be one of the configured CORS origins; the RP id is its host name."""
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin and request.headers.get("referer"):
        u = urlsplit(request.headers["referer"])
        origin = f"{u.scheme}://{u.netloc}"
    if origin not in allowed_origins():
        raise AppError(400, "origin_not_allowed", "Kirish kaliti faqat tizimning rasmiy manzilida ishlaydi")
    host = urlsplit(origin).hostname
    if not host:
        raise AppError(400, "origin_not_allowed", "Kirish kaliti faqat tizimning rasmiy manzilida ishlaydi")
    return origin, host


PASSKEY_FAIL = AppError(400, "passkey_failed", "Kirish kaliti tasdiqlanmadi. Qayta urinib koʻring")


@router.post("/passkey/register/options")
def passkey_register_options(request: Request, user: User = Depends(account_user)):
    rate_limiter.hit(f"acct:sec:{user.id}", 30, WINDOW)
    origin, rp_id = _origin(request)
    challenge = webauthn.b64u(secrets.token_bytes(32))
    with write_tx() as db:
        for old in db.execute(select(LoginChallenge).where(LoginChallenge.user_id == user.id, LoginChallenge.kind == "pk_reg",
                                                           LoginChallenge.consumed_at.is_(None))).scalars():
            old.consumed_at = utcnow()
        _new_challenge(db, "pk_reg", user_id=user.id, payload={"challenge": challenge, "rp_id": rp_id, "origin": origin})
        existing = [{"type": "public-key", "id": p.credential_id} for p in _passkeys(db, user.id)]
        name = decrypt(user.email_enc) or decrypt(user.phone_enc) or user.full_name
    return {"publicKey": {
        "challenge": challenge, "rp": {"id": rp_id, "name": RP_NAME},
        "user": {"id": webauthn.b64u(user.id.encode()), "name": name, "displayName": user.full_name},
        "pubKeyCredParams": [{"type": "public-key", "alg": webauthn.ES256}, {"type": "public-key", "alg": webauthn.RS256}],
        "authenticatorSelection": {"residentKey": "preferred", "requireResidentKey": False, "userVerification": "required"},
        "excludeCredentials": existing, "timeout": 60000, "attestation": "none"}}


class AttestationResponse(BaseModel):
    model_config = {"extra": "ignore"}
    clientDataJSON: str = Field(max_length=8000)
    attestationObject: str = Field(max_length=20000)


class PasskeyRegisterIn(BaseModel):
    model_config = {"extra": "ignore"}
    id: str = Field(max_length=1400)
    rawId: str = Field(max_length=1400)
    type: Literal["public-key"]
    response: AttestationResponse
    label: str | None = Field(None, max_length=80)


@router.post("/passkey/register/verify")
def passkey_register_verify(body: PasskeyRegisterIn, request: Request, user: User = Depends(account_user)):
    rate_limiter.hit(f"acct:sec:{user.id}", 30, WINDOW)
    reason = "no_challenge"
    with write_tx() as db:
        ch = db.execute(select(LoginChallenge).where(
            LoginChallenge.user_id == user.id, LoginChallenge.kind == "pk_reg", LoginChallenge.consumed_at.is_(None),
            LoginChallenge.expires_at >= utcnow()).order_by(LoginChallenge.created_at.desc())).scalars().first()
        if ch is not None:
            ch.consumed_at = utcnow()  # single use, success or not
            p = ch.payload or {}
            try:
                res = webauthn.verify_registration(
                    client_data_json=webauthn.b64u_dec(body.response.clientDataJSON),
                    attestation_object=webauthn.b64u_dec(body.response.attestationObject),
                    challenge=p["challenge"], rp_id=p["rp_id"], allowed_origins={p["origin"]} & allowed_origins())
                if not hmac.compare_digest(webauthn.b64u_dec(body.rawId), webauthn.b64u_dec(res["credential_id"])):
                    raise webauthn.WebAuthnError("credential_id_mismatch")
                if db.execute(select(PasskeyCredential.id).where(PasskeyCredential.credential_id == res["credential_id"])).first():
                    raise webauthn.WebAuthnError("duplicate_credential")
                n = len(_passkeys(db, user.id))
                if n >= 10:
                    raise webauthn.WebAuthnError("too_many")
                label = (body.label or "").strip() or f"Kirish kaliti {n + 1}"
                db.add(PasskeyCredential(user_id=user.id, credential_id=res["credential_id"], public_key_pem=res["public_key_pem"],
                                         alg=res["alg"], sign_count=res["sign_count"], label=label[:80], rp_id=p["rp_id"]))
                db.flush()
                _audit(db, user, "account.passkey.add", request, {"alg": res["alg"]})
                return {"ok": True, "passkeys": _passkey_items(db, user.id)}
            except webauthn.WebAuthnError as exc:
                reason = str(exc)
        _audit(db, user, "account.passkey.add", request, {"reason": reason}, outcome="failure")
    raise PASSKEY_FAIL


class PasskeyLoginOptionsIn(BaseModel):
    model_config = {"extra": "forbid"}
    identifier: str | None = Field(None, max_length=254)
    ticket: str | None = Field(None, max_length=200)


@router.post("/passkey/login/options")
def passkey_login_options(body: PasskeyLoginOptionsIn, request: Request):
    s = get_settings()
    rate_limiter.hit(f"acct:verify:ip:{_ip(request)}", s.ACCOUNT_VERIFY_PER_IP, WINDOW)
    origin, rp_id = _origin(request)
    challenge = webauthn.b64u(secrets.token_bytes(32))
    allow: list[dict] = []
    with write_tx() as db:
        user_id, payload = None, {"challenge": challenge, "rp_id": rp_id, "origin": origin}
        if body.ticket:
            t = _get_challenge(db, body.ticket, "ticket")
            if t is None:
                raise AppError(400, "invalid_ticket", "Kirish muddati tugadi. Qaytadan boshlang")
            user_id, payload["ticket_hash"] = t.user_id, t.token_hash
            allow = [{"type": "public-key", "id": p.credential_id} for p in _passkeys(db, user_id)]
        elif body.identifier:
            channel = "email" if "@" in body.identifier else "phone"
            identifier = normalise(channel, body.identifier)
            user = _find(db, channel, identifier)
            creds = _passkeys(db, user.id) if _usable(user) else []
            if creds:
                user_id = user.id
                allow = [{"type": "public-key", "id": p.credential_id} for p in creds]
            else:  # unknown account / no passkey: a stable decoy id, so the answer does not reveal which it is
                payload["void"] = True
                allow = [{"type": "public-key", "id": webauthn.b64u(bytes.fromhex(keyed_hash(f"decoy:{channel}:{identifier}")))}]
        state = _new_challenge(db, "pk_login", user_id=user_id, payload=payload)
    return {"publicKey": {"challenge": challenge, "rpId": rp_id, "allowCredentials": allow, "userVerification": "required",
                          "timeout": 60000}, "state": state}


class AssertionResponse(BaseModel):
    model_config = {"extra": "ignore"}
    clientDataJSON: str = Field(max_length=8000)
    authenticatorData: str = Field(max_length=4000)
    signature: str = Field(max_length=4000)
    userHandle: str | None = Field(None, max_length=400)


class PasskeyLoginIn(BaseModel):
    model_config = {"extra": "ignore"}
    state: str = Field(min_length=8, max_length=200)
    id: str = Field(max_length=1400)
    rawId: str | None = Field(None, max_length=1400)
    type: Literal["public-key"]
    response: AssertionResponse


@router.post("/passkey/login/verify")
def passkey_login_verify(body: PasskeyLoginIn, request: Request):
    s = get_settings()
    rate_limiter.hit(f"acct:verify:ip:{_ip(request)}", s.ACCOUNT_VERIFY_PER_IP, WINDOW)
    reason, user = "no_state", None
    with write_tx() as db:
        ch = _get_challenge(db, body.state, "pk_login")
        if ch is not None:
            ch.consumed_at = utcnow()  # single use
            p = ch.payload or {}
            try:
                cred_id = webauthn.b64u(webauthn.b64u_dec(body.rawId or body.id))
                cred = db.execute(select(PasskeyCredential).where(PasskeyCredential.credential_id == cred_id)).scalar_one_or_none()
                if cred is None or p.get("void"):
                    raise webauthn.WebAuthnError("unknown_credential")
                user = db.get(User, cred.user_id)
                if user is None or not user.is_active or user.account_status == "blocked":
                    raise webauthn.WebAuthnError("user_unavailable")
                if ch.user_id and ch.user_id != cred.user_id:
                    raise webauthn.WebAuthnError("credential_of_other_user")
                if body.response.userHandle and not hmac.compare_digest(webauthn.b64u_dec(body.response.userHandle), user.id.encode()):
                    raise webauthn.WebAuthnError("user_handle_mismatch")
                if cred.rp_id != p["rp_id"]:
                    raise webauthn.WebAuthnError("rp_id_mismatch")
                cred.sign_count = webauthn.verify_assertion(
                    client_data_json=webauthn.b64u_dec(body.response.clientDataJSON),
                    authenticator_data=webauthn.b64u_dec(body.response.authenticatorData),
                    signature=webauthn.b64u_dec(body.response.signature), challenge=p["challenge"], rp_id=p["rp_id"],
                    allowed_origins={p["origin"]} & allowed_origins(), public_key_pem=cred.public_key_pem, alg=cred.alg,
                    stored_sign_count=cred.sign_count)
                cred.last_used_at = utcnow()
                if p.get("ticket_hash"):  # second factor of a code / Google sign-in
                    t = db.execute(select(LoginChallenge).where(LoginChallenge.token_hash == p["ticket_hash"],
                                                                LoginChallenge.kind == "ticket")).scalar_one_or_none()
                    if t is None or t.consumed_at is not None or t.expires_at < utcnow() or t.user_id != user.id:
                        raise webauthn.WebAuthnError("ticket_expired")
                    t.consumed_at = utcnow()
                    _audit(db, user, "account.second_factor", request, {"method": "passkey"})
                    return _session(db, user, request, f"{(t.payload or {}).get('first', 'code')}+passkey")
                return _finish(db, user, request, "passkey", strong=True)
            except webauthn.WebAuthnError as exc:
                reason = str(exc)
                db.rollback()
                ch = _get_challenge(db, body.state, "pk_login")
                if ch is not None:
                    ch.consumed_at = utcnow()
                user = db.get(User, user.id) if user is not None else None
        # no lockout counter here: a signature cannot be guessed, and counting would let anyone lock a victim out
        _audit(db, user, "account.login", request, {"method": "passkey", "reason": reason}, outcome="failure")
    raise PASSKEY_FAIL


@router.delete("/passkey/{passkey_id}")
def passkey_delete(passkey_id: str, request: Request, user: User = Depends(account_user)):
    rate_limiter.hit(f"acct:sec:{user.id}", 30, WINDOW)
    with write_tx() as db:
        u = db.get(User, user.id)
        cred = db.get(PasskeyCredential, passkey_id) if len(passkey_id) <= 36 else None
        if cred is None or cred.user_id != u.id:
            raise AppError(404, "not_found", "Kirish kaliti topilmadi")
        if u.two_step and not u.totp_enabled and len(_passkeys(db, u.id)) == 1:
            raise AppError(422, "last_second_factor", "Bu oxirgi ikkinchi bosqich usuli. Avval ikki bosqichli himoyani oʻchiring")
        db.delete(cred)
        db.flush()
        _audit(db, u, "account.passkey.delete", request)
        return {"ok": True, "passkeys": _passkey_items(db, u.id)}


# ------------------------------------------------------------------ Google
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_jwks: dict = {"at": 0.0, "keys": {}}
_jwks_lock = threading.Lock()


def _google_key(kid: str):
    """Google's signing keys (JWKS), cached for an hour; refetched once when an unknown key id appears."""
    with _jwks_lock:
        now = time.time()
        if kid not in _jwks["keys"] and now - _jwks["at"] > 30 or now - _jwks["at"] > 3600:
            r = httpx.get(GOOGLE_JWKS_URL, timeout=5)
            r.raise_for_status()
            _jwks["keys"] = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(k) for k in r.json().get("keys", [])
                             if k.get("kty") == "RSA" and k.get("kid")}
            _jwks["at"] = now
        return _jwks["keys"].get(kid)


def verify_google_token(credential: str) -> dict:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(credential)
        if header.get("alg") != "RS256":
            raise jwt.InvalidTokenError("alg")
        key = _google_key(header.get("kid") or "")
        if key is None:
            raise jwt.InvalidTokenError("kid")
        claims = jwt.decode(credential, key, algorithms=["RS256"], audience=s.GOOGLE_CLIENT_ID, issuer=list(GOOGLE_ISSUERS),
                            options={"require": ["exp", "iat", "aud", "iss", "sub"]}, leeway=30)
    except jwt.PyJWTError:
        raise AppError(401, "google_invalid", "Google tasdiqlashi oʻtmadi")
    except httpx.HTTPError:
        raise AppError(503, "google_unavailable", "Google kalitlarini olishning imkoni boʻlmadi. Keyinroq urinib koʻring")
    if not claims.get("email") or claims.get("email_verified") not in (True, "true"):
        raise AppError(401, "google_invalid", "Google hisobidagi pochta tasdiqlanmagan")
    return claims


@router.get("/google/config")
def google_config():
    cid = get_settings().GOOGLE_CLIENT_ID.strip()
    return {"enabled": bool(cid), "client_id": cid or None}


class GoogleIn(BaseModel):
    model_config = {"extra": "forbid"}
    credential: str = Field(min_length=20, max_length=6000)


@router.post("/google/verify")
def google_verify(body: GoogleIn, request: Request):
    s = get_settings()
    if not s.GOOGLE_CLIENT_ID.strip():
        raise AppError(404, "google_disabled", "Google orqali kirish yoqilmagan")
    rate_limiter.hit(f"acct:verify:ip:{_ip(request)}", s.ACCOUNT_VERIFY_PER_IP, WINDOW)
    try:
        claims = verify_google_token(body.credential)
    except AppError:
        _audit_now(None, "account.google", request, {"reason": "invalid_token"}, outcome="failure")
        raise
    email = normalise("email", str(claims["email"]))
    sub_hash = keyed_hash(f"google:{claims['sub']}")
    with write_tx() as db:
        user = db.execute(select(User).where(User.google_sub_hash == sub_hash)).scalars().first() or _find(db, "email", email)
        if user is None:
            user = _create_user(db, request, channel="email", identifier=email, google_sub=str(claims["sub"]),
                                full_name=str(claims.get("name") or email.split("@")[0]))
        elif not user.is_active or user.account_status == "blocked" or _locked(user):
            _audit(db, user, "account.google", request, {"reason": "blocked"}, outcome="denied")
            user = None
        else:
            user.google_sub_hash = sub_hash  # link Google to the account that owns this verified address
        if user is not None:
            _audit(db, user, "account.google", request)
            return _finish(db, user, request, "google")
    raise AppError(403, "account_blocked", "Hisob bloklangan yoki vaqtincha yopilgan")


# ------------------------------------------------------------------ security settings
def _security(db: Session, u: User, session_id: str | None) -> dict:
    sessions = db.execute(select(AuthSession).where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None),
                                                    AuthSession.expires_at > utcnow())
                          .order_by(AuthSession.created_at.desc()).limit(20)).scalars()
    return {"two_step": bool(u.two_step),
            "methods": {"email": mask("email", decrypt(u.email_enc)), "phone": mask("phone", decrypt(u.phone_enc)),
                        "google": bool(u.google_sub_hash), "totp": bool(u.totp_enabled),
                        "passkeys": _passkey_items(db, u.id)},
            "sessions": [{"id": x.id, "created_at": iso(x.created_at), "current": x.id == session_id} for x in sessions]}


@router.get("/security")
def security(request: Request, user: User = Depends(account_user), db: Session = Depends(get_db)):
    return _security(db, db.get(User, user.id), request.state.session_id)


class SecurityIn(BaseModel):
    model_config = {"extra": "forbid"}
    two_step: bool


@router.post("/security")
def security_update(body: SecurityIn, request: Request, user: User = Depends(account_user)):
    rate_limiter.hit(f"acct:sec:{user.id}", 30, WINDOW)
    with write_tx() as db:
        u = db.get(User, user.id)
        if body.two_step and not _methods(db, u):
            raise AppError(422, "second_factor_missing",
                           "Avval autentifikator ilovasini yoki kirish kalitini (passkey) qoʻshing")
        if bool(u.two_step) != body.two_step:
            u.two_step = body.two_step
            _audit(db, u, "account.two_step", request, {"two_step": body.two_step})
        db.flush()
        return _security(db, u, request.state.session_id)


@router.post("/logout", status_code=204)
def logout(request: Request, user: User = Depends(account_user)):
    with write_tx() as db:
        sess = db.get(AuthSession, request.state.session_id)
        if sess is not None and sess.revoked_at is None:
            sess.revoked_at = utcnow()
        _audit(db, user, "account.logout", request)
    return Response(status_code=204)


@router.get("/me")
def me(user: User = Depends(account_user), db: Session = Depends(get_db)):
    geo.load(db)
    return live._user_payload(user)

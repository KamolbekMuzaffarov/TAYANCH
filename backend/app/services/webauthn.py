from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

ES256, RS256 = -7, -257
FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40


class WebAuthnError(Exception):
    """Carries a short machine reason (never user data)."""


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 20000:
        raise WebAuthnError("bad_base64")
    try:
        return base64.urlsafe_b64decode(value.replace("+", "-").replace("/", "_") + "=" * (-len(value) % 4))
    except Exception:  # noqa: BLE001
        raise WebAuthnError("bad_base64")


# ------------------------------------------------------------------ CBOR (definite-length subset)
def cbor_decode(data: bytes, pos: int = 0, depth: int = 0):
    """Returns (value, next_pos). Supports ints, byte/text strings, arrays, maps, simple values, floats."""
    if depth > 8:
        raise WebAuthnError("cbor_depth")
    try:
        ib = data[pos]
        major, info = ib >> 5, ib & 0x1F
        pos += 1
        if major == 7:
            if info in (20, 21):
                return info == 21, pos
            if info in (22, 23):
                return None, pos
            if info == 25:
                return struct.unpack(">e", data[pos:pos + 2])[0], pos + 2
            if info == 26:
                return struct.unpack(">f", data[pos:pos + 4])[0], pos + 4
            if info == 27:
                return struct.unpack(">d", data[pos:pos + 8])[0], pos + 8
            raise WebAuthnError("cbor_unsupported")
        if info < 24:
            n = info
        elif info in (24, 25, 26, 27):
            size = 1 << (info - 24)
            if pos + size > len(data):
                raise WebAuthnError("cbor_truncated")
            n = int.from_bytes(data[pos:pos + size], "big")
            pos += size
        else:
            raise WebAuthnError("cbor_indefinite")
        if major == 0:
            return n, pos
        if major == 1:
            return -1 - n, pos
        if major in (2, 3):
            if pos + n > len(data):
                raise WebAuthnError("cbor_truncated")
            raw = data[pos:pos + n]
            return (raw if major == 2 else raw.decode("utf-8")), pos + n
        if n > 64:
            raise WebAuthnError("cbor_too_large")
        if major == 4:
            out = []
            for _ in range(n):
                v, pos = cbor_decode(data, pos, depth + 1)
                out.append(v)
            return out, pos
        if major == 5:
            m = {}
            for _ in range(n):
                k, pos = cbor_decode(data, pos, depth + 1)
                v, pos = cbor_decode(data, pos, depth + 1)
                if isinstance(k, (int, str)):
                    m[k] = v
            return m, pos
        if major == 6:  # tag: ignore the tag, return the content
            return cbor_decode(data, pos, depth + 1)
    except (IndexError, struct.error, UnicodeDecodeError):
        raise WebAuthnError("cbor_truncated")
    raise WebAuthnError("cbor_unsupported")


# ------------------------------------------------------------------ parsing helpers
@dataclass
class AuthData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    credential_id: bytes | None = None
    cose_key: dict | None = None


def parse_auth_data(raw: bytes, expect_credential: bool) -> AuthData:
    if len(raw) < 37:
        raise WebAuthnError("auth_data_short")
    ad = AuthData(raw[:32], raw[32], int.from_bytes(raw[33:37], "big"))
    if expect_credential:
        if not ad.flags & FLAG_AT or len(raw) < 55:
            raise WebAuthnError("no_attested_credential")
        n = int.from_bytes(raw[53:55], "big")
        if n == 0 or n > 1023 or len(raw) < 55 + n:
            raise WebAuthnError("credential_id_length")
        ad.credential_id = raw[55:55 + n]
        key, _ = cbor_decode(raw, 55 + n)
        if not isinstance(key, dict):
            raise WebAuthnError("cose_key")
        ad.cose_key = key
    return ad


def cose_to_public_key(cose: dict) -> tuple[int, str]:
    """COSE_Key -> (alg, SubjectPublicKeyInfo PEM)."""
    try:
        kty, alg = cose.get(1), cose.get(3)
        if kty == 2 and alg == ES256 and cose.get(-1) == 1:
            x, y = cose[-2], cose[-3]
            if len(x) != 32 or len(y) != 32:
                raise WebAuthnError("ec_point")
            key = ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"),
                                                ec.SECP256R1()).public_key()  # validates that the point is on the curve
        elif kty == 3 and alg == RS256:
            n, e = int.from_bytes(cose[-1], "big"), int.from_bytes(cose[-2], "big")
            if n.bit_length() < 2048:
                raise WebAuthnError("rsa_too_small")
            key = rsa.RSAPublicNumbers(e, n).public_key()
        else:
            raise WebAuthnError("unsupported_algorithm")
    except (KeyError, TypeError, ValueError):
        raise WebAuthnError("cose_key")
    pem = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return alg, pem


def check_client_data(raw: bytes, *, expected_type: str, challenge: str, allowed_origins: set[str]) -> str:
    """Returns the (validated) origin."""
    try:
        cd = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise WebAuthnError("client_data_json")
    if not isinstance(cd, dict) or cd.get("type") != expected_type:
        raise WebAuthnError("client_data_type")
    got = cd.get("challenge")
    if not isinstance(got, str) or not hmac.compare_digest(got.rstrip("=").encode(), challenge.encode()):
        raise WebAuthnError("challenge_mismatch")
    origin = cd.get("origin")
    if not isinstance(origin, str) or origin.rstrip("/") not in allowed_origins:
        raise WebAuthnError("origin_not_allowed")
    if cd.get("crossOrigin") is True:
        raise WebAuthnError("cross_origin")
    return origin.rstrip("/")


def _check_flags_rp(ad: AuthData, rp_id: str) -> None:
    if not hmac.compare_digest(ad.rp_id_hash, hashlib.sha256(rp_id.encode()).digest()):
        raise WebAuthnError("rp_id_mismatch")
    if not ad.flags & FLAG_UP:
        raise WebAuthnError("user_not_present")
    if not ad.flags & FLAG_UV:
        raise WebAuthnError("user_not_verified")


# ------------------------------------------------------------------ ceremonies
def verify_registration(*, client_data_json: bytes, attestation_object: bytes, challenge: str, rp_id: str,
                        allowed_origins: set[str]) -> dict:
    check_client_data(client_data_json, expected_type="webauthn.create", challenge=challenge, allowed_origins=allowed_origins)
    att, _ = cbor_decode(attestation_object)
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise WebAuthnError("attestation_object")
    # attestation conveyance "none" was requested: the statement (att["attStmt"]) is intentionally not trusted or used
    ad = parse_auth_data(att["authData"], expect_credential=True)
    _check_flags_rp(ad, rp_id)
    alg, pem = cose_to_public_key(ad.cose_key)
    return {"credential_id": b64u(ad.credential_id), "alg": alg, "public_key_pem": pem, "sign_count": ad.sign_count}


def verify_assertion(*, client_data_json: bytes, authenticator_data: bytes, signature: bytes, challenge: str,
                     rp_id: str, allowed_origins: set[str], public_key_pem: str, alg: int, stored_sign_count: int) -> int:
    """Returns the new signature counter."""
    check_client_data(client_data_json, expected_type="webauthn.get", challenge=challenge, allowed_origins=allowed_origins)
    ad = parse_auth_data(authenticator_data, expect_credential=False)
    _check_flags_rp(ad, rp_id)
    signed = authenticator_data + hashlib.sha256(client_data_json).digest()
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode())
        if alg == ES256 and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == RS256 and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise WebAuthnError("unsupported_algorithm")
    except InvalidSignature:
        raise WebAuthnError("bad_signature")
    except ValueError:
        raise WebAuthnError("bad_key")
    # counter: 0/0 = authenticator without a counter (synced passkeys); otherwise it must strictly increase
    if (ad.sign_count or stored_sign_count) and ad.sign_count <= stored_sign_count:
        raise WebAuthnError("counter_regression")
    return ad.sign_count

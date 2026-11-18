from __future__ import annotations

from tests.conftest import PASSWORD, facility_id, login


def test_login_me_and_permissions(client, as_user):
    r = client.get("/api/v1/me", headers=as_user("qr.obgyn"))
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "obgyn" and "mother:create" in body["permissions"] and "audit:read" not in body["permissions"]


def test_wrong_password_generic_error_and_lockout(client):
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"username": "auditor", "password": "wrong-password-1"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    r = client.post("/api/v1/auth/login", json={"username": "auditor", "password": PASSWORD})
    assert r.status_code == 423  # locked after 5 failures
    # unlock for other tests
    from app.db import SessionLocal
    from app.models import User
    from sqlalchemy import select
    db = SessionLocal()
    u = db.execute(select(User).where(User.username == "auditor")).scalar_one()
    u.locked_until = None
    db.commit()
    db.close()


def test_unknown_user_same_error(client):
    r = client.post("/api/v1/auth/login", json={"username": "nobody.here", "password": "whatever-123"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_refresh_rotation_and_logout_revokes(client):
    r = client.post("/api/v1/auth/login", json={"username": "qr.lab", "password": PASSWORD})
    tok = r.json()
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r2.status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code == 401  # single use
    h = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert client.get("/api/v1/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert client.get("/api/v1/me", headers=h).status_code == 401


def test_requires_auth(client):
    r = client.get("/api/v1/regions")
    assert r.status_code == 401 and "request_id" in r.json()["error"]


def test_login_rate_limit(client):
    for _ in range(10):
        client.post("/api/v1/auth/login", json={"username": "rl.user", "password": "x" * 10})
    r = client.post("/api/v1/auth/login", json={"username": "rl.user", "password": "x" * 10})
    assert r.status_code == 429


def test_cross_region_patient_is_invisible(client, as_user, new_pregnancy):
    mid, pid = new_pregnancy("qr.obgyn")
    # other obgyn (Andijan), OvaBMU without referral, auditor, regional admin, national admin w/o clinical flag
    for user in ["an.obgyn", "qr.ovabmu", "national.admin"]:
        r = client.get(f"/api/v1/mothers/{mid}", headers=as_user(user))
        assert r.status_code == 404, (user, r.text)  # existence not revealed
    for user in ["auditor", "qr.region.admin", "qr.lab", "data.steward"]:
        r = client.get(f"/api/v1/mothers/{mid}", headers=as_user(user))
        assert r.status_code in (403, 404), user
    assert client.get(f"/api/v1/mothers/{mid}", headers=as_user("qr.obgyn")).status_code == 200
    # IDOR on write
    r = client.post(f"/api/v1/pregnancies/{pid}/observations", headers=as_user("an.obgyn"), json={"systolic_bp": 120, "diastolic_bp": 80})
    assert r.status_code == 404


def test_lab_operator_can_write_not_read(client, as_user, new_pregnancy):
    mid, pid = new_pregnancy("qr.obgyn")
    r = client.post(f"/api/v1/pregnancies/{pid}/lab-results", headers=as_user("qr.lab"),
                    json={"test_code": "hemoglobin", "value": 112, "unit": "g/L", "collected_at": "2026-11-16T08:00:00Z"})
    assert r.status_code == 201 and "risk_assessment" not in r.json()
    assert client.get(f"/api/v1/pregnancies/{pid}/timeline", headers=as_user("qr.lab")).status_code in (403, 404)


def test_regional_scope_enforced(client, as_user):
    h = as_user("qr.region.admin")
    assert client.get("/api/v1/regions/1735/overview", headers=h).status_code == 200
    assert client.get("/api/v1/regions/1703/overview", headers=h).status_code == 403
    regions = client.get("/api/v1/regions", headers=h).json()
    assert [r["soato"] for r in regions] == ["1735"]
    fc = client.get("/api/v1/regions/map-risk?include_geometry=false", headers=h).json()
    assert len(fc["features"]) == 1


def test_facility_admin_cannot_create_admins(client, as_user, db):
    r = client.post("/api/v1/users", headers=as_user("qr.facility.admin"), json={
        "username": "evil.admin", "full_name": "Evil X", "password": "a-very-long-pass-1", "role": "regional_admin",
        "facility_id": facility_id(db, "DEMO-1735-MAT")})
    assert r.status_code == 403


def test_validation_errors_do_not_echo_input(client, as_user, db):
    r = client.post("/api/v1/mothers", headers=as_user("qr.obgyn"), json={
        "full_name": "Secret Person", "pinfl": "123", "facility_id": facility_id(db, "DEMO-1735-MAT")})
    assert r.status_code == 422
    assert "Secret Person" not in r.text and "123" not in r.text.replace("request_id", "")


def test_duplicate_pinfl_rejected(client, as_user, db):
    body = {"full_name": "Dup Test", "pinfl": "31234567890123", "facility_id": facility_id(db, "DEMO-1735-MAT"), "is_synthetic": True}
    assert client.post("/api/v1/mothers", headers=as_user("qr.obgyn"), json=body).status_code == 201
    r = client.post("/api/v1/mothers", headers=as_user("qr.obgyn"), json=body)
    assert r.status_code == 409


def test_pii_encrypted_at_rest(db, client, as_user, new_pregnancy):
    from app.models import Mother
    mid, _ = new_pregnancy()
    m = db.get(Mother, mid)
    assert m.full_name_enc and "Test Ona" not in m.full_name_enc
    assert m.phone_enc and "998901234567" not in m.phone_enc


_ = login

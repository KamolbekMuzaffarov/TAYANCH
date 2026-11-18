from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="perinatal_test_")
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{Path(_TMP, 'test.db').as_posix()}",
    "SEED_DEMO_PASSWORD": "Test-Only-Passw0rd!",
    "CLAUDE_API_KEY": "",
    "VOICE_PROVIDER": "local",
    "VOICELAB_ENABLED": "false",
    "VOICELAB_EXTERNAL_PROCESSING_APPROVED": "false",
    "VOICELAB_API_KEY": "",
    "LOCAL_STT_BASE_URL": "",
    "LOCAL_STT_DEV_STUB": "true",
    "MODEL_DIR": str(Path(_TMP, "models")),  # ML shadow absent in tests unless trained -> rules must still work
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import rate_limiter  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = os.environ["SEED_DEMO_PASSWORD"]


@pytest.fixture(scope="session", autouse=True)
def _database():
    from scripts.seed import create_facilities, create_users
    from app.services import clinical_rules, data_import, regional

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    data_import.load_all(db)
    create_facilities(db)
    clinical_rules.seed_rules(db)
    regional.recompute_all(db)
    create_users(db, PASSWORD)
    db.close()
    yield


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client():
    rate_limiter.reset()
    with TestClient(app) as c:
        yield c


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def as_user(client):
    cache: dict[str, dict] = {}

    def _h(username: str) -> dict:
        if username not in cache:
            cache[username] = login(client, username)
        return cache[username]
    return _h


def facility_id(db, code: str) -> str:
    from sqlalchemy import select
    from app.models import Facility
    return db.execute(select(Facility.id).where(Facility.code == code)).scalar_one()


@pytest.fixture()
def new_pregnancy(client, as_user, db):
    """Factory: registers a synthetic mother + pregnancy as qr.obgyn and returns ids."""
    counter = {"n": 0}

    def _make(username: str = "qr.obgyn", fac: str = "DEMO-1735-MAT", lmp_weeks: int = 30):
        from datetime import date, timedelta
        counter["n"] += 1
        h = as_user(username)
        r = client.post("/api/v1/mothers", headers=h, json={
            "full_name": f"Test Ona {counter['n']}", "phone": "+998901234567", "facility_id": facility_id(db, fac),
            "birth_date": "1995-05-05", "is_synthetic": True})
        assert r.status_code == 201, r.text
        mid = r.json()["id"]
        r = client.post(f"/api/v1/mothers/{mid}/pregnancies", headers=h,
                        json={"lmp_date": (date.today() - timedelta(weeks=lmp_weeks)).isoformat(), "gravida": 2, "parity": 1})
        assert r.status_code == 201, r.text
        return mid, r.json()["id"]
    return _make

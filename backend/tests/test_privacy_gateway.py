from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.admin import get_gateway
from app.core.config import Settings
from app.main import app
from app.models import PrivacyEvent
from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.privacy_gateway import PrivacyGateway


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", model=kw["model"], usage=SimpleNamespace(input_tokens=42, output_tokens=17),
                               content=[SimpleNamespace(type="text", text="Hisobotni 'Hisobotlar' bo‘limidan yuklab olasiz.")])


class FakeClient:
    def __init__(self):
        self.beta = SimpleNamespace(messages=FakeMessages())


@pytest.fixture()
def fake_llm(db):
    fc = FakeClient()
    app.dependency_overrides[get_gateway] = lambda: PrivacyGateway(db, provider=ClaudeProvider(client=fc))
    yield fc.beta.messages
    app.dependency_overrides.pop(get_gateway, None)


FORBIDDEN_PAYLOADS = [
    {"feature": "admin_help", "topic": "other_admin", "question": "Bemor Karimova Dilnoza holati qanday?"},
    {"feature": "admin_help", "topic": "other_admin", "question": "Telefon +998 90 123 45 67 ga qo'ng'iroq qilish kerakmi"},
    {"feature": "admin_help", "topic": "other_admin", "question": "JSHSHIR 31234567890123 bo'yicha qidiring"},
    {"feature": "admin_help", "topic": "other_admin", "question": "Record M-1A2B3C4D ni oching"},
    {"feature": "admin_help", "topic": "other_admin", "question": "Qon bosimi 165/110, nima qilish kerak?"},
    {"feature": "admin_help", "topic": "other_admin", "question": "gemoglobin 65 bo'lsa davolash qanday?"},
    {"feature": "admin_help", "topic": "other_admin", "question": "Transkript: sistolik bosim 170, diastolik 115"},
    {"feature": "admin_help", "topic": "other_admin", "question": "Пациентка Иванова, давление 160 на 100"},
    {"feature": "admin_help", "topic": "other_admin", "question": "Tug'ilgan sana 12.03.1995 bo'lgan ayol"},
    {"feature": "admin_help", "topic": "other_admin", "question": "preeklampsiya tashxisini tasdiqlang"},
    {"feature": "admin_help", "topic": "other_admin", "question": "Yangiyo'l ko'chasi 12-uy manzilidagi ayol"},
]


@pytest.mark.parametrize("payload", FORBIDDEN_PAYLOADS)
def test_forbidden_content_is_blocked_before_any_outbound_call(client, as_user, fake_llm, payload):
    r = client.post("/api/v1/assistant/admin-help", headers=as_user("qr.obgyn"), json=payload)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "privacy_blocked"
    assert payload["question"] not in r.text  # never echoed
    assert fake_llm.calls == []  # Claude client never invoked


@pytest.mark.parametrize("payload", [
    {"feature": "admin_help", "topic": "system_usage", "question": "Hisobot qanday olinadi?", "patient_id": "abc"},
    {"feature": "admin_help", "topic": "system_usage", "question": "x?", "context": {"mother": "x"}},
    {"feature": "free_text_chat", "question": "salom"},
    {"feature": "admin_help", "topic": "clinical_triage", "question": "salom qalesiz"},
    {"feature": "synthetic_demo_content", "kind": "training_scenario", "attachment": "ZGF0YQ=="},
])
def test_non_allowlisted_shapes_are_blocked(client, as_user, fake_llm, payload):
    r = client.post("/api/v1/assistant/admin-help", headers=as_user("qr.obgyn"), json=payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == "privacy_blocked"
    assert fake_llm.calls == []


def test_allowed_admin_question_goes_out_without_patient_data_and_logs_no_body(client, as_user, fake_llm, db, caplog):
    caplog.set_level(logging.DEBUG)
    q = "Viloyat bo'yicha oylik hisobotni CSV formatida qanday eksport qilaman?"
    r = client.post("/api/v1/assistant/admin-help", headers=as_user("qr.region.admin"),
                    json={"feature": "admin_help", "topic": "reports_howto", "question": q})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "administrative_help_only" and "Tibbiy qaror" in body["disclaimer"]
    assert len(fake_llm.calls) == 1
    sent = fake_llm.calls[0]
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert q in sent["messages"][0]["content"]
    ev = db.query(PrivacyEvent).filter(PrivacyEvent.request_id == body["request_id"]).one()
    assert ev.decision == "allowed" and ev.input_tokens == 42 and ev.status == "ok"
    assert not any(q in rec.getMessage() for rec in caplog.records)  # prompt never logged


def test_public_statistics_explanation_uses_official_published_values_only(client, as_user, fake_llm):
    r = client.post("/api/v1/assistant/admin-help", headers=as_user("national.admin"),
                    json={"feature": "public_stats_explain", "indicator_code": "infant_mortality_rate", "region_soato": "1735"})
    assert r.status_code == 200, r.text
    prompt = fake_llm.calls[-1]["messages"][0]["content"]
    assert "Republic of Karakalpakstan" in prompt and "siat" in prompt
    # registry/synthetic data cannot be requested
    r = client.post("/api/v1/assistant/admin-help", headers=as_user("national.admin"),
                    json={"feature": "public_stats_explain", "indicator_code": "registry_red_pregnancies", "region_soato": "1735"})
    assert r.status_code == 404


def test_small_cell_estimate_blocked(client, as_user, fake_llm, db):
    from app.models import RegionalIndicator
    row = db.query(RegionalIndicator).filter(RegionalIndicator.source_key == "mics6").first()
    old = row.suppression_status
    row.suppression_status = "suppressed"
    db.commit()
    try:
        r = client.post("/api/v1/assistant/admin-help", headers=as_user("national.admin"),
                        json={"feature": "public_stats_explain", "indicator_code": row.indicator_code, "region_soato": row.region_soato})
        assert r.status_code == 422
        assert fake_llm.calls == []
    finally:
        row.suppression_status = old
        db.commit()


def test_detector_failure_fails_closed(client, as_user, fake_llm, monkeypatch):
    import app.services.llm.privacy_gateway as pg
    monkeypatch.setattr(pg, "screen_text", lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/v1/assistant/admin-help", headers=as_user("qr.obgyn"),
                    json={"feature": "admin_help", "topic": "system_usage", "question": "Tizimga qanday kiraman?"})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["detector"] == "detector_error_fail_closed"
    assert fake_llm.calls == []


def test_external_patient_data_flag_is_a_hard_invariant():
    with pytest.raises(Exception):
        Settings(CLAUDE_EXTERNAL_PATIENT_DATA=True)
    with pytest.raises(Exception):
        Settings(CLAUDE_LOG_PROMPT_BODY=True)


def test_production_rejects_unsafe_config():
    with pytest.raises(Exception):
        Settings(APP_ENV="production", APP_DEBUG=True)
    ok = Settings(APP_ENV="production", JWT_SECRET="x" * 40, ENCRYPTION_KEY="k", LOCAL_STT_DEV_STUB=False,
                  CLINICAL_RULES_ALLOW_UNAPPROVED=False)
    assert ok.VOICELAB_ENABLED is False and ok.CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA is False


def test_routes_never_import_claude_provider_directly():
    api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
    offenders = [p.name for p in api_dir.rglob("*.py") if "claude_provider" in p.read_text(encoding="utf-8")
                 or "import anthropic" in p.read_text(encoding="utf-8")]
    assert offenders == []
    services = Path(__file__).resolve().parents[1] / "app" / "services"
    importers = [p.relative_to(services).as_posix() for p in services.rglob("*.py")
                 if "claude_provider" in p.read_text(encoding="utf-8") and p.name != "claude_provider.py"]
    assert importers == ["llm/privacy_gateway.py"]


def test_clinical_endpoints_cannot_reach_claude(client, as_user, fake_llm, new_pregnancy):
    _, pid = new_pregnancy()
    client.post(f"/api/v1/pregnancies/{pid}/observations", headers=as_user("qr.obgyn"), json={"systolic_bp": 170, "diastolic_bp": 115})
    assert fake_llm.calls == []


def test_missing_api_key_returns_503_not_500(client, as_user, db):
    app.dependency_overrides[get_gateway] = lambda: PrivacyGateway(db, provider=ClaudeProvider())
    try:
        r = client.post("/api/v1/assistant/admin-help", headers=as_user("qr.obgyn"),
                        json={"feature": "admin_help", "topic": "system_usage", "question": "Parolni qanday almashtiraman?"})
        assert r.status_code == 503 and "not_configured" in r.json()["error"]["message"]
    finally:
        app.dependency_overrides.pop(get_gateway, None)

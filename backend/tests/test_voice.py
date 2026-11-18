from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.models import MaternalObservation, VoiceDraft, VoiceJob
from app.services.voice.parser import parse
from app.services.voice.providers import VoiceLabSTTProvider

DICTATION = "Sistolik bosim 162, diastolik 104. Puls 96, harorat 36,8, saturatsiya 97. Homila yurak urishi 150."


def _upload(client, h, pid, text=DICTATION, **form):
    return client.post("/api/v1/voice/transcribe", headers=h,
                       files={"audio": ("dictation.txt", text.encode(), "text/plain")},
                       data={"subject_type": "pregnancy", "subject_id": pid, "language": "uz", **form})


def test_parser_multilingual_and_flags():
    c = {x["field"]: x for x in parse("Давление 150 на 95, пульс сто двадцать, гемоглобин 9.5", "pregnancy")}
    assert c["systolic_bp"]["value"] == 150 and c["diastolic_bp"]["value"] == 95
    assert c["heart_rate"]["value"] == 120 and c["heart_rate"]["needs_review"]
    assert c["lab_hemoglobin"]["needs_review"] and "g/dL" in c["lab_hemoglobin"]["note"]
    uz = {x["field"]: x["value"] for x in parse("harorat o'ttiz olti butun yetti, nafas yigirma", "pregnancy")}
    assert uz == {"temperature_c": 36.7, "respiratory_rate": 20}
    cyr = {x["field"]: x["value"] for x in parse("Ҳарорат 37,9, пульс 110", "pregnancy")}
    assert cyr["temperature_c"] == 37.9 and cyr["heart_rate"] == 110


def test_draft_is_not_written_until_confirmed(client, as_user, new_pregnancy, db):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    r = _upload(client, h, pid)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["requires_confirmation"] and d["processing_location"] == "local_server"
    fields = {c["field"]: c["value"] for c in d["candidates"]}
    assert fields["systolic_bp"] == 162 and fields["fetal_heart_rate"] == 150
    assert db.query(MaternalObservation).filter(MaternalObservation.pregnancy_id == pid).count() == 0
    # transcript stored encrypted, no audio stored
    vd = db.get(VoiceDraft, d["draft_id"])
    assert vd.transcript_enc and "Sistolik" not in vd.transcript_enc
    assert not hasattr(db.get(VoiceJob, d["job_id"]), "audio_blob")
    # confirm without review flag -> rejected
    bad = client.post(f"/api/v1/voice/drafts/{d['draft_id']}/confirm", headers=h,
                      json={"fields": [{"field": "systolic_bp", "value": 162}], "reviewed_transcript": False})
    assert bad.status_code == 422
    # clinician corrects a value and confirms
    r = client.post(f"/api/v1/voice/drafts/{d['draft_id']}/confirm", headers=h, json={
        "reviewed_transcript": True,
        "fields": [{"field": "systolic_bp", "value": 162}, {"field": "diastolic_bp", "value": 104},
                   {"field": "heart_rate", "value": 96}, {"field": "fetal_heart_rate", "value": 150}]})
    assert r.status_code == 201, r.text
    assert r.json()["risk_level"] == "red" and "M_SEVERE_HYPERTENSION" in r.json()["triggered_rules"]
    obs = db.query(MaternalObservation).filter(MaternalObservation.pregnancy_id == pid).one()
    assert obs.source == "voice" and obs.voice_draft_id == d["draft_id"]
    db.refresh(vd)
    assert vd.status == "confirmed" and vd.transcript_enc is None  # transcript wiped after confirmation
    assert client.post(f"/api/v1/voice/drafts/{d['draft_id']}/confirm", headers=h,
                       json={"fields": [{"field": "systolic_bp", "value": 1}], "reviewed_transcript": True}).status_code == 410


def test_confirm_rejects_unknown_fields_and_bad_ranges(client, as_user, new_pregnancy):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    d = _upload(client, h, pid).json()
    r = client.post(f"/api/v1/voice/drafts/{d['draft_id']}/confirm", headers=h,
                    json={"fields": [{"field": "diagnosis", "value": "x"}], "reviewed_transcript": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "field_not_allowed"
    r = client.post(f"/api/v1/voice/drafts/{d['draft_id']}/confirm", headers=h,
                    json={"fields": [{"field": "systolic_bp", "value": 400}], "reviewed_transcript": True})
    assert r.status_code == 422


def test_discard_wipes_transcript(client, as_user, new_pregnancy, db):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    d = _upload(client, h, pid).json()
    assert client.delete(f"/api/v1/voice/drafts/{d['draft_id']}", headers=h).status_code == 204
    vd = db.get(VoiceDraft, d["draft_id"])
    db.refresh(vd)
    assert vd.status == "discarded" and vd.transcript_enc is None and vd.parsed_fields == []
    # other users cannot touch someone else's draft
    d2 = _upload(client, h, pid).json()
    assert client.get(f"/api/v1/voice/drafts/{d2['draft_id']}", headers=as_user("qr.ovabmu")).status_code == 404


def test_unrecognised_audio_rejected(client, as_user, new_pregnancy):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    r = client.post("/api/v1/voice/transcribe", headers=h, files={"audio": ("x.bin", b"\x00\x01garbage", "application/octet-stream")},
                    data={"subject_type": "pregnancy", "subject_id": pid})
    assert r.status_code == 415


def test_voicelab_disabled_makes_no_network_call(client, as_user, new_pregnancy, monkeypatch, db):
    calls = []

    class NoNetwork:
        def __init__(self, *a, **k):
            calls.append("client-created")

        def post(self, *a, **k):
            calls.append("post")
            raise AssertionError("network!")

    import app.services.voice.providers as prov
    monkeypatch.setattr(prov.httpx, "Client", NoNetwork)
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40
    r = client.post("/api/v1/voice/transcribe", headers=h, files={"audio": ("a.wav", wav, "audio/wav")},
                    data={"subject_type": "pregnancy", "subject_id": pid, "provider": "voicelab", "consent_external": "true"})
    assert r.status_code == 403 and "voicelab_disabled" in r.json()["error"]["message"]
    assert calls == []
    from app.models import PrivacyEvent
    assert db.query(PrivacyEvent).filter(PrivacyEvent.event_type == "voice_external_blocked").count() >= 1


def _voicelab_mock(seen, final_status="completed"):
    polls = {"n": 0}

    def handler(req):
        seen.append(req)
        if req.method == "POST" and req.url.path == "/v1/stt":
            return httpx.Response(202, json={"id": "stt_01TEST", "status": "queued", "request_id": "req_1"})
        if req.method == "GET" and req.url.path == "/v1/stt/transcriptions/stt_01TEST":
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"id": "stt_01TEST", "status": "processing"}, headers={"Retry-After": "1"})
            return httpx.Response(200, json={"id": "stt_01TEST", "status": final_status, "transcript": "Puls 80, harorat 36,6.",
                                             "language": "uz", "duration_ms": 2100, "segments": []})
        if req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": "not_found"}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_voicelab_requires_consent_and_approval_even_if_enabled():
    seen = []
    client = _voicelab_mock(seen)
    s = Settings(VOICELAB_ENABLED=True, VOICELAB_API_KEY="k", VOICELAB_EXTERNAL_PROCESSING_APPROVED=False)
    with pytest.raises(AppError) as e:
        VoiceLabSTTProvider(s, client).transcribe(b"RIFF", "audio/wav", "uz", "idem", consent_external=True)
    assert "vendor_legal_review_not_approved" in e.value.message and seen == []
    s2 = Settings(VOICELAB_ENABLED=True, VOICELAB_API_KEY="k", VOICELAB_EXTERNAL_PROCESSING_APPROVED=True)
    with pytest.raises(AppError):
        VoiceLabSTTProvider(s2, client).transcribe(b"RIFF", "audio/wav", "uz", "idem", consent_external=False)
    assert seen == []


def test_voicelab_async_flow_matches_documented_api():
    import uuid
    seen = []
    s = Settings(VOICELAB_ENABLED=True, VOICELAB_API_KEY="k", VOICELAB_EXTERNAL_PROCESSING_APPROVED=True)
    prov = VoiceLabSTTProvider(s, _voicelab_mock(seen))
    res = prov.transcribe(b"RIFF", "audio/wav", "uz-cyrl", "my-key", consent_external=True, sleep=lambda _: None)
    assert res.text == "Puls 80, harorat 36,6." and res.external_id == "stt_01TEST" and res.duration_ms == 2100
    assert res.confidence is None  # VoiceLab gives no confidence -> never treated as "certain"
    post = seen[0]
    body = post.content.decode(errors="ignore")
    assert post.headers["Authorization"] == "Bearer k"
    uuid.UUID(post.headers["Idempotency-Key"])  # must be a UUID
    assert 'name="audio"' in body and 'name="language"' in body and 'name="include_speakers"' in body
    assert body.split('name="language"')[1].split("--")[0].strip() == "uz"  # uz-cyrl is sent as "uz"
    assert [r.method for r in seen] == ["POST", "GET", "GET"]
    # same submission -> same idempotency UUID (safe retries, no double charge)
    seen2 = []
    VoiceLabSTTProvider(s, _voicelab_mock(seen2)).transcribe(b"RIFF", "audio/wav", "uz", "my-key", consent_external=True, sleep=lambda _: None)
    assert seen2[0].headers["Idempotency-Key"] == post.headers["Idempotency-Key"]
    assert prov.delete_remote("stt_01TEST") is True


def test_voicelab_failed_job_is_deleted_and_reported():
    seen = []
    s = Settings(VOICELAB_ENABLED=True, VOICELAB_API_KEY="k", VOICELAB_EXTERNAL_PROCESSING_APPROVED=True)
    with pytest.raises(AppError) as e:
        VoiceLabSTTProvider(s, _voicelab_mock(seen, "failed")).transcribe(b"RIFF", "audio/wav", "uz", "x", consent_external=True,
                                                                        sleep=lambda _: None)
    assert e.value.code == "stt_failed" and seen[-1].method == "DELETE"


def test_expired_drafts_cleanup(client, as_user, new_pregnancy, db):
    from datetime import timedelta
    from app.api.v1.voice import cleanup_expired_drafts
    from app.core.security import utcnow
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    d = _upload(client, h, pid).json()
    vd = db.get(VoiceDraft, d["draft_id"])
    vd.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert cleanup_expired_drafts(db) >= 1
    db.refresh(vd)
    assert vd.status == "expired" and vd.transcript_enc is None


def test_local_stt_http_contract_and_low_confidence_flags():
    from app.services.voice.providers import LocalSTTProvider
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"text": "puls 88", "language": "uz", "duration": 2.5, "confidence": 0.42})

    s = Settings(LOCAL_STT_BASE_URL="http://127.0.0.1:9001", LOCAL_STT_DEV_STUB=False)
    res = LocalSTTProvider(s, httpx.Client(transport=httpx.MockTransport(handler))).transcribe(b"RIFF", "audio/wav", "uz-cyrl", "k")
    assert res.text == "puls 88" and res.confidence == 0.42 and res.duration_ms == 2500 and res.provider == "local"
    assert seen[0].url.path == "/v1/audio/transcriptions" and seen[0].url.host == "127.0.0.1"
    assert 'name="language"' in seen[0].content.decode(errors="ignore")


def test_local_stt_unavailable_is_503():
    from app.services.voice.providers import LocalSTTProvider
    s = Settings(LOCAL_STT_BASE_URL="http://127.0.0.1:9001", LOCAL_STT_DEV_STUB=False)
    def boom(req):
        raise httpx.ConnectError("down")
    with pytest.raises(AppError) as e:
        LocalSTTProvider(s, httpx.Client(transport=httpx.MockTransport(boom))).transcribe(b"RIFF", "audio/wav", "uz", "k")
    assert e.value.status == 503

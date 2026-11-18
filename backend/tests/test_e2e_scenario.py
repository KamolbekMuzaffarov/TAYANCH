from __future__ import annotations

from app.models import AuditLog
from tests.conftest import facility_id


def test_full_perinatal_flow(client, as_user, new_pregnancy, db):
    doc, ova = as_user("qr.obgyn"), as_user("qr.ovabmu")
    mid, pid = new_pregnancy()

    # OvaBMU cannot see the patient before referral
    assert client.get(f"/api/v1/mothers/{mid}", headers=ova).status_code == 404

    # 1-2) dictation -> draft
    r = client.post("/api/v1/voice/transcribe", headers=doc,
                    files={"audio": ("d.txt", "qon bosimi 168 ga 112, oqsil uch plus, puls 104".encode(), "text/plain")},
                    data={"subject_type": "pregnancy", "subject_id": pid, "language": "uz"})
    draft = r.json()
    cand = {c["field"]: c["value"] for c in draft["candidates"]}
    assert cand == {"systolic_bp": 168, "diastolic_bp": 112, "proteinuria": "3+", "heart_rate": 104}

    # 3) clinician confirms -> 4) rules -> red
    r = client.post(f"/api/v1/voice/drafts/{draft['draft_id']}/confirm", headers=doc, json={
        "reviewed_transcript": True, "fields": [{"field": k, "value": v} for k, v in cand.items()]})
    assert r.json()["risk_level"] == "red"

    # 5) alert exists
    alert = [a for a in client.get("/api/v1/alerts?status=open", headers=doc).json()["items"] if a["subject_id"] == pid][0]
    assert {"M_SEVERE_HYPERTENSION", "M_PREECLAMPSIA_SEVERE_FEATURES"} <= set(alert["rule_ids"])

    # 6) referral to regional OvaBMU
    r = client.post("/api/v1/referrals", headers=doc, json={
        "subject_type": "pregnancy", "pregnancy_id": pid, "alert_id": alert["id"],
        "to_facility_id": facility_id(db, "DEMO-1735-OVA"), "priority": "emergency", "reason_code": "preeclampsia_suspected"})
    assert r.status_code == 201, r.text
    ref = r.json()

    # 7) OvaBMU queue -> accept (acknowledges alert) -> consult -> close
    q = client.get("/api/v1/referrals/queue", headers=ova).json()["items"]
    assert q[0]["id"] == ref["id"] and q[0]["priority"] == "emergency"
    assert client.get(f"/api/v1/mothers/{mid}", headers=ova).status_code == 200  # referral grants scoped access
    assert client.post(f"/api/v1/referrals/{ref['id']}/actions", headers=ova, json={"action": "accept"}).json()["status"] == "accepted"
    assert client.get(f"/api/v1/alerts/{alert['id']}", headers=ova).json()["alert"]["status"] == "acknowledged"
    r = client.post(f"/api/v1/referrals/{ref['id']}/actions", headers=ova,
                    json={"action": "record_consultation", "consult_outcome": "admitted", "consult_note": "Qabul qilindi"})
    assert r.json()["status"] == "consulted"
    assert client.post(f"/api/v1/referrals/{ref['id']}/actions", headers=ova, json={"action": "close"}).json()["status"] == "closed"
    # other region's OvaBMU cannot act on it
    assert client.post(f"/api/v1/referrals/{ref['id']}/actions", headers=as_user("an.ovabmu"), json={"action": "close"}).status_code == 404

    # 8) resolve alert and check audit trail
    client.post(f"/api/v1/alerts/{alert['id']}/resolve", headers=doc, json={"resolution_code": "referred"})
    actions = {a.action for a in db.query(AuditLog).all()}
    assert {"voice.transcribe", "voice.confirm", "alert.created", "referral.create", "referral.accept",
            "referral.record_consultation", "referral.close", "alert.resolved"} <= actions
    # timeline shows the voice-sourced observation and the assessment history
    tl = client.get(f"/api/v1/pregnancies/{pid}/timeline", headers=doc).json()
    assert tl["observations"][0]["source"] == "voice" and tl["risk_assessments"][-1]["level"] == "red"

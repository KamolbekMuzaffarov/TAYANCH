from __future__ import annotations

from datetime import timedelta

from app.core.security import utcnow
from app.models import Alert, AlertRecipient
from app.services import clinical_rules
from tests.conftest import facility_id


def _obs(client, h, pid, **kw):
    r = client.post(f"/api/v1/pregnancies/{pid}/observations", headers=h, json=kw)
    assert r.status_code == 201, r.text
    return r.json()["risk_assessment"]


def test_no_data_is_grey_never_green(client, as_user, new_pregnancy):
    _, pid = new_pregnancy()
    r = client.post(f"/api/v1/pregnancies/{pid}/risk-assessments", headers=as_user("qr.obgyn"))
    assert r.status_code == 201
    assert r.json()["level"] == "grey" and set(r.json()["missing_inputs"]) == {"systolic_bp", "diastolic_bp"}


def test_normal_values_green(client, as_user, new_pregnancy):
    _, pid = new_pregnancy()
    ra = _obs(client, as_user("qr.obgyn"), pid, systolic_bp=118, diastolic_bp=76, heart_rate=80, spo2=98, temperature_c=36.6)
    assert ra["level"] == "green" and ra["triggered_rules"] == []


def test_severe_hypertension_red_alert_dedup_and_merge(client, as_user, new_pregnancy, db):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    ra = _obs(client, h, pid, systolic_bp=165, diastolic_bp=112)
    assert ra["level"] == "red"
    assert "M_SEVERE_HYPERTENSION" in [t["rule_id"] for t in ra["triggered_rules"]]
    assert all(t["approval_status"] == clinical_rules.DEMO for t in ra["triggered_rules"])
    alerts = client.get("/api/v1/alerts?status=open", headers=h).json()["items"]
    mine = [a for a in alerts if a["subject_id"] == pid]
    assert len(mine) == 1 and mine[0]["severity"] == "red"
    # repeated signal -> same alert, occurrences++, new rule merged (not lost)
    _obs(client, h, pid, systolic_bp=168, diastolic_bp=112, proteinuria="3+", severe_headache=True)
    mine = [a for a in client.get("/api/v1/alerts?status=open", headers=h).json()["items"] if a["subject_id"] == pid]
    assert len(mine) == 1 and mine[0]["occurrences"] == 2
    assert "M_PREECLAMPSIA_SEVERE_FEATURES" in mine[0]["rule_ids"]
    # recipient got an in-app notification without clinical details
    from app.services.notifications import notifier
    texts = [t for (_, _, t) in notifier.sent if mine[0]["id"] in t]
    assert texts and all("165" not in t and "Test Ona" not in t for t in texts)
    assert db.query(AlertRecipient).filter(AlertRecipient.alert_id == mine[0]["id"]).count() >= 1


def test_alert_lifecycle_and_invalid_transitions(client, as_user, new_pregnancy):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    _obs(client, h, pid, systolic_bp=150, diastolic_bp=95)  # yellow (>=20 weeks)
    a = [x for x in client.get("/api/v1/alerts", headers=h).json()["items"] if x["subject_id"] == pid][0]
    assert a["severity"] == "yellow"
    assert client.post(f"/api/v1/alerts/{a['id']}/resolve", headers=h, json={"resolution_code": "bogus"}).status_code == 422
    assert client.post(f"/api/v1/alerts/{a['id']}/acknowledge", headers=h).json()["status"] == "acknowledged"
    assert client.post(f"/api/v1/alerts/{a['id']}/acknowledge", headers=h).status_code == 409
    r = client.post(f"/api/v1/alerts/{a['id']}/resolve", headers=h, json={"resolution_code": "clinically_managed"})
    assert r.json()["status"] == "resolved"
    detail = client.get(f"/api/v1/alerts/{a['id']}", headers=h).json()
    assert [x["action"] for x in detail["actions"]] == ["created", "acknowledged", "resolved"]
    # other clinicians cannot see it (IDOR)
    assert client.get(f"/api/v1/alerts/{a['id']}", headers=as_user("an.obgyn")).status_code == 404


def test_escalation_of_unacknowledged_alert(client, as_user, new_pregnancy, db):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    _obs(client, h, pid, systolic_bp=95, diastolic_bp=60, spo2=91)  # red (low SpO2)
    a = db.query(Alert).filter(Alert.subject_id == pid).one()
    a.sla_due_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = client.post("/api/v1/alerts/escalate-overdue", headers=as_user("national.admin"))
    assert a.id in r.json()["escalated"]
    db.refresh(a)
    assert a.escalation_level == 1
    # facility admin now sees it in their center
    ids = [x["id"] for x in client.get("/api/v1/alerts", headers=as_user("qr.facility.admin")).json()["items"]]
    assert a.id in ids


def test_lab_rules_and_unit_validation(client, as_user, new_pregnancy):
    h = as_user("qr.obgyn")
    _, pid = new_pregnancy()
    bad = client.post(f"/api/v1/pregnancies/{pid}/lab-results", headers=h,
                      json={"test_code": "hemoglobin", "value": 9.5, "unit": "g/L", "collected_at": "2026-11-16T08:00:00Z"})
    assert bad.status_code == 422  # ambiguous unit/value combination rejected, not silently converted
    r = client.post(f"/api/v1/pregnancies/{pid}/lab-results", headers=h,
                    json={"test_code": "hemoglobin", "value": 6.5, "unit": "g/dL", "collected_at": utcnow().isoformat(),
                          "idempotency_key": f"lab-{pid}"})
    assert r.status_code == 201
    assert "M_SEVERE_ANAEMIA" in [t["rule_id"] for t in r.json()["risk_assessment"]["triggered_rules"]]
    dup = client.post(f"/api/v1/pregnancies/{pid}/lab-results", headers=h,
                      json={"test_code": "hemoglobin", "value": 6.5, "unit": "g/dL", "collected_at": utcnow().isoformat(),
                            "idempotency_key": f"lab-{pid}"})
    assert dup.json().get("duplicate") is True


def test_observation_validation(client, as_user, new_pregnancy):
    _, pid = new_pregnancy()
    r = client.post(f"/api/v1/pregnancies/{pid}/observations", headers=as_user("qr.obgyn"), json={"systolic_bp": 80, "diastolic_bp": 90})
    assert r.status_code == 422


def test_newborn_rules(client, as_user, db):
    h = as_user("qr.neonatologist")
    r = client.post("/api/v1/newborns", headers=h, json={"birth_datetime": utcnow().isoformat(), "sex": "F",
                                                          "gestational_age_weeks": 30.5, "birth_weight_g": 1350, "apgar_5": 6,
                                                          "facility_id": facility_id(db, "DEMO-1735-MAT"), "is_synthetic": True})
    assert r.status_code == 201, r.text
    nb = r.json()
    assert nb["current_risk_level"] == "red"
    r = client.post(f"/api/v1/newborns/{nb['id']}/observations", headers=h,
                    json={"temperature_c": 35.5, "heart_rate": 150, "spo2": 87, "respiratory_rate": 66})
    rules = {t["rule_id"] for t in r.json()["risk_assessment"]["triggered_rules"]}
    assert {"N_MODERATE_HYPOTHERMIA", "N_HYPOXAEMIA", "N_FAST_BREATHING", "N_VERY_PRETERM", "N_VERY_LOW_BIRTH_WEIGHT"} <= rules
    # neonatologist of another facility cannot read it
    assert client.get(f"/api/v1/newborns/{nb['id']}", headers=as_user("an.obgyn")).status_code == 404


def test_unapproved_rules_do_not_fire_when_disallowed(db):
    rules = clinical_rules.active_rules(db, "pregnancy", allow_unapproved=False)
    assert rules == []  # seed rules are pending clinical approval
    out = clinical_rules.evaluate(rules, "pregnancy", {"systolic_bp": 200, "diastolic_bp": 120})
    assert out.level == "grey"  # no approved rule set -> never green


def test_rule_engine_missing_field_is_false():
    from app.models import ClinicalRuleVersion
    r = ClinicalRuleVersion(rule_id="X", version=1, subject_type="pregnancy", severity="red", approval_status="approved",
                            definition={"field": "spo2", "op": "<", "value": 95}, clinical_reference="t", effective_from=utcnow().date())
    out = clinical_rules.evaluate([r], "pregnancy", {"systolic_bp": 120, "diastolic_bp": 80})
    assert out.level == "green" and out.triggered == []

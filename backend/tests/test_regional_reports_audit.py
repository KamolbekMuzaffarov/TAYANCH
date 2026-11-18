from __future__ import annotations

import logging

from app.core.audit import verify_chain
from app.models import AuditLog
from app.services import regional
from app.services.regional import Comp, layer_status, overall


def test_map_risk_featurecollection(client, as_user):
    r = client.get("/api/v1/regions/map-risk", headers=as_user("national.admin"))
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 14
    for f in fc["features"]:
        p = f["properties"]
        assert p["status"] in {"red", "yellow", "green", "grey"} and f["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert set(p["layers"]) == {"maternal_area_risk", "newborn_area_risk", "healthcare_response_risk", "overall_area_status"}
    assert fc["methodology"]["approval_status"].startswith("prototype")
    assert any(s["key"] == "siat" for s in fc["sources"])


def test_karakalpakstan_2025_signal_is_explained(client, as_user):
    r = client.get("/api/v1/regions/1735/overview?year=2025", headers=as_user("national.admin")).json()
    imr = next(c for c in r["components"] if c["indicator"] == "infant_mortality_rate")
    assert imr["status"].startswith("worse_significant") and imr["value"] > imr["national"]
    assert "events=" in imr["detail"] and imr["source"] == "siat"
    assert r["layers"]["newborn_area_risk"] == "red"


def test_grey_rules():
    ins = Comp("a", "L", "insufficient")
    ok = Comp("b", "L", "not_different")
    assert layer_status([ins, ins, ok]) == "grey"           # fewer than half usable -> grey
    assert layer_status([Comp("c", "L", "context")]) == "grey"
    assert layer_status([ok, ok, ins]) == "green"
    assert layer_status([ok, Comp("d", "L", "worsening_trend")]) == "yellow"
    assert layer_status([Comp("e", "L", "worse_significant"), Comp("f", "L", "worse_significant")]) == "red"
    assert overall(["green", "grey", "green"]) == "grey"     # never green overall with a grey layer
    assert overall(["green", "yellow", "grey"]) == "yellow"


def test_missing_year_makes_region_grey(db):
    res = regional.compute_region(db, "1735", 2040)
    assert res["layers"]["newborn_area_risk"] in ("grey",) and res["layers"]["overall_area_status"] != "green"


def test_overdispersion_factor():
    assert regional._winsorised_phi([0.1, -0.2, 0.3, 0.1, -0.1, 0.2]) == 1.0
    assert regional._winsorised_phi([5, -6, 7, -4, 6, 5, -7, 8, -5, 6]) > 10


def test_district_is_grey_without_official_data(client, as_user):
    h = as_user("national.admin")
    d = client.get("/api/v1/regions/1735/districts", headers=h).json()
    assert len(d) > 5 and all(x["is_official_code"] is False for x in d)
    ov = client.get(f"/api/v1/districts/{d[0]['code']}/overview", headers=h).json()
    assert ov["status"] == "grey"


def test_indicator_provenance(client, as_user):
    r = client.get("/api/v1/indicators?region_soato=1735&code=infant_mortality_rate&year_from=2023",
                   headers=as_user("national.admin")).json()
    row = r["items"][-1]
    for k in ("numerator", "denominator", "rate", "period_start", "period_end", "source", "source_version",
              "quality_status", "suppression_status", "methodology"):
        assert k in row
    recomputed = row["numerator"] / row["denominator"] * 1000
    # SIAT's published rate uses its own denominator method; counts agree within 10% or the row is flagged
    assert abs(recomputed - row["rate"]) / row["rate"] < 0.10 or row["quality_status"] == "check"


def test_national_dashboard_separates_sources(client, as_user):
    r = client.get("/api/v1/dashboard/national", headers=as_user("national.admin")).json()
    assert r["official_statistics"]["infant_mortality_rate"]["source"] == "SIAT"
    assert "DEMO" in r["clinical_registry"]["label"]


def test_report_small_cell_suppression_and_export_audit(client, as_user, db):
    h = as_user("national.admin")
    rep = client.get("/api/v1/reports/registry-summary", headers=h).json()
    for row in rep["active_pregnancies_by_region_and_risk"]:
        if row["suppressed"]:
            assert row["count"] is None
        else:
            assert row["count"] >= 10
    r = client.get("/api/v1/reports/export.csv?report=regional_indicators", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert db.query(AuditLog).filter(AuditLog.action == "report.export").count() >= 1
    assert client.get("/api/v1/reports/export.csv?report=registry_summary", headers=as_user("qr.obgyn")).status_code == 403


def test_audit_chain_valid_and_tamper_evident(client, as_user, db):
    h = as_user("auditor")
    assert client.get("/api/v1/audit-logs/verify", headers=h).json()["valid"] is True
    row = db.query(AuditLog).order_by(AuditLog.seq).first()
    original = row.action
    row.action = "tampered"
    db.commit()
    try:
        assert verify_chain(db)["valid"] is False
    finally:
        row.action = original
        db.commit()
    assert verify_chain(db)["valid"] is True


def test_audit_detail_rejects_pii_keys(db):
    import pytest
    from app.core.audit import write_audit
    with pytest.raises(ValueError):
        write_audit(db, action="x", resource_type="y", detail={"full_name": "Ali"})


def test_no_patient_values_in_logs(client, as_user, new_pregnancy, caplog):
    caplog.set_level(logging.DEBUG)
    mid, pid = new_pregnancy()
    client.post(f"/api/v1/pregnancies/{pid}/observations", headers=as_user("qr.obgyn"), json={"systolic_bp": 187, "diastolic_bp": 119})
    client.get(f"/api/v1/mothers/{mid}", headers=as_user("qr.obgyn"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    import re
    assert "Test Ona" not in text and "998901234567" not in text and mid not in text and pid not in text
    assert not re.search(r"187", text)  # the systolic value (request ids / timings are not word-bounded)


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store"
    assert "X-Request-ID" in r.headers

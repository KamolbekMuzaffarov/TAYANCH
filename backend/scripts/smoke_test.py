from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
cred = dict(l.split("=", 1) for l in (Path(__file__).resolve().parents[1] / ".seed_credentials.txt").read_text(encoding="utf-8").splitlines()
            if "=" in l and not l.startswith("#"))
PW = cred["password"].strip()
c = httpx.Client(timeout=30)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(f"[{'OK ' if ok else 'FAIL'}] {name} {info}")


def login(u: str) -> dict:
    r = c.post(f"{BASE}/auth/login", json={"username": u, "password": PW})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


t0 = time.time()
health = httpx.get(BASE.replace("/api/v1", "") + "/health").json()
check("health", health["status"] == "ok", str(health))
nat, doc, ova, aud = login("national.admin"), login("qr.obgyn"), login("qr.ovabmu"), login("auditor")

fc = c.get(f"{BASE}/regions/map-risk", headers=nat).json()
statuses = {f["properties"]["name_uz"]: f["properties"]["status"] for f in fc["features"]}
check("map-risk 14 regions", len(statuses) == 14, f"year={fc['year']} " + ", ".join(f"{k.split()[0]}={v}" for k, v in statuses.items()))
ov = c.get(f"{BASE}/regions/1735/overview", headers=nat).json()
imr = next(x for x in ov["components"] if x["indicator"] == "infant_mortality_rate")
check("Karakalpakstan overview", ov["layers"]["overall_area_status"] in ("red", "yellow"),
      f"IMR {imr['value']} vs national {imr['national']} ({imr['status']})")
dash = c.get(f"{BASE}/dashboard/national", headers=nat).json()
check("national dashboard", dash["official_statistics"]["infant_mortality_rate"] is not None,
      f"IMR={dash['official_statistics']['infant_mortality_rate']['value']} MMR={dash['official_statistics']['maternal_mortality_ratio']['value']}")
ind = c.get(f"{BASE}/indicators?region_soato=1700&code=maternal_mortality_ratio", headers=nat).json()
check("indicator series", ind["total"] >= 10, f"{ind['total']} years of MMR")

fac = next(f for f in c.get(f"{BASE}/facilities?region_soato=1735", headers=doc).json() if f["code"] == "DEMO-1735-MAT")
ova_fac = next(f for f in c.get(f"{BASE}/facilities?region_soato=1735&facility_type=ovabmu", headers=nat).json())
m = c.post(f"{BASE}/mothers", headers=doc, json={"full_name": "Smoke Test Ona", "facility_id": fac["id"], "is_synthetic": True}).json()
p = c.post(f"{BASE}/mothers/{m['id']}/pregnancies", headers=doc, json={"lmp_date": "2026-04-20", "gravida": 1, "parity": 0}).json()
check("register synthetic mother+pregnancy", "id" in p)
vp = c.get(f"{BASE}/voice/providers", headers=doc).json()
stub = vp["local"]["dev_stub"] and vp["default"] == "local"
if stub:
    up = ("d.txt", "qon bosimi 166 ga 111, oqsil ikki plus, puls 100".encode(), "text/plain")
else:  # real local STT: send a synthetic Uzbek dictation recording
    sample = Path(__file__).resolve().parents[2] / "stt_server" / "samples" / "uz_bp_words.mp3"
    up = (sample.name, sample.read_bytes(), "audio/mpeg")
d = c.post(f"{BASE}/voice/transcribe", headers=doc, files={"audio": up},
           data={"subject_type": "pregnancy", "subject_id": p["id"], "language": "uz",
                 # synthetic test audio; the clinician-facing UI must show the external-processing notice first
                 "consent_external": "true" if vp["default"] == "voicelab" else "false"}).json()
fields = {x["field"]: x["value"] for x in d.get("candidates", [])}
check("voice draft" + (" (dev stub)" if stub else " (real local STT)"), fields.get("systolic_bp") in (166, 168),
      f"provider={d.get('provider')} {fields}")
conf = c.post(f"{BASE}/voice/drafts/{d['draft_id']}/confirm", headers=doc,
              json={"reviewed_transcript": True, "fields": [{"field": k, "value": v} for k, v in fields.items()]}).json()
check("confirm -> rules", conf["risk_level"] == "red", ",".join(conf["triggered_rules"]))
alert = next(a for a in c.get(f"{BASE}/alerts?status=open", headers=doc).json()["items"] if a["subject_id"] == p["id"])
ref = c.post(f"{BASE}/referrals", headers=doc, json={"subject_type": "pregnancy", "pregnancy_id": p["id"], "alert_id": alert["id"],
                                                      "to_facility_id": ova_fac["id"], "priority": "emergency",
                                                      "reason_code": "preeclampsia_suspected"}).json()
acc = c.post(f"{BASE}/referrals/{ref['id']}/actions", headers=ova, json={"action": "accept"}).json()
check("OvaBMU accept", acc["status"] == "accepted")
blocked = c.post(f"{BASE}/assistant/admin-help", headers=doc,
                 json={"feature": "admin_help", "topic": "other_admin", "question": "Bemor Smoke Test Ona bosimi 166/111"})
check("Claude gateway blocks patient data", blocked.status_code == 422, str([x["detector"] for x in blocked.json()["error"]["details"]]))
allowed = c.post(f"{BASE}/assistant/admin-help", headers=doc,
                 json={"feature": "admin_help", "topic": "reports_howto", "question": "Hisobotni qanday eksport qilaman?"})
check("Claude allowed request reaches provider only after checks", allowed.status_code in (200, 503),
      f"status={allowed.status_code} ({'no API key configured' if allowed.status_code == 503 else 'ok'})")
vl = c.post(f"{BASE}/voice/transcribe", headers=doc, files={"audio": ("a.wav", b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\0" * 40, "audio/wav")},
            data={"subject_type": "pregnancy", "subject_id": p["id"], "provider": "voicelab", "consent_external": "false"})
check("VoiceLab blocked without explicit consent", vl.status_code == 403 and "consent" in vl.json()["error"]["message"],
      vl.json()["error"]["message"])
check("audit chain", c.get(f"{BASE}/audit-logs/verify", headers=aud).json()["valid"] is True)
pe = c.get(f"{BASE}/privacy-events", headers=aud).json()
check("privacy events logged without bodies", all("question" not in e for e in pe), f"{len(pe)} events")
print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed in {time.time() - t0:.1f}s")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)

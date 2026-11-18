from __future__ import annotations

import sys
from pathlib import Path

import httpx

base = sys.argv[1].rstrip("/") + "/api/v1"
audio_path, lang = Path(sys.argv[2]), (sys.argv[3] if len(sys.argv) > 3 else "uz")
cred = dict(l.split("=", 1) for l in (Path(__file__).resolve().parents[1] / ".seed_credentials.txt").read_text(encoding="utf-8").splitlines()
            if "=" in l and not l.startswith("#"))
c = httpx.Client(timeout=120)
tok = c.post(f"{base}/auth/login", json={"username": "qr.obgyn", "password": cred["password"].strip()}).json()["access_token"]
h = {"Authorization": f"Bearer {tok}"}
print("voice providers:", c.get(f"{base}/voice/providers", headers=h).json()["local"])
fac = next(f for f in c.get(f"{base}/facilities?region_soato=1735", headers=h).json() if f["code"] == "DEMO-1735-MAT")
m = c.post(f"{base}/mothers", headers=h, json={"full_name": "Audio Test (DEMO)", "facility_id": fac["id"], "is_synthetic": True}).json()
p = c.post(f"{base}/mothers/{m['id']}/pregnancies", headers=h, json={"lmp_date": "2026-04-01"}).json()
mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "webm": "audio/webm"}[audio_path.suffix[1:]]
r = c.post(f"{base}/voice/transcribe", headers=h, files={"audio": (audio_path.name, audio_path.read_bytes(), mime)},
           data={"subject_type": "pregnancy", "subject_id": p["id"], "language": lang})
r.raise_for_status()
d = r.json()
print("provider:", d["provider"], "| location:", d["processing_location"])
print("transcript:", d["transcript"])
for x in d["candidates"]:
    print(f"  {x['field']:<18} {x['value']!s:<6} conf={x['confidence']:<5} review={x['needs_review']} {x['note'] or ''}")
conf = c.post(f"{base}/voice/drafts/{d['draft_id']}/confirm", headers=h, json={
    "reviewed_transcript": True, "fields": [{"field": x["field"], "value": x["value"]} for x in d["candidates"]
                                            if not x["field"].startswith("lab_")]})
print("confirm:", conf.status_code, conf.json())

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import tempfile
import warnings
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

warnings.filterwarnings("ignore")
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
_tmp = Path(tempfile.mkdtemp(prefix="live_check_"))
src = BACKEND / "perinatal_live.db"
if not src.exists():
    print("FAIL database: perinatal_live.db not found (run scripts/seed_national.py)")
    sys.exit(1)
import sqlite3  # noqa: E402

_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)  # consistent snapshot incl. WAL
_dst = sqlite3.connect((_tmp / "check.db").as_posix())
_con.backup(_dst)
_con.close()
_dst.close()
os.environ.update({"DATABASE_URL": f"sqlite:///{(_tmp / 'check.db').as_posix()}", "LIVE_SIMULATOR": "0", "CLAUDE_API_KEY": "",
                   "LIVE_DEMO_LOGIN": "1", "APP_ENV": "development", "LOG_LEVEL": "ERROR", "LOKY_MAX_CPU_COUNT": "1"})

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.v1 import live  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.deps import rate_limiter  # noqa: E402
from app.core.security import decrypt  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.core.security import utcnow  # noqa: E402
from app.models import Alert, AuditLog, CareAssignment, Mother, Newborn, Pregnancy, PrivacyEvent  # noqa: E402
from app.services import live_engine, live_sim  # noqa: E402
from app.services.live_engine import geo  # noqa: E402
from app.services.llm import privacy_gateway as pg  # noqa: E402
from app.services.llm.claude_provider import ClaudeProvider  # noqa: E402
from app.services.official_stats import stats  # noqa: E402

B = "/api/v1/live"
results: list[bool] = []


def check(name: str, ok: bool, info: str = "") -> bool:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}{' — ' + info if info else ''}")
    return bool(ok)


def _denied(reason: str | None = None) -> int:
    with SessionLocal() as db:
        rows = db.execute(select(AuditLog.detail).where(AuditLog.outcome == "denied")).scalars()
        return sum(1 for d in rows if reason is None or (d or {}).get("reason") == reason)


def role_scope_checks(c, nurse, spec, leader, pid) -> None:
    """Gap 1 + 2: role enforcement, scope (404), search scoping, denials audited, unknown fields rejected."""
    rate_limiter.reset()
    with SessionLocal() as db:
        geo.load(db)
        demo_facs = {f for f, d in geo.fac_district.items() if d == geo.demo_district}
        spec_region = geo.districts[geo.demo_district]["region_soato"]
        region_facs = {f for f, r in geo.fac_region.items() if r == spec_region}
        mine = set(db.execute(select(CareAssignment.mother_id).where(CareAssignment.user_id == geo.demo_nurse_id,
                                                                     CareAssignment.active.is_(True))).scalars())
        pregs = list(db.execute(select(Pregnancy.id, Pregnancy.facility_id, Pregnancy.mother_id).where(Pregnancy.status == "active")))
        alerted = set(db.execute(select(Alert.subject_id)).scalars())
        foreign_preg = next(i for i, f, m in pregs if f not in region_facs)
        other_district = next(i for i, f, m in pregs if f in region_facs and f not in demo_facs and m not in mine)
        no_alert_preg = next((i for i, f, m in pregs if f in region_facs and i not in alerted), None)
        foreign_alert = db.execute(select(Alert.id, Alert.facility_id).where(Alert.status == "open")).all()
        foreign_alert = next((i for i, f in foreign_alert if f not in region_facs), None)
        foreign_nb = next((i for i, f in db.execute(select(Newborn.id, Newborn.facility_id)) if f not in region_facs), None)
    before = _denied()
    ok_visit = {"pregnancy_id": pid, "systolic_bp": 118, "diastolic_bp": 76}
    codes = [c.post(f"{B}/visits", headers=h, json=ok_visit).status_code for h in (spec, leader)]
    codes += [c.get(f"{B}/nurse/today", headers=spec).status_code, c.get(f"{B}/inbox", headers=nurse).status_code]
    codes += [c.post(f"{B}/alerts/{foreign_alert}/ack", headers=h).status_code for h in (nurse, leader)]
    codes += [c.post(f"{B}/alerts/{foreign_alert}/decide", headers=h, json={"decision": "no_risk"}).status_code for h in (nurse, leader)]
    check("roles: only the nurse may POST /visits and read /nurse/today; only the specialist /inbox, ack, decide (403)",
          codes == [403] * 8, f"codes={codes}")
    r1 = c.post(f"{B}/visits", headers=nurse, json=ok_visit | {"pregnancy_id": foreign_preg})
    r2 = c.post(f"{B}/visits", headers=nurse, json=ok_visit | {"pregnancy_id": other_district})
    check("nurse: a visit for a woman outside her assignments / district -> 404", r1.status_code == 404 and r2.status_code == 404,
          f"{r1.status_code}/{r2.status_code}")
    r1, r2 = c.get(f"{B}/patients/{pid}", headers=nurse), c.get(f"{B}/patients/{foreign_preg}", headers=nurse)
    r3 = c.get(f"{B}/newborns/{foreign_nb}", headers=nurse)
    check("nurse: patient card only for her assigned women (200 own, 404 others, 404 unassigned newborn)",
          r1.status_code == 200 and r2.status_code == 404 and r3.status_code == 404, f"{r1.status_code}/{r2.status_code}/{r3.status_code}")
    a1 = c.post(f"{B}/alerts/{foreign_alert}/ack", headers=spec)
    a2 = c.post(f"{B}/alerts/{foreign_alert}/decide", headers=spec, json={"decision": "no_risk"})
    with SessionLocal() as db:
        untouched = db.get(Alert, foreign_alert).status == "open"
    check("specialist: ack / decide on an alert of another region -> 404, alert untouched",
          a1.status_code == 404 and a2.status_code == 404 and untouched, f"{a1.status_code}/{a2.status_code}")
    r1 = c.get(f"{B}/patients/{foreign_preg}", headers=spec)
    r2 = c.get(f"{B}/patients/{no_alert_preg}", headers=spec) if no_alert_preg else None
    r3 = c.get(f"{B}/newborns/{foreign_nb}", headers=spec)
    check("specialist: card only for patients of her region that have (or had) an alert (404 otherwise)",
          r1.status_code == 404 and (r2 is None or r2.status_code == 404) and r3.status_code == 404,
          f"{r1.status_code}/{r2.status_code if r2 is not None else '-'}/{r3.status_code}")
    with SessionLocal() as db:
        fac_of = {a.id: a.facility_id for a in db.execute(select(Alert).where(Alert.status.in_(("open", "acknowledged")))).scalars()}
    ib = c.get(f"{B}/inbox", headers=spec).json()
    check("specialist inbox contains only alerts of her region", bool(ib["items"])
          and all(fac_of.get(i["alert_id"]) in region_facs for i in ib["items"]), f"items={len(ib['items'])}")
    by_id = {i: (f, m) for i, f, m in pregs}
    hits_n, hits_s = [], []
    for q in ("ova", "yeva", "Tursunova", "PRG", "an"):
        hits_n += [x["id"] for x in c.get(f"{B}/search", headers=nurse, params={"q": q}).json()["results"] if x["kind"] == "patient"]
        hits_s += [x["id"] for x in c.get(f"{B}/search", headers=spec, params={"q": q}).json()["results"] if x["kind"] == "patient"]
    check("/search is scoped: nurse -> her assigned women, specialist -> alerted patients of her region",
          bool(hits_n) and all(by_id[i][1] in mine for i in hits_n) and bool(hits_s)
          and all(by_id[i][0] in region_facs and i in alerted for i in hits_s), f"nurse={len(hits_n)} specialist={len(hits_s)}")
    check("every denial is audited (role_not_allowed, not_assigned, outside_region, no_alert_for_patient)",
          _denied() - before >= 8 + 2 + 2 + 2 + 2 and _denied("role_not_allowed") >= 8 and _denied("not_assigned") >= 4
          and _denied("outside_region") >= 4 and (no_alert_preg is None or _denied("no_alert_for_patient") >= 1),
          f"new denied rows={_denied() - before}")
    r = c.post(f"{B}/visits", headers=nurse, json=ok_visit | {"zone": "green"})
    r2 = c.post(f"{B}/alerts/{foreign_alert}/decide", headers=spec, json={"decision": "no_risk", "status": "resolved"})
    check("live POST bodies reject unknown fields (/visits, /decide) -> 422", r.status_code == 422 and r2.status_code == 422
          and r.json()["error"]["details"][0]["type"] == "extra_forbidden", f"{r.status_code}/{r2.status_code}")
    rate_limiter.reset()


def escalation_and_backlog_checks(c, nurse, spec, leader, pid, district_code) -> None:
    """Gap 4: a back-dated deadline is escalated by the REAL scheduler loop. Gap 3: backlog caps + /map consistency."""
    c.post(f"{B}/demo/scenario", headers=leader, json={"name": "reset"})
    v = c.post(f"{B}/visits", headers=nurse, json={"pregnancy_id": pid, "systolic_bp": 150, "diastolic_bp": 95,
                                                   "severe_headache": True, "source": "voice"}).json()
    aid = (v.get("alert") or {}).get("id")
    last_id = max((e["id"] for e in c.get(f"{B}/events", headers=leader, params={"limit": 200}).json()), default=0)
    with live_engine.write_tx() as db:   # the nurse's alert was raised 31 minutes ago and nobody answered
        a = db.get(Alert, aid)
        a.created_at, a.sla_due_at = a.created_at - timedelta(minutes=31), utcnow() - timedelta(minutes=1)
    level, t0 = 0, time.monotonic()
    while time.monotonic() - t0 < live_sim.SCHEDULER_INTERVAL_SECONDS + 15:   # wait for the background scheduler tick
        with SessionLocal() as db:
            level = db.get(Alert, aid).escalation_level
        if level:
            break
        time.sleep(0.5)
    with SessionLocal() as db:
        a = db.get(Alert, aid)
        due_ok = a.sla_due_at > utcnow() and a.status == "open"
        audited = db.execute(select(AuditLog).where(AuditLog.action == "alert.escalated", AuditLog.resource_id == aid)).first()
    check("escalation: scheduler tick runs escalate_overdue on a back-dated deadline (level raised, audited)",
          level >= 1 and due_ok and bool(audited), f"level={level} after {time.monotonic() - t0:.0f}s")
    evs = [e for e in c.get(f"{B}/events", headers=leader, params={"limit": 200}).json()
           if e["id"] > last_id and e["type"] == "escalation" and e["district_code"] == district_code]
    check("escalation: an `escalation` event with an Uzbek text is published", bool(evs) and "javobsiz qoldi" in evs[0]["text"]
          and "yuqori bosqichga oʻtkazildi" in evs[0]["text"], evs[0]["text"] if evs else "no event")
    item = next((i for i in c.get(f"{B}/inbox", headers=spec).json()["items"] if i["alert_id"] == aid), None)
    case = next((x for x in c.get(f"{B}/districts/{district_code}", headers=leader).json()["cases"] if x["alert_id"] == aid), None)
    mp = c.get(f"{B}/map", headers=leader).json()
    dd = next(d for d in mp["districts"] if d["code"] == district_code)
    check("escalation: specialist inbox + district cases show it (escalation_level, overdue); the district turns red",
          bool(item) and item["escalation_level"] >= 1 and item["overdue"] is True and bool(case)
          and case["escalation_level"] >= 1 and case["overdue"] is True and dd["status"] == "red")

    # ---- backlog: 46 unanswered simulated alerts (half of them hours old) + 8 newborn alerts appear at once
    live_sim._load_population()
    rng = live_sim.rnd
    rng.seed(7)
    made = 0
    for i, preg_id in enumerate(rng.sample(live_sim.st.preg_ids, 46)):
        with live_engine.write_tx() as db:
            res = live_engine.record_visit(db, db.get(Pregnancy, preg_id), {"systolic_bp": 166, "diastolic_bp": 112, "heart_rate": 84},
                                           actor=None, confirmed=True, with_ml=False)
            if res.alert is not None and i % 2 == 0:
                res.alert.created_at -= timedelta(hours=5)
                res.alert.sla_due_at -= timedelta(hours=5)
            made += res.alert is not None
    for nb_id in rng.sample(live_sim.st.newborn_ids, 8):
        with live_engine.write_tx() as db:
            live_engine.record_newborn_obs(db, db.get(Newborn, nb_id), {"temperature_c": 35.5, "heart_rate": 140}, actor=None)
    before = c.get(f"{B}/map", headers=leader).json()["totals"]
    for _ in range(20):   # in production: one pass every ~12 s, at most 40 closures per pass
        if not live_sim._rebalance()["closed"]:
            break
    t = c.get(f"{B}/map", headers=leader).json()["totals"]
    with SessionLocal() as db:
        demo_open = db.get(Alert, aid).status == "open"
        n_open = len(list(db.execute(select(Alert.id).where(Alert.status == "open")).scalars()))
    check("simulator backlog cap: waiting 8-20, overdue 2-6, waiting newborns <= 4; the demo district is never touched",
          before["waiting"] > 40 and 8 <= t["waiting"] <= 20 and 2 <= t["overdue"] <= 6 and t["waiting_newborns"] <= 4 and demo_open,
          f"before waiting={before['waiting']} overdue={before['overdue']} newborn={before['waiting_newborns']} -> "
          f"after waiting={t['waiting']} overdue={t['overdue']} newborn={t['waiting_newborns']}")
    ib = c.get(f"{B}/inbox", headers=spec).json()
    mp = c.get(f"{B}/map", headers=leader).json()
    reg = next(r for r in mp["regions"] if r["soato"] == geo.districts[district_code]["region_soato"])
    check("/map totals use the /inbox definitions (waiting = status open)", mp["totals"]["waiting"] == n_open
          == mp["totals"]["waiting_mothers"] + mp["totals"]["waiting_newborns"] == sum(d["open_alerts"] for d in mp["districts"])
          and reg["open_alerts"] == ib["counts"]["waiting"]
          and reg["overdue_alerts"] == sum(i["overdue"] for i in ib["items"]),
          f"national waiting={n_open}, region waiting={reg['open_alerts']} = inbox {ib['counts']['waiting']}")
    a = c.post(f"{B}/alerts/{aid}/ack", headers=spec)
    d = c.post(f"{B}/alerts/{aid}/decide", headers=spec, json={"decision": "refer", "note": None})
    check("stage still works after escalation: ack + refer", a.status_code == 200 and d.json().get("status") == "resolved")


def complementary_suppression_checks() -> None:
    """Gap 5: a "<5" cell can never be recomputed from a published total."""
    def recomputable(cells: dict, total: str, parts: tuple) -> bool:
        if not isinstance(cells[total], int):
            return False
        hidden = [p for p in parts if not isinstance(cells[p], int)]
        small = [p for p in hidden if cells[p] == pg.SMALL]
        if not small or any(cells[p] == pg.MASKED for p in hidden):
            return False
        rem = cells[total] - sum(cells[p] for p in parts if p not in hidden)
        return len(hidden) == 1 or rem <= 0 or rem >= 4 * len(hidden)
    zp, ap = ("red", "yellow", "green", "grey"), ("open_mothers", "open_newborns")
    bad = n = masked = 0
    with SessionLocal() as db:
        for scope, ident in [("national", None)] + [("region", r) for r in geo.regions] + [("district", d) for d in geo.districts]:
            w = pg.build_brief_payload(live._brief_raw(db, scope, ident)).model_dump(mode="json")
            n += 1
            bad += recomputable(w["zones"], "pregnant", zp) or recomputable(w["alerts"], "open", ap)
            masked += pg.MASKED in w["zones"].values() or pg.MASKED in w["alerts"].values()
    one = pg.build_brief_payload({"scope": "district", "area_name": "Yunusobod", "zones": {"pregnant": 40, "red": 3, "yellow": 9,
                                  "green": 21, "grey": 7, "newborns": 6}, "alerts": {"open": 9, "overdue": 1, "open_mothers": 7,
                                                                                      "open_newborns": 2}}).model_dump(mode="json")
    check(f"complementary suppression: no \"<5\" cell is recomputable from a total in {n} payloads",
          bad == 0 and one["zones"]["red"] == "<5" and one["zones"]["green"] == "masked" and one["zones"]["pregnant"] == 40
          and one["alerts"]["open_newborns"] == "<5" and one["alerts"]["open_mothers"] == "masked"
          and not pg.small_cell_violations(one), f"payloads with a masked cell={masked}")
    s = get_settings()
    check("k is 5 everywhere it is user-visible (REPORT_MIN_CELL = K_ANON = 5)", s.REPORT_MIN_CELL == pg.K_ANON == 5
          and pg.BRIEF_POLICY["k_anonymity"] == 5)


# ====================================================================== accounts, public pages, admin console
A, ADM, PUB = "/api/v1/account", "/api/v1/admin", "/api/v1/public"
ORIGIN = "http://127.0.0.1:5173"
FAC = "/api/v1/facility"


def _cbor(v) -> bytes:
    """Tiny CBOR encoder for the software authenticator below (ints, bytes, text, maps)."""
    def head(major: int, n: int) -> bytes:
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        return bytes([major << 5 | 25]) + n.to_bytes(2, "big")
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    return head(5, len(v)) + b"".join(_cbor(k) + _cbor(x) for k, x in v.items())


class SoftAuthenticator:
    """ES256 software passkey: produces what navigator.credentials.create()/get() would send."""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.count = 0

    def _auth_data(self, rp_id: str, flags: int, attested: bool) -> bytes:
        import hashlib
        self.count += 1
        ad = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + self.count.to_bytes(4, "big")
        if attested:
            n = self.key.public_key().public_numbers()
            cose = {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
            ad += bytes(16) + len(self.cred_id).to_bytes(2, "big") + self.cred_id + _cbor(cose)
        return ad

    @staticmethod
    def _client(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()

    def create(self, pk: dict, origin: str = ORIGIN, flags: int = 0x45) -> dict:
        from app.services.webauthn import b64u
        ad = self._auth_data(pk["rp"]["id"], flags, True)
        att = _cbor({"fmt": "none", "attStmt": {}, "authData": ad})
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(self._client("webauthn.create", pk["challenge"], origin)),
                             "attestationObject": b64u(att)}}

    def get(self, opts: dict, origin: str = ORIGIN, flags: int = 0x05, tamper: bool = False, user_id: str | None = None) -> dict:
        import hashlib
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from app.services.webauthn import b64u
        pk = opts["publicKey"]
        ad = self._auth_data(pk["rpId"], flags, False)
        cd = self._client("webauthn.get", pk["challenge"], origin)
        sig = self.key.sign(ad + hashlib.sha256(b"x" + cd if tamper else cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"state": opts["state"], "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(cd), "authenticatorData": b64u(ad), "signature": b64u(sig),
                             "userHandle": b64u(user_id.encode()) if user_id else None}}


def account_checks(c, leader) -> None:
    import pyotp
    from app.api.v1 import console, public
    from app.models import LoginChallenge, PasskeyCredential, User
    s = get_settings()
    oh = {"Origin": ORIGIN}
    tag = os.urandom(3).hex()
    mail = f"Nurse.{tag}@Check.UZ"

    def H(tok):
        return {"Authorization": f"Bearer {tok}"}

    def start(mode, ident, channel="email", name="Sinov Hamshira"):
        body = {"mode": mode, "channel": channel, "identifier": ident}
        if mode == "register":
            body["full_name"] = name
        return c.post(f"{A}/start", json=body)

    def unlock():
        with SessionLocal() as db:
            u = db.get(User, uid)
            u.locked_until, u.failed_logins = None, 0
            db.commit()

    def signin(ident, mode="login", channel="email"):
        rate_limiter.reset()
        st = start(mode, ident, channel).json()
        return c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": st["dev_code"]})

    rate_limiter.reset()
    s.ADMIN_BOOTSTRAP_IDENTIFIER = f"boss.{tag}@check.uz"
    # ---- public pages
    po = c.get(f"{PUB}/overview")
    pj = po.json()
    live_ok = set(pj.get("live", {})) == {"pregnant", "waiting", "median_response_min", "answered_24h"}
    check("public overview: no sign-in, official national totals + national live totals only, cached 60 s",
          po.status_code == 200 and pj["totals"]["regions"] == 14 and pj["totals"]["districts"] == 199 and live_ok
          and bool(pj["totals"].get("births")) and "max-age=60" in po.headers.get("cache-control", "")
          and all(v is None or v == 0 or v >= 5 for v in pj["live"].values()),
          f"births={pj['totals'].get('births')} ({pj['totals'].get('births_year')}) live={pj.get('live')}")
    pm = c.get(f"{PUB}/official-map")
    mj = pm.json()
    with SessionLocal() as db:
        geo.load(db)
    codes = set(geo.districts)
    blob = po.text + pm.text + c.get(f"{PUB}/official-map", params={"indicator": "births_total"}).text
    check("public endpoints expose no district live data (region level only, no district code anywhere)",
          pm.status_code == 200 and len(mj["regions"]) == 14 and "districts" not in mj and "districts" not in pj
          and set(mj["regions"][0]) == {"soato", "name", "value", "national", "status"}
          and not any(f'"{code}"' in blob for code in codes) and "pregnancy_id" not in blob and "open_alerts" not in blob,
          f"regions={len(mj.get('regions', []))} year={mj.get('year')}")
    check("public: unknown indicator -> 422; no patient/live route under /public",
          c.get(f"{PUB}/official-map", params={"indicator": "nope_x"}).status_code == 422
          and c.get(f"{PUB}/map").status_code == 404 and c.get(f"{PUB}/districts/x").status_code == 404)
    rate_limiter.reset()
    seen = [c.get(f"{PUB}/overview").status_code for _ in range(s.PUBLIC_RATE_PER_MINUTE + 2)]
    check("public endpoints are rate-limited per IP (429)", seen[0] == 200 and seen[-1] == 429)
    rate_limiter.reset()

    # ---- register via e-mail code -> pending
    r = start("register", mail)
    st = r.json()
    unknown = start("login", f"ghost.{tag}@check.uz")
    uj = unknown.json()
    check("/account/start: 6-digit dev_code (no provider, dev), 300 s, masked address, e-mail lower-cased",
          r.status_code == 200 and re.fullmatch(r"\d{6}", st.get("dev_code") or "") is not None and st["expires_in"] == 300
          and st["sent_to"] == f"n***@check.uz", f"sent_to={st.get('sent_to')}")
    check("no user enumeration: unknown login gets the same response shape, and its challenge never verifies",
          unknown.status_code == 200 and set(uj) == set(st) and re.fullmatch(r"\d{6}", uj["dev_code"] or "") is not None
          and c.post(f"{A}/verify", json={"challenge_id": uj["challenge_id"], "code": uj["dev_code"]}).status_code == 400)
    with SessionLocal() as db:
        rows = list(db.execute(select(LoginChallenge)).scalars())
        dump = json.dumps([[x.code_hash, x.payload, x.token_hash] for x in rows], default=str)
        check("codes and challenge handles are hashed at rest; the address is stored encrypted",
              st["dev_code"] not in dump and st["challenge_id"] not in dump and "check.uz" not in dump.lower() and len(rows) >= 2)
    bad = c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": "000000" if st["dev_code"] != "000000" else "111111"})
    v = c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": st["dev_code"]})
    vj = v.json()
    pend = H(vj.get("access_token", ""))
    uid = vj.get("user", {}).get("id")
    check("register via e-mail code -> session with role pending (wrong code first: 400)", bad.status_code == 400
          and v.status_code == 200 and vj["user"]["role"] == "pending" and vj["user"]["email"] == mail.lower()
          and vj["user"]["two_step"] is False, f"role={vj.get('user', {}).get('role')}")
    check("a one-time code cannot be used twice",
          c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": st["dev_code"]}).status_code == 400)
    codes_seen = [c.get(f"{B}/map", headers=pend).status_code, c.get(f"{B}/nurse/today", headers=pend).status_code,
                  c.get(f"{B}/inbox", headers=pend).status_code, c.post(f"{B}/stream-ticket", headers=pend).status_code,
                  c.get(f"{ADM}/overview", headers=pend).status_code, c.get("/api/v1/me", headers=pend).status_code]
    me = c.get(f"{A}/me", headers=pend)
    check("pending user gets 403 on /live/map and every other live / admin endpoint, but may read /account/me",
          codes_seen[:5] == [403] * 5 and codes_seen[5] in (401, 403) and me.status_code == 200 and me.json()["role"] == "pending",
          f"codes={codes_seen}")
    check("/account/security two_step=true without a second factor -> 422",
          c.post(f"{A}/security", headers=pend, json={"two_step": True}).status_code == 422)

    # ---- admin: demo persona, assign the nurse role
    r = c.post(f"{B}/login", json={"persona": "admin"})
    aj = r.json()
    admin = H(aj.get("access_token", ""))
    check('demo persona "admin": role admin, persona leader, may open the map',
          r.status_code == 200 and aj["user"]["role"] == "admin" and aj["user"]["persona"] == "leader"
          and aj["user"]["name"] == "Admin — tizim boshqaruvchisi" and c.get(f"{B}/map", headers=admin).status_code == 200)
    pl = c.get(f"{ADM}/users", headers=admin, params={"status": "pending"}).json()["items"]
    item_keys = {"id", "name", "email", "phone", "role", "status", "two_step", "created_at", "last_login_at", "region_name",
                 "district_name"}
    check("admin sees the pending account in /admin/users?status=pending", any(i["id"] == uid for i in pl)
          and item_keys <= set(pl[0]) and all(i["status"] == "pending" for i in pl))
    r0 = c.post(f"{ADM}/users/{uid}", headers=admin, json={"role": "nurse", "district_code": geo.demo_district})
    r00 = c.post(f"{ADM}/users/{uid}", headers=admin, json={"role": "specialist", "region_soato": geo.demo_district[:4]})
    still = next(i for i in c.get(f"{ADM}/users", headers=admin).json()["items"] if i["id"] == uid)
    check("system admin cannot grant nurse / specialist via /admin/users (422, account stays pending)",
          r0.status_code == 422 and r00.status_code == 422 and still["role"] == "pending", f"{r0.status_code}/{r00.status_code}")
    fadm = H(c.post(f"{B}/login", json={"persona": "facility_admin"}).json().get("access_token", ""))
    ghost = c.post(f"{FAC}/staff/claim", headers=fadm, json={"identifier": f"nobody.{tag}@check.uz"})
    r1 = c.post(f"{FAC}/staff/claim", headers=fadm, json={"identifier": mail})
    check("facility admin claims the waiting account -> active nurse of that facility (unknown address: 404)",
          ghost.status_code == 404 and r1.status_code == 200 and r1.json()["item"]["role"] == "nurse"
          and r1.json()["item"]["status"] == "active" and bool(r1.json()["item"]["district_name"]),
          f"{ghost.status_code}/{r1.status_code} district={r1.json().get('item', {}).get('district_name')}")
    v = signin(mail)
    nj = v.json()
    nurse2 = H(nj.get("access_token", ""))
    today = c.get(f"{B}/nurse/today", headers=nurse2)
    check("login after approval -> nurse session; no assigned patients = empty list; other roles' endpoints 403",
          v.status_code == 200 and nj["user"]["role"] == "nurse" and nj["user"]["persona"] == "nurse"
          and today.status_code == 200 and today.json()["tasks"] == [] and c.get(f"{B}/inbox", headers=nurse2).status_code == 403
          and c.get(f"{B}/map", headers=nurse2).status_code == 200, f"today={today.status_code}")
    codes_seen = [c.get(f"{ADM}/{path}", headers=h).status_code for h in (nurse2, leader) for path in
                  ("overview", "users", "export.csv", "audit")] + [c.post(f"{ADM}/users/{uid}", headers=nurse2, json={"role": "admin"}).status_code,
                                                                  c.get(f"{ADM}/overview").status_code]
    check("non-admin gets 403 on /admin/* (nurse, demo leader; role change attempt too); no token -> 401",
          codes_seen == [403] * 9 + [401], f"codes={codes_seen}")

    # ---- admin overview + export + audit
    console.clear_cache()
    t0 = time.perf_counter()
    r = c.get(f"{ADM}/overview", headers=admin, params={"days": 30})
    cold = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    c.get(f"{ADM}/overview", headers=admin, params={"days": 30})
    warm = (time.perf_counter() - t0) * 1000
    oj = r.json()
    kp = {"pregnant", "newborns", "red", "yellow", "green", "grey", "open_alerts", "overdue_alerts", "median_response_min",
          "p90_response_min", "answered_in_sla_pct", "visits_7d", "voice_share_pct", "users", "pending_users"}
    mp = c.get(f"{B}/map", headers=admin).json()
    check("admin overview works: real tables, contract keys, consistent with /live/map, < 300 ms",
          r.status_code == 200 and set(oj["kpis"]) == kp and len(oj["by_region"]) == 14 and len(oj["series"]["alerts_by_day"]) == 30
          and set(oj["series"]) == {"alerts_by_day", "response_by_day", "visits_by_day"} and oj["kpis"]["pregnant"] == mp["totals"]["pregnant"]
          and oj["kpis"]["red"] == mp["totals"]["red"] and bool(oj["top_reasons"]) and all(x["label"] for x in oj["top_reasons"])
          and cold < 300, f"cold={cold:.0f} ms cached={warm:.0f} ms pregnant={oj['kpis']['pregnant']} "
          f"open={oj['kpis']['open_alerts']} median={oj['kpis']['median_response_min']} min users={oj['kpis']['users']}")
    blob = r.text
    with SessionLocal() as db:
        names = [decrypt(m.full_name_enc) for m in db.execute(select(Mother).limit(300)).scalars()]
    check("admin overview carries aggregates only (no patient names, no record ids)",
          not any(n and n in blob for n in names) and "pregnancy_id" not in blob and "mother" not in blob)
    small = re.compile(r"^[1-4]$")
    ok_csv, sizes = True, []
    for kind in ("regions", "districts", "alerts"):
        e = c.get(f"{ADM}/export.csv", headers=admin, params={"kind": kind})
        lines = [ln.split(",") for ln in e.text.lstrip("﻿").strip().splitlines()]
        head, body = lines[0], lines[1:]
        cols = [i for i, h in enumerate(head) if h in ("pregnant", "red", "yellow", "open_alerts", "overdue_alerts", "count")]
        ok_csv &= e.status_code == 200 and e.headers["content-type"].startswith("text/csv") and bool(body) \
            and not any(small.match(row[i]) for row in body for i in cols)
        sizes.append(f"{kind}={len(body)}")
    check("CSV export (regions / districts / alerts): aggregates, every cell 1..4 masked", ok_csv, " ".join(sizes))
    au = c.get(f"{ADM}/audit", headers=admin, params={"limit": 100}).json()
    with SessionLocal() as db:
        acts = [a for (a,) in db.execute(select(AuditLog.action).order_by(AuditLog.seq.desc()).limit(400))]
        details = json.dumps([d for (d,) in db.execute(select(AuditLog.detail).order_by(AuditLog.seq.desc()).limit(400))], default=str)
    need = {"account.start", "account.register", "account.login", "facility.staff.claim", "admin.overview.read", "admin.users.read",
            "admin.export", "admin.audit.read", "admin.access", "live.access"}
    check("admin audit has the /live/audit shape; sign-up, sign-in, role change, admin reads and denials are audited; no code in it",
          set(au) == {"items", "chain_ok"} and {"at", "actor", "action", "resource", "outcome"} <= set(au["items"][0])
          and need <= set(acts) and st["dev_code"] not in details and "check.uz" not in details.lower(),
          f"missing={sorted(need - set(acts))}")

    # ---- TOTP -> second factor
    su = c.post(f"{A}/totp/setup", headers=nurse2).json()
    totp = pyotp.TOTP(su["secret"])
    wrong = c.post(f"{A}/totp/enable", headers=nurse2, json={"code": "000000" if totp.now() != "000000" else "111111"})
    en = c.post(f"{A}/totp/enable", headers=nurse2, json={"code": totp.now()})
    ej = en.json()
    check("TOTP enable: otpauth URI, wrong code 400, right code -> 8 recovery codes, two-step on",
          su["otpauth_uri"].startswith("otpauth://totp/") and wrong.status_code == 400 and en.status_code == 200
          and len(ej["recovery_codes"]) == 8 and c.get(f"{A}/security", headers=nurse2).json()["two_step"] is True)
    v = signin(mail)
    tj = v.json()
    check("login now requires a second factor (no session after the code alone)", v.status_code == 200
          and tj.get("second_factor_required") is True and tj.get("methods") == ["totp"] and "access_token" not in tj)
    nxt = totp.at(time.time() + 30)  # the current step was consumed by "enable": single use
    w = c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "totp", "code": "000000" if nxt != "000000" else "111111"})
    replay = c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "totp", "code": totp.now()})
    g = c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "totp", "code": nxt})
    check("second factor: wrong code rejected, replayed (already used) code rejected, right code -> session",
          w.status_code == 400 and replay.status_code == 400 and g.status_code == 200 and g.json()["user"]["two_step"] is True,
          f"{w.status_code}/{replay.status_code}/{g.status_code}")
    check("a second-factor ticket is single use",
          c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "totp", "code": totp.at(time.time() + 60)}).status_code == 400)
    tj = signin(mail).json()
    rc = c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "recovery", "code": ej["recovery_codes"][0]})
    tj2 = signin(mail).json()
    rc2 = c.post(f"{A}/second", json={"ticket": tj2["ticket"], "method": "recovery", "code": ej["recovery_codes"][0]})
    check("recovery code works once", rc.status_code == 200 and rc2.status_code == 400)
    tj = signin(mail).json()
    tries = [c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "totp", "code": "12345" + str(i)}).status_code for i in range(5)]
    late = c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "totp", "code": totp.at(time.time() + 60)})
    check("5 wrong attempts burn the ticket (the right code no longer works)", tries == [400] * 5 and late.status_code == 400)
    unlock()
    with SessionLocal() as db:
        u = db.get(User, uid)
        check("TOTP secret is encrypted and recovery codes are hashed at rest", su["secret"] not in (u.totp_secret_enc or "")
              and decrypt(u.totp_secret_enc) == su["secret"] and not any(x.replace("-", "") in json.dumps(u.recovery_codes) for x in ej["recovery_codes"])
              and "@" not in (u.email_enc or "") and decrypt(u.email_enc) == mail.lower())
    rate_limiter.reset()

    # ---- passkeys (software ES256 authenticator)
    sa = SoftAuthenticator()
    no_origin = c.post(f"{A}/passkey/register/options", headers=nurse2)
    evil = c.post(f"{A}/passkey/register/options", headers=nurse2 | {"Origin": "https://evil.example"})
    ro = c.post(f"{A}/passkey/register/options", headers=nurse2 | oh)
    pk = ro.json().get("publicKey", {})
    check("passkey options: RP id from the request origin, only configured origins, UV required, attestation none",
          no_origin.status_code == 400 and evil.status_code == 400 and ro.status_code == 200 and pk["rp"]["id"] == "127.0.0.1"
          and pk["attestation"] == "none" and pk["authenticatorSelection"]["userVerification"] == "required"
          and {p["alg"] for p in pk["pubKeyCredParams"]} == {-7, -257})
    bad_o = c.post(f"{A}/passkey/register/verify", headers=nurse2 | oh, json=sa.create(pk, origin="https://evil.example"))
    pk = c.post(f"{A}/passkey/register/options", headers=nurse2 | oh).json()["publicKey"]
    no_uv = c.post(f"{A}/passkey/register/verify", headers=nurse2 | oh, json=SoftAuthenticator().create(pk, flags=0x41))
    pk = c.post(f"{A}/passkey/register/options", headers=nurse2 | oh).json()["publicKey"]
    good = c.post(f"{A}/passkey/register/verify", headers=nurse2 | oh, json=sa.create(pk))
    again = c.post(f"{A}/passkey/register/verify", headers=nurse2 | oh, json=sa.create(pk))
    check("passkey registration: wrong origin / no user verification / reused challenge rejected, valid one stored",
          bad_o.status_code == 400 and no_uv.status_code == 400 and good.status_code == 200 and again.status_code == 400
          and len(good.json()["passkeys"]) == 1 and good.json()["ok"] is True,
          f"{bad_o.status_code}/{no_uv.status_code}/{good.status_code}/{again.status_code}")

    def opts(**body):
        return c.post(f"{A}/passkey/login/options", headers=oh, json=body).json()
    o1 = opts(identifier=mail)
    ghost = opts(identifier=f"ghost.{tag}@check.uz")
    t1 = c.post(f"{A}/passkey/login/verify", headers=oh, json=sa.get(o1, tamper=True))
    t2 = c.post(f"{A}/passkey/login/verify", headers=oh, json=sa.get(opts(identifier=mail), origin="https://evil.example"))
    t3 = c.post(f"{A}/passkey/login/verify", headers=oh, json=sa.get(opts(identifier=mail), flags=0x01))
    o2 = opts(identifier=mail)
    msg = sa.get(o2, user_id=uid)
    okp = c.post(f"{A}/passkey/login/verify", headers=oh, json=msg)
    rep = c.post(f"{A}/passkey/login/verify", headers=oh, json=msg)
    sa.count -= 5
    t4 = c.post(f"{A}/passkey/login/verify", headers=oh, json=sa.get(opts(identifier=mail)))
    sa.count += 10
    check("passkey sign-in: bad signature / origin / missing UV / replayed state / counter regression rejected; valid -> session",
          [t1.status_code, t2.status_code, t3.status_code, rep.status_code, t4.status_code] == [400] * 5
          and okp.status_code == 200 and okp.json()["user"]["role"] == "nurse" and len(ghost["publicKey"]["allowCredentials"]) == 1
          and o1["publicKey"]["userVerification"] == "required",
          f"{t1.status_code}/{t2.status_code}/{t3.status_code}/{rep.status_code}/{t4.status_code} ok={okp.status_code}")
    disc = c.post(f"{A}/passkey/login/verify", headers=oh, json=sa.get(opts(), user_id=uid))
    tj = signin(mail).json()
    o3 = opts(ticket=tj["ticket"])
    as2 = c.post(f"{A}/passkey/login/verify", headers=oh, json=sa.get(o3))
    check("passkey as discoverable first factor and as the second factor of a code sign-in",
          disc.status_code == 200 and tj["methods"] == ["totp", "passkey"] and as2.status_code == 200
          and c.post(f"{A}/second", json={"ticket": tj["ticket"], "method": "totp", "code": totp.at(time.time() + 90)}).status_code == 400,
          f"{disc.status_code}/{as2.status_code}")
    sec = c.get(f"{A}/security", headers=nurse2).json()
    off = c.post(f"{A}/totp/disable", headers=nurse2, json={"code": ej["recovery_codes"][1]})  # TOTP steps in the window are spent
    last = c.delete(f"{A}/passkey/{sec['methods']['passkeys'][0]['id']}", headers=nurse2)
    c.post(f"{A}/security", headers=nurse2, json={"two_step": False})
    gone = c.delete(f"{A}/passkey/{sec['methods']['passkeys'][0]['id']}", headers=nurse2)
    check("security settings: masked methods, sessions, TOTP off needs a code, the last second factor cannot be removed while two-step is on",
          sec["methods"]["email"] == "n***@check.uz" and sec["methods"]["totp"] is True and any(x["current"] for x in sec["sessions"])
          and off.status_code == 200 and last.status_code == 422 and gone.status_code == 200 and gone.json()["passkeys"] == [],
          f"{off.status_code}/{last.status_code}/{gone.status_code}")
    rate_limiter.reset()

    # ---- brute force, rate limits, phone, bootstrap admin, block, logout, Google
    st = start("login", mail).json()
    tries = [c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": f"99999{i}" if st["dev_code"] != f"99999{i}" else "888888"}).status_code
             for i in range(5)]
    late = c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": st["dev_code"]})
    check("one-time code: 5 wrong attempts burn the challenge", tries == [400] * 5 and late.status_code == 400)
    rate_limiter.reset()
    seen = [start("login", mail).status_code for _ in range(s.OTP_START_PER_IDENTIFIER + 1)]
    check("/account/start is rate-limited per identifier (429)", seen[0] == 200 and seen[-1] == 429)
    rate_limiter.reset()
    with SessionLocal() as db:
        u = db.get(User, uid)
        u.failed_logins = s.ACCOUNT_LOCK_AFTER - 1
        db.commit()
    st = start("login", mail).json()
    c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": "000000" if st["dev_code"] != "000000" else "111111"})
    locked = c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": st["dev_code"]})
    st2 = start("login", mail)
    locked2 = c.post(f"{A}/verify", json={"challenge_id": st2.json()["challenge_id"], "code": st2.json()["dev_code"]})
    with SessionLocal() as db:
        u = db.get(User, uid)
        was_locked = u.locked_until is not None
        u.locked_until, u.failed_logins = None, 0
        db.commit()
    check("lockout after repeated failures: even the right code is refused while locked (same generic answer)",
          was_locked and locked.status_code == 400 and st2.status_code == 200 and locked2.status_code == 400)
    ph = start("register", "90 123-45-67", "phone", "Telefon Sinov")
    bad_ph = start("register", "+7 900 123 45 67", "phone")
    check("phone numbers are normalised to +998XXXXXXXXX (foreign numbers: 422)", ph.status_code == 200
          and ph.json()["sent_to"] == "+99890***67" and bad_ph.status_code == 422, f"sent_to={ph.json().get('sent_to')}")
    b = signin(s.ADMIN_BOOTSTRAP_IDENTIFIER, "register")
    bj = b.json()
    other = signin(f"second.{tag}@check.uz", "register").json()
    check("bootstrap: the account registering with ADMIN_BOOTSTRAP_IDENTIFIER becomes admin; everyone else is pending",
          b.status_code == 200 and bj["user"]["role"] == "admin" and bj["user"]["persona"] == "leader"
          and c.get(f"{ADM}/overview", headers=H(bj["access_token"])).status_code == 200 and other["user"]["role"] == "pending")
    boss = H(bj["access_token"])
    self_block = c.post(f"{ADM}/users/{bj['user']['id']}", headers=boss, json={"status": "blocked"})
    blk = c.post(f"{ADM}/users/{uid}", headers=boss, json={"status": "blocked"})
    after = c.get(f"{B}/map", headers=nurse2)
    st = start("login", mail)
    blocked_login = c.post(f"{A}/verify", json={"challenge_id": st.json()["challenge_id"], "code": st.json()["dev_code"]})
    check("blocking: admin cannot block herself (422); a blocked account loses its sessions (401) and cannot sign in",
          self_block.status_code == 422 and blk.status_code == 200 and blk.json()["status"] == "blocked"
          and after.status_code == 401 and st.status_code == 200 and blocked_login.status_code == 400)
    tok = H(other["access_token"])
    out = c.post(f"{A}/logout", headers=tok)
    check("logout revokes the session (204, then 401)", out.status_code == 204 and c.get(f"{A}/me", headers=tok).status_code == 401)
    gc = c.get(f"{A}/google/config").json()
    gv = c.post(f"{A}/google/verify", json={"credential": "x" * 40})
    s.GOOGLE_CLIENT_ID = "check-client.apps.googleusercontent.com"
    from app.api.v1 import account
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    import jwt as _jwt
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    account._jwks.update(at=time.time(), keys={"k1": key.public_key()})  # no network: the JWKS cache is pre-filled
    base = {"iss": "https://accounts.google.com", "aud": s.GOOGLE_CLIENT_ID, "sub": "g-" + tag, "email": f"G.{tag}@check.uz",
            "email_verified": True, "name": "Google Sinov", "iat": int(time.time()), "exp": int(time.time()) + 600}

    def gtok(k=key, **over):
        return _jwt.encode(base | over, k, algorithm="RS256", headers={"kid": "k1"})
    g_ok = c.post(f"{A}/google/verify", json={"credential": gtok()})
    g_codes = [c.post(f"{A}/google/verify", json={"credential": t}).status_code for t in (
        gtok(aud="someone-else"), gtok(iss="https://evil.example"), gtok(exp=int(time.time()) - 600),
        gtok(_rsa.generate_private_key(public_exponent=65537, key_size=2048)), gtok(email_verified=False))]
    s.GOOGLE_CLIENT_ID = ""
    check("Google: disabled without GOOGLE_CLIENT_ID; with it the ID token signature, aud, iss, exp and verified e-mail are checked",
          gc == {"enabled": False, "client_id": None} and gv.status_code == 404 and g_ok.status_code == 200
          and g_ok.json()["user"]["role"] == "pending" and g_ok.json()["user"]["email"] == f"g.{tag}@check.uz" and g_codes == [401] * 5,
          f"ok={g_ok.status_code} bad={g_codes}")
    s.ADMIN_BOOTSTRAP_IDENTIFIER = ""
    rate_limiter.reset()
    public.clear_cache()
    _ = PasskeyCredential


def facility_checks(c, nurse, spec, leader) -> None:
    """One administrator per facility; only that person adds nurses and doctors, only for their own facility."""
    from app.models import Facility, User
    tag = os.urandom(3).hex()
    rate_limiter.reset()

    def H(tok):
        return {"Authorization": f"Bearer {tok}"}

    def signin(ident):
        rate_limiter.reset()
        st = c.post(f"{A}/start", json={"mode": "login", "channel": "email", "identifier": ident}).json()
        return c.post(f"{A}/verify", json={"challenge_id": st["challenge_id"], "code": st["dev_code"]})

    admin = H(c.post(f"{B}/login", json={"persona": "admin"}).json()["access_token"])
    dj = c.post(f"{B}/login", json={"persona": "facility_admin"})
    demo_fa = H(dj.json().get("access_token", ""))
    me = c.get(f"{FAC}/me", headers=demo_fa)
    mj = me.json() if me.status_code == 200 else {}
    codes_seen = [c.get(f"{B}/map", headers=demo_fa).status_code, c.get(f"{B}/nurse/today", headers=demo_fa).status_code,
                  c.get(f"{B}/inbox", headers=demo_fa).status_code, c.get(f"{B}/search", headers=demo_fa, params={"q": "a"}).status_code,
                  c.post(f"{B}/stream-ticket", headers=demo_fa).status_code, c.get(f"{ADM}/users", headers=demo_fa).status_code,
                  c.get("/api/v1/me", headers=demo_fa).status_code]
    check('demo persona "facility_admin": own facility + staff counts only; map, patient lists, search, admin -> 403',
          dj.status_code == 200 and dj.json()["user"]["role"] == "facility_admin" and dj.json()["user"]["persona"] == "facility_admin"
          and me.status_code == 200 and mj["facility"]["district_code"] == geo.demo_district and mj["counts"]["nurses"] >= 1
          and codes_seen == [403] * 7 and c.get(f"{A}/me", headers=demo_fa).status_code == 200,
          f"facility={mj.get('facility', {}).get('name')} counts={mj.get('counts')} codes={codes_seen}")
    with SessionLocal() as db:
        names = [decrypt(m.full_name_enc) for m in db.execute(select(Mother).limit(300)).scalars()]
    staff_json = c.get(f"{FAC}/staff", headers=demo_fa).json()["items"]
    blob = me.text + json.dumps(staff_json, ensure_ascii=False)
    names = [n for n in names if n not in {i["name"] for i in staff_json}]  # a synthetic patient may share a staff name
    check("facility admin answers carry no patient data (no names, no record ids)",
          not any(n and n in blob for n in names) and "pregnancy_id" not in blob and "mother" not in blob,
          f"hit={[n for n in names if n and n in blob][:2]} keys={[k for k in ('pregnancy_id', 'mother') if k in blob]}")

    # ---- system admin: list, appoint by e-mail (pre-approved account), one-time-code sign-in
    fl = c.get(f"{ADM}/facilities", headers=admin, params={"type": "polyclinic"})
    items = fl.json().get("items", [])
    keys = {"id", "name", "type", "region", "district", "facility_admin", "staff_count"}
    free = [i for i in items if i["facility_admin"] is None and i["district_code"] != geo.demo_district]
    fa, fb = free[0], free[1]
    types = set(c.get(f"{ADM}/facilities", headers=admin).json()["types"])
    check("/admin/facilities: searchable list with the administrator and staff count; several facility types",
          fl.status_code == 200 and keys <= set(items[0]) and all(i["type"] == "polyclinic" for i in items)
          and any(i["facility_admin"] and i["facility_admin"]["demo"] for i in items) and len(types) >= 3
          and len(c.get(f"{ADM}/facilities", headers=admin, params={"q": fa["name"][:8]}).json()["items"]) >= 1,
          f"polyclinics={len(items)} types={sorted(types)}")
    m1, m2, m3 = f"fa1.{tag}@check.uz", f"fa2.{tag}@check.uz", f"fb.{tag}@check.uz"
    bad = c.post(f"{ADM}/facilities/{fa['id']}/admin", headers=admin, json={})
    a1 = c.post(f"{ADM}/facilities/{fa['id']}/admin", headers=admin, json={"identifier": m1, "full_name": "Birinchi Admin"})
    s1 = signin(m1)
    t1 = H(s1.json().get("access_token", ""))
    check("system admin appoints a facility admin by e-mail -> pre-approved account signs in with a one-time code",
          bad.status_code == 422 and a1.status_code == 200 and a1.json()["created"] is True
          and a1.json()["facility_admin"]["name"] == "Birinchi Admin" and s1.status_code == 200
          and s1.json()["user"]["role"] == "facility_admin" and c.get(f"{FAC}/me", headers=t1).json()["facility"]["id"] == fa["id"],
          f"{a1.status_code}/{s1.status_code}")

    # ---- facility admin adds a nurse and a doctor
    nm, dm = f"nurse.{tag}@check.uz", f"doc.{tag}@check.uz"
    wrong = c.post(f"{FAC}/staff", headers=t1, json={"full_name": "Yangi Hamshira", "identifier": nm, "role": "nurse",
                                                    "district_code": next(d for d, x in geo.districts.items()
                                                                          if x["region_soato"] != fa["region_soato"])})
    n1 = c.post(f"{FAC}/staff", headers=t1, json={"full_name": "Yangi Hamshira", "identifier": nm, "role": "nurse"})
    d1 = c.post(f"{FAC}/staff", headers=t1, json={"full_name": "Yangi Shifokor", "identifier": dm, "role": "specialist"})
    dup = c.post(f"{FAC}/staff", headers=t1, json={"full_name": "Yangi Hamshira", "identifier": nm, "role": "nurse"})
    extra = c.post(f"{FAC}/staff", headers=t1, json={"full_name": "X Y", "identifier": f"x.{tag}@check.uz", "role": "admin"})
    nid = n1.json().get("item", {}).get("id")
    ns = signin(nm)
    nt = H(ns.json().get("access_token", ""))
    today = c.get(f"{B}/nurse/today", headers=nt)
    ds = signin(dm)
    check("facility admin adds a nurse -> she signs in with a one-time code and reaches /live/nurse/today",
          n1.status_code == 200 and n1.json()["created"] is True and n1.json()["item"]["district_code"] == fa["district_code"]
          and n1.json()["item"]["contact"] == "n***@check.uz" and ns.status_code == 200 and ns.json()["user"]["role"] == "nurse"
          and today.status_code == 200, f"add={n1.status_code} login={ns.status_code} today={today.status_code}")
    check("facility admin adds a doctor (specialist of the facility's region); duplicates 409, other roles / foreign district 422",
          d1.status_code == 200 and ds.status_code == 200 and ds.json()["user"]["role"] == "specialist"
          and ds.json()["user"]["region_soato"] == fa["region_soato"] and c.get(f"{B}/inbox", headers=H(ds.json()["access_token"])).status_code == 200
          and dup.status_code == 409 and extra.status_code == 422 and wrong.status_code == 422,
          f"{d1.status_code}/{dup.status_code}/{extra.status_code}/{wrong.status_code}")
    sl = c.get(f"{FAC}/staff", headers=t1).json()["items"]
    check("/facility/staff lists exactly this facility's staff, contacts masked",
          {i["id"] for i in sl} == {nid, d1.json()["item"]["id"]} and nm not in json.dumps(sl) and dm not in json.dumps(sl))

    # ---- another facility's admin: 404 on that staff member
    a3 = c.post(f"{ADM}/facilities/{fb['id']}/admin", headers=admin, json={"identifier": m3, "full_name": "Boshqa Admin"})
    t3 = H(signin(m3).json().get("access_token", ""))
    before = _denied("outside_facility")
    codes_seen = [c.post(f"{FAC}/staff/{nid}", headers=t3, json={"status": "blocked"}).status_code,
                  c.delete(f"{FAC}/staff/{nid}", headers=t3).status_code,
                  c.post(f"{FAC}/staff/{nid}", headers=demo_fa, json={"role": "specialist"}).status_code]
    takeover = c.post(f"{FAC}/staff", headers=t3, json={"full_name": "Yangi Hamshira", "identifier": nm, "role": "nurse"})
    claim = c.post(f"{FAC}/staff/claim", headers=t3, json={"identifier": nm})
    check("another facility's admin gets 404 on that staff member, cannot take her over (409 / claim 404); denials audited",
          a3.status_code == 200 and codes_seen == [404] * 3 and takeover.status_code == 409 and claim.status_code == 404
          and nid not in c.get(f"{FAC}/staff", headers=t3).text and c.get(f"{B}/nurse/today", headers=nt).status_code == 200
          and _denied("outside_facility") >= before + 3, f"codes={codes_seen} takeover={takeover.status_code} claim={claim.status_code}")

    # ---- nurse / specialist / leader / system admin: 403 on /facility/*
    rate_limiter.reset()
    codes_seen = []
    for h in (nurse, spec, leader, admin, nt):
        codes_seen += [c.get(f"{FAC}/me", headers=h).status_code, c.get(f"{FAC}/staff", headers=h).status_code,
                       c.post(f"{FAC}/staff", headers=h, json={"full_name": "A B", "identifier": f"z.{tag}@check.uz", "role": "nurse"}).status_code,
                       c.delete(f"{FAC}/staff/{nid}", headers=h).status_code]
    check("nurse / specialist / leader / system admin get 403 on /facility/*; no token -> 401",
          codes_seen == [403] * 20 and c.get(f"{FAC}/me").status_code == 401
          and [c.get(f"{ADM}/facilities", headers=h).status_code for h in (t1, nurse, leader)] == [403] * 3, f"codes={set(codes_seen)}")

    # ---- block / unblock / role change / remove
    blk = c.post(f"{FAC}/staff/{nid}", headers=t1, json={"status": "blocked"})
    gone = c.get(f"{B}/nurse/today", headers=nt).status_code
    no_login = signin(nm).status_code
    unb = c.post(f"{FAC}/staff/{nid}", headers=t1, json={"status": "active"})
    back = signin(nm)
    nt = H(back.json().get("access_token", ""))
    with SessionLocal() as db:
        clinical = (db.execute(select(Pregnancy.id)).all().__len__(), db.execute(select(Alert.id)).all().__len__())
    rem = c.delete(f"{FAC}/staff/{nid}", headers=t1)
    with SessionLocal() as db:
        clinical2 = (db.execute(select(Pregnancy.id)).all().__len__(), db.execute(select(Alert.id)).all().__len__())
        row = db.get(User, nid)
        detached = row is not None and row.live_role == "pending" and not list(row.scopes)
    check("block revokes the nurse's sessions and sign-in; unblock restores; remove detaches the account, clinical data untouched",
          blk.status_code == 200 and blk.json()["status"] == "blocked" and gone == 401 and no_login == 400 and unb.status_code == 200
          and back.status_code == 200 and rem.status_code == 200 and detached and clinical == clinical2
          and c.get(f"{B}/nurse/today", headers=nt).status_code == 401 and signin(nm).json()["user"]["role"] == "pending",
          f"{blk.status_code}/{gone}/{no_login}/{unb.status_code}/{rem.status_code}")

    # ---- exactly one administrator per facility: the second replaces the first
    a2 = c.post(f"{ADM}/facilities/{fa['id']}/admin", headers=admin, json={"identifier": m2, "full_name": "Ikkinchi Admin"})
    old_codes = [c.get(f"{FAC}/me", headers=t1).status_code, signin(m1).json().get("user", {}).get("role")]
    t2 = H(signin(m2).json().get("access_token", ""))
    with SessionLocal() as db:
        f = db.get(Facility, fa["id"])
        n_admins = len([u for u in db.execute(select(User).where(User.live_role == "facility_admin")).scalars()
                        if any(sc.facility_id == fa["id"] for sc in u.scopes)])
        one = f.admin_user_id == a2.json()["facility_admin"]["id"] and n_admins == 1
        try:  # database-level guarantee: the same person cannot administer two facilities, a facility has one slot
            other = db.get(Facility, fb["id"])
            other.admin_user_id = f.admin_user_id
            db.commit()
            unique = False
        except Exception:  # noqa: BLE001
            db.rollback()
            unique = True
    elsewhere = c.post(f"{ADM}/facilities/{fb['id']}/admin", headers=admin, json={"user_id": a2.json()["facility_admin"]["id"]})
    check("a second facility admin for the same facility replaces the first (demoted to pending, sessions revoked, audited)",
          a2.status_code == 200 and old_codes == [401, "pending"] and one and unique and elsewhere.status_code == 409
          and c.get(f"{FAC}/me", headers=t2).json()["facility"]["id"] == fa["id"]
          and len(c.get(f"{FAC}/staff", headers=t2).json()["items"]) == 1, f"old={old_codes} one={one} unique_index={unique}")
    uid2 = a2.json()["facility_admin"]["id"]
    via_users = c.post(f"{ADM}/users/{uid2}", headers=admin, json={"role": "leader"})
    with SessionLocal() as db:
        freed = db.get(Facility, fa["id"]).admin_user_id is None
    again = c.post(f"{ADM}/users/{uid2}", headers=admin, json={"role": "facility_admin", "facility_id": fa["id"]})
    no_fac = c.post(f"{ADM}/users/{uid2}", headers=admin, json={"role": "facility_admin"})
    rm = c.delete(f"{ADM}/facilities/{fa['id']}/admin", headers=admin)
    demo_rm = c.delete(f"{ADM}/facilities/{mj['facility']['id']}/admin", headers=admin)
    check("/admin/users keeps the rule: another role frees the facility; facility_admin needs a facility; DELETE removes; demo is fixed",
          via_users.status_code == 200 and freed and again.status_code == 200 and again.json()["role"] == "facility_admin"
          and no_fac.status_code == 422 and rm.status_code == 200 and rm.json()["facility_admin"] is None
          and c.get(f"{FAC}/me", headers=t2).status_code == 401 and demo_rm.status_code == 409,
          f"{via_users.status_code}/{again.status_code}/{no_fac.status_code}/{rm.status_code}/{demo_rm.status_code}")
    with SessionLocal() as db:
        acts = set(db.execute(select(AuditLog.action).where(AuditLog.action.like("%facility%"))).scalars())
    want = {"admin.facility.admin.appoint", "admin.facility.admin.demote", "facility.staff.add", "facility.staff.claim",
            "facility.staff.update", "facility.staff.remove", "facility.access"}
    check("every facility action is in the audit log", want <= acts, f"missing={sorted(want - acts)}")
    rate_limiter.reset()
    seen = [c.post(f"{FAC}/staff/claim", headers=demo_fa, json={"identifier": f"p{i}.{tag}@check.uz"}).status_code for i in range(42)]
    check("adding / claiming staff is rate-limited (429)", seen[0] == 404 and seen[-1] == 429)
    rate_limiter.reset()


def main() -> int:
    s = get_settings()
    with TestClient(app) as c:
        def login(p):
            r = c.post(f"{B}/login", json={"persona": p})
            return {"Authorization": f"Bearer {r.json()['access_token']}"} if r.status_code == 200 else {}
        nurse, spec, leader = login("nurse"), login("specialist"), login("leader")
        check("demo login works for 3 personas (dev + LIVE_DEMO_LOGIN=1)", all((nurse, spec, leader)))

        # ---------------- stage scenario
        c.post(f"{B}/demo/scenario", headers=leader, json={"name": "reset"})
        today = c.get(f"{B}/nurse/today", headers=nurse).json()
        madina = next((t for t in today["tasks"] if t["name"] == "Madina Tursunova"), None)
        check("stage: nurse sees Madina Tursunova (yellow early warning)", bool(madina) and madina["zone"] == "yellow",
              f"zone={madina and madina['zone']}")
        pid = madina["pregnancy_id"] if madina else ""
        parsed = c.post(f"{B}/voice/parse", headers=nurse, json={"text": "bosim 150 ga 95, boshi ogʻriyapti"}).json()
        f = parsed.get("fields", {})
        v = c.post(f"{B}/visits", headers=nurse, json={"pregnancy_id": pid, "systolic_bp": f.get("systolic_bp"),
                                                       "diastolic_bp": f.get("diastolic_bp"),
                                                       "severe_headache": f.get("severe_headache"), "source": "voice"})
        vj = v.json()
        check("stage: dictated visit 150/95 + headache -> RED with alert", v.status_code == 200 and vj.get("zone") == "red"
              and bool(vj.get("alert")), f"status={v.status_code} zone={vj.get('zone')}")
        aid = (vj.get("alert") or {}).get("id")
        inbox = c.get(f"{B}/inbox", headers=spec).json()
        item = next((i for i in inbox["items"] if i["alert_id"] == aid), None)
        check("stage: specialist inbox shows the alert with the patient name", bool(item) and item["patient"]["name"] == "Madina Tursunova")
        a = c.post(f"{B}/alerts/{aid}/ack", headers=spec)
        d = c.post(f"{B}/alerts/{aid}/decide", headers=spec, json={"decision": "refer", "note": None})
        check("stage: ack + refer resolves the alert", a.status_code == 200 and d.status_code == 200 and d.json().get("status") == "resolved")
        m = c.get(f"{B}/map", headers=leader)
        check("stage: leader map answers", m.status_code == 200 and len(m.json()["districts"]) == 199)

        # ---------------- leader never sees identities
        r = c.get(f"{B}/patients/{pid}", headers=leader)
        check("leader cannot read a patient card (403)", r.status_code == 403 and "Madina" not in r.text, f"status={r.status_code}")
        with SessionLocal() as db:
            nb_id = db.execute(select(Newborn.id).limit(1)).scalar()
            names = {decrypt(x) for x in db.execute(select(Mother.full_name_enc)).scalars()} - {None}
            codes = set(db.execute(select(Mother.local_code)).scalars()) | set(db.execute(select(Newborn.local_code)).scalars())
        r = c.get(f"{B}/newborns/{nb_id}", headers=leader)
        check("leader cannot read a newborn card (403)", r.status_code == 403)
        r = c.get(f"{B}/patients/{pid}", headers=spec)
        pj = r.json()
        check("specialist reads the patient card (name decrypted for the care team)", r.status_code == 200
              and pj["patient"]["name"] == "Madina Tursunova")
        ml = pj.get("ml_shadow", {})
        check("ml_shadow label is shadow-only ('sinov rejimi') from maternal_shadow_v2", "inov rejimi" in ml.get("label", "")
              and "taʼsir qilmaydi" in ml.get("label", ""), f"version={ml.get('version')} p={ml.get('probability')}")
        li, ln = c.get(f"{B}/inbox", headers=leader), c.get(f"{B}/nurse/today", headers=leader)
        check("leader cannot open the specialist inbox or the nurse day list (403)", li.status_code == 403 and ln.status_code == 403)
        blob = json.dumps([c.get(f"{B}/search", headers=leader, params={"q": "Tursunova"}).json(),
                           c.get(f"{B}/search", headers=leader, params={"q": "Madina"}).json(), li.json(), ln.json(),
                           c.get(f"{B}/districts/{today['nurse']['district_code']}", headers=leader).json(), m.json()],
                          ensure_ascii=False)
        leaked = [n for n in names if n in blob]
        check("leader responses (/search, /inbox, /nurse/today, /districts, /map) contain no patient name or mahalla",
              not leaked and '"mahalla": "' not in blob, f"leaked={len(leaked)}")
        sr = c.get(f"{B}/search", headers=spec, params={"q": "Tursunova"}).json()["results"]
        check("care team can still search by name", any(x["kind"] == "patient" for x in sr))
        with SessionLocal() as db:
            ok_read = db.execute(select(AuditLog).where(AuditLog.action == "live.patient.read", AuditLog.resource_id == pid,
                                                        AuditLog.outcome == "success")).first()
            denied = db.execute(select(AuditLog).where(AuditLog.action == "live.patient.read", AuditLog.resource_id == pid,
                                                       AuditLog.outcome == "denied")).first()
        check("every patient read is audited (success + denied attempt)", bool(ok_read) and bool(denied))

        # ---------------- AI brief + gateway
        for scope, ident in (("national", None), ("region", "1726"), ("district", today["nurse"]["district_code"])):
            r = c.post(f"{B}/ai/brief", headers=leader, json={"scope": scope, "id": ident})
            j = r.json()
            check(f"/ai/brief {scope} works without a key (local)", r.status_code == 200 and j.get("source") == "local"
                  and j.get("blocked") is False and len(j.get("text", "")) > 60 and j.get("sent_fields") == [], j.get("text", "")[:90])
        bad_cells, bad_names, blocked, n = 0, 0, 0, 0
        gw = pg.BriefGateway()
        known = live._known_areas()
        small_int = re.compile(r'"(?:%s)": [0-4]\b' % "|".join(pg._COUNT_KEYS))
        with SessionLocal() as db:
            scopes = [("national", None)] + [("region", r) for r in geo.regions] + [("district", d) for d in geo.districts]
            for scope, ident in scopes:
                payload = pg.build_brief_payload(live._brief_raw(db, scope, ident))
                reasons, wire, text = gw.check(payload, known)
                n += 1
                bad_cells += bool(pg.small_cell_violations(wire)) or bool(small_int.search(text))
                bad_names += any(x in text for x in names) or any(x and x in text for x in codes)
                blocked += bool(reasons)
        check(f"payload builder: no small cell in {n} payloads (national + regions + all districts)", bad_cells == 0, f"violations={bad_cells}")
        check(f"payload builder: no patient name / record code in {n} payloads", bad_names == 0)
        check("payload builder: all real payloads pass the detectors", blocked == 0, f"blocked={blocked}")
        tiny = pg.build_brief_payload({"scope": "district", "area_name": "Yunusobod", "zones": {"pregnant": 3, "red": 1},
                                       "alerts": {"open": 4}, "answered": 2, "median_response_min": 7,
                                       "rule_counts": {"M_SEVERE_HYPERTENSION": 4},
                                       "official": [{"code": "maternal_mortality_ratio", "year": 2025, "value": 30.1, "events": 1}]})
        w = tiny.model_dump(mode="json", exclude_none=True)
        check("k-anonymity: cells < 5 become \"<5\", small-count rates and medians are dropped",
              w["zones"]["pregnant"] == "<5" and w["alerts"]["open"] == "<5" and "median_response_min" not in w["response"]
              and not w["official"] and not w["top_rule_categories"])
        try:
            pg.AggregateBriefPayload.model_validate(w | {"patient_name": "X"})
            extra_ok = False
        except Exception:  # noqa: BLE001
            extra_ok = True
        try:
            pg.AggregateBriefPayload.model_validate(w | {"zones": w["zones"] | {"red": 2}})
            cell_ok = False
        except Exception:  # noqa: BLE001
            cell_ok = True
        check("closed schema: extra field and raw small count are rejected", extra_ok and cell_ok)
        check("numeric guard catches a forged small cell", bool(pg.small_cell_violations({"zones": {"red": 3}})))
        r1, _, _ = gw.check(tiny.model_copy(update={"area_name": "Madina Tursunova"}), known)
        check("gateway blocks a non-geographic area name", "unknown_area_name" in r1)

        sent: list[dict] = []

        class FakeMessages:
            def create(self, **kw):
                sent.append(kw)
                return SimpleNamespace(stop_reason="end_turn", model=kw["model"], usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                       content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="Qisqa tahlil.")])
        fake = ClaudeProvider(client=SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
        events: list = []
        with SessionLocal() as db:
            raw = live._brief_raw(db, "national", None)
        out = pg.BriefGateway(provider=fake, event_sink=events.append).brief(None, raw, known)
        body = sent[0]["messages"][0]["content"] if sent else ""
        check("with a (fake) provider: source=claude-haiku, model=claude-haiku-5-5, only the k-anonymised JSON is sent",
              out["source"] == "claude-haiku" and sent and sent[0]["model"] == "claude-haiku-5-5" and json.loads(body).keys()
              <= set(pg.AggregateBriefPayload.model_fields) and not small_int.search(body) and not any(x in body for x in names)
              and "fallbacks" not in sent[0], f"fields={len(out['sent_fields'])}")
        check("gateway decision is logged (metadata only)", len(events) == 1 and events[0].decision == "allowed"
              and events[0].feature == pg.BRIEF_FEATURE)

        class Boom:
            name, model = "anthropic", "claude-haiku-5-5"

            def complete(self, *a, **k):
                raise RuntimeError("network down")
        out = pg.BriefGateway(provider=Boom(), event_sink=events.append).brief(None, raw, known)
        check("provider failure -> deterministic local brief", out["source"] == "local" and len(out["text"]) > 60 and not out["blocked"])
        s.CLAUDE_ALLOW_KANON_AGGREGATES = False
        out = pg.BriefGateway(provider=fake, event_sink=events.append).brief(None, raw, known)
        s.CLAUDE_ALLOW_KANON_AGGREGATES = True
        check("CLAUDE_ALLOW_KANON_AGGREGATES=false blocks the outbound call", out["blocked"] and out["source"] == "local" and len(sent) == 1)
        try:
            type(s)(CLAUDE_EXTERNAL_PATIENT_DATA=True)
            inv = False
        except Exception:  # noqa: BLE001
            inv = True
        check("CLAUDE_EXTERNAL_PATIENT_DATA=true refuses to start (invariant)", inv and s.CLAUDE_EXTERNAL_PATIENT_DATA is False)
        pr = c.get(f"{B}/ai/privacy", headers=leader).json()
        with SessionLocal() as db:
            n_ev = len(list(db.execute(select(PrivacyEvent).where(PrivacyEvent.feature == pg.BRIEF_FEATURE)).scalars()))
        check("/ai/privacy returns policy + last decisions; privacy_events rows written", pr.get("k_anonymity") == 5
              and pr.get("model") == "claude-haiku-5-5" and len(pr.get("decisions", [])) >= 3 and n_ev >= 3
              and bool(pr.get("never_sent")) and pr.get("mode") == "local", f"events={n_ev}")

        # ---------------- hardening
        t = c.post(f"{B}/stream-ticket", headers=nurse).json()
        first, second = live._redeem_ticket(t["ticket"]), live._redeem_ticket(t["ticket"])
        check("stream ticket: valid 60 s, single use", t.get("expires_in") == 60 and first is not None and second is None)
        tok = nurse["Authorization"].split()[1]
        check("?token= is not accepted outside /stream", c.get(f"{B}/map", params={"token": tok}).status_code == 401)
        check("invalid stream ticket -> 401", c.get(f"{B}/stream", params={"ticket": "x" * 20}).status_code == 401)
        r = c.post(f"{B}/visits", headers=nurse, json={"pregnancy_id": pid, "systolic_bp": 900, "note": "A" * 10})
        check("/visits rejects out-of-range values with a safe envelope", r.status_code == 422 and "Traceback" not in r.text
              and "900" not in r.text and r.json()["error"]["code"] == "validation_error")
        r = c.post(f"{B}/visits", headers=nurse, content=b'{"pregnancy_id":"' + b"a" * 70000 + b'"}', )
        check("request body limit (64 KB) -> 413", r.status_code == 413)
        r = c.post(f"{B}/voice/parse", headers=nurse, content=b'{"text":' + b"[" * 12 + b"]" * 12 + b"}")
        check("JSON depth limit -> 400", r.status_code == 400 and r.json()["error"]["code"] == "json_too_deep")
        rate_limiter.reset()
        codes_seen = [c.post(f"{B}/voice/parse", headers=nurse, json={"text": "puls 80"}).status_code
                      for _ in range(s.LIVE_WRITE_RATE_PER_MINUTE + 3)]
        check("write endpoints are rate-limited per user (429)", codes_seen[0] == 200 and codes_seen[-1] == 429
              and c.post(f"{B}/voice/parse", headers=spec, json={"text": "puls 80"}).status_code == 200)
        rate_limiter.reset()
        h = c.get(f"{B}/map", headers=leader, ).headers
        check("security headers (COOP, CORP, nosniff, frame, CSP, no-store)", h.get("cross-origin-opener-policy") == "same-origin"
              and h.get("cross-origin-resource-policy") == "same-site" and h.get("x-content-type-options") == "nosniff"
              and h.get("x-frame-options") == "DENY" and "default-src 'none'" in h.get("content-security-policy", "")
              and h.get("cache-control") == "no-store")
        r = c.options(f"{B}/map", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
        r2 = c.options(f"{B}/map", headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "GET"})
        check("CORS: only configured origins", "access-control-allow-origin" not in r.headers
              and r2.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173")
        s.LIVE_DEMO_LOGIN = False
        r = c.post(f"{B}/login", json={"persona": "leader"})
        s.LIVE_DEMO_LOGIN = True
        check("demo login refused when LIVE_DEMO_LOGIN=0", r.status_code == 403)
        r = c.post(f"{B}/login", json={"persona": "leader", "role": "admin"})
        check("login rejects unexpected fields", r.status_code == 422)
        rate_limiter.reset()
        codes_seen = [c.post(f"{B}/login", json={"persona": "leader"}).status_code for _ in range(s.LIVE_LOGIN_RATE_PER_MINUTE + 2)]
        check("demo login is rate-limited", codes_seen[-1] == 429)
        rate_limiter.reset()

        # ---------------- official statistics + models
        o = c.get(f"{B}/official/districts", headers=leader).json()
        st = {}
        for dd in o["districts"]:
            st[dd["status"]] = st.get(dd["status"], 0) + 1
        check("official district statistics: mapping + cautious status", o["matched"] + o["unmatched"] == 199 and o["matched"] >= 190
              and len(o["districts"]) == 199, f"matched={o['matched']} unmatched={o['unmatched']} year={o['year']} status={st}")
        mm = c.get(f"{B}/official/districts", headers=leader, params={"indicator": "maternal_mortality_ratio"}).json()
        coloured = [x for x in mm["districts"] if x["status"] in ("red", "yellow") and x["detail"].get("expected", 0) < 5]
        check("rare events never colour a district (maternal deaths)", not coloured,
              f"grey={sum(x['status'] == 'grey' for x in mm['districts'])}")
        check("unknown indicator -> 422", c.get(f"{B}/official/districts", headers=leader, params={"indicator": "nope_x"}).status_code == 422)
        dj = c.get(f"{B}/districts/{today['nurse']['district_code']}", headers=leader).json()
        check("/districts/{code} has the official block", bool(dj.get("official", {}).get("indicators")),
              f"siat={dj.get('official', {}).get('siat_name')}")
        mj = c.get(f"{B}/models", headers=leader).json()["items"]
        check("/models lists shadow model cards incl. fetal_ctg_shadow_v1", {"maternal_shadow_v2", "fetal_ctg_shadow_v1"}
              <= {x["name"] for x in mj} and all(x["status"] == "shadow" for x in mj)
              and any("leave_one_source_out" in x["metrics"] for x in mj))
        role_scope_checks(c, nurse, spec, leader, pid)
        escalation_and_backlog_checks(c, nurse, spec, leader, pid, today["nurse"]["district_code"])
        complementary_suppression_checks()
        account_checks(c, leader)
        facility_checks(c, nurse, spec, leader)
        c.post(f"{B}/demo/scenario", headers=leader, json={"name": "reset"})
        au = c.get(f"{B}/audit", headers=leader, params={"limit": 5}).json()
        check("audit hash chain verifies (chain_ok)", au.get("chain_ok") is True)
        check("matched / unmatched districts", True, f"{len(stats.mapping)} / {len(stats.unmatched_ours)} "
              f"(SIAT districts not on the map: {len(stats.unmatched_siat)})")
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    shutil.rmtree(_tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

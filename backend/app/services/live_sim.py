from __future__ import annotations

import asyncio
import logging
import random
import time

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.security import utcnow
from app.db import SessionLocal
from app.models import (Alert, AlertAction, Facility, MaternalObservation, Mother, Newborn, NewbornObservation,
                        Pregnancy, Referral, User)
from app.services import live_engine
from app.services.live_engine import geo, write_tx

log = logging.getLogger("perinatal.live.sim")
rnd = random.Random()

OPEN_MIN, OPEN_MAX = 8, 20          # nationwide waiting (unacknowledged) alerts
OVERDUE_MIN, OVERDUE_MAX = 2, 6     # of them past the deadline
NEWBORN_WAITING_MAX = 4
STALE_MINUTES = 90                  # older than this: closed quietly (no "answered in 600 min" line, no response-time skew)


class _State:
    preg_ids: list[str] = []
    newborn_ids: list[str] = []
    demo_facilities: set[str] = set()
    pending: dict[str, dict] = {}   # alert_id -> {"ack_at": t, "resolve_at": t}
    adopted = False
    warned = False
    open_now = 12        # last counts seen by _rebalance (drive how many new alerts are answered late)
    overdue_now = 3
    newborn_now = 0
    planned_overdue = 0  # waiting alerts whose simulated answer is scheduled after their deadline


st = _State()


def _load_population() -> bool:
    with SessionLocal() as db:
        geo.load(db, force=True)
        st.demo_facilities = {f for f, c in geo.fac_district.items() if geo.demo_district and c == geo.demo_district}
        st.preg_ids = [pid for pid, fid in db.execute(select(Pregnancy.id, Pregnancy.facility_id).where(Pregnancy.status == "active"))
                       if fid not in st.demo_facilities]
        st.newborn_ids = [nid for nid, fid in db.execute(select(Newborn.id, Newborn.facility_id)) if fid not in st.demo_facilities]
    return bool(st.preg_ids)


def _late_share() -> float:
    """Feedback: the emptier the national picture, the more new alerts are answered late (and the other way round)."""
    n = st.open_now
    return 0.5 if n < OPEN_MIN + 2 else 0.25 if n < 14 else 0.10 if n < OPEN_MAX - 2 else 0.0


def _schedule(alert_id: str, severity: str, subject_type: str = "pregnancy", *, late_share: float | None = None) -> None:
    now = time.time()
    share = _late_share() if late_share is None else late_share
    if subject_type == "newborn" and st.newborn_now >= NEWBORN_WAITING_MAX - 1:
        share = 0.0
    if rnd.random() < share:  # answered late: the district stays yellow, then red after the SLA
        if severity == "red":
            room = st.overdue_now + st.planned_overdue < OVERDUE_MAX - 1
            ack = now + (rnd.uniform(34, 70) if room else rnd.uniform(8, 26)) * 60
            st.planned_overdue += room
        else:
            ack = now + rnd.uniform(15, 60) * 60
    else:
        ack = now + rnd.uniform(20, 120)
    st.open_now += 1
    st.pending[alert_id] = {"ack_at": ack, "resolve_at": ack + rnd.uniform(90, 600)}


def _adopt_existing() -> None:
    """After a (re)start: open alerts outside the demo district get a simulated specialist too."""
    now = time.time()
    with SessionLocal() as db:
        for a in db.execute(select(Alert).where(Alert.status.in_(("open", "acknowledged")))).scalars():
            if a.facility_id in st.demo_facilities or a.id in st.pending:
                continue
            if a.status == "acknowledged":  # answered earlier: gets closed within the hour (no pile-up over long runs)
                st.pending[a.id] = {"ack_at": 0, "resolve_at": now + rnd.uniform(120, 3600), "acked": True}
                continue
            if a.escalation_level > 0:      # already past SLA: stays red on the map for a while
                ack = now + rnd.uniform(12, 80) * 60
            elif a.severity == "red":
                ack = now + rnd.uniform(40, 420)
            else:
                ack = now + rnd.uniform(120, 2400)
            st.pending[a.id] = {"ack_at": ack, "resolve_at": ack + rnd.uniform(120, 900)}
    st.adopted = True


# ------------------------------------------------------------------ one simulated visit
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _visit_payload(preg: Pregnancy, last: MaternalObservation | None, weeks: float) -> tuple[dict, bool]:
    """Mostly normal; ~6% worsening; an abnormal previous state often persists."""
    base_s = last.systolic_bp if last and last.systolic_bp and last.systolic_bp < 130 else rnd.randint(104, 120)
    base_d = last.diastolic_bp if last and last.diastolic_bp and last.diastolic_bp < 84 else rnd.randint(64, 76)
    s = int(_clamp(base_s + rnd.gauss(0, 3), 98, 128))
    d = int(_clamp(base_d + rnd.gauss(0, 2.5), 60, 82))
    p = {"systolic_bp": s, "diastolic_bp": d, "heart_rate": int(_clamp(rnd.gauss(78, 6), 62, min(96, 0.86 * s))),
         "temperature_c": round(rnd.uniform(36.3, 37.0), 1), "spo2": rnd.randint(96, 99), "proteinuria": "neg",
         "fetal_heart_rate": rnd.randint(125, 155) if weeks >= 20 else None,
         "severe_headache": False, "visual_disturbance": False, "vaginal_bleeding": False,
         "reduced_fetal_movements": False, "convulsions": False}
    if last and last.weight_kg:
        days = max(0.0, (utcnow() - last.observed_at).total_seconds() / 86400)
        p["weight_kg"] = round(last.weight_kg + min(days, 28) / 7 * rnd.uniform(0.25, 0.45), 1)
    r = rnd.random()
    was_high = bool(last and ((last.systolic_bp or 0) >= 140 or (last.diastolic_bp or 0) >= 90))
    if r < 0.06:
        kinds = ["htn", "htn", "htn", "pe", "severe", "movements", "bleeding", "fever"] if weeks >= 20 else ["bleeding", "fever"]
        k = rnd.choice(kinds)
        if k == "htn":
            p.update(systolic_bp=rnd.randint(141, 156), diastolic_bp=rnd.randint(90, 101))
        elif k == "pe":
            p.update(systolic_bp=rnd.randint(146, 158), diastolic_bp=rnd.randint(94, 104), proteinuria=rnd.choice(["2+", "3+"]),
                     severe_headache=rnd.random() < 0.6)
        elif k == "severe":
            p.update(systolic_bp=rnd.randint(161, 176), diastolic_bp=rnd.randint(106, 114))
        elif k == "movements":
            p.update(reduced_fetal_movements=True)
        elif k == "bleeding":
            p.update(vaginal_bleeding=True)
        else:
            p.update(temperature_c=round(rnd.uniform(38.1, 39.0), 1))
        return p, True
    if was_high and r < 0.45:  # still elevated at the follow-up visit
        p.update(systolic_bp=int(_clamp(last.systolic_bp + rnd.gauss(-3, 4), 140, 158)),
                 diastolic_bp=int(_clamp((last.diastolic_bp or 92) + rnd.gauss(-2, 3), 88, 104)))
    return p, False


def _sim_visit() -> None:
    if not st.preg_ids:
        return
    new_alert = None
    with write_tx() as db:
        preg = db.get(Pregnancy, rnd.choice(st.preg_ids))
        if preg is None or preg.status != "active" or preg.facility_id in st.demo_facilities:
            return
        last = db.execute(select(MaternalObservation).where(MaternalObservation.pregnancy_id == preg.id)
                          .order_by(MaternalObservation.observed_at.desc()).limit(1)).scalar_one_or_none()
        weeks = (utcnow().date() - preg.lmp_date).days / 7 if preg.lmp_date else 20
        payload, _ = _visit_payload(preg, last, weeks)
        res = live_engine.record_visit(db, preg, payload, actor=None, source="manual", confirmed=True, with_ml=False)
        if res.new_alert and res.alert is not None:
            new_alert = (res.alert.id, res.alert.severity)
    if new_alert:
        _schedule(*new_alert)


def _sim_newborn() -> None:
    if not st.newborn_ids:
        return
    new_alert = None
    with write_tx() as db:
        nb = db.get(Newborn, rnd.choice(st.newborn_ids))
        if nb is None or nb.facility_id in st.demo_facilities:
            return
        p = {"temperature_c": round(rnd.uniform(36.5, 37.3), 1), "heart_rate": rnd.randint(115, 160),
             "respiratory_rate": rnd.randint(35, 58), "spo2": rnd.randint(94, 99), "jaundice": False, "poor_feeding": False,
             "convulsions": False, "severe_chest_indrawing": False}
        r = rnd.random()
        if r < 0.05:
            p["temperature_c"] = round(rnd.uniform(35.4, 35.9), 1)
        elif r < 0.10:
            p["temperature_c"] = round(rnd.uniform(36.0, 36.4), 1)
        elif r < 0.14:
            p["jaundice"] = True
        res = live_engine.record_newborn_obs(db, nb, p, actor=None)
        if res.new_alert and res.alert is not None:
            new_alert = (res.alert.id, res.alert.severity, "newborn")
    if new_alert:
        _schedule(*new_alert)


# ------------------------------------------------------------------ simulated specialists
def _respond() -> None:
    now = time.time()
    # an entry is actionable when its NEXT step is due (an already answered alert only waits for its resolve time;
    # otherwise such entries would occupy the 4 slots of every pass and starve everything behind them)
    due = sorted(((aid, p) for aid, p in list(st.pending.items())
                  if p["resolve_at"] <= now or (not p.get("acked") and p["ack_at"] <= now)),
                 key=lambda x: x[1]["resolve_at"] if x[1].get("acked") else x[1]["ack_at"])
    for aid, plan in due[:4]:
        with write_tx() as db:
            a = db.get(Alert, aid)
            if a is None or a.status == "resolved" or a.facility_id in st.demo_facilities:
                st.pending.pop(aid, None)
                continue
            o = geo.ova.get(a.region_soato)
            user = db.get(User, o[0]) if o else None
            if user is None:
                st.pending.pop(aid, None)
                continue
            if a.status == "open" and plan["ack_at"] <= now:
                live_engine.acknowledge(db, a, user)
                plan["acked"] = True
                plan["resolve_at"] = max(plan["resolve_at"], now + 60)
            elif a.status == "acknowledged" and not plan.get("acked"):
                plan["acked"] = True
            elif a.status == "acknowledged" and plan["resolve_at"] <= now:
                code = rnd.choices(["clinically_managed", "referred", "false_positive"], weights=[60, 28, 12])[0]
                if code == "referred":
                    to_fac = db.execute(select(Facility).where(Facility.region_soato == a.region_soato,
                                                               Facility.facility_type == "ovabmu")).scalars().first()
                    if to_fac is not None:
                        db.add(Referral(subject_type=a.subject_type, mother_id=a.mother_id if a.subject_type == "pregnancy" else None,
                                        newborn_id=a.newborn_id, pregnancy_id=a.subject_id if a.subject_type == "pregnancy" else None,
                                        alert_id=a.id, from_facility_id=a.facility_id, to_facility_id=to_fac.id,
                                        priority="emergency" if a.severity == "red" else "urgent",
                                        reason_code=(a.rule_ids[0] if a.rule_ids else "clinical_review")[:60], status="accepted",
                                        created_by=user.id, accepted_by=user.id, accepted_at=utcnow(), is_synthetic=True))
                        db.add(AlertAction(alert_id=a.id, action="referred", actor_id=user.id, at=utcnow(),
                                           detail={"to_facility_id": to_fac.id}))
                live_engine.resolve(db, a, user, code)
                st.pending.pop(aid, None)


# ------------------------------------------------------------------ backlog cap (long runs)
def _is_overdue(row, now_dt) -> bool:
    return row.escalation_level > 0 or row.sla_due_at < now_dt


def _close_beyond_cap(alert_id: str) -> None:
    """Answer one simulated alert that is beyond a cap. Recent: a normal (late) acknowledgement with its live event.
    Stale (> 90 min): closed quietly as 'clinically managed' with no acknowledging user, so that neither the feed
    nor the response-time statistics show an invented 10-hour answer."""
    with write_tx() as db:
        a = db.get(Alert, alert_id)
        if a is None or a.status != "open" or a.facility_id in st.demo_facilities:
            st.pending.pop(alert_id, None)
            return
        now = utcnow()
        o = geo.ova.get(a.region_soato)
        user = db.get(User, o[0]) if o else None
        if user is not None and (now - a.created_at).total_seconds() <= STALE_MINUTES * 60:
            live_engine.acknowledge(db, a, user)
            st.pending[alert_id] = {"ack_at": 0, "resolve_at": time.time() + rnd.uniform(90, 600), "acked": True}
            return
        a.status, a.resolved_at, a.resolution_code = "resolved", now, "clinically_managed"
        a.acknowledged_at = a.acknowledged_at or now
        detail = {"resolution_code": "clinically_managed", "auto": "sim_backlog_cap"}
        db.add(AlertAction(alert_id=a.id, action="resolved", at=now, detail=detail))
        write_audit(db, action="alert.resolved", resource_type="alert", resource_id=a.id, detail=detail, commit=False)
        st.pending.pop(alert_id, None)


def _rebalance(max_closures: int = 40) -> dict:
    """Keep the national picture believable however long the app runs. Never touches the demo district."""
    now, now_dt = time.time(), utcnow()
    with SessionLocal() as db:
        rows = list(db.execute(select(Alert.id, Alert.severity, Alert.subject_type, Alert.facility_id, Alert.created_at,
                                      Alert.sla_due_at, Alert.escalation_level)
                               .where(Alert.status == "open").order_by(Alert.created_at)))
        answered = list(db.execute(select(Alert.id, Alert.facility_id).where(Alert.status == "acknowledged")))
    for aid, fid in answered:  # answered alerts nobody is going to close (e.g. after a restart) are closed too
        if fid not in st.demo_facilities and aid not in st.pending:
            st.pending[aid] = {"ack_at": 0, "resolve_at": now + rnd.uniform(60, 1800), "acked": True}
    sim = [r for r in rows if r.facility_id not in st.demo_facilities]
    for r in sim:  # an alert the simulator lost track of still gets a simulated specialist
        if r.id not in st.pending:
            ack = now + rnd.uniform(30, 300)
            st.pending[r.id] = {"ack_at": ack, "resolve_at": ack + rnd.uniform(90, 600)}
    chosen: list[str] = []

    def trim(candidates: list, excess: int) -> None:
        chosen.extend(r.id for r in candidates[:max(0, excess)])

    left = lambda rs: [r for r in rs if r.id not in chosen]  # noqa: E731
    # 1. waiting newborn alerts, 2. overdue, 3. everything waiting - always the oldest simulated ones first
    trim([r for r in sim if r.subject_type == "newborn"], sum(r.subject_type == "newborn" for r in rows) - NEWBORN_WAITING_MAX)
    trim([r for r in left(sim) if _is_overdue(r, now_dt)], sum(_is_overdue(r, now_dt) for r in left(rows)) - OVERDUE_MAX)
    rest = left(sim)
    trim([r for r in rest if not _is_overdue(r, now_dt)] + [r for r in rest if _is_overdue(r, now_dt)],
         len(left(rows)) - OPEN_MAX)
    for aid in chosen[:max_closures]:
        _close_beyond_cap(aid)
    closed = set(chosen[:max_closures])
    rows = [r for r in rows if r.id not in closed]
    sim = [r for r in sim if r.id not in closed]
    overdue = [r for r in rows if _is_overdue(r, now_dt)]
    due_wall = lambda r: now + (r.sla_due_at - now_dt).total_seconds()  # noqa: E731
    waiting_ok = [r for r in sim if not _is_overdue(r, now_dt)]
    planned = [r for r in waiting_ok if st.pending[r.id]["ack_at"] > due_wall(r)]
    # floor for overdue: let the alerts that are closest to their deadline run past it
    # (overdue + on-the-way) is steered to OVERDUE_MAX; about two thirds of them are past the deadline at any moment
    need = OVERDUE_MAX + (2 if len(overdue) < OVERDUE_MIN else 0) - len(overdue) - len(planned)
    if need > 0:
        soon = now + 45 * 60
        for r in sorted((r for r in waiting_ok if r not in planned and r.subject_type != "newborn" and due_wall(r) < soon),
                        key=lambda r: r.sla_due_at)[:need]:
            ack = due_wall(r) + rnd.uniform(10, 45) * 60
            st.pending[r.id] = {"ack_at": ack, "resolve_at": ack + rnd.uniform(90, 600)}
            planned.append(r)
    # floor for waiting: postpone answers that are about to happen (but stay inside the deadline)
    if len(rows) < OPEN_MIN:
        for r in waiting_ok:
            p = st.pending[r.id]
            if r in planned or p["ack_at"] > now + 180 or (r.subject_type == "newborn" and st.newborn_now >= NEWBORN_WAITING_MAX - 1):
                continue
            ack = min(now + rnd.uniform(6, 20) * 60, due_wall(r) - 90)
            if ack > p["ack_at"]:
                st.pending[r.id] = {"ack_at": ack, "resolve_at": ack + rnd.uniform(90, 600)}
    st.open_now, st.overdue_now = len(rows), len(overdue)
    st.newborn_now, st.planned_overdue = sum(r.subject_type == "newborn" for r in rows), len(planned)
    return {"open": st.open_now, "overdue": st.overdue_now, "newborn": st.newborn_now, "closed": len(closed)}


# ------------------------------------------------------------------ loops
async def _guard(name: str, fn) -> None:
    try:
        await asyncio.to_thread(fn)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - the demo must keep running
        if not st.warned:
            log.warning("live %s skipped: %s: %s", name, type(e).__name__, str(e)[:160])
            st.warned = True


SCHEDULER_INTERVAL_SECONDS = 20


async def scheduler_tick() -> None:
    """One scheduler tick: SLA escalation (alert_engine.escalate_overdue) + one live `escalation` event per alert."""
    await _guard("escalation", live_engine.escalation_tick)


async def scheduler_loop() -> None:
    """SLA escalation every 20 s (runs with or without the simulator)."""
    while True:
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
        await scheduler_tick()


async def visits_loop() -> None:
    await asyncio.sleep(2)
    while True:
        if not st.preg_ids:
            ok = False
            try:
                ok = await asyncio.to_thread(_load_population)
            except Exception as e:  # noqa: BLE001 - database not seeded yet
                if not st.warned:
                    log.warning("live simulator waiting for a seeded database (%s)", type(e).__name__)
                    st.warned = True
            if not ok:
                await asyncio.sleep(15)
                continue
            st.warned = False
            await _guard("adopt", _adopt_existing)
            log.info("live simulator started: %d pregnancies, %d newborns, demo district %s untouched",
                     len(st.preg_ids), len(st.newborn_ids), geo.demo_district)
        await asyncio.sleep(rnd.uniform(3, 6))
        await _guard("visit", _sim_newborn if rnd.random() < 0.10 else _sim_visit)


async def responder_loop() -> None:
    n = 0
    while True:
        await asyncio.sleep(3)
        n += 1
        if st.adopted and n % 4 == 0:   # every ~12 s
            await _guard("rebalance", _rebalance)
        if st.pending:
            await _guard("responder", _respond)


def start() -> list[asyncio.Task]:
    s = get_settings()
    tasks: list[asyncio.Task] = []
    if s.APP_ENV == "test":
        return tasks
    tasks.append(asyncio.create_task(scheduler_loop(), name="live-scheduler"))
    if s.APP_ENV == "development" and s.LIVE_SIMULATOR:
        tasks.append(asyncio.create_task(visits_loop(), name="live-sim-visits"))
        tasks.append(asyncio.create_task(responder_loop(), name="live-sim-responder"))
    return tasks

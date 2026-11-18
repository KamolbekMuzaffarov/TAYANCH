from __future__ import annotations

import hashlib
import json
import threading
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models import AuditLog

GENESIS = "0" * 64
_lock = threading.Lock()

# Keys that must never be written into audit detail.
_FORBIDDEN_DETAIL_KEYS = {"full_name", "name", "phone", "address", "pinfl", "birth_date", "transcript", "text",
                          "note", "password", "token", "question", "value"}


def _canonical(row: dict) -> str:
    return json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))


def _row_payload(e: AuditLog) -> dict:
    return {"at": e.at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") if e.at else None, "actor_id": e.actor_id, "actor_role": e.actor_role,
            "action": e.action, "resource_type": e.resource_type, "resource_id": e.resource_id, "outcome": e.outcome,
            "purpose": e.purpose, "request_id": e.request_id, "detail": e.detail}


def write_audit(db: Session, *, actor=None, action: str, resource_type: str, resource_id: str | None = None,
                outcome: str = "success", purpose: str | None = None, request_id: str | None = None,
                detail: dict | None = None, commit: bool = True) -> AuditLog:
    if detail:
        bad = _FORBIDDEN_DETAIL_KEYS & set(detail)
        if bad:
            raise ValueError(f"audit detail must not contain {sorted(bad)}")
    with _lock:
        last = db.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)).scalar_one_or_none()
        prev = last.hash if last else GENESIS
        e = AuditLog(at=utcnow(), actor_id=getattr(actor, "id", None),
                     actor_role=getattr(getattr(actor, "role", None), "value", None), action=action,
                     resource_type=resource_type, resource_id=resource_id, outcome=outcome, purpose=purpose,
                     request_id=request_id, detail=detail, prev_hash=prev, hash="")
        e.hash = hashlib.sha256((prev + _canonical(_row_payload(e))).encode()).hexdigest()
        db.add(e)
        if commit:
            db.commit()
        else:
            db.flush()
    return e


def verify_chain(db: Session) -> dict:
    prev = GENESIS
    n = 0
    for e in db.execute(select(AuditLog).order_by(AuditLog.seq)).scalars():
        n += 1
        if e.prev_hash != prev:
            return {"valid": False, "broken_at_seq": e.seq, "checked": n}
        expected = hashlib.sha256((prev + _canonical(_row_payload(e))).encode()).hexdigest()
        if expected != e.hash:
            return {"valid": False, "broken_at_seq": e.seq, "checked": n}
        prev = e.hash
    return {"valid": True, "checked": n}

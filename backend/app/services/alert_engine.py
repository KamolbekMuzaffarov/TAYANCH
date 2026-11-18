from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import utcnow
from app.models import (Alert, AlertAction, AlertRecipient, CareAssignment, Facility, Newborn, Pregnancy, Role,
                        RiskAssessment, ScopeType, User, UserScope)
from app.services.notifications import alert_message, notifier

OPEN_STATES = ("open", "acknowledged")
TRANSITIONS = {"open": {"acknowledged", "resolved"}, "acknowledged": {"resolved"}, "resolved": set()}
MAX_ESCALATION_LEVEL = 3  # facility admin -> regional OvaBMU -> stays at the top level (no endless re-escalation)
RESOLUTION_CODES = {"clinically_managed", "referred", "false_positive", "data_entry_error", "patient_transferred", "duplicate"}


def _recipients_for(db: Session, subject_type: str, subject) -> set[str]:
    ids: set[str] = set()
    if subject_type == "pregnancy":
        if subject.assigned_clinician_id:
            ids.add(subject.assigned_clinician_id)
        q = select(CareAssignment.user_id).where(CareAssignment.mother_id == subject.mother_id, CareAssignment.active.is_(True))
    else:
        q = select(CareAssignment.user_id).where(CareAssignment.newborn_id == subject.id, CareAssignment.active.is_(True))
    ids.update(db.execute(q).scalars())
    if subject_type == "newborn":  # neonatologists of the facility
        q2 = (select(User.id).join(UserScope, UserScope.user_id == User.id)
              .where(User.role == Role.NEONATOLOGIST, UserScope.scope_type == ScopeType.FACILITY,
                     UserScope.facility_id == subject.facility_id))
        ids.update(db.execute(q2).scalars())
    return ids


def raise_alert(db: Session, *, subject_type: str, subject, assessment: RiskAssessment, region_soato: str,
                is_synthetic: bool) -> Alert | None:
    if assessment.level not in ("red", "yellow"):
        return None
    s = get_settings()
    severity = assessment.level
    rules = [t["rule_id"] for t in assessment.triggered_rules if t["severity"] == severity] or \
            [t["rule_id"] for t in assessment.triggered_rules]
    dedup = f"{subject_type}:{subject.id}:{severity}"
    existing = db.execute(select(Alert).where(Alert.dedup_key == dedup, Alert.status.in_(OPEN_STATES))).scalar_one_or_none()
    now = utcnow()
    if existing:
        new_rules = sorted(set(rules) - set(existing.rule_ids))
        existing.occurrences += 1
        existing.risk_assessment_id = assessment.id
        if new_rules:  # an important change must not be swallowed by dedup
            existing.rule_ids = sorted(set(existing.rule_ids) | set(new_rules))
            existing.reason_codes = existing.rule_ids
        db.add(AlertAction(alert_id=existing.id, action="repeated", at=now,
                           detail={"new_rule_ids": new_rules, "assessment_id": assessment.id}))
        db.flush()
        return existing
    sla = s.ALERT_SLA_MINUTES_RED if severity == "red" else s.ALERT_SLA_MINUTES_YELLOW
    alert = Alert(subject_type=subject_type, subject_id=subject.id,
                  mother_id=subject.mother_id if subject_type == "pregnancy" else subject.mother_id,
                  newborn_id=subject.id if subject_type == "newborn" else None, facility_id=subject.facility_id,
                  region_soato=region_soato, severity=severity, rule_ids=sorted(rules), reason_codes=sorted(rules),
                  risk_assessment_id=assessment.id, dedup_key=dedup, sla_due_at=now + timedelta(minutes=sla),
                  is_synthetic=is_synthetic)
    db.add(alert)
    db.flush()
    db.add(AlertAction(alert_id=alert.id, action="created", at=now, detail={"rule_ids": alert.rule_ids}))
    for uid in _recipients_for(db, subject_type, subject):
        ok = notifier.send(uid, "in_app", alert_message(alert.id, severity))
        db.add(AlertRecipient(alert_id=alert.id, user_id=uid, channel="in_app",
                              delivered_at=now if ok else None, delivery_status="delivered" if ok else "failed"))
    write_audit(db, action="alert.created", resource_type="alert", resource_id=alert.id,
                detail={"severity": severity, "rule_ids": alert.rule_ids}, commit=False)
    db.flush()
    return alert


def transition(db: Session, alert: Alert, user: User, target: str, resolution_code: str | None = None,
               request_id: str | None = None) -> Alert:
    if target not in TRANSITIONS.get(alert.status, set()):
        raise AppError(409, "invalid_transition", f"Cannot move alert from {alert.status} to {target}")
    now = utcnow()
    if target == "acknowledged":
        alert.status, alert.acknowledged_at, alert.acknowledged_by = "acknowledged", now, user.id
        alert.assigned_to = alert.assigned_to or user.id
    elif target == "resolved":
        if resolution_code not in RESOLUTION_CODES:
            raise AppError(422, "invalid_resolution", f"resolution_code must be one of {sorted(RESOLUTION_CODES)}")
        if alert.acknowledged_at is None:
            alert.acknowledged_at, alert.acknowledged_by = now, user.id
        alert.status, alert.resolved_at, alert.resolved_by, alert.resolution_code = "resolved", now, user.id, resolution_code
    db.add(AlertAction(alert_id=alert.id, action=target, actor_id=user.id, at=now,
                       detail={"resolution_code": resolution_code} if resolution_code else None))
    write_audit(db, actor=user, action=f"alert.{target}", resource_type="alert", resource_id=alert.id,
                request_id=request_id, detail={"resolution_code": resolution_code} if resolution_code else None, commit=False)
    db.commit()
    return alert


def escalate_overdue(db: Session, now: datetime | None = None) -> list[str]:
    """Escalate unacknowledged alerts past SLA: level 1 -> facility admins, level 2+ -> regional OvaBMU specialists."""
    now = now or utcnow()
    s = get_settings()
    escalated = []
    for a in db.execute(select(Alert).where(Alert.status == "open", Alert.sla_due_at < now,
                                            Alert.escalation_level < MAX_ESCALATION_LEVEL)).scalars():
        a.escalation_level += 1
        a.sla_due_at = now + timedelta(minutes=s.ALERT_SLA_MINUTES_RED if a.severity == "red" else s.ALERT_SLA_MINUTES_YELLOW)
        if a.escalation_level == 1:
            q = (select(User.id).join(UserScope, UserScope.user_id == User.id)
                 .where(User.role == Role.FACILITY_ADMIN, UserScope.facility_id == a.facility_id))
        else:
            ovabmu_fac = select(Facility.id).where(Facility.region_soato == a.region_soato, Facility.facility_type == "ovabmu")
            q = (select(User.id).join(UserScope, UserScope.user_id == User.id)
                 .where(User.role == Role.OVABMU_SPECIALIST, UserScope.facility_id.in_(ovabmu_fac)))
        targets = set(db.execute(q).scalars())
        for uid in targets:
            notifier.send(uid, "in_app", alert_message(a.id, a.severity))
            db.add(AlertRecipient(alert_id=a.id, user_id=uid, channel="in_app", delivered_at=now, delivery_status="delivered"))
        db.add(AlertAction(alert_id=a.id, action="escalated", at=now, detail={"level": a.escalation_level, "recipients": len(targets)}))
        write_audit(db, action="alert.escalated", resource_type="alert", resource_id=a.id,
                    detail={"level": a.escalation_level}, commit=False)
        escalated.append(a.id)
    db.commit()
    return escalated


def subject_of(db: Session, alert: Alert):
    return db.get(Pregnancy, alert.subject_id) if alert.subject_type == "pregnancy" else db.get(Newborn, alert.subject_id)

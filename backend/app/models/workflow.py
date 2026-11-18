from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import utcnow
from app.db import Base
from app.models.common import IdMixin, TimestampMixin, UTCDateTime


# ------------------------------------------------------------------ alerts
class Alert(IdMixin, TimestampMixin, Base):
    __tablename__ = "alerts"
    subject_type: Mapped[str] = mapped_column(String(12), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    mother_id: Mapped[str | None] = mapped_column(ForeignKey("mothers.id"))
    newborn_id: Mapped[str | None] = mapped_column(ForeignKey("newborns.id"))
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), nullable=False, index=True)
    region_soato: Mapped[str] = mapped_column(ForeignKey("regions.soato"), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # red|yellow
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    risk_assessment_id: Mapped[str | None] = mapped_column(ForeignKey("risk_assessments.id"))
    dedup_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)  # open|acknowledged|resolved
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    sla_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledged_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    resolution_code: Mapped[str | None] = mapped_column(String(40))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (Index("ix_alerts_status_fac", "status", "facility_id"),)


class AlertRecipient(IdMixin, Base):
    __tablename__ = "alert_recipients"
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default="in_app", nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivery_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)


class AlertAction(IdMixin, Base):
    __tablename__ = "alert_actions"
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)  # created|repeated|acknowledged|escalated|resolved|referred
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON)


class Referral(IdMixin, TimestampMixin, Base):
    __tablename__ = "referrals"
    subject_type: Mapped[str] = mapped_column(String(12), nullable=False)
    mother_id: Mapped[str | None] = mapped_column(ForeignKey("mothers.id"), index=True)
    newborn_id: Mapped[str | None] = mapped_column(ForeignKey("newborns.id"), index=True)
    pregnancy_id: Mapped[str | None] = mapped_column(ForeignKey("pregnancies.id"))
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id"))
    from_facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), nullable=False)
    to_facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)  # emergency|urgent|routine
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="sent", nullable=False)  # sent|accepted|consulted|closed|declined
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    accepted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    consult_outcome: Mapped[str | None] = mapped_column(String(60))
    consult_note_enc: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ------------------------------------------------------------------ regional analytics
class DataSource(IdMixin, TimestampMixin, Base):
    __tablename__ = "data_sources"
    key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # official_statistics|household_survey|clinical_registry|synthetic|...
    url: Mapped[str | None] = mapped_column(String(300))
    license: Mapped[str | None] = mapped_column(String(200))
    citation: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DataImportRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "data_import_runs"
    source_key: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # running|success|failed
    rows_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_loaded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    started_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RegionalIndicator(IdMixin, TimestampMixin, Base):
    __tablename__ = "regional_indicators"
    indicator_code: Mapped[str] = mapped_column(String(60), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)  # maternal|newborn|response|context
    region_soato: Mapped[str] = mapped_column(String(8), nullable=False, index=True)  # 1700 = national
    district_code: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    numerator: Mapped[float | None] = mapped_column(Float)
    denominator: Mapped[float | None] = mapped_column(Float)
    rate: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    ci_low: Mapped[float | None] = mapped_column(Float)
    ci_high: Mapped[float | None] = mapped_column(Float)
    n_unweighted: Mapped[int | None] = mapped_column(Integer)
    source_key: Mapped[str] = mapped_column(String(40), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(80))
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    quality_status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)  # ok|low_precision|stale|invalid
    suppression_status: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    methodology: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("indicator_code", "region_soato", "district_code", "period_start", "source_key", name="uq_regind"),
        Index("ix_regind_code_period", "indicator_code", "period_end"),
    )


class RegionalRiskScore(IdMixin, TimestampMixin, Base):
    __tablename__ = "regional_risk_scores"
    region_soato: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    district_code: Mapped[str | None] = mapped_column(String(32))
    layer: Mapped[str] = mapped_column(String(24), nullable=False)  # maternal_area_risk|newborn_area_risk|healthcare_response_risk|overall_area_status
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # red|yellow|green|grey
    components: Mapped[list] = mapped_column(JSON, default=list)
    methodology_version: Mapped[str] = mapped_column(String(40), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(80), nullable=False)
    __table_args__ = (UniqueConstraint("region_soato", "district_code", "layer", "period_year", "methodology_version", name="uq_regrisk"),)


# ------------------------------------------------------------------ voice
class VoiceJob(IdMixin, TimestampMixin, Base):
    """Minimal metadata only - no audio, no transcript."""
    __tablename__ = "voice_jobs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # done|failed|blocked
    audio_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    external_job_id: Mapped[str | None] = mapped_column(String(80))
    external_deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    error_code: Mapped[str | None] = mapped_column(String(60))


class VoiceDraft(IdMixin, TimestampMixin, Base):
    __tablename__ = "voice_drafts"
    job_id: Mapped[str] = mapped_column(ForeignKey("voice_jobs.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(12), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transcript_enc: Mapped[str | None] = mapped_column(Text)  # encrypted, TTL-limited
    parsed_fields: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)  # draft|confirmed|discarded|expired
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    confirmed_record_id: Mapped[str | None] = mapped_column(String(36))


# ------------------------------------------------------------------ consent / privacy / audit
class Consent(IdMixin, TimestampMixin, Base):
    __tablename__ = "consents"
    mother_id: Mapped[str | None] = mapped_column(ForeignKey("mothers.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)  # registry|external_voice_processing|research|...
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # granted|withdrawn|refused
    legal_basis: Mapped[str] = mapped_column(String(60), nullable=False)
    recorded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PrivacyEvent(IdMixin, Base):
    __tablename__ = "privacy_events"
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)  # llm_blocked|llm_allowed|voice_external_blocked|export_suppressed
    feature: Mapped[str | None] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)  # detector names only, never the matched text
    request_id: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(30))
    model: Mapped[str | None] = mapped_column(String(60))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(30))


class AuditLog(Base):
    """Append-only, hash-chained audit trail (tamper-evident)."""
    __tablename__ = "audit_logs"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(60))
    request_id: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[dict | None] = mapped_column(JSON)  # codes and ids only
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

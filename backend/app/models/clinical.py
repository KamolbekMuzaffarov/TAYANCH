from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import utcnow
from app.db import Base
from app.models.common import IdMixin, TimestampMixin, UTCDateTime


class Mother(IdMixin, TimestampMixin, Base):
    __tablename__ = "mothers"
    local_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # internal only, never in messages
    full_name_enc: Mapped[str | None] = mapped_column(Text)
    phone_enc: Mapped[str | None] = mapped_column(Text)
    address_enc: Mapped[str | None] = mapped_column(Text)
    pinfl_enc: Mapped[str | None] = mapped_column(Text)
    pinfl_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    birth_date_enc: Mapped[str | None] = mapped_column(Text)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    region_soato: Mapped[str] = mapped_column(ForeignKey("regions.soato"), nullable=False, index=True)
    district_code: Mapped[str | None] = mapped_column(ForeignKey("districts.code"))
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), nullable=False, index=True)
    legal_basis: Mapped[str] = mapped_column(String(40), default="healthcare_provision", nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    pregnancies: Mapped[list["Pregnancy"]] = relationship(back_populates="mother", lazy="selectin")


class CareAssignment(IdMixin, TimestampMixin, Base):
    """Which clinician is responsible for which patient (ABAC: assignment)."""
    __tablename__ = "care_assignments"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    mother_id: Mapped[str | None] = mapped_column(ForeignKey("mothers.id"), index=True)
    newborn_id: Mapped[str | None] = mapped_column(ForeignKey("newborns.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Pregnancy(IdMixin, TimestampMixin, Base):
    __tablename__ = "pregnancies"
    mother_id: Mapped[str] = mapped_column(ForeignKey("mothers.id"), nullable=False, index=True)
    lmp_date: Mapped[date | None] = mapped_column(Date)
    edd: Mapped[date | None] = mapped_column(Date)
    gravida: Mapped[int | None] = mapped_column(Integer)
    parity: Mapped[int | None] = mapped_column(Integer)
    plurality: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active|delivered|ended
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), nullable=False, index=True)
    assigned_clinician_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    current_risk_level: Mapped[str] = mapped_column(String(10), default="grey", nullable=False)
    current_risk_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    mother: Mapped[Mother] = relationship(back_populates="pregnancies")


class MaternalHistory(IdMixin, TimestampMixin, Base):
    __tablename__ = "maternal_histories"
    mother_id: Mapped[str] = mapped_column(ForeignKey("mothers.id"), nullable=False, index=True)
    factor_code: Mapped[str] = mapped_column(String(60), nullable=False)  # coded risk factor, no free text
    present: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    recorded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class MaternalObservation(IdMixin, TimestampMixin, Base):
    __tablename__ = "maternal_observations"
    pregnancy_id: Mapped[str] = mapped_column(ForeignKey("pregnancies.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    gestational_week: Mapped[float | None] = mapped_column(Float)
    systolic_bp: Mapped[int | None] = mapped_column(Integer)
    diastolic_bp: Mapped[int | None] = mapped_column(Integer)
    heart_rate: Mapped[int | None] = mapped_column(Integer)
    respiratory_rate: Mapped[int | None] = mapped_column(Integer)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    spo2: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    fetal_heart_rate: Mapped[int | None] = mapped_column(Integer)
    proteinuria: Mapped[str | None] = mapped_column(String(8))  # neg|trace|1+|2+|3+|4+
    reduced_fetal_movements: Mapped[bool | None] = mapped_column(Boolean)
    vaginal_bleeding: Mapped[bool | None] = mapped_column(Boolean)
    severe_headache: Mapped[bool | None] = mapped_column(Boolean)
    visual_disturbance: Mapped[bool | None] = mapped_column(Boolean)
    convulsions: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual|voice|import
    voice_draft_id: Mapped[str | None] = mapped_column(String(36))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint("systolic_bp IS NULL OR (systolic_bp BETWEEN 40 AND 300)", name="ck_obs_sbp"),
        CheckConstraint("diastolic_bp IS NULL OR (diastolic_bp BETWEEN 20 AND 200)", name="ck_obs_dbp"),
        CheckConstraint("spo2 IS NULL OR (spo2 BETWEEN 40 AND 100)", name="ck_obs_spo2"),
        Index("ix_mobs_preg_time", "pregnancy_id", "observed_at"),
    )


class LabResult(IdMixin, TimestampMixin, Base):
    __tablename__ = "lab_results"
    pregnancy_id: Mapped[str] = mapped_column(ForeignKey("pregnancies.id"), nullable=False, index=True)
    test_code: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    facility_id: Mapped[str | None] = mapped_column(ForeignKey("facilities.id"))
    status: Mapped[str] = mapped_column(String(16), default="final", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class Fetus(IdMixin, TimestampMixin, Base):
    __tablename__ = "fetuses"
    pregnancy_id: Mapped[str] = mapped_column(ForeignKey("pregnancies.id"), nullable=False, index=True)
    fetus_index: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (UniqueConstraint("pregnancy_id", "fetus_index"),)


class CtgRecord(IdMixin, TimestampMixin, Base):
    """Placeholder for the future CTG adapter (summary features only, no raw traces)."""
    __tablename__ = "ctg_records"
    fetus_id: Mapped[str] = mapped_column(ForeignKey("fetuses.id"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    baseline_fhr: Mapped[int | None] = mapped_column(Integer)
    variability_bpm: Mapped[float | None] = mapped_column(Float)
    accelerations: Mapped[int | None] = mapped_column(Integer)
    decelerations: Mapped[int | None] = mapped_column(Integer)
    source_format: Mapped[str | None] = mapped_column(String(40))


class Newborn(IdMixin, TimestampMixin, Base):
    __tablename__ = "newborns"
    local_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    mother_id: Mapped[str | None] = mapped_column(ForeignKey("mothers.id"), index=True)
    pregnancy_id: Mapped[str | None] = mapped_column(ForeignKey("pregnancies.id"))
    birth_datetime: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sex: Mapped[str | None] = mapped_column(String(1))  # F|M|U
    gestational_age_weeks: Mapped[float | None] = mapped_column(Float)
    birth_weight_g: Mapped[int | None] = mapped_column(Integer)
    apgar_1: Mapped[int | None] = mapped_column(Integer)
    apgar_5: Mapped[int | None] = mapped_column(Integer)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), nullable=False, index=True)
    region_soato: Mapped[str] = mapped_column(ForeignKey("regions.soato"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="in_facility", nullable=False)  # in_facility|discharged|transferred|deceased
    current_risk_level: Mapped[str] = mapped_column(String(10), default="grey", nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint("birth_weight_g IS NULL OR (birth_weight_g BETWEEN 200 AND 7000)", name="ck_nb_weight"),
        CheckConstraint("apgar_5 IS NULL OR (apgar_5 BETWEEN 0 AND 10)", name="ck_nb_apgar5"),
    )


class NewbornObservation(IdMixin, TimestampMixin, Base):
    __tablename__ = "newborn_observations"
    newborn_id: Mapped[str] = mapped_column(ForeignKey("newborns.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    respiratory_rate: Mapped[int | None] = mapped_column(Integer)
    heart_rate: Mapped[int | None] = mapped_column(Integer)
    spo2: Mapped[int | None] = mapped_column(Integer)
    weight_g: Mapped[int | None] = mapped_column(Integer)
    jaundice: Mapped[bool | None] = mapped_column(Boolean)
    poor_feeding: Mapped[bool | None] = mapped_column(Boolean)
    convulsions: Mapped[bool | None] = mapped_column(Boolean)
    severe_chest_indrawing: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    voice_draft_id: Mapped[str | None] = mapped_column(String(36))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class RiskAssessment(IdMixin, TimestampMixin, Base):
    __tablename__ = "risk_assessments"
    subject_type: Mapped[str] = mapped_column(String(12), nullable=False)  # pregnancy|newborn
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)  # red|yellow|green|grey
    triggered_rules: Mapped[list] = mapped_column(JSON, default=list)
    missing_inputs: Mapped[list] = mapped_column(JSON, default=list)
    rule_set_version: Mapped[str] = mapped_column(String(40), nullable=False)
    ml_shadow: Mapped[dict | None] = mapped_column(JSON)  # shadow-mode model output, never drives alerts
    model_version: Mapped[str | None] = mapped_column(String(60))
    trigger_source_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ClinicalRuleVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "clinical_rule_versions"
    rule_id: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(12), nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(80), nullable=False)  # approved|demo_pending_clinical_approval|retired
    approved_by: Mapped[str | None] = mapped_column(String(200))
    clinical_reference: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("rule_id", "version"),)


class ModelVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "model_versions"
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    training_dataset: Mapped[str] = mapped_column(String(200), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # shadow|approved|retired
    artifact_path: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("name", "version"),)


class VisitDetail(IdMixin, TimestampMixin, Base):
    """Extended visit record: diagnoses (ICD-10), mood screening (EPDS), complaints and contributing causes.
    Codes are stored as coded lists; free-text notes are encrypted at field level."""
    __tablename__ = "visit_details"
    pregnancy_id: Mapped[str] = mapped_column(ForeignKey("pregnancies.id"), nullable=False, index=True)
    mother_id: Mapped[str] = mapped_column(ForeignKey("mothers.id"), nullable=False, index=True)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    gestational_week: Mapped[float | None] = mapped_column(Float)
    diagnoses: Mapped[list] = mapped_column(JSON, default=list)
    epds_answers: Mapped[list | None] = mapped_column(JSON)
    epds_score: Mapped[int | None] = mapped_column(Integer)
    mood_flag: Mapped[str | None] = mapped_column(String(10))  # ok|watch|urgent
    stress_level: Mapped[int | None] = mapped_column(Integer)  # 0..3
    complaints: Mapped[list] = mapped_column(JSON, default=list)
    complaint_note_enc: Mapped[str | None] = mapped_column(Text)
    causes: Mapped[list] = mapped_column(JSON, default=list)
    cause_note_enc: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

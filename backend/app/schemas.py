from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------ auth
class LoginIn(Strict):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    otp: str | None = Field(default=None, pattern=r"^\d{6}$")


class RefreshIn(Strict):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class ScopeOut(Out):
    scope_type: str
    region_soato: str | None
    district_code: str | None
    facility_id: str | None


class MeOut(Out):
    id: str
    username: str
    full_name: str
    role: str
    mfa_enabled: bool
    scopes: list[ScopeOut]
    permissions: list[str]


# ------------------------------------------------------------------ registry
Proteinuria = Literal["neg", "trace", "1+", "2+", "3+", "4+"]


class MotherCreate(Strict):
    full_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, pattern=r"^\+?\d{9,13}$")
    pinfl: str | None = Field(default=None, pattern=r"^\d{14}$")
    address: str | None = Field(default=None, max_length=300)
    birth_date: date | None = None
    facility_id: str
    district_code: str | None = None
    legal_basis: Literal["healthcare_provision", "consent"] = "healthcare_provision"
    registry_consent: bool = True
    is_synthetic: bool = False


class MotherUpdate(Strict):
    phone: str | None = Field(default=None, pattern=r"^\+?\d{9,13}$")
    address: str | None = Field(default=None, max_length=300)
    district_code: str | None = None


class MotherSummary(Out):
    id: str
    local_code: str
    display_name: str
    birth_year: int | None
    region_soato: str
    facility_id: str
    is_synthetic: bool
    current_risk_level: str | None = None


class PregnancyCreate(Strict):
    lmp_date: date | None = None
    edd: date | None = None
    gravida: int | None = Field(default=None, ge=1, le=25)
    parity: int | None = Field(default=None, ge=0, le=25)
    plurality: int = Field(default=1, ge=1, le=6)
    risk_factors: list[Literal["prior_caesarean", "prior_preeclampsia", "chronic_hypertension", "pregestational_diabetes",
                               "prior_stillbirth", "prior_preterm_birth", "anaemia_history", "renal_disease",
                               "cardiac_disease", "age_under_18", "age_over_35", "multiple_pregnancy"]] = []

    @model_validator(mode="after")
    def _check(self):
        if self.lmp_date and self.lmp_date > date.today():
            raise ValueError("lmp_date cannot be in the future")
        if self.gravida is not None and self.parity is not None and self.parity >= self.gravida + 1:
            raise ValueError("parity must be < gravida + 1")
        return self


class PregnancyOut(Out):
    id: str
    mother_id: str
    lmp_date: date | None
    edd: date | None
    gravida: int | None
    parity: int | None
    status: str
    facility_id: str
    current_risk_level: str
    current_risk_at: datetime | None
    is_synthetic: bool


class MaternalObservationIn(Strict):
    observed_at: datetime | None = None
    gestational_week: float | None = Field(default=None, ge=4, le=44)
    systolic_bp: int | None = Field(default=None, ge=50, le=260)
    diastolic_bp: int | None = Field(default=None, ge=30, le=170)
    heart_rate: int | None = Field(default=None, ge=30, le=220)
    respiratory_rate: int | None = Field(default=None, ge=5, le=60)
    temperature_c: float | None = Field(default=None, ge=30, le=43)
    spo2: int | None = Field(default=None, ge=50, le=100)
    weight_kg: float | None = Field(default=None, ge=30, le=200)
    fetal_heart_rate: int | None = Field(default=None, ge=50, le=240)
    proteinuria: Proteinuria | None = None
    reduced_fetal_movements: bool | None = None
    vaginal_bleeding: bool | None = None
    severe_headache: bool | None = None
    visual_disturbance: bool | None = None
    convulsions: bool | None = None
    idempotency_key: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _bp(self):
        if self.systolic_bp is not None and self.diastolic_bp is not None and self.systolic_bp <= self.diastolic_bp:
            raise ValueError("systolic_bp must be greater than diastolic_bp")
        if self.observed_at and self.observed_at.tzinfo and self.observed_at > datetime.now(self.observed_at.tzinfo):
            raise ValueError("observed_at cannot be in the future")
        return self


class MaternalObservationOut(Out):
    id: str
    pregnancy_id: str
    observed_at: datetime
    gestational_week: float | None
    systolic_bp: int | None
    diastolic_bp: int | None
    heart_rate: int | None
    respiratory_rate: int | None
    temperature_c: float | None
    spo2: int | None
    weight_kg: float | None
    fetal_heart_rate: int | None
    proteinuria: str | None
    reduced_fetal_movements: bool | None
    vaginal_bleeding: bool | None
    severe_headache: bool | None
    visual_disturbance: bool | None
    convulsions: bool | None
    source: str


LAB_UNITS = {"hemoglobin": {"g/L", "g/dL"}, "glucose_fasting": {"mmol/L"}, "platelets": {"10^9/L"},
             "urine_protein_g_24h": {"g/24h"}, "creatinine": {"umol/L"}, "alt": {"U/L"}, "ast": {"U/L"}}


class LabResultIn(Strict):
    test_code: Literal["hemoglobin", "glucose_fasting", "platelets", "urine_protein_g_24h", "creatinine", "alt", "ast"]
    value: float = Field(ge=0, le=100000)
    unit: str
    collected_at: datetime
    idempotency_key: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _unit(self):
        if self.unit not in LAB_UNITS[self.test_code]:
            raise ValueError(f"unit for {self.test_code} must be one of {sorted(LAB_UNITS[self.test_code])}")
        if self.test_code == "hemoglobin":
            if self.unit == "g/L" and not 30 <= self.value <= 220:
                raise ValueError("hemoglobin g/L out of plausible range (30-220)")
            if self.unit == "g/dL" and not 3 <= self.value <= 22:
                raise ValueError("hemoglobin g/dL out of plausible range (3-22)")
        return self


class LabResultOut(Out):
    id: str
    pregnancy_id: str
    test_code: str
    value: float
    unit: str
    collected_at: datetime
    status: str


class NewbornCreate(Strict):
    mother_id: str | None = None
    pregnancy_id: str | None = None
    birth_datetime: datetime
    sex: Literal["F", "M", "U"] | None = None
    gestational_age_weeks: float | None = Field(default=None, ge=20, le=45)
    birth_weight_g: int | None = Field(default=None, ge=300, le=6500)
    apgar_1: int | None = Field(default=None, ge=0, le=10)
    apgar_5: int | None = Field(default=None, ge=0, le=10)
    facility_id: str
    is_synthetic: bool = False


class NewbornOut(Out):
    id: str
    local_code: str
    mother_id: str | None
    birth_datetime: datetime
    sex: str | None
    gestational_age_weeks: float | None
    birth_weight_g: int | None
    apgar_1: int | None
    apgar_5: int | None
    facility_id: str
    region_soato: str
    status: str
    current_risk_level: str
    is_synthetic: bool


class NewbornObservationIn(Strict):
    observed_at: datetime | None = None
    temperature_c: float | None = Field(default=None, ge=30, le=43)
    respiratory_rate: int | None = Field(default=None, ge=5, le=120)
    heart_rate: int | None = Field(default=None, ge=50, le=260)
    spo2: int | None = Field(default=None, ge=50, le=100)
    weight_g: int | None = Field(default=None, ge=300, le=6500)
    jaundice: bool | None = None
    poor_feeding: bool | None = None
    convulsions: bool | None = None
    severe_chest_indrawing: bool | None = None
    idempotency_key: str | None = Field(default=None, max_length=80)


class RiskAssessmentOut(Out):
    id: str
    subject_type: str
    subject_id: str
    level: str
    triggered_rules: list
    missing_inputs: list
    rule_set_version: str
    ml_shadow: dict | None
    created_at: datetime


# ------------------------------------------------------------------ alerts / referrals
class AlertOut(Out):
    id: str
    subject_type: str
    subject_id: str
    facility_id: str
    region_soato: str
    severity: str
    rule_ids: list
    status: str
    sla_due_at: datetime
    escalation_level: int
    occurrences: int
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    resolution_code: str | None
    is_synthetic: bool
    created_at: datetime


class ResolveIn(Strict):
    resolution_code: Literal["clinically_managed", "referred", "false_positive", "data_entry_error", "patient_transferred", "duplicate"]


class ReferralCreate(Strict):
    subject_type: Literal["pregnancy", "newborn"]
    pregnancy_id: str | None = None
    newborn_id: str | None = None
    alert_id: str | None = None
    to_facility_id: str
    priority: Literal["emergency", "urgent", "routine"]
    reason_code: Literal["severe_hypertension", "preeclampsia_suspected", "bleeding", "severe_anaemia", "fetal_concern",
                         "preterm_labour", "neonatal_respiratory", "neonatal_hypothermia", "low_birth_weight",
                         "neonatal_danger_signs", "other_specialist_review"]

    @model_validator(mode="after")
    def _subj(self):
        if (self.subject_type == "pregnancy") != bool(self.pregnancy_id) or (self.subject_type == "newborn") != bool(self.newborn_id):
            raise ValueError("provide pregnancy_id for pregnancy referrals or newborn_id for newborn referrals")
        return self


class ReferralAction(Strict):
    action: Literal["accept", "decline", "record_consultation", "close"]
    consult_outcome: Literal["admitted", "managed_remotely", "transferred_to_republic_center", "returned_to_local_care",
                             "no_action_needed"] | None = None
    consult_note: str | None = Field(default=None, max_length=2000)


class ReferralOut(Out):
    id: str
    subject_type: str
    mother_id: str | None
    newborn_id: str | None
    pregnancy_id: str | None
    alert_id: str | None
    from_facility_id: str
    to_facility_id: str
    priority: str
    reason_code: str
    status: str
    accepted_at: datetime | None
    consult_outcome: str | None
    closed_at: datetime | None
    is_synthetic: bool
    created_at: datetime


# ------------------------------------------------------------------ voice
class VoiceDraftOut(BaseModel):
    draft_id: str
    job_id: str
    provider: str
    processing_location: str
    transcript: str
    candidates: list[dict]
    expires_at: datetime
    requires_confirmation: bool = True


class ConfirmedField(Strict):
    field: str
    value: float | int | str | bool


class VoiceConfirmIn(Strict):
    fields: list[ConfirmedField] = Field(min_length=1, max_length=20)
    reviewed_transcript: bool = Field(description="Clinician confirms the transcript and values were reviewed")
    observed_at: datetime | None = None

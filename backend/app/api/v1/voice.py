from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.registry import save_maternal_observation, save_newborn_observation
from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.deps import current_user, rate_limiter
from app.core.errors import AppError, not_found
from app.core.permissions import assert_newborn_access, assert_pregnancy_access, require
from app.core.security import decrypt, encrypt, utcnow
from app.db import get_db
from app.models import LabResult, Newborn, Pregnancy, PrivacyEvent, User, VoiceDraft, VoiceJob
from app.schemas import LabResultIn, MaternalObservationIn, NewbornObservationIn, VoiceConfirmIn, VoiceDraftOut
from app.services import risk_engine
from app.services.voice.parser import parse
from app.services.voice.providers import SUPPORTED_LANGUAGES, LocalSTTProvider, VoiceLabSTTProvider, get_provider

router = APIRouter(prefix="/voice", tags=["voice"])

_MAGIC = [(b"RIFF", "audio/wav"), (b"OggS", "audio/ogg"), (b"\x1aE\xdf\xa3", "audio/webm"), (b"fLaC", "audio/flac"),
          (b"ID3", "audio/mpeg"), (b"\xff\xfb", "audio/mpeg"), (b"\xff\xf3", "audio/mpeg")]
PREG_FIELDS = set(MaternalObservationIn.model_fields) - {"observed_at", "idempotency_key"}
NB_FIELDS = set(NewbornObservationIn.model_fields) - {"observed_at", "idempotency_key"}
LAB_FIELDS = {"lab_hemoglobin": ("hemoglobin", "g/L"), "lab_glucose_fasting": ("glucose_fasting", "mmol/L")}


def _sniff(data: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    return None


def _subject(db: Session, user: User, subject_type: str, subject_id: str):
    if subject_type == "pregnancy":
        return assert_pregnancy_access(db, user, db.get(Pregnancy, subject_id), "update")
    if subject_type == "newborn":
        return assert_newborn_access(db, user, db.get(Newborn, subject_id), "update")
    raise AppError(422, "invalid_subject", "subject_type must be pregnancy or newborn")


def _draft_out(d: VoiceDraft, job: VoiceJob) -> VoiceDraftOut:
    return VoiceDraftOut(draft_id=d.id, job_id=job.id, provider=job.provider,
                         processing_location="external_approved_provider" if job.provider == "voicelab" else "local_server",
                         transcript=decrypt(d.transcript_enc) or "", candidates=d.parsed_fields, expires_at=d.expires_at)


@router.post("/transcribe", response_model=VoiceDraftOut, status_code=201)
async def transcribe(request: Request, audio: UploadFile = File(...), subject_type: str = Form(...), subject_id: str = Form(...),
                     language: str = Form("uz"), provider: str | None = Form(None), consent_external: bool = Form(False),
                     idempotency_key: str | None = Form(None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    require(user, "voice:use")
    rate_limiter.hit(f"voice:{user.id}", 30)
    _subject(db, user, subject_type, subject_id)
    if language not in SUPPORTED_LANGUAGES:
        raise AppError(422, "unsupported_language", f"language must be one of {sorted(SUPPORTED_LANGUAGES)}")
    if idempotency_key:
        prev = db.execute(select(VoiceJob).where(VoiceJob.idempotency_key == idempotency_key, VoiceJob.user_id == user.id)).scalar_one_or_none()
        if prev:
            d = db.execute(select(VoiceDraft).where(VoiceDraft.job_id == prev.id)).scalar_one_or_none()
            if d and d.status == "draft":
                return _draft_out(d, prev)
            raise AppError(409, "duplicate_submission", "This audio was already processed")
    data = await audio.read(s.VOICE_MAX_AUDIO_BYTES + 1)
    if len(data) > s.VOICE_MAX_AUDIO_BYTES:
        raise AppError(413, "payload_too_large", "Audio too large")
    mime = _sniff(data)
    if mime is None:
        dev_text = (audio.content_type or "").startswith("text/") and s.LOCAL_STT_DEV_STUB and not s.is_production
        if not dev_text:
            raise AppError(415, "unsupported_media", "Unsupported or unrecognised audio format")
        mime = "text/plain"
    prov = get_provider(provider)
    job = VoiceJob(user_id=user.id, provider=prov.name, language=language, status="running", audio_bytes=len(data),
                   idempotency_key=idempotency_key)
    db.add(job)
    db.flush()
    try:
        if isinstance(prov, VoiceLabSTTProvider):
            reasons = prov.gate(consent_external)
            if reasons:
                db.add(PrivacyEvent(user_id=user.id, event_type="voice_external_blocked", feature="voice", decision="blocked",
                                    reasons=reasons, provider="voicelab"))
                job.status, job.error_code = "blocked", "external_stt_blocked"
                db.commit()
                raise AppError(403, "external_stt_blocked", "External STT blocked: " + ", ".join(reasons))
            res = await run_in_threadpool(prov.transcribe, data, mime, language, idempotency_key or job.id,
                                          consent_external=True)
        else:
            res = await run_in_threadpool(prov.transcribe, data, mime, language, idempotency_key or job.id)
    except AppError as e:
        if job.status != "blocked":
            job.status, job.error_code = "failed", e.code
            db.commit()
        raise
    finally:
        del data  # audio is never persisted
    job.status, job.provider, job.duration_ms = "done", res.provider, res.duration_ms
    if res.external_id and isinstance(prov, VoiceLabSTTProvider):
        job.external_job_id = res.external_id
        if prov.delete_remote(res.external_id):  # retention policy: delete at vendor immediately
            job.external_deleted_at = utcnow()
    candidates = parse(res.text, subject_type)
    if res.confidence is not None and res.confidence < 0.8:  # low acoustic confidence -> every value needs review
        for c in candidates:
            c["needs_review"] = True
            c["confidence"] = round(min(c["confidence"], res.confidence), 2)
            c["note"] = c["note"] or "low speech-recognition confidence"
    draft = VoiceDraft(job_id=job.id, user_id=user.id, subject_type=subject_type, subject_id=subject_id,
                       transcript_enc=encrypt(res.text), parsed_fields=candidates,
                       expires_at=utcnow() + timedelta(minutes=s.VOICE_DRAFT_TTL_MINUTES))
    db.add(draft)
    write_audit(db, actor=user, action="voice.transcribe", resource_type="voice_draft", resource_id=draft.id,
                request_id=request.state.request_id, detail={"provider": job.provider, "language": language,
                                                             "candidates": len(draft.parsed_fields)})
    return _draft_out(draft, job)


class ReparseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript: str = Field(min_length=1, max_length=5000)


def _own_draft(db: Session, user: User, draft_id: str) -> VoiceDraft:
    d = db.get(VoiceDraft, draft_id)
    if d is None or d.user_id != user.id:
        raise not_found("draft")
    if d.status == "draft" and d.expires_at < utcnow():
        d.status, d.transcript_enc = "expired", None
        db.commit()
    if d.status != "draft":
        raise AppError(410, "draft_closed", f"Draft is {d.status}")
    return d


@router.get("/drafts/{draft_id}", response_model=VoiceDraftOut)
def get_draft(draft_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _own_draft(db, user, draft_id)
    return _draft_out(d, db.get(VoiceJob, d.job_id))


@router.post("/drafts/{draft_id}/parse", response_model=VoiceDraftOut)
def reparse(draft_id: str, body: ReparseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Clinician edited the transcript: re-run the parser. Still a draft."""
    d = _own_draft(db, user, draft_id)
    d.transcript_enc, d.parsed_fields = encrypt(body.transcript), parse(body.transcript, d.subject_type)
    db.commit()
    return _draft_out(d, db.get(VoiceJob, d.job_id))


@router.post("/drafts/{draft_id}/confirm", status_code=201)
def confirm(draft_id: str, body: VoiceConfirmIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Only explicitly confirmed values are written. Backend re-validates schema, units, ranges and role."""
    if not body.reviewed_transcript:
        raise AppError(422, "review_required", "Clinician must confirm that the transcript and values were reviewed")
    d = _own_draft(db, user, draft_id)
    subject = _subject(db, user, d.subject_type, d.subject_id)
    allowed = PREG_FIELDS | set(LAB_FIELDS) if d.subject_type == "pregnancy" else NB_FIELDS
    bad = [f.field for f in body.fields if f.field not in allowed]
    if bad:
        raise AppError(422, "field_not_allowed", f"Fields not allowed for {d.subject_type}: {bad}")
    obs = {f.field: f.value for f in body.fields if f.field not in LAB_FIELDS}
    labs = {f.field: f.value for f in body.fields if f.field in LAB_FIELDS}
    when = body.observed_at or utcnow()
    try:
        validated = (MaternalObservationIn if d.subject_type == "pregnancy" else NewbornObservationIn)(**obs, observed_at=when)
        lab_models = [LabResultIn(test_code=LAB_FIELDS[k][0], value=float(v), unit=LAB_FIELDS[k][1], collected_at=when)
                      for k, v in labs.items()]
    except ValidationError as e:
        raise AppError(422, "validation_error", "Confirmed values failed validation",
                       [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()])
    record_id, ra = None, None
    if obs:
        payload = validated.model_dump(exclude_none=True)
        if d.subject_type == "pregnancy":
            rec, ra = save_maternal_observation(db, user, subject, payload, source="voice", voice_draft_id=d.id)
        else:
            rec, ra = save_newborn_observation(db, user, subject, payload, source="voice", voice_draft_id=d.id)
        record_id = rec.id
    for lm in lab_models:
        db.add(LabResult(pregnancy_id=subject.id, test_code=lm.test_code, value=lm.value, unit=lm.unit,
                         collected_at=lm.collected_at, facility_id=subject.facility_id, created_by=user.id))
    if lab_models:
        db.flush()
        ra = risk_engine.assess_pregnancy(db, subject, actor_id=user.id)
    d.status, d.confirmed_at, d.confirmed_record_id, d.transcript_enc = "confirmed", utcnow(), record_id, None
    write_audit(db, actor=user, action="voice.confirm", resource_type="voice_draft", resource_id=d.id,
                request_id=request.state.request_id, detail={"fields": sorted(obs) + sorted(labs)})
    return {"draft_id": d.id, "status": "confirmed", "record_id": record_id,
            "risk_level": ra.level if ra else None, "triggered_rules": [t["rule_id"] for t in (ra.triggered_rules if ra else [])]}


@router.delete("/drafts/{draft_id}", status_code=204)
def discard(draft_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(VoiceDraft, draft_id)
    if d is None or d.user_id != user.id:
        raise not_found("draft")
    d.status, d.transcript_enc, d.parsed_fields = "discarded", None, []
    write_audit(db, actor=user, action="voice.discard", resource_type="voice_draft", resource_id=d.id,
                request_id=request.state.request_id)


@router.get("/providers")
def providers_status(user: User = Depends(current_user)):
    require(user, "voice:use")
    s = get_settings()
    vl = VoiceLabSTTProvider(s)
    return {"default": s.VOICE_PROVIDER,
            "local": {"configured": bool(s.LOCAL_STT_BASE_URL), "dev_stub": s.LOCAL_STT_DEV_STUB and not s.is_production,
                      "processing_location": "local_server"},
            "voicelab": {"enabled": s.VOICELAB_ENABLED, "blocked_reasons": vl.gate(True),
                         "processing_location": "external", "requires_explicit_consent": True}}


def cleanup_expired_drafts(db: Session) -> int:
    """TTL job: expire drafts and wipe transcripts. Called by the worker/scheduler and on startup."""
    res = db.execute(update(VoiceDraft).where(VoiceDraft.status == "draft", VoiceDraft.expires_at < utcnow())
                     .values(status="expired", transcript_enc=None))
    db.commit()
    return res.rowcount or 0


_ = LocalSTTProvider  # re-export for tests

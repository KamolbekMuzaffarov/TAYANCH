from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import AppError

log = logging.getLogger("perinatal.voice")

SUPPORTED_LANGUAGES = {"uz", "uz-cyrl", "ru", "en"}
# Vocabulary prompts bias Whisper toward correct medical spelling and number formats (measured: Uzbek
# confidence 0.59 -> 0.88 on a synthetic dictation). They contain no patient data.
STT_PROMPTS = {
    "uz": ("Shifokor diktovkasi. Qon bosimi 120 ga 80. Sistolik bosim, diastolik bosim, puls, harorat 36,6, saturatsiya, "
           "homila yurak urishi, gemoglobin, qand, oqsil manfiy, oqsil bir plus, homiladorlik muddati 32 hafta."),
    "uz-cyrl": ("Шифокор диктовкаси. Қон босими 120 га 80. Систолик босим, диастолик босим, пульс, ҳарорат 36,6, "
                "сатурация, ҳомила юрак уриши, гемоглобин, қанд, оқсил, ҳомиладорлик муддати 32 ҳафта."),
    "ru": ("Диктовка врача. Давление 120 на 80. Пульс, температура 36,6, сатурация, ЧСС плода, гемоглобин, глюкоза, "
           "белок отрицательный, срок беременности 32 недели."),
    "en": "Clinician dictation. Blood pressure 120 over 80. Pulse, temperature 36.6, saturation, fetal heart rate, hemoglobin.",
}


@dataclass
class STTResult:
    text: str
    language: str
    provider: str
    duration_ms: int | None = None
    external_id: str | None = None
    confidence: float | None = None


class VoiceProvider(Protocol):
    name: str

    def transcribe(self, audio: bytes, mime: str, language: str, idempotency_key: str) -> STTResult: ...


class LocalSTTProvider:
    """Self-hosted STT (e.g. faster-whisper behind an OpenAI-compatible /v1/audio/transcriptions endpoint)
    on the project's own server. Audio never leaves the controlled environment."""

    name = "local"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.s = settings or get_settings()
        self.client = client

    def transcribe(self, audio: bytes, mime: str, language: str, idempotency_key: str) -> STTResult:
        if not self.s.LOCAL_STT_BASE_URL:
            if self.s.LOCAL_STT_DEV_STUB and not self.s.is_production:
                # Deterministic dev stub so the workflow can be exercised without a GPU STT server.
                # A synthetic dictation can be supplied as UTF-8 text in place of audio (mime text/plain).
                text = audio.decode("utf-8", errors="ignore") if mime.startswith("text/") else \
                    "sistolik bosim 120, diastolik 80, puls 82, harorat 36,7"
                return STTResult(text=text, language=language, provider="local-dev-stub", duration_ms=0, confidence=0.5)
            raise AppError(503, "stt_unavailable", "Local STT service is not configured")
        lang = "uz" if language.startswith("uz") else language
        c = self.client or httpx.Client(timeout=60)
        try:
            r = c.post(f"{self.s.LOCAL_STT_BASE_URL.rstrip('/')}/v1/audio/transcriptions",
                       files={"file": ("audio", audio, mime)},
                       data={"language": lang, "response_format": "json", "prompt": STT_PROMPTS.get(language, "")})
            if r.status_code in (413, 415, 422):
                raise AppError(r.status_code, "stt_rejected_audio", "Local STT rejected the audio (format, length or size)")
            r.raise_for_status()
            body = r.json()
        except httpx.HTTPError as e:
            raise AppError(503, "stt_unavailable", f"Local STT error: {type(e).__name__}")
        dur = body.get("duration")
        return STTResult(text=body.get("text", ""), language=language, provider="local",
                         duration_ms=int(dur * 1000) if dur is not None else None, confidence=body.get("confidence"))


class VoiceLabSTTProvider:
    """Optional external provider. Every call is blocked unless ALL gates pass:
    VOICELAB_ENABLED, VOICELAB_EXTERNAL_PROCESSING_APPROVED (legal/vendor review done), API key present,
    and an explicit external-processing consent supplied by the caller."""

    name = "voicelab"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.s = settings or get_settings()
        self.client = client

    def gate(self, consent_external: bool) -> list[str]:
        reasons = []
        if not self.s.VOICELAB_ENABLED:
            reasons.append("voicelab_disabled")
        if not self.s.VOICELAB_EXTERNAL_PROCESSING_APPROVED:
            reasons.append("vendor_legal_review_not_approved")
        if not self.s.VOICELAB_API_KEY:
            reasons.append("api_key_missing")
        if not consent_external:
            reasons.append("external_processing_consent_missing")
        return reasons

    # VoiceLab STT is asynchronous (docs.voicelab.uz/api/stt):
    #   POST /v1/stt (multipart: audio, language, include_speakers; Idempotency-Key = UUID) -> 202 {id, status}
    #   GET  /v1/stt/transcriptions/{id} -> {status: queued|processing|completed|failed, transcript, duration_ms, ...}
    #   DELETE /v1/stt/transcriptions/{id} -> 204 (transcription + retained source audio)
    POLL_DEADLINE_S = 120.0
    _ERRORS = {401: "invalid_api_key", 402: "insufficient_credits", 403: "insufficient_scope", 413: "audio_too_large",
               415: "unsupported_audio_format", 422: "invalid_audio", 429: "rate_limited"}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.VOICELAB_API_KEY}"}

    def _fail(self, r: httpx.Response) -> AppError:
        try:
            code = (r.json().get("error") or {}).get("code") or self._ERRORS.get(r.status_code, "voicelab_error")
        except ValueError:
            code = self._ERRORS.get(r.status_code, "voicelab_error")
        status = r.status_code if r.status_code in (413, 415, 422) else 503
        return AppError(status, "stt_provider_error", f"VoiceLab: {code}")

    def transcribe(self, audio: bytes, mime: str, language: str, idempotency_key: str, consent_external: bool = False,
                   sleep=time.sleep) -> STTResult:
        reasons = self.gate(consent_external)
        if reasons:
            raise AppError(403, "external_stt_blocked", "External STT blocked: " + ", ".join(reasons))
        lang = "uz" if language.startswith("uz") else language
        # VoiceLab requires a UUID; derive a stable one so retries of the same submission are de-duplicated.
        idem = str(uuid.uuid5(uuid.NAMESPACE_URL, f"perinatal-voice:{idempotency_key}"))
        base = self.s.VOICELAB_BASE_URL.rstrip("/")
        c = self.client or httpx.Client(timeout=60)
        try:
            r = c.post(f"{base}/v1/stt", headers={**self._headers(), "Idempotency-Key": idem},
                       files={"audio": ("audio", audio, mime)}, data={"language": lang, "include_speakers": "false"})
            if r.status_code not in (200, 201, 202):
                raise self._fail(r)
            job = r.json()
            job_id = str(job.get("id") or "")
            if not job_id:
                raise AppError(503, "stt_provider_error", "VoiceLab: missing job id")
            deadline = time.monotonic() + self.POLL_DEADLINE_S
            body = job
            while body.get("status") not in ("completed", "failed"):
                if time.monotonic() > deadline:
                    self.delete_remote(job_id)
                    raise AppError(504, "stt_timeout", "VoiceLab transcription did not finish in time")
                retry_after = r.headers.get("Retry-After")
                sleep(max(0.5, min(5.0, float(retry_after))) if retry_after and retry_after.replace(".", "", 1).isdigit() else 1.0)
                r = c.get(f"{base}/v1/stt/transcriptions/{job_id}", headers=self._headers())
                if r.status_code != 200:
                    raise self._fail(r)
                body = r.json()
        except httpx.HTTPError as e:
            raise AppError(503, "stt_unavailable", f"VoiceLab error: {type(e).__name__}")
        if body.get("status") == "failed":
            self.delete_remote(job_id)
            raise AppError(422, "stt_failed", "VoiceLab could not transcribe the audio")
        dur = body.get("duration_ms")
        # VoiceLab returns no confidence score -> None (values still need clinician confirmation).
        return STTResult(text=body.get("transcript") or "", language=language, provider="voicelab",
                         duration_ms=int(dur) if dur is not None else None, external_id=job_id, confidence=None)

    def delete_remote(self, external_id: str) -> bool:
        """Request deletion of stored transcript/audio at the vendor (does not prove backup deletion)."""
        if self.gate(True)[:1] == ["voicelab_disabled"] or not external_id:
            return False
        c = self.client or httpx.Client(timeout=30)
        try:
            r = c.delete(f"{self.s.VOICELAB_BASE_URL.rstrip('/')}/v1/stt/transcriptions/{external_id}",
                         headers={"Authorization": f"Bearer {self.s.VOICELAB_API_KEY}"})
            return r.status_code in (200, 202, 204, 404)  # 404 = already gone
        except httpx.HTTPError:
            return False


def get_provider(name: str | None = None) -> LocalSTTProvider | VoiceLabSTTProvider:
    s = get_settings()
    name = name or s.VOICE_PROVIDER
    if name == "voicelab":
        return VoiceLabSTTProvider(s)
    return LocalSTTProvider(s)

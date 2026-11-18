"""Self-hosted speech-to-text service for Perinatal AI (faster-whisper / CTranslate2).

OpenAI-compatible subset:  POST /v1/audio/transcriptions  (multipart: file, language, response_format)
                            GET  /health

Privacy: binds to 127.0.0.1 by default, audio is decoded in memory and never written to disk,
transcripts are never logged (only duration / timing / language). No speaker diarisation,
no voice biometrics.

Env:
  STT_MODEL        model name or local path (default: large-v3-turbo on GPU, small on CPU)
  STT_DEVICE       auto | cuda | cpu
  STT_COMPUTE_TYPE default int8_float16 (cuda) / int8 (cpu)
  STT_MODEL_DIR    where models are cached
  STT_MAX_BYTES    upload limit (default 15 MB)
Run:  python -m uvicorn stt_server.server:app --host 127.0.0.1 --port 9001
"""
from __future__ import annotations

import io
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile


def _add_cuda_dll_dirs() -> None:
    """On Windows, make pip-installed NVIDIA cuBLAS/cuDNN DLLs visible to CTranslate2."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # type: ignore
    except ImportError:
        return
    for base in getattr(nvidia, "__path__", []):
        for bin_dir in Path(base).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dll_dirs()

from faster_whisper import WhisperModel, decode_audio  # noqa: E402

log = logging.getLogger("perinatal.stt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

LANG_MAP = {"uz": "uz", "uz-cyrl": "uz", "ru": "ru", "en": "en"}
MAX_BYTES = int(os.environ.get("STT_MAX_BYTES", 15 * 1024 * 1024))
_model: WhisperModel | None = None
_model_info: dict = {}
_lock = threading.Lock()


def _load() -> WhisperModel:
    global _model
    if _model is not None:
        return _model
    import ctranslate2
    device = os.environ.get("STT_DEVICE", "auto")
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    name = os.environ.get("STT_MODEL") or ("large-v3-turbo" if device == "cuda" else "small")
    compute = os.environ.get("STT_COMPUTE_TYPE") or ("int8_float16" if device == "cuda" else "int8")
    root = os.environ.get("STT_MODEL_DIR") or str(Path.home() / "perinatal-devtools" / "stt_models")
    t0 = time.time()
    try:
        _model = WhisperModel(name, device=device, compute_type=compute, download_root=root)
    except Exception as e:  # e.g. missing CUDA libraries -> fall back to CPU
        if device != "cuda":
            raise
        log.warning("CUDA load failed (%s); falling back to CPU", type(e).__name__)
        device, compute, name = "cpu", "int8", os.environ.get("STT_MODEL") or "small"
        _model = WhisperModel(name, device=device, compute_type=compute, download_root=root)
    _model_info.update(model=name, device=device, compute_type=compute, load_seconds=round(time.time() - t0, 1))
    log.info("model loaded %s", _model_info)
    return _model


app = FastAPI(title="Perinatal AI local STT", version="0.1.0", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    _load()


@app.get("/health")
def health():
    return {"status": "ok" if _model is not None else "loading", **_model_info}


@app.post("/v1/audio/transcriptions")
async def transcribe(file: UploadFile = File(...), language: str = Form("uz"), response_format: str = Form("json"),
                     prompt: str | None = Form(None)):
    lang = LANG_MAP.get(language)
    if lang is None:
        raise HTTPException(422, "unsupported language")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "audio too large")
    t0 = time.time()
    try:
        audio = decode_audio(io.BytesIO(data), sampling_rate=16000)
    except Exception:
        raise HTTPException(415, "could not decode audio")
    finally:
        del data
    duration = len(audio) / 16000.0
    if duration < 0.3:
        raise HTTPException(422, "audio too short")
    model = _load()
    with _lock:  # one inference at a time on a single GPU/CPU model
        segments, info = model.transcribe(
            audio, language=lang, task="transcribe", beam_size=5, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=prompt or None)
        segs = list(segments)
    del audio
    text = " ".join(s.text.strip() for s in segs).strip()
    if segs:
        tot = sum(s.end - s.start for s in segs) or 1.0
        avg_logprob = sum(s.avg_logprob * (s.end - s.start) for s in segs) / tot
        no_speech = max(s.no_speech_prob for s in segs)
    else:
        avg_logprob, no_speech = -5.0, 1.0
    confidence = round(max(0.0, min(1.0, math.exp(avg_logprob))) * (1 - no_speech), 3)
    elapsed = time.time() - t0
    log.info("transcribed lang=%s audio=%.1fs took=%.2fs segments=%d", lang, duration, elapsed, len(segs))
    if response_format == "text":
        return text
    return {"text": text, "language": lang, "duration": round(duration, 2), "confidence": confidence,
            "processing_seconds": round(elapsed, 2), "model": _model_info.get("model")}

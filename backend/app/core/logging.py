from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"\b\d{14}\b"), "[PINFL]"),
    (re.compile(r"\+?998[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}"), "[PHONE]"),
    (re.compile(r"\b[A-Z]{2}\s?\d{7}\b"), "[PASSPORT]"),
    (re.compile(r"(?i)bearer\s+[a-z0-9\-_.]+"), "Bearer [TOKEN]"),
    (re.compile(r"(?i)(sk-ant-[a-z0-9\-_]+)"), "[API_KEY]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
]


def redact(text: str) -> str:
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = redact(msg)
        record.args = None
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.setLevel(level)
    # uvicorn access log prints query strings; we log our own sanitized access line instead.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)

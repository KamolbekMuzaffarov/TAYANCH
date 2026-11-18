from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

import httpx

from app.core.config import get_settings

log = logging.getLogger("perinatal.delivery")


class CodeProvider(Protocol):
    channel: str

    def send(self, to: str, code: str) -> None: ...


def _text(code: str) -> str:
    return f"Perinatal AI: tasdiqlash kodi {code}. Kod 5 daqiqa amal qiladi. Uni hech kimga bermang."


class SmtpProvider:
    channel = "email"

    def send(self, to: str, code: str) -> None:
        s = get_settings()
        msg = EmailMessage()
        msg["Subject"] = "Perinatal AI — tasdiqlash kodi"
        msg["From"] = s.SMTP_FROM or s.SMTP_USER
        msg["To"] = to
        msg.set_content(_text(code))
        ctx = ssl.create_default_context()
        if s.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(s.SMTP_HOST, s.SMTP_PORT, timeout=10, context=ctx) as srv:
                if s.SMTP_USER:
                    srv.login(s.SMTP_USER, s.SMTP_PASSWORD)
                srv.send_message(msg)
            return
        with smtplib.SMTP(s.SMTP_HOST, s.SMTP_PORT, timeout=10) as srv:
            srv.starttls(context=ctx)  # never send credentials or codes in clear text
            if s.SMTP_USER:
                srv.login(s.SMTP_USER, s.SMTP_PASSWORD)
            srv.send_message(msg)


class HttpSmsProvider:
    """Stub adapter: POST {to, from, text} with a bearer key. Replace with the contracted operator's API."""
    channel = "phone"

    def send(self, to: str, code: str) -> None:
        s = get_settings()
        r = httpx.post(s.SMS_PROVIDER_URL, json={"to": to, "from": s.SMS_FROM, "text": _text(code)},
                       headers={"Authorization": f"Bearer {s.SMS_API_KEY}"}, timeout=10)
        r.raise_for_status()


def provider_for(channel: str) -> CodeProvider | None:
    s = get_settings()
    if channel == "email":
        return SmtpProvider() if s.SMTP_HOST and (s.SMTP_FROM or s.SMTP_USER) else None
    if channel == "phone":
        return HttpSmsProvider() if s.SMS_PROVIDER_ENABLED and s.SMS_PROVIDER_URL.startswith("https://") else None
    return None


def deliver(provider: CodeProvider, to: str, code: str) -> bool:
    """Runs in a background task so the response time does not reveal whether a message was sent."""
    try:
        provider.send(to, code)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("one-time code delivery failed channel=%s error=%s", provider.channel, type(exc).__name__)
        return False

from __future__ import annotations

import logging
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger("perinatal.notify")


class Notifier(Protocol):
    def send(self, user_id: str, channel: str, text: str) -> bool: ...


class InAppOnlyNotifier:
    """Default: alerts are delivered through the authenticated in-app alert center only."""

    sent: list[tuple[str, str, str]] = []

    def send(self, user_id: str, channel: str, text: str) -> bool:
        self.sent.append((user_id, channel, text))
        log.info("notification queued channel=%s", channel)
        return True


def alert_message(alert_id: str, severity: str) -> str:
    s = get_settings()
    pr = "YUQORI USTUVORLIK" if severity == "red" else "ustuvorlik: o‘rta"
    return f"Perinatal AI: yangi klinik ogohlantirish ({pr}). Ko‘rish uchun tizimga kiring: {s.APP_BASE_URL}/alerts/{alert_id}"


notifier: Notifier = InAppOnlyNotifier()

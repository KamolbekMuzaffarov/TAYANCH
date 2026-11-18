from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import datetime, timezone


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventBus:
    def __init__(self, keep: int = 300):
        self._lock = threading.Lock()
        self._next_id = 1000
        self._recent: deque[dict] = deque(maxlen=keep)
        self._subs: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}

    def publish(self, type: str, text: str, *, zone: str | None = None, district_code: str | None = None,
                district_name: str | None = None, region_soato: str | None = None, at: datetime | None = None,
                live: bool = True) -> dict:
        with self._lock:
            self._next_id += 1
            ev = {"id": self._next_id, "type": type, "at": iso(at or datetime.now(timezone.utc)), "zone": zone,
                  "district_code": district_code, "district_name": district_name, "region_soato": region_soato,
                  "text": text}
            self._recent.append(ev)
            subs = list(self._subs.items()) if live else []
        for q, loop in subs:
            try:
                loop.call_soon_threadsafe(self._offer, q, ev)
            except RuntimeError:  # loop closed
                self.unsubscribe(q)
        return ev

    @staticmethod
    def _offer(q: asyncio.Queue, ev: dict) -> None:
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            pass  # a stalled client loses events instead of blocking everyone

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subs[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.pop(q, None)

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            items = list(self._recent)
        return items[-limit:][::-1]  # newest first

    def is_empty(self) -> bool:
        with self._lock:
            return not self._recent

    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


bus = EventBus()

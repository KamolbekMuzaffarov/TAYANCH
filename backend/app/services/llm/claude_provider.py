"""Claude adapter. INTERNAL: only `privacy_gateway` may import this module (enforced by tests).

Receives an already-vetted, patient-free prompt. Never logs prompt or response bodies.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anthropic

from app.core.config import get_settings


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int
    status: str  # ok|refused|error


class ClaudeProvider:
    name = "anthropic"

    def __init__(self, client: Any | None = None):
        s = get_settings()
        self.model = s.CLAUDE_MODEL
        self._client = client
        self._timeout = s.CLAUDE_TIMEOUT_SECONDS
        self._api_key = s.CLAUDE_API_KEY

    @property
    def client(self):
        if self._client is None:
            # API key comes only from server-side secret management (env), never from the client.
            self._client = anthropic.Anthropic(api_key=self._api_key or None, timeout=float(self._timeout), max_retries=2)
        return self._client

    def complete(self, system: str, user_text: str, max_tokens: int = 2000, timeout: float | None = None,
                 max_retries: int | None = None) -> LLMResult:
        t0 = time.monotonic()
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system,
                                      messages=[{"role": "user", "content": user_text}],
                                      output_config={"effort": "low"})
        if "haiku" not in self.model:
            # Server-side refusal fallback exists for the Opus/Sonnet/Fable tiers only (not for Haiku).
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        client = self.client
        if timeout is not None and hasattr(client, "with_options"):
            client = client.with_options(timeout=float(timeout), max_retries=max_retries if max_retries is not None else 0)
        try:
            resp = client.beta.messages.create(**kwargs)
        except anthropic.RateLimitError:
            return LLMResult("", self.model, None, None, int((time.monotonic() - t0) * 1000), "rate_limited")
        except anthropic.APIStatusError as e:
            return LLMResult("", self.model, None, None, int((time.monotonic() - t0) * 1000), f"api_error_{e.status_code}")
        except anthropic.APITimeoutError:
            return LLMResult("", self.model, None, None, int((time.monotonic() - t0) * 1000), "timeout")
        except anthropic.APIConnectionError:
            return LLMResult("", self.model, None, None, int((time.monotonic() - t0) * 1000), "unavailable")
        except (anthropic.AnthropicError, TypeError):  # e.g. no credentials configured on this server
            return LLMResult("", self.model, None, None, int((time.monotonic() - t0) * 1000), "not_configured")
        dur = int((time.monotonic() - t0) * 1000)
        usage = getattr(resp, "usage", None)
        if getattr(resp, "stop_reason", None) == "refusal":
            return LLMResult("", getattr(resp, "model", self.model), getattr(usage, "input_tokens", None),
                             getattr(usage, "output_tokens", None), dur, "refused")
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return LLMResult(text, getattr(resp, "model", self.model), getattr(usage, "input_tokens", None),
                         getattr(usage, "output_tokens", None), dur, "ok")

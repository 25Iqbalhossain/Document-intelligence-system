"""LLM provider abstraction. Extraction logic depends only on `LLMProvider`, never on a vendor SDK."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings, get_settings


class LLMTransientError(Exception):
    """Timeouts, rate limits, 5xx: worth retrying later with backoff."""


class LLMPermanentError(Exception):
    """Authentication, bad request, misconfiguration: retrying will not help."""


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)


Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": "..."}


class LLMProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def complete(self, messages: list[Message]) -> LLMResponse: ...


class OpenAICompatibleProvider(LLMProvider):
    """Any server speaking the /chat/completions protocol (hosted or self-hosted)."""

    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, *, json_mode: bool = True, timeout_s: int = 120,
                 client: httpx.Client | None = None) -> None:
        if not base_url or not model:
            raise LLMPermanentError("LLM base_url and model must be configured")
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.json_mode = json_mode
        self._client = client or httpx.Client(timeout=timeout_s)

    def complete(self, messages: list[Message]) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = self._client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMTransientError(f"LLM request failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise LLMTransientError(f"LLM HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMPermanentError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMTransientError(f"Unexpected LLM response shape: {exc}") from exc
        return LLMResponse(text=text or "", model=str(data.get("model") or self.model), usage=data.get("usage") or {})


class MockProvider(LLMProvider):
    """Deterministic provider for tests and keyless runs.

    Returns a registered response for a known document, otherwise an empty extraction ({"invoices": []}),
    so keyless runs send everything to human review rather than fabricating data. It is NOT an extractor and
    is never used to produce accuracy numbers.
    """

    name = "mock"

    def __init__(self, responses: dict[str, str] | None = None, model: str = "mock-1") -> None:
        self.responses = responses or {}
        self.model = model
        self.calls: list[list[Message]] = []

    @staticmethod
    def key_for(document_text: str) -> str:
        return hashlib.sha256(document_text.encode()).hexdigest()

    def complete(self, messages: list[Message]) -> LLMResponse:
        self.calls.append(messages)
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = self.responses.get(self.key_for(user), json.dumps({"invoices": []}))
        return LLMResponse(text=text, model=self.model)


def get_provider(settings: Settings | None = None, *, fallback: bool = False) -> LLMProvider | None:
    s = settings or get_settings()
    if s.llm_provider == "mock":
        return None if fallback else MockProvider()
    model = s.llm_fallback_model if fallback else s.llm_model
    if fallback and not model:
        return None
    return OpenAICompatibleProvider(s.llm_base_url, s.llm_api_key.get_secret_value(), model,
                                    json_mode=s.llm_json_mode, timeout_s=s.llm_timeout_s)

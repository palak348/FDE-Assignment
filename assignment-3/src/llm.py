"""Provider-agnostic LLM client (OpenAI-compatible chat completions).

Works with any OpenAI-compatible endpoint or gateway (Gemini's OpenAI endpoint,
LiteLLM, OpenRouter, Portkey, OpenAI itself ...), configured only via env vars:

    LLM_BASE_URL   default: Gemini OpenAI-compatible endpoint
    LLM_API_KEY    falls back to GEMINI_API_KEY / OPENAI_API_KEY
    LLM_MODEL      default: gemini-3.1-flash-lite
    LLM_RPM        client-side requests-per-minute cap (free tiers are low), default 8
    LLM_MAX_RETRIES  retries on 429/5xx/network errors, default 5
    LLM_CACHE_DIR  optional on-disk response cache (reproducible offline eval reruns)
    LLM_FALLBACK_MODELS  optional comma-separated models to switch to when the current
                   model's *daily* quota is exhausted (free tiers have per-model daily caps)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.1-flash-lite"


class LLMError(RuntimeError):
    """The model could not produce a response (after retries)."""


class ModelSwitchedError(LLMError):
    """The daily quota of the current model ran out and the client switched to a fallback.
    The caller should restart the whole run so one request never mixes models."""


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]
    message: dict  # assistant message to append to the conversation verbatim
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False


class ChatModel(Protocol):
    model: str

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 json_mode: bool = False) -> LLMResponse: ...


class _RateLimiter:
    def __init__(self, rpm: int) -> None:
        self.rpm = max(rpm, 1)
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.rpm:
                time.sleep(max(0.0, 60 - (now - self.calls[0]) + 0.5))
            self.calls.append(time.monotonic())


_LIMITERS: dict[int, _RateLimiter] = {}


def _parse_tool_args(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


class OpenAICompatibleLLM:
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None) -> None:
        from openai import OpenAI

        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_MODEL
        self.fallback_models = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL
        key = api_key or os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise LLMError("No LLM API key configured. Set LLM_API_KEY (or GEMINI_API_KEY) in .env")
        self.client = OpenAI(api_key=key, base_url=self.base_url, timeout=float(os.getenv("LLM_TIMEOUT", "90")),
                             max_retries=0)
        self.max_retries = int(os.getenv("LLM_MAX_RETRIES", "5"))
        rpm = int(os.getenv("LLM_RPM", "8"))
        self.limiter = _LIMITERS.setdefault(rpm, _RateLimiter(rpm))
        cache_dir = os.getenv("LLM_CACHE_DIR")
        self.cache_dir = Path(cache_dir) if cache_dir else None

    # ------------------------------------------------------------------ cache
    def _cache_path(self, payload: dict) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 json_mode: bool = False) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        cache_path = self._cache_path(kwargs)
        if cache_path and cache_path.is_file():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return LLMResponse(data["content"], [ToolCallRequest(**t) for t in data["tool_calls"]], data["message"],
                               data["prompt_tokens"], data["completion_tokens"], 0.0, cached=True)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            start = time.perf_counter()
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as exc:  # classify below
                last_error = exc
                status = getattr(exc, "status_code", None)
                if status == 429 and _is_daily_quota(exc):
                    # Retrying cannot help today: switch model if a fallback is configured.
                    if self.fallback_models:
                        previous, self.model = self.model, self.fallback_models.pop(0)
                        raise ModelSwitchedError(f"Daily quota exhausted for {previous}; switched to {self.model}")
                    break
                retryable = status in (408, 409, 429, 500, 502, 503, 504) or exc.__class__.__name__ in {
                    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"}
                if not retryable or attempt == self.max_retries:
                    break
                time.sleep(self._backoff(exc, attempt))
                continue
            latency = (time.perf_counter() - start) * 1000
            if not resp.choices:
                last_error = LLMError("Empty response (no choices)")
                time.sleep(self._backoff(None, attempt))
                continue
            msg = resp.choices[0].message
            tool_calls = [ToolCallRequest(t.id, t.function.name, _parse_tool_args(t.function.arguments))
                          for t in (msg.tool_calls or [])]
            message = msg.model_dump(exclude_none=True)
            message.setdefault("role", "assistant")
            if message.get("content") is None:
                message["content"] = ""
            usage = resp.usage
            out = LLMResponse(msg.content, tool_calls, message,
                              getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0,
                              latency)
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps({
                    "content": out.content, "tool_calls": [t.__dict__ for t in tool_calls], "message": message,
                    "prompt_tokens": out.prompt_tokens, "completion_tokens": out.completion_tokens}), encoding="utf-8")
            return out
        raise LLMError(f"LLM call failed after {attempt + 1} attempt(s): {last_error.__class__.__name__}: "
                       f"{str(last_error)[:300]}")

    @staticmethod
    def _backoff(exc: Exception | None, attempt: int) -> float:
        text = str(exc or "")
        match = re.search(r"retry(?:_delay| in|Delay)?\D{0,20}(\d+(?:\.\d+)?)\s*s", text, re.I)
        if match:
            return min(float(match.group(1)) + 1.0, 65.0)
        return min(2.0 * (2 ** attempt), 40.0)


def _is_daily_quota(exc: Exception) -> bool:
    text = str(exc)
    if "PerDay" in text:
        return True
    match = re.search(r"retryDelay'?:\s*'(\d+)s", text)
    return bool(match and int(match.group(1)) > 300)


def parse_json_object(text: str | None) -> dict | None:
    """Extract a JSON object from model text (tolerates ```json fences and preamble)."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    for candidate in (cleaned, text):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


@dataclass
class ScriptedLLM:
    """Offline fake model for tests: returns pre-scripted responses in order."""
    responses: list[LLMResponse] = field(default_factory=list)
    model: str = "scripted-fake"
    seen: list[list[dict]] = field(default_factory=list)

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 json_mode: bool = False) -> LLMResponse:
        self.seen.append(messages)
        if not self.responses:
            raise LLMError("ScriptedLLM has no more responses")
        return self.responses.pop(0)


_DEFAULT: OpenAICompatibleLLM | None = None


def get_default_llm() -> ChatModel:
    """Process-wide client so rate limiting and daily-quota fallback state are shared."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = OpenAICompatibleLLM()
    return _DEFAULT

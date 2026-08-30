"""The LLM provider router: Groq -> Gemini -> OpenRouter -> Ollama.

Real fallback, not a config comment. Each provider is tried in order and a
failure moves to the next one, because on demo day the failure mode that
actually happens is a provider being rate-limited mid-answer.

Two budget facts drive the design:

* **OpenRouter's free tier is 50 requests/day**, verified. It is therefore the
  *third* choice and gets a daily budget counter, so a judge hammering the chat
  cannot burn the quota that protects the Q&A.
* **Groq is fast and generous**, so it is the primary and carries the demo.

Gemini's key is the new ``AQ.`` format, which must be sent as the
``x-goog-api-key`` header rather than a ``?key=`` query parameter — the older
form returns 400 and looks like an invalid key.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Provider, utcnow
from orca.sources.base import get_client

log = logging.getLogger(__name__)


@dataclass(slots=True)
class LlmSpec:
    """One provider option."""

    name: str
    model: str
    base_url: str
    #: Settings attribute holding the key, e.g. ``groq_api_key``.
    key_attr: str | None
    #: How the key is presented. Gemini is the odd one out.
    auth: str = "bearer"
    #: Requests per day we allow ourselves. ``None`` means unmetered.
    daily_budget: int | None = None
    supports_tools: bool = True
    #: For reasoning models, how much of the token budget to spend thinking.
    #:
    #: This is not a quality dial, it is a correctness one. `openai/gpt-oss-120b`
    #: emits its chain of thought into a separate `reasoning` field and charges it
    #: against `max_tokens`. On a planner prompt carrying the full tool catalogue
    #: it spent the entire 700-token budget reasoning and returned an EMPTY
    #: `content` — which surfaced as "provider returned an empty completion" and
    #: looked like an outage. At "low" the same call reasons in 92 characters and
    #: returns 564 of answer.
    reasoning_effort: str | None = None
    notes: str = ""


#: Order matters — this IS the fallback chain.
PROVIDERS: tuple[LlmSpec, ...] = (
    LlmSpec(
        name="groq",
        model="openai/gpt-oss-120b",
        base_url="https://api.groq.com/openai/v1/chat/completions",
        key_attr="groq_api_key",
        reasoning_effort="low",
        notes=(
            "Primary. Verified available, fast, tool-calling. A reasoning model, so "
            "`reasoning_effort` is set — see the field's note."
        ),
    ),
    LlmSpec(
        name="gemini",
        # `gemini-2.5-flash` returns 404 for keys issued after its retirement to
        # new users: "no longer available to new users". `gemini-flash-latest` is
        # the tracking alias but answered 503 under load during testing, so the
        # concrete current model is pinned instead.
        model="gemini-3-flash-preview",
        base_url="https://generativelanguage.googleapis.com/v1beta/models",
        key_attr="google_api_key",
        auth="x-goog-api-key",
        notes="1M context. New AQ.-format key needs the header, not ?key=.",
    ),
    LlmSpec(
        name="openrouter",
        # The previous free Llama slug now 404s with "unavailable for free".
        # Verified working on this key at the time of writing; free slugs on
        # OpenRouter come and go, so /agent/providers reports which one answered.
        model="nvidia/nemotron-3-super-120b-a12b:free",
        base_url="https://openrouter.ai/api/v1/chat/completions",
        key_attr="openrouter_api_key",
        daily_budget=45,  # free tier is 50/day; leave headroom for Q&A
        notes="Third choice, metered. Free tier is 50 req/day.",
    ),
    LlmSpec(
        name="ollama",
        model="llama3.1:8b",
        base_url="http://127.0.0.1:11434/v1/chat/completions",
        key_attr=None,
        auth="none",
        notes="Offline last resort. Only used if a local Ollama is running.",
    ),
)


@dataclass
class _Budget:
    """A per-provider daily counter, so a metered free tier is actually metered."""

    day: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d"))
    used: int = 0

    def take(self, limit: int) -> bool:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.used = 0
        if self.used >= limit:
            return False
        self.used += 1
        return True


_budgets: dict[str, _Budget] = {}


class LlmUnavailable(RuntimeError):
    """Every provider in the chain failed. Carries what each one said."""

    def __init__(self, attempts: list[tuple[str, str]]) -> None:
        self.attempts = attempts
        detail = "; ".join(f"{name}: {error}" for name, error in attempts)
        super().__init__(f"no LLM provider could answer ({detail})")


@dataclass(slots=True)
class LlmReply:
    text: str
    provider: str
    model: str
    latency_ms: float
    tokens_in: int | None = None
    tokens_out: int | None = None
    #: Providers we tried and why they failed, for the trace panel.
    fallbacks: list[tuple[str, str]] = field(default_factory=list)


def available_providers() -> list[LlmSpec]:
    """Providers whose credential is present. Ollama is always listed, since we
    cannot know whether it is running without asking it."""
    settings = get_settings()
    out = []
    for spec in PROVIDERS:
        if spec.key_attr is None or getattr(settings, spec.key_attr, None) is not None:
            out.append(spec)
    return out


def _headers(spec: LlmSpec) -> dict[str, str]:
    settings = get_settings()
    if spec.key_attr is None:
        return {}
    secret = getattr(settings, spec.key_attr, None)
    if secret is None:
        return {}
    key = secret.get_secret_value()
    if spec.auth == "x-goog-api-key":
        return {"x-goog-api-key": key}
    if spec.auth == "none":
        return {}
    return {"Authorization": f"Bearer {key}"}


def _diagnose_empty(spec: LlmSpec, payload: dict[str, Any]) -> str:
    """Explain an empty completion instead of just reporting one."""
    try:
        choice = (payload.get("choices") or [{}])[0]
        finish = str(choice.get("finish_reason") or "unknown")
        message = choice.get("message") or {}
        reasoning = str(message.get("reasoning") or "")
        usage = payload.get("usage") or {}
        completion_tokens = usage.get("completion_tokens")
    except (AttributeError, IndexError, TypeError):
        return "provider returned an empty completion (and an unparseable response)"

    if reasoning and finish == "length":
        return (
            f"{spec.name} spent its whole {completion_tokens}-token budget on reasoning and "
            "returned no answer. Raise max_tokens or lower reasoning_effort — this is a budget "
            "problem, not an outage."
        )
    if finish == "length":
        return (
            f"{spec.name} hit the token limit before producing any text "
            f"(completion_tokens={completion_tokens})."
        )
    if finish in {"content_filter", "safety"}:
        return f"{spec.name} refused the request (finish_reason={finish})."
    return f"{spec.name} returned an empty completion (finish_reason={finish})."


def _openai_body(spec: LlmSpec, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": spec.model,
        "messages": messages,
        "temperature": kw.get("temperature", 0.2),
        "max_tokens": kw.get("max_tokens", 1400),
        "stream": kw.get("stream", False),
    }
    if spec.reasoning_effort:
        body["reasoning_effort"] = spec.reasoning_effort
    return body


def _gemini_body(messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    """Gemini's schema differs enough to need its own shape: a separate
    ``systemInstruction`` and ``parts`` rather than ``content``."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
        if m["role"] != "system"
    ]
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": kw.get("temperature", 0.2),
            "maxOutputTokens": kw.get("max_tokens", 1400),
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    return body


async def complete(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1400,
    prefer: str | None = None,
) -> LlmReply:
    """Ask the chain until one provider answers.

    ``prefer`` pins a provider by name for testing and for the demo's
    "which model answered this?" control.
    """
    settings = get_settings()
    specs = available_providers()
    if prefer:
        specs = sorted(specs, key=lambda s: s.name != prefer)
    if not specs:
        raise LlmUnavailable([("none", "no provider credential is configured")])

    attempts: list[tuple[str, str]] = []
    client = await get_client()

    for spec in specs:
        source = f"llm.{spec.name}"
        registry.declare(source, provider=Provider.ORCA, variables=["completion"])

        if spec.daily_budget is not None:
            budget = _budgets.setdefault(spec.name, _Budget())
            limit = (
                settings.openrouter_daily_budget if spec.name == "openrouter" else spec.daily_budget
            )
            if not budget.take(limit):
                attempts.append((spec.name, f"daily budget of {limit} requests exhausted"))
                continue

        started = time.perf_counter()
        try:
            if spec.auth == "x-goog-api-key":
                url = f"{spec.base_url}/{spec.model}:generateContent"
                body = _gemini_body(messages, temperature=temperature, max_tokens=max_tokens)
            else:
                url = spec.base_url
                body = _openai_body(spec, messages, temperature=temperature, max_tokens=max_tokens)

            response = await client.post(
                url,
                headers={**_headers(spec), "Content-Type": "application/json"},
                json=body,
                timeout=45.0,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:180]}")

            payload = response.json()
            text, tokens_in, tokens_out = _extract(spec, payload)
            if not text.strip():
                # Say WHY it is empty. A reasoning model that spent the whole
                # budget thinking, and a provider that is actually down, both
                # produce no text — and they need completely different fixes.
                # The first version reported them identically and sent us looking
                # for an outage that was not there.
                raise RuntimeError(_diagnose_empty(spec, payload))

            latency_ms = (time.perf_counter() - started) * 1000
            registry.record_success(source, latency_ms=latency_ms)
            if attempts:
                log.info(
                    "LLM fell through to %s after %s",
                    spec.name,
                    ", ".join(f"{n} ({e[:60]})" for n, e in attempts),
                )
            return LlmReply(
                text=text,
                provider=spec.name,
                model=spec.model,
                latency_ms=round(latency_ms, 1),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                fallbacks=attempts,
            )

        except Exception as exc:  # noqa: BLE001 — any failure means try the next provider
            error = f"{type(exc).__name__}: {exc}"
            registry.record_failure(source, error)
            attempts.append((spec.name, error))
            log.warning("LLM provider %s failed: %s", spec.name, error[:200])

    raise LlmUnavailable(attempts)


def _extract(spec: LlmSpec, payload: dict[str, Any]) -> tuple[str, int | None, int | None]:
    if spec.auth == "x-goog-api-key":
        candidates = payload.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"no candidates in response: {json.dumps(payload)[:180]}")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        usage = payload.get("usageMetadata") or {}
        return text, usage.get("promptTokenCount"), usage.get("candidatesTokenCount")

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"no choices in response: {json.dumps(payload)[:180]}")
    text = choices[0].get("message", {}).get("content") or ""
    usage = payload.get("usage") or {}
    return text, usage.get("prompt_tokens"), usage.get("completion_tokens")


async def stream(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1400,
    prefer: str | None = None,
) -> AsyncIterator[str]:
    """Token stream from the first provider that will stream.

    Streaming matters for a specific reason: an agent trace that arrives in one
    lump at the end looks exactly like a fake. Providers that cannot stream fall
    back to a single chunk, which is honest — the UI shows one token event rather
    than pretending to type.
    """
    settings = get_settings()
    specs = available_providers()
    if prefer:
        specs = sorted(specs, key=lambda s: s.name != prefer)

    attempts: list[tuple[str, str]] = []
    client = await get_client()

    for spec in specs:
        # Gemini's streaming endpoint uses a different protocol; for the stream
        # path we use the OpenAI-compatible providers and let Gemini serve the
        # non-streaming fallback rather than maintaining two SSE parsers.
        if spec.auth == "x-goog-api-key":
            continue
        if spec.daily_budget is not None:
            limit = (
                settings.openrouter_daily_budget if spec.name == "openrouter" else spec.daily_budget
            )
            if not _budgets.setdefault(spec.name, _Budget()).take(limit):
                attempts.append((spec.name, "daily budget exhausted"))
                continue

        source = f"llm.{spec.name}"
        started = time.perf_counter()
        try:
            body = _openai_body(
                spec, messages, temperature=temperature, max_tokens=max_tokens, stream=True
            )
            async with client.stream(
                "POST",
                spec.base_url,
                headers={**_headers(spec), "Content-Type": "application/json"},
                json=body,
                timeout=60.0,
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode()[:180]
                    raise RuntimeError(f"HTTP {response.status_code}: {detail}")

                got_any = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    piece = delta.get("content")
                    if piece:
                        got_any = True
                        yield piece

                if not got_any:
                    raise RuntimeError("stream produced no content")

            registry.record_success(source, latency_ms=(time.perf_counter() - started) * 1000)
            return

        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            registry.record_failure(source, error)
            attempts.append((spec.name, error))
            log.warning("LLM stream via %s failed: %s", spec.name, error[:200])

    # Nothing would stream. Fall back to a single completion so the caller still
    # gets an answer rather than an error — degrade, do not fail.
    try:
        reply = await complete(
            messages, temperature=temperature, max_tokens=max_tokens, prefer=prefer
        )
        yield reply.text
    except LlmUnavailable as exc:
        raise LlmUnavailable([*attempts, *exc.attempts]) from exc


def budget_status() -> dict[str, Any]:
    """For ``/healthz`` and the demo: how much of the metered quota is left."""
    settings = get_settings()
    out: dict[str, Any] = {"as_of": utcnow().isoformat(), "providers": []}
    for spec in PROVIDERS:
        limit = settings.openrouter_daily_budget if spec.name == "openrouter" else spec.daily_budget
        budget = _budgets.get(spec.name)
        out["providers"].append(
            {
                "name": spec.name,
                "model": spec.model,
                "configured": spec.key_attr is None
                or getattr(settings, spec.key_attr, None) is not None,
                "daily_limit": limit,
                "used_today": budget.used if budget else 0,
                "notes": spec.notes,
            }
        )
    return out

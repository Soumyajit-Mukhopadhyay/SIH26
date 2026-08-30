"""Translation: Sarvam primary, Gemini fallback, always through the guard.

Sarvam is verified working and preserved "wave height 2.4 metres" ->
"அலை உயரம் 2.4 மீட்டர்" in testing, numeral intact. That is encouraging and not
sufficient: :mod:`orca.language.guard` verifies every translation regardless,
because a model that gets it right on a good day is not the same as a guarantee.

The provider detail that matters: Sarvam authenticates with an
``api-subscription-key`` header, not a bearer token, and expects BCP-47-ish
locale codes (``ta-IN``), not bare ISO 639 codes.
"""

from __future__ import annotations

import logging
from typing import Any

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Provider
from orca.sources.base import get_client

log = logging.getLogger(__name__)

SARVAM_URL = "https://api.sarvam.ai/translate"

#: ORCA language code -> Sarvam locale.
SARVAM_LOCALES: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "gu": "gu-IN",
    "mr": "mr-IN",
    "kok": "kok-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "or": "od-IN",  # Sarvam uses `od-IN`, not the ISO `or`
    "bn": "bn-IN",
}

#: Sarvam caps a single translate call. Longer advisories are split on sentence
#: boundaries rather than mid-number.
MAX_CHARS = 950


class TranslationUnavailable(RuntimeError):
    """No provider could translate. The caller must fall back to English."""


async def _sarvam(text: str, source: str, target: str) -> str:
    settings = get_settings()
    if not settings.has_sarvam:
        raise TranslationUnavailable("SARVAM_API_KEY is not configured")

    src = SARVAM_LOCALES.get(source, "en-IN")
    dst = SARVAM_LOCALES.get(target)
    if dst is None:
        raise TranslationUnavailable(f"Sarvam has no locale for {target!r}")

    client = await get_client()
    response = await client.post(
        SARVAM_URL,
        headers={
            # Not a bearer token. Sending Authorization instead returns 403 and
            # looks like an invalid key.
            "api-subscription-key": settings.sarvam_api_key.get_secret_value(),  # type: ignore[union-attr]
            "Content-Type": "application/json",
        },
        json={
            "input": text,
            "source_language_code": src,
            "target_language_code": dst,
            "speaker_gender": "Male",
            "mode": "formal",
            "enable_preprocessing": False,
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise TranslationUnavailable(f"Sarvam HTTP {response.status_code}: {response.text[:160]}")
    payload = response.json()
    translated = payload.get("translated_text")
    if not translated:
        raise TranslationUnavailable(f"Sarvam returned no translation: {str(payload)[:160]}")
    return str(translated)


async def _gemini(text: str, source: str, target: str) -> str:
    """Fallback. Instructed firmly about numerals, then verified anyway."""
    from orca.agents.llm import LlmUnavailable, complete
    from orca.language.detect import SUPPORTED

    target_name = SUPPORTED.get(target, {}).get("name", target)
    source_name = SUPPORTED.get(source, {}).get("name", source)

    try:
        reply = await complete(
            [
                {
                    "role": "system",
                    "content": (
                        f"Translate from {source_name} to {target_name}. Reply with ONLY the "
                        "translation — no preamble, no notes, no transliteration. "
                        "Copy every number, unit and place name EXACTLY as written. "
                        "Never round, convert or localise a number. If the text contains "
                        "tokens like NUMTOKEN0XX, reproduce them character for character."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=900,
            prefer="gemini",
        )
    except LlmUnavailable as exc:
        raise TranslationUnavailable(str(exc)) from exc
    return reply.text.strip()


def _split(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split long text on sentence boundaries, never inside a number."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in text.replace("\n", " \n").split(". "):
        candidate = f"{current}. {sentence}" if current else sentence
        if len(candidate) > limit and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def translate_raw(text: str, *, source: str = "en", target: str = "ta") -> tuple[str, str]:
    """Translate without the guard. Returns ``(text, provider)``.

    Not for direct use in an advisory — call :func:`translate` instead, which
    verifies the numerals.
    """
    if source == target or not text.strip():
        return text, "identity"

    errors: list[str] = []
    for name, fn in (("sarvam", _sarvam), ("gemini", _gemini)):
        source_name = f"language.{name}"
        registry.declare(source_name, provider=Provider.SARVAM, variables=["translation"])
        try:
            parts = [await fn(chunk, source, target) for chunk in _split(text)]
            registry.record_success(source_name)
            return " ".join(parts), name
        except Exception as exc:  # noqa: BLE001 — try the next provider
            message = f"{type(exc).__name__}: {exc}"
            registry.record_failure(source_name, message)
            errors.append(f"{name}: {message}")
            log.warning("translation via %s failed: %s", name, message[:180])

    raise TranslationUnavailable("; ".join(errors))


async def translate(
    text: str,
    *,
    source: str = "en",
    target: str = "ta",
    keep_latin_digits: bool = True,
) -> dict[str, Any]:
    """Translate, then prove every numeral survived.

    ``keep_latin_digits`` defaults to True on purpose: ASCII numerals are
    universally readable on a phone and in a marine context, and rendering
    "2.4" as "௨.௪" is authentic but harder for many readers to act on quickly.
    The option exists because that is a judgement call, not a fact.
    """
    from orca.language.detect import target_script
    from orca.language.guard import guarded_translate

    provider_used = "none"

    async def _call(payload: str) -> str:
        nonlocal provider_used
        translated, provider = await translate_raw(payload, source=source, target=target)
        provider_used = provider
        return translated

    result = await guarded_translate(
        text,
        translate=_call,
        target_script="latin" if keep_latin_digits else target_script(target),
    )

    return {
        "text": result.text if result.ok else text,
        "source": source,
        "target": target,
        "provider": provider_used,
        "guard": result.describe(),
        # The caller MUST honour this. A failed guard means the translation may
        # carry a wrong figure, and a wrong figure in a marine advisory is worse
        # than an English one.
        "safe_to_speak": result.ok,
        "fallback_reason": None if result.ok else result.note,
    }

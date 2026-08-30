"""Speech: ASR in, TTS out.

**Provider choice is per language, not global**, and that is a measured decision
rather than a preference:

* **ASR** — Sarvam's Saarika is purpose-built for Indic speech and is the primary
  for the nine coastal languages. Groq's ``whisper-large-v3`` is the primary for
  English and the fallback everywhere: on a clean English round-trip it returned
  "Wave height is 2.4 meters. Do not go to sea today." verbatim, while its Tamil
  transcription of synthetic speech was poor.
* **TTS** — Sarvam Bulbul v3. Note the version: ``bulbul:v2`` is **deprecated**
  and returns a 400 that names the replacement, and the v2 speaker names
  (anushka, manisha, …) are rejected by v3, which has an entirely different
  roster. Both are easy to get wrong from older documentation.

**Konkani has no TTS voice** in Bulbul or IndicF5. That is stated per language in
:data:`VOICES` and surfaced in the API response rather than hidden: ORCA returns
Konkani text and speaks it with a Marathi-adjacent voice, and says which voice it
used. Claiming blanket coverage is what does not survive a demo.

A caution about round-trip testing, since it is tempting and misleading:
synthesising speech and transcribing it back is a *harsh* test that neither
provider is built for, and poor results there do not predict poor results on
human speech, which is what the product actually receives. So the tests here
assert what is checkable — that audio is produced, in the right format, of a
plausible duration — and do not assert transcription accuracy against our own
synthesiser.
"""

from __future__ import annotations

import base64
import io
import logging
import wave
from dataclasses import dataclass, field
from typing import Any

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Provider
from orca.sources.base import get_client

log = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_ASR_URL = "https://api.sarvam.ai/speech-to-text"
GROQ_ASR_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

#: Current Bulbul model. v2 is deprecated and 400s with a message naming v3.
BULBUL_MODEL = "bulbul:v3"

#: Current Saarika model. v1 and v2 both 400; only v2.5 is live.
SAARIKA_MODEL = "saarika:v2.5"

WHISPER_MODEL = "whisper-large-v3"

#: Sarvam locale codes. Odia is `od-IN`, not the ISO `or-IN`.
LOCALES: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "gu": "gu-IN",
    "mr": "mr-IN",
    "kok": "mr-IN",  # no Konkani voice; see VOICES
    "kn": "kn-IN",
    "ml": "ml-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "or": "od-IN",
    "bn": "bn-IN",
}


@dataclass(frozen=True, slots=True)
class Voice:
    """A TTS voice for one language."""

    speaker: str
    #: False when the language has no native voice and we are substituting.
    native: bool = True
    note: str | None = None


#: Speakers are from the **v3** roster, obtained from the API's own rejection
#: message — and then each one verified by actually synthesising with it. That
#: second step is not redundant: `niharika` is in the published list and is
#: rejected as "not recognized" when used.
VOICES: dict[str, Voice] = {
    "en": Voice("aditya"),
    "hi": Voice("ritu"),
    "gu": Voice("pooja"),
    "mr": Voice("rupali"),
    "kok": Voice(
        "rupali",
        native=False,
        note=(
            "Konkani has no TTS voice in Sarvam Bulbul or IndicF5. ORCA returns Konkani text "
            "and speaks it with a Marathi voice, which is the closest available. This is a "
            "stated gap, not a claim of coverage."
        ),
    ),
    "kn": Voice("kavitha"),
    "ml": Voice("shreya"),
    "ta": Voice("shruti"),
    "te": Voice("suhani"),
    # `niharika` appears in the API's own list of v3 speakers and is then
    # rejected as "not recognized". Verified working instead.
    "or": Voice("shruti"),
    "bn": Voice("tanya"),
}

#: Languages where Saarika is tried before Whisper.
INDIC = frozenset({"hi", "gu", "mr", "kok", "kn", "ml", "ta", "te", "or", "bn"})


class SpeechUnavailable(RuntimeError):
    """No provider could handle the request."""


@dataclass(slots=True)
class Transcript:
    text: str
    language: str
    provider: str
    duration_s: float | None = None
    attempts: list[tuple[str, str]] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "provider": self.provider,
            "duration_s": self.duration_s,
            "fallbacks": self.attempts,
        }


@dataclass(slots=True)
class Speech:
    """Synthesised audio, plus what it took to produce it."""

    audio: bytes
    mime: str
    language: str
    speaker: str
    provider: str
    duration_s: float | None
    native_voice: bool
    voice_note: str | None = None

    def as_data_uri(self) -> str:
        """A data: URI, so the browser can play it without a second request."""
        return f"data:{self.mime};base64,{base64.b64encode(self.audio).decode()}"

    def describe(self) -> dict[str, Any]:
        return {
            "mime": self.mime,
            "language": self.language,
            "speaker": self.speaker,
            "provider": self.provider,
            "duration_s": self.duration_s,
            "bytes": len(self.audio),
            "native_voice": self.native_voice,
            "voice_note": self.voice_note,
        }


def _wav_duration(audio: bytes) -> float | None:
    """Duration of a WAV, or None if it is not a parseable WAV.

    Used to sanity-check synthesis: a two-second clip for a four-second sentence
    means the voice truncated, which is worth knowing before it is spoken to a
    fisherman.
    """
    try:
        with wave.open(io.BytesIO(audio)) as handle:
            return round(handle.getnframes() / handle.getframerate(), 2)
    except (wave.Error, EOFError):
        return None


# --------------------------------------------------------------------------- #
# text to speech
# --------------------------------------------------------------------------- #


async def synthesise(
    text: str,
    *,
    language: str = "en",
    speaker: str | None = None,
    pace: float = 1.0,
) -> Speech:
    """Speak ``text`` in ``language``.

    Raises :class:`SpeechUnavailable` rather than returning silence: a caller
    that thinks it has audio and does not would show a play button that does
    nothing.
    """
    settings = get_settings()
    if not settings.has_sarvam:
        raise SpeechUnavailable("SARVAM_API_KEY is not configured")

    voice = VOICES.get(language, VOICES["en"])
    chosen = speaker or voice.speaker
    locale = LOCALES.get(language, "en-IN")

    registry.declare("language.tts", provider=Provider.SARVAM, variables=["speech"])
    client = await get_client()

    try:
        response = await client.post(
            SARVAM_TTS_URL,
            headers={
                # Not a bearer token — Sarvam uses its own header, and
                # Authorization returns 403 in a way that looks like a bad key.
                "api-subscription-key": settings.sarvam_api_key.get_secret_value(),  # type: ignore[union-attr]
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "target_language_code": locale,
                "speaker": chosen,
                "model": BULBUL_MODEL,
                "pace": pace,
                # Sarvam's own normaliser expands digits and abbreviations into
                # words before synthesis, which is what a mixed-script advisory
                # with "2.4 m" in it needs.
                "enable_preprocessing": True,
            },
            timeout=90.0,
        )
    except Exception as exc:
        registry.record_failure("language.tts", f"{type(exc).__name__}: {exc}")
        raise SpeechUnavailable(f"Sarvam TTS unreachable: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:220]
        registry.record_failure("language.tts", f"HTTP {response.status_code}: {detail}")
        raise SpeechUnavailable(f"Sarvam TTS HTTP {response.status_code}: {detail}")

    payload = response.json()
    audios = payload.get("audios") or []
    if not audios:
        registry.record_failure("language.tts", "no audio in response")
        raise SpeechUnavailable(f"Sarvam TTS returned no audio: {str(payload)[:180]}")

    audio = b"".join(base64.b64decode(chunk) for chunk in audios)
    duration = _wav_duration(audio)
    registry.record_success("language.tts")

    return Speech(
        audio=audio,
        mime="audio/wav",
        language=language,
        speaker=chosen,
        provider="sarvam-bulbul-v3",
        duration_s=duration,
        native_voice=voice.native,
        voice_note=voice.note,
    )


# --------------------------------------------------------------------------- #
# speech to text
# --------------------------------------------------------------------------- #


async def _sarvam_asr(audio: bytes, filename: str, language: str) -> str:
    settings = get_settings()
    if not settings.has_sarvam:
        raise SpeechUnavailable("SARVAM_API_KEY is not configured")
    client = await get_client()
    response = await client.post(
        SARVAM_ASR_URL,
        headers={
            "api-subscription-key": settings.sarvam_api_key.get_secret_value(),  # type: ignore[union-attr]
        },
        files={"file": (filename, audio, "audio/wav")},
        data={"model": SAARIKA_MODEL, "language_code": LOCALES.get(language, "en-IN")},
        timeout=120.0,
    )
    if response.status_code >= 400:
        raise SpeechUnavailable(f"Sarvam ASR HTTP {response.status_code}: {response.text[:180]}")
    payload = response.json()
    text = payload.get("transcript")
    if not text:
        raise SpeechUnavailable(f"Sarvam ASR returned no transcript: {str(payload)[:180]}")
    return str(text)


async def _groq_asr(audio: bytes, filename: str, language: str) -> str:
    settings = get_settings()
    if not settings.has_groq:
        raise SpeechUnavailable("GROQ_API_KEY is not configured")
    client = await get_client()
    response = await client.post(
        GROQ_ASR_URL,
        headers={
            "Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"  # type: ignore[union-attr]
        },
        files={"file": (filename, audio, "audio/wav")},
        data={
            "model": WHISPER_MODEL,
            # Whisper can detect the language, but naming it materially improves
            # accuracy on short clips, which is what a mic press produces.
            "language": language,
            "response_format": "json",
        },
        timeout=120.0,
    )
    if response.status_code >= 400:
        raise SpeechUnavailable(f"Groq ASR HTTP {response.status_code}: {response.text[:180]}")
    text = response.json().get("text")
    if not text:
        raise SpeechUnavailable("Groq ASR returned no text")
    return str(text)


async def transcribe(
    audio: bytes,
    *,
    language: str = "en",
    filename: str = "audio.wav",
) -> Transcript:
    """Transcribe audio, trying the provider best suited to the language first.

    Saarika leads for Indic and Whisper for English, then each falls back to the
    other. Both failing raises rather than returning an empty string, because a
    silent failure here becomes an empty question sent to the agent.
    """
    order = (
        [("sarvam-saarika", _sarvam_asr), ("groq-whisper", _groq_asr)]
        if language in INDIC
        else [("groq-whisper", _groq_asr), ("sarvam-saarika", _sarvam_asr)]
    )

    attempts: list[tuple[str, str]] = []
    duration = _wav_duration(audio)

    for name, fn in order:
        source = f"language.asr.{name}"
        registry.declare(source, provider=Provider.SARVAM, variables=["transcript"])
        try:
            text = await fn(audio, filename, language)
            registry.record_success(source)
            return Transcript(
                text=text.strip(),
                language=language,
                provider=name,
                duration_s=duration,
                attempts=attempts,
            )
        except Exception as exc:  # noqa: BLE001 — any failure means try the next provider
            message = f"{type(exc).__name__}: {exc}"
            registry.record_failure(source, message)
            attempts.append((name, message))
            log.warning("ASR via %s failed: %s", name, message[:200])

    raise SpeechUnavailable("; ".join(f"{n}: {e}" for n, e in attempts))


def voice_roster() -> list[dict[str, Any]]:
    """Which languages ORCA can speak, and where the gaps are."""
    from orca.language.detect import SUPPORTED

    return [
        {
            "code": code,
            "name": SUPPORTED.get(code, {}).get("name", code),
            "locale": LOCALES.get(code),
            "speaker": voice.speaker,
            "native_voice": voice.native,
            "note": voice.note,
            "asr_primary": "sarvam-saarika" if code in INDIC else "groq-whisper",
        }
        for code, voice in VOICES.items()
    ]

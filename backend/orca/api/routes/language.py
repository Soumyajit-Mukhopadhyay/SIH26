"""The language plane: detect, translate, and the number guard.

``/language/translate`` never returns a translation without also returning the
guard's verdict. ``safe_to_speak`` is the field that matters — a client that
ignores it and voices a failed translation is voicing a possibly-wrong wave
height, which is the specific failure this plane exists to prevent.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from orca.language import detect as detect_module
from orca.language import guard as guard_module
from orca.language import speech as speech_module
from orca.language import translate as translate_module
from orca.provenance import utcnow

log = logging.getLogger(__name__)

router = APIRouter(prefix="/language", tags=["language"])


class DetectRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    target: str = Field(description="ORCA language code, e.g. 'ta'.")
    source: str | None = Field(default=None, description="Detected when omitted.")
    keep_latin_digits: bool = True


@router.get("/languages", summary="The nine coastal languages, and their gaps")
async def languages() -> dict[str, Any]:
    return {
        "languages": detect_module.roster(),
        "note": (
            "Coverage is stated per language rather than claimed in general. Konkani has no "
            "TTS voice in either IndicF5 or Sarvam Bulbul; ORCA returns Konkani text and says "
            "which voice it used."
        ),
    }


@router.post("/detect", summary="Identify the language, including romanised input")
async def detect_language(request: DetectRequest) -> dict[str, Any]:
    """Script ranges plus function words. No model download, works offline.

    Reports a confidence and alternatives, because Devanagari is shared by Hindi,
    Marathi and Konkani, and a wrong guess answers a fisherman in the wrong
    language.
    """
    result = detect_module.detect(request.text)
    return {
        **result.describe(),
        "script_histogram": detect_module.script_histogram(request.text),
        "generated_at": utcnow().isoformat(),
    }


@router.post("/translate", summary="Translate with the number-integrity guard")
async def translate_text(request: TranslateRequest) -> dict[str, Any]:
    if request.target not in detect_module.SUPPORTED:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unsupported target {request.target!r}; "
                f"see GET /language/languages for the {len(detect_module.SUPPORTED)} supported"
            ),
        )

    source = request.source
    detection = None
    if source is None:
        detection = detect_module.detect(request.text)
        source = detection.language

    try:
        result = await translate_module.translate(
            request.text,
            source=source,
            target=request.target,
            keep_latin_digits=request.keep_latin_digits,
        )
    except translate_module.TranslationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"no translation provider could answer: {exc}. "
                "ORCA falls back to English rather than guessing."
            ),
        ) from exc

    return {
        **result,
        "detected": detection.describe() if detection else None,
        "generated_at": utcnow().isoformat(),
    }


@router.post("/verify-numbers", summary="Check that two texts carry the same numerals")
async def verify_numbers(payload: dict[str, str]) -> dict[str, Any]:
    """The guard, exposed on its own.

    Useful for testing, and genuinely useful in the demo: paste a source and a
    translation and see the multiset comparison that decides whether ORCA is
    willing to speak it.
    """
    source = payload.get("source", "")
    output = payload.get("output", "")
    if not source or not output:
        raise HTTPException(status_code=422, detail="both 'source' and 'output' are required")
    result = guard_module.verify(source, output)
    return {
        **result.describe(),
        "explanation": (
            "Numerals are compared as a MULTISET after normalising Indic digits to ASCII, so "
            "'2.4' and the Tamil rendering compare equal, but losing one of two identical "
            "figures is still caught."
        ),
    }


# --------------------------------------------------------------------------- #
# speech
# --------------------------------------------------------------------------- #


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2500)
    language: str = "en"
    speaker: str | None = None
    pace: float = Field(default=1.0, ge=0.5, le=2.0)


@router.get("/voices", summary="Which languages ORCA can speak, and where the gaps are")
async def voices() -> dict[str, Any]:
    return {
        "voices": speech_module.voice_roster(),
        "tts_model": speech_module.BULBUL_MODEL,
        "asr_indic": speech_module.SAARIKA_MODEL,
        "asr_english": speech_module.WHISPER_MODEL,
        "note": (
            "The ASR provider is chosen per language, not globally. Saarika leads for the "
            "Indic languages and Whisper for English: on the same Tamil clip Saarika "
            "transcribed it exactly, numerals included, where Whisper garbled it."
        ),
    }


@router.post("/speak", summary="Synthesise speech (returns WAV)")
async def speak(request: SpeakRequest) -> Response:
    """Speak text in one of the supported languages.

    Returns ``audio/wav`` directly so the browser can play the response without a
    second request. The headers name the voice actually used, including when it is
    a substitute — Konkani has no native voice, and ORCA says so rather than
    passing a Marathi voice off as Konkani.
    """
    if request.language not in detect_module.SUPPORTED:
        raise HTTPException(status_code=422, detail=f"unsupported language {request.language!r}")

    try:
        result = await speech_module.synthesise(
            request.text,
            language=request.language,
            speaker=request.speaker,
            pace=request.pace,
        )
    except speech_module.SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    headers = {
        "X-Orca-Speaker": result.speaker,
        "X-Orca-Provider": result.provider,
        "X-Orca-Duration-S": str(result.duration_s),
        "X-Orca-Native-Voice": str(result.native_voice).lower(),
        "Cache-Control": "no-store",
    }
    if result.voice_note:
        headers["X-Orca-Voice-Note"] = result.voice_note

    return Response(content=result.audio, media_type=result.mime, headers=headers)


#: Module-level singletons: FastAPI wants these as defaults, and constructing
#: them in the signature trips ruff's B008.
_AUDIO_FILE = File(...)
_LANGUAGE_FORM = Form("en")


@router.post("/listen", summary="Transcribe uploaded audio")
async def listen(
    file: UploadFile = _AUDIO_FILE,
    language: str = _LANGUAGE_FORM,
) -> dict[str, Any]:
    """Transcribe a recording, then detect the language of what came back.

    The caller names the expected language because doing so materially improves
    accuracy on the short clips a mic press produces. The detection returned is on
    the TRANSCRIPT — so if someone selects English and speaks Tamil, the response
    reveals it rather than answering the wrong question in the wrong language.
    """
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=422, detail="the uploaded audio was empty")
    if len(audio) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="audio must be under 12 MB")

    try:
        transcript = await speech_module.transcribe(
            audio, language=language, filename=file.filename or "audio.wav"
        )
    except speech_module.SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    detected = detect_module.detect(transcript.text) if transcript.text else None
    return {
        **transcript.describe(),
        "detected": detected.describe() if detected else None,
        "bytes": len(audio),
        "generated_at": utcnow().isoformat(),
    }

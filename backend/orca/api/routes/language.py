"""The language plane: detect, translate, and the number guard.

``/language/translate`` never returns a translation without also returning the
guard's verdict. ``safe_to_speak`` is the field that matters — a client that
ignores it and voices a failed translation is voicing a possibly-wrong wave
height, which is the specific failure this plane exists to prevent.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orca.language import detect as detect_module
from orca.language import guard as guard_module
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

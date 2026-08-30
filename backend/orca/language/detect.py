"""Language detection for the nine coastal languages, plus English.

Script ranges do most of the work and are exact: Tamil text is in the Tamil
block and nothing else is. The genuinely hard cases are the two the plan calls
out:

* **Devanagari is shared** by Hindi, Marathi and Konkani. The block tells you the
  script, not the language, so those are separated by function words.
* **Romanised input is common** — a fisherman typing "kadal safe ah?" on a phone
  keyboard. Pure script detection calls that English and answers in the wrong
  language, which is a worse failure than not detecting at all.

No model download: a script-range pass plus a small function-word table gets the
coastal languages right, runs in microseconds, and works offline. The confidence
is reported so a caller can fall back to asking rather than guessing.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

#: The nine coastal languages the plan names, plus English and Hindi.
SUPPORTED: dict[str, dict[str, str]] = {
    "en": {"name": "English", "script": "latin", "coast": "-"},
    "hi": {"name": "Hindi", "script": "devanagari", "coast": "-"},
    "gu": {"name": "Gujarati", "script": "gujarati", "coast": "Gujarat"},
    "mr": {"name": "Marathi", "script": "devanagari", "coast": "Maharashtra"},
    "kok": {"name": "Konkani", "script": "devanagari", "coast": "Goa"},
    "kn": {"name": "Kannada", "script": "kannada", "coast": "Karnataka"},
    "ml": {"name": "Malayalam", "script": "malayalam", "coast": "Kerala"},
    "ta": {"name": "Tamil", "script": "tamil", "coast": "Tamil Nadu"},
    "te": {"name": "Telugu", "script": "telugu", "coast": "Andhra Pradesh"},
    "or": {"name": "Odia", "script": "odia", "coast": "Odisha"},
    "bn": {"name": "Bengali", "script": "bengali", "coast": "West Bengal"},
}

#: Unicode block ranges. A script maps to exactly one language except Devanagari.
_SCRIPT_RANGES: list[tuple[str, int, int]] = [
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gujarati", 0x0A80, 0x0AFF),
    ("odia", 0x0B00, 0x0B7F),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
]

_SCRIPT_TO_LANG = {
    "bengali": "bn",
    "gujarati": "gu",
    "odia": "or",
    "tamil": "ta",
    "telugu": "te",
    "kannada": "kn",
    "malayalam": "ml",
}

#: Function words that separate the three Devanagari languages.
#:
#: Every entry must be DISTINCTIVE, not merely frequent. An earlier version
#: listed "समुद्र" (sea) under both Hindi and Marathi, so the one word most
#: likely to appear in a marine question scored for both and separated neither —
#: a marker that fires for two languages is worse than no marker, because it
#: manufactures false confidence.
_DEVANAGARI_MARKERS: dict[str, tuple[str, ...]] = {
    "mr": ("आहे", "नाही", "माझ", "तुम्ही", "होडी", "आम्ही", "कशी", "काय", "पाहिजे"),
    "kok": ("आसा", "म्हण", "तुका", "आमी", "दर्या", "कितें", "खंय", "जाल्यार"),
    "hi": ("है", "नहीं", "मेरा", "आपको", "करना", "क्या", "हमें", "कैसे", "चाहिए"),
}

#: Romanised markers, for phone-keyboard input. Deliberately conservative: a
#: wrong guess here answers in the wrong language, so ambiguous input stays
#: English with low confidence and the caller can ask.
_ROMAN_MARKERS: dict[str, tuple[str, ...]] = {
    "ta": ("kadal", "meen", "padagu", "poga", "illai", "enna", "eppadi", "vanakkam", "safe ah"),
    "ml": ("kadal", "meen", "vallam", "pokaan", "illa", "entha", "engane", "namaskaram"),
    "te": ("samudram", "chepa", "padava", "vellali", "ledu", "ela", "namaskaram"),
    "kn": ("samudra", "meenu", "doni", "hogabahuda", "illa", "hege"),
    "hi": ("samundar", "machhli", "nav", "jana", "nahi", "kaise", "kya", "namaste"),
    "mr": ("samudra", "masa", "hodi", "jaycha", "nahi", "kasa"),
    "bn": ("samudra", "machh", "nouka", "jabo", "na", "kemon"),
    "gu": ("dariyo", "machhli", "hodi", "javu", "nathi", "kem"),
}


@dataclass(slots=True)
class Detection:
    language: str
    script: str
    confidence: float
    method: str
    name: str
    #: Alternatives worth offering when confidence is low.
    alternatives: list[str]

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.75

    def describe(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "name": self.name,
            "script": self.script,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "alternatives": self.alternatives,
            "confident": self.is_confident,
        }


def script_histogram(text: str) -> dict[str, int]:
    """Count characters per script block. Punctuation and digits are ignored."""
    counts: dict[str, int] = {}
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        if code < 128:
            counts["latin"] = counts.get("latin", 0) + 1
            continue
        for name, low, high in _SCRIPT_RANGES:
            if low <= code <= high:
                counts[name] = counts.get(name, 0) + 1
                break
        else:
            counts[unicodedata.name(char, "unknown").split()[0].lower()] = (
                counts.get("other", 0) + 1
            )
    return counts


def detect(text: str) -> Detection:
    """Best-guess language, with a confidence and the method that produced it."""
    stripped = text.strip()
    if not stripped:
        return Detection("en", "latin", 0.0, "empty", "English", [])

    counts = script_histogram(stripped)
    if not counts:
        return Detection("en", "latin", 0.2, "no-letters", "English", [])

    dominant = max(counts, key=lambda k: counts[k])
    total = sum(counts.values())
    share = counts[dominant] / total

    # ---- an unambiguous Indic script ----
    if dominant in _SCRIPT_TO_LANG:
        language = _SCRIPT_TO_LANG[dominant]
        return Detection(
            language=language,
            script=dominant,
            confidence=min(0.99, 0.80 + share * 0.19),
            method="unicode-script",
            name=SUPPORTED[language]["name"],
            alternatives=[],
        )

    # ---- Devanagari: script is known, language is not ----
    if dominant == "devanagari":
        scores = {
            lang: sum(1 for marker in markers if marker in stripped)
            for lang, markers in _DEVANAGARI_MARKERS.items()
        }
        best = max(scores, key=lambda k: scores[k])
        if scores[best] > 0:
            runner_up = sorted(scores.values(), reverse=True)[1]
            margin = scores[best] - runner_up
            return Detection(
                language=best,
                script="devanagari",
                confidence=0.70 + min(0.25, margin * 0.12),
                method="devanagari-function-words",
                name=SUPPORTED[best]["name"],
                alternatives=[lang for lang in scores if lang != best],
            )
        # Devanagari with no distinguishing marker. Hindi is the safe default,
        # but say the confidence is low and offer the alternatives rather than
        # answering a Konkani speaker in Hindi as if certain.
        return Detection(
            language="hi",
            script="devanagari",
            confidence=0.45,
            method="devanagari-default",
            name="Hindi",
            alternatives=["mr", "kok"],
        )

    # ---- Latin: English, or romanised Indic ----
    lowered = f" {stripped.lower()} "
    scores = {
        lang: sum(1 for marker in markers if f" {marker}" in lowered or marker in lowered)
        for lang, markers in _ROMAN_MARKERS.items()
    }
    best = max(scores, key=lambda k: scores[k]) if scores else "en"
    if scores.get(best, 0) >= 2:
        return Detection(
            language=best,
            script="latin",
            confidence=0.62 + min(0.2, scores[best] * 0.06),
            method="romanised-markers",
            name=SUPPORTED[best]["name"],
            alternatives=["en", *[k for k, v in scores.items() if v > 0 and k != best]],
        )
    if scores.get(best, 0) == 1:
        # One marker is a hint, not a determination — "nav" appears in English
        # too. Answer in English but tell the caller what else it might be.
        return Detection(
            language="en",
            script="latin",
            confidence=0.55,
            method="latin-ambiguous",
            name="English",
            alternatives=[best],
        )

    return Detection("en", "latin", 0.90, "latin-default", "English", [])


def target_script(language: str) -> str:
    return SUPPORTED.get(language, {}).get("script", "latin")


def roster() -> list[dict[str, Any]]:
    """The supported languages, for the UI's language picker and for /healthz."""
    return [
        {
            "code": code,
            "name": meta["name"],
            "script": meta["script"],
            "coast": meta["coast"],
            # Stated per language rather than in a footnote: Konkani has no TTS
            # in either IndicF5 or Sarvam Bulbul, and claiming otherwise is the
            # kind of blanket coverage claim that does not survive a demo.
            "tts": code != "kok",
            "tts_note": (
                "Konkani has no TTS voice in IndicF5 or Sarvam Bulbul. ORCA returns "
                "Konkani text and speaks it with a Marathi-adjacent voice, and says so."
                if code == "kok"
                else None
            ),
        }
        for code, meta in SUPPORTED.items()
    ]

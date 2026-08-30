"""The number-integrity guard.

**The part of the language plane that matters in a safety system.** Everything
else here is convenience; this is correctness.

A translation model that renders "wave height 2.4 metres" as "wave height 2.5
metres" has not made a cosmetic error — it has changed a number a fisherman will
act on, in an advisory whose entire purpose is that number. Worse, it fails
silently and looks perfectly fluent.

So the guard does not trust the translator:

1. **Mask** every numeral in the source, replacing it with an opaque token.
2. **Translate** the masked text, so there are no digits for the model to
   "improve".
3. **Re-inject** the original numerals verbatim, converting to the target
   script's digits only where that is unambiguous.
4. **Verify** — extract the numerals from the result and compare, as a multiset,
   against the source. A mismatch is a hard failure, not a warning.

Indic digit handling is why step 3 is not simply string replacement: Tamil,
Devanagari, Bengali, Gujarati, Odia, Telugu, Kannada and Malayalam each have
their own digit glyphs, and a model may emit either those or ASCII. The verifier
normalises both sides before comparing, so "௨.௪" and "2.4" compare equal.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Per-script digit glyphs, indexed 0-9. Used to normalise before comparing and
#: to render numerals in the target script when asked.
DIGIT_SETS: dict[str, str] = {
    "latin": "0123456789",
    "devanagari": "०१२३४५६७८९",
    "bengali": "০১২৩৪৫৬৭৮৯",
    "gujarati": "૦૧૨૩૪૫૬૭૮૯",
    "odia": "୦୧୨୩୪୫୬୭୮୯",
    "tamil": "௦௧௨௩௪௫௬௭௮௯",
    "telugu": "౦౧౨౩౪౫౬౭౮౯",
    "kannada": "೦೧೨೩೪೫೬೭೮೯",
    "malayalam": "൦൧൨൩൪൫൬൭൮൯",
}

#: Every non-Latin digit mapped back to its ASCII equivalent.
_TO_LATIN = {
    glyph: str(index)
    for script, digits in DIGIT_SETS.items()
    if script != "latin"
    for index, glyph in enumerate(digits)
}

#: A number, with optional decimal part and thousands separators, in any script.
_ALL_DIGITS = "".join(DIGIT_SETS.values())
_NUMBER = re.compile(rf"[{_ALL_DIGITS}]+(?:[.,][{_ALL_DIGITS}]+)*")

#: Terms that must survive translation VERBATIM, for the same reason numerals
#: must: they carry the decision, not the prose.
#:
#: Found the hard way. Asked to translate an advisory beginning "**NO-GO**",
#: Sarvam returned "**நெறிதவறிச் செல்வோருக்குத் தண்டனை**" — roughly "punishment
#: for those who go astray". Fluent, confident, and it destroyed the single word
#: the whole advisory exists to convey. The number guard had protected every
#: figure and waved the verdict straight through.
PROTECTED_TERMS: tuple[str, ...] = (
    "NO-GO",
    "NO GO",
    "CAUTION",
    "UNVERIFIABLE",
    "GO",
    "IMD",
    "INCOIS",
    "ORCA",
    "VHF",
    "EEZ",
    "IMBL",
    "PFZ",
    "CAPE",
)

#: Longest first, so "NO-GO" is masked before "GO" can match inside it.
_TERM_RE = re.compile(
    "|".join(re.escape(t) for t in sorted(PROTECTED_TERMS, key=len, reverse=True)),
    re.IGNORECASE,
)
_TERM_TOKEN = "TRMTOKEN{index}XX"
_TERM_TOKEN_RE = re.compile(r"TRMTOKEN([A-Z]+)XX", re.IGNORECASE)

#: The mask token. Chosen to survive translation: no spaces to be split on, and
#: a shape unlike ordinary words so a model does not "correct" it.
#:
#: The index is encoded in LETTERS, not digits. An earlier NUMTOKEN0XX form left
#: a digit in the masked text, which defeats half the point — the model is meant
#: to see no numerals at all, and a stray one is exactly the kind of thing a
#: model decides to tidy up.
_TOKEN = "NUMTOKEN{index}XX"
_TOKEN_RE = re.compile(r"NUMTOKEN([A-Z]+)XX", re.IGNORECASE)


def _encode_index(index: int) -> str:
    """0 -> A, 1 -> B, ... 25 -> Z, 26 -> BA. Bijective enough for our purposes."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _decode_index(letters: str) -> int:
    value = 0
    for char in letters.upper():
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def to_latin_digits(text: str) -> str:
    """Normalise any Indic digits to ASCII, so numerals can be compared."""
    return "".join(_TO_LATIN.get(char, char) for char in text)


def to_script_digits(text: str, script: str) -> str:
    """Render ASCII digits in a target script. Unknown scripts pass through."""
    digits = DIGIT_SETS.get(script)
    if digits is None or script == "latin":
        return text
    return "".join(digits[int(char)] if char.isdigit() else char for char in text)


def extract_numbers(text: str) -> list[str]:
    """Every numeral in the text, normalised to ASCII and canonical form.

    Thousands separators are stripped and a trailing ``.0`` normalised, so
    "2,400" and "2400" compare equal and "2.40" matches "2.4" — differences of
    formatting, not of value. A difference of *value* still fails.
    """
    out: list[str] = []
    for match in _NUMBER.findall(text):
        normalised = to_latin_digits(match)
        # A comma between digit groups is a separator; a comma as a decimal mark
        # (some locales) is handled by treating a single trailing group of 1-2
        # digits as decimal.
        if "." in normalised:
            normalised = normalised.replace(",", "")
        elif normalised.count(",") == 1 and len(normalised.split(",")[1]) in (1, 2):
            normalised = normalised.replace(",", ".")
        else:
            normalised = normalised.replace(",", "")
        try:
            value = float(normalised)
        except ValueError:
            continue
        out.append(f"{value:g}")
    return out


@dataclass(slots=True)
class GuardResult:
    """The outcome of a guarded translation."""

    text: str
    ok: bool
    source_numbers: list[str]
    output_numbers: list[str]
    #: Numbers present in the source but missing or altered in the output.
    lost: list[str] = field(default_factory=list)
    #: Numbers the output invented.
    invented: list[str] = field(default_factory=list)
    #: Protected terms (verdict words, agency names) lost in translation.
    lost_terms: list[str] = field(default_factory=list)
    #: How many attempts the guard needed.
    attempts: int = 1
    strategy: str = "direct"
    note: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strategy": self.strategy,
            "attempts": self.attempts,
            "source_numbers": self.source_numbers,
            "output_numbers": self.output_numbers,
            "lost": self.lost,
            "invented": self.invented,
            "lost_terms": self.lost_terms,
            "note": self.note,
        }


def mask_numbers(text: str) -> tuple[str, list[str]]:
    """Replace every numeral with an opaque token.

    Returns the masked text and the originals, in order. Translating the masked
    text means the model never sees a digit it could round, localise or
    "correct".
    """
    originals: list[str] = []

    def replace(match: re.Match[str]) -> str:
        originals.append(match.group(0))
        return _TOKEN.format(index=_encode_index(len(originals) - 1))

    return _NUMBER.sub(replace, text), originals


def unmask_numbers(text: str, originals: list[str], *, script: str = "latin") -> str:
    """Put the original numerals back, verbatim.

    Verbatim is the point: the number that comes out is byte-for-byte the number
    that went in, so the translator cannot have altered it.
    """

    def replace(match: re.Match[str]) -> str:
        index = _decode_index(match.group(1))
        if not 0 <= index < len(originals):
            return match.group(0)
        return to_script_digits(originals[index], script)

    return _TOKEN_RE.sub(replace, text)


def mask_terms(text: str) -> tuple[str, list[str]]:
    """Replace protected terms with opaque tokens, preserving their original case."""
    originals: list[str] = []

    def replace(match: re.Match[str]) -> str:
        originals.append(match.group(0))
        return _TERM_TOKEN.format(index=_encode_index(len(originals) - 1))

    return _TERM_RE.sub(replace, text), originals


def unmask_terms(text: str, originals: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        index = _decode_index(match.group(1))
        if not 0 <= index < len(originals):
            return match.group(0)
        return originals[index]

    return _TERM_TOKEN_RE.sub(replace, text)


def missing_terms(source: str, output: str) -> list[str]:
    """Protected terms present in the source and absent from the output."""
    present = {m.group(0).upper() for m in _TERM_RE.finditer(output)}
    wanted = {m.group(0).upper() for m in _TERM_RE.finditer(source)}
    return sorted(wanted - present)


def verify(source: str, output: str) -> GuardResult:
    """Compare the numerals in two texts as multisets.

    A multiset, not a set: "2.4 m swell and 2.4 m wind sea" losing one of its two
    2.4s is a real defect, and a set comparison would call it fine.

    A multiset, and not a *sequence*: translating a real advisory into Tamil,
    Sarvam returned the figures in the order 2.64, 8.2, 1.5 where the English had
    2.64, 1.5, 8.2 — Tamil puts the possessive before the limit. Every number was
    correct and the translation was good. An order-sensitive check would have
    rejected it and forced a needless masked retry.
    """
    source_numbers = extract_numbers(source)
    output_numbers = extract_numbers(output)

    remaining = list(output_numbers)
    lost: list[str] = []
    for number in source_numbers:
        if number in remaining:
            remaining.remove(number)
        else:
            lost.append(number)

    lost_terms = missing_terms(source, output)
    return GuardResult(
        text=output,
        ok=not lost and not remaining and not lost_terms,
        source_numbers=source_numbers,
        output_numbers=output_numbers,
        lost=lost,
        invented=remaining,
        lost_terms=lost_terms,
    )


async def guarded_translate(
    text: str,
    *,
    translate: Any,
    target_script: str = "latin",
    **translate_kwargs: Any,
) -> GuardResult:
    """Translate, then prove the numbers survived.

    ``translate`` is an async callable taking the text and returning the
    translation, so this module has no dependency on which provider is used.

    Strategy, in order:

    1. Translate directly. If every numeral survived, we are done — the direct
       output usually reads better than a masked one.
    2. If not, mask, translate, re-inject. This is near-guaranteed, since the
       model never sees a digit.
    3. If even that fails (the model dropped a token), return the source's own
       numbers with the failure recorded. The caller must then refuse to speak
       the translation rather than voice a wrong figure.
    """
    source_numbers = extract_numbers(text)
    source_terms = [m.group(0) for m in _TERM_RE.finditer(text)]

    # Nothing to guard: no numerals AND no protected terms.
    if not source_numbers and not source_terms:
        translated = await translate(text, **translate_kwargs)
        return GuardResult(
            text=translated,
            ok=True,
            source_numbers=[],
            output_numbers=[],
            strategy="no-numbers",
        )

    direct = await translate(text, **translate_kwargs)
    checked = verify(text, direct)
    if checked.ok:
        checked.strategy = "direct"
        return checked

    log.warning(
        "translation altered protected content (numerals lost=%s invented=%s, terms lost=%s); "
        "retrying with masking",
        checked.lost,
        checked.invented,
        checked.lost_terms,
    )

    masked, originals = mask_numbers(text)
    masked, terms = mask_terms(masked)
    masked_translation = await translate(masked, **translate_kwargs)
    restored = unmask_terms(masked_translation, terms)
    restored = unmask_numbers(restored, originals, script=target_script)

    result = verify(text, restored)
    result.attempts = 2
    result.strategy = "masked"
    if result.ok:
        result.note = (
            "The direct translation altered a number or a protected term, so ORCA masked "
            "both, translated, and re-inserted the originals verbatim."
        )
        return result

    # Both strategies failed. Say so loudly; do not ship a wrong figure.
    result.attempts = 3
    result.strategy = "failed"
    result.ok = False
    result.note = (
        "Both direct and masked translation lost or altered a numeral or a protected term "
        "(a verdict word, or an agency name). The translated text must NOT be spoken or "
        "shown as an advisory — fall back to English."
    )
    log.error(
        "number guard failed for %r: lost=%s invented=%s", text[:80], result.lost, result.invented
    )
    return result

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
#: Verdict words and other content that must survive VERBATIM and that is only
#: meaningful in upper case. Matched case-SENSITIVELY on purpose: the rule engine
#: always emits "NO-GO" and "CAUTION" in caps, and matching case-insensitively
#: would mask the ordinary English word "go" everywhere it appears in prose,
#: leaving stray English words scattered through a Tamil advisory.
PROTECTED_VERDICTS: tuple[str, ...] = (
    "NO-GO",
    "NO GO",
    "CAUTION",
    "UNVERIFIABLE",
    "GO",
)

#: Compass bearings, kept in Latin letters rather than translated.
#:
#: Sarvam spelled "SSW" out phonetically as "எஸ்எஸ்டபிள்யூ" — letter by letter,
#: as if reading an unfamiliar acronym aloud. A fisherman reads a bearing off a
#: compass rose printed in Latin letters, so the Latin form is the useful one.
#: Upper case only, and word-bounded: a case-insensitive "NE" matches inside
#: "one", and a case-insensitive "SE" inside "these".
PROTECTED_BEARINGS: tuple[str, ...] = (
    "NNE",
    "ENE",
    "ESE",
    "SSE",
    "SSW",
    "WSW",
    "WNW",
    "NNW",
    "NE",
    "SE",
    "SW",
    "NW",
)

#: Names and acronyms, matched case-insensitively because they appear both ways.
PROTECTED_NAMES: tuple[str, ...] = (
    "INCOIS",
    "ORCA",
    "IMBL",
    "CAPE",
    "IMD",
    "VHF",
    "EEZ",
    "PFZ",
)

#: Units that must survive translation verbatim, because mistranslating them
#: changes the PHYSICAL QUANTITY rather than the wording.
#:
#: Found in a real Tamil advisory: "wind = 14.7 kn (limit 22 kn)" came back as
#: "காற்று = 14.7 கிலோ மீட்டர் (22 கிலோ மீட்டருக்கு வரம்பு)" — knots rendered as
#: kilometres. Every numeral survived the guard intact and the sentence was
#: nonetheless wrong about the quantity, which is worse than a lost figure: a
#: missing number is visibly missing, and 14.7 km of wind reads as a real
#: measurement.
#:
#: **This list is deliberately narrow.** It is NOT every unit. Rendering "8.4 km"
#: as "8.4 கிமீ" or "%" in Tamil is correct, idiomatic and clearer for the reader,
#: and forcing those into Latin would make the advisory worse. What is protected
#: is the set where a wrong translation swaps one quantity for another:
#:
#: * `kn`/`kt`/`knots` — confusable with km, and that is exactly what happened;
#: * `nm` — a nautical mile, or a nanometre;
#: * compound and derived units (`m/s`, `km/h`, `J/kg`, `hPa`) which have no
#:   short vernacular form and get spelled out into something unrecognisable.
PROTECTED_UNITS: tuple[str, ...] = (
    "knots",
    "kn",
    "kt",
    "nm",
    "m/s",
    "km/h",
    "J/kg",
    "hPa",
    "degC",
)

#: A unit is only a unit when it follows a number.
#:
#: Anchored on a preceding digit rather than word-bounded, because `\bkt\b` and
#: `\bm/s\b` are safe but `\bnm\b` is not and a bare `\bkn\b` in prose would be
#: masked for no reason. "14.7 kn" is unambiguous; the word "kn" alone is noise.
#:
#: This is why terms are masked BEFORE numerals in `guarded_translate` — after
#: masking, "14.7 kn" is "NUMTOKENAXX kn" and the digit this depends on is gone.
_UNIT_RE = re.compile(
    # Two fixed-width lookbehinds rather than consuming the space. Matching
    # " kn" swallowed the separator, so the masked text read
    # "NUMTOKENAXXTRMTOKENAXX" — a 22-character alphanumeric blob that Sarvam
    # chopped, which failed the guard and sent three clauses of a real Tamil
    # advisory back to English. Leaving the space in place keeps two tokens
    # legible as two tokens, and a unit written without a space still matches.
    r"(?:(?<=[0-9])|(?<=[0-9] ))(?:" + "|".join(re.escape(u) for u in PROTECTED_UNITS) + r")\b",
    re.IGNORECASE,
)


#: Everything the guard protects, for reporting and for tests.
#:
#: Found the hard way. Asked to translate an advisory beginning "**NO-GO**",
#: Sarvam returned "**நெறிதவறிச் செல்வோருக்குத் தண்டனை**" — roughly "punishment
#: for those who go astray". Fluent, confident, and it destroyed the single word
#: the whole advisory exists to convey. The number guard had protected every
#: figure and waved the verdict straight through.
PROTECTED_TERMS: tuple[str, ...] = (
    PROTECTED_VERDICTS + PROTECTED_BEARINGS + PROTECTED_NAMES + PROTECTED_UNITS
)


#: Every character a model might use where a hyphen belongs.
#:
#: The reporting model writes "NO‑GO" with U+2011 NON-BREAKING HYPHEN about half
#: the time, which is typographically correct and completely defeated a pattern
#: written with an ASCII hyphen. The verdict then went unmasked, was translated,
#: and came back as "NO‽GO" — an interrobang in the middle of the one word the
#: whole advisory exists to convey.
#:
#: Normalising the text would be the wrong fix: the guard's contract is that what
#: comes out is byte-for-byte what went in, so the PATTERN widens and the original
#: spelling is restored verbatim.
_HYPHENS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_HYPHEN_CLASS = f"[{re.escape(_HYPHENS)}]"


def _hyphen_tolerant(term: str) -> str:
    """A pattern for `term` that accepts any hyphen variant, and a space for one.

    "NO-GO", "NO‑GO" and "NO GO" are the same verdict, and a guard that protects
    only one of the three protects nothing in practice.
    """
    parts = [re.escape(p) for p in re.split(r"[-\s]", term) if p]
    return f"(?:{_HYPHEN_CLASS}|\\s)".join(parts) if len(parts) > 1 else re.escape(term)


def _alternation(terms: tuple[str, ...]) -> str:
    """Longest first, so "NO-GO" is masked before "GO" can match inside it."""
    return "|".join(_hyphen_tolerant(t) for t in sorted(terms, key=len, reverse=True))


#: Word-bounded throughout. Without \b, "GO" matched inside "going" and "NE"
#: inside "one", masking fragments of ordinary words and leaving the remains
#: untranslatable.
_TERM_CASED_RE = re.compile(rf"\b(?:{_alternation(PROTECTED_VERDICTS + PROTECTED_BEARINGS)})\b")
_TERM_ANY_CASE_RE = re.compile(rf"\b(?:{_alternation(PROTECTED_NAMES)})\b", re.IGNORECASE)


def _iter_terms(text: str) -> list[re.Match[str]]:
    """Every protected term in the text, in position order.

    Three regexes rather than one because the matching rules differ — verdicts
    and bearings are case-sensitive, agency names are not, and units are anchored
    on a preceding digit — and they are merged here so callers see a single
    ordered stream. A later match starting inside an earlier one is dropped rather
    than double-masked.
    """
    matches = sorted(
        [
            *_TERM_CASED_RE.finditer(text),
            *_TERM_ANY_CASE_RE.finditer(text),
            *_UNIT_RE.finditer(text),
        ],
        key=lambda m: m.start(),
    )
    kept: list[re.Match[str]] = []
    end = -1
    for match in matches:
        if match.start() >= end:
            kept.append(match)
            end = match.end()
    return kept


_TERM_TOKEN = "TRMTOKEN{index}XX"
_TERM_TOKEN_RE = re.compile(r"TRMTOKEN([A-WYZ]+)XX", re.IGNORECASE)

#: The mask token. Chosen to survive translation: no spaces to be split on, and
#: a shape unlike ordinary words so a model does not "correct" it.
#:
#: The index is encoded in LETTERS, not digits. An earlier NUMTOKEN0XX form left
#: a digit in the masked text, which defeats half the point — the model is meant
#: to see no numerals at all, and a stray one is exactly the kind of thing a
#: model decides to tidy up.
_TOKEN = "NUMTOKEN{index}XX"
_TOKEN_RE = re.compile(r"NUMTOKEN([A-WYZ]+)XX", re.IGNORECASE)


#: The index alphabet, deliberately WITHOUT the letter X.
#:
#: The tokens end in "XX", so an index that could itself contain an X makes the
#: terminator ambiguous. This is not theoretical: with a 26-letter alphabet,
#: index 23 encodes to "X" and the token becomes NUMTOKENXXX — at which point the
#: pattern can no longer tell where the index stops.
#:
#: The failure it actually caused was worse and did not need an X at all. Two
#: tokens ending up adjacent, as they do when a unit immediately follows a
#: numeral, produced "NUMTOKENAXXTRMTOKENAXX"; the greedy index group swallowed
#: "AXXTRMTOKENA", the whole run matched as one token with an out-of-range index,
#: and the replacement silently left it in place. The numeral was unrecoverable
#: and the advisory shipped with a mask token where a wave height should have
#: been. Excluding X from the alphabet lets the pattern stop at the first "XX",
#: which fixes both.
_INDEX_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWYZ"
_INDEX_BASE = len(_INDEX_ALPHABET)


def _encode_index(index: int) -> str:
    """Bijective base-25 over :data:`_INDEX_ALPHABET`. 0 -> A, 1 -> B, ... 24 -> Z, 25 -> AA."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, _INDEX_BASE)
        letters = _INDEX_ALPHABET[remainder] + letters
    return letters


def _decode_index(letters: str) -> int:
    value = 0
    for char in letters.upper():
        position = _INDEX_ALPHABET.find(char)
        if position < 0:
            # Not one of ours. Signal out of range so the caller leaves the text
            # alone rather than substituting whatever happens to be at index 0.
            return -1
        value = value * _INDEX_BASE + position + 1
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

    pieces: list[str] = []
    cursor = 0
    for match in _iter_terms(text):
        originals.append(match.group(0))
        pieces.append(text[cursor : match.start()])
        pieces.append(_TERM_TOKEN.format(index=_encode_index(len(originals) - 1)))
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces), originals


#: Double quotes only. An apostrophe is a legitimate character in English text
#: and removing one would corrupt a clause that was deliberately left in the
#: source language; the observed artefact is always a double quote.
_ALIEN_QUOTES = '"“”'


def strip_alien_quotes(text: str, *, source: str) -> str:
    """Remove double quotes the translator inserted mid-token.

    Sarvam wedges a double quote at boundaries it finds unusual — between a
    figure and its unit, and inside an all-caps verdict. A single Tamil advisory
    came back with ``21.4"kn``, ``100"%``, ``2"km`` and, worst of all,
    ``NO"GO``: every figure correct, every one of them unreadable, and the
    verdict word broken in a way that also defeats the term check.

    Two conditions before anything is removed, so a real quotation survives:

    * the quote character does not appear in the source at all, and
    * it sits between two non-space characters.

    Checked per character rather than for quotes in general. An earlier version
    bailed out if the source contained any quote-like character, and since that
    set included the apostrophe — which turns up in ordinary English prose all
    the time — the cleanup almost never ran.
    """
    alien = "".join(q for q in _ALIEN_QUOTES if q not in source)
    if not alien:
        return text
    pattern = re.compile(rf"(?<=\S)[{re.escape(alien)}](?=\S)")
    return pattern.sub("", text)


def unmask_terms(text: str, originals: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        index = _decode_index(match.group(1))
        if not 0 <= index < len(originals):
            return match.group(0)
        return originals[index]

    return _TERM_TOKEN_RE.sub(replace, text)


def missing_terms(source: str, output: str) -> list[str]:
    """Protected terms present in the source and absent from the output.

    Stripped, because the unit pattern captures the space before a unit (it is
    anchored on the preceding digit) and a report reading " KN" rather than "KN"
    ends up on screen in front of a user.
    """
    present = {m.group(0).strip().upper() for m in _iter_terms(output)}
    wanted = {m.group(0).strip().upper() for m in _iter_terms(source)}
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
    source_terms = [m.group(0) for m in _iter_terms(text)]

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
    direct = strip_alien_quotes(direct, source=text)
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

    # Terms FIRST, then numerals. The unit pattern is anchored on a preceding
    # digit, and once numbers are masked "14.7 kn" reads "NUMTOKENAXX kn" with no
    # digit left to anchor on — so masking numbers first silently disables unit
    # protection entirely. The token forms carry no digits (their indices are
    # encoded as letters, deliberately), so this order is safe in reverse too.
    masked, terms = mask_terms(text)
    masked, originals = mask_numbers(masked)
    masked_translation = await translate(masked, **translate_kwargs)
    masked_translation = strip_alien_quotes(masked_translation, source=text)
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

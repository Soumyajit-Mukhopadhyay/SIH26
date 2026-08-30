"""The language plane, and above all the number-integrity guard.

The plan's stated test: *"Tamil in → Tamil out with every numeral intact; guard
unit test catches an injected mangle."* Both are here. The injected-mangle test
is the important one — it proves the guard catches a translator that quietly
changes 2.4 to 2.5, which is the failure mode that would otherwise ship.
"""

from __future__ import annotations

import pytest

from orca.language import guard
from orca.language.detect import detect, roster, script_histogram
from orca.language.guard import (
    PROTECTED_TERMS,
    extract_numbers,
    guarded_translate,
    mask_numbers,
    mask_terms,
    missing_terms,
    to_latin_digits,
    to_script_digits,
    unmask_numbers,
    unmask_terms,
    verify,
)


class TestNumberExtraction:
    def test_plain_numbers(self):
        assert extract_numbers("wave height 2.4 metres, wind 26 kn") == ["2.4", "26"]

    def test_tamil_digits_normalise_to_ascii(self):
        # The whole point: the same value written in two scripts must compare equal.
        assert to_latin_digits("௨.௪") == "2.4"
        assert extract_numbers("அலை உயரம் ௨.௪ மீட்டர்") == ["2.4"]

    @pytest.mark.parametrize(
        ("script", "expected"),
        [("devanagari", "२.४"), ("bengali", "২.৪"), ("tamil", "௨.௪"), ("latin", "2.4")],
    )
    def test_rendering_into_each_script(self, script, expected):
        assert to_script_digits("2.4", script) == expected

    def test_formatting_differences_are_not_value_differences(self):
        # 2.40 and 2.4 are the same number; 2,400 and 2400 are the same number.
        assert extract_numbers("2.40 m") == extract_numbers("2.4 m")
        assert extract_numbers("2,400 m") == extract_numbers("2400 m")

    def test_no_numbers_is_an_empty_list(self):
        assert extract_numbers("the sea is calm today") == []


class TestVerify:
    def test_an_intact_translation_passes(self):
        result = verify("wave height 2.4 m", "அலை உயரம் 2.4 மீ")
        assert result.ok
        assert result.lost == []

    def test_an_injected_mangle_is_caught(self):
        # THE test. A translator that renders 2.4 as 2.5 is fluent, plausible,
        # and wrong about the only thing that matters.
        result = verify("wave height 2.4 m", "அலை உயரம் 2.5 மீ")
        assert not result.ok
        assert result.lost == ["2.4"]
        assert result.invented == ["2.5"]

    def test_a_dropped_number_is_caught(self):
        result = verify("Hs 2.4 m, wind 26 kn", "அலை உயரம் 2.4 மீ")
        assert not result.ok
        assert result.lost == ["26"]

    def test_duplicates_are_compared_as_a_multiset(self):
        # Losing one of two identical figures is a real defect that a set
        # comparison would call fine.
        result = verify("2.4 m swell and 2.4 m wind sea", "2.4 m only")
        assert not result.ok
        assert result.lost == ["2.4"]

    def test_a_script_change_alone_still_passes(self):
        result = verify("wave height 2.4 m", "அலை உயரம் ௨.௪ மீ")
        assert result.ok


class TestMasking:
    def test_masking_removes_every_digit(self):
        # Not one digit may survive: the token index is encoded in letters
        # precisely so the model sees no numeral it might tidy up.
        masked, originals = mask_numbers("Hs 2.4 m, wind 26 kn, visibility 6 km")
        assert originals == ["2.4", "26", "6"]
        assert not any(char.isdigit() for char in masked), masked

    def test_round_trip_is_exact(self):
        text = "Hs 2.4 m, wind 26 kn"
        masked, originals = mask_numbers(text)
        assert unmask_numbers(masked, originals) == text

    def test_round_trip_survives_reordering(self):
        # Translation legitimately reorders clauses; the tokens must still land
        # on the right values.
        masked, originals = mask_numbers("Hs 2.4 m and wind 26 kn")
        reordered = masked.replace("Hs ", "").split(" and ")
        restored = unmask_numbers(" / ".join(reversed(reordered)), originals)
        assert "2.4" in restored
        assert "26" in restored

    def test_unmasking_into_a_target_script(self):
        masked, originals = mask_numbers("wave 2.4 m")
        assert "௨.௪" in unmask_numbers(masked, originals, script="tamil")


class TestGuardedTranslate:
    async def test_a_clean_translator_takes_the_direct_path(self):
        async def clean(text: str) -> str:
            return text.replace("wave height", "அலை உயரம்")

        result = await guarded_translate("wave height 2.4 m", translate=clean)
        assert result.ok
        assert result.strategy == "direct"
        assert result.attempts == 1

    async def test_a_mangling_translator_is_caught_and_worked_around(self):
        """A translator that rounds numbers gets bypassed by masking."""
        calls: list[str] = []

        async def mangler(text: str) -> str:
            calls.append(text)
            # Rounds any decimal it sees — but cannot touch what it cannot see.
            return text.replace("2.4", "2.5")

        result = await guarded_translate("wave height 2.4 m", translate=mangler)
        assert result.ok, "masking should have protected the numeral"
        assert result.strategy == "masked"
        assert result.attempts == 2
        assert "2.4" in result.text
        assert "2.5" not in result.text
        # It really did try direct first, then masked.
        assert len(calls) == 2
        assert "2.4" in calls[0] and "2.4" not in calls[1]

    async def test_a_translator_that_destroys_tokens_fails_loudly(self):
        """The worst case: even masking cannot save it, so ORCA must refuse."""

        async def destroyer(text: str) -> str:
            import re

            # Eats the mask tokens themselves, and mangles any bare numeral.
            return re.sub(r"NUMTOKEN[A-Z]+XX", "some", text, flags=re.IGNORECASE).replace(
                "2.4", "9.9"
            )

        result = await guarded_translate("wave height 2.4 m", translate=destroyer)
        assert not result.ok
        assert result.strategy == "failed"
        # The caller must be told not to speak it.
        assert "must NOT be spoken" in result.note

    async def test_text_without_numbers_skips_the_guard(self):
        async def clean(text: str) -> str:
            return "கடல் அமைதியாக உள்ளது"

        result = await guarded_translate("the sea is calm", translate=clean)
        assert result.ok
        assert result.strategy == "no-numbers"

    async def test_a_full_advisory_keeps_every_figure(self):
        advisory = (
            "NO-GO. Wave height 2.64 m is over the 1.5 m limit for your 8.2 m boat. "
            "Wind 22.3 kn, visibility 18.3 km, lightning 72.7 percent."
        )

        async def sloppy(text: str) -> str:
            return text.replace("2.64", "2.6").replace("72.7", "73")

        result = await guarded_translate(advisory, translate=sloppy)
        assert result.ok
        for figure in ("2.64", "1.5", "8.2", "22.3", "18.3", "72.7"):
            assert figure in result.text, f"lost {figure}"


class TestDetection:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("நாளை காலை கடலுக்குப் போவது பாதுகாப்பானதா?", "ta"),
            ("നാളെ കടലിൽ പോകുന്നത് സുരക്ഷിതമാണോ?", "ml"),
            ("રેપો સમુદ્ર સલામત છે?", "gu"),
            ("সমুদ্র কি নিরাপদ?", "bn"),
            ("ସମୁଦ୍ର ନିରାପଦ କି?", "or"),
            ("సముద్రం సురక్షితమేనా?", "te"),
            ("ಸಮುದ್ರ ಸುರಕ್ಷಿತವೇ?", "kn"),
            ("Is it safe to go to sea tomorrow?", "en"),
        ],
    )
    def test_unambiguous_scripts(self, text, expected):
        result = detect(text)
        assert result.language == expected
        assert result.is_confident

    def test_devanagari_is_disambiguated_by_function_words(self):
        marathi = detect("उद्या समुद्र सुरक्षित आहे का?")
        hindi = detect("क्या कल समुद्र सुरक्षित है?")
        assert marathi.language == "mr"
        assert hindi.language == "hi"
        assert marathi.method == "devanagari-function-words"

    def test_ambiguous_devanagari_admits_it(self):
        # "समुद्र" (sea) is shared by Hindi and Marathi, so it identifies the
        # script and not the language. ORCA must default to Hindi with LOW
        # confidence and offer the alternatives, rather than answering a Konkani
        # speaker in Hindi as though certain.
        result = detect("समुद्र")
        assert result.language == "hi"
        assert not result.is_confident
        assert "mr" in result.alternatives and "kok" in result.alternatives

    def test_romanised_tamil_is_not_mistaken_for_english(self):
        # A phone keyboard is how this actually arrives.
        result = detect("kadal safe ah? meen pidikka poga mudiyuma")
        assert result.language == "ta"
        assert result.method == "romanised-markers"

    def test_a_single_weak_marker_stays_english_but_flags_it(self):
        result = detect("the nav system is broken")
        assert result.language == "en"
        assert result.alternatives

    def test_script_histogram_counts_letters_only(self):
        # 3, not 4: the Tamil pulli is a combining mark rather than a letter, and
        # digits and spaces are excluded.
        counts = script_histogram("கடல் 2.4 m")
        assert counts.get("tamil", 0) == 3
        assert counts.get("latin", 0) == 1


class TestRoster:
    def test_all_nine_coastal_languages_are_present(self):
        codes = {row["code"] for row in roster()}
        for expected in ("gu", "mr", "kok", "kn", "ml", "ta", "te", "or", "bn"):
            assert expected in codes

    def test_the_konkani_tts_gap_is_declared(self):
        konkani = next(row for row in roster() if row["code"] == "kok")
        assert konkani["tts"] is False
        assert "no TTS voice" in konkani["tts_note"]

    def test_every_other_language_claims_tts(self):
        for row in roster():
            if row["code"] != "kok":
                assert row["tts"] is True


class TestProtectedTerms:
    """The verdict word must survive translation, exactly as the numerals must.

    Found in production: asked to translate an advisory beginning "**NO-GO**",
    Sarvam returned "**நெறிதவறிச் செல்வோருக்குத் தண்டனை**" — roughly "punishment
    for those who go astray". Every figure was protected and the one word the
    advisory exists to convey was destroyed.
    """

    def test_the_verdict_words_are_protected(self):
        for verdict in ("NO-GO", "CAUTION", "UNVERIFIABLE", "GO"):
            assert verdict in PROTECTED_TERMS

    def test_a_lost_verdict_is_detected(self):
        result = verify(
            "**NO-GO** Wave height 2.4 m.",
            "**நெறிதவறிச் செல்வோருக்குத் தண்டனை** அலை உயரம் 2.4 மீ.",
        )
        # The numerals survived, so a number-only guard would have passed this.
        assert result.lost == []
        assert result.lost_terms == ["NO-GO"]
        assert not result.ok, "the guard must fail when the verdict word is gone"

    def test_a_preserved_verdict_passes(self):
        result = verify("**NO-GO** Wave height 2.4 m.", "**NO-GO** அலை உயரம் 2.4 மீ.")
        assert result.ok
        assert result.lost_terms == []

    def test_masking_hides_the_term_from_the_translator(self):
        masked, terms = mask_terms("NO-GO. Confirm with IMD or INCOIS.")
        assert terms == ["NO-GO", "IMD", "INCOIS"]
        for term in ("NO-GO", "IMD", "INCOIS"):
            assert term not in masked

    def test_masking_round_trips_exactly(self):
        text = "NO-GO. Confirm with IMD or INCOIS on VHF."
        masked, terms = mask_terms(text)
        assert unmask_terms(masked, terms) == text

    def test_longer_terms_mask_before_shorter_ones(self):
        # "GO" is a substring of "NO-GO"; masking it first would corrupt both.
        masked, terms = mask_terms("NO-GO")
        assert terms == ["NO-GO"]
        assert unmask_terms(masked, terms) == "NO-GO"

    def test_missing_terms_is_case_insensitive(self):
        assert missing_terms("Confirm with IMD.", "imd உடன் சரிபார்க்கவும்.") == []

    async def test_a_translator_that_mangles_the_verdict_is_worked_around(self):
        async def mangler(text: str) -> str:
            # Destroys the verdict but leaves numbers alone — the exact failure
            # a number-only guard let through.
            return text.replace("NO-GO", "punishment for those who go astray")

        result = await guarded_translate("NO-GO. Wave height 2.4 m.", translate=mangler)
        assert result.ok, "masking should have protected the verdict"
        assert result.strategy == "masked"
        assert "NO-GO" in result.text

    async def test_a_verdict_only_advisory_is_still_guarded(self):
        # No numerals at all, so the guard must still engage on the term.
        async def mangler(text: str) -> str:
            return text.replace("CAUTION", "be a bit careful maybe")

        result = await guarded_translate("CAUTION advised.", translate=mangler)
        assert result.strategy == "masked"
        assert "CAUTION" in result.text


# --- protected terms: word boundaries and case rules -------------------------
#
# These pin behaviour found by reading a real Tamil advisory on screen, not by
# reasoning about the regex.


def test_lowercase_go_is_not_masked() -> None:
    """The ordinary English word "go" must not be protected.

    Case-insensitive matching on "GO" masked every "go" in prose, which left
    English words scattered through a Tamil advisory. The rule engine always
    emits the verdict in caps, so upper case is the discriminator.
    """
    masked, terms = guard.mask_terms("you can go out, but going far is unwise")
    assert terms == []
    assert masked == "you can go out, but going far is unwise"


def test_uppercase_verdicts_are_masked() -> None:
    masked, terms = guard.mask_terms("**NO-GO** now, CAUTION later, GO tomorrow")
    assert terms == ["NO-GO", "CAUTION", "GO"]
    assert "NO-GO" not in masked
    assert guard.unmask_terms(masked, terms) == "**NO-GO** now, CAUTION later, GO tomorrow"


def test_bearings_survive_but_do_not_match_inside_words() -> None:
    """ "SSW" is kept in Latin; "ne" inside "one" is not a bearing.

    Sarvam spelled SSW out phonetically letter by letter. A fisherman reads a
    bearing off a compass rose printed in Latin, so the Latin form is protected —
    but an unbounded case-insensitive "NE" matches inside "one" and "SE" inside
    "these", which would mask fragments of ordinary words.
    """
    masked, terms = guard.mask_terms("one zone 713 km SSW and these waters NE of it")
    assert terms == ["SSW", "NE"]
    assert masked.startswith("one zone 713 km ")
    assert "these waters" in masked


def test_agency_names_are_case_insensitive() -> None:
    _, terms = guard.mask_terms("per IMD and incois, via Incois")
    assert [t.upper() for t in terms] == ["IMD", "INCOIS", "INCOIS"]


def test_bearing_lost_in_translation_is_reported() -> None:
    result = guard.verify("Zone 713 km SSW of you", "உங்களிடமிருந்து 713 கிமீ எஸ்எஸ்டபிள்யூ")
    assert result.lost_terms == ["SSW"]
    assert result.ok is False


def test_translator_quotes_around_a_mask_token_are_stripped() -> None:
    """Sarvam wrapped a mask token in quotes, so "47.2/100" came back as
    `47.2"/"100` — figures correct, punctuation invented."""
    got = guard.strip_alien_quotes('NUMTOKENAXX"/"NUMTOKENBXX', source="confidence 47.2/100")
    assert got == "NUMTOKENAXX/NUMTOKENBXX"


def test_a_real_quotation_mark_is_not_stripped() -> None:
    """Losing a genuine quote to tidy up a cosmetic one is the wrong trade."""
    quoted = 'he said "47.2/100"'
    got = guard.strip_alien_quotes('NUMTOKENAXX"/"NUMTOKENBXX', source=quoted)
    assert got == 'NUMTOKENAXX"/"NUMTOKENBXX'


def test_a_quote_wedged_between_a_figure_and_its_unit_is_removed() -> None:
    """Sarvam wedges a double quote where it finds a boundary unusual.

    One Tamil advisory came back with `21.4"kn`, `100"%`, `2"km` and `NO"GO` —
    every figure correct and every one of them unreadable, with the verdict word
    broken in a way that also defeats the term check.
    """
    got = guard.strip_alien_quotes(
        'காற்று 21.4"kn, மின்னல் 100"%, NO"GO', source="wind 21.4 kn, lightning 100 %, NO-GO"
    )
    assert got == "காற்று 21.4kn, மின்னல் 100%, NOGO"


def test_an_apostrophe_is_never_stripped() -> None:
    """The earlier version treated the apostrophe as a quote, and since ordinary
    English prose is full of them the cleanup almost never ran at all."""
    text = "the vessel's limit is 1.5 m"
    assert guard.strip_alien_quotes(text, source="the vessel's limit is 1.5 m") == text

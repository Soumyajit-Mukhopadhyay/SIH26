"""The harbour advisory board: the register, the ordering, and the stretches.

The board is read by an officer who will broadcast it. The failure that matters
is not a crash — it is a stretch that reads as contiguous coast when it is not,
or a harbour whose position drifted inland and now reports the weather somewhere
else entirely.
"""

from __future__ import annotations

import pytest

from orca.services import harbourboard
from orca.services.harbours import HARBOURS, UNRESOLVED
from orca.services.risk_engine import assess
from orca.services.thresholds import BOAT_CLASSES, by_code


def row(name: str, coast: str, verdicts: dict[str, str], *, wave: float | None = 1.0):
    """A HarbourVerdicts with the verdicts forced, for testing `stretches`."""
    from orca.services.harbours import Harbour

    results = {}
    for code, verdict in verdicts.items():
        # Drive the real engine to the wanted verdict rather than faking a
        # RiskResult: the shape of a real result is part of what is under test.
        boat = by_code(code)
        assert boat is not None
        wave_for = {"GO": 0.1, "CAUTION": boat.max_wave_m * 0.9, "NO-GO": boat.max_wave_m + 1.0}[
            verdict
        ]
        result = assess(
            wave_m=wave_for, wind_kn=1.0, visibility_km=20.0, cape_j_kg=0.0, boat_class=boat
        )
        assert result.verdict == verdict, f"could not drive {code} to {verdict}"
        results[code] = result

    return harbourboard.HarbourVerdicts(
        harbour=Harbour(name, "d", "s", 10.0, 75.0, coast),
        wave_m=wave,
        wind_kn=5.0,
        visibility_km=20.0,
        cape_j_kg=100.0,
        verdicts=results,
    )


class TestRegister:
    def test_every_harbour_is_in_indian_waters(self) -> None:
        """Catches a transposed coordinate, the likeliest error in a geocoded
        table and one that would silently report Somalia's weather."""
        for harbour in HARBOURS:
            assert 5.0 <= harbour.lat <= 25.0, f"{harbour.name} latitude {harbour.lat}"
            assert 68.0 <= harbour.lon <= 93.0, f"{harbour.name} longitude {harbour.lon}"

    def test_coast_matches_the_side_of_the_country(self) -> None:
        """A west-coast harbour east of 80 E, or an east-coast one west of 70 E,
        is a mislabelled record — and `stretches` uses coast to decide where a
        run breaks, so a wrong side merges two coasts into one advisory."""
        for harbour in HARBOURS:
            if harbour.coast == "west":
                assert harbour.lon < 80.0, f"{harbour.name} is west coast at {harbour.lon} E"
            else:
                assert harbour.lon > 76.0, f"{harbour.name} is east coast at {harbour.lon} E"

    def test_harbours_are_stored_in_coastal_order(self) -> None:
        """West coast descends in latitude (Kutch to Kanyakumari), then the east
        coast ascends. Alphabetical or plain-latitude order would put Porbandar
        next to Digha, which are on opposite coasts a thousand km apart."""
        west = [h for h in HARBOURS if h.coast == "west"]
        east = [h for h in HARBOURS if h.coast == "east"]

        # The west block must come first and be unbroken.
        assert [h.coast for h in HARBOURS] == ["west"] * len(west) + ["east"] * len(east)

        latitudes = [h.lat for h in west]
        assert latitudes == sorted(latitudes, reverse=True)

        # The east coast ascends as far as Odisha; above 21 N the Bengal shore
        # turns and runs west to east, so longitude takes over there.
        below = [h.lat for h in east if h.lat < 21.0]
        assert below == sorted(below)

    def test_no_two_harbours_share_a_position(self) -> None:
        seen = {(round(h.lat, 3), round(h.lon, 3)) for h in HARBOURS}
        assert len(seen) == len(HARBOURS)

    def test_unresolved_harbours_are_recorded_not_dropped(self) -> None:
        """Three PMMSY harbours could not be geocoded. Recording them is what
        stops the register reading as complete."""
        assert set(UNRESOLVED) == {"Navabandar", "Mudhunagar", "Azhagankuppam"}

    def test_a_proxy_position_says_so(self) -> None:
        """Five harbours sit at a nearby place Nominatim knows instead. The name
        must not imply a precision the coordinate does not have."""
        proxies = [h for h in HARBOURS if h.position_proxy]
        assert len(proxies) == 5
        for harbour in proxies:
            assert harbour.position_proxy and len(harbour.position_proxy) > 10


class TestStretches:
    def test_adjacent_harbours_with_the_same_verdict_become_one_stretch(self) -> None:
        """The whole point. Four separate NO-GO rows are not an advisory; "NO-GO
        from A to D" is."""
        rows = [
            row("A", "west", {"IND-TRAD": "NO-GO"}),
            row("B", "west", {"IND-TRAD": "NO-GO"}),
            row("C", "west", {"IND-TRAD": "NO-GO"}),
            row("D", "west", {"IND-TRAD": "GO"}),
        ]
        runs = harbourboard.stretches(rows, "IND-TRAD")
        assert len(runs) == 2
        assert runs[0]["from"] == "A" and runs[0]["to"] == "C"
        assert "from A to C" in runs[0]["sentence"]

    def test_a_stretch_never_spans_both_coasts(self) -> None:
        """Kanyakumari is where the advisory breaks. A run that jumped from the
        Kerala coast to the Tamil Nadu coast would tell an officer that a swell
        on one side applies to the other."""
        rows = [
            row("Kerala end", "west", {"IND-TRAD": "NO-GO"}),
            row("TN start", "east", {"IND-TRAD": "NO-GO"}),
        ]
        runs = harbourboard.stretches(rows, "IND-TRAD")
        assert len(runs) == 2

    def test_a_single_harbour_stretch_is_kept(self) -> None:
        """An isolated NO-GO is usually a harbour with a bar or a shallow
        approach — exactly the case worth knowing, not noise to smooth away."""
        rows = [
            row("A", "west", {"IND-TRAD": "GO"}),
            row("B", "west", {"IND-TRAD": "NO-GO"}),
            row("C", "west", {"IND-TRAD": "GO"}),
        ]
        runs = harbourboard.stretches(rows, "IND-TRAD")
        assert len(runs) == 3
        assert runs[1]["sentence"] == "NO-GO at B"

    def test_a_harbour_the_model_did_not_answer_for_is_left_out(self) -> None:
        """A null wave height means no sea cell was reachable. Including it would
        break a stretch in two on the strength of a registry problem rather than
        the weather."""
        rows = [
            row("A", "west", {"IND-TRAD": "NO-GO"}),
            row("B", "west", {"IND-TRAD": "NO-GO"}, wave=None),
            row("C", "west", {"IND-TRAD": "NO-GO"}),
        ]
        runs = harbourboard.stretches(rows, "IND-TRAD")
        assert len(runs) == 1
        assert runs[0]["harbours"] == ["A", "C"]


class TestTheBoardIsJustTheRuleEngine:
    def test_no_new_thresholds_were_invented(self) -> None:
        """The board's value is that it is the SAME engine. If it grew its own
        limits they would drift from the point verdict, and a fisherman and
        their officer would be reading different rules."""
        import inspect

        source = inspect.getsource(harbourboard)
        assert "max_wave_m =" not in source
        assert "max_wind_kn =" not in source
        assert "from orca.services.risk_engine import" in source

    @pytest.mark.parametrize("boat", BOAT_CLASSES, ids=[b.code for b in BOAT_CLASSES])
    def test_every_class_is_assessable(self, boat) -> None:
        result = assess(
            wave_m=1.2, wind_kn=18.0, visibility_km=8.0, cape_j_kg=500.0, boat_class=boat
        )
        assert result.verdict in ("GO", "CAUTION", "NO-GO")
        assert result.verdict_source == "rule_engine"

    def test_the_severity_order_covers_every_verdict_the_engine_emits(self) -> None:
        """`worst` sorts on this map. A verdict missing from it would sort as
        UNVERIFIABLE and could hide a NO-GO."""
        assert set(harbourboard._SEVERITY) == {"NO-GO", "UNVERIFIABLE", "CAUTION", "GO"}

    def test_worst_picks_the_most_severe_class(self) -> None:
        entry = row("A", "west", {"IND-TRAD": "NO-GO", "IND-DEEPSEA": "GO"})
        assert entry.worst == "NO-GO"

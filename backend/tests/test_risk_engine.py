"""The deterministic core's regression tests.

The headline case is the blueprint's own worked example (D.3, the Tamil query
from an 8.2 m boat at Kasimedu). Note the discrepancy, deliberately preserved:

    The blueprint states "index = 25/100". Its own formula gives 22.0:
        wave  = max(0, 100 x (1 - (2.4/1.5)^2)) = 0
        wind  = max(0, 100 x (1 - (26/22)^2))   = 0
        vis   = min(100, 6/10 x 100)            = 60
        light = max(0, 100 - 35)                = 65
        index = 0.35(0) + 0.30(0) + 0.15(60) + 0.20(65) = 0 + 0 + 9 + 13 = 22.0

    The verdict (NO-GO) and the veto count (2) match the blueprint exactly; only
    the stated index does not. We implement the formula and assert 22.0 rather
    than bending the weights to reproduce a number the source's own arithmetic
    contradicts.
"""

from __future__ import annotations

import itertools

import pytest

from orca.provenance import Evidence, Freshness, Provenance, Provider, utcnow
from orca.services import risk_engine
from orca.services.risk_engine import assess, assess_from_evidence, cape_to_lightning_pct
from orca.services.thresholds import BOAT_CLASSES, THRESHOLDS_VERSION, by_code, classify


class TestBlueprintWorkedExample:
    """8.2 m boat, Hs 2.4 m, wind 26 kn, visibility 6 km, lightning 35%."""

    @pytest.fixture
    def result(self):
        return assess(
            wave_m=2.4,
            wind_kn=26.0,
            visibility_km=6.0,
            lightning_pct=35.0,
            loa_m=8.2,
            data_age_hours=1.0,
        )

    def test_verdict_is_no_go(self, result):
        assert result.verdict == "NO-GO"

    def test_index_is_22_not_the_blueprints_stated_25(self, result):
        # See the module docstring. 0.15(60) + 0.20(65) = 22.0.
        assert result.index == 22.0

    def test_there_are_exactly_two_vetoes(self, result):
        assert len(result.vetoes) == 2

    def test_the_vetoes_name_the_value_the_limit_and_the_boat(self, result):
        wave_veto = next(v for v in result.vetoes if "Hs" in v)
        assert "2.4 m" in wave_veto
        assert "1.5 m" in wave_veto
        assert "8.2 m boat" in wave_veto

    def test_component_scores_match_the_blueprint(self, result):
        scores = {c.name: c.score for c in result.components}
        assert scores == {"wave": 0.0, "wind": 0.0, "visibility": 60.0, "lightning": 65.0}

    def test_the_boat_is_classified_as_a_motorised_frp_boat(self, result):
        assert result.boat_class_code == "IND-MOT-S"

    def test_the_arithmetic_is_shown_not_just_the_answer(self, result):
        wave = next(c for c in result.components if c.name == "wave")
        assert "2.4" in wave.formula and "1.5" in wave.formula

    def test_it_says_what_would_change_the_verdict(self, result):
        joined = " ".join(result.what_would_change_it)
        assert "1.5 m" in joined
        assert "22 kn" in joined

    def test_the_verdict_records_its_thresholds_version(self, result):
        assert result.thresholds_version == THRESHOLDS_VERSION


class TestVerdictBoundaries:
    def _calm(self, **kw):
        base = {
            "wave_m": 0.4,
            "wind_kn": 8.0,
            "visibility_km": 10.0,
            "lightning_pct": 0.0,
            "loa_m": 8.2,
        }
        return assess(**{**base, **kw})

    def test_calm_conditions_are_a_go(self):
        result = self._calm()
        assert result.verdict == "GO"
        assert result.vetoes == []
        assert result.index >= 70

    def test_a_single_veto_overrides_an_otherwise_perfect_index(self):
        # Everything benign except the wave, which is at the class limit. The
        # blended score must not be allowed to rescue it.
        result = self._calm(wave_m=1.5)
        assert result.verdict == "NO-GO"
        assert len(result.vetoes) == 1

    def test_the_limit_is_inclusive(self):
        # "At or over" — exactly at the limit is already a veto.
        assert assess(
            wave_m=1.5, wind_kn=5.0, visibility_km=10.0, lightning_pct=0.0, loa_m=8.2
        ).vetoes
        assert not assess(
            wave_m=1.49, wind_kn=5.0, visibility_km=10.0, lightning_pct=0.0, loa_m=8.2
        ).vetoes

    def test_lightning_vetoes_regardless_of_boat_size(self):
        for boat in BOAT_CLASSES:
            result = assess(
                wave_m=0.2,
                wind_kn=5.0,
                visibility_km=10.0,
                lightning_pct=75.0,
                loa_m=boat.loa_min_m + 0.5,
            )
            assert result.verdict == "NO-GO", f"{boat.code} should be vetoed by lightning"

    def test_the_same_conditions_can_be_go_for_a_trawler_and_no_go_for_a_canoe(self):
        conditions = {
            "wave_m": 1.2,
            "wind_kn": 18.0,
            "visibility_km": 10.0,
            "lightning_pct": 5.0,
        }
        canoe = assess(**conditions, loa_m=5.0)
        trawler = assess(**conditions, loa_m=20.0)
        assert canoe.verdict == "NO-GO"
        assert trawler.verdict == "GO"


class TestRefusalToGuess:
    def test_missing_wave_data_is_unverifiable_not_calm(self):
        # The whole point: "we could not measure it" must never render as safe.
        result = assess(wave_m=None, wind_kn=8.0, visibility_km=10.0, lightning_pct=0.0)
        assert result.verdict == "UNVERIFIABLE"
        assert result.escalate is True
        assert "wave height" in result.escalation_message

    def test_stale_data_drops_confidence_and_escalates(self):
        result = assess(
            wave_m=0.4,
            wind_kn=8.0,
            visibility_km=10.0,
            lightning_pct=0.0,
            data_age_hours=9.0,
        )
        assert result.confidence == "low"
        assert result.escalate is True
        assert "9.0 h old" in result.escalation_message

    def test_escalation_tells_the_user_where_to_go_instead(self):
        result = assess(wave_m=None, wind_kn=None, visibility_km=None)
        assert "fisheries office" in result.escalation_message
        assert "VHF" in result.escalation_message

    def test_simulated_evidence_cannot_support_a_confident_verdict(self):
        simulated = Evidence(
            dataset_id="orca:demo-fleet",
            provider=Provider.ORCA,
            variable="wave_height",
            value=0.4,
            unit="m",
            provenance=Provenance.SIMULATED,
            freshness=Freshness.of("wave_height", utcnow()),
        )
        result = assess(
            wave_m=0.4,
            wind_kn=8.0,
            visibility_km=10.0,
            lightning_pct=0.0,
            evidence=[simulated],
        )
        assert result.confidence == "low"
        assert result.escalate is True
        assert "SIMULATED" in result.escalation_message


class TestCapeProxy:
    @pytest.mark.parametrize(
        ("cape", "expected"),
        [(0, 0.0), (300, 0.0), (2500, 100.0), (4000, 100.0)],
    )
    def test_endpoints(self, cape, expected):
        assert cape_to_lightning_pct(cape) == expected

    def test_midrange_is_monotonic(self):
        values = [cape_to_lightning_pct(c) for c in (400, 900, 1400, 2000, 2400)]
        assert values == sorted(values)

    def test_strong_instability_vetoes(self):
        result = assess(wave_m=0.3, wind_kn=6.0, visibility_km=10.0, cape_j_kg=3000.0, loa_m=8.2)
        assert result.verdict == "NO-GO"
        assert any("lightning" in v for v in result.vetoes)


class TestEvidenceIntegration:
    def test_assess_from_evidence_converts_visibility_metres_to_km(self):
        def ev(variable, value, unit):
            return Evidence(
                dataset_id="open_meteo.test",
                provider=Provider.OPEN_METEO,
                variable=variable,
                value=value,
                unit=unit,
                provenance=Provenance.LIVE,
                freshness=Freshness.of(variable, utcnow()),
            )

        result = assess_from_evidence(
            {
                "wave_height": ev("wave_height", 2.4, "m"),
                "wind_speed": ev("wind_speed", 26.0, "kn"),
                "visibility": ev("visibility", 6000.0, "m"),  # metres in, km expected
                "convective_energy": ev("convective_energy", 1070.0, "J/kg"),
            },
            loa_m=8.2,
        )
        vis = next(c for c in result.components if c.name == "visibility")
        assert vis.value == 6.0
        assert result.verdict == "NO-GO"

    def test_unavailable_evidence_is_not_treated_as_a_reading(self):
        result = assess_from_evidence(
            {
                "wave_height": Evidence.unavailable(
                    dataset_id="x",
                    provider=Provider.OPEN_METEO,
                    variable="wave_height",
                    reason="source down",
                )
            },
            loa_m=8.2,
        )
        assert result.verdict == "UNVERIFIABLE"


class TestArchitecturalBoundary:
    def test_services_never_import_agents(self):
        """The line between the deterministic core and the LLM plane is drawn in
        the code, not only in the diagram."""
        import pkgutil
        from pathlib import Path

        import orca.services

        root = Path(orca.services.__file__).parent
        offenders = []
        for module in pkgutil.iter_modules([str(root)]):
            text = (root / f"{module.name}.py").read_text(encoding="utf-8")
            if "orca.agents" in text or "from orca import agents" in text:
                offenders.append(module.name)
        assert not offenders, f"services/ must not import agents/: {offenders}"

    def test_verdict_source_cannot_be_an_llm(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            risk_engine.RiskResult(
                verdict="GO",
                verdict_source="llm",  # type: ignore[arg-type]
                index=80,
                vetoes=[],
                components=[],
                boat_class_code="IND-MOT-S",
                boat_class_label="x",
                loa_m=8.2,
                confidence="high",
                escalate=False,
                data_age_hours=0,
                evaluated_at="now",
            )


class TestThresholdTable:
    def test_every_class_cites_a_source(self):
        for boat in BOAT_CLASSES:
            assert boat.source_citation.url or boat.source_citation.identifier

    def test_classes_tile_the_length_range_without_gaps(self):
        for smaller, larger in itertools.pairwise(BOAT_CLASSES):
            assert smaller.loa_max_m == larger.loa_min_m

    def test_limits_increase_with_vessel_size(self):
        waves = [b.max_wave_m for b in BOAT_CLASSES]
        winds = [b.max_wind_kn for b in BOAT_CLASSES]
        assert waves == sorted(waves)
        assert winds == sorted(winds)

    def test_an_82_metre_boat_lands_in_the_motorised_frp_class(self):
        assert classify(8.2).code == "IND-MOT-S"
        assert by_code("IND-MOT-S").max_wave_m == 1.5

    def test_an_absurd_length_still_returns_a_class(self):
        assert classify(0.1) is not None
        assert classify(500.0) is not None

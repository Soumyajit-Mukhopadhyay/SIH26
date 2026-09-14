"""The researcher catalogue, and the rule that keeps discovery honest.

The load-bearing claim of this feature is that **a language model parses the
request and deterministic code does the matching**, so a researcher can never be
handed a dataset identifier that does not exist. These tests pin that boundary,
because it is invisible from outside and the failure it prevents surfaces weeks
later rather than immediately.
"""

from __future__ import annotations

import inspect

import pytest

from orca import research


class TestCatalogueIntegrity:
    def test_every_dataset_declares_a_caveat(self) -> None:
        """A catalogue that lists only capabilities is how a monthly composite
        ends up answering a daily question."""
        for dataset in research.CATALOGUE:
            assert dataset.caveats.strip(), f"{dataset.id} has no caveat"
            assert len(dataset.caveats) > 40, f"{dataset.id}'s caveat is too thin to be useful"

    def test_every_dataset_declares_a_licence_and_endpoint(self) -> None:
        for dataset in research.CATALOGUE:
            assert dataset.licence.strip(), f"{dataset.id} has no licence"
            assert dataset.endpoint.strip(), f"{dataset.id} has no endpoint"

    def test_ids_are_unique(self) -> None:
        ids = [d.id for d in research.CATALOGUE]
        assert len(ids) == len(set(ids))

    def test_native_resolution_is_stated_separately(self) -> None:
        """ORCA serves MUR at 0.05 deg while the product is 1 km. A researcher
        copying our figure into a methods section must be copying the one that
        describes the array they actually received, so both are carried."""
        mur = research.BY_ID["mur_sst"]
        assert mur.resolution_deg == 0.05
        assert "0.01" in mur.native_resolution or "1 km" in mur.native_resolution

    def test_orca_derived_products_say_they_are_not_the_official_product(self) -> None:
        """The PFZ entry must never read as INCOIS's own advisory."""
        pfz = research.BY_ID["orca_pfz"]
        assert "reimplementation" in pfz.caveats.lower()
        assert "do not cite it as incois" in pfz.caveats.lower()

    def test_gfw_states_the_bias_that_matters_here(self) -> None:
        """Apparent fishing effort is inferred from AIS, and most Indian small
        craft carry no transponder — so it under-represents exactly the fleet
        ORCA exists to serve. Omitting that would invert its meaning."""
        gfw = research.BY_ID["gfw_fishing_effort"]
        assert "ais" in gfw.caveats.lower()
        assert "absence" in gfw.caveats.lower()


class TestTheModelCannotNameADataset:
    def test_the_prompt_forbids_naming_a_dataset(self) -> None:
        """The whole safety argument rests on this instruction being present."""
        source = inspect.getsource(research.parse_with_model)
        assert "must NOT name a dataset" in source

    def test_model_output_is_filtered_to_known_variables(self) -> None:
        """Even a compliant-looking parse is intersected with the real variable
        set, so an invented variable cannot reach the matcher."""
        source = inspect.getsource(research.parse_with_model)
        assert "allowed" in source and "if v in allowed" in source

    def test_matching_is_a_pure_function_of_the_intent(self) -> None:
        """`match` must not reach for a model, a network or a clock."""
        source = inspect.getsource(research.match)
        for forbidden in ("complete(", "httpx", "await ", "requests"):
            assert forbidden not in source, f"match() must not use {forbidden}"

    def test_every_match_comes_from_the_registry(self) -> None:
        intent = research.Intent(variables=["sst", "chlorophyll", "wave_height"])
        for hit in research.match(intent, limit=20):
            assert hit.dataset is research.BY_ID[hit.dataset.id]


class TestHeuristicParse:
    def test_it_finds_variables_from_keywords(self) -> None:
        intent = research.parse_heuristic("I want chlorophyll and sst")
        assert "chlorophyll" in intent.variables
        assert "sst" in intent.variables

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("blooms in the Bay of Bengal", "bay of bengal"),
            ("winds over the Arabian Sea", "arabian sea"),
            # Researchers write "off Tamil Nadu" far more often than
            # "Tamil Nadu coast"; returning no region would silently widen a
            # specific request to the whole EEZ.
            ("fronts near Tamil Nadu", "tamil nadu coast"),
            ("upwelling off Gujarat", "gujarat coast"),
        ],
    )
    def test_region_names_resolve(self, text: str, expected: str) -> None:
        intent = research.parse_heuristic(text)
        assert intent.place == expected
        assert intent.bbox == research.REGIONS[expected]

    def test_longer_region_names_win(self) -> None:
        """'gulf of mannar' must not be swallowed by a shorter key."""
        found = research.resolve_region("survey in the gulf of mannar")
        assert found is not None and found[0] == "gulf of mannar"

    def test_monsoon_is_expanded_and_the_guess_is_declared(self) -> None:
        intent = research.parse_heuristic("chlorophyll during the monsoon")
        assert intent.start and intent.start.endswith("-06-01")
        assert intent.end and intent.end.endswith("-09-30")
        assert any("monsoon" in a for a in intent.assumptions), (
            "an inferred date range must be declared, not applied silently"
        )

    def test_an_explicit_year_and_month_win_over_a_relative_phrase(self) -> None:
        intent = research.parse_heuristic("sst in March 2024")
        assert intent.start == "2024-03-01"
        assert intent.end == "2024-03-31"

    def test_coordinates_in_the_question_override_a_named_region(self) -> None:
        intent = research.parse_heuristic("sst from 75E to 80E and 8N to 13N")
        assert intent.bbox == (75.0, 8.0, 80.0, 13.0)
        assert intent.place is None


class TestMerge:
    def test_a_keyword_the_model_dropped_still_survives(self) -> None:
        """The heuristic runs on every request as a safety net, not only as a
        fallback — discovery quietly losing a variable because the model missed
        it would be worse than discovery failing outright."""
        model = research.Intent(variables=["sst"])
        heuristic = research.Intent(variables=["chlorophyll"], place="arabian sea")
        merged = research.merge(model, heuristic)
        assert merged.variables == ["sst", "chlorophyll"]
        assert merged.place == "arabian sea"

    def test_no_model_is_recorded_as_an_assumption(self) -> None:
        merged = research.merge(None, research.parse_heuristic("sst"))
        assert any("without a language model" in a for a in merged.assumptions)


class TestRanking:
    def test_a_forecast_product_is_penalised_for_a_historical_range(self) -> None:
        """And it is penalised rather than filtered out: a researcher is better
        served seeing it ranked low WITH the reason than having it vanish and
        concluding ORCA has no such data."""
        intent = research.Intent(variables=["wave_height"], start="2020-01-01")
        hits = {m.dataset.id: m for m in research.match(intent, limit=20)}
        assert "open_meteo_marine" in hits
        assert any("historical" in w for w in hits["open_meteo_marine"].why)

    def test_a_servable_dataset_outranks_an_equivalent_one_we_only_index(self) -> None:
        intent = research.Intent(variables=["wave_height"])
        ranked = [m.dataset.id for m in research.match(intent, limit=20)]
        assert ranked.index("open_meteo_marine") < ranked.index("cmems_wave")

    def test_snippet_carries_the_parsed_box_and_dates(self) -> None:
        intent = research.Intent(
            variables=["sst"], bbox=(79.0, 9.0, 80.5, 10.5), start="2025-03-01", end="2025-03-31"
        )
        code = research.snippet(research.BY_ID["mur_sst"], intent)
        assert "79.0" in code and "10.5" in code
        assert "2025-03-01" in code


class TestSeparationFromTheVerdict:
    def test_services_does_not_import_the_ml_package(self) -> None:
        """The same wall that keeps the agent plane away from the rule engine
        keeps the learned models away from it too. A front is a fishing signal,
        not a hazard threshold."""
        from pathlib import Path

        import orca.services

        services = Path(orca.services.__file__).parent
        offenders = [
            path.name
            for path in services.glob("*.py")
            if "orca.ml" in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"services/ must not import orca.ml: {offenders}"

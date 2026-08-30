"""The provenance contract. If these break, every claim ORCA makes on stage
about knowing where its numbers came from is void."""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest
from pydantic import ValidationError

from orca.provenance import (
    DECISION_GRADE,
    NEVER_STALE,
    STALENESS_HOURS,
    Citation,
    Evidence,
    Freshness,
    Provenance,
    Provider,
    evidence_summary,
    humanise_hours,
    staleness_hours,
    utcnow,
)


def _ev(**kw) -> Evidence:
    base = {
        "dataset_id": "marine-api-v1",
        "provider": Provider.OPEN_METEO,
        "variable": "wave_height",
        "value": 2.4,
        "unit": "m",
        "provenance": Provenance.LIVE,
        "freshness": Freshness.of("wave_height", utcnow()),
    }
    return Evidence(**{**base, **kw})


class TestFreshness:
    def test_fresh_value_is_not_stale_and_has_no_note(self):
        f = Freshness.of("wave_height", utcnow() - timedelta(hours=1))
        assert not f.is_stale
        assert f.note is None
        assert 0.9 < f.age_hours < 1.1

    def test_stale_value_carries_an_explanatory_note(self):
        f = Freshness.of("sst", utcnow() - timedelta(hours=40))
        assert f.is_stale
        assert f.note is not None
        # The note must name the age AND the limit — a bare "this is old" is
        # useless to a fisherman deciding whether to sail.
        assert "40" in f.note
        assert "24 h" in f.note

    def test_threshold_is_per_variable_not_global(self):
        # 40 h is stale for SST (24 h) but fine for chlorophyll (72 h), which has
        # to wait for a cloud-free pass.
        old = utcnow() - timedelta(hours=40)
        assert Freshness.of("sst", old).is_stale
        assert not Freshness.of("chlorophyll", old).is_stale

    def test_reference_geography_never_goes_stale(self):
        f = Freshness.of("eez", utcnow() - timedelta(days=900))
        assert not f.is_stale
        assert f.note is None

    @pytest.mark.parametrize("variable", sorted(NEVER_STALE))
    def test_every_never_stale_variable_has_infinite_threshold(self, variable):
        assert staleness_hours(variable) == float("inf")

    def test_unknown_variable_gets_a_conservative_default(self):
        # A typo must not produce an optimistically-fresh value.
        assert staleness_hours("wave_heigth") == 6.0

    def test_naive_datetimes_are_treated_as_utc(self):
        naive = (utcnow() - timedelta(hours=2)).replace(tzinfo=None)
        f = Freshness.of("wind_speed", naive)
        assert f.valid_time.tzinfo is UTC
        assert 1.9 < f.age_hours < 2.1

    def test_static_is_never_stale(self):
        assert not Freshness.static().is_stale

    def test_thresholds_are_all_positive(self):
        assert all(v > 0 for v in STALENESS_HOURS.values())


class TestEvidenceInvariants:
    def test_derived_evidence_without_lineage_is_rejected(self):
        # This is the guard that makes the PFZ layer auditable: a computed value
        # must name what it was computed from.
        with pytest.raises(ValidationError, match="lineage"):
            Evidence(
                dataset_id="orca:pfz",
                provider=Provider.ORCA,
                variable="pfz_rank",
                value=1,
                provenance=Provenance.DERIVED,
                freshness=Freshness.static(),
            )

    def test_derived_evidence_with_lineage_is_accepted(self):
        e = Evidence(
            dataset_id="orca:pfz",
            provider=Provider.ORCA,
            variable="pfz_rank",
            value=1,
            provenance=Provenance.DERIVED,
            freshness=Freshness.static(),
            lineage=["incois_tmi_3day_datasets", "IRS_chlorophyll_datasets"],
            method="Sobel-P90 + SIED front detector",
        )
        assert e.lineage == ["incois_tmi_3day_datasets", "IRS_chlorophyll_datasets"]

    def test_unavailable_evidence_may_not_carry_a_value(self):
        with pytest.raises(ValidationError, match="contradiction"):
            Evidence(
                dataset_id="bhuvan:wms",
                provider=Provider.BHUVAN,
                variable="chlorophyll",
                value=0.42,
                provenance=Provenance.UNAVAILABLE,
                freshness=Freshness.static(),
            )

    def test_unavailable_constructor_records_the_reason(self):
        e = Evidence.unavailable(
            dataset_id="bhuvan:wms",
            provider=Provider.BHUVAN,
            variable="chlorophyll",
            reason="connect timeout after 8s",
        )
        assert e.value is None
        assert e.notes == "connect timeout after 8s"
        assert not e.is_decision_grade

    def test_evidence_is_immutable(self):
        # A value must not be mutated after its provenance has been established.
        with pytest.raises(ValidationError):
            _ev().value = 9.9

    def test_simulated_data_is_never_decision_grade(self):
        # A GO/NO-GO must never rest on invented data.
        assert Provenance.SIMULATED not in DECISION_GRADE
        e = _ev(provenance=Provenance.SIMULATED)
        assert not e.is_decision_grade

    @pytest.mark.parametrize("provenance", [Provenance.LIVE, Provenance.CACHED, Provenance.CURATED])
    def test_real_data_is_decision_grade(self, provenance):
        assert _ev(provenance=provenance).is_decision_grade

    def test_a_null_value_is_never_decision_grade(self):
        assert not _ev(value=None).is_decision_grade

    def test_display_shows_value_unit_badge_and_source(self):
        s = _ev().display()
        assert "2.4 m" in s
        assert "[LIVE]" in s
        assert "Open-Meteo/marine-api-v1" in s

    def test_display_flags_staleness(self):
        e = _ev(freshness=Freshness.of("wave_height", utcnow() - timedelta(hours=30)))
        assert "!stale" in e.display()

    def test_citations_attach_to_evidence(self):
        e = _ev(
            citations=[
                Citation(
                    label="INCOIS PFZ methodology",
                    provider=Provider.INCOIS,
                    url="https://incois.gov.in/portal/osf/pfz.jsp",
                )
            ]
        )
        assert e.citations[0].provider == Provider.INCOIS


class TestEvidenceSummary:
    def test_summary_reports_the_worst_provenance_present(self):
        # A card mixing live and simulated data must badge as simulated. Showing
        # the most flattering state would be the single most misleading thing the
        # UI could do.
        s = evidence_summary([_ev(), _ev(provenance=Provenance.SIMULATED)])
        assert s["provenance"] == "simulated"
        assert s["mix"] == ["live", "simulated"]

    def test_summary_reports_the_worst_freshness_present(self):
        s = evidence_summary(
            [_ev(), _ev(freshness=Freshness.of("wave_height", utcnow() - timedelta(hours=30)))]
        )
        assert s["stale"] is True
        assert s["max_age_hours"] >= 29

    def test_empty_summary_is_well_formed(self):
        s = evidence_summary([])
        assert s == {
            "count": 0,
            "provenance": None,
            "mix": [],
            "stale": False,
            "max_age_hours": None,
        }


class TestHumanise:
    @pytest.mark.parametrize(
        ("hours", "expected"),
        [
            (0.25, "15 min"),
            (1.0, "1 h"),
            (2.5, "2.5 h"),
            (72.0, "3 days"),
            (float("inf"), "unlimited"),
        ],
    )
    def test_humanise_hours(self, hours, expected):
        assert humanise_hours(hours) == expected

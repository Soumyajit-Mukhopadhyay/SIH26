"""The distress flow: the register, the search plan, and the things that must not drift.

Most of these pin *honesty* properties rather than arithmetic, for the same
reason the builder tests do. A search plan is read by somebody who cannot see
the system that produced it, under time pressure, and the failure that matters
is not a crash — it is a plausible number that quietly came from nowhere.
"""

from __future__ import annotations

import math

import pytest

from orca.services import authorities, searchplan


class TestRegister:
    def test_the_register_matches_the_published_one(self) -> None:
        """NMSAR Plan 2022 Appendix 'A' lists 3 MRCCs and 36 MRSCs. If someone
        adds a centre from a news article or a memory, this fails."""
        assert sum(1 for c in authorities.CENTRES if c.is_mrcc) == 3
        assert sum(1 for c in authorities.CENTRES if not c.is_mrcc) == 36

    def test_every_centre_answers_to_a_real_mrcc(self) -> None:
        mrccs = {c.name for c in authorities.CENTRES if c.is_mrcc}
        for centre in authorities.CENTRES:
            assert centre.mrcc in mrccs, f"{centre.name} reports to unknown {centre.mrcc!r}"

    def test_an_mrcc_is_its_own_parent(self) -> None:
        for centre in authorities.CENTRES:
            if centre.is_mrcc:
                assert centre.mrcc == centre.name

    def test_every_centre_is_reachable(self) -> None:
        """A centre with no telephone and no email is a map pin, not a contact."""
        for centre in authorities.CENTRES:
            assert centre.telephone, f"{centre.name} has no telephone"
            assert centre.email, f"{centre.name} has no email"

    def test_the_distress_number_is_offered_first(self) -> None:
        """1554 reaches the responsible centre directly (NMSAR para 54(b)). It
        goes first because under pressure people dial the first number they see,
        and a landline that rings out is the worst thing on that list."""
        for centre in authorities.CENTRES:
            assert centre.dial()[0] == authorities.MSAR_DISTRESS_NUMBER

    def test_every_centre_is_in_indian_waters(self) -> None:
        """Catches a transposed latitude/longitude, which is the single most
        likely error in a hand-entered coordinate table and one that would put a
        rescue centre in the middle of Africa without anything complaining."""
        for centre in authorities.CENTRES:
            assert 5.0 <= centre.lat <= 25.0, f"{centre.name} latitude {centre.lat}"
            assert 68.0 <= centre.lon <= 95.0, f"{centre.name} longitude {centre.lon}"

    def test_no_two_centres_share_a_position(self) -> None:
        seen = {(round(c.lat, 3), round(c.lon, 3)) for c in authorities.CENTRES}
        assert len(seen) == len(authorities.CENTRES)

    @pytest.mark.parametrize(
        ("lat", "lon", "expected"),
        [
            (13.0, 80.6, "MRCC Chennai"),
            (9.8, 75.9, "MRCC Mumbai"),
            (11.2, 92.2, "MRCC Port Blair"),
        ],
    )
    def test_the_coordinating_mrcc_matches_the_sea_area(
        self, lat: float, lon: float, expected: str
    ) -> None:
        assert authorities.responsible_mrcc(lat, lon).name == expected

    def test_nearest_is_ordered_and_bounded(self) -> None:
        ranked = authorities.nearest(13.0, 80.6, limit=4)
        assert len(ranked) == 4
        distances = [km for _, km, _ in ranked]
        assert distances == sorted(distances)


class TestSweepWidth:
    def test_the_table_is_transcribed_not_interpolated_at_the_columns(self) -> None:
        """Table H-19, person in water, vessel SRU. If these six numbers change,
        somebody has edited a published table."""
        row = searchplan._SWEEP_WIDTH_NM["person_in_water"]["vessel"]
        assert row == (0.3, 0.4, 0.5, 0.5, 0.5, 0.5)
        for visibility, expected in zip(searchplan.VISIBILITY_COLUMNS_NM, row, strict=True):
            assert searchplan._interpolate(visibility, row) == pytest.approx(expected)

    def test_a_small_boat_sees_less_than_a_ship(self) -> None:
        """Eye height. The small-boat column must never exceed the vessel column,
        and swapping the two would silently double every track spacing."""
        for rows in searchplan._SWEEP_WIDTH_NM.values():
            for small, vessel in zip(rows["small_boat"], rows["vessel"], strict=True):
                assert small <= vessel

    def test_visibility_below_the_table_clamps_instead_of_going_to_zero(self) -> None:
        """A zero sweep width divides into an infinite search time, which would
        render as a blank cell rather than as the refusal it actually is."""
        row = searchplan._SWEEP_WIDTH_NM["person_in_water"]["vessel"]
        assert searchplan._interpolate(0.1, row) == row[0]
        assert searchplan._interpolate(500.0, row) == row[-1]

    def test_weather_correction_takes_the_worse_of_wind_and_sea(self) -> None:
        """Table H-10's own rule: "If weather conditions in more than one row
        apply ... use the lower row for more correction"."""
        calm_wind_big_sea, _ = searchplan.weather_correction(
            wind_kn=5.0, wave_m=2.0, small_object=True
        )
        assert calm_wind_big_sea == 0.25

    def test_a_person_is_penalised_far_harder_than_a_hull(self) -> None:
        person, _ = searchplan.weather_correction(wind_kn=30.0, wave_m=2.0, small_object=True)
        hull, _ = searchplan.weather_correction(wind_kn=30.0, wave_m=2.0, small_object=False)
        assert person == 0.25
        assert hull == 0.9

    def test_missing_weather_is_declared_optimistic(self) -> None:
        """Table H-10 only ever reduces sweep width. Applying no correction is
        therefore the most favourable assumption available, and saying nothing
        would let it pass as a measurement."""
        factor, note = searchplan.weather_correction(wind_kn=None, wave_m=None, small_object=True)
        assert factor == 1.0
        assert "OPTIMISTIC" in note


class TestSearchPlan:
    def test_the_plan_moves_with_conditions(self) -> None:
        """The whole point. The same box in a gale must take materially longer
        than in a calm, because the legs have to run closer together."""
        calm = searchplan.plan_search(
            area_km2=200.0,
            drift_object_class="PIW-VERTICAL",
            visibility_km=30.0,
            wind_kn=8.0,
            wave_m=0.5,
        )
        gale = searchplan.plan_search(
            area_km2=200.0,
            drift_object_class="PIW-VERTICAL",
            visibility_km=30.0,
            wind_kn=30.0,
            wave_m=2.5,
        )
        assert gale["track_spacing_nm"] < calm["track_spacing_nm"]
        assert gale["search_hours"] > calm["search_hours"] * 3

    def test_coverage_is_sweep_width_over_track_spacing(self) -> None:
        """C = W/S, verbatim from H.5.4. The identity, not a re-derivation."""
        plan = searchplan.plan_search(area_km2=300.0, drift_object_class="LIFE-RAFT-CANOPY")
        w = plan["sweep_width"]["corrected_nm"]
        s = plan["track_spacing_nm"]
        assert plan["coverage_achieved"] == pytest.approx(w / s, rel=1e-3)

    def test_the_area_actually_gets_covered_in_the_stated_time(self) -> None:
        """A = S x V x T. If the reported hours do not sweep the reported area,
        the plan is a decoration."""
        plan = searchplan.plan_search(area_km2=500.0, drift_object_class="FISHING-VESSEL-SMALL")
        swept = plan["track_spacing_nm"] * plan["unit"]["search_speed_kn"] * plan["search_hours"]
        assert swept == pytest.approx(plan["area_sq_nm"], rel=0.02)

    def test_track_spacing_never_goes_below_the_navigational_limit(self) -> None:
        """0.1 NM is the floor for accurate surface navigation (H.5.4.4). A plan
        asking for legs closer than a boat can hold is not a plan."""
        plan = searchplan.plan_search(
            area_km2=100.0,
            drift_object_class="PIW-VERTICAL",
            visibility_km=2.0,
            wind_kn=40.0,
            wave_m=4.0,
        )
        assert plan["track_spacing_nm"] >= searchplan.MIN_TRACK_SPACING_NM

    def test_flooring_the_spacing_costs_coverage_and_says_so(self) -> None:
        """The floor forces legs FURTHER apart than the sweep width wants, so
        coverage comes out BELOW what was asked for — a surface unit simply
        cannot search that thoroughly. Reporting the requested coverage here
        would promise detection odds nothing can deliver."""
        plan = searchplan.plan_search(
            area_km2=50.0,
            drift_object_class="PIW-VERTICAL",
            visibility_km=2.0,
            wind_kn=40.0,
            wave_m=4.0,
        )
        assert plan["track_spacing_floored"] is True
        assert plan["coverage_achieved"] < plan["coverage_requested"]
        assert any("floor" in limit for limit in plan["limits"])
        # And the odds quoted must be the ones actually achievable.
        assert plan["probability_of_detection"] == pytest.approx(
            1.0 - math.exp(-plan["coverage_achieved"]), abs=1e-3
        )

    def test_pod_follows_the_normal_conditions_curve(self) -> None:
        plan = searchplan.plan_search(area_km2=200.0, drift_object_class="PIW-VERTICAL")
        expected = 1.0 - math.exp(-plan["coverage_achieved"])
        assert plan["probability_of_detection"] == pytest.approx(expected, abs=1e-3)

    def test_an_unknown_object_falls_back_to_the_published_default(self) -> None:
        """The USCG default when the object cannot be determined is a 20-foot
        power boat — a documented choice rather than ORCA's own guess."""
        plan = searchplan.plan_search(area_km2=100.0, drift_object_class="SOMETHING-NEW")
        assert plan["sweep_width"]["search_object"] == searchplan.DEFAULT_SEARCH_OBJECT

    def test_every_drift_class_maps_to_a_real_table_row(self) -> None:
        """The two taxonomies are independent — drift classifies by leeway, the
        sweep table by visibility — so a new leeway class will not fail loudly
        on its own."""
        from orca.services.drift import LEEWAY_CLASSES

        for code in LEEWAY_CLASSES:
            row = searchplan.DRIFT_CLASS_TO_SEARCH_OBJECT.get(code)
            assert row is not None, f"drift class {code} has no sweep-table row"
            assert row in searchplan._SWEEP_WIDTH_NM

    def test_endurance_shortfall_is_reported_not_hidden(self) -> None:
        """A 20-hour search by a unit with 12 hours on scene is two sorties and a
        moved datum. Returning the 20 hours without that note would read as a
        single achievable task."""
        plan = searchplan.plan_search(
            area_km2=4000.0, drift_object_class="PIW-VERTICAL", unit_code="ICG-FPV"
        )
        assert plan["closes"] is False
        assert any("endurance" in limit for limit in plan["limits"])

    def test_more_units_cover_the_area_proportionally_faster(self) -> None:
        one = searchplan.plan_search(area_km2=600.0, drift_object_class="PIW-VERTICAL", units=1)
        four = searchplan.plan_search(area_km2=600.0, drift_object_class="PIW-VERTICAL", units=4)
        assert four["search_hours"] == pytest.approx(one["search_hours"] / 4, rel=0.02)

    def test_a_person_in_a_small_area_gets_a_sector_search(self) -> None:
        plan = searchplan.plan_search(area_km2=60.0, drift_object_class="PIW-VERTICAL")
        assert plan["pattern"]["code"] == "VS"

    def test_the_aircraft_gap_is_stated_on_every_plan(self) -> None:
        """A surface-only plan that does not say it is surface-only implies the
        Coast Guard has no aircraft."""
        plan = searchplan.plan_search(area_km2=200.0, drift_object_class="PIW-VERTICAL")
        assert "Dornier" in plan["aircraft_note"]


class TestSearchUnits:
    def test_no_unit_searches_faster_than_it_transits(self) -> None:
        """Searching is slower than steaming. Inverting the two would overstate
        the area covered per hour — the error that makes a plan look affordable
        and then run out of daylight."""
        for unit in searchplan.SEARCH_UNITS:
            assert unit.search_speed_kn <= unit.transit_speed_kn

    def test_a_small_boat_is_not_given_a_ship_s_sea_keeping(self) -> None:
        small = [u for u in searchplan.SEARCH_UNITS if u.sru_type == "small_boat"]
        large = [u for u in searchplan.SEARCH_UNITS if u.sru_type == "vessel"]
        assert max(u.max_search_wave_m for u in small) <= max(u.max_search_wave_m for u in large)


class TestTheRescueUnitIsNotAFishingBoat:
    def test_a_rescue_transit_is_routed_on_rescue_limits(self) -> None:
        """The most dangerous output this system could produce is "no safe route
        to the casualty" generated from a trawler's comfort threshold. The SAR
        class must exceed every fishing class it would otherwise be confused
        with."""
        from orca.services.distress import SAR_UNIT_CLASS
        from orca.services.thresholds import BOAT_CLASSES

        assert SAR_UNIT_CLASS.max_wave_m > max(b.max_wave_m for b in BOAT_CLASSES)
        assert SAR_UNIT_CLASS.max_wind_kn > max(b.max_wind_kn for b in BOAT_CLASSES)

    def test_the_sar_class_is_not_in_the_fishing_registry(self) -> None:
        """It must not be selectable as a fisherman's vessel, and `classify()`
        must not return it for any hull length."""
        from orca.services.thresholds import BOAT_CLASSES

        assert all(b.code != "ICG-SAR-UNIT" for b in BOAT_CLASSES)

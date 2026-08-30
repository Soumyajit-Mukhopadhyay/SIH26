"""Geofencing and CAP 1.2.

Uses synthetic geometry rather than the live Marine Regions download, so the
tests are fast, offline and deterministic. The live path is exercised separately
by the app's startup, and `test_reference_geography_shape` pins the contract
between the two.

The state machine gets the most attention, because its failure mode is the
quietest: a machine that reports levels instead of transitions turns the
proactive alert rail into a stream of duplicates, users mute it, and the feature
is dead without anything having visibly broken.
"""

from __future__ import annotations

import pytest

from orca.services.cap_builder import Alert, Area, Info, from_geofence, from_risk, validate
from orca.services.geofence import (
    APPROACHING_M,
    FenceState,
    GeofenceIndex,
    _state_for,
    crossed_line,
)
from orca.services.risk_engine import assess

# A square "EEZ" from 79-81 E, 8-10 N, and a north-south "IMBL" line at 80 E.
FAKE_PAYLOAD = {
    "fences": [
        {
            "key": "eez_test",
            "name": "Test EEZ",
            "kind": "eez",
            "consequence": "You leave national jurisdiction.",
            "authority": "test",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[79.0, 8.0], [81.0, 8.0], [81.0, 10.0], [79.0, 10.0], [79.0, 8.0]]
                ],
            },
        },
        {
            "key": "imbl_test",
            "name": "Test IMBL",
            "kind": "imbl",
            "consequence": "You enter a neighbour's waters.",
            "authority": "treaty",
            "length_km": 222.0,
            "geometry": {"type": "LineString", "coordinates": [[80.0, 8.0], [80.0, 10.0]]},
        },
    ]
}


@pytest.fixture
def index() -> GeofenceIndex:
    idx = GeofenceIndex()
    assert idx.load(FAKE_PAYLOAD) == 2
    return idx


class TestIndex:
    def test_it_builds(self, index):
        assert index.ready
        described = index.describe()
        assert described["fence_count"] == 2
        assert described["index"] == "shapely STRtree"

    def test_an_unparseable_fence_is_skipped_not_fatal(self):
        idx = GeofenceIndex()
        count = idx.load(
            {
                "fences": [
                    {"key": "bad", "name": "bad", "kind": "eez", "geometry": {"type": "Nonsense"}},
                    *FAKE_PAYLOAD["fences"],
                ]
            }
        )
        assert count == 2, "one bad geometry must not lose the whole set"

    def test_an_empty_payload_leaves_it_not_ready(self):
        idx = GeofenceIndex()
        assert idx.load({"fences": []}) == 0
        assert not idx.ready


class TestContainmentAndDistance:
    def test_a_point_inside_the_area_is_inside(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.5, radius_km=300)}
        assert hits["eez_test"].inside is True

    def test_distance_when_inside_is_to_the_BOUNDARY_not_zero(self, index):
        """The bug this pins: measuring against the polygon rather than its
        boundary returns 0 for every interior point, so a boat deep inside the
        EEZ was told it was 0.0 km from the line."""
        hits = {h.fence.key: h for h in index.check(9.0, 80.0, radius_km=300)}
        eez = hits["eez_test"]
        assert eez.inside is True
        # 80.0 E is 1 degree from either side, so ~110 km.
        assert eez.distance_m > 100_000
        assert eez.distance_m == pytest.approx(110_000, rel=0.05)

    def test_a_point_outside_is_outside(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 85.0, radius_km=800)}
        assert hits["eez_test"].inside is False

    def test_distances_are_metres_not_degrees(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, radius_km=300)}
        # 0.1 degrees of longitude at 9 N is ~11 km, not 0.1.
        assert hits["imbl_test"].distance_m == pytest.approx(11_000, rel=0.1)

    def test_bearing_and_compass_point_at_the_boundary(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, radius_km=300)}
        imbl = hits["imbl_test"]
        assert 80 < imbl.bearing_to_deg < 100  # the line is due east
        assert imbl.describe()["compass"] == "E"


class TestTimeToCross:
    def test_a_heading_toward_the_line_gives_a_time(self, index):
        # 0.1 deg west of the line (~11 km), steering due east at 8 kn.
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, heading_deg=90, speed_kn=8.0)}
        minutes = hits["imbl_test"].time_to_cross_min
        assert minutes is not None
        # 11 km at 8 kn (14.8 km/h) is ~45 minutes.
        assert minutes == pytest.approx(45, rel=0.2)

    def test_a_heading_parallel_to_the_line_gives_no_time(self, index):
        """Distance-over-speed would promise a crossing here. The projection does
        not, because the track never meets the line."""
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, heading_deg=0, speed_kn=8.0)}
        assert hits["imbl_test"].time_to_cross_min is None

    def test_a_heading_away_reports_not_closing(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, heading_deg=270, speed_kn=8.0)}
        imbl = hits["imbl_test"]
        assert imbl.time_to_cross_min is None
        assert imbl.closing is False

    def test_no_heading_means_no_projection(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.9)}
        assert hits["imbl_test"].time_to_cross_min is None

    def test_zero_speed_gives_no_time(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, heading_deg=90, speed_kn=0.0)}
        assert hits["imbl_test"].time_to_cross_min is None

    def test_the_narrative_is_speakable(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, heading_deg=90, speed_kn=8.0)}
        narrative = hits["imbl_test"].narrative()
        assert "cross Test IMBL in" in narrative
        assert "minutes" in narrative

    def test_the_quoted_distance_is_to_the_CROSSING_not_the_nearest_point(self, index):
        """Standing inside the square EEZ and steering north-east, the nearest
        boundary point is west but the crossing is ahead to the north-east.

        Quoting the nearest distance beside a crossing time produced a sentence
        that contradicted itself on screen: "2.9 km away, 1.9 h to cross", which
        at 8 knots is impossible.
        """
        # Inside the square, nearer the south-west corner, steering north-east.
        # The nearest edges are south and west (~55 km); the crossing is the
        # north-east corner region (~230 km ahead). 25 kn so a 6 h projection
        # actually reaches it.
        hits = {h.fence.key: h for h in index.check(8.5, 79.5, heading_deg=45, speed_kn=25.0)}
        eez = hits["eez_test"]
        assert eez.time_to_cross_min is not None
        assert eez.cross_distance_m is not None
        # The crossing is further away than the nearest edge.
        assert eez.cross_distance_m > eez.distance_m
        # And the time matches the crossing distance, not the nearest one.
        implied_km = eez.time_to_cross_min * (25.0 * 1.852 / 60)
        assert implied_km == pytest.approx(eez.cross_distance_m / 1000, rel=0.02)
        # And the sentence says "leave", not "cross", because we are inside it.
        narrative = eez.narrative()
        assert "ahead" in narrative
        assert "leave it in" in narrative

    def test_no_crossing_means_no_crossing_distance(self, index):
        hits = {h.fence.key: h for h in index.check(9.0, 79.9, heading_deg=0, speed_kn=8.0)}
        assert hits["imbl_test"].cross_distance_m is None


class TestStateMachine:
    """Events must fire on TRANSITIONS. This is the whole value of the machine."""

    def test_first_sight_inside_is_INSIDE_not_CROSSED(self):
        # The bug: treating the first fix of a trip as an entry event fired a
        # spurious "you have entered the EEZ" alert at the start of every trip.
        assert _state_for(inside=True, distance_m=50_000, is_area=True, previous=None) is (
            FenceState.INSIDE
        )

    def test_outside_then_inside_is_CROSSED(self):
        assert (
            _state_for(inside=True, distance_m=100, is_area=True, previous=FenceState.OUTSIDE.value)
            is FenceState.CROSSED
        )

    def test_approaching_then_inside_is_CROSSED(self):
        assert (
            _state_for(
                inside=True, distance_m=100, is_area=True, previous=FenceState.APPROACHING.value
            )
            is FenceState.CROSSED
        )

    def test_crossed_then_still_inside_settles_to_INSIDE(self):
        # The second fix must NOT re-fire. This is what stops duplicates.
        assert (
            _state_for(inside=True, distance_m=100, is_area=True, previous=FenceState.CROSSED.value)
            is FenceState.INSIDE
        )

    def test_inside_then_outside_is_EXITED(self):
        assert (
            _state_for(inside=False, distance_m=100, is_area=True, previous=FenceState.INSIDE.value)
            is FenceState.EXITED
        )

    def test_loitering_near_a_line_stays_APPROACHING(self):
        state = FenceState.APPROACHING.value
        for _ in range(5):
            new = _state_for(inside=False, distance_m=1000, is_area=False, previous=state)
            assert new is FenceState.APPROACHING
            state = new.value

    def test_the_approaching_threshold_is_two_nautical_miles(self):
        assert APPROACHING_M == pytest.approx(3704, abs=1)
        assert (
            _state_for(inside=False, distance_m=APPROACHING_M - 1, is_area=False, previous=None)
            is FenceState.APPROACHING
        )
        assert (
            _state_for(inside=False, distance_m=APPROACHING_M + 1, is_area=False, previous=None)
            is FenceState.OUTSIDE
        )


class TestLineCrossing:
    def test_a_segment_stepping_over_the_line_is_detected(self, index):
        """At 8 knots a boat covers ~1.5 km between fixes and steps clean over a
        line, so this must be a segment intersection, not a distance threshold."""
        events = crossed_line(index, (9.0, 79.95), (9.0, 80.05))
        assert len(events) == 1
        assert events[0]["fence"] == "imbl_test"
        assert events[0]["at"]["lon"] == pytest.approx(80.0, abs=0.01)

    def test_a_segment_that_does_not_reach_the_line_is_not_detected(self, index):
        assert crossed_line(index, (9.0, 79.5), (9.0, 79.9)) == []

    def test_a_segment_parallel_to_the_line_is_not_detected(self, index):
        assert crossed_line(index, (8.5, 79.9), (9.5, 79.9)) == []

    def test_area_fences_are_not_reported_as_line_crossings(self, index):
        events = crossed_line(index, (9.0, 78.5), (9.0, 79.5))
        assert all(e["fence"] != "eez_test" for e in events)

    def test_the_crossing_carries_its_consequence(self, index):
        events = crossed_line(index, (9.0, 79.95), (9.0, 80.05))
        assert "neighbour's waters" in events[0]["consequence"]
        assert "crossed Test IMBL" in events[0]["narrative"]


class TestCap:
    @pytest.fixture
    def risk(self):
        return assess(
            wave_m=2.64,
            wind_kn=22.3,
            visibility_km=18.3,
            lightning_pct=72.7,
            loa_m=8.2,
            data_age_hours=1.0,
        ).model_dump(mode="json")

    def test_a_verdict_produces_a_valid_cap_document(self, risk):
        xml = from_risk(risk, lat=9.0, lon=79.5).to_string()
        result = validate(xml)
        assert result["valid"], result["problems"]
        assert result["namespace"] == "urn:oasis:names:tc:emergency:cap:1.2"

    def test_a_no_go_maps_to_severe_and_immediate(self, risk):
        xml = from_risk(risk, lat=9.0, lon=79.5).to_string()
        assert "<severity>Severe</severity>" in xml
        assert "<urgency>Immediate</urgency>" in xml

    def test_a_go_is_not_inflated(self):
        calm = assess(
            wave_m=0.4, wind_kn=8.0, visibility_km=10.0, lightning_pct=0.0, loa_m=8.2
        ).model_dump(mode="json")
        xml = from_risk(calm, lat=9.0, lon=79.5).to_string()
        # Inflating severity is how an alerting channel loses credibility.
        assert "<severity>Minor</severity>" in xml
        assert "Extreme" not in xml

    def test_the_veto_figures_reach_the_description(self, risk):
        xml = from_risk(risk, lat=9.0, lon=79.5).to_string()
        assert "2.64" in xml
        assert "1.5" in xml

    def test_provenance_travels_with_the_alert(self, risk):
        xml = from_risk(risk, lat=9.0, lon=79.5).to_string()
        # A recipient must be able to tell an ORCA advisory from an IMD bulletin.
        assert "verdict_source" in xml
        assert "rule_engine" in xml
        assert "thresholds_version" in xml
        assert "supplements, never replaces" in xml

    def test_a_bilingual_alert_uses_two_info_blocks(self, risk):
        xml = from_risk(
            risk,
            lat=9.0,
            lon=79.5,
            translated={
                "language": "ta-IN",
                "headline": "கடலுக்குச் செல்ல வேண்டாம்",
                "description": "அலை உயரம் 2.64 மீ",
                "instruction": "செல்ல வேண்டாம்",
            },
        ).to_string()
        result = validate(xml)
        assert result["valid"], result["problems"]
        assert result["info_blocks"] == 2
        assert result["languages"] == ["en-IN", "ta-IN"]

    def test_identifiers_are_unique(self, risk):
        first = from_risk(risk, lat=9.0, lon=79.5).identifier
        second = from_risk(risk, lat=9.0, lon=79.5).identifier
        # A colliding identifier is silently dropped as a duplicate downstream.
        assert first != second

    def test_sent_carries_a_timezone_offset(self, risk):
        xml = from_risk(risk, lat=9.0, lon=79.5).to_string()
        assert "+00:00</sent>" in xml

    def test_a_geofence_crossing_produces_a_valid_alert(self):
        event = {
            "fence": "imbl_test",
            "name": "Sri Lanka - India",
            "consequence": "You are in Sri Lanka's waters.",
            "authority": "treaty",
        }
        result = validate(from_geofence(event, lat=9.1, lon=79.6).to_string())
        assert result["valid"], result["problems"]

    def test_cap_polygons_are_lat_lon_not_lon_lat(self):
        """CAP is lat,lon — the opposite of GeoJSON. Swapping puts the alert in
        the wrong ocean."""
        alert = Alert(
            infos=[
                Info(
                    headline="h",
                    description="d",
                    instruction="i",
                    event="e",
                    urgency="Immediate",
                    severity="Severe",
                    certainty="Likely",
                    areas=[Area(description="a", polygon=[(9.0, 79.0), (9.0, 80.0), (10.0, 80.0)])],
                )
            ]
        )
        xml = alert.to_string()
        assert "9.0000,79.0000" in xml

    def test_validation_catches_a_missing_required_element(self, risk):
        xml = from_risk(risk, lat=9.0, lon=79.5).to_string()
        broken = xml.replace("<urgency>Immediate</urgency>", "")
        result = validate(broken)
        assert not result["valid"]
        assert any("urgency" in p for p in result["problems"])

    def test_validation_catches_out_of_order_info_children(self, risk):
        # CAP 1.2 declares a sequence, so shuffled-but-present elements are
        # invalid, and a validator that only checks presence would pass this.
        xml = from_risk(risk, lat=9.0, lon=79.5).to_string()
        broken = xml.replace("<language>en-IN</language>", "", 1).replace(
            "<senderName>", "<language>en-IN</language>\n    <senderName>", 1
        )
        result = validate(broken)
        assert not result["valid"]
        assert any("sequence order" in p for p in result["problems"])

    def test_validation_rejects_non_xml(self):
        result = validate("this is not xml at all")
        assert not result["valid"]


class TestReferenceGeographyContract:
    def test_the_payload_shape_the_index_expects(self):
        """Pins the contract between marine_regions and geofence, so a change to
        one breaks a test rather than silently emptying the fence set."""
        from orca.sources.marine_regions import Fence

        fence = Fence(
            key="k",
            name="n",
            kind="eez",
            geometry={"type": "Point", "coordinates": [80.0, 9.0]},
            consequence="c",
            authority="a",
        )
        entry = {**fence.describe(), "geometry": fence.geometry}
        for required in ("key", "name", "kind", "consequence", "authority", "geometry"):
            assert required in entry

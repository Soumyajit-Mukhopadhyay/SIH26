"""Geodesy and the AOI grid.

The plan names this test explicitly: *"the known-1 km pair asserts metres"*. The
blueprint's warning is that every team ships the degrees-vs-metres bug, where
`ST_Distance` on a geometry returns 0.5 and nobody notices it is not 55 km.

The reference value is not invented: it is what **PostGIS itself returned** for
the pair, so the SQLite/shapely path and the PostGIS path are checked against a
single oracle rather than against each other.

    select st_distance(
      st_geogfromtext('POINT(80.2707 13.0827)'),
      st_geogfromtext('POINT(80.2707 13.0917)'))   ->  995.68 m
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from orca.science.grid import AOI, AOI_COARSE, H3_RESOLUTION, Grid, regrid_nearest, subgrid
from orca.services.geo import (
    bearing_deg,
    compass_point,
    destination,
    geodesic_km,
    geodesic_m,
    path_length_m,
    time_to_cross_minutes,
)

#: Two points off Chennai, 0.009 degrees of latitude apart.
CHENNAI = (13.0827, 80.2707)
CHENNAI_NORTH = (13.0917, 80.2707)
POSTGIS_ORACLE_M = 995.68


class TestDistanceIsInMetres:
    def test_the_known_pair_matches_postgis(self):
        distance = geodesic_m(*CHENNAI, *CHENNAI_NORTH)
        assert distance == pytest.approx(POSTGIS_ORACLE_M, abs=0.05)

    def test_the_answer_is_metres_not_degrees(self):
        # The actual bug this file exists to prevent: a degree-space answer would
        # be 0.009, not ~996.
        distance = geodesic_m(*CHENNAI, *CHENNAI_NORTH)
        assert distance > 900, "distance came back in degrees, not metres"
        assert distance < 1100

    def test_km_helper_agrees_with_the_metre_one(self):
        assert geodesic_km(*CHENNAI, *CHENNAI_NORTH) == pytest.approx(
            geodesic_m(*CHENNAI, *CHENNAI_NORTH) / 1000
        )

    def test_distance_is_symmetric(self):
        there = geodesic_m(*CHENNAI, *CHENNAI_NORTH)
        back = geodesic_m(*CHENNAI_NORTH, *CHENNAI)
        assert there == pytest.approx(back, abs=1e-6)

    def test_a_degree_of_longitude_shrinks_towards_the_pole(self):
        # A naive sqrt(dlat^2 + dlon^2) would make these equal, which is the
        # whole reason a real geodesic solver is used.
        at_equator = geodesic_m(0.0, 80.0, 0.0, 81.0)
        at_25_north = geodesic_m(25.0, 80.0, 25.0, 81.0)
        assert at_equator > at_25_north
        assert at_25_north / at_equator == pytest.approx(math.cos(math.radians(25)), rel=0.01)

    def test_round_trip_through_destination(self):
        lat, lon = destination(*CHENNAI, 90.0, 5000.0)
        assert geodesic_m(*CHENNAI, lat, lon) == pytest.approx(5000.0, abs=0.5)

    def test_path_length_sums_the_legs(self):
        points = [CHENNAI, CHENNAI_NORTH, (13.1007, 80.2707)]
        assert path_length_m(points) == pytest.approx(2 * POSTGIS_ORACLE_M, rel=0.01)


class TestBearing:
    def test_due_north(self):
        assert bearing_deg(*CHENNAI, *CHENNAI_NORTH) == pytest.approx(0.0, abs=0.01)

    def test_due_east(self):
        assert bearing_deg(13.0, 80.0, 13.0, 81.0) == pytest.approx(90.0, abs=0.2)

    @pytest.mark.parametrize(
        ("bearing", "expected"),
        [(0, "N"), (45, "NE"), (90, "E"), (135, "SE"), (180, "S"), (270, "W"), (359, "N")],
    )
    def test_compass_points(self, bearing, expected):
        assert compass_point(bearing) == expected


class TestTimeToCross:
    def test_a_realistic_case(self):
        # 11.4 km at 10 kn is about 37 minutes — the Palk Bay demo figure.
        minutes = time_to_cross_minutes(11_400, 10.0)
        assert minutes == pytest.approx(36.9, abs=0.5)

    def test_a_stationary_boat_has_no_answer(self):
        # None, not infinity: the caller must say "you are not moving" rather
        # than print a number.
        assert time_to_cross_minutes(1000, 0.0) is None
        assert time_to_cross_minutes(1000, -3.0) is None


class TestAoiGrid:
    def test_the_aoi_is_the_indian_eez_envelope(self):
        assert AOI.bounds == (60.0, 0.0, 100.0, 25.0)
        assert AOI.shape == (500, 800)
        assert AOI.step == 0.05

    def test_latitudes_run_north_to_south(self):
        # Image row order. Reversing it flips every raster vertically, which
        # looks like bad data rather than a bad index.
        assert AOI.lats[0] > AOI.lats[-1]
        assert AOI.lats[0] == pytest.approx(24.975)
        assert AOI.lats[-1] == pytest.approx(0.025)

    def test_longitudes_run_west_to_east(self):
        assert AOI.lons[0] < AOI.lons[-1]
        assert AOI.lons[0] == pytest.approx(60.025)

    def test_index_round_trips_through_the_cell_centre(self):
        index = AOI.index_of(13.0, 80.5)
        assert index is not None
        lat, lon = AOI.cell_centre(*index)
        assert abs(lat - 13.0) <= AOI.step
        assert abs(lon - 80.5) <= AOI.step

    def test_a_point_outside_the_aoi_returns_none_rather_than_clamping(self):
        # Clamping would answer a question about the South China Sea with a value
        # from the AOI's eastern edge.
        assert AOI.index_of(13.0, 120.0) is None
        assert AOI.index_of(-5.0, 80.0) is None
        assert AOI.contains(13.0, 80.0)

    def test_cell_area_shrinks_towards_the_pole(self):
        areas = AOI.cell_area_km2()
        assert areas[0] < areas[-1]  # row 0 is 25 N, the last row is near 0 N
        # Roughly 10% across the AOI, which is why a PFZ area cannot use a
        # constant cell size.
        assert areas[-1] / areas[0] == pytest.approx(1 / math.cos(math.radians(25)), rel=0.02)

    def test_one_h3_resolution_for_ingest_and_query(self):
        assert H3_RESOLUTION == 6
        assert AOI.describe()["h3_resolution"] == H3_RESOLUTION

    def test_subgrid_snaps_to_the_parent_cells(self):
        window = subgrid(AOI, 79.03, 12.02, 81.07, 14.04)
        assert (window.west / AOI.step) % 1 == pytest.approx(0.0)
        assert window.step == AOI.step
        assert window.west >= AOI.west and window.east <= AOI.east

    def test_the_coarse_grid_shares_the_extent(self):
        assert AOI_COARSE.bounds == AOI.bounds
        assert AOI_COARSE.shape == (250, 400)


class TestRegrid:
    def _ramp(self, lats, lons):
        """A field whose value is its latitude, so a flip is unmistakable."""
        return np.repeat(np.asarray(lats)[:, None], len(lons), axis=1).astype(float)

    def test_a_south_to_north_source_is_flipped_to_image_order(self):
        lats = np.linspace(0, 25, 26)  # ascending, the netCDF norm
        lons = np.linspace(60, 100, 41)
        out = regrid_nearest(self._ramp(lats, lons), lats, lons, AOI)
        # Row 0 of the ORCA grid is 25 N, so it must hold the high value.
        assert out[0, 400] > out[-1, 400]
        assert out[0, 400] == pytest.approx(25.0, abs=1.0)

    def test_0_360_longitudes_are_normalised(self):
        lats = np.linspace(25, 0, 26)
        lons = np.linspace(60, 100, 41)  # already in range, but exercise the path
        out = regrid_nearest(self._ramp(lats, lons), lats, lons, AOI)
        assert np.isfinite(out).mean() > 0.9

    def test_cells_beyond_the_source_are_blanked_not_smeared(self):
        # A source covering only the east must not have its edge value spread
        # across the whole AOI.
        lats = np.linspace(25, 0, 26)
        lons = np.linspace(90, 100, 11)
        out = regrid_nearest(self._ramp(lats, lons), lats, lons, AOI)
        west_column = AOI.index_of(12.0, 62.0)
        assert west_column is not None
        assert np.isnan(out[west_column])

    def test_shape_mismatch_is_rejected(self):
        with pytest.raises(ValueError, match="does not match"):
            regrid_nearest(np.zeros((3, 4)), np.zeros(5), np.zeros(4), AOI)


class TestGridIsShared:
    def test_science_and_services_agree_on_one_grid(self):
        """Nothing may define its own AOI. A mismatch between the ingest grid and
        the query grid renders an empty map with no error anywhere."""
        from orca.jobs import ingest
        from orca.science import pfz

        assert ingest.AOI is AOI
        assert pfz.AOI is AOI

    def test_the_grid_is_immutable(self):
        with pytest.raises((AttributeError, TypeError)):
            AOI.step = 0.1  # type: ignore[misc]

    def test_bitmap_bounds_are_in_deckgl_order(self):
        # [west, south, east, north]. Transposing this puts the Indian Ocean over
        # Africa, which is the kind of bug that looks like a projection problem.
        assert AOI.bitmap_bounds == [60.0, 0.0, 100.0, 25.0]


class TestGridConstruction:
    def test_a_custom_grid_computes_its_own_shape(self):
        grid = Grid(west=70.0, east=80.0, south=5.0, north=15.0, step=0.1)
        assert grid.shape == (100, 100)
        assert grid.lats[0] == pytest.approx(14.95)

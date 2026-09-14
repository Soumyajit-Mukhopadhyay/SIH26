"""The router's decision logic, tested without touching the network.

Everything that matters about this module is a pure function of a costed lattice:
whether a veto blocks a cell absolutely, whether A* is optimal under the cost
model, whether the direct line's refusals are reported. Those are the tests. The
upstream sampling is exercised by hand against the live API — see the commit
message — because a test that asserts on today's Bay of Bengal is a test that
fails tomorrow for the right reasons and the wrong purpose.
"""

from __future__ import annotations

import itertools
import math

import pytest

from orca.services import router as R
from orca.services.risk_engine import assess
from orca.services.thresholds import by_code, classify


def make_lattice(rows: list[str], *, lat0: float = 12.0, lon0: float = 80.0, step: float = 0.25):
    """Build a costed lattice from an ASCII map.

    ``.`` open water (index 90), ``~`` rough but passable (index 30), ``#``
    vetoed, ``L`` land (no wave height at all). Rows run north to south.
    """
    lats = [round(lat0 - i * step, 6) for i in range(len(rows))]
    lons = [round(lon0 + j * step, 6) for j in range(len(rows[0]))]
    lattice = R.Lattice(lats=lats, lons=lons, step_deg=step)

    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            lat, lon = lats[i], lons[j]
            if cell == "L":
                lattice.nodes[(i, j)] = R.Node(
                    i=i,
                    j=j,
                    lat=lat,
                    lon=lon,
                    conditions={"wave_height": None},
                    risk=None,
                    passable=False,
                    reason="no wave height here — land, or outside the wave model's domain",
                )
                continue

            # Real engine calls, so the test cannot drift from the thresholds.
            if cell == "#":
                risk = assess(
                    wave_m=4.0,
                    wind_kn=10.0,
                    visibility_km=10.0,
                    lightning_pct=5.0,
                    boat_class=by_code("IND-MOT-S"),
                )
            elif cell == "~":
                risk = assess(
                    wave_m=1.3,
                    wind_kn=20.0,
                    visibility_km=6.0,
                    lightning_pct=25.0,
                    boat_class=by_code("IND-MOT-S"),
                )
            else:
                risk = assess(
                    wave_m=0.4,
                    wind_kn=6.0,
                    visibility_km=20.0,
                    lightning_pct=0.0,
                    boat_class=by_code("IND-MOT-S"),
                )
            vetoed = bool(risk.vetoes)
            lattice.nodes[(i, j)] = R.Node(
                i=i,
                j=j,
                lat=lat,
                lon=lon,
                conditions={"wave_height": 1.0, "wind_speed": 10.0},
                risk=risk,
                passable=not vetoed,
                reason=None if not vetoed else "; ".join(risk.vetoes),
            )
    return lattice


class TestCostModel:
    def test_a_veto_is_impassable_not_expensive(self) -> None:
        """The distinction the whole module turns on.

        A vetoed cell must be unreachable at any price. Encoding "never" as a
        large cost is how a router ends up sailing through a cyclone to save a
        day, and it is the failure that would be least visible on a map.
        """
        lattice = make_lattice(["..#..", "..#..", "..#.."])
        blocked = lattice.nodes[(1, 2)]
        assert blocked.passable is False
        assert blocked.reason
        assert R.astar(lattice, (1, 0), (1, 4)) is None

    def test_penalty_is_finite_for_passable_and_infinite_for_none(self) -> None:
        lattice = make_lattice(["..", "L~"])
        assert R._penalty(lattice.nodes[(0, 0)].risk) == pytest.approx(1.0, abs=0.5)
        assert R._penalty(lattice.nodes[(1, 1)].risk) > R._penalty(lattice.nodes[(0, 0)].risk)
        assert R._penalty(None) == math.inf

    def test_rough_water_is_avoided_but_not_forbidden(self) -> None:
        """A detour around rough water is the point; refusing to sail it is not.

        With a clear route available the planner takes it; with rough water the
        only option it still returns a route, because CAUTION is a verdict a
        skipper is allowed to act on and NO-GO is not.
        """
        detour = make_lattice(["..~..", "~~~~~", "....."])
        path = R.astar(detour, (1, 0), (1, 4))
        assert path is not None
        rows = {i for i, _ in path}
        # It should prefer the calm bottom row over the rough middle one.
        assert 2 in rows

        forced = make_lattice(["~~~~~"])
        assert R.astar(forced, (0, 0), (0, 4)) is not None


class TestOptimality:
    def test_astar_finds_the_cheapest_route_not_merely_one(self) -> None:
        """Checked against exhaustive Dijkstra over the same lattice.

        The heuristic is plain great-circle distance with no risk term precisely
        so that it stays admissible; this is the test that would catch someone
        "speeding it up" by inflating it.
        """
        lattice = make_lattice(
            [
                ".....",
                ".~~~.",
                ".~#~.",
                ".~~~.",
                ".....",
            ]
        )
        source, target = (0, 0), (4, 4)
        path = R.astar(lattice, source, target)
        assert path is not None

        def cost(route: list[tuple[int, int]]) -> float:
            from orca.services.geo import geodesic_m

            total = 0.0
            for a, b in itertools.pairwise(route):
                na, nb = lattice.nodes[a], lattice.nodes[b]
                total += geodesic_m(na.lat, na.lon, nb.lat, nb.lon) * R._penalty(nb.risk)
            return total

        import heapq

        # Dijkstra: the same cost function, no heuristic at all.
        best: dict[tuple[int, int], float] = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            g, current = heapq.heappop(queue)
            if g > best.get(current, math.inf):
                continue
            node = lattice.nodes[current]
            for di, dj in R._NEIGHBOURS:
                key = (current[0] + di, current[1] + dj)
                neighbour = lattice.nodes.get(key)
                if neighbour is None or not neighbour.passable:
                    continue
                from orca.services.geo import geodesic_m

                step = geodesic_m(node.lat, node.lon, neighbour.lat, neighbour.lon)
                candidate = g + step * R._penalty(neighbour.risk)
                if candidate < best.get(key, math.inf):
                    best[key] = candidate
                    heapq.heappush(queue, (candidate, key))

        assert cost(path) == pytest.approx(best[target], rel=1e-9)


class TestReporting:
    def test_the_direct_line_names_the_cells_that_block_it(self) -> None:
        """ "No route found" alone is indistinguishable from a broken planner."""
        lattice = make_lattice(["..#..", "..#..", "..#.."])
        cells = R._direct_line(lattice, (1, 0), (1, 4))
        refused = [c for c in cells if not c.passable]
        assert refused, "the direct line crosses the wall and must say so"
        assert all(c.reason for c in refused)
        assert any("1.5 m limit" in (c.reason or "") for c in refused)

    def test_collinear_waypoints_are_dropped_and_turns_are_kept(self) -> None:
        """A waypoint every 28 km on an unchanged course is unusable on a plotter."""
        lattice = make_lattice(["......", "......"])
        straight = [(0, j) for j in range(6)]
        assert R._simplify(straight, lattice) == [(0, 0), (0, 5)]

        dogleg = [(0, 0), (0, 1), (0, 2), (1, 3), (1, 4)]
        simplified = R._simplify(dogleg, lattice)
        assert simplified[0] == (0, 0)
        assert simplified[-1] == (1, 4)
        assert (0, 2) in simplified or (1, 3) in simplified

    def test_land_is_reported_as_land_not_as_bad_weather(self) -> None:
        lattice = make_lattice(["LL...", "LL...", "....."])
        land = lattice.nodes[(0, 0)]
        assert land.risk is None
        assert "no wave height" in (land.reason or "")
        assert land.describe()["verdict"] is None


class TestLattice:
    def test_the_lattice_coarsens_rather_than_exceeding_the_call_budget(self) -> None:
        """Every node is an upstream call against a metered budget, so lattice
        size is a cost, not a resolution knob."""
        wide = R.build_lattice(start=(8.0, 72.0), goal=(20.0, 92.0), step_deg=0.25)
        assert len(wide.lats) * len(wide.lons) <= R.MAX_NODES
        assert wide.coarsened is True
        assert wide.step_deg > 0.25

        small = R.build_lattice(start=(13.0, 80.4), goal=(13.2, 80.8), step_deg=0.25)
        assert small.coarsened is False
        assert small.step_deg == pytest.approx(0.25)

    def test_max_nodes_leaves_room_for_both_upstream_apis(self) -> None:
        """The bug this encodes: a 315-node lattice spent its whole minute on the
        marine batch, the forecast batch was refused, and the route came back
        costed on wave height alone — looking like a working router with a
        cautious opinion."""
        from orca.sources.open_meteo import CALLS_PER_MINUTE_BUDGET

        assert R.MAX_NODES * R._APIS_PER_NODE <= CALLS_PER_MINUTE_BUDGET

    def test_snapping_to_water_is_reported_when_it_moves(self) -> None:
        """A harbour is a land cell almost every time, so the snap has to happen —
        but a 40 km move answers a different question than the one asked."""
        lattice = make_lattice(["LLLL", "LLLL", "...."])
        key, note = R._snap_passable(lattice, 12.0, 80.0)
        assert key is not None
        assert "snapped" in note

        onwater, note = R._snap_passable(lattice, 11.5, 80.0)
        assert onwater == (2, 0)
        assert "nearest lattice node" in note

    def test_no_water_at_all_is_a_refusal_with_a_reason(self) -> None:
        lattice = make_lattice(["LL", "LL"])
        key, note = R._snap_passable(lattice, 12.0, 80.0)
        assert key is None
        assert "no passable node" in note


def test_classify_and_the_router_agree_on_the_vessel() -> None:
    """The router must never carry its own idea of what a boat can take."""
    assert classify(8.2).code == "IND-MOT-S"
    assert classify(22.0).code == "IND-MECH-L"


class TestMaritimeWaterMask:
    """Inland cells the Marine API still fills must not become a passage."""

    def test_missing_mask_does_not_invent_a_land_veto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(R, "_water_polygon", lambda: None)
        assert R.in_navigable_water(19.0, 73.0) is None
        assert R._land_reason(19.0, 73.0, wave=1.2) is None

    def test_inland_of_the_eez_is_land_even_with_a_wave_height(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from shapely.geometry import box

        # A strip of "sea" well east of the Indian west-coast interior.
        monkeypatch.setattr(R, "_water_polygon", lambda: box(79.5, 12.0, 81.0, 14.0))
        assert R.in_navigable_water(13.1, 80.3) is True
        assert R.in_navigable_water(19.2, 73.1) is False
        assert "inland" in (R._land_reason(19.2, 73.1, wave=1.4) or "")

    def test_a_hop_whose_midpoint_is_inland_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from shapely.geometry import box

        monkeypatch.setattr(R, "_water_polygon", lambda: box(79.0, 11.0, 80.1, 13.0))
        assert R._edge_crosses_land(12.0, 80.0, 12.0, 80.5) is True
        assert R._edge_crosses_land(12.0, 79.4, 12.0, 79.8) is False

    def test_drawn_path_pins_to_the_marked_sea_point(self) -> None:
        lattice_path = [[80.0, 12.0], [80.0, 12.5]]
        drawn = R._extend_drawn_path(lattice_path, start=(12.05, 80.02), goal=(13.10, 80.40))
        assert drawn[0] == [80.02, 12.05]
        assert drawn[-1] == [80.4, 13.1]

    def test_drawn_path_reaches_the_marked_coastal_click(self) -> None:
        # A* ends on a cell centre; the skipper marked the beach. The line must
        # still terminate on that click, not 28 km offshore.
        lattice_path = [[72.75, 19.00], [72.75, 18.75]]
        drawn = R._extend_drawn_path(lattice_path, start=(19.02, 72.70), goal=(18.92, 72.82))
        assert drawn[0] == [72.70, 19.02]
        assert drawn[-1] == [72.82, 18.92]

    def test_coastal_mark_pins_to_nearest_sea_not_inland(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from shapely.geometry import box

        # A west-coast sea strip; 19.2N 73.1E is inland of it (Pune side).
        monkeypatch.setattr(R, "_water_polygon", lambda: box(72.0, 18.0, 72.8, 20.0))
        pin = R.closest_sea(19.2, 73.1)
        assert pin is not None
        assert pin[1] == pytest.approx(72.8, abs=0.05)
        assert R.in_navigable_water(*pin) is True

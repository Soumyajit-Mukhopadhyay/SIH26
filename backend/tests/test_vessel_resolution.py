"""Phase 1: category-first vessel resolution (no silent 8.2 m default)."""

from __future__ import annotations

import inspect

import pytest

from orca.agents import graph as agent_graph
from orca.agents import tools as agent_tools
from orca.jobs.monitor import Monitor
from orca.services.risk_engine import assess
from orca.services.router import plan as plan_route
from orca.services.thresholds import (
    BOAT_CLASSES,
    by_code,
    classify,
    resolve_vessel,
)


CALM = dict(wave_m=0.4, wind_kn=8.0, visibility_km=10.0, lightning_pct=0.0)


class TestResolveVessel:
    def test_category_only(self):
        r = resolve_vessel(boat_class_code="IND-MOT-S", loa_m=None)
        assert r.source == "category"
        assert r.boat is not None and r.boat.code == "IND-MOT-S"
        assert r.error is None

    def test_loa_only(self):
        r = resolve_vessel(boat_class_code=None, loa_m=8.2)
        assert r.source == "loa"
        assert r.boat == classify(8.2)
        assert r.boat.code == "IND-MOT-S"

    def test_category_wins_over_loa(self):
        r = resolve_vessel(boat_class_code="IND-TRAD", loa_m=8.2)
        assert r.source == "category"
        assert r.boat is not None and r.boat.code == "IND-TRAD"

    def test_neither_is_unknown(self):
        r = resolve_vessel()
        assert r.source == "unknown"
        assert r.boat is None
        assert r.loa_m is None

    def test_invalid_category_does_not_fall_back_to_loa(self):
        r = resolve_vessel(boat_class_code="NOT-A-CLASS", loa_m=8.2)
        assert r.error is not None
        assert r.boat is None
        assert "unknown boat class" in r.error


class TestAssessVesselPrecedence:
    def test_category_only_uses_class_thresholds(self):
        boat = by_code("IND-MOT-S")
        assert boat is not None
        result = assess(**CALM, boat_class_code="IND-MOT-S", loa_m=None)
        assert result.vessel_source == "category"
        assert result.boat_class_code == "IND-MOT-S"
        assert result.loa_m is None
        wave = next(c for c in result.components if c.name == "wave")
        assert wave.limit == boat.max_wave_m

    def test_loa_only_matches_classify(self):
        result = assess(**CALM, loa_m=8.2)
        assert result.vessel_source == "loa"
        assert result.boat_class_code == classify(8.2).code

    def test_category_overrides_loa(self):
        result = assess(**CALM, boat_class_code="IND-TRAD", loa_m=8.2)
        assert result.vessel_source == "category"
        assert result.boat_class_code == "IND-TRAD"
        wave = next(c for c in result.components if c.name == "wave")
        assert wave.limit == by_code("IND-TRAD").max_wave_m  # type: ignore[union-attr]

    def test_neither_is_unverifiable_unknown_not_eight_point_two(self):
        result = assess(**CALM)
        assert result.verdict == "UNVERIFIABLE"
        assert result.vessel_source == "unknown"
        assert result.boat_class_code == "UNKNOWN"
        assert result.loa_m is None
        assert result.boat_class_code != "IND-MOT-S"


class TestAgentAndWatchVessel:
    def test_agent_run_accepts_boat_class_without_loa(self):
        sig = inspect.signature(agent_graph.run)
        assert "boat_class_code" in sig.parameters
        assert sig.parameters["loa_m"].default is None

    def test_assess_risk_tool_kwargs_carry_category(self):
        sig = inspect.signature(agent_tools._assess_risk)
        assert "boat_class_code" in sig.parameters
        assert sig.parameters["loa_m"].default is None

    def test_watch_persists_category_without_loa(self):
        mon = Monitor()
        entry = mon.watch(lat=13.0, lon=80.6, boat_class_code="IND-MOT-S", loa_m=None)
        assert entry.boat_class_code == "IND-MOT-S"
        assert entry.loa_m is None
        described = entry.describe()
        assert described["boat_class_code"] == "IND-MOT-S"
        assert described["loa_m"] is None


class TestRoutingCategoryOnly:
    @pytest.mark.asyncio
    async def test_category_only_resolves_same_class_as_loa(self, monkeypatch):
        """Category-only planning must use the same BoatClass as LOA-derived class."""
        from orca.services import router as route_mod

        captured: list = []

        async def fake_cost(lattice, *, boat):
            captured.append(boat)
            # Minimal: mark nothing passable so plan returns early after costing.
            return lattice, ["wave_height"]

        monkeypatch.setattr(route_mod, "cost_lattice", fake_cost)

        start, goal = (13.1, 80.4), (13.3, 80.6)
        await plan_route(
            start=start, goal=goal, boat_class_code="IND-MOT-S", loa_m=None, step_deg=0.5
        )
        await plan_route(start=start, goal=goal, loa_m=8.2, step_deg=0.5)
        assert len(captured) == 2
        assert captured[0].code == captured[1].code == "IND-MOT-S"
        assert captured[0].max_wave_m == captured[1].max_wave_m
        assert captured[0].max_wind_kn == captured[1].max_wind_kn

    @pytest.mark.asyncio
    async def test_unknown_vessel_refuses_without_inventing_class(self):
        result = await plan_route(
            start=(13.1, 80.4), goal=(13.3, 80.6), loa_m=None, boat_class_code=None
        )
        assert result["ok"] is False
        assert result.get("vessel_unknown") is True
        assert result["boat_class"] == "UNKNOWN"


def test_boat_class_table_unchanged_codes():
    codes = {b.code for b in BOAT_CLASSES}
    assert codes == {
        "IND-TRAD",
        "IND-MOT-S",
        "IND-MECH-S",
        "IND-MECH-L",
        "IND-DEEPSEA",
    }

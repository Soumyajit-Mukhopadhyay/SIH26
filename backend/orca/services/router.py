"""Safe-passage routing: A* over a lattice the risk engine costs.

The claim this module has to support is narrow and checkable: **ORCA does not
route around weather it dislikes, it routes around cells its own rule engine
refuses.** So there is exactly one source of truth about whether a cell is
passable — :func:`orca.services.risk_engine.assess`, the same pure function that
produces the GO / CAUTION / NO-GO on screen — and this file contains no
thresholds of its own.

Three design decisions worth stating:

**The land mask is two tests, both required.** Open-Meteo's Marine API often
returns a wave height for coastal and even inland 0.25° cells (the model
interpolates from nearby sea). Treating "wave is not None" as water is how a
Mumbai–Gujarat passage walks across Maharashtra. So a cell is water only when
it has a wave height *and* sits inside India's EEZ polygon — the maritime
area, not the landward side of the coastline. Edges are checked the same way
at their midpoint so an 8-connected hop cannot cut a peninsula.

**A rejected route is an answer.** When no passage exists the caller gets the
reason, node by node: this is why the direct line fails, this is what blocks
every alternative, and here is what would have to change. "No route found" on
its own is indistinguishable from a broken planner.

**The detour is reported against the direct line.** A route that is 4% longer
than the great circle and avoids a 3.1 m sea is worth taking; one that is 180%
longer is a signal that the honest answer was "not today". The caller gets the
percentage and decides.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from orca.services.geo import geodesic_m
from orca.services.risk_engine import RiskResult, assess
from orca.services.thresholds import BoatClass, resolve_vessel
from orca.sources.open_meteo import CALLS_PER_MINUTE_BUDGET

log = logging.getLogger(__name__)

ROUTER_VERSION = "orca-router-2026.09"

#: Lattice spacing. 0.25° is ~28 km, which is both the scale at which the wave
#: model has independent information and about the scale at which a fishing boat
#: can usefully change its mind.
DEFAULT_STEP_DEG = 0.25

#: How far either side of the direct line the lattice extends. Wide enough for a
#: real detour, narrow enough that the node count stays affordable — the lattice
#: is sampled from a live API with a metered budget, so its size is a cost.
DEFAULT_CORRIDOR_DEG = 1.1

#: How many upstream APIs each node has to be sampled from. Waves come from the
#: Marine API and wind/visibility/convection from the Forecast API, so a node
#: costs two calls, not one.
_APIS_PER_NODE = 2

#: Hard ceiling on lattice nodes, derived from Open-Meteo's per-minute budget
#: rather than picked.
#:
#: The first version used a flat 900, and a 315-node lattice then spent 315 calls
#: on the marine batch and had the forecast batch refused for exceeding the
#: minute's budget. The route still came back — costed on wave height alone, with
#: wind, visibility and convection missing at every node, and UNVERIFIABLE
#: throughout. It looked like a working router with a cautious opinion. This is
#: why the ceiling is computed and why `missing_variables` below is reported.
MAX_NODES = int((CALLS_PER_MINUTE_BUDGET * 0.8) // _APIS_PER_NODE)

#: How much a risky cell is allowed to lengthen the route. At 4.0 a cell scoring
#: 40/100 costs 2.4× its distance, so the planner will accept a substantial
#: detour to avoid it — but a 300% detour still loses to going through, which is
#: why the veto is a hard block and not a large number. Encoding "never" as a big
#: cost is how routers end up sailing through a cyclone to save a day.
DETOUR_WEIGHT = 4.0

#: Eight-connected. Four-connected produces visible staircase routes that no
#: skipper would follow and that overstate distance by up to 41%.
_NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _water_polygon():
    """India's EEZ — sea only. ``None`` when the geofence index is not loaded."""
    try:
        from orca.services.geofence import index as geofence_index
    except Exception:  # noqa: BLE001
        return None
    for fence in geofence_index.fences():
        if fence.key == "eez_india" and fence.is_area:
            return fence.geometry
    return None


def in_navigable_water(lat: float, lon: float) -> bool | None:
    """Whether ``(lat, lon)`` is inside India's EEZ water mask.

    ``None`` means the mask is unavailable — callers must not treat that as
    clearance, but they also must not invent a land veto from silence.
    """
    polygon = _water_polygon()
    if polygon is None:
        return None
    from shapely.geometry import Point

    # ``covers`` includes the coastline itself; ``contains`` would reject a
    # harbour click that sits on the EEZ boundary.
    return bool(polygon.covers(Point(lon, lat)))


def _land_reason(lat: float, lon: float, wave: float | None) -> str | None:
    """Why this cell is not navigable water, or ``None`` if it may be routed."""
    if wave is None:
        return "no wave height here — land, or outside the wave model's domain"
    sea = in_navigable_water(lat, lon)
    if sea is False:
        return "inland of India's EEZ — not navigable water"
    return None


def _edge_crosses_land(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> bool:
    """True when the hop itself leaves the sea (diagonal cut across a peninsula)."""
    mid = in_navigable_water((a_lat + b_lat) / 2.0, (a_lon + b_lon) / 2.0)
    return mid is False


def closest_sea(lat: float, lon: float) -> tuple[float, float] | None:
    """The requested point if it is at sea, else the nearest EEZ-water point.

    Coastal clicks sit on the land/water line. The lattice then ends ~28 km
    offshore at a cell centre. Pinning the drawn path here is what actually
    reaches the place the skipper marked.
    """
    if in_navigable_water(lat, lon) is True:
        return (lat, lon)
    polygon = _water_polygon()
    if polygon is None:
        return None
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    # First geometry is the sea; that is the pin. The second is the query
    # point itself — unpacking the other way left inland clicks unchanged.
    near, _ = nearest_points(polygon, Point(lon, lat))
    return (float(near.y), float(near.x))


def _marked_vertex(lat: float, lon: float) -> list[float]:
    return [round(lon, 4), round(lat, 4)]


def _extend_drawn_path(
    lattice_path: list[list[float]],
    start: tuple[float, float],
    goal: tuple[float, float],
) -> list[list[float]]:
    """Prepend/append the marked ends so the line reaches the click, not only the cell.

    A* still only walks passable sea cells. These stubs are drawing, not a claim
    that the last few hundred metres were costed — a harbour click sits on the
    land/water line and the lattice cannot land on it.
    """
    out = list(lattice_path)
    first = _marked_vertex(*start)
    last = _marked_vertex(*goal)
    if not out or geodesic_m(start[0], start[1], out[0][1], out[0][0]) > 200:
        out = [first, *out]
    if not out or geodesic_m(goal[0], goal[1], out[-1][1], out[-1][0]) > 200:
        out = [*out, last]
    return out


@dataclass(slots=True)
class Node:
    """One lattice cell, with the verdict the rule engine gave it."""

    i: int
    j: int
    lat: float
    lon: float
    conditions: dict[str, float | None]
    risk: RiskResult | None
    passable: bool
    reason: str | None

    def describe(self) -> dict[str, Any]:
        return {
            "lat": round(self.lat, 4),
            "lon": round(self.lon, 4),
            "passable": self.passable,
            "reason": self.reason,
            "verdict": self.risk.verdict if self.risk else None,
            "index": round(self.risk.index, 1) if self.risk else None,
            "wave_m": self.conditions.get("wave_height"),
            "wind_kn": (
                round(w, 1) if (w := self.conditions.get("wind_speed")) is not None else None
            ),
        }


@dataclass(slots=True)
class Lattice:
    lats: list[float]
    lons: list[float]
    nodes: dict[tuple[int, int], Node] = field(default_factory=dict)
    step_deg: float = DEFAULT_STEP_DEG
    coarsened: bool = False

    def nearest(self, lat: float, lon: float) -> tuple[int, int]:
        i = min(range(len(self.lats)), key=lambda k: abs(self.lats[k] - lat))
        j = min(range(len(self.lons)), key=lambda k: abs(self.lons[k] - lon))
        return i, j


def _axis(low: float, high: float, step: float) -> list[float]:
    """Inclusive axis from ``low`` to ``high`` on a ``step`` grid."""
    start = math.floor(low / step) * step
    count = math.ceil((high - start) / step) + 1
    return [round(start + k * step, 6) for k in range(count)]


def build_lattice(
    *,
    start: tuple[float, float],
    goal: tuple[float, float],
    step_deg: float = DEFAULT_STEP_DEG,
    corridor_deg: float = DEFAULT_CORRIDOR_DEG,
) -> Lattice:
    """A lattice covering the corridor between two points.

    Coarsens itself rather than exceeding :data:`MAX_NODES`. That matters because
    every node is an upstream API call against a metered budget: a long route at
    a fine step would silently spend thousands of calls and then fail partway
    through, leaving holes in the cost field that look like calm water.
    """
    step = step_deg
    coarsened = False
    for _ in range(6):
        lats = _axis(
            min(start[0], goal[0]) - corridor_deg, max(start[0], goal[0]) + corridor_deg, step
        )
        lons = _axis(
            min(start[1], goal[1]) - corridor_deg, max(start[1], goal[1]) + corridor_deg, step
        )
        if len(lats) * len(lons) <= MAX_NODES:
            return Lattice(lats=lats, lons=lons, step_deg=step, coarsened=coarsened)
        step *= 1.5
        coarsened = True

    return Lattice(lats=lats, lons=lons, step_deg=step, coarsened=True)


async def cost_lattice(lattice: Lattice, *, boat: BoatClass) -> tuple[Lattice, list[str]]:
    """Sample conditions at every node and ask the rule engine about each one.

    Returns the lattice and the list of variables that came back empty at EVERY
    node. That list is the difference between "the router considered wind and
    found it acceptable" and "the router never saw the wind" — states which
    produce very similar-looking routes and completely different levels of trust.
    """
    from orca.sources.open_meteo import ROUTING_FIELDS, sample_conditions

    lats: list[float] = []
    lons: list[float] = []
    keys: list[tuple[int, int]] = []
    for i, lat in enumerate(lattice.lats):
        for j, lon in enumerate(lattice.lons):
            lats.append(lat)
            lons.append(lon)
            keys.append((i, j))

    samples = await sample_conditions(lats, lons)

    for (i, j), lat, lon, conditions in zip(keys, lats, lons, samples, strict=True):
        wave = conditions.get("wave_height")
        land = _land_reason(lat, lon, wave)
        if land is not None:
            # Wave silence *or* an inland cell the Marine API still filled.
            lattice.nodes[(i, j)] = Node(
                i=i,
                j=j,
                lat=lat,
                lon=lon,
                conditions=conditions,
                risk=None,
                passable=False,
                reason=land,
            )
            continue

        visibility_m = conditions.get("visibility")
        risk = assess(
            wave_m=wave,
            wind_kn=conditions.get("wind_speed"),
            visibility_km=None if visibility_m is None else visibility_m / 1000.0,
            cape_j_kg=conditions.get("convective_energy"),
            boat_class=boat,
        )
        vetoed = bool(risk.vetoes)
        lattice.nodes[(i, j)] = Node(
            i=i,
            j=j,
            lat=lat,
            lon=lon,
            conditions=conditions,
            risk=risk,
            passable=not vetoed,
            reason=None if not vetoed else "; ".join(risk.vetoes),
        )

    # A variable absent from every sample means a whole upstream batch was lost or
    # refused, not that the sea is calm. Wave height is excluded: absent there is
    # the land mask, which is expected and handled above.
    missing = sorted(
        variable
        for variable in ROUTING_FIELDS
        if variable != "wave_height" and all(sample.get(variable) is None for sample in samples)
    )
    if missing:
        log.warning(
            "routing lattice is missing %s at every one of %d nodes — the route will be "
            "costed on wave height alone",
            ", ".join(missing),
            len(samples),
        )

    return lattice, missing


def _penalty(risk: RiskResult | None) -> float:
    """Distance multiplier for crossing a cell.

    Quadratic in the shortfall from a perfect score, so a mildly worse cell is
    barely avoided and a nearly-vetoed one is avoided hard. Linear made the
    planner indifferent between one bad cell and three mediocre ones, which is
    not how a skipper thinks about a squall.
    """
    if risk is None:
        return math.inf
    shortfall = max(0.0, (100.0 - risk.index) / 100.0)
    return 1.0 + DETOUR_WEIGHT * shortfall * shortfall


def _snap_passable(lattice: Lattice, lat: float, lon: float) -> tuple[tuple[int, int] | None, str]:
    """Nearest passable node, and a note about how far it moved.

    A departure point inside a harbour lands on a land cell almost every time,
    so refusing to snap would make the router useless exactly where it is used.
    But the snap is reported: a 40 km move is a different answer to the question
    that was asked.
    """
    i0, j0 = lattice.nearest(lat, lon)
    best: tuple[float, tuple[int, int]] | None = None
    for (i, j), node in lattice.nodes.items():
        if not node.passable:
            continue
        distance = geodesic_m(lat, lon, node.lat, node.lon)
        if best is None or distance < best[0]:
            best = (distance, (i, j))

    if best is None:
        return None, "no passable node anywhere in the corridor"
    distance, key = best
    if key == (i0, j0):
        return key, "on the nearest lattice node"
    return key, f"snapped {distance / 1000:.1f} km to the nearest passable water"


def _reconstruct(came: dict[tuple[int, int], tuple[int, int]], end: tuple[int, int]):
    path = [end]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    return path


def _simplify(path: list[tuple[int, int]], lattice: Lattice) -> list[tuple[int, int]]:
    """Drop nodes that lie on a straight run.

    A 30-node lattice path is 30 waypoints, and a waypoint list that changes
    course every 28 km when the course has not changed is unusable on a chart
    plotter. Collinear runs collapse to their endpoints; every turn survives.
    """
    if len(path) < 3:
        return path
    kept = [path[0]]
    for previous, current, following in zip(path, path[1:], path[2:], strict=False):
        before = (current[0] - previous[0], current[1] - previous[1])
        after = (following[0] - current[0], following[1] - current[1])
        if before != after:
            kept.append(current)
    kept.append(path[-1])
    return kept


def _leg_distance_m(lattice: Lattice, path: list[tuple[int, int]]) -> float:
    total = 0.0
    for a, b in itertools.pairwise(path):
        na, nb = lattice.nodes[a], lattice.nodes[b]
        total += geodesic_m(na.lat, na.lon, nb.lat, nb.lon)
    return total


def astar(
    lattice: Lattice, source: tuple[int, int], target: tuple[int, int]
) -> list[tuple[int, int]] | None:
    """A* with a geodesic heuristic.

    The heuristic is plain great-circle distance, with no risk term. That makes
    it admissible — every edge costs at least its own distance, since the penalty
    multiplier has a floor of 1.0 — which is what makes the result a genuinely
    optimal route under the cost model rather than merely a plausible one. An
    inflated heuristic would be faster and would quietly stop being optimal.
    """
    goal_node = lattice.nodes[target]

    def h(key: tuple[int, int]) -> float:
        node = lattice.nodes[key]
        return geodesic_m(node.lat, node.lon, goal_node.lat, goal_node.lon)

    open_set: list[tuple[float, tuple[int, int]]] = [(h(source), source)]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    g: dict[tuple[int, int], float] = {source: 0.0}
    closed: set[tuple[int, int]] = set()

    while open_set:
        _, current = heapq.heappop(open_set)
        if current == target:
            return _reconstruct(came, current)
        if current in closed:
            continue
        closed.add(current)

        node = lattice.nodes[current]
        for di, dj in _NEIGHBOURS:
            key = (current[0] + di, current[1] + dj)
            neighbour = lattice.nodes.get(key)
            if neighbour is None or not neighbour.passable or key in closed:
                continue
            if _edge_crosses_land(node.lat, node.lon, neighbour.lat, neighbour.lon):
                continue
            step = geodesic_m(node.lat, node.lon, neighbour.lat, neighbour.lon)
            tentative = g[current] + step * _penalty(neighbour.risk)
            if tentative < g.get(key, math.inf):
                came[key] = current
                g[key] = tentative
                heapq.heappush(open_set, (tentative + h(key), key))

    return None


def _direct_line(lattice: Lattice, source: tuple[int, int], target: tuple[int, int]):
    """The lattice nodes a straight run would cross, for the rejection rationale.

    Bresenham on the lattice rather than a true great circle: over a few hundred
    kilometres at these latitudes the difference is far below one cell, and the
    point is to name the cells the direct line hits, which has to be expressed in
    cells to mean anything.
    """
    (i0, j0), (i1, j1) = source, target
    di, dj = abs(i1 - i0), abs(j1 - j0)
    si, sj = (1 if i1 > i0 else -1), (1 if j1 > j0 else -1)
    error = di - dj
    cells = []
    i, j = i0, j0
    for _ in range(di + dj + 2):
        node = lattice.nodes.get((i, j))
        if node is not None:
            cells.append(node)
        if (i, j) == (i1, j1):
            break
        doubled = error * 2
        if doubled > -dj:
            error -= dj
            i += si
        if doubled < di:
            error += di
            j += sj
    return cells


async def plan(
    *,
    start: tuple[float, float],
    goal: tuple[float, float],
    loa_m: float | None = None,
    boat_class: BoatClass | None = None,
    boat_class_code: str | None = None,
    speed_kn: float = 8.0,
    step_deg: float = DEFAULT_STEP_DEG,
    corridor_deg: float = DEFAULT_CORRIDOR_DEG,
) -> dict[str, Any]:
    """Plan a passage, or explain in detail why there is not one.

    Vessel precedence: explicit ``boat_class`` / ``boat_class_code`` → ``loa_m``
    → UNKNOWN (no silent 8.2 m / IND-MOT-S).
    """
    if boat_class is None:
        resolved = resolve_vessel(boat_class_code=boat_class_code, loa_m=loa_m)
        if resolved.error or resolved.boat is None:
            return {
                "ok": False,
                "vessel_unknown": True,
                "router_version": ROUTER_VERSION,
                "boat_class": "UNKNOWN",
                "reason": (
                    resolved.error
                    or (
                        "Vessel type not specified. ORCA will not invent a boat class to "
                        "claim a route is safe."
                    )
                ),
            }
        boat = resolved.boat
    else:
        boat = boat_class
    lattice = build_lattice(start=start, goal=goal, step_deg=step_deg, corridor_deg=corridor_deg)
    _, missing_variables = await cost_lattice(lattice, boat=boat)

    source, source_note = _snap_passable(lattice, *start)
    target, target_note = _snap_passable(lattice, *goal)

    total_nodes = len(lattice.nodes)
    passable = sum(1 for n in lattice.nodes.values() if n.passable)
    water = sum(1 for n in lattice.nodes.values() if n.risk is not None)
    blocked_by_veto = water - passable

    lattice_report = {
        "step_deg": round(lattice.step_deg, 4),
        "coarsened": lattice.coarsened,
        "nodes": total_nodes,
        "water_nodes": water,
        "passable_nodes": passable,
        "vetoed_water_nodes": blocked_by_veto,
        "note": (
            f"{total_nodes} nodes at {lattice.step_deg:.2f}° ({water} on water, "
            f"{blocked_by_veto} of those refused by the rule engine)"
        )
        + (
            f". The step was coarsened from {step_deg:.2f}° to keep the upstream sampling "
            "within budget, which makes the router MORE conservative rather than less: one "
            f"vetoed cell now blocks a {lattice.step_deg * 111:.0f} km swath that a boat might "
            "have threaded. A refusal at this resolution is worth re-checking on a shorter leg."
            if lattice.coarsened
            else ""
        ),
    }

    # Surfaced at the top level, not buried in the lattice report: if the router
    # never saw the wind, that governs how much any of this is worth.
    degraded = (
        None
        if not missing_variables
        else (
            f"{', '.join(missing_variables)} could not be sampled anywhere on this lattice, so "
            "the route was costed on wave height alone. Every cell is UNVERIFIABLE for that "
            "reason rather than because the sea is bad — treat the route as indicative and "
            "check an official bulletin before sailing."
        )
    )

    if source is None or target is None:
        return {
            "ok": False,
            "router_version": ROUTER_VERSION,
            "boat_class": boat.label,
            "lattice": lattice_report,
            "degraded": degraded,
            "reason": (
                "There is no water in this corridor that the rule engine will clear for "
                f"a {boat.label}."
                if water
                # `water` counts nodes that got a risk assessment, so zero of them
                # means one of two completely different things. Saying "check the
                # coordinates" when the truth is "we were rate-limited and sampled
                # nothing" sends the reader to look for a bug in their own input.
                else (
                    "Not one node in this corridor could be costed — every upstream sample was "
                    "refused or empty, so the router does not know whether there is water here. "
                    "This is a sampling failure, not a geographic finding. Retry in a minute: "
                    "the most common cause is the upstream per-minute budget already being spent."
                    if len(missing_variables) >= 2
                    else "There is no water in this corridor at all — check the coordinates."
                )
            ),
            "blocked_by": sorted(
                {n.reason for n in lattice.nodes.values() if n.reason and n.passable is False}
            )[:6],
        }

    direct_cells = _direct_line(lattice, source, target)
    refused = [c for c in direct_cells if not c.passable]
    direct_m = geodesic_m(
        lattice.nodes[source].lat,
        lattice.nodes[source].lon,
        lattice.nodes[target].lat,
        lattice.nodes[target].lon,
    )

    path = astar(lattice, source, target)
    if path is None:
        return {
            "ok": False,
            "router_version": ROUTER_VERSION,
            "boat_class": boat.label,
            "lattice": lattice_report,
            "degraded": degraded,
            "reason": (
                "No passage exists in this corridor: every route from start to finish crosses "
                "a cell the rule engine vetoes for this vessel class. A wider corridor may "
                "find one, but a detour that large is usually the answer 'not today'."
            ),
            "direct_line": [c.describe() for c in direct_cells],
            "refused_on_direct_line": [c.describe() for c in refused],
            "what_would_change_it": sorted(
                {r for c in refused for r in (c.reason or "").split("; ")}
            )[:6],
        }

    simplified = _simplify(path, lattice)
    route_m = _leg_distance_m(lattice, path)
    along = [lattice.nodes[key] for key in path]
    indices = [n.risk.index for n in along if n.risk]
    verdicts = [n.risk.verdict for n in along if n.risk]

    # The worst verdict on the route governs, not the average. A route that is
    # GO for 90% of its length and CAUTION for one leg is a CAUTION passage.
    order = {"GO": 0, "CAUTION": 1, "NO-GO": 2, "UNVERIFIABLE": 3}
    worst = max(verdicts, key=lambda v: order.get(v, 0)) if verdicts else "UNVERIFIABLE"

    lattice_coords = [
        [round(lattice.nodes[k].lon, 4), round(lattice.nodes[k].lat, 4)] for k in path
    ]
    drawn = _extend_drawn_path(lattice_coords, start, goal)
    if len(drawn) >= 2:
        pinned = 0.0
        for a, b in itertools.pairwise(drawn):
            pinned += geodesic_m(a[1], a[0], b[1], b[0])
        route_m = pinned

    dest_pin = closest_sea(*goal)
    if dest_pin is not None and geodesic_m(*goal, *dest_pin) > 200:
        target_note = (
            f"closest sea to the marked point "
            f"({geodesic_m(*goal, *dest_pin) / 1000:.1f} km) — the costed route stays "
            "on water; the last drawn stub reaches the mark"
        )
    else:
        target_note = "at the marked destination"

    hours = (route_m / 1852.0) / max(0.5, speed_kn)

    return {
        "ok": True,
        "router_version": ROUTER_VERSION,
        "boat_class": boat.label,
        "thresholds_version": along[0].risk.thresholds_version if along[0].risk else None,
        "lattice": lattice_report,
        "degraded": degraded,
        "start_note": source_note,
        "goal_note": target_note,
        "waypoints": [lattice.nodes[key].describe() for key in simplified],
        "path": drawn,
        "distance_nm": round(route_m / 1852.0, 1),
        "direct_nm": round(direct_m / 1852.0, 1),
        "detour_pct": round(100.0 * (route_m - direct_m) / max(1.0, direct_m), 1),
        "duration_h": round(hours, 2),
        "speed_kn": speed_kn,
        "worst_verdict": worst,
        "worst_index": round(min(indices), 1) if indices else None,
        "mean_index": round(sum(indices) / len(indices), 1) if indices else None,
        "refused_on_direct_line": [c.describe() for c in refused],
        "why_this_route": _rationale(refused, route_m, direct_m, worst),
        "disclaimer": (
            "Advisory only. Every cell on this route was cleared by ORCA's deterministic rule "
            "engine against the same cited thresholds used for a point verdict — not by a "
            "language model. It supplements, never replaces, official IMD and INCOIS bulletins "
            "and the skipper's own judgement."
        ),
    }


def _rationale(refused: list[Node], route_m: float, direct_m: float, worst: str) -> str:
    detour = 100.0 * (route_m - direct_m) / max(1.0, direct_m)
    if not refused:
        return (
            f"The direct line is clear for this vessel class, so the route follows it "
            f"({detour:.0f}% longer than the great circle, which is lattice geometry rather "
            "than avoidance)."
        )
    reasons = sorted({(cell.reason or "unspecified").split(";")[0].strip() for cell in refused})
    return (
        f"The direct line crosses {len(refused)} cell(s) the rule engine refuses "
        f"({'; '.join(reasons[:3])}). This route goes around them at a cost of "
        f"{detour:.0f}% extra distance, and is {worst} throughout."
    )

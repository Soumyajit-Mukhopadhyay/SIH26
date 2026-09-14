"""Geofencing: which side of a maritime boundary are you on, and when will you
cross it.

Three design decisions, each with a reason:

**1. STRtree coarse filter, then exact geometry.** The plan's replacement for
Redis ``GEOSEARCH``. At ORCA's fence count — one EEZ, four IMBL treaty segments,
a handful of neighbours, plus MPAs — an in-memory R-tree answers in microseconds,
which is faster than a Redis round-trip plus a PostGIS query. The exact test
still runs on the full-resolution geometry, because a simplified boundary would
put the line in the wrong place and the entire purpose is telling someone which
side of it they are on.

**2. A state machine, so events fire on TRANSITIONS.** A boat sitting 400 m from
the IMBL must not generate an alert every time its GPS reports. The machine
tracks ``outside -> approaching -> crossed -> inside -> exited`` per fence per
trip, and only a change of state is an event. Without this the proactive alert
rail becomes noise and gets muted, which is the same as not having it.

**3. Time-to-cross on the current heading**, not distance alone. "You are 11.4 km
from the line" requires the listener to do arithmetic while steering. "On your
current heading you cross into Sri Lankan waters in 38 minutes" does not.

Distances all go through :func:`orca.services.geo.geodesic_m`. Nothing here
subtracts coordinates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from orca.provenance import Citation, Provenance, Provider, utcnow
from orca.services.geo import bearing_deg, compass_point, destination, geodesic_m

log = logging.getLogger(__name__)

#: Within this distance of a boundary, a vessel is "approaching" it. 2 NM is the
#: distance at which a skipper can still turn comfortably at fishing speed.
APPROACHING_M = 3704.0  # 2 nautical miles

#: How far ahead to project the current heading when computing time-to-cross.
#: Beyond ~6 hours a constant heading is a fiction, so the projection stops.
PROJECTION_HOURS = 6.0


class FenceState(StrEnum):
    """Per-fence state for one vessel. Events fire on transitions between these."""

    OUTSIDE = "outside"
    APPROACHING = "approaching"
    CROSSED = "crossed"
    INSIDE = "inside"
    EXITED = "exited"


@dataclass(slots=True)
class FenceRecord:
    """A fence, prepared for fast repeated queries."""

    key: str
    name: str
    kind: str
    consequence: str
    authority: str
    geometry: BaseGeometry
    mrgid: int | None = None
    length_km: float | None = None
    #: True for a polygon (containment is meaningful), False for a line.
    is_area: bool = False


@dataclass(slots=True)
class Proximity:
    """How a position relates to one fence."""

    fence: FenceRecord
    inside: bool
    distance_m: float
    bearing_to_deg: float
    #: (lat, lon) of the nearest point on the boundary.
    nearest: tuple[float, float]
    state: FenceState
    time_to_cross_min: float | None = None
    closing: bool | None = None
    #: Distance to the point where the current heading actually crosses, which is
    #: NOT the nearest point on the boundary. Reporting the nearest distance
    #: beside a crossing time read as a contradiction: "2.9 km away, 1.9 h to
    #: cross" at 8 knots is nonsense, because the crossing was 28 km ahead.
    cross_distance_m: float | None = None
    cross_point: tuple[float, float] | None = None

    def narrative(self) -> str:
        """The sentence the advisory actually uses."""
        km = self.distance_m / 1000.0
        compass = compass_point(self.bearing_to_deg)
        inside_area = self.fence.is_area and self.inside

        if self.time_to_cross_min is not None:
            minutes = self.time_to_cross_min
            when = f"{minutes:.0f} minutes" if minutes < 90 else f"{minutes / 60:.1f} hours"
            # The distance quoted must be to the CROSSING, not to the nearest
            # point, or the sentence contradicts itself: "2.9 km away, 1.9 h to
            # cross" is impossible at fishing speed, and that is what a reader saw.
            ahead_km = self.cross_distance_m / 1000.0 if self.cross_distance_m is not None else km
            if inside_area:
                # "Leave", not "cross": you are on the inside, and leaving is the
                # thing that changes your jurisdiction.
                return (
                    f"You are inside {self.fence.name}. On your current heading you leave it "
                    f"in {when} ({ahead_km:.1f} km ahead)."
                )
            return (
                f"On your current heading you cross {self.fence.name} in {when} "
                f"({ahead_km:.1f} km ahead)."
            )

        if inside_area:
            return f"You are inside {self.fence.name}, {km:.1f} km from its boundary."

        if self.state is FenceState.APPROACHING:
            return f"You are {km:.1f} km from {self.fence.name}, bearing {compass}."

        return f"{self.fence.name} is {km:.1f} km away, bearing {compass}."

    def describe(self) -> dict[str, Any]:
        return {
            "fence": self.fence.key,
            "name": self.fence.name,
            "kind": self.fence.kind,
            "inside": self.inside,
            "distance_km": round(self.distance_m / 1000.0, 3),
            "bearing_deg": round(self.bearing_to_deg, 1),
            "compass": compass_point(self.bearing_to_deg),
            "nearest_point": {"lat": round(self.nearest[0], 5), "lon": round(self.nearest[1], 5)},
            "state": self.state.value,
            "time_to_cross_min": (
                None if self.time_to_cross_min is None else round(self.time_to_cross_min, 1)
            ),
            "closing": self.closing,
            "cross_distance_km": (
                None if self.cross_distance_m is None else round(self.cross_distance_m / 1000.0, 3)
            ),
            "cross_point": (
                None
                if self.cross_point is None
                else {"lat": round(self.cross_point[0], 5), "lon": round(self.cross_point[1], 5)}
            ),
            "consequence": self.fence.consequence,
            "authority": self.fence.authority,
            "narrative": self.narrative(),
        }


class GeofenceIndex:
    """The fence set, with an STRtree over it.

    Built once at startup. ``query`` is the hot path and does no I/O.
    """

    def __init__(self) -> None:
        self._fences: list[FenceRecord] = []
        self._tree: STRtree | None = None
        self._built_at = None

    # ------------------------------------------------------------------ build
    def load(self, payload: dict[str, Any]) -> int:
        """Build the index from ``ensure_reference_geography``'s payload."""
        records: list[FenceRecord] = []
        for entry in payload.get("fences", []):
            geometry_json = entry.get("geometry")
            if not geometry_json:
                continue
            try:
                geometry = shape(geometry_json)
            except Exception as exc:  # noqa: BLE001 — one bad fence must not kill the set
                log.warning("skipping fence %s: unparseable geometry: %s", entry.get("key"), exc)
                continue
            if geometry.is_empty:
                continue
            records.append(
                FenceRecord(
                    key=str(entry["key"]),
                    name=str(entry["name"]),
                    kind=str(entry["kind"]),
                    consequence=str(entry.get("consequence", "")),
                    authority=str(entry.get("authority", "")),
                    geometry=geometry,
                    mrgid=entry.get("mrgid"),
                    length_km=entry.get("length_km"),
                    is_area=geometry.geom_type in {"Polygon", "MultiPolygon"},
                )
            )

        self._fences = records
        self._tree = STRtree([r.geometry for r in records]) if records else None
        self._built_at = utcnow()
        log.info(
            "geofence index built: %d fence(s) — %s",
            len(records),
            ", ".join(f"{r.key}({r.geometry.geom_type})" for r in records[:6]),
        )
        return len(records)

    @property
    def ready(self) -> bool:
        return bool(self._fences)

    def fences(self) -> list[FenceRecord]:
        return list(self._fences)

    def describe(self) -> dict[str, Any]:
        return {
            "fence_count": len(self._fences),
            "built_at": self._built_at.isoformat() if self._built_at else None,
            "index": "shapely STRtree",
            "approaching_threshold_km": round(APPROACHING_M / 1000, 2),
            "fences": [
                {
                    "key": r.key,
                    "name": r.name,
                    "kind": r.kind,
                    "geometry_type": r.geometry.geom_type,
                    "length_km": r.length_km,
                }
                for r in self._fences
            ],
        }

    # ------------------------------------------------------------------ query
    def _candidates(self, point: Point, radius_deg: float) -> list[FenceRecord]:
        """Coarse filter. The STRtree replaces Redis GEOSEARCH here."""
        if self._tree is None:
            return []
        box = point.buffer(radius_deg, quad_segs=4)
        indices = self._tree.query(box)
        return [self._fences[int(i)] for i in indices]

    def check(
        self,
        lat: float,
        lon: float,
        *,
        heading_deg: float | None = None,
        speed_kn: float | None = None,
        radius_km: float = 120.0,
        previous: dict[str, str] | None = None,
    ) -> list[Proximity]:
        """How this position relates to every nearby fence.

        ``previous`` maps fence key to its last state, which is what turns this
        from a proximity report into a transition detector.
        """
        point = Point(lon, lat)
        # A degree of longitude is shortest at the top of the AOI (25 N), so
        # convert with the worst case to be sure the coarse filter never excludes
        # a fence the exact test would have matched.
        radius_deg = radius_km / (111.32 * 0.9)

        results: list[Proximity] = []
        for fence in self._candidates(point, radius_deg):
            # ``covers`` counts the coastline itself; ``contains`` rejected a
            # harbour click on the EEZ rim and left the panel stuck on OUTSIDE.
            inside = bool(fence.is_area and fence.geometry.covers(point))
            # `.boundary` for BOTH Polygon and MultiPolygon. Measuring against
            # the polygon itself returns 0 for any interior point, so a boat deep
            # inside the EEZ reported "0.0 km from its boundary" — which reads as
            # "you are on the line" and is the opposite of the truth.
            nearest_geom = fence.geometry.boundary if fence.is_area else fence.geometry
            if nearest_geom is None or nearest_geom.is_empty:
                nearest_geom = fence.geometry

            # shapely works in degrees; the *distance* must not. Find the nearest
            # point geometrically, then measure it geodesically.
            from shapely.ops import nearest_points

            try:
                _, near = nearest_points(point, nearest_geom)
            except Exception as exc:  # noqa: BLE001
                # A self-intersecting or degenerate boundary must not take the
                # whole fence set down with it.
                log.warning("nearest_points failed for fence %s: %s", fence.key, exc)
                continue
            near_lat, near_lon = near.y, near.x
            distance_m = geodesic_m(lat, lon, near_lat, near_lon)

            if distance_m > radius_km * 1000:
                continue

            bearing = bearing_deg(lat, lon, near_lat, near_lon)
            time_to_cross, closing, cross_distance, cross_point = _project(
                lat, lon, fence, heading_deg, speed_kn, inside=inside
            )

            state = _state_for(
                inside=inside,
                distance_m=distance_m,
                is_area=fence.is_area,
                previous=(previous or {}).get(fence.key),
            )

            results.append(
                Proximity(
                    fence=fence,
                    inside=inside,
                    distance_m=distance_m,
                    bearing_to_deg=bearing,
                    nearest=(near_lat, near_lon),
                    state=state,
                    time_to_cross_min=time_to_cross,
                    closing=closing,
                    cross_distance_m=cross_distance,
                    cross_point=cross_point,
                )
            )

        # The 200 NM line is the EEZ's outer rim. A boat inside the polygon is
        # on India's side of that line — saying OUTSIDE there looked broken.
        india_inside = any(p.fence.kind == "eez" and p.inside for p in results)
        if india_inside:
            for proximity in results:
                if proximity.fence.kind != "eez_outer":
                    continue
                proximity.inside = True
                if proximity.state == FenceState.OUTSIDE:
                    proximity.state = FenceState.INSIDE

        results.sort(key=lambda p: p.distance_m)
        return results


def _project(
    lat: float,
    lon: float,
    fence: FenceRecord,
    heading_deg: float | None,
    speed_kn: float | None,
    *,
    inside: bool,
) -> tuple[float | None, bool | None, float | None, tuple[float, float] | None]:
    """Minutes until the current heading crosses this fence, and whether it is
    closing at all.

    Built by projecting the track forward and intersecting it with the fence,
    rather than dividing distance by speed. The difference matters: the nearest
    point on a boundary is often not the point you are heading for, and dividing
    distance by speed would promise a crossing on a heading that runs parallel to
    the line.
    """
    if heading_deg is None or speed_kn is None or speed_kn <= 0:
        return None, None, None, None

    reach_m = speed_kn * 1852.0 * PROJECTION_HOURS
    end_lat, end_lon = destination(lat, lon, heading_deg, reach_m)
    track = LineString([(lon, lat), (end_lon, end_lat)])

    target = fence.geometry.boundary if fence.is_area else fence.geometry
    if target is None or target.is_empty:
        return None, None, None, None

    try:
        crossing = track.intersection(target)
    except Exception:  # noqa: BLE001 — a self-intersecting boundary is not fatal
        return None, None, None, None

    if crossing.is_empty:
        # Not on this heading. Report whether they are at least closing, which is
        # still worth saying.
        ahead_lat, ahead_lon = destination(lat, lon, heading_deg, min(reach_m, 5000.0))
        from shapely.ops import nearest_points

        _, near_now = nearest_points(Point(lon, lat), target)
        _, near_ahead = nearest_points(Point(ahead_lon, ahead_lat), target)
        now_m = geodesic_m(lat, lon, near_now.y, near_now.x)
        ahead_m = geodesic_m(ahead_lat, ahead_lon, near_ahead.y, near_ahead.x)
        return None, ahead_m < now_m, None, None

    # Nearest crossing point along the track.
    points = [crossing] if crossing.geom_type == "Point" else list(getattr(crossing, "geoms", []))
    coords: list[tuple[float, float]] = []
    for geom in points:
        if geom.geom_type == "Point":
            coords.append((geom.y, geom.x))
        elif hasattr(geom, "coords"):
            coords.extend((y, x) for x, y in geom.coords)
    if not coords:
        return None, True, None, None

    # The FIRST crossing along the track, so a heading that clips a boundary
    # twice reports the one you reach first.
    nearest_crossing = min(coords, key=lambda pair: geodesic_m(lat, lon, pair[0], pair[1]))
    distance_m = geodesic_m(lat, lon, *nearest_crossing)
    metres_per_minute = speed_kn * 1852.0 / 60.0
    return distance_m / metres_per_minute, True, distance_m, nearest_crossing


def _state_for(
    *, inside: bool, distance_m: float, is_area: bool, previous: str | None
) -> FenceState:
    """The state machine.

    Transitions, not levels, are what the alert rail consumes — so this must be a
    pure function of (current geometry, previous state) with no hysteresis
    surprises. A boat loitering just inside APPROACHING stays APPROACHING and
    generates exactly one event.
    """
    was = FenceState(previous) if previous in set(FenceState) else None

    if is_area:
        if inside:
            # CROSSED only on an observed TRANSITION from outside. A first
            # observation (previous is None) must be INSIDE, not CROSSED: we did
            # not see them enter, and treating "the first fix of the trip" as an
            # entry event fired a spurious "you have entered the EEZ" alert at
            # the start of every single trip — which is precisely the noise the
            # state machine exists to prevent.
            if was in (FenceState.OUTSIDE, FenceState.APPROACHING):
                return FenceState.CROSSED
            return FenceState.INSIDE
        if was in (FenceState.INSIDE, FenceState.CROSSED):
            return FenceState.EXITED
        return FenceState.APPROACHING if distance_m <= APPROACHING_M else FenceState.OUTSIDE

    # A line has no inside. Crossing is detected by the caller comparing
    # successive positions; proximity is all this can say on its own.
    if distance_m <= APPROACHING_M:
        return FenceState.APPROACHING
    return FenceState.OUTSIDE


def crossed_line(
    index: GeofenceIndex,
    previous: tuple[float, float],
    current: tuple[float, float],
    *,
    fence_key: str | None = None,
) -> list[dict[str, Any]]:
    """Which line fences the segment between two fixes actually crossed.

    This is how a line crossing is detected properly: not by distance going to
    zero (a fix either side of the line never reports zero), but by testing
    whether the track segment intersects the boundary. Sampling positions every
    few minutes, a boat at 8 knots moves ~1.5 km between fixes and would step
    clean over a line that a distance threshold might never flag.
    """
    segment = LineString([(previous[1], previous[0]), (current[1], current[0])])
    events: list[dict[str, Any]] = []

    for fence in index.fences():
        if fence_key and fence.key != fence_key:
            continue
        if fence.is_area:
            continue
        try:
            if not segment.intersects(fence.geometry):
                continue
            where = segment.intersection(fence.geometry)
        except Exception as exc:  # noqa: BLE001
            log.warning("crossing test failed for fence %s: %s", fence.key, exc)
            continue

        point = where if where.geom_type == "Point" else where.representative_point()
        events.append(
            {
                "fence": fence.key,
                "name": fence.name,
                "kind": fence.kind,
                "at": {"lat": round(point.y, 5), "lon": round(point.x, 5)},
                "consequence": fence.consequence,
                "authority": fence.authority,
                "narrative": (f"You have crossed {fence.name}. {fence.consequence}"),
            }
        )
    return events


#: Process-wide index, built during the app's lifespan.
index = GeofenceIndex()


def evidence_citation() -> Citation:
    from orca.sources.marine_regions import CITATION

    return CITATION


def provenance() -> Provenance:
    """Reference geography is CURATED — a hand-checked treaty edition, not a
    measurement, and it does not go stale on a clock."""
    return Provenance.CURATED


def provider() -> Provider:
    return Provider.MARINE_REGIONS

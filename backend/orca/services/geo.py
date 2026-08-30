"""Geodesy. Every distance in ORCA goes through here.

The blueprint warns that every team hits the same bug: ``ST_Distance`` on a
``geometry`` returns **degrees**, not metres, and 0.5 "distance" reads as a
plausible number right up until someone notices the boat is 55 km from where the
app says it is. On a safety-of-life-adjacent system that is not an acceptable
class of defect, so the footgun is designed out rather than remembered:

* there is exactly one distance function, :func:`geodesic_m`, and it returns
  metres from pyproj's ``Geod`` on WGS84;
* nothing else in the codebase is allowed to subtract coordinates;
* ``tests/test_geo.py`` asserts a known 1 km pair, and the reference value is the
  one PostGIS itself returned (995.68 m for two points 0.009 degrees apart), so
  the SQLite path and the PostGIS path are checked against the same oracle.
"""

from __future__ import annotations

import itertools
import math
from functools import lru_cache

from pyproj import Geod

#: WGS84. The same ellipsoid PostGIS uses for ``geography``, so the two drivers
#: agree to the millimetre rather than "closely enough".
_GEOD = Geod(ellps="WGS84")

#: 16-point compass, for turning a bearing into something sayable over VHF.
_COMPASS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


def geodesic_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in **metres** on the WGS84 ellipsoid.

    The only distance function in ORCA. If you are about to write
    ``sqrt(dlat**2 + dlon**2)``, this is the function you wanted.
    """
    _, _, distance = _GEOD.inv(lon1, lat1, lon2, lat2)
    return abs(float(distance))


def geodesic_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return geodesic_m(lat1, lon1, lat2, lon2) / 1000.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing, degrees clockwise from true north, 0-360."""
    forward, _, _ = _GEOD.inv(lon1, lat1, lon2, lat2)
    return float(forward % 360.0)


def compass_point(bearing: float) -> str:
    """A 16-point compass name. ``"south-east"`` beats ``"137.4 degrees"`` in an
    advisory that may be read aloud."""
    index = round((bearing % 360.0) / 22.5) % 16
    return _COMPASS[index]


def compass_words(bearing: float) -> str:
    """The spoken form, for TTS and the language plane."""
    point = compass_point(bearing)
    words = {"N": "north", "E": "east", "S": "south", "W": "west"}
    return "-".join(words.get(part, part) for part in _split_compass(point))


def _split_compass(point: str) -> list[str]:
    if len(point) <= 1:
        return [point]
    if len(point) == 2:
        return [point[0], point[1]]
    # NNE -> N, NE ; ENE -> E, NE
    return [point[0], point[1:]] if point[1] == point[2] else [point[:2], point[2]]


def destination(
    lat: float, lon: float, bearing_deg_: float, distance_m: float
) -> tuple[float, float]:
    """Where you end up steering ``bearing`` for ``distance_m``. Returns (lat, lon)."""
    lon2, lat2, _ = _GEOD.fwd(lon, lat, bearing_deg_, distance_m)
    return (float(lat2), float(lon2))


def path_length_m(points: list[tuple[float, float]]) -> float:
    """Total geodesic length of a (lat, lon) polyline, in metres."""
    return sum(geodesic_m(a[0], a[1], b[0], b[1]) for a, b in itertools.pairwise(points))


@lru_cache(maxsize=512)
def km_per_degree(lat: float) -> tuple[float, float]:
    """``(km_per_deg_lat, km_per_deg_lon)`` at a latitude.

    For grid maths where a full inverse solve per cell would be wasteful. Never
    for a reported distance — those go through :func:`geodesic_m`.
    """
    lat_rad = math.radians(lat)
    km_lat = 111.132954 - 0.559822 * math.cos(2 * lat_rad) + 0.001175 * math.cos(4 * lat_rad)
    km_lon = 111.319488 * math.cos(lat_rad)
    return (km_lat, km_lon)


def time_to_cross_minutes(distance_m: float, speed_kn: float) -> float | None:
    """Minutes to cover a distance at a speed in knots.

    ``None`` for a non-positive speed rather than infinity or a divide-by-zero:
    a stationary boat has no time-to-cross, and the caller should say so instead
    of printing a number.
    """
    if speed_kn <= 0:
        return None
    metres_per_minute = speed_kn * 1852.0 / 60.0
    return distance_m / metres_per_minute

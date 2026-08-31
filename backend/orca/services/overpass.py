"""Potential satellite swath crossings calculated with SGP4.

An SGP4 ground track can say when an instrument's nominal swath reaches a
coordinate.  It cannot say whether the operator scheduled an acquisition,
whether the sensor was healthy, or whether an optical image was cloud-free.
Those limits are part of every response, not left to UI copy.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from pyproj import Geod, Transformer
from sgp4.api import SGP4_ERRORS, Satrec
from sgp4.functions import jday

from orca.provenance import Provenance, utcnow
from orca.sources.celestrak import TleRecord, celestrak

_GEOD = Geod(ellps="WGS84")
_ECEF_TO_GEODETIC = Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True)


@dataclass(frozen=True, slots=True)
class SatelliteSpec:
    name: str
    norad_id: int
    sensor: str
    swath_width_km: float
    optical: bool
    source_url: str


# The width is the nominal surface swath, not a guaranteed acquisition width.
SATELLITES: tuple[SatelliteSpec, ...] = (
    SatelliteSpec(
        "Sentinel-3A",
        41335,
        "OLCI ocean colour",
        1270.0,
        True,
        "https://sentinels.copernicus.eu/copernicus/sentinel-3",
    ),
    SatelliteSpec(
        "Sentinel-3B",
        43437,
        "OLCI ocean colour",
        1270.0,
        True,
        "https://sentinels.copernicus.eu/copernicus/sentinel-3",
    ),
    SatelliteSpec(
        "EOS-06 (Oceansat-3)",
        54361,
        "OCM-3 / SSTM / SCAT-3",
        1400.0,
        True,
        "https://www.isro.gov.in/EOS_06.html",
    ),
)


class GroundPoint(BaseModel):
    time: datetime
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class Overpass(BaseModel):
    satellite: str
    norad_id: int
    sensor: str
    start_time: datetime
    closest_time: datetime
    end_time: datetime
    closest_distance_km: float = Field(ge=0)
    swath_width_km: float = Field(gt=0)
    daylight_at_target: bool
    tle_epoch: datetime
    tle_age_hours: float
    tle_provenance: Provenance
    confidence: str
    source_url: str
    caveat: str
    ground_track: list[GroundPoint]


class OverpassResponse(BaseModel):
    lat: float
    lon: float
    generated_at: datetime
    horizon_hours: int
    step_seconds: int
    passes: list[Overpass]
    unavailable_satellites: list[str]
    method: str
    caveat: str


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _gmst_radians(julian_date: float) -> float:
    centuries = (julian_date - 2451545.0) / 36525.0
    degrees = (
        280.46061837
        + 360.98564736629 * (julian_date - 2451545.0)
        + 0.000387933 * centuries**2
        - centuries**3 / 38710000.0
    )
    return math.radians(degrees % 360.0)


def subpoint(satellite: Satrec, when: datetime) -> tuple[float, float, float]:
    """Return WGS84 ``(lon, lat, altitude_km)`` from an SGP4 TEME position."""
    moment = _as_utc(when)
    jd, fraction = jday(
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second + moment.microsecond / 1_000_000,
    )
    error, position, _velocity = satellite.sgp4(jd, fraction)
    if error:
        raise ValueError(f"SGP4 error {error}: {SGP4_ERRORS.get(error, 'unknown error')}")

    theta = _gmst_radians(jd + fraction)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    x_km = cos_t * position[0] + sin_t * position[1]
    y_km = -sin_t * position[0] + cos_t * position[1]
    z_km = position[2]
    lon, lat, height_m = _ECEF_TO_GEODETIC.transform(x_km * 1000.0, y_km * 1000.0, z_km * 1000.0)
    return float(lon), float(lat), float(height_m) / 1000.0


def _distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    _az1, _az2, metres = _GEOD.inv(lon1, lat1, lon2, lat2)
    return abs(float(metres)) / 1000.0


def _daylight_at(lat: float, lon: float, when: datetime) -> bool:
    """Use NOAA's fractional-year solar-position approximation at the target."""
    moment = _as_utc(when)
    utc_hour = moment.hour + moment.minute / 60 + moment.second / 3600
    days = (
        366 if moment.year % 4 == 0 and (moment.year % 100 != 0 or moment.year % 400 == 0) else 365
    )
    gamma = 2 * math.pi / days * (moment.timetuple().tm_yday - 1 + (utc_hour - 12) / 24)
    equation_minutes = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    true_solar_minutes = (utc_hour * 60 + equation_minutes + 4 * lon) % 1440
    hour_angle = math.radians(true_solar_minutes / 4 - 180)
    latitude = math.radians(lat)
    cos_zenith = math.sin(latitude) * math.sin(declination) + math.cos(latitude) * math.cos(
        declination
    ) * math.cos(hour_angle)
    return cos_zenith > 0


def _track(
    satellite: Satrec, start: datetime, end: datetime, *, step_seconds: int = 120
) -> list[GroundPoint]:
    points: list[GroundPoint] = []
    cursor = start
    while cursor <= end:
        lon, lat, _altitude = subpoint(satellite, cursor)
        points.append(GroundPoint(time=cursor, lon=round(lon, 4), lat=round(lat, 4)))
        cursor += timedelta(seconds=step_seconds)
    return points


def predict_for_tle(
    spec: SatelliteSpec,
    tle: TleRecord,
    *,
    lat: float,
    lon: float,
    start: datetime,
    horizon_hours: int,
    step_seconds: int,
) -> list[Overpass]:
    satellite = Satrec.twoline2rv(tle.line1, tle.line2)
    half_swath = spec.swath_width_km / 2.0
    samples: list[tuple[datetime, float]] = []
    cursor = start
    end = start + timedelta(hours=horizon_hours)
    while cursor <= end:
        sat_lon, sat_lat, _altitude = subpoint(satellite, cursor)
        samples.append((cursor, _distance_km(lon, lat, sat_lon, sat_lat)))
        cursor += timedelta(seconds=step_seconds)

    groups: list[list[tuple[datetime, float]]] = []
    current: list[tuple[datetime, float]] = []
    for sample in samples:
        if sample[1] <= half_swath:
            current.append(sample)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    tle_age = (start - _as_utc(tle.epoch)).total_seconds() / 3600.0
    confidence = "nominal" if tle_age <= 72 else "degraded: TLE older than 72 hours"
    passes: list[Overpass] = []
    for group in groups:
        closest_time, closest_distance = min(group, key=lambda item: item[1])
        pass_start = max(start, group[0][0] - timedelta(seconds=step_seconds / 2))
        pass_end = group[-1][0] + timedelta(seconds=step_seconds / 2)
        passes.append(
            Overpass(
                satellite=spec.name,
                norad_id=spec.norad_id,
                sensor=spec.sensor,
                start_time=pass_start,
                closest_time=closest_time,
                end_time=pass_end,
                closest_distance_km=round(closest_distance, 1),
                swath_width_km=spec.swath_width_km,
                daylight_at_target=_daylight_at(lat, lon, closest_time),
                tle_epoch=tle.epoch,
                tle_age_hours=round(tle_age, 2),
                tle_provenance=tle.provenance,
                confidence=confidence,
                source_url=spec.source_url,
                caveat=(
                    "Potential nominal-swath crossing only; it does not confirm tasking, "
                    "image acquisition, usable cloud cover, or product delivery."
                ),
                ground_track=_track(
                    satellite,
                    max(start, pass_start - timedelta(minutes=8)),
                    pass_end + timedelta(minutes=8),
                ),
            )
        )
    return passes


async def predict_overpasses(
    lat: float,
    lon: float,
    *,
    hours: int = 48,
    step_seconds: int = 30,
    now: datetime | None = None,
) -> OverpassResponse:
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("latitude/longitude outside WGS84 range")
    if not 1 <= hours <= 168:
        raise ValueError("hours must be between 1 and 168")
    if not 10 <= step_seconds <= 300:
        raise ValueError("step_seconds must be between 10 and 300")

    start = _as_utc(now or utcnow())
    results = await asyncio.gather(
        *(celestrak.tle(spec.norad_id) for spec in SATELLITES), return_exceptions=True
    )
    passes: list[Overpass] = []
    unavailable: list[str] = []
    for spec, result in zip(SATELLITES, results, strict=True):
        if isinstance(result, BaseException):
            unavailable.append(f"{spec.name}: {type(result).__name__}: {result}")
            continue
        try:
            passes.extend(
                predict_for_tle(
                    spec,
                    result,
                    lat=lat,
                    lon=lon,
                    start=start,
                    horizon_hours=hours,
                    step_seconds=step_seconds,
                )
            )
        except (ValueError, OverflowError) as exc:
            unavailable.append(f"{spec.name}: {type(exc).__name__}: {exc}")

    passes.sort(key=lambda item: item.closest_time)
    return OverpassResponse(
        lat=lat,
        lon=lon,
        generated_at=utcnow(),
        horizon_hours=hours,
        step_seconds=step_seconds,
        passes=passes,
        unavailable_satellites=unavailable,
        method="CelesTrak GP element sets propagated with SGP4; WGS84 geodesic swath test",
        caveat=(
            "These are predicted opportunities within each sensor's nominal swath, not "
            "confirmed photographs. Optical usefulness also depends on daylight and cloud."
        ),
    )

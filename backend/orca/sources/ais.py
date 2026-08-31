"""Bounded AISStream snapshots with explicit sparse-coverage semantics."""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from pyproj import Geod
from websockets.asyncio.client import connect

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Provenance, Provider, utcnow
from orca.sources.base import Source

AIS_URL = "wss://stream.aisstream.io/v0/stream"
POSITION_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
)
_GEOD = Geod(ellps="WGS84")

CollisionLevel = Literal["danger", "warning", "monitor", "clear", "insufficient"]
COLLISION_HORIZON_MIN = 30.0
DANGER_DCPA_NM = 0.5
WARNING_DCPA_NM = 1.0
MONITOR_DISTANCE_NM = 2.0


class VesselPosition(BaseModel):
    mmsi: str
    name: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_kn: float | None = Field(default=None, ge=0, le=102.3)
    course_deg: float | None = Field(default=None, ge=0, le=360)
    heading_deg: float | None = Field(default=None, ge=0, le=360)
    message_type: str
    received_at: datetime
    provenance: Provenance = Provenance.LIVE


class CollisionAdvisory(BaseModel):
    mmsi: str
    name: str | None = None
    level: CollisionLevel
    current_distance_nm: float = Field(ge=0)
    bearing_deg: float = Field(ge=0, lt=360)
    tcpa_minutes: float | None = None
    dcpa_nm: float | None = Field(default=None, ge=0)
    reason: str


class OwnMotion(BaseModel):
    speed_kn: float = Field(ge=0, le=60)
    course_deg: float = Field(ge=0, lt=360)
    horizon_minutes: float = Field(gt=0, le=120)


class AisSnapshot(BaseModel):
    bbox: tuple[float, float, float, float]
    started_at: datetime
    duration_seconds: float = Field(ge=0)
    connected: bool
    vessels: list[VesselPosition]
    raw_position_reports: int = Field(ge=0)
    own_motion: OwnMotion | None = None
    collision_advisories: list[CollisionAdvisory] = Field(default_factory=list)
    collision_summary: dict[str, int] = Field(default_factory=dict)
    provenance: Provenance
    error: str | None = None
    coverage_note: str


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            pass
    return utcnow()


def parse_position(payload: dict[str, Any]) -> VesselPosition | None:
    message_type = str(payload.get("MessageType") or "")
    if message_type not in POSITION_TYPES:
        return None
    metadata = payload.get("MetaData") or {}
    envelope = payload.get("Message") or {}
    report = envelope.get(message_type) or {}
    lat = _number(metadata.get("latitude", report.get("Latitude")))
    lon = _number(metadata.get("longitude", report.get("Longitude")))
    mmsi = metadata.get("MMSI", report.get("UserID"))
    if lat is None or lon is None or mmsi is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    speed = _number(report.get("Sog"))
    course = _number(report.get("Cog"))
    heading = _number(report.get("TrueHeading"))
    if speed is not None and not 0 <= speed <= 102.2:
        speed = None
    if course is not None and not 0 <= course <= 360:
        course = None
    if heading is not None and (heading == 511 or not 0 <= heading <= 360):
        heading = None
    name = metadata.get("ShipName")
    return VesselPosition(
        mmsi=str(mmsi),
        name=str(name).strip() or None if name is not None else None,
        lat=lat,
        lon=lon,
        speed_kn=speed,
        course_deg=course,
        heading_deg=heading,
        message_type=message_type,
        received_at=_timestamp(metadata.get("time_utc")),
    )


def assess_collision(
    own_lat: float,
    own_lon: float,
    vessel: VesselPosition,
    *,
    own_speed_kn: float | None,
    own_course_deg: float | None,
    horizon_minutes: float = COLLISION_HORIZON_MIN,
) -> CollisionAdvisory:
    """Screen one AIS target using a local-plane closest-point-of-approach calculation.

    A straight, constant course and speed is assumed for both vessels. The output
    is an advisory screening level, not a COLREG decision or a substitute for
    radar, visual watchkeeping, or the vessel's certified collision system.
    """
    bearing, _back_azimuth, distance_m = _GEOD.inv(own_lon, own_lat, vessel.lon, vessel.lat)
    bearing = bearing % 360
    distance_nm = max(0.0, float(distance_m) / 1852.0)
    base = {
        "mmsi": vessel.mmsi,
        "name": vessel.name,
        "current_distance_nm": round(distance_nm, 3),
        "bearing_deg": round(bearing, 1),
    }
    if distance_nm <= DANGER_DCPA_NM:
        return CollisionAdvisory(
            **base,
            level="danger",
            reason=(f"Target is already within ORCA's {DANGER_DCPA_NM:.1f} nm proximity screen."),
        )
    if (
        own_speed_kn is None
        or own_course_deg is None
        or vessel.speed_kn is None
        or vessel.course_deg is None
    ):
        return CollisionAdvisory(
            **base,
            level="insufficient",
            reason="CPA unavailable because one or both vessels lack course/speed data.",
        )

    radians = math.radians(bearing)
    relative_east = distance_nm * math.sin(radians)
    relative_north = distance_nm * math.cos(radians)

    def velocity(speed_kn: float, course_deg: float) -> tuple[float, float]:
        angle = math.radians(course_deg)
        return speed_kn / 60.0 * math.sin(angle), speed_kn / 60.0 * math.cos(angle)

    own_east, own_north = velocity(own_speed_kn, own_course_deg)
    target_east, target_north = velocity(vessel.speed_kn, vessel.course_deg)
    velocity_east = target_east - own_east
    velocity_north = target_north - own_north
    speed_squared = velocity_east**2 + velocity_north**2
    if speed_squared < 1e-10:
        return CollisionAdvisory(
            **base,
            level="monitor" if distance_nm <= MONITOR_DISTANCE_NM else "clear",
            reason="Relative motion is negligible; current separation is effectively constant.",
        )

    tcpa = -(relative_east * velocity_east + relative_north * velocity_north) / speed_squared
    if tcpa < 0:
        return CollisionAdvisory(
            **base,
            level="monitor" if distance_nm <= MONITOR_DISTANCE_NM else "clear",
            tcpa_minutes=round(tcpa, 2),
            dcpa_nm=round(distance_nm, 3),
            reason="Closest approach is in the past; the target is not converging on this track.",
        )

    cpa_east = relative_east + velocity_east * tcpa
    cpa_north = relative_north + velocity_north * tcpa
    dcpa = math.hypot(cpa_east, cpa_north)
    in_horizon = tcpa <= horizon_minutes
    if in_horizon and dcpa <= DANGER_DCPA_NM:
        level: CollisionLevel = "danger"
    elif in_horizon and dcpa <= WARNING_DCPA_NM:
        level = "warning"
    elif distance_nm <= MONITOR_DISTANCE_NM or (in_horizon and dcpa <= MONITOR_DISTANCE_NM):
        level = "monitor"
    else:
        level = "clear"
    return CollisionAdvisory(
        **base,
        level=level,
        tcpa_minutes=round(tcpa, 2),
        dcpa_nm=round(dcpa, 3),
        reason=(
            f"Constant-course CPA screen: {dcpa:.2f} nm in {tcpa:.1f} min."
            if in_horizon
            else f"Predicted CPA is beyond the {horizon_minutes:.0f} min screening horizon."
        ),
    )


class AisStreamSource(Source):
    name = "aisstream.websocket"
    provider = Provider.AISSTREAM
    variables = ("ais_position",)
    requires = "has_ais"
    docs_url = "https://aisstream.io/documentation"

    async def snapshot(
        self,
        bbox: tuple[float, float, float, float],
        *,
        duration_seconds: float = 5.0,
        max_messages: int = 250,
        own_lat: float | None = None,
        own_lon: float | None = None,
        own_speed_kn: float | None = None,
        own_course_deg: float | None = None,
        collision_horizon_minutes: float = COLLISION_HORIZON_MIN,
    ) -> AisSnapshot:
        west, south, east, north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox must be west,south,east,north in WGS84")
        duration_seconds = min(max(duration_seconds, 1.0), 15.0)
        settings = get_settings()
        started_at = utcnow()
        started_clock = time.monotonic()
        if not settings.has_ais:
            return AisSnapshot(
                bbox=bbox,
                started_at=started_at,
                duration_seconds=0,
                connected=False,
                vessels=[],
                raw_position_reports=0,
                provenance=Provenance.UNAVAILABLE,
                error="AISSTREAM_API_KEY is not configured",
                coverage_note="No AIS connection was attempted.",
            )
        assert settings.aisstream_api_key
        subscription = {
            "APIKey": settings.aisstream_api_key.get_secret_value(),
            "BoundingBoxes": [[[south, west], [north, east]]],
            "FilterMessageTypes": list(POSITION_TYPES),
        }
        latest: dict[str, VesselPosition] = {}
        report_count = 0
        connected = False
        error: str | None = None
        try:
            async with connect(AIS_URL, open_timeout=6, close_timeout=2) as websocket:
                connected = True
                await websocket.send(json.dumps(subscription))
                deadline = time.monotonic() + duration_seconds
                while time.monotonic() < deadline and report_count < max_messages:
                    remaining = max(0.01, deadline - time.monotonic())
                    try:
                        async with asyncio.timeout(remaining):
                            raw = await websocket.recv()
                    except TimeoutError:
                        break
                    try:
                        payload = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    position = parse_position(payload)
                    if position is None:
                        continue
                    report_count += 1
                    if west <= position.lon <= east and south <= position.lat <= north:
                        latest[position.mmsi] = position
        except Exception as exc:  # noqa: BLE001 - websocket libraries expose many failure types
            error = f"{type(exc).__name__}: {exc}"
            registry.record_failure(self.name, error)
        else:
            registry.record_success(
                self.name,
                provenance=Provenance.LIVE,
                latency_ms=(time.monotonic() - started_clock) * 1000,
            )
        elapsed = time.monotonic() - started_clock
        completed = connected and error is None
        own_motion = None
        advisories: list[CollisionAdvisory] = []
        if own_lat is not None and own_lon is not None:
            if own_speed_kn is not None and own_course_deg is not None:
                own_motion = OwnMotion(
                    speed_kn=own_speed_kn,
                    course_deg=own_course_deg,
                    horizon_minutes=collision_horizon_minutes,
                )
            advisories = [
                assess_collision(
                    own_lat,
                    own_lon,
                    vessel,
                    own_speed_kn=own_speed_kn,
                    own_course_deg=own_course_deg,
                    horizon_minutes=collision_horizon_minutes,
                )
                for vessel in latest.values()
            ]
            rank = {"danger": 0, "warning": 1, "monitor": 2, "insufficient": 3, "clear": 4}
            advisories.sort(key=lambda item: (rank[item.level], item.current_distance_nm))
        collision_summary = {
            level: 0 for level in ("danger", "warning", "monitor", "clear", "insufficient")
        }
        for advisory in advisories:
            collision_summary[advisory.level] += 1
        return AisSnapshot(
            bbox=bbox,
            started_at=started_at,
            duration_seconds=round(elapsed, 3),
            connected=completed,
            vessels=sorted(latest.values(), key=lambda vessel: vessel.mmsi),
            raw_position_reports=report_count,
            own_motion=own_motion,
            collision_advisories=advisories,
            collision_summary=collision_summary,
            provenance=Provenance.LIVE if completed else Provenance.UNAVAILABLE,
            error=error,
            coverage_note=(
                "Zero vessels is a valid live result, not evidence of empty water. Free "
                "AISStream receiver coverage over the Indian Ocean is sparse and intermittent."
                if completed
                else "The live AIS snapshot did not complete; no traffic claim can be made."
            ),
        )


aisstream = AisStreamSource()

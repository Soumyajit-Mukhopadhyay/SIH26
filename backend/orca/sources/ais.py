"""Bounded AISStream snapshots with explicit sparse-coverage semantics."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
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


class AisSnapshot(BaseModel):
    bbox: tuple[float, float, float, float]
    started_at: datetime
    duration_seconds: float = Field(ge=0)
    connected: bool
    vessels: list[VesselPosition]
    raw_position_reports: int = Field(ge=0)
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
        return AisSnapshot(
            bbox=bbox,
            started_at=started_at,
            duration_seconds=round(elapsed, 3),
            connected=completed,
            vessels=sorted(latest.values(), key=lambda vessel: vessel.mmsi),
            raw_position_reports=report_count,
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

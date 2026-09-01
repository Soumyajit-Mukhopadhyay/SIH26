"""Operational intelligence: orbital opportunities, archives and vessel activity."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from orca.config import get_settings
from orca.provenance import utcnow
from orca.services.cross_validation import CrossValidationResponse, validate_point
from orca.services.overpass import OverpassResponse, predict_overpasses
from orca.sources.ais import AisSnapshot, aisstream
from orca.sources.cmems import CmemsCatalogueResponse, cmems
from orca.sources.gfw import FishingEffortResponse, gfw
from orca.sources.nasa import NasaSearchResponse, cmr
from orca.sources.nasa_gibs import nasa_gibs
from orca.sources.sentinel_hub import SentinelSearchResponse, sentinel_hub

router = APIRouter(tags=["operational intelligence"])


class IntegrationState(BaseModel):
    implemented: bool
    configured: bool
    runtime_ready: bool
    mode: str
    caveat: str


def _bbox(lat: float, lon: float, radius_deg: float) -> tuple[float, float, float, float]:
    return (
        max(-180.0, lon - radius_deg),
        max(-90.0, lat - radius_deg),
        min(180.0, lon + radius_deg),
        min(90.0, lat + radius_deg),
    )


@router.get("/integrations/status", response_model=dict[str, IntegrationState])
async def integration_status() -> dict[str, IntegrationState]:
    settings = get_settings()
    return {
        "satellite_overpass": IntegrationState(
            implemented=True,
            configured=True,
            runtime_ready=True,
            mode="live CelesTrak TLE + local SGP4 propagation",
            caveat="Predicts nominal-swath opportunities, not confirmed image acquisition.",
        ),
        "copernicus_marine": IntegrationState(
            implemented=True,
            configured=settings.has_cmems,
            runtime_ready=settings.has_cmems and cmems.installed(),
            mode="official Copernicus Marine Toolbox",
            caveat="Wave point reads are live model access; catalogue reads are metadata only.",
        ),
        "nasa_earthdata": IntegrationState(
            implemented=True,
            configured=settings.has_earthdata,
            runtime_ready=True,
            mode="public CMR discovery + NASA POWER hourly point data",
            caveat="CMR search does not imply a granule was downloaded.",
        ),
        "sentinel_hub": IntegrationState(
            implemented=True,
            configured=settings.has_cdse,
            runtime_ready=settings.has_cdse,
            mode="CDSE OAuth client + Sentinel Hub STAC Catalog",
            caveat="Catalogue matches do not imply imagery was processed by ORCA.",
        ),
        "aisstream": IntegrationState(
            implemented=True,
            configured=settings.has_ais,
            runtime_ready=settings.has_ais,
            mode="bounded live WebSocket snapshot",
            caveat="Zero reports can mean sparse receiver coverage, not empty water.",
        ),
        "global_fishing_watch": IntegrationState(
            implemented=True,
            configured=settings.has_gfw,
            runtime_ready=settings.has_gfw,
            mode="4Wings apparent-fishing-effort report",
            caveat="Historical apparent fishing from AIS, normally delayed by several days.",
        ),
    }


@router.get("/satellites/overpasses", response_model=OverpassResponse)
async def satellite_overpasses(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    hours: int = Query(48, ge=1, le=168),
) -> OverpassResponse:
    return await predict_overpasses(lat, lon, hours=hours)


@router.get("/catalog/nasa", response_model=NasaSearchResponse)
async def nasa_catalogue(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    short_name: str = Query("MUR-JPL-L4-GLOB-v4.1", min_length=1, max_length=100),
    days: int = Query(7, ge=1, le=3660),
    radius_deg: float = Query(0.25, gt=0, le=10),
    limit: int = Query(10, ge=1, le=100),
) -> NasaSearchResponse:
    end = utcnow()
    start = end - timedelta(days=days)
    try:
        return await cmr.search(short_name, _bbox(lat, lon, radius_deg), start, end, limit=limit)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/catalog/sentinel", response_model=SentinelSearchResponse)
async def sentinel_catalogue(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    collection: str = Query("sentinel-3-olci", min_length=1, max_length=100),
    days: int = Query(7, ge=1, le=3660),
    radius_deg: float = Query(0.25, gt=0, le=10),
    limit: int = Query(10, ge=1, le=100),
) -> SentinelSearchResponse:
    end = utcnow()
    start = end - timedelta(days=days)
    try:
        return await sentinel_hub.search(
            collection, _bbox(lat, lon, radius_deg), start, end, limit=limit
        )
    except (RuntimeError, ValueError) as exc:
        status = 503 if "not configured" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get(
    "/imagery/sentinel/preview",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def sentinel_preview(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    days: int = Query(7, ge=1, le=90),
    size: int = Query(384, ge=128, le=768),
    collection: Literal["sentinel-3-olci", "sentinel-2-l2a"] = Query(
        "sentinel-3-olci"
    ),
) -> Response:
    try:
        preview = await sentinel_hub.preview(
            lat, lon, collection=collection, days=days, size=size
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        status = 503 if "not configured" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return Response(
        content=preview.content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=900",
            "X-ORCA-Acquired-At": preview.acquired_at.isoformat(),
            "X-ORCA-Provenance": preview.provenance,
            "X-ORCA-Source": preview.source_label,
            "X-ORCA-Satellite": preview.platform,
            "X-ORCA-Resolution-M": str(preview.resolution_m),
            "X-ORCA-Collection": preview.collection,
            "X-ORCA-Item-Id": preview.item_id,
            **(
                {"X-ORCA-Cloud-Cover": f"{preview.cloud_cover_percent:.1f}"}
                if preview.cloud_cover_percent is not None
                else {}
            ),
        },
    )


@router.get(
    "/imagery/nasa/preview",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def nasa_imagery_preview(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    days: int = Query(3, ge=1, le=7),
    size: int = Query(384, ge=128, le=768),
) -> Response:
    try:
        preview = await nasa_gibs.preview(lat, lon, days=days, size=size)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=preview.content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=900",
            "X-ORCA-Observation-Date": preview.observation_date.isoformat(),
            "X-ORCA-Provenance": preview.provenance,
            "X-ORCA-Source": "NASA GIBS corrected reflectance",
            "X-ORCA-Satellite": preview.satellite,
            "X-ORCA-Instrument": preview.instrument,
            "X-ORCA-Resolution-M": str(preview.resolution_m),
            "X-ORCA-Layer": preview.layer_id,
            "X-ORCA-Time-Precision": "date",
        },
    )


@router.get("/catalog/cmems", response_model=CmemsCatalogueResponse)
async def cmems_catalogue(
    query: str = Query("Indian Ocean", min_length=2, max_length=100),
) -> CmemsCatalogueResponse:
    try:
        return await cmems.catalogue(query)
    except (RuntimeError, ValueError) as exc:
        status = 503 if "not configured" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/traffic/ais", response_model=AisSnapshot)
async def ais_snapshot(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_deg: float = Query(0.5, gt=0, le=10),
    duration_seconds: float = Query(5, ge=1, le=15),
    own_speed_kn: float | None = Query(default=None, ge=0, le=60),
    own_course_deg: float | None = Query(default=None, ge=0, lt=360),
    collision_horizon_minutes: float = Query(30, gt=0, le=120),
) -> AisSnapshot:
    return await aisstream.snapshot(
        _bbox(lat, lon, radius_deg),
        duration_seconds=duration_seconds,
        own_lat=lat,
        own_lon=lon,
        own_speed_kn=own_speed_kn,
        own_course_deg=own_course_deg,
        collision_horizon_minutes=collision_horizon_minutes,
    )


@router.get("/traffic/fishing-effort", response_model=FishingEffortResponse)
async def fishing_effort(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_deg: float = Query(0.5, gt=0, le=10),
    days: int = Query(30, ge=1, le=366),
) -> FishingEffortResponse:
    return await gfw.effort(_bbox(lat, lon, radius_deg), days=days)


@router.get("/validation/point", response_model=CrossValidationResponse)
async def point_validation(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    include_wave: bool = Query(True),
) -> CrossValidationResponse:
    return await validate_point(lat, lon, include_wave=include_wave)

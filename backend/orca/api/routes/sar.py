"""Search and rescue: a drift search area from a last-known position.

Returns GeoJSON alongside the numbers, because the consumer of this endpoint is
as likely to be a Coast Guard plotting tool as ORCA's own map.

Every response carries the disclaimer, and it is not boilerplate: ORCA computes
where a physical model says an object *could* be. A coordinated search that
concentrates on a single predicted point because software offered one is a worse
outcome than no software at all.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orca.provenance import Lat, Lon, utcnow
from orca.services import drift as drift_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["sar"])


class DriftRequest(BaseModel):
    lat: Lat
    lon: Lon
    #: Hours since the last known position. 48 h is the practical ceiling: beyond
    #: it the forecast forcing runs out and the area grows faster than a search
    #: can cover it, so a longer number would be arithmetic rather than advice.
    hours: float = Field(default=6.0, gt=0, le=48)
    object_class: str = "PIW-VERTICAL"
    particles: int = Field(default=drift_service.DEFAULT_PARTICLES, ge=200, le=8000)
    #: Fix the RNG to make a run reproducible for a briefing or an incident log.
    seed: int | None = None


@router.post("/sar/drift", summary="Search area for a drifting person or vessel")
async def sar_drift(request: DriftRequest) -> dict[str, Any]:
    if request.object_class not in drift_service.LEEWAY_CLASSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown object class {request.object_class!r}; "
                f"see GET /sar/classes for the {len(drift_service.LEEWAY_CLASSES)} available"
            ),
        )

    try:
        result = await drift_service.simulate(
            lat=float(request.lat),
            lon=float(request.lon),
            hours=request.hours,
            object_class=request.object_class,
            particles=request.particles,
            seed=request.seed,
        )
    except Exception as exc:
        log.exception("drift simulation failed")
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    payload = drift_service.describe(result)

    # GeoJSON, so this drops straight into any chart plotter. The areas are
    # ordered widest-last on purpose: drawn in that order the tighter containment
    # sits visibly inside the looser one instead of being hidden by it.
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [area["ring"]]},
            "properties": {
                "containment": area["fraction"],
                "area_km2": area["area_km2"],
                "radius_km": area["radius_km"],
                "label": f"{int(area['fraction'] * 100)}% containment",
            },
        }
        for area in sorted(payload["areas"], key=lambda a: a["fraction"])
    ]
    features.append(
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": payload["track"]},
            "properties": {"label": "mean drift track", "hours": payload["hours"]},
        }
    )
    features.append(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": payload["last_known_position"]},
            "properties": {"label": "last known position"},
        }
    )

    return {
        **payload,
        "geojson": {"type": "FeatureCollection", "features": features},
        "requested": request.model_dump(),
    }


@router.get("/sar/classes", summary="Drift object classes and their leeway coefficients")
async def sar_classes() -> dict[str, Any]:
    """The coefficients, published rather than hidden.

    A search coordinator with local leeway data should be able to see exactly
    what ORCA assumed and disagree with it specifically.
    """
    return {
        "drift_version": drift_service.DRIFT_VERSION,
        "classes": drift_service.roster(),
        "current_field_error_ms": drift_service.CURRENT_ERROR_MS,
        "step_minutes": drift_service.STEP_MINUTES,
        "resample_km": drift_service.RESAMPLE_KM,
        "model": (
            "drift = surface current + leeway(wind), after the IAMSAR formulation. Downwind and "
            "crosswind leeway are both linear in the 10 m wind speed; the crosswind sign is "
            "drawn per particle because it flips unpredictably between individual objects of "
            "the same class."
        ),
        "note": (
            "For a person in the water the search area's size is set by the CURRENT FIELD's own "
            "error, not by the object's leeway — a PIW has almost no sail area. That error is "
            "drawn once per particle and held, because it is correlated in time; treating it as "
            "fresh noise each step understates the area by a factor of sqrt(steps)."
        ),
        "generated_at": utcnow().isoformat(),
    }

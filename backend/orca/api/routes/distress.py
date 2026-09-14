"""Distress: one position in, a complete response package out.

Separate from ``/sar/drift`` on purpose. That endpoint answers a modelling
question — where could this object be. This one answers an operational one —
somebody is in the water, what happens now — and the difference is who is
holding the phone.

Nothing here notifies anybody. See :mod:`orca.services.distress`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from orca.provenance import Lat, Lon
from orca.services import authorities, searchplan
from orca.services import distress as distress_service
from orca.services import drift as drift_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["distress"])


class DistressRequest(BaseModel):
    lat: Lat
    lon: Lon
    #: Time since the person or vessel was last known to be at this position.
    #: Defaults to one hour rather than zero: a report almost never reaches a
    #: centre the instant of the event, and zero would produce a search area of
    #: a few hundred metres that flatters the system and misleads the searcher.
    hours_since: float = Field(default=1.0, gt=0, le=48)
    object_class: str = "PIW-VERTICAL"
    #: Which unit the plan is costed for. Changing it changes track spacing (eye
    #: height moves the sweep table column), endurance and ETA.
    unit_code: str = "ICG-FPV"
    units: int = Field(default=1, ge=1, le=12)
    particles: int = Field(default=1200, ge=200, le=8000)
    seed: int | None = None
    #: Routing the transit costs a live lattice sample. Off for a fast triage
    #: answer; on for the real plan.
    route_transit: bool = True


@router.post("/distress/alert", summary="Full response package for a person or vessel in distress")
async def distress_alert(request: DistressRequest) -> dict[str, Any]:
    if request.object_class not in drift_service.LEEWAY_CLASSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown object class {request.object_class!r}; "
                f"see GET /sar/classes for the {len(drift_service.LEEWAY_CLASSES)} available"
            ),
        )
    if request.unit_code not in searchplan.UNITS_BY_CODE:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown unit {request.unit_code!r}; "
                f"see GET /distress/units for the {len(searchplan.SEARCH_UNITS)} available"
            ),
        )

    try:
        return await distress_service.respond(
            lat=float(request.lat),
            lon=float(request.lon),
            hours_since=request.hours_since,
            object_class=request.object_class,
            unit_code=request.unit_code,
            units=request.units,
            particles=request.particles,
            seed=request.seed,
            route_transit=request.route_transit,
        )
    except Exception as exc:
        log.exception("distress: response assembly failed")
        raise HTTPException(status_code=502, detail=f"distress response failed: {exc}") from exc


@router.get("/distress/authorities", summary="Maritime rescue centres, nearest first")
async def distress_authorities(
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    limit: int = Query(default=5, ge=1, le=40),
) -> dict[str, Any]:
    """The register, or the part of it nearest a position.

    Without coordinates this is the whole register — useful on its own, because
    "here is every MRCC and MRSC in India with its distress number" is a thing no
    other surface in the system offers.
    """
    if lat is None or lon is None:
        return {
            **authorities.summary(),
            "all": [
                authorities.describe_contact(centre, 0.0, 0.0) for centre in authorities.CENTRES
            ],
        }

    ranked = authorities.nearest(lat, lon, limit=limit)
    mrcc = authorities.responsible_mrcc(lat, lon)
    return {
        **authorities.summary(),
        "query": {"lat": lat, "lon": lon},
        "coordinating_mrcc": mrcc.name,
        "nearest": [authorities.describe_contact(c, km, bearing) for c, km, bearing in ranked],
    }


@router.get("/distress/units", summary="Search and rescue units the planner knows")
async def distress_units() -> dict[str, Any]:
    return {
        "units": searchplan.units(),
        "note": (
            "Speeds are SEARCH speeds where marked, not maximum speeds. A hull that makes 33 kn "
            "does not search at 33 kn, and planning as if it did is how an area gets declared "
            "covered when it was not."
        ),
        "aircraft": (
            "Not offered. Aircraft sweep widths are indexed on search altitude (Tables H-11 to "
            "H-18) and are not carried in this build. An ICG Dornier covers a large area far "
            "faster than any hull — treat a surface-only plan as a floor, not the full picture."
        ),
    }


class SearchPlanRequest(BaseModel):
    """Plan a search over an area you already have."""

    area_km2: float = Field(gt=0, le=200_000)
    object_class: str = "PIW-VERTICAL"
    unit_code: str = "ICG-FPV"
    units: int = Field(default=1, ge=1, le=12)
    visibility_km: float | None = Field(default=None, ge=0, le=100)
    wind_kn: float | None = Field(default=None, ge=0, le=200)
    wave_m: float | None = Field(default=None, ge=0, le=25)
    coverage: float = Field(default=searchplan.INITIAL_COVERAGE, gt=0, le=5)
    daylight_hours_remaining: float | None = Field(default=None, ge=0, le=24)


@router.post("/distress/search-plan", summary="Track spacing, pattern, hours and odds for an area")
async def distress_search_plan(request: SearchPlanRequest) -> dict[str, Any]:
    if request.unit_code not in searchplan.UNITS_BY_CODE:
        raise HTTPException(status_code=422, detail=f"unknown unit {request.unit_code!r}")
    return searchplan.plan_search(
        area_km2=request.area_km2,
        drift_object_class=request.object_class,
        unit_code=request.unit_code,
        units=request.units,
        visibility_km=request.visibility_km,
        wind_kn=request.wind_kn,
        wave_m=request.wave_m,
        coverage=request.coverage,
        daylight_hours_remaining=request.daylight_hours_remaining,
    )

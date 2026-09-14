"""``POST /route/plan`` — a passage the rule engine has cleared, cell by cell.

The endpoint returns 200 for a refusal as well as for a route, and that is
deliberate. "There is no safe passage for an 8.2 m boat across this corridor
today, and here are the six cells that block it" is a successful answer to the
question asked; expressing it as a 4xx would put it in the same bucket as a
malformed request and encourage a client to treat it as an error to retry.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orca.provenance import Lat, Lon, utcnow
from orca.services import router as route_service
from orca.services.thresholds import resolve_vessel

log = logging.getLogger(__name__)

router = APIRouter(tags=["routing"])


class RouteRequest(BaseModel):
    from_lat: Lat
    from_lon: Lon
    to_lat: Lat
    to_lon: Lon
    #: Optional. Precedence: boat_class / boat_class_code > loa_m > UNKNOWN.
    loa_m: float | None = Field(default=None, gt=0, le=200)
    #: Preferred vessel category code (legacy field name kept for API clients).
    boat_class: str | None = None
    boat_class_code: str | None = None
    speed_kn: float = Field(default=8.0, gt=0.5, le=40)
    step_deg: float = Field(
        default=route_service.DEFAULT_STEP_DEG,
        ge=0.05,
        le=1.0,
        description="Lattice spacing. Finer costs more upstream calls, not more accuracy.",
    )
    corridor_deg: float = Field(default=route_service.DEFAULT_CORRIDOR_DEG, ge=0.2, le=4.0)


@router.post("/route/plan", summary="Plan a passage, or explain why there is not one")
async def plan_route(request: RouteRequest) -> dict[str, Any]:
    code = request.boat_class_code or request.boat_class
    resolved = resolve_vessel(boat_class_code=code, loa_m=request.loa_m)
    if resolved.error:
        raise HTTPException(status_code=422, detail=resolved.error)
    if resolved.boat is None:
        return {
            "ok": False,
            "vessel_unknown": True,
            "boat_class": "UNKNOWN",
            "reason": (
                "Vessel type not specified. Select a boat category (or optionally enter "
                "length overall) before planning a passage — ORCA will not invent a class."
            ),
            "requested": {
                "from": [float(request.from_lon), float(request.from_lat)],
                "to": [float(request.to_lon), float(request.to_lat)],
                "speed_kn": request.speed_kn,
                "boat_class": None,
                "loa_m": request.loa_m,
            },
            "generated_at": utcnow().isoformat(),
        }
    boat = resolved.boat

    try:
        result = await route_service.plan(
            start=(float(request.from_lat), float(request.from_lon)),
            goal=(float(request.to_lat), float(request.to_lon)),
            boat_class=boat,
            loa_m=request.loa_m,
            speed_kn=request.speed_kn,
            step_deg=request.step_deg,
            corridor_deg=request.corridor_deg,
        )
    except Exception as exc:
        log.exception("route planning failed")
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    return {
        **result,
        "requested": {
            "from": [float(request.from_lon), float(request.from_lat)],
            "to": [float(request.to_lon), float(request.to_lat)],
            "speed_kn": request.speed_kn,
            "boat_class": boat.code,
            "loa_m": request.loa_m,
            "vessel_source": resolved.source,
        },
        "generated_at": utcnow().isoformat(),
    }


@router.get("/route/limits", summary="What the router costs and what bounds it")
async def route_limits() -> dict[str, Any]:
    """Exposed so the cost model is inspectable rather than asserted.

    A judge asking "how does it decide" should be able to read the weights off an
    endpoint instead of taking a slide's word for it.
    """
    from orca.sources.open_meteo import budget, sample_cache_status

    return {
        "router_version": route_service.ROUTER_VERSION,
        "default_step_deg": route_service.DEFAULT_STEP_DEG,
        "default_corridor_deg": route_service.DEFAULT_CORRIDOR_DEG,
        "max_nodes": route_service.MAX_NODES,
        "detour_weight": route_service.DETOUR_WEIGHT,
        "connectivity": 8,
        "cost_model": (
            "edge cost = geodesic metres × (1 + DETOUR_WEIGHT × ((100 − index)/100)²), where "
            "`index` is the deterministic risk engine's score for the destination cell. A cell "
            "with any hard veto is impassable, not expensive: encoding 'never' as a large number "
            "is how routers end up sailing through a cyclone to save a day."
        ),
        "heuristic": (
            "Great-circle distance with no risk term, which keeps it admissible — every edge "
            "costs at least its own length — so the result is optimal under the cost model "
            "rather than merely plausible."
        ),
        "land_mask": (
            "A cell is water only if the Marine API returns a wave height AND the point lies "
            "inside India's EEZ polygon. Wave height alone is not enough: the marine model "
            "often fills coastal and inland 0.25° cells. Hops whose midpoint leaves the EEZ "
            "are also blocked so a diagonal cannot cut a peninsula."
        ),
        "upstream_budget": budget.describe(),
        "sample_cache": sample_cache_status(),
    }

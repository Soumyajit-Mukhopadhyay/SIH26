"""Forecast, risk and PFZ-adjacent endpoints — the API the console draws from.

Every response carries Evidence, not bare numbers, so the provenance badge in the
UI is reading a real field rather than a decoration the frontend invented.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from orca.provenance import Evidence, Lat, Lon, evidence_summary, utcnow
from orca.services import thresholds
from orca.services.risk_engine import RiskResult, assess_from_evidence
from orca.sources import open_meteo
from orca.sources.erddap import erddap

log = logging.getLogger(__name__)

router = APIRouter(tags=["forecast"])

#: A handful of named places so the demo and the agent can resolve "off Chennai"
#: without a geocoder round-trip. Coordinates are the fishing-harbour mouths, a
#: few km offshore, because a harbour's own coordinates sit on land and marine
#: grids return nothing there.
LANDMARKS: dict[str, tuple[float, float, str]] = {
    "chennai": (13.10, 80.40, "Kasimedu / Chennai fishing harbour"),
    "kasimedu": (13.13, 80.32, "Kasimedu fishing harbour, Chennai"),
    "rameswaram": (9.28, 79.35, "Rameswaram, Palk Bay"),
    "tuticorin": (8.75, 78.25, "Thoothukudi (Tuticorin)"),
    "kochi": (9.95, 76.20, "Kochi / Cochin"),
    "kozhikode": (11.25, 75.70, "Kozhikode (Calicut)"),
    "mangaluru": (12.85, 74.80, "Mangaluru"),
    "goa": (15.42, 73.75, "Panaji / Goa"),
    "mumbai": (18.92, 72.75, "Mumbai / Sassoon Dock"),
    "veraval": (20.90, 70.35, "Veraval, Gujarat"),
    "porbandar": (21.63, 69.55, "Porbandar, Gujarat"),
    "visakhapatnam": (17.68, 83.30, "Visakhapatnam"),
    "paradip": (20.25, 86.70, "Paradip, Odisha"),
    "digha": (21.62, 87.55, "Digha, West Bengal"),
    "port_blair": (11.65, 92.75, "Port Blair, Andaman"),
}


class PointForecast(BaseModel):
    lat: float
    lon: float
    place: str | None = None
    generated_at: str
    evidence: dict[str, Evidence]
    summary: dict[str, object]


class RiskRequest(BaseModel):
    lat: Lat = Field(description="Latitude, degrees north.")
    lon: Lon = Field(description="Longitude, degrees east.")
    #: Optional. Precedence: boat_class_code > loa_m > UNKNOWN (never silent 8.2).
    loa_m: float | None = Field(
        default=None, gt=0, le=200, description="Optional length overall, metres."
    )
    boat_class_code: str | None = Field(
        default=None,
        description=(
            "Preferred vessel category (IND-TRAD / IND-MOT-S / …). "
            "Wins over loa_m when both are supplied."
        ),
    )


@router.get("/forecast/point", response_model=PointForecast, summary="All variables at a point")
async def forecast_point(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    include_satellite_sst: bool = Query(
        True, description="Also fetch MUR SST (1 km satellite) for cross-validation."
    ),
) -> PointForecast:
    """Waves, wind, visibility and convective energy at a point, each as Evidence.

    Satellite SST is fetched alongside the model SST on purpose: two independent
    sources for the same variable is what makes the agreement badge meaningful
    rather than decorative.
    """
    tasks = [open_meteo.conditions_at(lat, lon)]
    if include_satellite_sst:
        tasks.append(erddap.point("mur_sst", lat, lon, variables=["sst", "sst_uncertainty"]))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    evidence: dict[str, Evidence] = {}
    for result in results:
        if isinstance(result, BaseException):
            log.warning("forecast_point: a source raised: %s", result)
            continue
        for key, item in result.items():
            # The model SST and the satellite SST are both wanted; keep them
            # distinguishable rather than letting one overwrite the other.
            if key == "sst" and "sst" in evidence:
                evidence["sst_satellite"] = item
            else:
                evidence[key] = item

    if not evidence:
        raise HTTPException(status_code=502, detail="every upstream source failed")

    return PointForecast(
        lat=lat,
        lon=lon,
        generated_at=utcnow().isoformat(),
        evidence=evidence,
        summary=evidence_summary(list(evidence.values())),
    )


@router.get("/forecast/series", summary="Hourly series at a point, per variable")
async def forecast_series(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    days: int = Query(3, ge=1, le=7),
) -> dict[str, object]:
    """The input to the forecast charts and the trip monitor."""
    series = await open_meteo.series_at(lat, lon, forecast_days=days)
    return {
        "lat": lat,
        "lon": lon,
        "generated_at": utcnow().isoformat(),
        "variables": {
            name: {
                "unit": points[0].unit if points else None,
                "provenance": points[0].provenance.value if points else "unavailable",
                "points": [{"t": p.freshness.valid_time.isoformat(), "v": p.value} for p in points],
            }
            for name, points in series.items()
        },
    }


@router.post("/risk/assess", response_model=RiskResult, summary="Deterministic GO / NO-GO")
async def risk_assess(request: RiskRequest) -> RiskResult:
    """The safety verdict. Computed by the rule engine — no LLM is involved.

    The response carries the component arithmetic, the veto strings, the
    thresholds version and every piece of Evidence used, so the answer can be
    audited rather than merely trusted.
    """
    boat_class = None
    if request.boat_class_code:
        boat_class = thresholds.by_code(request.boat_class_code)
        if boat_class is None:
            raise HTTPException(
                status_code=422,
                detail=f"unknown boat class {request.boat_class_code!r}; see GET /risk/thresholds",
            )

    evidence = await open_meteo.conditions_at(request.lat, request.lon)
    # Category wins; LOA-only clients keep working; neither → UNKNOWN/UNVERIFIABLE.
    return assess_from_evidence(
        evidence,
        loa_m=request.loa_m,
        boat_class=boat_class,
        boat_class_code=None if boat_class else request.boat_class_code,
    )


@router.get("/risk/thresholds", summary="The versioned threshold table, with citations")
async def risk_thresholds() -> dict[str, object]:
    """Exposed deliberately: a user who can read the numbers that judged them can
    argue with them, and a jury that can read them can check us."""
    return {"classes": thresholds.table(), "policy": thresholds.policy()}


@router.get("/landmarks", summary="Named coastal locations the console can jump to")
async def landmarks() -> dict[str, object]:
    return {
        "landmarks": [
            {"key": key, "lat": lat, "lon": lon, "label": label}
            for key, (lat, lon, label) in sorted(LANDMARKS.items())
        ]
    }


@router.get("/datasets", summary="Every dataset ORCA can read, and its real coverage")
async def datasets() -> dict[str, object]:
    """The provenance roster.

    Includes the measured coverage end of each dataset, which is how ORCA states
    plainly that most INCOIS griddap products are archives rather than live
    feeds — a fact worth surfacing rather than hiding.
    """
    from orca.sources.erddap import DATASETS

    return {
        "generated_at": utcnow().isoformat(),
        "grids": [
            {
                "key": key,
                "dataset_id": ds.dataset_id,
                "title": ds.title,
                "provider": str(ds.provider),
                "role": ds.role,
                "coverage_start": ds.coverage_start.isoformat() if ds.coverage_start else None,
                "coverage_end": ds.coverage_end.isoformat() if ds.coverage_end else None,
                "is_current": ds.is_current,
                "provenance": ds.provenance.value,
                "notes": ds.notes,
                "url": ds.url(".html"),
            }
            for key, ds in DATASETS.items()
        ],
        "point_sources": [
            {
                "name": open_meteo.marine.name,
                "provider": str(open_meteo.marine.provider),
                "variables": list(open_meteo.marine.variables),
                "role": "live",
            },
            {
                "name": open_meteo.forecast.name,
                "provider": str(open_meteo.forecast.provider),
                "variables": list(open_meteo.forecast.variables),
                "role": "live",
            },
        ],
    }


# --------------------------------------------------------------- harbour board


@router.get("/harbours/board", summary="Verdict at every fishing harbour, by boat class")
async def harbour_board(
    state: str | None = Query(default=None, description="Restrict to one maritime state or UT."),
    fresh: bool = Query(
        default=False,
        description=(
            "Recompute instead of serving the cached board. One board costs 60 upstream "
            "calls against a 600-per-minute limit, so this is deliberate rather than default."
        ),
    ),
) -> dict[str, Any]:
    """Which stretches of coast are unsafe today, and for whom.

    A fisheries officer does not want one boat's verdict. They want the fleet's,
    laid out along their coast. This runs the same deterministic rule engine that
    answers a point query at every harbour on the Department of Fisheries' PMMSY
    register, for all five boat classes, and reports contiguous runs of the same
    verdict — because "traditional craft should not sail anywhere between Kochi
    and Mangaluru" is the form an advisory actually takes.

    No new model and no new thresholds. The same engine, run across a register
    instead of at one coordinate.
    """
    from orca.services import harbourboard

    # An empty `?state=` is what a select with no selection sends, and it is not
    # a request for a state called "". Treating it as one returned 404 for the
    # default view of the page.
    wanted = (state or "").strip() or None

    rows = await harbourboard.board(state=wanted, fresh=fresh)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no harbours on the register for {wanted!r}; "
                f"see GET /harbours for the {len(harbourboard.HARBOURS)} available"
            ),
        )
    return harbourboard.describe(rows, age_s=harbourboard.cache_age_s(wanted))


@router.get("/harbours", summary="The fishing-harbour register")
async def harbour_register() -> dict[str, Any]:
    """The register itself, without running the engine over it."""
    from orca.services.harbours import (
        HARBOURS,
        PMMSY_CITATION,
        POSITION_CITATION,
        STATES,
        UNRESOLVED,
    )

    return {
        "count": len(HARBOURS),
        "states": list(STATES),
        "harbours": [
            {
                "name": h.name,
                "district": h.district,
                "state": h.state,
                "coast": h.coast,
                "lat": h.lat,
                "lon": h.lon,
                "position_proxy": h.position_proxy,
            }
            for h in HARBOURS
        ],
        "unresolved": UNRESOLVED,
        "source": PMMSY_CITATION.model_dump(mode="json"),
        "positions": POSITION_CITATION.model_dump(mode="json"),
    }

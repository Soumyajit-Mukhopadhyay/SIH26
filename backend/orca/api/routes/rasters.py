"""Raster endpoints: the colour-mapped fields the map draws.

Deliberately dumb: the ingest job did the work, so these serve files. That is
the whole point of the plan's raster decision — no tile server to keep alive, no
pyramid to build, and a request that cannot be slow because it never touches an
upstream API.

``/rasters/legend/{variable}`` exists because a legend generated from the same
lookup table as the image is a legend that cannot lie about what a colour means.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from orca.config import get_settings
from orca.jobs import ingest
from orca.provenance import utcnow
from orca.science import colormap, vectorfield
from orca.science.grid import AOI, H3_RESOLUTION

log = logging.getLogger(__name__)

router = APIRouter(tags=["rasters"])

#: Guard against a path-traversal via the variable segment. Only names we
#: actually produce are servable.
#:
#: Built from BOTH registries. Deriving it from the colour ramps alone silently
#: 404'd the u/v flow fields, which are not colour-mapped — the layer rail listed
#: them, the toggle worked, and the map stayed empty with only a bare 404 in the
#: console to say why.
_ALLOWED = set(colormap.RAMPS) | set(vectorfield.SPECS) | {"pfz_rank"}

_refresh_state: dict[str, Any] = {"running": False, "last": None}


def _safe_dir(variable: str):
    if variable not in _ALLOWED:
        raise HTTPException(
            status_code=404,
            detail=f"unknown raster variable {variable!r}; see GET /rasters/catalogue",
        )
    return get_settings().raster_dir / variable


@router.get("/rasters/catalogue", summary="Which rasters exist, with their provenance")
async def raster_catalogue() -> dict[str, Any]:
    payload = ingest.catalogue()
    payload["grid"] = AOI.describe()
    payload["h3_resolution"] = H3_RESOLUTION
    payload["refresh"] = {
        "running": _refresh_state["running"],
        "last": _refresh_state["last"],
    }
    if not payload["variables"]:
        payload["hint"] = (
            "No rasters have been generated yet. POST /rasters/refresh to run the ingest "
            "job (it takes roughly 30 seconds, mostly waiting on the ERDDAP subset)."
        )
    return payload


@router.get("/rasters/legend/{variable}", summary="Legend stops, from the image's own ramp")
async def raster_legend(variable: str) -> dict[str, Any]:
    # A vector field has no colour ramp; its "legend" is the encoding, which is
    # what makes the packed PNG interpretable at all.
    spec = vectorfield.SPECS.get(variable)
    if spec is not None:
        return {
            "variable": variable,
            "kind": "vector",
            "label": spec.label,
            "unit": spec.unit,
            "max_abs": spec.max_abs,
            "direction_convention": spec.convention,
            "particle_speed": spec.particle_speed,
            "description": spec.description,
            "note": (
                "Particle paths are a rendering of the field, not a trajectory forecast. "
                "SAR drift is a separate deterministic computation."
            ),
        }
    try:
        return colormap.legend(variable)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/rasters/{variable}/{stem}.png", summary="A colour-mapped field")
async def raster_png(variable: str, stem: str) -> FileResponse:
    path = _safe_dir(variable) / f"{_safe_stem(stem)}.png"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"no {variable} raster for {stem!r}; POST /rasters/refresh to generate one",
        )
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            # The image for a given timestep never changes, so it can be cached
            # hard. `latest` is the exception and must revalidate.
            "Cache-Control": "no-cache" if stem == "latest" else "public, max-age=86400",
        },
    )


@router.get("/rasters/{variable}/{stem}.json", summary="The sidecar that makes the PNG readable")
async def raster_sidecar(variable: str, stem: str) -> JSONResponse:
    path = _safe_dir(variable) / f"{_safe_stem(stem)}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no {variable} sidecar for {stem!r}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"corrupt sidecar: {exc}") from exc
    return JSONResponse(payload, headers={"Cache-Control": "no-cache"})


@router.post("/rasters/refresh", summary="Run the ingest job")
async def raster_refresh(
    background: BackgroundTasks,
    detector: str = Query("sobel", pattern="^(sobel|canny|sied)$"),
) -> dict[str, Any]:
    """Kick the ingest job off in the background.

    Returns immediately: the ERDDAP subset takes ~20 s and holding an HTTP
    request open for it would make the console feel broken. Poll
    ``/rasters/catalogue`` to see when the new rasters land.
    """
    if _refresh_state["running"]:
        return {
            "status": "already_running",
            "detail": "an ingest pass is in flight; poll /rasters/catalogue",
        }

    async def task() -> None:
        _refresh_state["running"] = True
        try:
            report = await ingest.run_ingest(detector=detector)
            _refresh_state["last"] = report.describe()
        except Exception as exc:
            log.exception("ingest job failed")
            _refresh_state["last"] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            _refresh_state["running"] = False

    background.add_task(task)
    return {
        "status": "started",
        "detector": detector,
        "started_at": utcnow().isoformat(),
        "detail": "poll GET /rasters/catalogue; expect roughly 30 seconds",
    }


@router.get("/pfz/zones", summary="PFZ rank polygons with their derivation")
async def pfz_zones(
    min_rank: int = Query(1, ge=1, le=3),
) -> dict[str, Any]:
    """The zones the last ingest derived, straight from the PFZ sidecar."""
    path = get_settings().raster_dir / "pfz_rank" / "latest.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="no PFZ has been derived yet; POST /rasters/refresh first",
        )
    sidecar = json.loads(path.read_text(encoding="utf-8"))
    zones = [z for z in sidecar.get("zones", []) if z["rank"] >= min_rank]
    derivation = sidecar.get("pfz", {})
    return {
        "valid_time": sidecar.get("valid_time"),
        "provenance": sidecar.get("provenance"),
        "lineage": sidecar.get("lineage", []),
        "method": sidecar.get("method"),
        "derivation": derivation,
        "zone_count": len(zones),
        "zones": zones,
        # Stated on every response, because the whole PFZ layer rests on it.
        "disclaimer": (
            "INCOIS publishes PFZ advisories as maps, not as an API. ORCA reimplements the "
            "published methodology and validates against the official bulletins. This layer "
            "is DERIVED, its inputs are named in `lineage`, and `derivation.inputs_missing` "
            "lists any criterion that could not be applied."
        ),
    }


@router.get("/pfz/nearest", summary="Nearest PFZ, as a distance and a bearing")
async def pfz_nearest(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    min_rank: int = Query(1, ge=1, le=3),
) -> dict[str, Any]:
    """Answered as distance and compass bearing, not as a centroid.

    "Rank 2 zone 42 km south-east of you" is usable from a wheelhouse; a pair of
    decimal degrees is not.
    """
    from orca.services.geo import bearing_deg, compass_point, geodesic_m

    path = get_settings().raster_dir / "pfz_rank" / "latest.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no PFZ derived yet")
    sidecar = json.loads(path.read_text(encoding="utf-8"))

    candidates = [z for z in sidecar.get("zones", []) if z["rank"] >= min_rank]
    if not candidates:
        return {
            "found": False,
            "detail": f"no zone of rank {min_rank} or better in the current derivation",
            "derivation": sidecar.get("pfz", {}),
        }

    scored = [
        (geodesic_m(lat, lon, z["centroid"]["lat"], z["centroid"]["lon"]), z) for z in candidates
    ]
    distance_m, zone = min(scored, key=lambda pair: pair[0])
    bearing = bearing_deg(lat, lon, zone["centroid"]["lat"], zone["centroid"]["lon"])
    compass = compass_point(bearing)

    return {
        "found": True,
        "from": {"lat": lat, "lon": lon},
        "zone": zone,
        "distance_km": round(distance_m / 1000.0, 1),
        "bearing_deg": round(bearing, 1),
        "compass": compass,
        "narrative": (
            f"Rank {zone['rank']} zone about {distance_m / 1000:.0f} km {compass} of you, "
            f"roughly {zone['area_km2']:.0f} km2."
        ),
        "valid_time": sidecar.get("valid_time"),
        "provenance": sidecar.get("provenance"),
        "lineage": sidecar.get("lineage", []),
        "method": sidecar.get("method"),
    }


def _safe_stem(stem: str) -> str:
    """Only ``latest`` or a compact timestamp. No separators, no traversal."""
    if stem == "latest":
        return stem
    if len(stem) == 13 and stem[8] == "T" and stem[:8].isdigit() and stem[9:].isdigit():
        return stem
    raise HTTPException(
        status_code=400,
        detail=f"invalid timestep {stem!r}; expected 'latest' or YYYYmmddTHHMM",
    )

"""Geofence and CAP endpoints.

``/geofence/check`` is the Palk Bay demo: a position, a heading and a speed in,
and "on your current heading you cross Sri Lanka - India in 53 minutes" out.

``/advisories/cap`` emits CAP 1.2 so an ORCA advisory can be consumed by systems
that already exist — a state disaster authority's dashboard, a cell-broadcast
gateway — rather than requiring anyone to write an adapter for us.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from orca.provenance import Lat, Lon, utcnow
from orca.services import cap_builder
from orca.services import geofence as geofence_service
from orca.services.risk_engine import assess_from_evidence
from orca.services.thresholds import by_code

log = logging.getLogger(__name__)

router = APIRouter(tags=["geofence"])


class GeofenceRequest(BaseModel):
    lat: Lat
    lon: Lon
    heading_deg: float | None = Field(
        default=None, ge=0, lt=360, description="True heading. Enables time-to-cross."
    )
    speed_kn: float | None = Field(default=None, ge=0, le=60)
    radius_km: float = Field(default=120.0, gt=0, le=600)
    #: Previous state per fence key, so the response reports TRANSITIONS rather
    #: than levels. A trip monitor passes back what it last saw.
    previous: dict[str, str] = Field(default_factory=dict)


class TrackRequest(BaseModel):
    from_lat: Lat
    from_lon: Lon
    to_lat: Lat
    to_lon: Lon


@router.get("/geofence/fences", summary="The fence set, and how it is indexed")
async def fences() -> dict[str, Any]:
    if not geofence_service.index.ready:
        raise HTTPException(
            status_code=503,
            detail=(
                "the geofence index is empty — Marine Regions was unreachable at startup and "
                "nothing was cached. Every other subsystem is unaffected."
            ),
        )
    return {
        **geofence_service.index.describe(),
        "citation": geofence_service.evidence_citation().model_dump(mode="json"),
        "provenance": geofence_service.provenance().value,
    }


@router.get("/geofence/geojson", summary="Fence geometry for the map")
async def fences_geojson(
    kind: str | None = Query(None, description="Filter: eez, imbl, eez_outer"),
    simplify_deg: float = Query(
        0.01,
        ge=0,
        le=0.5,
        description=(
            "Simplification tolerance in degrees for DISPLAY only. The geofence engine always "
            "uses full resolution — a simplified boundary would put the line in the wrong place."
        ),
    ),
) -> dict[str, Any]:
    """Simplified geometry, because the full India EEZ is 72k vertices and 1.85 MB.

    The simplification is explicitly display-only and the response says so, so
    nobody is tempted to do a containment test against what they got here.
    """
    from shapely.geometry import mapping

    features = []
    for fence in geofence_service.index.fences():
        if kind and fence.kind != kind:
            continue
        geometry = fence.geometry.simplify(simplify_deg) if simplify_deg > 0 else fence.geometry
        if geometry.is_empty:
            geometry = fence.geometry
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(geometry),
                "properties": {
                    "key": fence.key,
                    "name": fence.name,
                    "kind": fence.kind,
                    "authority": fence.authority,
                    "consequence": fence.consequence,
                    "length_km": fence.length_km,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "provenance": geofence_service.provenance().value,
        "citation": geofence_service.evidence_citation().model_dump(mode="json"),
        "simplified_deg": simplify_deg,
        "note": (
            "Geometry is simplified for display only. Containment and distance are computed "
            "server-side against the full-resolution boundary."
        ),
    }


@router.post("/geofence/check", summary="Which side of each boundary, and when you cross")
async def check(request: GeofenceRequest) -> dict[str, Any]:
    if not geofence_service.index.ready:
        raise HTTPException(status_code=503, detail="the geofence index is empty")

    hits = geofence_service.index.check(
        request.lat,
        request.lon,
        heading_deg=request.heading_deg,
        speed_kn=request.speed_kn,
        radius_km=request.radius_km,
        previous=request.previous,
    )

    # Only transitions are events. A boat loitering 400 m from the line must not
    # generate an alert on every fix.
    transitions = [
        h.describe()
        for h in hits
        if request.previous.get(h.fence.key) != h.state.value
        and h.state.value in {"crossed", "exited", "approaching"}
    ]

    return {
        "position": {"lat": request.lat, "lon": request.lon},
        "heading_deg": request.heading_deg,
        "speed_kn": request.speed_kn,
        "generated_at": utcnow().isoformat(),
        "fences_in_range": len(hits),
        "proximities": [h.describe() for h in hits],
        "transitions": transitions,
        "states": {h.fence.key: h.state.value for h in hits},
        "provenance": geofence_service.provenance().value,
        "note": (
            "time_to_cross_min is computed by projecting the current heading and intersecting "
            "it with the boundary, not by dividing distance by speed — the nearest point on a "
            "line is usually not the point you are steering for."
        ),
    }


@router.post("/geofence/track", summary="Did this track segment cross a boundary?")
async def track(request: TrackRequest) -> dict[str, Any]:
    """Crossing detection between two fixes.

    Tested as a segment intersection rather than by watching distance approach
    zero: at 8 knots a boat covers ~1.5 km between fixes and steps clean over a
    line that a distance threshold may never see.
    """
    if not geofence_service.index.ready:
        raise HTTPException(status_code=503, detail="the geofence index is empty")

    events = geofence_service.crossed_line(
        geofence_service.index,
        (request.from_lat, request.from_lon),
        (request.to_lat, request.to_lon),
    )
    return {
        "from": {"lat": request.from_lat, "lon": request.from_lon},
        "to": {"lat": request.to_lat, "lon": request.to_lon},
        "crossings": events,
        "crossed": bool(events),
        "generated_at": utcnow().isoformat(),
    }


class CapRequest(BaseModel):
    lat: Lat
    lon: Lon
    #: Optional. Precedence: boat_class_code > loa_m > UNKNOWN.
    loa_m: float | None = Field(default=None, gt=0, le=200)
    boat_class_code: str | None = None
    place: str | None = None
    radius_km: float = Field(default=25.0, gt=0, le=200)
    #: Second language for a bilingual alert. CAP's own mechanism is a second
    #: <info> block, which is better than sending two alerts.
    translate_to: str | None = None


@router.post("/advisories/cap", summary="CAP 1.2 XML for the current verdict")
async def advisory_cap(request: CapRequest) -> Response:
    """A CAP 1.2 alert built from the deterministic verdict.

    Returns XML with the correct content type, so a consumer can point a CAP
    parser straight at it.
    """
    from orca.sources import open_meteo

    boat_class = None
    if request.boat_class_code:
        boat_class = by_code(request.boat_class_code)
        if boat_class is None:
            raise HTTPException(
                status_code=422, detail=f"unknown boat class {request.boat_class_code!r}"
            )

    evidence = await open_meteo.conditions_at(request.lat, request.lon)
    risk = assess_from_evidence(
        evidence,
        loa_m=request.loa_m,
        boat_class=boat_class,
        boat_class_code=None if boat_class else request.boat_class_code,
    )

    translated = None
    if request.translate_to:
        from orca.language.translate import TranslationUnavailable, translate

        try:
            headline = await translate(
                f"{risk.verdict}. " + (risk.vetoes[0] if risk.vetoes else "Conditions assessed."),
                source="en",
                target=request.translate_to,
            )
            instruction = await translate(
                risk.escalation_message
                or "Do not put to sea in a vessel of this class until conditions improve.",
                source="en",
                target=request.translate_to,
            )
            # Only include the translation if the number guard passed. A CAP
            # alert carrying a mistranslated wave height is worse than a
            # monolingual one.
            # `fully_translated`, not `safe_to_speak`: a CAP alert should carry a
            # wholly-translated <info> block or none at all, since a downstream
            # consumer cannot show a half-translated one sensibly.
            if headline.get("fully_translated") and instruction.get("fully_translated"):
                translated = {
                    "language": f"{request.translate_to}-IN",
                    "headline": headline["text"],
                    "description": headline["text"],
                    "instruction": instruction["text"],
                }
            else:
                log.warning(
                    "omitting the %s <info> block: not every clause could be verified",
                    request.translate_to,
                )
        except TranslationUnavailable as exc:
            log.warning("CAP translation unavailable: %s", exc)

    alert = cap_builder.from_risk(
        risk.model_dump(mode="json"),
        lat=request.lat,
        lon=request.lon,
        place=request.place,
        radius_km=request.radius_km,
        translated=translated,
    )
    xml = alert.to_string()
    validation = cap_builder.validate(xml)
    if not validation["valid"]:
        log.error("generated an invalid CAP document: %s", validation["problems"])

    return Response(
        content=xml,
        media_type="application/cap+xml",
        headers={
            "Content-Disposition": f'inline; filename="{alert.identifier}.xml"',
            "X-Orca-Cap-Valid": str(validation["valid"]).lower(),
            "X-Orca-Cap-Identifier": alert.identifier,
        },
    )


@router.post("/advisories/cap/validate", summary="Structurally validate a CAP document")
async def validate_cap(payload: dict[str, str]) -> dict[str, Any]:
    xml = payload.get("xml", "")
    if not xml:
        raise HTTPException(status_code=422, detail="'xml' is required")
    return cap_builder.validate(xml)

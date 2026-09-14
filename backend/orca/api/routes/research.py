"""The researcher surface: catalogue, natural-language discovery, subsetting, and
the learned models.

This is the one part of ORCA aimed at somebody who wants the *data*, not an
advisory. The problem statement names researchers alongside fishermen and coastal
authorities, and their needs genuinely differ: a fisherman wants one sentence, a
researcher wants the array, its provenance, its caveats and a citation.

What keeps it honest is the same rule as everywhere else. ``/research/discover``
puts a language model in front of the catalogue, but the model only parses the
request into variables, a box and a date range — the datasets themselves are
chosen by deterministic matching over a registry of sources ORCA has actually
integrated. A researcher can therefore never be handed a plausible-looking
dataset identifier that does not exist, which is the failure mode that would
matter most and show up latest.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from orca import research
from orca.provenance import utcnow

log = logging.getLogger(__name__)

router = APIRouter(tags=["research"])


# ------------------------------------------------------------------ catalogue


@router.get("/research/catalogue", summary="Every dataset ORCA can put in a researcher's hands")
async def catalogue() -> dict[str, Any]:
    entries = [d.describe() for d in research.CATALOGUE]
    return {
        "catalogue_version": research.CATALOGUE_VERSION,
        "count": len(entries),
        "servable": sum(1 for d in research.CATALOGUE if d.servable),
        "datasets": entries,
        "regions": {k: list(v) for k, v in research.REGIONS.items()},
        "note": (
            "Resolutions are stated AS ORCA SERVES THEM, which for several products is coarser "
            "than the upstream native resolution — that figure is given separately. A researcher "
            "copying our number into a methods section should be copying the one that describes "
            "the array they actually received."
        ),
    }


# ------------------------------------------------------------------- discover


class DiscoverRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600)
    limit: int = Field(default=8, ge=1, le=20)


@router.post("/research/discover", summary="Natural language to real datasets")
async def discover(request: DiscoverRequest) -> dict[str, Any]:
    """Parse a request with a model, then match it against the catalogue in code."""
    try:
        return await research.discover(request.question, limit=request.limit)
    except Exception as exc:
        log.exception("research discovery failed")
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------- export


#: Cells a single export may return. A researcher asking for the whole EEZ at
#: full resolution across a year would pull tens of millions of points through a
#: JSON endpoint; the honest answer is a refusal that names the real route.
MAX_EXPORT_CELLS = 250_000


@router.get("/research/export", summary="Subset a servable dataset (CSV or JSON)")
async def export(
    dataset: str = Query(description="Dataset id from /research/catalogue"),
    west: float = Query(default=60.0, ge=-180, le=180),
    south: float = Query(default=0.0, ge=-90, le=90),
    east: float = Query(default=100.0, ge=-180, le=180),
    north: float = Query(default=25.0, ge=-90, le=90),
    start: str | None = Query(default=None, description="ISO date; defaults to the latest field"),
    end: str | None = Query(default=None),
    step: float = Query(default=0.1, gt=0.01, le=2.0, description="Output resolution, degrees"),
    fmt: str = Query(default="csv", alias="format", pattern="^(csv|json)$"),
) -> Any:
    """Return a real array, with its provenance in the response.

    Only datasets marked ``servable`` come back with data. For the rest ORCA
    returns the upstream endpoint and the licence instead of proxying somebody
    else's terabytes — pretending to serve a product we merely index would be a
    worse answer than pointing at it.
    """
    entry = research.BY_ID.get(dataset)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no dataset {dataset!r} in the catalogue")
    if east <= west or north <= south:
        raise HTTPException(status_code=422, detail="east must exceed west and north exceed south")

    if not entry.servable:
        return {
            "dataset": entry.describe(),
            "served": False,
            "reason": (
                "ORCA indexes this product but does not proxy it. Use the endpoint below with "
                "your own credentials — it is the same one ORCA reads."
            ),
            "endpoint": entry.endpoint,
            "licence": entry.licence,
        }

    from orca.jobs.ingest import fetch_grid
    from orca.science.grid import Grid

    grid = Grid(west=west, south=south, east=east, north=north, step=step)
    cells = grid.nx * grid.ny
    if cells > MAX_EXPORT_CELLS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{cells:,} cells exceeds the {MAX_EXPORT_CELLS:,} limit for a single export. "
                f"Coarsen `step` (currently {step} deg), shrink the box, or go to the upstream "
                f"endpoint directly: {entry.endpoint}"
            ),
        )

    when = _parse_day(start)
    key = _erddap_key(entry.id)
    if key is None:
        raise HTTPException(
            status_code=501,
            detail=f"{entry.id} is servable in principle but has no gridded export path yet",
        )

    out = await fetch_grid(key, grid=grid, when=when)
    if out is None:
        raise HTTPException(
            status_code=503,
            detail=f"{entry.title} could not serve that box and time — the upstream returned nothing",
        )
    field, valid_time, dataset_id = out

    # Derived products are computed here rather than fetched.
    variable = entry.variables[0].name
    if entry.id == "orca_fronts_sied":
        from orca.ml.frontcast import label_from_sst

        field = label_from_sst(field, dilate=0)
        variable = "front_mask"

    lats, lons = grid.lats, grid.lons
    finite = int(np.isfinite(field).sum())
    meta = {
        "dataset": entry.id,
        "title": entry.title,
        "variable": variable,
        "unit": next((v.unit for v in entry.variables if v.name == variable), ""),
        "upstream_dataset_id": dataset_id,
        "valid_time": valid_time.isoformat(),
        "bbox": [west, south, east, north],
        "step_deg": step,
        "shape": [grid.ny, grid.nx],
        "cells": cells,
        "cells_with_data": finite,
        "provenance": entry.provenance.value,
        "licence": entry.licence,
        "caveats": entry.caveats,
        "generated_at": utcnow().isoformat(),
        "citation": _citation(entry, valid_time),
    }

    if fmt == "json":
        return {
            **meta,
            "lats": [round(float(v), 5) for v in lats],
            "lons": [round(float(v), 5) for v in lons],
            # None rather than NaN: NaN is not valid JSON and json.dumps emits a
            # bare `NaN` token that strict parsers reject.
            "values": [
                [None if not np.isfinite(v) else round(float(v), 4) for v in row] for row in field
            ],
        }

    buffer = io.StringIO()
    for key_, value in meta.items():
        buffer.write(f"# {key_}: {value}\n")
    writer = csv.writer(buffer)
    writer.writerow(["latitude", "longitude", variable])
    for row, lat in enumerate(lats):
        for col, lon in enumerate(lons):
            value = field[row, col]
            if np.isfinite(value):
                writer.writerow([f"{lat:.5f}", f"{lon:.5f}", f"{float(value):.4f}"])
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv")


def _erddap_key(dataset_id: str) -> str | None:
    """Catalogue id to the ERDDAP registry key the ingest path understands."""
    return {
        "mur_sst": "mur_sst",
        "esacci_chl_monthly": "esacci_chl_monthly",
        "incois_tmi_sst": "incois_tmi_sst",
        "incois_oceansat2": "incois_oceansat2",
        "ascat_winds": "ascat_winds",
        # Derived products are computed from MUR SST after the fetch.
        "orca_fronts_sied": "mur_sst",
    }.get(dataset_id)


def _parse_day(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(hour=9, tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"start must be YYYY-MM-DD, got {value!r}"
        ) from exc


def _citation(entry: research.Dataset, valid_time: datetime) -> str:
    year = valid_time.year
    return (
        f"{entry.provider} ({year}). {entry.title}. Accessed via ORCA "
        f"({research.CATALOGUE_VERSION}) on {utcnow():%Y-%m-%d}. {entry.licence}."
    )


# ---------------------------------------------------------------- federation


@router.get("/research/federation/servers", summary="Which public data servers ORCA searches")
async def federation_servers() -> dict[str, Any]:
    from orca.research import federation

    return federation.servers_report()


@router.get("/research/federation/search", summary="Search the public ERDDAP network")
async def federation_search(
    q: str = Query(min_length=2, max_length=200, description="Free-text search"),
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    """Live search across every reachable public ERDDAP server.

    ORCA stores none of this. The response is identifiers and subsetting URLs on
    the providers' own servers — mirroring other institutions' archives would be
    a licensing problem, a storage problem and a staleness problem at once.
    """
    from orca.research import federation

    bbox = None
    if None not in (west, south, east, north):
        bbox = (float(west), float(south), float(east), float(north))  # type: ignore[arg-type]
    return await federation.search(terms=q, bbox=bbox, start=start, end=end, limit=limit)


@router.get("/research/federation/preview", summary="A few real rows from a federated dataset")
async def federation_preview(
    server: str = Query(description="Server key from /research/federation/servers"),
    dataset_id: str = Query(min_length=1, max_length=120),
    protocol: str = Query(default="tabledap", pattern="^(griddap|tabledap)$"),
    rows: int = Query(default=40, ge=1, le=60),
) -> dict[str, Any]:
    """Pull a handful of real values so the shape and units are visible.

    Hard row cap, and nothing is stored: a preview exists so a researcher can
    see what they would get before committing to a download, not to become a
    quiet mirror of somebody else's archive.
    """
    from orca.research import federation

    return await federation.preview(
        server_key=server, dataset_id=dataset_id, protocol=protocol, rows=rows
    )


# ------------------------------------------------------------- build a file


class BuildRequestBody(BaseModel):
    # 16, not 8. The builder delivers 13 variables now and twelve of them come
    # from two range endpoints, so asking for all of them is two requests per
    # point rather than thirteen per day. The cap that matters is MAX_CELLS,
    # which plan() enforces with the actual figure.
    variables: list[str] = Field(min_length=1, max_length=16)
    west: float = Field(default=60.0, ge=-180, le=180)
    south: float = Field(default=0.0, ge=-90, le=90)
    east: float = Field(default=100.0, ge=-180, le=180)
    north: float = Field(default=25.0, ge=-90, le=90)
    start: str = Field(description="YYYY-MM-DD")
    end: str = Field(default="today", description="YYYY-MM-DD, or 'today'")
    points: int = Field(default=1, ge=1, le=9)
    step_days: int = Field(default=1, ge=1, le=30)
    format: str = Field(default="xlsx", pattern="^(xlsx|csv|json)$")


@router.post("/research/build", summary="Build a multi-variable, multi-day dataset file")
async def build_dataset(request: BuildRequestBody) -> Any:
    """Several variables over a date range, as one downloadable file.

    This is the shape a researcher actually asks for — "SST and wind off Kerala
    from 1 June to today, as a spreadsheet" — as opposed to the single-day grid
    `/research/export` returns.

    The Excel workbook carries a second sheet with every source's endpoint,
    licence and caveat. A spreadsheet that leaves the system with no record of
    where its numbers came from is how a figure ends up in a paper with the wrong
    attribution, and a commented CSV header does not survive a trip through Excel.
    """
    from fastapi.responses import Response

    from orca.research import builder

    now = utcnow().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
    try:
        start = builder.parse_day(request.start, fallback=now)
        end = builder.parse_day(request.end, fallback=now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if start > end:
        raise HTTPException(status_code=422, detail="start must not be after end")

    known = set(builder.known_variables())
    unknown = [v for v in request.variables if v not in known]
    if len(unknown) == len(request.variables):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "none of those variables can be served",
                "requested": request.variables,
                "available": sorted(known),
            },
        )

    spec = builder.BuildRequest(
        variables=request.variables,
        west=request.west,
        south=request.south,
        east=request.east,
        north=request.north,
        start=start,
        end=end,
        points=request.points,
        step_days=request.step_days,
    )
    cost = builder.plan(spec)
    if not cost["within_limits"]:
        raise HTTPException(
            status_code=413,
            detail={
                "message": "that request is too large to build in one file",
                **cost,
                "suggestions": [
                    f"raise step_days (currently {request.step_days}) to sample every Nth day",
                    "narrow the date range",
                    "ask for fewer variables, or a single point instead of a lattice",
                ],
            },
        )

    result = await builder.build(spec)
    if not result.rows:
        raise HTTPException(status_code=503, detail="no data could be retrieved for that request")

    stem = f"orca_{start:%Y%m%d}_{end:%Y%m%d}"
    if request.format == "json":
        return {"summary": result.summary(), "rows": result.rows}
    if request.format == "csv":
        return PlainTextResponse(
            builder.to_csv(result),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'},
        )
    return Response(
        content=builder.to_xlsx(result),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}.xlsx"',
            # So the browser can read the summary without parsing the workbook.
            "X-Orca-Rows": str(len(result.rows)),
            "X-Orca-Missing": str(result.gaps),
        },
    )


@router.get("/research/build/variables", summary="Variables a built file can contain")
async def build_variables() -> dict[str, Any]:
    from orca.research import builder

    return {
        "variables": builder.known_variables(),
        "max_cells": builder.MAX_CELLS,
        "max_days": builder.MAX_DAYS,
        "note": (
            "Cost is days x points x variables. A request over the limit is refused with the "
            "figure and a suggested reduction rather than left to die at a proxy timeout."
        ),
    }


# ------------------------------------------------------------ ground truth


@router.get("/research/insitu/buoys", summary="Moored buoys reporting in a box")
async def insitu_buoys(
    west: float = Query(default=60.0),
    south: float = Query(default=0.0),
    east: float = Query(default=100.0),
    north: float = Query(default=25.0),
) -> dict[str, Any]:
    """Where the physical thermometers actually are.

    Worth showing on a map because the sparsity is the point: thirteen RAMA
    stations exist inside the Indian EEZ and, measured on 2026-09-14, one had
    reported in the previous four months.
    """
    from orca.sources import insitu

    stations = await insitu.buoys_in_box(west, south, east, north)
    return {
        "bbox": [west, south, east, north],
        "reporting": len(stations),
        "stations": stations,
        "note": (
            "NOAA PMEL's RAMA array — the Indian Ocean arm of the global tropical moored buoy "
            "network. These are thermometers in the water, the only ground truth ORCA has. The "
            "array runs about a month behind, so it validates a product's track record rather "
            "than today's field."
        ),
    }


@router.get("/research/insitu/validate-sst", summary="Satellite SST against a real thermometer")
async def insitu_validate(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    days: int = Query(default=30, ge=5, le=180),
) -> dict[str, Any]:
    """Compare ORCA's satellite SST against the nearest moored buoy, day by day.

    The honest answer to "how do you know your numbers are right?". Every other
    cross-check in ORCA compares one inference with another; this one compares an
    inference with a measurement.
    """
    from orca.sources import insitu

    result = await insitu.validate_sst_against_buoy(lat, lon, days=days)
    if result is None:
        return {
            "validated": False,
            "reason": (
                "No moored buoy has reported near this position recently. Over most of the "
                "Indian EEZ that is the normal case rather than a fault — the array is sparse "
                "and much of it is currently silent."
            ),
        }
    return {"validated": True, **result.describe()}


# ---------------------------------------------------------------- the models


@router.get("/ml/models", summary="What ORCA has learned, and how well")
async def models() -> dict[str, Any]:
    from orca.ml.frontcast import frontcast

    return {
        "models": [frontcast.status()],
        "policy": (
            "A model's output is evidence, not a verdict. Nothing in orca.ml can move a "
            "GO/NO-GO — the deterministic rule engine does that, and a test asserts services/ "
            "never imports the ml package."
        ),
    }


class FrontCastRequest(BaseModel):
    west: float = Field(default=60.0, ge=-180, le=180)
    south: float = Field(default=0.0, ge=-90, le=90)
    east: float = Field(default=100.0, ge=-180, le=180)
    north: float = Field(default=25.0, ge=-90, le=90)
    step: float = Field(default=0.1, gt=0.02, le=1.0)


@router.post("/ml/frontcast/predict", summary="Forecast thermal fronts at +1/+2/+3 days")
async def frontcast_predict(request: FrontCastRequest) -> dict[str, Any]:
    """Fetch the recent SST history for a box and predict where fronts will be.

    The response carries the model's measured skill AND the persistence baseline
    it was scored against, because a forecast score with nothing to compare it to
    is not information.
    """
    from orca.jobs.ingest import fetch_grid
    from orca.ml.frontcast import HISTORY_DAYS, FrontCastUnavailable, frontcast
    from orca.science.grid import Grid

    if not frontcast.available:
        status = frontcast.status()
        raise HTTPException(
            status_code=503,
            detail={
                "message": "FrontCast is not available on this deployment",
                **{k: status[k] for k in ("torch_installed", "weights_present", "load_error")},
            },
        )

    grid = Grid(
        west=request.west,
        south=request.south,
        east=request.east,
        north=request.north,
        step=request.step,
    )
    if grid.nx * grid.ny > MAX_EXPORT_CELLS:
        raise HTTPException(status_code=413, detail="box too large; coarsen `step`")

    end = utcnow().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=2)
    history: list[np.ndarray] = []
    days_used: list[str] = []
    for offset in range(HISTORY_DAYS - 1, -1, -1):
        day = end - timedelta(days=offset)
        out = await fetch_grid("mur_sst", grid=grid, when=day)
        if out is None:
            continue
        history.append(out[0])
        days_used.append(day.strftime("%Y-%m-%d"))

    if not history:
        raise HTTPException(status_code=503, detail="no SST history available for that box")

    try:
        prediction = frontcast.predict(history)
    except FrontCastUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        **prediction.describe(),
        "bbox": [request.west, request.south, request.east, request.north],
        "step_deg": request.step,
        "shape": [grid.ny, grid.nx],
        "lats": [round(float(v), 5) for v in grid.lats],
        "lons": [round(float(v), 5) for v in grid.lons],
        "history_days_used": days_used,
        "history_days_requested": HISTORY_DAYS,
        "fields": {
            f"+{lead}d": [
                [None if not np.isfinite(v) else round(float(v), 4) for v in row] for row in field
            ]
            for lead, field in prediction.probability.items()
        },
        "reading_it": (
            "Values are the probability that a pixel lies in a thermal front ZONE at that lead "
            "time, as ORCA's Cayula-Cornillon SIED would label it. Compare the model's skill "
            "against the persistence baseline in training_report before relying on it."
        ),
    }

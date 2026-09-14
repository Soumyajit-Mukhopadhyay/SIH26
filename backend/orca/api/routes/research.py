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

from orca.provenance import utcnow
from orca.services import research

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

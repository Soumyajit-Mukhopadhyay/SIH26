"""Build a researcher's dataset: many variables, many days, one file.

## What this is for

A researcher does not want a raster. They want "sea-surface temperature and wind
speed off Kerala, from 1 June to today, as a spreadsheet" — several variables,
a date range, one file they can open.

Everything needed for that already existed in pieces: the ERDDAP grid fetcher
takes a ``when``, the catalogue knows which datasets serve which variables, and
the intent parser turns prose into a box and a range. This assembles them.

## Two decisions that shape the output

**A time series at one point, not a grid per day.** A 250x400 grid over 90 days
is nine million rows and no spreadsheet opens it. What a researcher almost always
wants from a range is *how a place changed*, so the builder samples a small set
of points — the box's centre by default, or a coarse lattice if asked — and walks
time. A grid for one day is what ``/research/export`` already does, and the two
are kept separate rather than merged into one endpoint with a mode flag.

**Every request is bounded before it runs.** Days times points times variables
is the cost, and it is computed and checked up front. A researcher who asks for
something too large gets a refusal that names the number and suggests the
reduction, rather than a request that dies at a proxy timeout with nothing to
show for it.

## The file carries its own provenance

The Excel workbook gets a second sheet — ``Provenance`` — with the dataset ids,
the endpoints, the licences, the caveats and a citation line. A spreadsheet that
leaves the system with no record of where its numbers came from is exactly how a
figure ends up in a paper with the wrong attribution.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from orca.provenance import utcnow
from orca.research.catalogue import BY_ID, CATALOGUE, CATALOGUE_VERSION, Dataset

log = logging.getLogger(__name__)

#: Hard ceiling on (days x points x variables). Roughly what fits comfortably in
#: a spreadsheet and in an upstream's patience.
MAX_CELLS = 20_000

#: Days a single build may span. Two years of daily data at a handful of points
#: is a legitimate research request; a decade is a bulk download and belongs at
#: the provider.
MAX_DAYS = 800

#: Points sampled across the box when the caller asks for more than the centre.
#: A 3x3 lattice shows a spatial gradient without turning a time series into a
#: raster.
LATTICE = 3


@dataclass(slots=True)
class BuildRequest:
    variables: list[str]
    west: float
    south: float
    east: float
    north: float
    start: datetime
    end: datetime
    #: 1 = the box centre only. Otherwise a LATTICE x LATTICE grid.
    points: int = 1
    step_days: int = 1


@dataclass(slots=True)
class BuildResult:
    rows: list[dict[str, Any]]
    variables: list[str]
    datasets: list[Dataset]
    requested: BuildRequest
    #: Variables asked for that no catalogue dataset can serve.
    unavailable: list[str] = field(default_factory=list)
    #: (day, variable) pairs the upstream had no value for.
    gaps: int = 0
    #: Missing count per variable. A single total says "25 values are missing";
    #: this says WHICH column is empty, which is the difference between a file a
    #: researcher can debug and one they quietly mistrust.
    gaps_by_variable: dict[str, int] = field(default_factory=dict)
    days_attempted: int = 0

    def empty_columns(self) -> dict[str, str]:
        """Variables with NO value at all, and the likely reason.

        A column of blanks is the most misleading thing a dataset file can
        contain: it reads as "measured and absent" rather than "never available
        at this cadence". The commonest cause here is asking a monthly composite
        for daily values, so that is named explicitly.
        """
        total = len(self.rows)
        if not total:
            return {}
        explained: dict[str, str] = {}
        for variable in self.variables:
            if self.gaps_by_variable.get(variable, 0) < total:
                continue
            dataset = next(
                (d for d in self.datasets if any(v.name == variable for v in d.variables)), None
            )
            cadence = (dataset.cadence if dataset else "unknown").lower()
            if "month" in cadence:
                explained[variable] = (
                    f"{dataset.title if dataset else variable} is a MONTHLY composite, so it has "
                    "no value on an individual day. Request a monthly step, or use a daily "
                    "product for this variable."
                )
            else:
                # An archive whose coverage ended is the commonest cause, and it
                # is knowable rather than guessable: the ERDDAP registry records
                # the last timestamp each dataset actually contains.
                ends = _coverage_end(dataset)
                if ends is not None and self.requested.start.date() > ends.date():
                    explained[variable] = (
                        f"{dataset.title if dataset else variable} is an ARCHIVE that ends "
                        f"{ends:%Y-%m-%d}. Your range starts after that, so there is nothing to "
                        "return. Ask for a period before that date, or use a live product."
                    )
                else:
                    explained[variable] = (
                        f"No value was returned for any requested day ({cadence} cadence). The "
                        "upstream may not cover this box or this period."
                    )
        return explained

    def summary(self) -> dict[str, Any]:
        return {
            "rows": len(self.rows),
            "variables": self.variables,
            "unavailable_variables": self.unavailable,
            "days_attempted": self.days_attempted,
            "missing_values": self.gaps,
            "missing_by_variable": self.gaps_by_variable,
            "empty_columns": self.empty_columns(),
            "datasets": [
                {
                    "id": d.id,
                    "title": d.title,
                    "provider": d.provider,
                    "licence": d.licence,
                    "endpoint": d.endpoint,
                    "caveats": d.caveats,
                }
                for d in self.datasets
            ],
            "catalogue_version": CATALOGUE_VERSION,
            "generated_at": utcnow().isoformat(),
        }


def plan(request: BuildRequest) -> dict[str, Any]:
    """Cost a request before running it, and say what it would cost.

    Returned rather than raised so the caller can show the number and offer the
    reduction. A refusal that names the figure is actionable; a timeout is not.
    """
    days = max(1, (request.end - request.start).days // max(request.step_days, 1) + 1)
    points = 1 if request.points <= 1 else LATTICE * LATTICE
    cells = days * points * max(len(request.variables), 1)
    return {
        "days": days,
        "points": points,
        "variables": len(request.variables),
        "cells": cells,
        "within_limits": days <= MAX_DAYS and cells <= MAX_CELLS,
        "max_cells": MAX_CELLS,
        "max_days": MAX_DAYS,
    }


def _sources_for(variables: list[str]) -> tuple[dict[str, Dataset], list[str]]:
    """Pick one fetchable dataset per variable, preferring the finest resolution.

    Deterministic, like the rest of the matching: no model chooses a source for a
    researcher's file.

    "Fetchable" is stricter than "servable". The builder walks a date range
    through ERDDAP's griddap, so a dataset is only a candidate if it has an
    ERDDAP key here. Ranking on resolution alone picked Open-Meteo for wind —
    finest at 0.1 deg, and a forecast API with no griddap endpoint — so every
    wind cell came back empty while the file looked like it had simply found no
    data.
    """
    chosen: dict[str, Dataset] = {}
    missing: list[str] = []
    for variable in variables:
        candidates = [
            d
            for d in CATALOGUE
            if d.servable
            and _erddap_key(d.id) is not None
            and any(v.name == variable for v in d.variables)
        ]
        if not candidates:
            missing.append(variable)
            continue
        chosen[variable] = min(candidates, key=lambda d: d.resolution_deg)
    return chosen, missing


def _coverage_end(dataset: Dataset | None) -> datetime | None:
    """The last timestamp a dataset actually contains, from the ERDDAP registry.

    Measured by the registry rather than assumed from the catalogue prose, so an
    archive that quietly stopped updating cannot masquerade as a live feed.
    """
    if dataset is None:
        return None
    from orca.sources.erddap import DATASETS

    key = _erddap_key(dataset.id)
    entry = DATASETS.get(key) if key else None
    return getattr(entry, "coverage_end", None)


def _serves_daily(dataset: Dataset) -> bool:
    """Whether asking this dataset for one specific day can ever work.

    A monthly composite cannot. Checking up front matters because ORCA's HTTP
    client retries four times on failure — entirely correctly, for a transient
    error — so twenty daily requests against a monthly product became eighty
    calls to somebody else's server for an answer that was never going to exist.
    """
    return "month" not in dataset.cadence.lower()


def _points(request: BuildRequest) -> list[tuple[float, float, str]]:
    if request.points <= 1:
        lat = (request.south + request.north) / 2
        lon = (request.west + request.east) / 2
        return [(lat, lon, "centre")]
    out: list[tuple[float, float, str]] = []
    for row in range(LATTICE):
        for col in range(LATTICE):
            lat = request.south + (request.north - request.south) * (row + 0.5) / LATTICE
            lon = request.west + (request.east - request.west) * (col + 0.5) / LATTICE
            out.append((lat, lon, f"r{row}c{col}"))
    return out


#: Concurrent upstream fetches. Bounded rather than unlimited: ERDDAP servers are
#: run by research institutions on modest hardware, and a builder that opens
#: sixty simultaneous connections to one of them is the reason free endpoints get
#: rate limits. Eight is fast enough to make a 30-day build interactive and
#: polite enough not to be noticed.
CONCURRENCY = 8


async def build(request: BuildRequest) -> BuildResult:
    """Walk the date range and assemble a tidy table.

    One row per (day, point), one column per variable — "tidy" in the sense a
    statistician means, because that is the shape every analysis package expects
    and reshaping a wide table is a chore nobody thanks you for.

    Fetches run CONCURRENTLY under a semaphore. Sequentially, a 20-day build of
    three variables is sixty round trips at a second or more each, which is a
    minute of a researcher staring at a spinner and long enough for a proxy to
    give up on the request entirely.
    """
    import asyncio

    from orca.sources import erddap

    sources, missing = _sources_for(request.variables)
    served = [v for v in request.variables if v in sources]
    points = _points(request)

    days: list[datetime] = []
    current = request.start
    while current <= request.end:
        days.append(current)
        current += timedelta(days=max(request.step_days, 1))

    limit = asyncio.Semaphore(CONCURRENCY)

    async def fetch(day: datetime, lat: float, lon: float, variable: str) -> float | None:
        dataset = sources[variable]
        key = _erddap_key(dataset.id)
        if key is None or not _serves_daily(dataset):
            # Not attempted: a monthly composite has no value for one day, and
            # the retry policy would turn each impossible request into four.
            return None
        async with limit:
            try:
                found = await erddap.erddap.point(key, lat, lon, when=day, variables=[variable])
            except Exception as exc:  # noqa: BLE001 — one bad day is not fatal
                log.debug("builder: %s %s failed (%s)", variable, day.date(), exc)
                return None
        evidence = found.get(variable)
        if evidence is not None and isinstance(evidence.value, (int, float)):
            return round(float(evidence.value), 4)
        return None

    jobs = [
        (day, lat, lon, label, variable)
        for day in days
        for lat, lon, label in points
        for variable in served
    ]
    values = await asyncio.gather(
        *(fetch(day, lat, lon, variable) for day, lat, lon, _, variable in jobs)
    )

    table: dict[tuple[str, str], dict[str, Any]] = {}
    per_variable: dict[str, int] = {}
    gaps = 0
    for (day, lat, lon, label, variable), value in zip(jobs, values, strict=True):
        key = (day.strftime("%Y-%m-%d"), label)
        row = table.setdefault(
            key,
            {
                "date": key[0],
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "point": label,
            },
        )
        row[variable] = value
        if value is None:
            gaps += 1
            per_variable[variable] = per_variable.get(variable, 0) + 1

    rows = [table[k] for k in sorted(table)]
    return BuildResult(
        rows=rows,
        variables=served,
        datasets=list({sources[v].id: sources[v] for v in served}.values()),
        requested=request,
        unavailable=missing,
        gaps=gaps,
        gaps_by_variable=per_variable,
        days_attempted=len(days),
    )


def _erddap_key(dataset_id: str) -> str | None:
    return {
        "mur_sst": "mur_sst",
        "esacci_chl_monthly": "esacci_chl_monthly",
        "incois_tmi_sst": "incois_tmi_sst",
        "incois_oceansat2": "incois_oceansat2",
        # The registry key, not the catalogue id. These diverged and the lookup
        # returned None silently, so every wind cell came back empty and the
        # "archive has ended" explanation never fired either — it depends on the
        # same lookup.
        "ascat_winds": "incois_ascat_wind",
    }.get(dataset_id)


# ----------------------------------------------------------------- rendering


def to_csv(result: BuildResult) -> str:
    """Tidy CSV with a commented provenance preamble."""
    import csv

    buffer = io.StringIO()
    meta = result.summary()
    buffer.write(f"# ORCA dataset build · {meta['generated_at']}\n")
    buffer.write(f"# catalogue: {meta['catalogue_version']}\n")
    for dataset in meta["datasets"]:
        buffer.write(
            f"# source: {dataset['title']} ({dataset['provider']}) — {dataset['licence']}\n"
        )
        buffer.write(f"#   endpoint: {dataset['endpoint']}\n")
        buffer.write(f"#   caveat: {dataset['caveats']}\n")
    if result.unavailable:
        buffer.write(
            f"# NOT SERVED (no catalogue dataset provides these): {', '.join(result.unavailable)}\n"
        )
    buffer.write(f"# missing values: {result.gaps} — blank cells are upstream gaps, not zeroes\n")

    columns = ["date", "latitude", "longitude", "point", *result.variables]
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(result.rows)
    return buffer.getvalue()


def to_xlsx(result: BuildResult) -> bytes:
    """An .xlsx with the data on one sheet and its provenance on another.

    The second sheet is the point. A spreadsheet that leaves the system with no
    record of where its numbers came from is how a figure ends up in a paper with
    the wrong attribution — and a commented CSV header does not survive the trip
    through Excel, which is exactly where this file is going.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    meta = result.summary()
    book = Workbook()

    sheet = book.active
    sheet.title = "Data"
    columns = ["date", "latitude", "longitude", "point", *result.variables]
    sheet.append(columns)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for row in result.rows:
        sheet.append([row.get(name) for name in columns])
    for index, name in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = max(11, len(name) + 3)

    notes = book.create_sheet("Provenance")
    notes.column_dimensions["A"].width = 22
    notes.column_dimensions["B"].width = 110
    bold = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")

    def line(label: str, value: str) -> None:
        notes.append([label, value])
        notes.cell(row=notes.max_row, column=1).font = bold
        notes.cell(row=notes.max_row, column=2).alignment = wrap

    line("Generated by", "ORCA — Marine EcOsystem Reasoning with Collaborative Agents")
    line("Generated at", meta["generated_at"])
    line("Catalogue version", meta["catalogue_version"])
    line(
        "Area",
        f"{result.requested.west}, {result.requested.south} to "
        f"{result.requested.east}, {result.requested.north}",
    )
    line("Period", f"{result.requested.start:%Y-%m-%d} to {result.requested.end:%Y-%m-%d}")
    line("Rows", str(len(result.rows)))
    line("Missing values", f"{result.gaps} — blank cells are upstream gaps, not zeroes")
    for variable, why in result.empty_columns().items():
        line(f"EMPTY: {variable}", why)
    if result.unavailable:
        line(
            "NOT served",
            ", ".join(result.unavailable) + " — no dataset in ORCA's catalogue provides these",
        )
    notes.append([])

    for dataset in meta["datasets"]:
        line("Source", dataset["title"])
        line("  provider", dataset["provider"])
        line("  licence", dataset["licence"])
        line("  endpoint", dataset["endpoint"])
        line("  caveat", dataset["caveats"])
        notes.append([])

    line(
        "How to cite",
        "; ".join(
            f"{d['provider']}. {d['title']}. Accessed via ORCA on "
            f"{utcnow():%Y-%m-%d}. {d['licence']}."
            for d in meta["datasets"]
        ),
    )

    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def parse_day(value: str, *, fallback: datetime) -> datetime:
    """ISO date, or 'today'/'now'. Researchers write both."""
    text = (value or "").strip().lower()
    if text in ("", "today", "now", "current"):
        return fallback
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(hour=9, tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"expected YYYY-MM-DD or 'today', got {value!r}") from exc


def known_variables() -> list[str]:
    """Variables the builder can ACTUALLY deliver, not merely ones we list.

    A variable only qualifies if some servable dataset both has an ERDDAP key
    here AND declares a mapping for it in the registry. `wind_speed` failed that
    second test: the ASCAT entry carries no variable mapping, so a request for it
    returned an empty column on every row. Advertising a variable and then
    handing back blanks is worse than not offering it — the researcher cannot
    tell "no data today" from "never possible".
    """
    from orca.sources.erddap import DATASETS

    available: set[str] = set()
    for dataset in CATALOGUE:
        if not dataset.servable:
            continue
        key = _erddap_key(dataset.id)
        entry = DATASETS.get(key) if key else None
        if entry is None:
            continue
        mapped = set(entry.variables.values())
        for variable in dataset.variables:
            if variable.name in mapped:
                available.add(variable.name)
    return sorted(available)


__all__ = [
    "BY_ID",
    "BuildRequest",
    "BuildResult",
    "build",
    "known_variables",
    "parse_day",
    "plan",
    "to_csv",
    "to_xlsx",
]

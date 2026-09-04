"""The ingest job: pull, clip, derive, colour-map, write.

The pragmatic pattern the blueprint prescribes, and the reason it matters here:
the AOI subset from ERDDAP takes ~20 seconds, which is fine on a schedule and
unacceptable inside a request. So nothing in the request path fetches a grid;
requests read the PNG and the sidecar this job wrote.

One run produces, per variable, ``data/rasters/<variable>/latest.png`` plus a
``latest.json`` sidecar, and a timestamped copy so the time slider has history to
scrub through.

Failure is per-variable and non-fatal. If chlorophyll is unavailable the SST
raster and a rank-1 PFZ still land, and the PFZ result records that its
productivity criterion could not be applied — which is the honest degradation the
provenance model exists to express.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Provenance, Provider, utcnow
from orca.science import colormap
from orca.science.grid import AOI, Grid, regrid_nearest
from orca.sources.erddap import DATASETS, erddap

log = logging.getLogger(__name__)

#: Where the netCDF subsets land before they are turned into rasters. Not the
#: served directory: these are working files and are safe to delete.
CACHE_SUBDIR = "cache"


@dataclass
class IngestReport:
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    variables_written: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        end = self.finished_at or utcnow()
        return round((end - self.started_at).total_seconds(), 2)

    def describe(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_s": self.duration_s,
            "variables_written": self.variables_written,
            "failures": self.failures,
            "notes": self.notes,
            "details": self.details,
        }


async def fetch_grid(
    key: str,
    *,
    grid: Grid = AOI,
    variable: str | None = None,
) -> tuple[np.ndarray, datetime, str] | None:
    """Fetch one ERDDAP dataset over the AOI and put it on the ORCA grid.

    Returns ``(field, valid_time, dataset_id)``, or ``None`` when the source
    cannot serve the AOI — a condition the caller must handle rather than a
    reason to abort the run.
    """
    dataset = DATASETS.get(key)
    if dataset is None:
        log.error("no such grid dataset: %s", key)
        return None

    if not dataset.is_current:
        log.info(
            "skipping %s for a live raster: coverage ends %s (it is a climatology source)",
            key,
            dataset.coverage_end,
        )
        return None

    valid_time = await erddap.latest_time(key)
    if valid_time is None:
        return None

    erddap_var = variable or next(iter(dataset.variables), None)
    if erddap_var is None:
        log.error("%s: no variable to request", key)
        return None

    # Stride server-side so we ask for roughly the ORCA grid rather than pulling a
    # 1 km global product and discarding 96% of it. Taken from each product's true
    # native resolution: one hardcoded figure would silently over- or under-sample
    # the next dataset added here.
    native_step = {"mur_sst": 0.01, "esacci_chl_monthly": 1 / 24}.get(key, grid.step)
    stride = max(1, round(grid.step / native_step))

    stamp = valid_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    south, north = grid.south, grid.north
    west, east = grid.west, grid.east
    if dataset.lon_0_360:
        west, east = west % 360, east % 360

    # Singleton axes (altitude/depth) sit between time and latitude and still need
    # a subscript. Omitting one does not error: ERDDAP applies the latitude
    # constraint to the altitude axis and reports "0.0 is greater than the axis
    # maximum", which reads like a bad bounding box rather than a missing
    # dimension. Declared per dataset in GridDataset.extra_axes so it cannot
    # recur silently on the next 4-D product.
    axis_padding = "[0]" * len(dataset.extra_axes)
    selector = (
        (f"[({stamp})]{axis_padding}[({south}):{stride}:({north})][({west}):{stride}:({east})]")
        .replace("[", "%5B")
        .replace("]", "%5D")
    )
    url = dataset.url(f".nc?{erddap_var}{selector}")

    settings = get_settings()
    cache_dir = settings.data_dir / CACHE_SUBDIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{key}_{valid_time:%Y%m%dT%H%M}.nc"

    if not target.exists():
        started = time.perf_counter()
        # netCDF rather than CSV: the AOI is 400k cells, which is ~3 MB of binary
        # and ~12 MB of text, and the text needs parsing.
        result = await erddap.fetch(url, expect_binary=True, timeout_s=180.0, conditional=False)
        if not result.ok or not result.content:
            log.warning("%s: AOI subset failed: %s", key, result.error)
            return None
        target.write_bytes(result.content)
        log.info(
            "%s: fetched %d KB in %.1fs",
            key,
            len(result.content) // 1024,
            time.perf_counter() - started,
        )

    try:
        import xarray as xr

        with xr.open_dataset(target) as ds:
            array = ds[erddap_var]
            if "time" in array.dims:
                array = array.isel(time=0)
            lat_name = "latitude" if "latitude" in ds.coords else "lat"
            lon_name = "longitude" if "longitude" in ds.coords else "lon"
            values = np.asarray(array.values, dtype=float)
            lats = np.asarray(ds[lat_name].values, dtype=float)
            lons = np.asarray(ds[lon_name].values, dtype=float)
    except Exception as exc:  # noqa: BLE001 — a corrupt cache file must not abort the run
        log.warning("%s: could not read %s: %s", key, target.name, exc)
        target.unlink(missing_ok=True)
        return None

    field_ = regrid_nearest(values, lats, lons, grid)
    return field_, valid_time, dataset.dataset_id


async def run_ingest(
    *,
    grid: Grid = AOI,
    detector: str = "sobel",
    keep_history: int = 12,
) -> IngestReport:
    """One full pass: SST, its gradient, chlorophyll if available, then PFZ."""
    report = IngestReport()
    settings = get_settings()
    raster_dir = settings.raster_dir
    raster_dir.mkdir(parents=True, exist_ok=True)
    registry.declare("jobs.ingest", provider=Provider.ORCA, variables=["sst", "pfz_rank"])

    # ---------------- SST: the field everything else needs ----------------
    sst_result = await fetch_grid("mur_sst", grid=grid)
    if sst_result is None:
        report.failures["sst"] = "MUR SST AOI subset unavailable"
        report.notes.append(
            "Without SST there is no thermal front, so no PFZ could be derived this run."
        )
        registry.record_failure("jobs.ingest", "SST subset unavailable")
        report.finished_at = utcnow()
        return report

    sst, valid_time, sst_dataset = sst_result
    _write(
        report,
        sst,
        "sst",
        valid_time=valid_time,
        directory=raster_dir,
        grid=grid,
        provenance=Provenance.CACHED,
        dataset_id=sst_dataset,
        provider="NASA",
        keep_history=keep_history,
    )

    # ---------------- SST gradient: front strength as its own layer -------
    from orca.science.fronts import gradient_magnitude

    _write(
        report,
        gradient_magnitude(sst),
        "sst_gradient",
        valid_time=valid_time,
        directory=raster_dir,
        grid=grid,
        provenance=Provenance.DERIVED,
        dataset_id="orca:sst_gradient",
        provider="ORCA",
        lineage=[sst_dataset],
        method="Sobel gradient magnitude with the land/cloud mask eroded by one cell",
        keep_history=keep_history,
    )

    # ---------------- chlorophyll: optional, monthly composite ------------
    chlorophyll = None
    chl_dataset = None
    chl_result = await fetch_grid("esacci_chl_monthly", grid=grid)
    if chl_result is None:
        report.notes.append(
            "Chlorophyll was unavailable, so the PFZ productivity criterion could not be "
            "applied and rank 3 is unreachable this run."
        )
    else:
        chlorophyll, chl_time, chl_dataset = chl_result
        _write(
            report,
            chlorophyll,
            "chlorophyll",
            valid_time=chl_time,
            directory=raster_dir,
            grid=grid,
            provenance=Provenance.CACHED,
            dataset_id=chl_dataset,
            provider="NOAA",
            keep_history=keep_history,
        )

    # ---------------- vector fields for the particle layers ---------------
    await _ingest_vector_fields(report, raster_dir, grid, keep_history)

    # ---------------- PFZ ------------------------------------------------
    # Imported here (not at module load) so FastAPI startup does not pull SciPy.
    # On Windows, eager SciPy import has failed with a paging-file / DLL error and
    # taken the whole API down — including risk/forecast that never need PFZ.
    from orca.science import pfz

    lineage = [sst_dataset] + ([chl_dataset] if chl_dataset else [])
    try:
        result = pfz.derive(
            sst=sst,
            chlorophyll=chlorophyll,
            valid_time=valid_time,
            grid=grid,
            detector=detector,  # type: ignore[arg-type]
            lineage=lineage,
        )
        _write(
            report,
            result.ranks.astype(float),
            "pfz_rank",
            valid_time=valid_time,
            directory=raster_dir,
            grid=grid,
            provenance=Provenance.DERIVED,
            dataset_id="orca:pfz_rank",
            provider="ORCA",
            lineage=lineage,
            method=result.method,
            categorical=True,
            extra={"pfz": result.describe(), "zones": result.zones[:80]},
            keep_history=keep_history,
        )
        report.details["pfz"] = result.describe()
        report.notes.extend(result.notes)
    except Exception as exc:
        log.exception("PFZ derivation failed")
        report.failures["pfz_rank"] = f"{type(exc).__name__}: {exc}"

    report.finished_at = utcnow()
    registry.record_success("jobs.ingest", provenance=Provenance.DERIVED)
    log.info(
        "ingest finished in %.1fs: wrote %s%s",
        report.duration_s,
        ", ".join(report.variables_written) or "nothing",
        f"; failures: {report.failures}" if report.failures else "",
    )
    return report


async def _ingest_vector_fields(
    report: IngestReport, raster_dir: Path, grid: Grid, keep_history: int
) -> None:
    """Sample wind and current on a coarse lattice and write u/v rasters.

    The two fields are spaced apart on purpose: Open-Meteo counts each location
    in a multi-point request as a separate call against a 600-per-minute budget,
    and running both back to back lost a batch to a 429 — which leaves a hole in
    a flow field that looks like slack water rather than missing data.
    """
    from orca.science import vectorfield
    from orca.sources.open_meteo import sample_lattice, sample_vectors

    lats, lons = sample_lattice(grid, step_deg=2.0)

    for index, kind in enumerate(("wind", "current")):
        if index > 0:
            # Space the fields apart to stay inside the per-minute call budget.
            await asyncio.sleep(12.0)
        variable = f"{kind}_uv"
        try:
            points = await sample_vectors(lats, lons, kind=kind)
            if len(points) < len(lats) * 0.5:
                report.failures[variable] = (
                    f"only {len(points)} of {len(lats)} lattice points returned; "
                    "the field would have large gaps"
                )
                continue

            u, v = vectorfield.interpolate_to_grid(points, grid, max_distance_deg=2.2)
            sidecar = vectorfield.write_vector_raster(
                u,
                v,
                variable,
                valid_time=utcnow(),
                directory=raster_dir,
                grid=grid,
                provenance=Provenance.DERIVED,
                lineage=["open_meteo.forecast" if kind == "wind" else "open_meteo.marine"],
                dataset_id=f"open_meteo.{kind}",
                provider="Open-Meteo",
            )
            report.variables_written.append(variable)
            report.details[variable] = {
                "bytes": sidecar["bytes"],
                "samples": len(points),
                "requested": len(lats),
                "statistics": sidecar["statistics"],
            }

            if len(points) < len(lats):
                report.notes.append(
                    f"{variable}: {len(points)} of {len(lats)} lattice points returned, so the "
                    "field is interpolated across some gaps."
                )
            _archive(raster_dir / variable, sidecar, keep_history)
        except Exception as exc:
            log.exception("failed to build the %s field", variable)
            report.failures[variable] = f"{type(exc).__name__}: {exc}"


def _write(
    report: IngestReport,
    field_: np.ndarray,
    variable: str,
    *,
    keep_history: int,
    **kwargs: Any,
) -> None:
    """Write one raster, recording success or failure without aborting the run."""
    try:
        sidecar = colormap.write_raster(field_, variable, stem="latest", **kwargs)
        report.variables_written.append(variable)
        report.details[variable] = {
            "bytes": sidecar["bytes"],
            "valid_time": sidecar["valid_time"],
            "statistics": sidecar["statistics"],
        }
        _archive(kwargs["directory"] / variable, sidecar, keep_history)
    except Exception as exc:
        log.exception("failed to write the %s raster", variable)
        report.failures[variable] = f"{type(exc).__name__}: {exc}"


def _archive(directory: Path, sidecar: dict[str, Any], keep: int) -> None:
    """Keep a timestamped copy so the time slider has something to scrub.

    Trimmed to ``keep`` entries: the demo needs history, not an archive, and an
    unbounded raster directory is how a laptop demo runs out of disk.
    """
    try:
        stamp = datetime.fromisoformat(str(sidecar["valid_time"])).astimezone(UTC)
    except ValueError:
        return
    label = stamp.strftime("%Y%m%dT%H%M")
    for suffix in (".png", ".json"):
        source = directory / f"latest{suffix}"
        if source.exists():
            shutil.copy2(source, directory / f"{label}{suffix}")

    snapshots = sorted(p for p in directory.glob("*.png") if p.stem != "latest")
    for stale in snapshots[:-keep]:
        stale.unlink(missing_ok=True)
        stale.with_suffix(".json").unlink(missing_ok=True)


def catalogue() -> dict[str, Any]:
    """What rasters exist on disk right now, read from the sidecars.

    Read from disk rather than from an in-memory registry so a restarted process
    still knows what it has, and so the catalogue cannot claim an image that is
    not there.
    """
    import json

    settings = get_settings()
    root = settings.raster_dir
    variables: list[dict[str, Any]] = []

    if root.is_dir():
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            sidecar_path = directory / "latest.json"
            if not sidecar_path.exists():
                continue
            try:
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            timesteps = sorted(p.stem for p in directory.glob("*.json") if p.stem != "latest")
            entry = {
                "variable": sidecar.get("variable", directory.name),
                "unit": sidecar.get("unit"),
                "valid_time": sidecar.get("valid_time"),
                "generated_at": sidecar.get("generated_at"),
                "provenance": sidecar.get("provenance"),
                "lineage": sidecar.get("lineage", []),
                "method": sidecar.get("method"),
                "bounds": sidecar.get("bounds"),
                "bytes": sidecar.get("bytes"),
                "colormap": sidecar.get("colormap"),
                "statistics": sidecar.get("statistics"),
                "png": f"/rasters/{directory.name}/latest.png",
                "sidecar": f"/rasters/{directory.name}/latest.json",
                "timesteps": timesteps,
            }
            # Pass through the kind-specific blocks rather than hand-picking a
            # fixed key list. Dropping `encoding` left the client unable to decode
            # a u/v PNG at all: the layer listed, the toggle worked, the image
            # returned 200, and nothing drew — a silent failure caused by the
            # catalogue, not by the layer.
            for optional in (
                "kind",
                "encoding",
                "direction_convention",
                "convention_note",
                "particle_speed",
                "label",
                "description",
                "pfz",
                "zones",
            ):
                if optional in sidecar:
                    entry[optional] = sidecar[optional]
            variables.append(entry)

    return {"generated_at": utcnow().isoformat(), "variables": variables}

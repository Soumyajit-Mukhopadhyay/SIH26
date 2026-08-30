"""Server-side colour mapping: a field goes out as a PNG plus a JSON sidecar.

The plan's raster decision, restated because it drives this whole module: the AOI
at 0.05 degrees is 800 x 500, which colour-maps to a ~300 KB PNG. Tiling a 300 KB
image is pure overhead — a tile server to keep alive, a pyramid to build, and
more requests than the single image costs. So ORCA writes one PNG per variable
per timestep and draws it as a deck.gl ``BitmapLayer``.

Colouring on the server rather than in a shader buys three things:

* **`cmocean`'s perceptually uniform, colour-blind-safe ramps.** Thermal for SST,
  algae for chlorophyll, and so on. Accessibility is a scored requirement, and
  hand-rolling a ramp in GLSL would quietly lose it.
* **One definition of what a colour means.** The sidecar carries the exact
  domain, so the legend and the pixels cannot disagree.
* **Honest gaps.** Land and cloud become fully transparent, not the bottom of
  the ramp. A cloud gap rendered as "cold water" is a fabricated measurement.

The sidecar is not optional metadata; it is what makes the image interpretable.
Without the domain, a viewer cannot invert a colour back to a number.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from orca.provenance import Provenance, utcnow
from orca.science.grid import AOI, Grid

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Ramp:
    """A named colour ramp with the domain it is defined over."""

    name: str
    #: cmocean colormap name, or a matplotlib name as a fallback.
    cmap: str
    vmin: float
    vmax: float
    unit: str
    label: str
    #: Whether low values should be the visually "heavy" end. Rarely needed, but
    #: chlorophyll reads better reversed on a dark ground.
    reverse: bool = False
    description: str = ""


#: One ramp per variable, fixed. The plan's rule is that a data type always gets
#: the same colour treatment so a judge learns the code in ten seconds — which
#: only works if the domains are stable across timesteps too, hence the fixed
#: vmin/vmax rather than per-image autoscaling.
RAMPS: dict[str, Ramp] = {
    "sst": Ramp(
        name="sst",
        cmap="thermal",
        vmin=22.0,
        vmax=32.0,
        unit="degC",
        label="Sea surface temperature",
        description="cmocean thermal. Fixed 22-32 degC domain covers the Indian EEZ year-round.",
    ),
    "sst_gradient": Ramp(
        name="sst_gradient",
        cmap="amp",
        vmin=0.0,
        vmax=0.25,
        unit="degC/cell",
        label="SST gradient magnitude",
        description="cmocean amp. Front strength, not temperature.",
    ),
    "sst_anomaly": Ramp(
        name="sst_anomaly",
        cmap="balance",
        vmin=-3.0,
        vmax=3.0,
        unit="degC",
        label="SST anomaly vs climatology",
        description="cmocean balance, diverging about zero. White means at climatology.",
    ),
    "chlorophyll": Ramp(
        name="chlorophyll",
        cmap="algae",
        vmin=0.0,
        vmax=2.0,
        unit="mg m-3",
        label="Chlorophyll-a",
        description="cmocean algae. Log-ish domain clipped at 2 mg m-3.",
    ),
    "wave_height": Ramp(
        name="wave_height",
        cmap="dense",
        vmin=0.0,
        vmax=4.0,
        unit="m",
        label="Significant wave height",
        description="cmocean dense. 0-4 m spans calm to a small-craft gale.",
    ),
    "wind_speed": Ramp(
        name="wind_speed",
        cmap="speed",
        vmin=0.0,
        vmax=40.0,
        unit="kn",
        label="Wind speed",
        description="cmocean speed. 40 kn is past a gale warning.",
    ),
    "current_speed": Ramp(
        name="current_speed",
        cmap="tempo",
        vmin=0.0,
        vmax=1.5,
        unit="m/s",
        label="Surface current speed",
        description="cmocean tempo.",
    ),
}

#: PFZ rank is categorical, not continuous, so it gets explicit colours rather
#: than a ramp. Cyan is ORCA's accent and rank 3 is the best zone, so rank 3 is
#: the most saturated cyan.
PFZ_COLOURS: dict[int, tuple[int, int, int, int]] = {
    0: (0, 0, 0, 0),
    1: (34, 211, 238, 70),
    2: (34, 211, 238, 140),
    3: (34, 211, 238, 225),
}


def _lookup_table(ramp: Ramp, levels: int = 256) -> np.ndarray:
    """An ``(levels, 4)`` uint8 RGBA table for a ramp.

    cmocean is preferred; matplotlib's own maps are the fallback so a missing
    cmocean does not take the raster pipeline down with it.
    """
    positions = np.linspace(0.0, 1.0, levels)
    if ramp.reverse:
        positions = positions[::-1]

    cmap = None
    try:
        import cmocean

        cmap = getattr(cmocean.cm, ramp.cmap, None)
    except ModuleNotFoundError:
        log.warning("cmocean is not installed; falling back to matplotlib colormaps")

    if cmap is None:
        import matplotlib

        fallback = {
            "thermal": "inferno",
            "amp": "Reds",
            "balance": "RdBu_r",
            "algae": "YlGn",
            "dense": "viridis",
            "speed": "cividis",
            "tempo": "GnBu",
        }.get(ramp.cmap, "viridis")
        cmap = matplotlib.colormaps[fallback]

    return (np.asarray(cmap(positions)) * 255).round().astype(np.uint8)


def colorize(
    field: np.ndarray,
    variable: str,
    *,
    ramp: Ramp | None = None,
) -> tuple[np.ndarray, Ramp]:
    """Map a float field to RGBA. NaN becomes fully transparent.

    Values outside the domain are clamped rather than dropped: 33 degC water is
    real and should render at the top of the ramp, not as a hole in the map.
    """
    chosen = ramp or RAMPS.get(variable)
    if chosen is None:
        raise KeyError(f"no ramp defined for {variable!r}; add one to RAMPS")

    table = _lookup_table(chosen)
    values = np.asarray(field, dtype=float)
    valid = np.isfinite(values)

    span = chosen.vmax - chosen.vmin
    if span <= 0:
        raise ValueError(f"ramp {chosen.name} has a non-positive domain")

    normalised = np.clip((values - chosen.vmin) / span, 0.0, 1.0)
    indices = np.zeros(values.shape, dtype=np.int32)
    indices[valid] = (normalised[valid] * (table.shape[0] - 1)).round().astype(np.int32)

    rgba = table[indices]
    # The transparency is the honest part: no data means no pixel, never the
    # bottom of the ramp.
    rgba[~valid] = (0, 0, 0, 0)
    return rgba, chosen


def colorize_ranks(ranks: np.ndarray) -> np.ndarray:
    """Categorical colouring for the PFZ rank field."""
    rgba = np.zeros((*ranks.shape, 4), dtype=np.uint8)
    for rank, colour in PFZ_COLOURS.items():
        rgba[ranks == rank] = colour
    return rgba


def write_png(rgba: np.ndarray, path: Path) -> int:
    """Write an RGBA array as a PNG. Returns the byte size."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG", optimize=True)
    return path.stat().st_size


def write_raster(
    field: np.ndarray,
    variable: str,
    *,
    valid_time: datetime,
    directory: Path,
    grid: Grid = AOI,
    provenance: Provenance = Provenance.DERIVED,
    lineage: list[str] | None = None,
    method: str | None = None,
    dataset_id: str | None = None,
    provider: str | None = None,
    categorical: bool = False,
    extra: dict[str, Any] | None = None,
    stem: str = "latest",
) -> dict[str, Any]:
    """Write ``<variable>/<stem>.png`` and ``<variable>/<stem>.json``.

    The sidecar carries the grid bounds, the colour domain, the provenance state
    and the lineage — everything the client needs to draw the image in the right
    place, label it correctly, and say where it came from.
    """
    if field.shape != grid.shape:
        raise ValueError(
            f"{variable}: field shape {field.shape} does not match the "
            f"{grid.step} degree AOI grid {grid.shape} — regrid before writing"
        )

    target = directory / variable
    png_path = target / f"{stem}.png"
    json_path = target / f"{stem}.json"

    if categorical:
        rgba = colorize_ranks(field.astype(int))
        ramp_meta: dict[str, Any] = {
            "kind": "categorical",
            "classes": [
                {"value": rank, "rgba": list(colour), "label": f"rank {rank}" if rank else "none"}
                for rank, colour in PFZ_COLOURS.items()
            ],
        }
        unit = "rank"
    else:
        rgba, ramp = colorize(field, variable)
        ramp_meta = {
            "kind": "continuous",
            "cmap": ramp.cmap,
            "vmin": ramp.vmin,
            "vmax": ramp.vmax,
            "label": ramp.label,
            "description": ramp.description,
        }
        unit = ramp.unit

    size = write_png(rgba, png_path)

    finite = np.isfinite(field)
    sidecar: dict[str, Any] = {
        "variable": variable,
        "unit": unit,
        "valid_time": valid_time.isoformat(),
        "generated_at": utcnow().isoformat(),
        "provenance": provenance.value,
        "lineage": lineage or [],
        "method": method,
        "dataset_id": dataset_id,
        "provider": provider,
        "grid": grid.describe(),
        # deck.gl BitmapLayer bounds, in the order it expects. Handing the client
        # the exact array removes a chance to transpose it.
        "bounds": grid.bitmap_bounds,
        "image": png_path.name,
        "bytes": size,
        "colormap": ramp_meta,
        "statistics": {
            "valid_cells": int(finite.sum()),
            "total_cells": int(field.size),
            "min": None if not finite.any() else round(float(np.nanmin(field)), 4),
            "max": None if not finite.any() else round(float(np.nanmax(field)), 4),
            "mean": None if not finite.any() else round(float(np.nanmean(field)), 4),
        },
    }
    if extra:
        sidecar.update(extra)

    json_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    log.info("wrote %s (%d KB) + sidecar", png_path.name, size // 1024)
    return sidecar


def legend(variable: str) -> dict[str, Any]:
    """Legend stops for the UI, sampled from the actual ramp so the swatches the
    user sees are the colours in the image."""
    if variable == "pfz_rank":
        return {
            "variable": variable,
            "kind": "categorical",
            "classes": [
                {
                    "value": rank,
                    "rgba": list(colour),
                    "label": {
                        0: "no zone",
                        1: "rank 1 - front only",
                        2: "rank 2 - front plus one indicator",
                        3: "rank 3 - front, eddy and high chlorophyll",
                    }[rank],
                }
                for rank, colour in PFZ_COLOURS.items()
            ],
        }

    ramp = RAMPS.get(variable)
    if ramp is None:
        raise KeyError(f"no ramp for {variable!r}")
    table = _lookup_table(ramp)
    stops = []
    for fraction in np.linspace(0, 1, 9):
        index = int(fraction * (table.shape[0] - 1))
        r, g, b, a = (int(v) for v in table[index])
        stops.append(
            {
                "value": round(ramp.vmin + fraction * (ramp.vmax - ramp.vmin), 3),
                "rgba": [r, g, b, a],
            }
        )
    return {
        "variable": variable,
        "kind": "continuous",
        "label": ramp.label,
        "unit": ramp.unit,
        "vmin": ramp.vmin,
        "vmax": ramp.vmax,
        "cmap": ramp.cmap,
        "description": ramp.description,
        "stops": stops,
    }

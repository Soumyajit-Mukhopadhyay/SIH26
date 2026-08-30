"""Vector fields (wind, currents) encoded as PNGs for GPU particle advection.

The plan's approach, from the layer registry: a GPU particle system reading a
**u/v PNG** — red channel is the eastward component, green the northward — plus a
JSON sidecar carrying the value range needed to decode the bytes back to m/s.
The shader lineage is ``mapbox/webgl-wind`` (ISC), written from scratch.

Why a PNG rather than JSON: 1000 vectors as JSON is ~60 KB of text the GPU cannot
read; as an RGB PNG it is ~3 KB and uploads directly as a texture that the
fragment shader samples with free bilinear interpolation. The particles then move
smoothly between grid points without us interpolating anything in JavaScript.

**The convention trap, which is the whole reason this module is careful.**
Meteorology and oceanography disagree about what a direction means:

* **Wind direction is where the wind comes FROM.** A "270 degree wind" blows
  from the west, towards the east.
* **Current direction is where the water flows TO.** A "270 degree current"
  flows towards the west.

So the two need *opposite* signs when converted to u/v. Getting it wrong yields a
field that looks entirely plausible and points backwards — arrows streaming into
the monsoon instead of with it. Both conversions are here, named, tested, and
never inlined at a call site.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from orca.provenance import Provenance, utcnow
from orca.science.grid import Grid

log = logging.getLogger(__name__)

Convention = Literal["from", "to"]

#: How each variable's direction is reported. Declared per variable rather than
#: assumed, because the two disciplines genuinely disagree and a single default
#: would silently reverse one of them.
DIRECTION_CONVENTION: dict[str, Convention] = {
    "wind": "from",  # meteorological: the direction it blows FROM
    "current": "to",  # oceanographic: the direction it flows TO
    "wave": "from",  # wave direction follows the meteorological convention
    "swell": "from",
}


def to_uv(speed: float, direction_deg: float, convention: Convention) -> tuple[float, float]:
    """Speed and compass direction to eastward/northward components.

    ``convention="from"`` (wind, waves): a 270-degree wind comes from the west
    and therefore blows *towards* the east, so u is positive.

    ``convention="to"`` (currents): a 270-degree current flows *towards* the
    west, so u is negative.

    The sign flip between the two is the entire point of this function existing.
    """
    radians = math.radians(direction_deg)
    # Pointing towards `direction_deg`.
    east = speed * math.sin(radians)
    north = speed * math.cos(radians)
    if convention == "from":
        # It comes from there, so it travels the opposite way.
        return (-east, -north)
    return (east, north)


def from_uv(u: float, v: float, convention: Convention) -> tuple[float, float]:
    """Inverse of :func:`to_uv`. Returns ``(speed, direction_deg)``."""
    speed = math.hypot(u, v)
    if speed == 0:
        return (0.0, 0.0)
    towards = math.degrees(math.atan2(u, v)) % 360.0
    return (speed, towards if convention == "to" else (towards + 180.0) % 360.0)


@dataclass(frozen=True, slots=True)
class VectorFieldSpec:
    """How a vector field is encoded, so a client can decode it exactly."""

    variable: str
    #: Symmetric encoding range in the field's unit. Values are clamped to it.
    max_abs: float
    unit: str
    label: str
    convention: Convention
    #: Multiplier the shader applies to convert texture units to screen motion.
    particle_speed: float
    description: str


SPECS: dict[str, VectorFieldSpec] = {
    "wind_uv": VectorFieldSpec(
        variable="wind_uv",
        # +/- 30 m/s covers everything short of a cyclone core. Beyond that the
        # value clamps, which the sidecar records so a client can say so.
        max_abs=30.0,
        unit="m/s",
        label="Wind at 10 m",
        convention="from",
        particle_speed=0.35,
        description=(
            "Eastward (u) and northward (v) wind components, derived from "
            "Open-Meteo speed and meteorological direction."
        ),
    ),
    "current_uv": VectorFieldSpec(
        variable="current_uv",
        #: Surface currents rarely exceed 2 m/s outside a jet.
        max_abs=2.0,
        unit="m/s",
        label="Surface current",
        convention="to",
        particle_speed=0.9,
        description=(
            "Eastward (u) and northward (v) surface current components, derived "
            "from Open-Meteo velocity and oceanographic direction."
        ),
    ),
}


def encode(u: np.ndarray, v: np.ndarray, spec: VectorFieldSpec) -> np.ndarray:
    """Pack u/v into an RGBA image.

    * **R** = u, **G** = v, both mapped from ``[-max_abs, +max_abs]`` to 0-255.
    * **B** = magnitude, so a shader can colour or fade particles by strength
      without a second texture fetch.
    * **A** = 0 where there is no data, which is how the shader knows to kill a
      particle that wanders onto land instead of advecting it through Tamil Nadu.

    128 is the zero point. A cell with no data encodes as fully transparent
    rather than as (128, 128) — "no flow" and "no data" must not look identical,
    which is the same rule the provenance model applies everywhere else.
    """
    if u.shape != v.shape:
        raise ValueError(f"u {u.shape} and v {v.shape} must match")

    valid = np.isfinite(u) & np.isfinite(v)
    scale = 127.0 / spec.max_abs

    rgba = np.zeros((*u.shape, 4), dtype=np.uint8)
    u_clamped = np.clip(np.nan_to_num(u), -spec.max_abs, spec.max_abs)
    v_clamped = np.clip(np.nan_to_num(v), -spec.max_abs, spec.max_abs)

    rgba[..., 0] = np.round(u_clamped * scale + 128).astype(np.uint8)
    rgba[..., 1] = np.round(v_clamped * scale + 128).astype(np.uint8)
    magnitude = np.hypot(u_clamped, v_clamped)
    rgba[..., 2] = np.round(np.clip(magnitude / spec.max_abs, 0, 1) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    return rgba


def decode(rgba: np.ndarray, spec: VectorFieldSpec) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of :func:`encode`, for tests and for server-side sampling."""
    scale = spec.max_abs / 127.0
    u = (rgba[..., 0].astype(float) - 128) * scale
    v = (rgba[..., 1].astype(float) - 128) * scale
    missing = rgba[..., 3] == 0
    u[missing] = np.nan
    v[missing] = np.nan
    return u, v


def write_vector_raster(
    u: np.ndarray,
    v: np.ndarray,
    variable: str,
    *,
    valid_time: datetime,
    directory: Path,
    grid: Grid,
    provenance: Provenance = Provenance.DERIVED,
    lineage: list[str] | None = None,
    dataset_id: str | None = None,
    provider: str | None = None,
    stem: str = "latest",
) -> dict[str, Any]:
    """Write ``<variable>/<stem>.png`` plus the sidecar the shader needs.

    The sidecar is not optional here in a way it almost is for a colour-mapped
    field: without ``max_abs`` the client cannot turn bytes back into m/s at all,
    so the image is meaningless on its own.
    """
    from PIL import Image

    spec = SPECS.get(variable)
    if spec is None:
        raise KeyError(f"no vector field spec for {variable!r}")

    rgba = encode(u, v, spec)
    target = directory / variable
    target.mkdir(parents=True, exist_ok=True)
    png_path = target / f"{stem}.png"
    Image.fromarray(rgba, mode="RGBA").save(png_path, format="PNG", optimize=True)

    valid_count = int((rgba[..., 3] > 0).sum())
    magnitude = np.hypot(np.nan_to_num(u), np.nan_to_num(v))
    finite = np.isfinite(u) & np.isfinite(v)

    sidecar = {
        "variable": variable,
        "kind": "vector",
        "unit": spec.unit,
        "label": spec.label,
        "valid_time": valid_time.isoformat(),
        "generated_at": utcnow().isoformat(),
        "provenance": provenance.value,
        "lineage": lineage or [],
        "dataset_id": dataset_id,
        "provider": provider,
        "grid": grid.describe(),
        "bounds": grid.bitmap_bounds,
        "image": png_path.name,
        "bytes": png_path.stat().st_size,
        # Everything a shader needs to decode the texture.
        "encoding": {
            "u_channel": "r",
            "v_channel": "g",
            "magnitude_channel": "b",
            "mask_channel": "a",
            "zero_point": 128,
            "max_abs": spec.max_abs,
            "scale": spec.max_abs / 127.0,
            "formula": "value = (channel - 128) * max_abs / 127",
            "note": (
                "Alpha 0 means NO DATA, not zero flow. A particle that reaches an "
                "alpha-0 cell must be respawned, not advected."
            ),
        },
        "direction_convention": spec.convention,
        "convention_note": (
            "Wind direction is reported as the direction it comes FROM; current "
            "direction as the direction it flows TO. The u/v here are already "
            "resolved to eastward/northward motion, so a client must not apply "
            "either convention again."
        ),
        "particle_speed": spec.particle_speed,
        "description": spec.description,
        # Same statistics shape as a colour-mapped raster, so the catalogue and
        # the layer rail have ONE contract. min/max/mean describe the magnitude
        # here rather than a scalar value; omitting them left the UI rendering
        # "min undefined" for the flow layers.
        "statistics": {
            "valid_cells": valid_count,
            "total_cells": int(u.size),
            "min": round(float(magnitude[finite].min()), 3) if finite.any() else None,
            "max": round(float(magnitude[finite].max()), 3) if finite.any() else None,
            "mean": round(float(magnitude[finite].mean()), 3) if finite.any() else None,
            "quantity": "speed",
            "clamped_cells": int((np.abs(np.nan_to_num(u)) > spec.max_abs).sum()),
        },
    }
    (target / f"{stem}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    log.info(
        "wrote %s (%d KB, %d/%d valid cells, max %.1f %s)",
        png_path.name,
        sidecar["bytes"] // 1024,
        valid_count,
        u.size,
        sidecar["statistics"]["max"] or 0.0,
        spec.unit,
    )
    return sidecar


def interpolate_to_grid(
    points: list[tuple[float, float, float, float]],
    grid: Grid,
    *,
    max_distance_deg: float = 1.6,
    neighbours: int = 6,
) -> tuple[np.ndarray, np.ndarray]:
    """Scatter (lat, lon, u, v) samples onto a grid by inverse-distance weighting.

    The samples come from a coarse multi-point API request, so they are sparse
    relative to the target grid. IDW over the nearest few samples gives a field
    smooth enough for particle advection without pretending to more resolution
    than we have — and cells further than ``max_distance_deg`` from any sample
    stay NaN rather than borrowing a value from across the basin.

    Uses a KD-tree over the ``neighbours`` nearest samples. The obvious
    fully-vectorised version — every cell against every sample — allocates a
    ``(ny, nx, n_samples)`` array, which for the AOI at 0.05 degrees and 800
    samples is 2.38 GiB and simply fails. It is also the wrong computation: a
    sample on the far side of the basin has no business influencing a cell, and
    cutting it off *after* allocating it is backwards.
    """
    if not points:
        return (np.full(grid.shape, np.nan), np.full(grid.shape, np.nan))

    from scipy.spatial import cKDTree

    sample = np.asarray(points, dtype=float)
    sample_lat, sample_lon = sample[:, 0], sample[:, 1]
    sample_u, sample_v = sample[:, 2], sample[:, 3]

    # Scale longitude by cos(lat) so a degree of longitude is comparable to a
    # degree of latitude. Using raw degrees would stretch the kernel east-west,
    # which at 20 N is a 6% error in the wrong direction.
    mean_cos = float(np.cos(np.deg2rad(np.mean([grid.south, grid.north]))))
    tree = cKDTree(np.column_stack([sample_lat, sample_lon * mean_cos]))

    lat2d, lon2d = grid.meshgrid()
    query = np.column_stack([lat2d.ravel(), lon2d.ravel() * mean_cos])

    k = min(neighbours, len(points))
    distance, indices = tree.query(query, k=k)
    if k == 1:
        distance = distance[:, None]
        indices = indices[:, None]

    weights = 1.0 / np.maximum(distance, 1e-6) ** 2
    weights[distance > max_distance_deg] = 0.0
    total = weights.sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        u = (weights * sample_u[indices]).sum(axis=1) / total
        v = (weights * sample_v[indices]).sum(axis=1) / total

    empty = total <= 0
    u[empty] = np.nan
    v[empty] = np.nan
    return u.reshape(grid.shape), v.reshape(grid.shape)

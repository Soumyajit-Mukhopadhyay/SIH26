"""The AOI grid. ONE definition, imported everywhere.

The blueprint flags a specific own-goal: a resolution or extent mismatch between
the ingest side and the query side produces a map that renders empty with no
error anywhere. Nothing computes its own grid — every field, every raster, every
H3 lookup comes through here.

    60-100 degrees E, 0-25 degrees N, at 0.05 degrees  ->  800 x 500 cells

That is the Indian EEZ envelope. 800 x 500 is a deliberate choice and it drives
the raster decision in the plan: a colour-mapped PNG of that size is ~300 KB, so
tiling it would be pure overhead. One image, one sidecar, drawn as a deck.gl
BitmapLayer.

Latitude runs **north-to-south** in array order, matching how images are stored
and how PNG rows are written. Getting that backwards flips the map vertically —
a bug that looks like bad data rather than bad indexing, so the convention is
stated here once and asserted in the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

#: The single H3 resolution. Res 6 is ~36 km edge, ~3.2 km apothem — the right
#: granularity for "the PFZ is 40 km south-east of you". Ingest and query MUST
#: agree, so there is one constant.
H3_RESOLUTION = 6


@dataclass(frozen=True, slots=True)
class Grid:
    """A regular lat/lon grid. Immutable, so nobody can quietly rescale it."""

    west: float
    east: float
    south: float
    north: float
    step: float

    @property
    def nx(self) -> int:
        return round((self.east - self.west) / self.step)

    @property
    def ny(self) -> int:
        return round((self.north - self.south) / self.step)

    @property
    def shape(self) -> tuple[int, int]:
        """``(ny, nx)`` — row-major, as NumPy and images both expect."""
        return (self.ny, self.nx)

    @property
    def lons(self) -> np.ndarray:
        """Cell-centre longitudes, west to east."""
        return self.west + (np.arange(self.nx) + 0.5) * self.step

    @property
    def lats(self) -> np.ndarray:
        """Cell-centre latitudes, **north to south** — image row order."""
        return self.north - (np.arange(self.ny) + 0.5) * self.step

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(west, south, east, north)`` — GeoJSON/deck.gl order."""
        return (self.west, self.south, self.east, self.north)

    @property
    def bitmap_bounds(self) -> list[float]:
        """deck.gl ``BitmapLayer`` bounds: ``[west, south, east, north]``."""
        return [self.west, self.south, self.east, self.north]

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east

    def index_of(self, lat: float, lon: float) -> tuple[int, int] | None:
        """``(row, col)`` for a position, or ``None`` if outside the AOI.

        Returning ``None`` rather than clamping is deliberate: a clamped index
        would silently answer a question about the Andaman Sea with a value from
        the grid edge.
        """
        if not self.contains(lat, lon):
            return None
        col = int((lon - self.west) / self.step)
        row = int((self.north - lat) / self.step)
        return (min(row, self.ny - 1), min(col, self.nx - 1))

    def cell_centre(self, row: int, col: int) -> tuple[float, float]:
        """``(lat, lon)`` at the centre of a cell."""
        return (
            self.north - (row + 0.5) * self.step,
            self.west + (col + 0.5) * self.step,
        )

    def meshgrid(self) -> tuple[np.ndarray, np.ndarray]:
        """``(lat2d, lon2d)``, both shaped like the grid."""
        return np.meshgrid(self.lats, self.lons, indexing="ij")

    def cell_area_km2(self) -> np.ndarray:
        """Per-row cell area, since a 0.05 degree cell shrinks towards the pole.

        Needed for anything reported as an area — a PFZ polygon quoted in km2
        would be wrong by 10% across this AOI if we assumed a constant cell.
        """
        lat_rad = np.deg2rad(self.lats)
        km_per_deg_lat = 110.574
        km_per_deg_lon = 111.320 * np.cos(lat_rad)
        return (self.step * km_per_deg_lat) * (self.step * km_per_deg_lon)

    def describe(self) -> dict[str, Any]:
        return {
            "west": self.west,
            "east": self.east,
            "south": self.south,
            "north": self.north,
            "step": self.step,
            "nx": self.nx,
            "ny": self.ny,
            "cells": self.nx * self.ny,
            "lat_order": "north_to_south",
            "h3_resolution": H3_RESOLUTION,
        }


#: **The** grid. The Indian EEZ envelope at 0.05 degrees.
AOI = Grid(west=60.0, east=100.0, south=0.0, north=25.0, step=0.05)

#: A coarser grid for whole-AOI previews and for the ingest job's first pass,
#: where 0.05 degrees would be a needlessly large upstream request.
AOI_COARSE = Grid(west=60.0, east=100.0, south=0.0, north=25.0, step=0.10)


def subgrid(grid: Grid, west: float, south: float, east: float, north: float) -> Grid:
    """A window on a grid, snapped to its cell boundaries.

    Snapping matters: an unsnapped window would put cell centres at different
    offsets from the parent grid, so a value looked up in the window and the same
    value looked up in the parent would come from different cells.
    """
    step = grid.step
    return Grid(
        west=max(grid.west, np.floor(west / step) * step),
        east=min(grid.east, np.ceil(east / step) * step),
        south=max(grid.south, np.floor(south / step) * step),
        north=min(grid.north, np.ceil(north / step) * step),
        step=step,
    )


def regrid_nearest(
    values: np.ndarray,
    src_lats: np.ndarray,
    src_lons: np.ndarray,
    target: Grid = AOI,
) -> np.ndarray:
    """Put an arbitrary lat/lon field onto an ORCA grid by nearest neighbour.

    Nearest neighbour rather than bilinear on purpose: these are geophysical
    fields with genuine land/cloud gaps encoded as NaN, and interpolating across
    a gap invents data at the coastline — exactly where a fisherman is.

    Handles a source that runs south-to-north (most netCDF) or 0-360 longitudes
    (the INCOIS grids), because getting either wrong yields a plausible-looking
    but wrong or empty field.
    """
    src_lats = np.asarray(src_lats, dtype=float)
    src_lons = np.asarray(src_lons, dtype=float)
    field = np.asarray(values, dtype=float)

    if field.shape != (src_lats.size, src_lons.size):
        raise ValueError(
            f"field shape {field.shape} does not match "
            f"({src_lats.size}, {src_lons.size}) from the supplied axes"
        )

    # Normalise 0..360 to -180..180 and re-sort, so a westward AOI still lands.
    if src_lons.max() > 180.0:
        src_lons = np.where(src_lons > 180.0, src_lons - 360.0, src_lons)
        order = np.argsort(src_lons)
        src_lons = src_lons[order]
        field = field[:, order]

    # Source latitudes ascending is the netCDF norm; our grid is descending.
    if src_lats.size > 1 and src_lats[0] < src_lats[-1]:
        src_lats = src_lats[::-1]
        field = field[::-1, :]

    row_idx = np.abs(target.lats[:, None] - src_lats[None, :]).argmin(axis=1)
    col_idx = np.abs(target.lons[:, None] - src_lons[None, :]).argmin(axis=1)

    out = field[np.ix_(row_idx, col_idx)]

    # Anything further than one source cell away is a fabrication, not a
    # resample. Blank it rather than smearing the nearest real value across a
    # region the source never covered.
    lat_tol = _spacing(src_lats) * 1.5
    lon_tol = _spacing(src_lons) * 1.5
    lat_bad = np.abs(target.lats - src_lats[row_idx]) > lat_tol
    lon_bad = np.abs(target.lons - src_lons[col_idx]) > lon_tol
    out = out.copy()
    out[lat_bad, :] = np.nan
    out[:, lon_bad] = np.nan
    return out


def _spacing(axis: np.ndarray) -> float:
    if axis.size < 2:
        return float("inf")
    return float(np.median(np.abs(np.diff(axis))))

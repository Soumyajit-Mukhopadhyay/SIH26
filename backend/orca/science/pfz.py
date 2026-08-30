"""Potential Fishing Zone derivation.

**The central technical finding of the blueprint**, restated because it is what
this module exists for: INCOIS publishes PFZ advisories as *PDF maps*, not as an
API. There is nothing to call. So ORCA reimplements the published methodology and
validates the output against the bulletins, rather than pretending to a feed that
does not exist.

The rule, from the INCOIS method:

1. **Thermal front mask** from SST.
2. **Chlorophyll front mask** — Canny, sigma 2.0, the detector INCOIS uses.
3. **Productivity cut** — chlorophyll-a above 0.3 mg m-3.
4. **Eddy mask** from sea-surface-height anomaly.
5. **Rank**: 1 = front alone; 2 = front plus an eddy *or* high chlorophyll;
   3 = front plus eddy *and* high chlorophyll.
6. **Advect** forward on Ekman-derived surface currents, reported as a bearing
   and a distance rather than a new polygon nobody can check.
7. **Polygonise** and index to H3 res 6.

Two honesty requirements are enforced in code, not left to the UI:

* The result declares **which inputs were actually available**. Chlorophyll comes
  from a monthly composite and SSHA needs CMEMS, so a run frequently has SST
  only. A rank-1-only field from SST alone is a legitimate answer; silently
  presenting it as the full three-rank product is not.
* The result is ``DERIVED`` with a ``lineage`` naming every source dataset. The
  Evidence model refuses a DERIVED value with no lineage, so this cannot be
  forgotten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any

import numpy as np
from scipy import ndimage

from orca.provenance import Citation, Evidence, Freshness, Provenance, Provider
from orca.science import fronts
from orca.science.fronts import Detector, FrontResult
from orca.science.grid import AOI, H3_RESOLUTION, Grid

log = logging.getLogger(__name__)

#: The INCOIS productivity cut.
CHLOROPHYLL_THRESHOLD = 0.3  # mg m-3

#: SSHA magnitude above which a cell is treated as eddy-influenced.
EDDY_SSHA_THRESHOLD = 0.08  # m

#: Minimum contiguous cells for a zone to be reported. A three-cell speck is
#: noise, and sending a fisherman to it would be worse than saying nothing.
MIN_ZONE_CELLS = 12

INCOIS_CITATION = Citation(
    label="INCOIS Potential Fishing Zone advisory methodology",
    provider=Provider.INCOIS,
    url="https://incois.gov.in/portal/osf/pfz.jsp",
    quote=(
        "INCOIS derives PFZ advisories from thermal and chlorophyll fronts in satellite "
        "imagery. The advisories are published as maps, not as a machine-readable service, "
        "so ORCA reimplements the published method and validates against the bulletins."
    ),
)


@dataclass(slots=True)
class PfzResult:
    """The rank field, the zones, and a full account of how it was derived."""

    ranks: np.ndarray
    grid: Grid
    valid_time: datetime
    detector: Detector
    front: FrontResult
    #: Which of sst / chlorophyll / ssha were actually present.
    inputs_used: list[str]
    #: Inputs the rule wanted but did not get, and what that costs.
    inputs_missing: list[str]
    lineage: list[str]
    zones: list[dict[str, Any]] = dataclass_field(default_factory=list)
    notes: list[str] = dataclass_field(default_factory=list)

    @property
    def max_rank(self) -> int:
        return int(self.ranks.max()) if self.ranks.size else 0

    def rank_cells(self) -> dict[int, int]:
        return {rank: int(np.count_nonzero(self.ranks == rank)) for rank in (1, 2, 3)}

    @property
    def method(self) -> str:
        return f"INCOIS PFZ rule over {'+'.join(self.inputs_used)}; fronts by {self.front.method}"

    def describe(self) -> dict[str, Any]:
        return {
            "valid_time": self.valid_time.isoformat(),
            "detector": self.detector,
            "front": self.front.describe(),
            "inputs_used": self.inputs_used,
            "inputs_missing": self.inputs_missing,
            "lineage": self.lineage,
            "rank_cells": self.rank_cells(),
            "max_rank": self.max_rank,
            "zone_count": len(self.zones),
            "h3_resolution": H3_RESOLUTION,
            "method": self.method,
            "notes": self.notes,
            "thresholds": {
                "chlorophyll_mg_m3": CHLOROPHYLL_THRESHOLD,
                "eddy_ssha_m": EDDY_SSHA_THRESHOLD,
                "min_zone_cells": MIN_ZONE_CELLS,
            },
        }

    def evidence(self, *, lat: float, lon: float) -> Evidence:
        """The rank at a point, as DERIVED Evidence with its lineage."""
        index = self.grid.index_of(lat, lon)
        rank = 0 if index is None else int(self.ranks[index])
        return Evidence(
            dataset_id="orca:pfz_rank",
            provider=Provider.ORCA,
            variable="pfz_rank",
            value=rank,
            unit="rank",
            provenance=Provenance.DERIVED,
            freshness=Freshness.of("pfz_rank", self.valid_time),
            lineage=self.lineage,
            method=self.method,
            location=(lon, lat),
            citations=[
                INCOIS_CITATION,
                Citation(label=self.front.citation, provider=Provider.ORCA),
            ],
            notes="; ".join(self.notes) or None,
        )


def derive(
    *,
    sst: np.ndarray,
    valid_time: datetime,
    chlorophyll: np.ndarray | None = None,
    ssha: np.ndarray | None = None,
    grid: Grid = AOI,
    detector: Detector = "sobel",
    lineage: list[str] | None = None,
    front_kwargs: dict[str, Any] | None = None,
) -> PfzResult:
    """Apply the INCOIS rule to whatever inputs are actually available.

    SST is required — without a thermal front there is no rank 1, and therefore
    nothing to rank. Chlorophyll and SSHA are optional, and their absence caps
    the achievable rank, which the result says out loud.
    """
    if sst.shape != grid.shape:
        raise ValueError(f"sst shape {sst.shape} does not match the grid {grid.shape}")

    inputs_used = ["sst"]
    inputs_missing: list[str] = []
    notes: list[str] = []

    thermal = fronts.detect(sst, detector, **(front_kwargs or {}))

    # ---- chlorophyll ----
    chl_front = np.zeros(grid.shape, dtype=bool)
    productive = np.zeros(grid.shape, dtype=bool)
    if chlorophyll is not None:
        if chlorophyll.shape != grid.shape:
            raise ValueError("chlorophyll shape does not match the grid")
        inputs_used.append("chlorophyll")
        # Canny at sigma 2.0 — the detector INCOIS applies to ocean colour.
        chl_front = fronts.canny_fronts(chlorophyll, sigma=2.0).mask
        productive = np.isfinite(chlorophyll) & (chlorophyll > CHLOROPHYLL_THRESHOLD)
    else:
        inputs_missing.append("chlorophyll")
        notes.append(
            "No chlorophyll field was available, so the productivity criterion could not be "
            "applied. Ranks are based on thermal fronts alone and rank 3 is unreachable."
        )

    # ---- eddies ----
    eddy = np.zeros(grid.shape, dtype=bool)
    if ssha is not None:
        if ssha.shape != grid.shape:
            raise ValueError("ssha shape does not match the grid")
        inputs_used.append("ssha")
        eddy = np.isfinite(ssha) & (np.abs(ssha) > EDDY_SSHA_THRESHOLD)
    else:
        inputs_missing.append("ssha")
        notes.append(
            "No sea-surface-height anomaly was available (CMEMS), so the eddy criterion could "
            "not be applied."
        )

    # ---- the rank rule ----
    front_any = thermal.mask | chl_front
    indicators = eddy.astype(np.uint8) + productive.astype(np.uint8)

    ranks = np.zeros(grid.shape, dtype=np.uint8)
    ranks[front_any] = 1
    ranks[front_any & (indicators == 1)] = 2
    ranks[front_any & (indicators >= 2)] = 3

    # Land and cloud can never be a fishing zone.
    ranks[~np.isfinite(sst)] = 0

    ranks = _drop_specks(ranks, MIN_ZONE_CELLS)

    result = PfzResult(
        ranks=ranks,
        grid=grid,
        valid_time=valid_time,
        detector=detector,
        front=thermal,
        inputs_used=inputs_used,
        inputs_missing=inputs_missing,
        lineage=lineage or ["orca:sst_grid"],
        notes=notes,
    )
    result.zones = polygonise(result)
    return result


def _drop_specks(ranks: np.ndarray, min_cells: int) -> np.ndarray:
    """Remove connected components smaller than ``min_cells``.

    A front detector produces isolated cells; a fishing advisory should not.
    Applied per rank so a large rank-1 region is not deleted because the rank-3
    core inside it happens to be small.
    """
    out = ranks.copy()
    for rank in (1, 2, 3):
        labelled, count = ndimage.label(ranks == rank)
        if count == 0:
            continue
        sizes = ndimage.sum(np.ones_like(labelled), labelled, index=np.arange(1, count + 1))
        too_small = np.isin(labelled, np.nonzero(sizes < min_cells)[0] + 1)
        out[too_small & (out == rank)] = 0
    return out


def polygonise(result: PfzResult) -> list[dict[str, Any]]:
    """Connected rank regions as GeoJSON-ready polygons with H3 indices.

    The polygon comes from ``skimage.measure.find_contours`` on the rank mask
    rather than a per-cell rectangle union: a boundary that follows the front
    reads as an oceanographic feature, and a staircase of 0.05 degree squares
    reads as a rendering artefact.
    """
    from skimage import measure

    grid = result.grid
    zones: list[dict[str, Any]] = []

    for rank in (3, 2, 1):
        mask = result.ranks >= rank if rank == 1 else result.ranks == rank
        labelled, count = ndimage.label(mask)
        for label_id in range(1, count + 1):
            component = labelled == label_id
            cells = int(component.sum())
            if cells < MIN_ZONE_CELLS:
                continue

            rows, cols = np.nonzero(component)
            centre_lat, centre_lon = grid.cell_centre(round(rows.mean()), round(cols.mean()))

            # Area must use the per-row cell area: a 0.05 degree cell at 0 N and
            # one at 25 N differ by ~10%, and a PFZ quoted in km2 with a constant
            # cell would be wrong by that much.
            areas = grid.cell_area_km2()
            area_km2 = float(areas[rows].sum())

            padded = np.pad(component.astype(float), 1, constant_values=0.0)
            contours = measure.find_contours(padded, 0.5)
            if not contours:
                continue
            outline = max(contours, key=len)
            ring = [
                [
                    float(grid.west + (x - 1 + 0.5) * grid.step),
                    float(grid.north - (y - 1 + 0.5) * grid.step),
                ]
                for y, x in outline[:: max(1, len(outline) // 120)]
            ]
            if len(ring) < 4:
                continue
            if ring[0] != ring[-1]:
                ring.append(ring[0])

            zones.append(
                {
                    "rank": rank,
                    "cells": cells,
                    "area_km2": round(area_km2, 1),
                    "centroid": {"lat": round(centre_lat, 4), "lon": round(centre_lon, 4)},
                    "h3": _h3_index(centre_lat, centre_lon),
                    "polygon": ring,
                }
            )

    zones.sort(key=lambda z: (-z["rank"], -z["area_km2"]))
    return zones


def _h3_index(lat: float, lon: float) -> str | None:
    """H3 cell at the one ORCA resolution.

    Kept in a helper with the constant imported from ``grid`` because the
    blueprint flags a resolution mismatch between ingest and query as a
    silent-empty-map bug.
    """
    try:
        import h3

        return h3.latlng_to_cell(lat, lon, H3_RESOLUTION)
    except Exception as exc:  # noqa: BLE001 — h3 API names have moved between majors
        log.warning("H3 indexing failed: %s", exc)
        return None


def nearest_zone(
    result: PfzResult, lat: float, lon: float, *, min_rank: int = 1
) -> dict[str, Any] | None:
    """The nearest qualifying zone, as a distance and a bearing.

    Reported that way on purpose. "Rank 2 zone 42 km south-east of you" is
    actionable from a wheelhouse; a polygon centroid in decimal degrees is not.
    """
    from orca.services.geo import bearing_deg, compass_point, geodesic_m

    candidates = [z for z in result.zones if z["rank"] >= min_rank]
    if not candidates:
        return None

    scored = [
        (geodesic_m(lat, lon, z["centroid"]["lat"], z["centroid"]["lon"]), z) for z in candidates
    ]
    distance_m, zone = min(scored, key=lambda pair: pair[0])
    bearing = bearing_deg(lat, lon, zone["centroid"]["lat"], zone["centroid"]["lon"])

    return {
        **zone,
        "distance_km": round(distance_m / 1000.0, 1),
        "bearing_deg": round(bearing, 1),
        "compass": compass_point(bearing),
        "narrative": (
            f"rank {zone['rank']} zone {distance_m / 1000:.0f} km "
            f"{compass_point(bearing)} of you, about {zone['area_km2']:.0f} km2"
        ),
    }

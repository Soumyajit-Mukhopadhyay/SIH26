"""Thermal and chlorophyll front detection.

Three detectors, and **the UI states which one produced a given layer**. That
distinction is not pedantry: a Sobel percentile threshold and Cayula-Cornillon
SIED are different claims about the ocean, and blurring them while citing the
INCOIS methodology would be the kind of thing that unravels in questioning.

* :func:`sobel_fronts` — gradient magnitude, thresholded at a percentile. Fast,
  robust, and honest about being a heuristic. The MVP default.
* :func:`canny_fronts` — Canny with sigma 2.0, which is what INCOIS actually
  applies to chlorophyll.
* :func:`sied_fronts` — Cayula & Cornillon (1992) Single Image Edge Detection,
  the published method for SST fronts: slide a window, test the histogram for
  bimodality, and accept the separating temperature only if the two populations
  are also spatially cohesive.

All three take a masked field (NaN for land and cloud) and return a boolean mask
plus per-detector diagnostics, because "how many cells did this actually fire on"
is the first question worth asking of an edge detector.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

log = logging.getLogger(__name__)

Detector = Literal["sobel", "canny", "sied"]


def _ndimage():
    """Lazy SciPy import — avoids crashing FastAPI startup on Windows DLL load."""
    from scipy import ndimage

    return ndimage


@dataclass(slots=True)
class FrontResult:
    """A front mask and everything needed to describe how it was made."""

    mask: np.ndarray
    gradient: np.ndarray
    detector: Detector
    #: Human sentence naming the method, shown in the layer legend.
    method: str
    #: Literature reference for the method, carried onto the Evidence citation.
    citation: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def cell_count(self) -> int:
        return int(np.count_nonzero(self.mask))

    @property
    def coverage_fraction(self) -> float:
        valid = np.count_nonzero(np.isfinite(self.gradient))
        return float(self.cell_count / valid) if valid else 0.0

    def describe(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "method": self.method,
            "citation": self.citation,
            "params": self.params,
            "front_cells": self.cell_count,
            "coverage_fraction": round(self.coverage_fraction, 5),
        }


def _fill_for_filtering(field_: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Replace NaN with a nearest-valid value so a convolution can run.

    Returns the filled field and the validity mask. The mask is reapplied
    afterwards — without it, every coastline becomes a spurious front, because
    the land/sea NaN boundary is the strongest gradient in the image.
    """
    valid = np.isfinite(field_)
    if valid.all():
        return field_.astype(float), valid
    filled = field_.astype(float).copy()
    if not valid.any():
        return np.zeros_like(filled), valid
    # distance_transform_edt with return_indices gives, for each invalid cell,
    # the index of the nearest valid one.
    idx = _ndimage().distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = filled[tuple(idx)]
    return filled, valid


def gradient_magnitude(field_: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude, with land and cloud excluded rather than filled.

    The mask is eroded by one cell before the gradient is kept, so the ring of
    cells whose Sobel window touched a filled value is discarded. Skipping that
    step is the single most common way a front map ends up tracing the coastline.
    """
    filled, valid = _fill_for_filtering(field_)
    gx = _ndimage().sobel(filled, axis=1, mode="nearest")
    gy = _ndimage().sobel(filled, axis=0, mode="nearest")
    magnitude = np.hypot(gx, gy) / 8.0  # /8 normalises the Sobel kernel weights

    interior = _ndimage().binary_erosion(valid, structure=np.ones((3, 3)), border_value=0)
    out = np.where(interior, magnitude, np.nan)
    return out


def sobel_fronts(
    field_: np.ndarray,
    *,
    percentile: float = 90.0,
    min_gradient: float | None = None,
) -> FrontResult:
    """Gradient magnitude above a percentile threshold.

    ``percentile`` is taken over the *valid* cells only. Including the NaN land
    mass would drag the threshold down and light up the whole basin.
    """
    grad = gradient_magnitude(field_)
    finite = grad[np.isfinite(grad)]
    if finite.size == 0:
        return FrontResult(
            mask=np.zeros(field_.shape, dtype=bool),
            gradient=grad,
            detector="sobel",
            method="Sobel gradient magnitude, percentile threshold",
            citation="Standard edge detection; ORCA MVP default.",
            params={"percentile": percentile, "threshold": None},
        )

    threshold = float(np.percentile(finite, percentile))
    if min_gradient is not None:
        threshold = max(threshold, min_gradient)

    mask = np.isfinite(grad) & (grad >= threshold)
    return FrontResult(
        mask=mask,
        gradient=grad,
        detector="sobel",
        method=f"Sobel gradient magnitude, {percentile:g}th-percentile threshold",
        citation=(
            "Heuristic gradient threshold. NOT the Cayula-Cornillon method — "
            "ORCA labels which detector produced each layer."
        ),
        params={"percentile": percentile, "threshold": round(threshold, 5)},
    )


def canny_fronts(field_: np.ndarray, *, sigma: float = 2.0) -> FrontResult:
    """Canny edges — the detector INCOIS applies to ocean colour."""
    from skimage.feature import canny

    filled, valid = _fill_for_filtering(field_)
    finite = filled[valid]
    if finite.size == 0:
        return FrontResult(
            mask=np.zeros(field_.shape, dtype=bool),
            gradient=np.full(field_.shape, np.nan),
            detector="canny",
            method="Canny",
            citation="Canny (1986)",
            params={"sigma": sigma},
        )

    # Canny wants a normalised image; scale on the valid range only.
    low, high = float(np.nanmin(finite)), float(np.nanmax(finite))
    scaled = (filled - low) / (high - low) if high > low else np.zeros_like(filled)

    interior = _ndimage().binary_erosion(
        valid, structure=np.ones((3, 3)), iterations=int(np.ceil(sigma)), border_value=0
    )
    edges = canny(scaled, sigma=sigma, mask=interior)

    return FrontResult(
        mask=edges & interior,
        gradient=gradient_magnitude(field_),
        detector="canny",
        method=f"Canny edge detector, sigma={sigma:g}",
        citation=(
            "Canny, J. (1986) A Computational Approach to Edge Detection. "
            "The detector INCOIS applies to chlorophyll imagery."
        ),
        params={"sigma": sigma},
    )


def sied_fronts(
    field_: np.ndarray,
    *,
    window: int = 32,
    stride: int | None = None,
    min_population_fraction: float = 0.25,
    min_separation: float = 0.4,
    min_cohesion: float = 0.90,
    bins: int = 64,
) -> FrontResult:
    """Cayula & Cornillon (1992) Single Image Edge Detection.

    The published SST front algorithm, and the reason it is worth implementing
    rather than approximating: it does not ask "is the gradient large", it asks
    "does this window contain two distinct water masses". Those are different
    questions, and the second is the one a fisherman cares about.

    Per window:

    1. Build the temperature histogram.
    2. Find the threshold maximising between-class variance (Otsu). Cayula and
       Cornillon phrase this as maximising the separability criterion; it is the
       same optimisation.
    3. **Bimodality test** — both populations must be large enough, and the
       separation between their means must exceed ``min_separation`` degrees.
       A single water mass with noise fails here.
    4. **Cohesion test** — the two populations must be spatially contiguous, not
       interleaved. This is the step that rejects a window that is merely noisy,
       and it is what distinguishes SIED from a thresholding heuristic.
    5. Cells on the boundary between the two accepted populations are fronts.

    ``window`` is in grid cells; at ORCA's 0.05 degree grid, 32 cells is ~1.6
    degrees, close to the 32x32 windows of the original paper on comparable
    imagery.
    """
    stride = stride or window // 2
    ny, nx = field_.shape
    mask = np.zeros(field_.shape, dtype=bool)
    windows_tested = 0
    windows_accepted = 0

    for top in range(0, max(ny - window + 1, 1), stride):
        for left in range(0, max(nx - window + 1, 1), stride):
            tile = field_[top : top + window, left : left + window]
            if tile.size == 0:
                continue
            valid = np.isfinite(tile)
            n_valid = int(valid.sum())
            # A window that is mostly land cannot support the population tests.
            if n_valid < tile.size * 0.5:
                continue

            windows_tested += 1
            values = tile[valid]
            spread = float(values.max() - values.min())
            if spread < min_separation:
                continue  # one water mass; no edge to find

            counts, edges = np.histogram(values, bins=bins)
            threshold = _otsu_threshold(counts, edges)
            if threshold is None:
                continue

            cold = valid & (tile <= threshold)
            warm = valid & (tile > threshold)
            n_cold, n_warm = int(cold.sum()), int(warm.sum())
            if min(n_cold, n_warm) < n_valid * min_population_fraction:
                continue  # lopsided split: a tail, not a second water mass

            separation = float(np.mean(tile[warm]) - np.mean(tile[cold]))
            if separation < min_separation:
                continue

            if _cohesion(cold, warm) < min_cohesion:
                continue  # interleaved, so noise rather than two water masses

            windows_accepted += 1
            # The front is the boundary between the populations.
            boundary = _population_boundary(cold, warm)
            mask[top : top + window, left : left + window] |= boundary

    return FrontResult(
        mask=mask,
        gradient=gradient_magnitude(field_),
        detector="sied",
        method=(
            f"Cayula-Cornillon SIED, {window}x{window} windows "
            f"(histogram bimodality + spatial cohesion)"
        ),
        citation=(
            "Cayula, J.-F. & Cornillon, P. (1992) Edge Detection Algorithm for SST Images. "
            "J. Atmos. Oceanic Technol. 9(1):67-80."
        ),
        params={
            "window": window,
            "stride": stride,
            "min_separation_degC": min_separation,
            "min_cohesion": min_cohesion,
            "min_population_fraction": min_population_fraction,
            "windows_tested": windows_tested,
            "windows_accepted": windows_accepted,
        },
    )


def _otsu_threshold(counts: np.ndarray, edges: np.ndarray) -> float | None:
    """The threshold maximising between-class variance.

    Cayula and Cornillon's separability criterion is the same optimisation Otsu
    describes, so this is the published step rather than a substitute for it.
    """
    total = counts.sum()
    if total == 0:
        return None
    centres = (edges[:-1] + edges[1:]) / 2.0
    weights = counts / total
    cumulative = np.cumsum(weights)
    means = np.cumsum(weights * centres)
    grand_mean = means[-1]

    denominator = cumulative * (1.0 - cumulative)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (grand_mean * cumulative - means) ** 2 / denominator
    between[~np.isfinite(between)] = 0.0
    if between.max() <= 0:
        return None
    return float(centres[int(between.argmax())])


def _cohesion(cold: np.ndarray, warm: np.ndarray) -> float:
    """Fraction of adjacent pairs that fall within the same population.

    This is Cayula and Cornillon's cohesion test. A window split by a real front
    has high cohesion — the cold cells sit together and so do the warm ones.
    A noisy window has the two populations interleaved, and scores low.
    """

    def same_neighbour_fraction(population: np.ndarray) -> tuple[int, int]:
        horizontal = population[:, :-1] & population[:, 1:]
        vertical = population[:-1, :] & population[1:, :]
        same = int(horizontal.sum() + vertical.sum())
        # Every adjacency where either member is in this population.
        touching_h = int((population[:, :-1] | population[:, 1:]).sum())
        touching_v = int((population[:-1, :] | population[1:, :]).sum())
        return same, touching_h + touching_v

    cold_same, cold_total = same_neighbour_fraction(cold)
    warm_same, warm_total = same_neighbour_fraction(warm)
    total = cold_total + warm_total
    return float((cold_same + warm_same) / total) if total else 0.0


def _population_boundary(cold: np.ndarray, warm: np.ndarray) -> np.ndarray:
    """Cells of one population directly adjacent to the other — the edge itself."""
    structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
    warm_dilated = _ndimage().binary_dilation(warm, structure=structure)
    cold_dilated = _ndimage().binary_dilation(cold, structure=structure)
    return (cold & warm_dilated) | (warm & cold_dilated)


def detect(
    field_: np.ndarray,
    detector: Detector = "sobel",
    **kwargs: Any,
) -> FrontResult:
    """Run the named detector. One entry point, so the choice is a parameter and
    the layer can always report which method produced it."""
    if detector == "sobel":
        return sobel_fronts(field_, **kwargs)
    if detector == "canny":
        return canny_fronts(field_, **kwargs)
    if detector == "sied":
        return sied_fronts(field_, **kwargs)
    raise ValueError(f"unknown detector {detector!r}; expected sobel, canny or sied")

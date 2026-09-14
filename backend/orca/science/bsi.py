"""INCOIS SVAS Boat Safety Index (BSI) — verified equations only.

Primary source
--------------
N. D. Aditya, K. G. Sandhya, R. Harikumar, T. M. Balakrishnan Nair (2020/2022).
"Development of small vessel advisory and forecast services system for safe
navigation and operations at sea." Journal of Operational Oceanography.
https://doi.org/10.1080/1755876X.2020.1846267

Verified from that paper (sections 3.2 and 4.1):

    Isteepness = (Ss / 0.05) * (Hs / h0)     eq (1), h0 = 2.5 m (Indian seas)
    Icrossing  = 0.5 * Hs * exp(-10*(σs-1)^2)  eq (2)
    Z6h        = |Hsea_i - Hsea_f| / Hsea_i    eq (4)
    warn if Z6h >= 0.2                          eq (5)

    BSI = Ssteepness + Scrosssea + Srapiddev    eq (6)
    Ssteepness = 1 if Isteepness > 0.8 else 0   eq (7), threshold §4.1
    Scrosssea  = 2 if Icrossing  > 0.65 else 0  eq (8), threshold §4.1
    Srapiddev  = 4 if Z6h        > 0.2  else 0  eq (9), threshold = 0.2

    BSI range: 0 (safe) .. 7 (all criteria exceeded).
    Any BSI > 0 is treated as dangerous in the paper (§3.2.4).

    Beam criterion (boat-specific, separate from BSI sum) eq (10):
        Beam < 4 * Hs  → vessel in that beam class is prone to capsizing
        where BSI is already non-zero.

What this module deliberately does NOT do
-----------------------------------------
* Invent directional spread from wave direction alone.
* Invent vessel beam from LOA or boat class.
* Fold visibility / CAPE / lightning into BSI.
* Map BSI 0–7 linearly onto a 0–100 score.
* Claim ORCA is INCOIS-certified or operationally identical to SVAS.

Wave steepness Ss
-----------------
Aditya et al. take Ss from SWAN model output and do not reprint the Ss formula.
ORCA may either:
  (a) accept an externally supplied Ss, or
  (b) derive deep-water significant steepness Ss = 2π Hs / (g T²)
      (standard oceanographic definition; PM limiting steepness 0.05 is the
      same constant used in eq 1).

Option (b) is labelled ``derived_deep_water`` and is NOT claimed to be identical
to INCOIS SWAN Ss (period type / spectral definition may differ).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Source / version metadata (carried into every component result)
# ---------------------------------------------------------------------------

BSI_MODEL = "INCOIS_SVAS_BSI"
BSI_MODEL_VERSION = "aditya-2020-joo-v1"
BSI_SCALE = "0-7"
BSI_SOURCE = (
    "Aditya et al. (2020), Journal of Operational Oceanography — "
    "INCOIS Small Vessel Advisory and Forecast Services (SVAS)"
)
BSI_SOURCE_URL = "https://doi.org/10.1080/1755876X.2020.1846267"
BSI_INCOIS_URL = "https://incois.gov.in/site/services/SVA_overview.jsp"

# Verified thresholds (Aditya §4.1 / eq 5)
THRESHOLD_STEEPNESS = 0.8
THRESHOLD_CROSSING = 0.65
THRESHOLD_RAPID_DEV = 0.2

# Verified constants (Aditya eq 1)
PM_LIMITING_STEEPNESS = 0.05  # PM-Limiting Zero Crossing Steepness
H0_INDIAN_SEAS_M = 2.5  # region-specific constant for Indian seas

# Binary contributions (Aditya eq 7–9)
S_STEEPNESS = 1
S_CROSSSEA = 2
S_RAPIDDEV = 4

# Beam criterion (Aditya eq 10) — Maritime & Coastguard Agency 2004 via Aditya
BEAM_HS_FACTOR = 4.0

G_MS2 = 9.81  # deep-water wavelength constant

ComponentStatus = Literal["available", "unavailable", "triggered", "clear"]
Completeness = Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"]


@dataclass(frozen=True, slots=True)
class BsiComponentResult:
    """One BSI building block with full provenance."""

    component: str
    status: ComponentStatus
    value: float | None
    threshold: float | None
    units: str | None
    triggered: bool | None
    contribution: int  # 0, or the binary weight if triggered
    source: str
    source_url: str
    model_version: str
    processing_method: str
    decision_grade: bool
    limitations: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BsiResult:
    """Full or partial Boat Safety Index assessment."""

    model: str
    model_version: str
    scale: str
    score: int | None  # None unless COMPLETE
    completeness: Completeness
    hazard: Literal["SAFE", "DANGEROUS", "UNVERIFIABLE", "PARTIAL"]
    #: Sum of available binary contributions (informative only when PARTIAL).
    partial_contribution: int
    components: list[BsiComponentResult]
    beam_criterion: dict[str, Any] | None
    source: str
    source_url: str
    limitations: list[str]
    geographic_applicability: str
    disclaimer: str

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "model_version": self.model_version,
            "scale": self.scale,
            "score": self.score,
            "completeness": self.completeness,
            "hazard": self.hazard,
            "partial_contribution": self.partial_contribution,
            "components": [c.describe() for c in self.components],
            "beam_criterion": self.beam_criterion,
            "source": self.source,
            "source_url": self.source_url,
            "limitations": self.limitations,
            "geographic_applicability": self.geographic_applicability,
            "disclaimer": self.disclaimer,
        }


def _unavailable(
    component: str,
    *,
    reason: str,
    threshold: float | None = None,
    units: str | None = None,
) -> BsiComponentResult:
    return BsiComponentResult(
        component=component,
        status="unavailable",
        value=None,
        threshold=threshold,
        units=units,
        triggered=None,
        contribution=0,
        source=BSI_SOURCE,
        source_url=BSI_SOURCE_URL,
        model_version=BSI_MODEL_VERSION,
        processing_method="not_computed",
        decision_grade=False,
        limitations=[reason],
    )


# ---------------------------------------------------------------------------
# Pure component calculators
# ---------------------------------------------------------------------------


def significant_steepness_deep_water(*, hs_m: float, period_s: float) -> float:
    """Deep-water significant steepness Ss = 2π Hs / (g T²).

    Standard oceanographic definition. Not reprinted in Aditya et al.; used
    only when an external Ss is not supplied. Caller must label the method.
    """
    if hs_m < 0:
        raise ValueError(f"hs_m must be >= 0, got {hs_m}")
    if period_s <= 0:
        raise ValueError(f"period_s must be > 0, got {period_s}")
    return (2.0 * math.pi * hs_m) / (G_MS2 * period_s * period_s)


def calculate_steepness_index(
    *,
    hs_m: float | None,
    ss: float | None = None,
    period_s: float | None = None,
    h0_m: float = H0_INDIAN_SEAS_M,
) -> BsiComponentResult:
    """Aditya eq (1): Isteepness = (Ss / 0.05) * (Hs / h0).

    Provide either ``ss`` directly, or ``period_s`` to derive deep-water Ss.
    """
    if hs_m is None:
        return _unavailable(
            "steepness",
            reason="Significant wave height (Hs) is required for Isteepness",
            threshold=THRESHOLD_STEEPNESS,
            units="dimensionless",
        )
    if hs_m < 0:
        return _unavailable(
            "steepness",
            reason=f"Invalid Hs={hs_m} m (must be >= 0)",
            threshold=THRESHOLD_STEEPNESS,
            units="dimensionless",
        )

    limitations: list[str] = []
    method: str

    if ss is not None:
        if ss < 0:
            return _unavailable(
                "steepness",
                reason=f"Invalid Ss={ss} (must be >= 0)",
                threshold=THRESHOLD_STEEPNESS,
                units="dimensionless",
            )
        steepness = ss
        method = "ss_supplied"
    elif period_s is not None and period_s > 0:
        steepness = significant_steepness_deep_water(hs_m=hs_m, period_s=period_s)
        method = "derived_deep_water_Ss=2πHs/(gT²)"
        limitations.append(
            "Ss derived from deep-water significant steepness using Open-Meteo "
            "wave period; not identical to INCOIS SWAN spectral steepness output."
        )
    else:
        return _unavailable(
            "steepness",
            reason=(
                "Wave steepness Ss is required. Provide Ss directly, or Hs + wave "
                "period so deep-water Ss can be derived."
            ),
            threshold=THRESHOLD_STEEPNESS,
            units="dimensionless",
        )

    index = (steepness / PM_LIMITING_STEEPNESS) * (hs_m / h0_m)
    triggered = index > THRESHOLD_STEEPNESS
    return BsiComponentResult(
        component="steepness",
        status="triggered" if triggered else "clear",
        value=round(index, 6),
        threshold=THRESHOLD_STEEPNESS,
        units="dimensionless",
        triggered=triggered,
        contribution=S_STEEPNESS if triggered else 0,
        source=BSI_SOURCE,
        source_url=BSI_SOURCE_URL,
        model_version=BSI_MODEL_VERSION,
        processing_method=method,
        decision_grade=method == "ss_supplied",
        limitations=limitations,
    )


def calculate_crossing_sea_index(
    *,
    hs_m: float | None,
    directional_spread: float | None,
) -> BsiComponentResult:
    """Aditya eq (2): Icrossing = 0.5 * Hs * exp(-10*(σs-1)^2).

    Directional spread σs is required. Wave direction alone is NOT a substitute.
    """
    if directional_spread is None:
        return _unavailable(
            "crossing_sea",
            reason=(
                "Directional spread (σs) is required for Icrossing seas. "
                "ORCA does not currently receive spectral directional spread; "
                "wave direction alone must not be substituted."
            ),
            threshold=THRESHOLD_CROSSING,
            units="m",
        )
    if hs_m is None:
        return _unavailable(
            "crossing_sea",
            reason="Significant wave height (Hs) is required for Icrossing seas",
            threshold=THRESHOLD_CROSSING,
            units="m",
        )
    if hs_m < 0:
        return _unavailable(
            "crossing_sea",
            reason=f"Invalid Hs={hs_m} m",
            threshold=THRESHOLD_CROSSING,
            units="m",
        )

    # Aditya: directional spread ranges 0 (unidirectional) to 2 (uniform)
    sigma = float(directional_spread)
    index = 0.5 * hs_m * math.exp(-10.0 * (sigma - 1.0) ** 2)
    triggered = index > THRESHOLD_CROSSING
    return BsiComponentResult(
        component="crossing_sea",
        status="triggered" if triggered else "clear",
        value=round(index, 6),
        threshold=THRESHOLD_CROSSING,
        units="m",
        triggered=triggered,
        contribution=S_CROSSSEA if triggered else 0,
        source=BSI_SOURCE,
        source_url=BSI_SOURCE_URL,
        model_version=BSI_MODEL_VERSION,
        processing_method="aditya_eq2",
        decision_grade=True,
        limitations=[],
    )


def calculate_rapid_development_index(
    *,
    hsea_initial_m: float | None,
    hsea_final_m: float | None,
) -> BsiComponentResult:
    """Aditya eq (4)–(5): Z6h = |Hsea_i − Hsea_f| / Hsea_i; warn if ≥ 0.2.

    Both values must be wind-sea wave height (Hsea), not total Hs.
    """
    if hsea_initial_m is None or hsea_final_m is None:
        return _unavailable(
            "rapid_development",
            reason=(
                "Wind-sea wave height at two times ~6 h apart (Hsea_i, Hsea_f) "
                "is required. Total Hs must not be substituted without justification."
            ),
            threshold=THRESHOLD_RAPID_DEV,
            units="fraction",
        )
    if hsea_initial_m <= 0:
        return _unavailable(
            "rapid_development",
            reason=f"Hsea_i must be > 0 for Z6h, got {hsea_initial_m}",
            threshold=THRESHOLD_RAPID_DEV,
            units="fraction",
        )
    if hsea_final_m < 0:
        return _unavailable(
            "rapid_development",
            reason=f"Invalid Hsea_f={hsea_final_m}",
            threshold=THRESHOLD_RAPID_DEV,
            units="fraction",
        )

    z6h = abs(hsea_initial_m - hsea_final_m) / hsea_initial_m
    # Paper eq (5): Z6h ≥ 0.2. Use a tiny epsilon so binary float (e.g. 1.2)
    # does not miss the published boundary.
    triggered = z6h + 1e-12 >= THRESHOLD_RAPID_DEV
    return BsiComponentResult(
        component="rapid_development",
        status="triggered" if triggered else "clear",
        value=round(z6h, 6),
        threshold=THRESHOLD_RAPID_DEV,
        units="fraction",
        triggered=triggered,
        contribution=S_RAPIDDEV if triggered else 0,
        source=BSI_SOURCE,
        source_url=BSI_SOURCE_URL,
        model_version=BSI_MODEL_VERSION,
        processing_method="aditya_eq4",
        decision_grade=True,
        limitations=[],
    )


def evaluate_beam_criterion(
    *,
    hs_m: float | None,
    beam_m: float | None,
    bsi_nonzero: bool,
) -> dict[str, Any]:
    """Aditya eq (10): Beam < 4×Hs — only applied when BSI is non-zero.

    Unknown beam → no vessel-specific claim (Phase 1 / Phase 3 constraint).
    """
    if beam_m is None:
        return {
            "applicable": False,
            "status": "unavailable",
            "reason": (
                "Vessel beam was not supplied. ORCA does not estimate beam from "
                "LOA or boat class. Vessel-specific BSI advisory not claimed."
            ),
            "equation": "Beam < 4 × Hs (Aditya eq 10)",
        }
    if hs_m is None:
        return {
            "applicable": False,
            "status": "unavailable",
            "reason": "Hs required for beam criterion",
            "equation": "Beam < 4 × Hs (Aditya eq 10)",
        }
    if not bsi_nonzero:
        return {
            "applicable": False,
            "status": "not_applicable",
            "reason": "Beam criterion is applied only over non-zero BSI regions (Aditya §4.4)",
            "equation": "Beam < 4 × Hs (Aditya eq 10)",
            "beam_m": beam_m,
            "hs_m": hs_m,
            "threshold_beam_m": round(BEAM_HS_FACTOR * hs_m, 3),
        }

    threshold_beam = BEAM_HS_FACTOR * hs_m
    prone = beam_m < threshold_beam
    return {
        "applicable": True,
        "status": "prone" if prone else "not_prone",
        "beam_m": beam_m,
        "hs_m": hs_m,
        "threshold_beam_m": round(threshold_beam, 3),
        "prone_to_capsizing": prone,
        "equation": "Beam < 4 × Hs (Aditya eq 10)",
        "source": BSI_SOURCE,
        "source_url": BSI_SOURCE_URL,
    }


def calculate_bsi(
    *,
    hs_m: float | None = None,
    ss: float | None = None,
    period_s: float | None = None,
    directional_spread: float | None = None,
    hsea_initial_m: float | None = None,
    hsea_final_m: float | None = None,
    beam_m: float | None = None,
) -> BsiResult:
    """Assemble BSI from verified component equations.

    Full score (0–7) is returned ONLY when every component is available.
    Otherwise completeness=PARTIAL or UNAVAILABLE — never a fabricated BSI.
    """
    steep = calculate_steepness_index(hs_m=hs_m, ss=ss, period_s=period_s)
    cross = calculate_crossing_sea_index(hs_m=hs_m, directional_spread=directional_spread)
    rapid = calculate_rapid_development_index(
        hsea_initial_m=hsea_initial_m,
        hsea_final_m=hsea_final_m,
    )
    components = [steep, cross, rapid]

    available = [c for c in components if c.status != "unavailable"]
    unavailable = [c for c in components if c.status == "unavailable"]
    partial = sum(c.contribution for c in available)

    limitations: list[str] = []
    for c in components:
        limitations.extend(c.limitations)

    if not available:
        completeness: Completeness = "UNAVAILABLE"
        score: int | None = None
        hazard: Literal["SAFE", "DANGEROUS", "UNVERIFIABLE", "PARTIAL"] = "UNVERIFIABLE"
    elif unavailable:
        completeness = "PARTIAL"
        score = None
        hazard = "PARTIAL"
        limitations.append(
            "Full INCOIS SVAS BSI (0–7) requires steepness, crossing-sea, and "
            "rapid-development components. Missing components prevent a complete score."
        )
    else:
        completeness = "COMPLETE"
        score = sum(c.contribution for c in components)
        # Aditya §3.2.4: BSI=0 safe; any non-zero is dangerous
        hazard = "SAFE" if score == 0 else "DANGEROUS"

    beam = evaluate_beam_criterion(
        hs_m=hs_m,
        beam_m=beam_m,
        bsi_nonzero=(score is not None and score > 0)
        or (completeness == "PARTIAL" and partial > 0),
    )

    return BsiResult(
        model=BSI_MODEL,
        model_version=BSI_MODEL_VERSION,
        scale=BSI_SCALE,
        score=score,
        completeness=completeness,
        hazard=hazard,
        partial_contribution=partial,
        components=components,
        beam_criterion=beam,
        source=BSI_SOURCE,
        source_url=BSI_SOURCE_URL,
        limitations=limitations,
        geographic_applicability=(
            "Formulated for Indian coastal waters (INCOIS SVAS). "
            "h0=2.5 m is the Indian-seas constant from Aditya et al."
        ),
        disclaimer=(
            "ORCA computes an SVAS-derived indicator from verified published "
            "equations. This is not an official INCOIS SVAS product and does not "
            "constitute INCOIS certification or a legal decision."
        ),
    )

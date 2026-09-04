"""Multi-model environmental assessment abstraction (Phase 3).

Keeps ORCA_LEGACY (0–100 weighted index) and INCOIS_SVAS_BSI (0–7) as
separate models. Never collapses them into one arbitrary weighted number.
"""

from __future__ import annotations

from typing import Any, Literal

from orca.science.bsi import BsiResult, calculate_bsi

EnvironmentalModelId = Literal["ORCA_LEGACY", "INCOIS_SVAS_BSI"]


def legacy_assessment(
    *,
    index: float,
    verdict: str,
    components: list[dict[str, Any]],
    thresholds_version: str,
    vetoes: list[str],
) -> dict[str, Any]:
    """Wrap the existing ORCA weighted score without altering it."""
    return {
        "model": "ORCA_LEGACY",
        "score": index,
        "scale": "0-100",
        "verdict": verdict,
        "components": components,
        "thresholds_version": thresholds_version,
        "vetoes": vetoes,
        "scientific_status": "empirical",
        "notes": (
            "ORCA legacy comfort index. Weights (wave 0.35, wind 0.30, "
            "visibility 0.15, CAPE/convective indicator 0.20) are product-designed, "
            "not taken from a primary peer-reviewed environmental hazard standard."
        ),
    }


def bsi_assessment_from_inputs(
    *,
    hs_m: float | None = None,
    period_s: float | None = None,
    ss: float | None = None,
    directional_spread: float | None = None,
    hsea_initial_m: float | None = None,
    hsea_final_m: float | None = None,
    beam_m: float | None = None,
) -> dict[str, Any]:
    """Run verified BSI calculators and return a serialisable assessment."""
    result: BsiResult = calculate_bsi(
        hs_m=hs_m,
        ss=ss,
        period_s=period_s,
        directional_spread=directional_spread,
        hsea_initial_m=hsea_initial_m,
        hsea_final_m=hsea_final_m,
        beam_m=beam_m,
    )
    payload = result.describe()
    payload["scientific_status"] = (
        "verified_equations_partial_inputs"
        if result.completeness != "COMPLETE"
        else "verified_equations"
    )
    # BSI does not own the ORCA GO/CAUTION/NO-GO verdict.
    payload["verdict"] = None
    payload["notes"] = (
        "INCOIS SVAS Boat Safety Index (0–7). Hazard semantics from Aditya et al.: "
        "BSI=0 safe for the three criteria; BSI>0 dangerous. ORCA does not map "
        "BSI onto GO/CAUTION/NO-GO; the legacy rule engine retains that verdict."
    )
    return payload


def build_environmental_assessments(
    *,
    legacy: dict[str, Any],
    bsi: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Ordered list of environmental models for API / UI consumption."""
    models = [legacy]
    if bsi is not None:
        models.append(bsi)
    return models

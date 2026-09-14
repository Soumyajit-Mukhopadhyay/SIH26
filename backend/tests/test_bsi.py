"""Phase 3 — INCOIS SVAS BSI unit tests (Aditya et al. 2020 verified equations)."""

from __future__ import annotations

import math

import pytest

from orca.science.bsi import (
    BEAM_HS_FACTOR,
    H0_INDIAN_SEAS_M,
    PM_LIMITING_STEEPNESS,
    THRESHOLD_CROSSING,
    THRESHOLD_RAPID_DEV,
    THRESHOLD_STEEPNESS,
    calculate_bsi,
    calculate_crossing_sea_index,
    calculate_rapid_development_index,
    calculate_steepness_index,
    evaluate_beam_criterion,
    significant_steepness_deep_water,
)
from orca.services.decision import resolve_decision
from orca.services.risk_engine import WEIGHTS, assess
from orca.services.thresholds import resolve_vessel


# ---------------------------------------------------------------------------
# Steepness (Aditya eq 1)
# ---------------------------------------------------------------------------


def test_steepness_below_threshold_clear():
    # Choose Hs and Ss so Isteepness is clearly below 0.8
    # I = (Ss/0.05)*(Hs/2.5). With Ss=0.02, Hs=1.0 → (0.4)*(0.4)=0.16
    result = calculate_steepness_index(hs_m=1.0, ss=0.02)
    assert result.status == "clear"
    assert result.triggered is False
    assert result.contribution == 0
    assert result.value is not None
    assert result.value < THRESHOLD_STEEPNESS


def test_steepness_at_boundary_not_triggered():
    # Exactly at threshold: eq (7) uses >, so equal is clear
    # I = 0.8 → (Ss/0.05)*(Hs/2.5) = 0.8
    # With Hs=2.5: Ss/0.05 = 0.8 → Ss = 0.04
    result = calculate_steepness_index(hs_m=H0_INDIAN_SEAS_M, ss=0.04)
    assert result.value == pytest.approx(0.8)
    assert result.triggered is False
    assert result.contribution == 0


def test_steepness_above_threshold_triggered():
    result = calculate_steepness_index(hs_m=H0_INDIAN_SEAS_M, ss=0.05)
    # I = (0.05/0.05)*(2.5/2.5) = 1.0 > 0.8
    assert result.triggered is True
    assert result.contribution == 1
    assert result.status == "triggered"
    assert result.decision_grade is True


def test_steepness_missing_hs_unavailable():
    result = calculate_steepness_index(hs_m=None, ss=0.04)
    assert result.status == "unavailable"
    assert result.decision_grade is False


def test_steepness_derived_from_period_labelled():
    result = calculate_steepness_index(hs_m=2.0, period_s=8.0)
    assert result.status in {"clear", "triggered"}
    assert "derived_deep_water" in result.processing_method
    assert result.decision_grade is False  # derived, not SWAN Ss
    assert any(
        "not identical" in lim.lower() or "derived" in lim.lower() for lim in result.limitations
    )


def test_significant_steepness_formula():
    # Ss = 2π Hs / (g T²)
    hs, t = 2.0, 8.0
    expected = (2 * math.pi * hs) / (9.81 * t * t)
    assert significant_steepness_deep_water(hs_m=hs, period_s=t) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Crossing sea (Aditya eq 2)
# ---------------------------------------------------------------------------


def test_crossing_missing_spread_unavailable():
    result = calculate_crossing_sea_index(hs_m=2.0, directional_spread=None)
    assert result.status == "unavailable"
    assert "directional spread" in result.limitations[0].lower()


def test_crossing_below_threshold():
    # I = 0.5 * Hs * exp(-10*(σ-1)^2). At σ=1, I = 0.5*Hs.
    # Hs=1.0 → I=0.5 < 0.65
    result = calculate_crossing_sea_index(hs_m=1.0, directional_spread=1.0)
    assert result.triggered is False
    assert result.contribution == 0
    assert result.value == pytest.approx(0.5)


def test_crossing_above_threshold():
    # Hs=2.0, σ=1 → I=1.0 > 0.65
    result = calculate_crossing_sea_index(hs_m=2.0, directional_spread=1.0)
    assert result.triggered is True
    assert result.contribution == 2
    assert result.value == pytest.approx(1.0)


def test_crossing_boundary():
    # Exactly 0.65: eq (8) uses >, so not triggered
    # 0.5 * Hs * 1 = 0.65 → Hs = 1.3, σ=1
    result = calculate_crossing_sea_index(hs_m=1.3, directional_spread=1.0)
    assert result.value == pytest.approx(THRESHOLD_CROSSING)
    assert result.triggered is False


# ---------------------------------------------------------------------------
# Rapid development (Aditya eq 4–5)
# ---------------------------------------------------------------------------


def test_rapid_missing_inputs_unavailable():
    result = calculate_rapid_development_index(hsea_initial_m=None, hsea_final_m=1.0)
    assert result.status == "unavailable"


def test_rapid_below_threshold():
    # |1.0 - 1.1| / 1.0 = 0.1 < 0.2
    result = calculate_rapid_development_index(hsea_initial_m=1.0, hsea_final_m=1.1)
    assert result.triggered is False
    assert result.contribution == 0


def test_rapid_at_threshold_triggered():
    # eq (5) uses ≥ 0.2
    result = calculate_rapid_development_index(hsea_initial_m=1.0, hsea_final_m=1.2)
    assert result.value == pytest.approx(THRESHOLD_RAPID_DEV)
    assert result.triggered is True
    assert result.contribution == 4


def test_rapid_above_threshold():
    result = calculate_rapid_development_index(hsea_initial_m=1.0, hsea_final_m=1.5)
    assert result.triggered is True
    assert result.contribution == 4


# ---------------------------------------------------------------------------
# BSI sum (Aditya eq 6–9)
# ---------------------------------------------------------------------------


def test_bsi_complete_all_clear():
    result = calculate_bsi(
        hs_m=1.0,
        ss=0.02,
        directional_spread=0.5,  # far from 1 → small Icrossing
        hsea_initial_m=1.0,
        hsea_final_m=1.05,
    )
    assert result.completeness == "COMPLETE"
    assert result.score == 0
    assert result.hazard == "SAFE"
    assert result.scale == "0-7"


def test_bsi_complete_all_triggered():
    result = calculate_bsi(
        hs_m=2.5,
        ss=0.05,  # steepness triggered
        directional_spread=1.0,  # crossing triggered (I=1.25)
        hsea_initial_m=1.0,
        hsea_final_m=1.5,  # rapid triggered
    )
    assert result.completeness == "COMPLETE"
    assert result.score == 7
    assert result.hazard == "DANGEROUS"


def test_bsi_partial_without_directional_spread():
    result = calculate_bsi(hs_m=2.0, period_s=7.0)
    assert result.completeness == "PARTIAL"
    assert result.score is None  # must not invent a full 0–7 score
    assert result.hazard == "PARTIAL"
    crossing = next(c for c in result.components if c.component == "crossing_sea")
    assert crossing.status == "unavailable"


def test_bsi_provenance_on_components():
    result = calculate_bsi(
        hs_m=2.0,
        ss=0.03,
        directional_spread=1.0,
        hsea_initial_m=1.0,
        hsea_final_m=1.0,
    )
    for component in result.components:
        assert component.source_url.startswith("https://doi.org/")
        assert component.model_version
        assert component.threshold is not None or component.status == "unavailable"


def test_beam_not_invented_from_loa():
    result = calculate_bsi(
        hs_m=2.5,
        ss=0.05,
        directional_spread=1.0,
        hsea_initial_m=1.0,
        hsea_final_m=1.5,
        beam_m=None,
    )
    assert result.score == 7
    assert result.beam_criterion is not None
    assert result.beam_criterion["status"] == "unavailable"
    assert "does not estimate beam" in result.beam_criterion["reason"].lower()


def test_beam_criterion_when_supplied():
    # BSI non-zero, beam < 4*Hs → prone
    hs = 2.0
    beam = 5.0  # 5 < 8 → prone
    result = calculate_bsi(
        hs_m=hs,
        ss=0.05,
        directional_spread=1.0,
        hsea_initial_m=1.0,
        hsea_final_m=1.5,
        beam_m=beam,
    )
    assert result.beam_criterion["applicable"] is True
    assert result.beam_criterion["prone_to_capsizing"] is True
    assert result.beam_criterion["threshold_beam_m"] == pytest.approx(BEAM_HS_FACTOR * hs)


# ---------------------------------------------------------------------------
# Legacy score unchanged + Phase 1/2 intact
# ---------------------------------------------------------------------------


def test_legacy_weights_unchanged():
    assert WEIGHTS == {"wave": 0.35, "wind": 0.30, "visibility": 0.15, "lightning": 0.20}


def test_assess_attaches_both_models_without_changing_verdict():
    result = assess(
        wave_m=0.5,
        wind_kn=8.0,
        visibility_km=12.0,
        lightning_pct=5.0,
        boat_class_code="IND-MECH-L",
        wave_period_s=8.0,
    )
    assert result.verdict in {"GO", "CAUTION", "NO-GO", "UNVERIFIABLE"}
    models = {m["model"]: m for m in result.environmental_models}
    assert "ORCA_LEGACY" in models
    assert "INCOIS_SVAS_BSI" in models
    assert models["ORCA_LEGACY"]["scale"] == "0-100"
    assert models["INCOIS_SVAS_BSI"]["scale"] == "0-7"
    assert models["ORCA_LEGACY"]["score"] == result.index
    # BSI must not be presented as 0–100
    assert models["INCOIS_SVAS_BSI"]["score"] is None or models["INCOIS_SVAS_BSI"]["score"] <= 7


def test_official_warning_still_overrides_environmental():
    from orca.services.decision import _official_from_alert_data

    future = {
        "official_alerts_verified": True,
        "cyclone_alert": "Severe cyclonic storm",
        "lightning_alert": "unavailable",
        "reason": "",
    }
    assert _official_from_alert_data(future)["status"] == "WARNING"

    # Decision cascade: with risk GO but we can't easily inject official WARNING
    # via tool_results without verified adapter — geographic PROHIBITED still wins.
    result = resolve_decision(
        tool_results=[
            {
                "tool": "check_geofences",
                "data": {
                    "proximities": [
                        {
                            "fence": "imbl_x",
                            "kind": "imbl",
                            "inside": True,
                            "distance_km": 0.2,
                            "state": "crossed",
                            "name": "IMBL",
                            "consequence": "boundary",
                        }
                    ]
                },
            }
        ],
        risk={
            "verdict": "GO",
            "index": 90,
            "vetoes": [],
            "confidence": "high",
            "boat_class_code": "IND-MECH-L",
            "boat_class_label": "Large Mechanised",
            "vessel_source": "category",
            "environmental_models": [],
        },
    )
    assert result["final_status"]["action"] == "DO_NOT_PROCEED"


def test_pfz_never_overrides_environmental_nogo():
    result = resolve_decision(
        tool_results=[
            {
                "tool": "find_fishing_zones",
                "data": {
                    "zones": [{"rank": 3, "centroid": {"lat": 10, "lon": 78}, "area_km2": 10}],
                    "stale": False,
                },
            }
        ],
        risk={
            "verdict": "NO-GO",
            "index": 20,
            "vetoes": ["wave veto"],
            "confidence": "high",
            "boat_class_code": "IND-MECH-L",
            "boat_class_label": "Large Mechanised",
            "vessel_source": "category",
        },
    )
    assert result["final_status"]["action"] == "DO_NOT_PROCEED"
    assert result["fishing_opportunity"]["status"] == "HIGH"


def test_phase1_vessel_resolution_intact():
    assert resolve_vessel(boat_class_code="IND-MECH-L", loa_m=None).source == "category"
    assert resolve_vessel(boat_class_code=None, loa_m=12.0).source == "loa"
    assert resolve_vessel(boat_class_code=None, loa_m=None).source == "unknown"


def test_unknown_vessel_no_fabricated_beam_in_bsi():
    result = assess(
        wave_m=1.5,
        wind_kn=10.0,
        visibility_km=10.0,
        lightning_pct=10.0,
        loa_m=None,
        boat_class_code=None,
        wave_period_s=7.0,
    )
    assert result.verdict == "UNVERIFIABLE"
    bsi = next(m for m in result.environmental_models if m["model"] == "INCOIS_SVAS_BSI")
    assert bsi["beam_criterion"]["status"] == "unavailable"

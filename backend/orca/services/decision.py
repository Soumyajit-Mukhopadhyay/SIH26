"""Deterministic decision orchestrator — Phase 2.

Separates six independent decision dimensions so that a high PFZ opportunity
cannot cancel a legal prohibition, a favourable sea state cannot cancel an
official warning, and UNKNOWN sources are never silently converted to
permission or safety.

Dimension priority (higher authority wins where conflict exists):
    1. Official emergency / warning
    2. Legal prohibition
    3. Geographic prohibition
    4. Environmental NO-GO
    5. Data insufficiency (UNVERIFIABLE)
    6. Environmental CAUTION
    7. Default PROCEED

The LLM explains the structured result; it does not determine it.
"""

from __future__ import annotations

from typing import Any, Literal

# ---------------------------------------------------------------------------
# Literal aliases — kept narrow so exhaustiveness is obvious in the cascade
# ---------------------------------------------------------------------------

OfficialStatusCode = Literal["NONE", "ADVISORY", "WARNING", "EMERGENCY", "UNKNOWN"]
LegalStatusCode = Literal["PERMITTED", "RESTRICTED", "PROHIBITED", "UNKNOWN"]
GeographicStatusCode = Literal["CLEAR", "RESTRICTED", "PROHIBITED", "UNKNOWN"]
FishingOpportunityCode = Literal["HIGH", "MODERATE", "LOW", "NONE", "UNKNOWN"]
DataConfidenceCode = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
FinalAction = Literal["PROCEED", "CAUTION", "DO_NOT_PROCEED", "UNVERIFIABLE"]


# ---------------------------------------------------------------------------
# Dimension builders — each reads only the one tool it owns
# ---------------------------------------------------------------------------


def _official_from_alert_data(alert_data: dict[str, Any] | None) -> dict[str, Any]:
    """Map ``check_marine_alerts`` tool data → ``official_status`` dict.

    Unverified feeds stay UNKNOWN — never a silent all-clear. When the IMD
    adapter marks ``official_alerts_verified=True``, cyclone / fishermen / port
    warnings are WARNING (or EMERGENCY for a severe cyclone) and lightning or
    coastal rainfall is ADVISORY.
    """
    if alert_data is None:
        return {
            "status": "UNKNOWN",
            "items": ["check_marine_alerts tool was not run in this request"],
        }

    if not alert_data.get("official_alerts_verified", False):
        reason = alert_data.get(
            "reason",
            "Official alert adapter is not operational in this deployment",
        )
        return {
            "status": "UNKNOWN",
            "items": [f"Official alert verification unavailable: {reason}"],
        }

    def _active(value: Any) -> bool:
        if value is None:
            return False
        return str(value).strip().lower() not in ("unavailable", "none", "false", "", "nil")

    cyclone = alert_data.get("cyclone_alert")
    lightning = alert_data.get("lightning_alert")
    fishermen = alert_data.get("fishermen_warning")
    port = alert_data.get("port_warning")
    sea = alert_data.get("sea_area_warning")
    coastal = alert_data.get("coastal_warning")
    rainfall = alert_data.get("rainfall_advisory")
    items: list[str] = []
    severity: OfficialStatusCode = "NONE"

    if _active(cyclone):
        items.append(f"Cyclone alert: {cyclone}")
        blob = str(cyclone).lower()
        severity = (
            "EMERGENCY"
            if any(token in blob for token in ("very severe", "super cyclone", "extremely severe"))
            else "WARNING"
        )
    if _active(fishermen):
        items.append(f"Fishermen warning: {fishermen}")
        if severity not in ("WARNING", "EMERGENCY"):
            severity = "WARNING"
    if _active(port):
        items.append(f"Port warning: {port}")
        if severity not in ("WARNING", "EMERGENCY"):
            severity = "WARNING"
    if _active(sea):
        items.append(f"Sea-area bulletin: {sea}")
        if severity not in ("WARNING", "EMERGENCY"):
            severity = "WARNING"
    if _active(coastal):
        items.append(f"Coastal bulletin: {coastal}")
        if severity not in ("WARNING", "EMERGENCY"):
            severity = "WARNING"
    if _active(lightning):
        items.append(f"Lightning alert: {lightning}")
        if severity not in ("WARNING", "EMERGENCY"):
            severity = "ADVISORY"
    if _active(rainfall):
        items.append(f"Coastal rainfall advisory: {rainfall}")
        if severity not in ("WARNING", "EMERGENCY", "ADVISORY"):
            severity = "ADVISORY"

    return {"status": severity, "items": items}


def _legal_from_tool_data() -> dict[str, Any]:
    """Legal / regulatory status.

    No verified legal-rule adapter exists in Phase 2.  Absence of a discovered
    rule is NOT evidence of permission — the status is explicitly UNKNOWN.
    """
    return {
        "status": "UNKNOWN",
        "items": [
            "No verified legal-rule source is connected. "
            "Fishing-ban, MPA, and government-notice ingestion are not implemented in this phase. "
            "Absence of a discovered rule does not mean fishing is legally permitted."
        ],
    }


def _geographic_from_geofence_data(geofence_data: dict[str, Any] | None) -> dict[str, Any]:
    """Map ``check_geofences`` tool data → ``geographic_status`` dict.

    Important: geographic status reflects whether a boundary is crossed or
    close — it does NOT automatically imply a legal prohibition unless the
    boundary explicitly carries that meaning (e.g. IMBL crossing).
    """
    if geofence_data is None:
        return {
            "status": "UNKNOWN",
            "items": ["check_geofences tool was not run in this request"],
        }

    proximities: list[dict[str, Any]] = geofence_data.get("proximities") or []
    items: list[str] = []
    most_severe: GeographicStatusCode = "CLEAR"

    for prox in proximities:
        fence: str = prox.get("fence", "")
        kind: str = prox.get("kind", "")
        inside: bool = bool(prox.get("inside", False))
        distance_km: float = float(prox.get("distance_km") or 9999.0)
        state: str = prox.get("state", "")
        name: str = prox.get("name", fence)
        consequence: str = prox.get("consequence", "")

        if kind == "eez" and fence == "eez_india":
            if inside:
                items.append(f"Inside India's EEZ ({name})")
            else:
                items.append(
                    f"Outside India's EEZ ({name}): "
                    + (consequence or "confirm applicable jurisdiction")
                )
                if most_severe == "CLEAR":
                    most_severe = "RESTRICTED"

        elif kind == "imbl":
            if state in ("crossed", "inside") or distance_km <= 2.0:
                items.append(
                    f"At or crossed {name} (IMBL): "
                    + (consequence or "international maritime boundary")
                )
                most_severe = "PROHIBITED"
            elif distance_km <= 25.0:
                items.append(
                    f"{distance_km:.1f} km from {name} (IMBL): boundary proximity"
                )
                if most_severe != "PROHIBITED":
                    most_severe = "RESTRICTED"

        else:
            # Other configured exclusion zones
            if state in ("crossed", "inside"):
                items.append(
                    f"Inside/crossed {name}: " + (consequence or "restricted zone")
                )
                most_severe = "PROHIBITED"

    if not items:
        items.append("No boundary violations or proximity restrictions detected")

    return {"status": most_severe, "items": items}


def _fishing_from_zone_data(
    find_data: dict[str, Any] | None,
    screen_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map PFZ tool data → ``fishing_opportunity`` dict.

    PFZ status reflects fish-aggregation opportunity only.  It carries no
    safety or legal semantics — HIGH opportunity never grants permission.
    """
    if find_data is None and screen_data is None:
        return {"status": "UNKNOWN", "zones": [], "stale": False}

    # Prefer screened data (richer) over raw find data
    if screen_data is not None:
        zones: list[dict[str, Any]] = list(screen_data.get("screened_zones") or [])
        stale: bool = bool(screen_data.get("pfz_stale", False))
    else:
        zones = list((find_data or {}).get("zones") or [])  # type: ignore[arg-type]
        stale = bool((find_data or {}).get("stale", False))

    if not zones:
        return {"status": "NONE", "zones": [], "stale": stale}

    ranks = [int(z.get("rank") or 0) for z in zones]
    max_rank = max(ranks) if ranks else 0

    if max_rank >= 3:
        status: FishingOpportunityCode = "HIGH"
    elif max_rank == 2:
        status = "MODERATE"
    elif max_rank == 1:
        status = "LOW"
    else:
        status = "NONE"

    zone_summaries = [
        {
            "rank": z.get("rank"),
            "action": z.get("action"),
            "inside_india_eez": z.get("inside_india_eez"),
            "reasons": list(z.get("reasons") or [])[:3],
        }
        for z in zones[:3]
    ]

    return {"status": status, "zones": zone_summaries, "stale": stale}


def _data_confidence(
    risk: dict[str, Any] | None,
    official_status_code: str,
    legal_status_code: str,
    vessel_source: str | None,
) -> dict[str, Any]:
    """Aggregate data confidence across all dimensions."""
    limitations: list[str] = []

    if official_status_code == "UNKNOWN":
        limitations.append("Official marine alert source unavailable")

    if legal_status_code == "UNKNOWN":
        limitations.append(
            "No verified legal-rule source connected; legal status is indeterminate"
        )

    if vessel_source in ("unknown", None):
        limitations.append(
            "Vessel type unknown — environmental assessment is vessel-generic only"
        )

    if risk is None:
        limitations.append("Environmental risk assessment was not performed in this request")
        return {"confidence": "LOW", "limitations": limitations}

    risk_confidence = risk.get("confidence", "high")
    if risk_confidence == "low":
        msg = risk.get("escalation_message") or "see vetoes"
        limitations.append(f"Risk engine confidence is low: {msg}")

    if not limitations:
        confidence: DataConfidenceCode = "HIGH"
    elif len(limitations) <= 1:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {"confidence": confidence, "limitations": limitations}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_decision(
    tool_results: list[dict[str, Any]],
    risk: dict[str, Any] | None,
    boat_class_code: str | None = None,
    loa_m: float | None = None,
) -> dict[str, Any]:
    """Deterministic decision orchestrator.

    Assembles six independent decision dimensions from tool results and the
    risk engine output, then resolves a final action using strict priority
    ordering.

    Parameters
    ----------
    tool_results:
        The ``tool_results`` list from ``OrcaState`` — each entry has a
        ``"tool"`` key and a ``"data"`` dict.
    risk:
        The risk engine result dict (``RiskResult.model_dump``), or ``None``
        if no safety assessment ran.
    boat_class_code:
        Optional explicit vessel category (Phase 1 vessel context).
    loa_m:
        Optional vessel LOA in metres (Phase 1 vessel context).

    Returns
    -------
    A plain dict matching the Phase 2 decision schema — JSON-serialisable,
    storable directly in ``OrcaState.structured_decision``.
    """
    by_tool: dict[str, dict[str, Any]] = {
        r.get("tool", ""): (r.get("data") or {}) for r in tool_results
    }

    vessel_source: str | None = (risk or {}).get("vessel_source")

    # ------------------------------------------------------------------ #
    # Build each dimension independently
    # ------------------------------------------------------------------ #
    official_status = _official_from_alert_data(by_tool.get("check_marine_alerts"))
    legal_status = _legal_from_tool_data()
    geographic_status = _geographic_from_geofence_data(by_tool.get("check_geofences"))

    if risk is not None:
        environmental_status: dict[str, Any] = {
            "verdict": risk["verdict"],
            "risk": {
                "index": risk.get("index"),
                "vetoes": risk.get("vetoes", []),
                "confidence": risk.get("confidence"),
                "boat_class_code": risk.get("boat_class_code"),
                "boat_class_label": risk.get("boat_class_label"),
                "vessel_source": risk.get("vessel_source"),
                # Phase 3 — primary display model remains ORCA_LEGACY for the
                # GO/CAUTION/NO-GO interface; BSI is a parallel hazard indicator.
                "model": "ORCA_LEGACY",
                "score": risk.get("index"),
                "scale": "0-100",
                "environmental_models": risk.get("environmental_models") or [],
            },
        }
    else:
        environmental_status = {"verdict": "UNVERIFIABLE", "risk": {}}

    fishing_opportunity = _fishing_from_zone_data(
        by_tool.get("find_fishing_zones"),
        by_tool.get("screen_fishing_zones"),
    )

    data_status = _data_confidence(
        risk,
        official_status["status"],
        legal_status["status"],
        vessel_source,
    )

    # ------------------------------------------------------------------ #
    # Deterministic cascade — highest-authority constraint wins
    # ------------------------------------------------------------------ #
    reason_codes: list[str] = []
    official_code: str = official_status["status"]
    legal_code: str = legal_status["status"]
    geo_code: str = geographic_status["status"]
    env_verdict: str = environmental_status["verdict"]

    if official_code in ("WARNING", "EMERGENCY"):
        action: FinalAction = "DO_NOT_PROCEED"
        reason_codes.append(f"OFFICIAL_WARNING:{official_code}")
    elif legal_code == "PROHIBITED":
        action = "DO_NOT_PROCEED"
        reason_codes.append("LEGAL_PROHIBITED")
    elif geo_code == "PROHIBITED":
        action = "DO_NOT_PROCEED"
        reason_codes.append("GEOGRAPHIC_PROHIBITED")
    elif env_verdict == "NO-GO":
        action = "DO_NOT_PROCEED"
        reason_codes.append("ENVIRONMENTAL_NO_GO")
    elif env_verdict == "UNVERIFIABLE":
        action = "UNVERIFIABLE"
        reason_codes.append("ENVIRONMENTAL_UNVERIFIABLE")
    elif env_verdict == "CAUTION":
        action = "CAUTION"
        reason_codes.append("ENVIRONMENTAL_CAUTION")
    else:
        action = "PROCEED"
        reason_codes.append("NO_BLOCKING_CONDITION")

    # Preserve data-gap codes even on PROCEED so the LLM knows what is unknown
    if action in ("PROCEED", "CAUTION", "UNVERIFIABLE"):
        if official_code == "UNKNOWN":
            reason_codes.append("OFFICIAL_STATUS_UNKNOWN")
        if legal_code == "UNKNOWN":
            reason_codes.append("LEGAL_STATUS_UNKNOWN")

    final_status: dict[str, Any] = {"action": action, "reason_codes": reason_codes}

    return {
        "official_status": official_status,
        "legal_status": legal_status,
        "geographic_status": geographic_status,
        "environmental_status": environmental_status,
        "data_status": data_status,
        "fishing_opportunity": fishing_opportunity,
        "final_status": final_status,
    }

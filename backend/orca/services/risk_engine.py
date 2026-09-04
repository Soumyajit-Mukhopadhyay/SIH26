"""The deterministic safety core. No LLM below this line, ever.

Three rules, non-negotiable, and all three are enforced by the code rather than
by convention:

1. **The rule engine owns the verdict.** :func:`assess` is a pure function of
   forecast variables and boat class. ``RiskResult.verdict_source`` is typed
   ``Literal["rule_engine"]``, so ``"llm"`` is unrepresentable — a language model
   cannot produce a value of this type.
2. **The LLM may only explain.** It never issues or softens a verdict, and the
   critic node re-reads its draft against the fields here.
3. **Low confidence escalates; it never guesses harder.** Past the data-age
   limit, or with too little decision-grade evidence, the answer becomes "I
   cannot verify this — confirm with your fisheries office or VHF" rather than a
   more confident-sounding guess.

``orca/services/`` imports nothing from ``orca/agents/``. There is a test
asserting it, because the architecture diagram is worth nothing if an import
quietly crosses the line.

The veto list is the most valuable string in the system. "Hs 2.4 m at or over the
1.5 m limit for your 8.2 m boat" teaches the user something real, and shows a
jury that the reasoning is a computation rather than a generation.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from orca.provenance import DECISION_GRADE, Citation, Evidence, Provenance, utcnow
from orca.services.thresholds import (
    CAPE_BAND_ELEVATED_MAX,
    CAPE_BAND_HIGH_MAX,
    CAPE_BAND_LOW_MAX,
    CAPE_BAND_MODERATE_MAX,
    CONFIDENCE_AGE_LIMIT_H,
    THRESHOLDS_VERSION,
    BoatClass,
    resolve_vessel,
)

log = logging.getLogger(__name__)

Verdict = Literal["GO", "CAUTION", "NO-GO", "UNVERIFIABLE"]

#: Component weights. Wave dominates because it is what capsizes small craft.
WEIGHTS = {"wave": 0.35, "wind": 0.30, "visibility": 0.15, "lightning": 0.20}

GO_THRESHOLD = 70.0
CAUTION_THRESHOLD = 40.0


def _margin(delta: float, unit: str = "kn") -> str:
    """A margin, phrased so a tiny overshoot does not round to "a drop of 0".

    22.3 kn against a 22 kn limit is over the line by 0.3, and rendering that as
    "0 kn" makes a correct veto look like a bug to the person reading it.
    """
    if delta < 0.05:
        return f"barely — it is only just over the limit ({delta:.2f} {unit})"
    if delta < 1:
        return f"{delta:.1f} {unit}"
    return f"{delta:.0f} {unit}" if unit == "kn" else f"{delta:.1f} {unit}"


def _num(value: float) -> str:
    """Format a number the way a mariner writes it: 22 kn, not 22.0 kn; 1.5 m,
    not 2 m. These strings go straight into a spoken advisory, so trailing
    ".0" is noise a fisherman has to parse past."""
    return f"{value:g}"


class Component(BaseModel):
    """One scored input, with the arithmetic left visible.

    ``score`` is 0-100 where 100 is safest. ``formula`` is carried so the UI can
    show the actual computation — the difference between a number and an
    explanation.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    value: float | None
    unit: str
    limit: float
    score: float
    weight: float
    contribution: float
    formula: str
    exceeded: bool
    #: Qualitative CAPE band when ``name == "lightning"`` and CAPE was assessed.
    band: str | None = None
    provenance: Provenance | None = None
    age_hours: float | None = None


class RiskResult(BaseModel):
    """The verdict, and everything needed to defend or dispute it."""

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    #: Typed so that "the LLM decided" is not a representable state.
    verdict_source: Literal["rule_engine"] = "rule_engine"
    index: float = Field(ge=0, le=100, description="0-100, higher is safer.")
    vetoes: list[str]
    components: list[Component]
    boat_class_code: str
    boat_class_label: str
    loa_m: float | None = None
    confidence: Literal["high", "low"]
    escalate: bool
    escalation_message: str | None = None
    data_age_hours: float
    thresholds_version: str = THRESHOLDS_VERSION
    evaluated_at: str
    evidence: list[Evidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    #: Plain-language statement of what would have to change for a GO. This is
    #: what a fisherman actually wants and what nobody else's demo answers.
    what_would_change_it: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "ORCA supplements, never replaces, official IMD and INCOIS bulletins."
    )
    #: How the vessel was identified: category | loa | unknown
    vessel_source: Literal["category", "loa", "unknown"] | None = None
    #: Phase 3 — parallel environmental models (ORCA_LEGACY + optional BSI).
    #: Never collapse these into one weighted number.
    environmental_models: list[dict] = Field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return self.verdict == "GO"


def _score_wave(wave_m: float, limit: float) -> tuple[float, str]:
    """Quadratic, because the risk to a small hull grows faster than linearly
    with wave height — a 2 m sea is far more than twice as dangerous as a 1 m sea
    for an 8 m boat."""
    score = max(0.0, 100 * (1 - (wave_m / limit) ** 2))
    return score, f"max(0, 100 x (1 - ({wave_m} / {limit})^2)) = {score:.1f}"


def _score_wind(wind_kn: float, limit: float) -> tuple[float, str]:
    score = max(0.0, 100 * (1 - (wind_kn / limit) ** 2))
    return score, f"max(0, 100 x (1 - ({wind_kn} / {limit})^2)) = {score:.1f}"


def _score_visibility(visibility_km: float) -> tuple[float, str]:
    """Linear to 10 km, then capped. Beyond 10 km, more visibility does not make
    a trip meaningfully safer."""
    score = min(100.0, visibility_km / 10 * 100)
    return score, f"min(100, {visibility_km} / 10 x 100) = {score:.1f}"


CapeBand = Literal["LOW", "MODERATE", "ELEVATED", "HIGH", "VERY_HIGH"]

#: Graded ORCA_LEGACY contribution from CAPE instability bands. Softer than
#: wave/wind: CAPE never alone forces a hard veto.
_CAPE_BAND_SCORES: dict[CapeBand, float] = {
    "LOW": 100.0,
    "MODERATE": 75.0,
    "ELEVATED": 55.0,
    "HIGH": 35.0,
    "VERY_HIGH": 15.0,
}


def classify_cape(cape_j_kg: float) -> CapeBand:
    """Map CAPE (J/kg) to a qualitative atmospheric-instability band.

    Bands are ORCA product labels informed by conventional instability language
    (including NOAA-style moderate ~1000–2500 / strong >2500). They are **not**
    lightning probability, detected strikes, or official marine limits.
    """
    if cape_j_kg < CAPE_BAND_LOW_MAX:
        return "LOW"
    if cape_j_kg < CAPE_BAND_MODERATE_MAX:
        return "MODERATE"
    if cape_j_kg < CAPE_BAND_ELEVATED_MAX:
        return "ELEVATED"
    if cape_j_kg <= CAPE_BAND_HIGH_MAX:
        return "HIGH"
    return "VERY_HIGH"


def _score_cape(band: CapeBand) -> tuple[float, str]:
    score = _CAPE_BAND_SCORES[band]
    return score, f"CAPE band {band} → score {score:.0f}/100 (instability indicator, not a veto)"


def _score_lightning_legacy_pct(lightning_pct: float) -> tuple[float, str]:
    """Legacy soft score when callers supply a percentage without CAPE.

    Retained for older tests/fixtures. Does **not** create a hard veto.
    """
    score = max(0.0, 100 - lightning_pct)
    return score, f"max(0, 100 - {lightning_pct}) = {score:.1f} (legacy pct; no CAPE veto)"


def cape_to_lightning_pct(cape_j_kg: float) -> float:
    """Deprecated compatibility shim.

    Historically mapped CAPE onto a 0–100 "convective-risk proxy" used for a
    hard veto at 60%. Prefer :func:`classify_cape`. This function no longer
    drives any veto path.
    """
    band = classify_cape(cape_j_kg)
    # Invert band scores so callers expecting a rising "risk %" still get a
    # monotonic signal; 0 ≈ calm, 100 ≈ very high instability.
    return round(100.0 - _CAPE_BAND_SCORES[band], 1)


def unknown_vessel_result(
    *,
    evidence: list[Evidence] | None = None,
    data_age_hours: float = 0.0,
    loa_m: float | None = None,
) -> RiskResult:
    """Honest refusal when neither category nor LOA was supplied.

    Must not invent IND-MOT-S / 8.2 m. Environmental Evidence may still be
    attached so the UI can show conditions without a vessel-specific verdict.
    """
    evidence = evidence or []
    return RiskResult(
        verdict="UNVERIFIABLE",
        index=0.0,
        vetoes=[],
        components=[],
        boat_class_code="UNKNOWN",
        boat_class_label="Vessel type not specified",
        loa_m=loa_m,
        confidence="low",
        escalate=True,
        escalation_message=(
            "ORCA cannot issue a vessel-specific safety verdict without knowing the boat type. "
            "Select a vessel category (or optionally enter length overall), then ask again."
        ),
        data_age_hours=round(data_age_hours, 2),
        evaluated_at=utcnow().isoformat(),
        evidence=evidence,
        citations=[],
        what_would_change_it=[
            "select a vessel category that matches your boat, or enter an approximate length overall"
        ],
        vessel_source="unknown",
    )


def _attach_environmental_models(
    result: RiskResult,
    *,
    wave_m: float | None,
    period_s: float | None = None,
    directional_spread: float | None = None,
    hsea_initial_m: float | None = None,
    hsea_final_m: float | None = None,
    beam_m: float | None = None,
) -> RiskResult:
    """Attach ORCA_LEGACY + INCOIS_SVAS_BSI without changing the legacy verdict."""
    from orca.services.environmental_assessment import (
        bsi_assessment_from_inputs,
        build_environmental_assessments,
        legacy_assessment,
    )

    legacy = legacy_assessment(
        index=result.index,
        verdict=result.verdict,
        components=[c.model_dump(mode="json") for c in result.components],
        thresholds_version=result.thresholds_version,
        vetoes=list(result.vetoes),
    )
    bsi = bsi_assessment_from_inputs(
        hs_m=wave_m,
        period_s=period_s,
        directional_spread=directional_spread,
        hsea_initial_m=hsea_initial_m,
        hsea_final_m=hsea_final_m,
        beam_m=beam_m,
    )
    models = build_environmental_assessments(legacy=legacy, bsi=bsi)
    return result.model_copy(update={"environmental_models": models})


def assess(
    *,
    wave_m: float | None,
    wind_kn: float | None,
    visibility_km: float | None,
    lightning_pct: float | None = None,
    cape_j_kg: float | None = None,
    loa_m: float | None = None,
    boat_class: BoatClass | None = None,
    boat_class_code: str | None = None,
    data_age_hours: float = 0.0,
    evidence: list[Evidence] | None = None,
    wave_period_s: float | None = None,
    directional_spread: float | None = None,
    hsea_initial_m: float | None = None,
    hsea_final_m: float | None = None,
    beam_m: float | None = None,
) -> RiskResult:
    """Deterministic GO / CAUTION / NO-GO. Pure function. No I/O, no LLM.

    Vessel resolution (Phase 1): explicit ``boat_class`` / ``boat_class_code``
    wins; else ``loa_m`` via :func:`classify`; else UNKNOWN → UNVERIFIABLE.
    There is no silent 8.2 m / IND-MOT-S default.

    CAPE (``cape_j_kg``) is preferred for the convective component: it is scored
    as a qualitative instability band and never alone produces a hard veto.
    ``lightning_pct`` remains a legacy soft-score input when CAPE is absent.

    A missing input does not silently score zero. It scores zero *and* forces low
    confidence, because "we could not measure the waves" and "the waves are
    calm" must never produce the same answer.
    """
    evidence = evidence or []
    vessel_source: Literal["category", "loa", "unknown"] = "unknown"

    if boat_class is not None:
        boat = boat_class
        vessel_source = "category"
    else:
        resolved = resolve_vessel(boat_class_code=boat_class_code, loa_m=loa_m)
        if resolved.error or resolved.boat is None:
            # Vessel-specific ORCA verdict is UNVERIFIABLE, but the environmental
            # BSI hazard indicator (without beam) may still be computed.
            unknown = unknown_vessel_result(
                evidence=evidence, data_age_hours=data_age_hours, loa_m=loa_m
            )
            return _attach_environmental_models(
                unknown,
                wave_m=wave_m,
                period_s=wave_period_s,
                directional_spread=directional_spread,
                hsea_initial_m=hsea_initial_m,
                hsea_final_m=hsea_final_m,
                beam_m=beam_m,
            )
        boat = resolved.boat
        vessel_source = resolved.source  # type: ignore[assignment]
        if resolved.loa_m is not None:
            loa_m = resolved.loa_m

    cape_band: CapeBand | None = None
    if cape_j_kg is not None:
        cape_band = classify_cape(cape_j_kg)

    missing: list[str] = []
    if wave_m is None:
        missing.append("significant wave height")
    if wind_kn is None:
        missing.append("wind speed")
    if visibility_km is None:
        missing.append("visibility")
    if cape_j_kg is None and lightning_pct is None:
        missing.append("convective energy (CAPE)")

    # Absent inputs score 0 (the cautious direction) and are marked as absent so
    # the UI can distinguish "dangerous" from "unknown".
    wave_score, wave_formula = (
        _score_wave(wave_m, boat.max_wave_m) if wave_m is not None else (0.0, "no data")
    )
    wind_score, wind_formula = (
        _score_wind(wind_kn, boat.max_wind_kn) if wind_kn is not None else (0.0, "no data")
    )
    vis_score, vis_formula = (
        _score_visibility(visibility_km) if visibility_km is not None else (0.0, "no data")
    )
    if cape_band is not None:
        light_score, light_formula = _score_cape(cape_band)
    elif lightning_pct is not None:
        light_score, light_formula = _score_lightning_legacy_pct(lightning_pct)
    else:
        light_score, light_formula = 0.0, "no data"

    index = round(
        WEIGHTS["wave"] * wave_score
        + WEIGHTS["wind"] * wind_score
        + WEIGHTS["visibility"] * vis_score
        + WEIGHTS["lightning"] * light_score,
        1,
    )

    vessel_phrase = (
        f"{_num(loa_m)} m boat ({boat.label})" if loa_m is not None else f"{boat.label}"
    )

    # ---- hard vetoes override the blended score entirely ----
    # Wave / wind / visibility only. CAPE is graded into the index; it is not a
    # marine hard limit and must not alone force NO-GO.
    vetoes: list[str] = []
    if wave_m is not None and wave_m >= boat.max_wave_m:
        vetoes.append(
            f"Hs {_num(wave_m)} m is at or over the {_num(boat.max_wave_m)} m limit for your "
            f"{vessel_phrase}"
        )
    if wind_kn is not None and wind_kn >= boat.max_wind_kn:
        vetoes.append(
            f"wind {_num(wind_kn)} kn is at or over the {_num(boat.max_wind_kn)} kn limit "
            f"for your {vessel_phrase}"
        )
    if visibility_km is not None and visibility_km < boat.min_visibility_km:
        vetoes.append(
            f"visibility {_num(visibility_km)} km is below the "
            f"{_num(boat.min_visibility_km)} km minimum for safe navigation in this class"
        )

    if cape_j_kg is not None and cape_band is not None:
        convective_value: float | None = cape_j_kg
        convective_unit = "J/kg"
        convective_limit = 0.0
        convective_exceeded = False
    elif lightning_pct is not None:
        convective_value = lightning_pct
        convective_unit = "%"
        convective_limit = 0.0
        convective_exceeded = False
    else:
        convective_value = None
        convective_unit = "J/kg"
        convective_limit = 0.0
        convective_exceeded = False

    components = [
        Component(
            name="wave",
            value=wave_m,
            unit="m",
            limit=boat.max_wave_m,
            score=round(wave_score, 1),
            weight=WEIGHTS["wave"],
            contribution=round(WEIGHTS["wave"] * wave_score, 1),
            formula=wave_formula,
            exceeded=wave_m is not None and wave_m >= boat.max_wave_m,
        ),
        Component(
            name="wind",
            value=wind_kn,
            unit="kn",
            limit=boat.max_wind_kn,
            score=round(wind_score, 1),
            weight=WEIGHTS["wind"],
            contribution=round(WEIGHTS["wind"] * wind_score, 1),
            formula=wind_formula,
            exceeded=wind_kn is not None and wind_kn >= boat.max_wind_kn,
        ),
        Component(
            name="visibility",
            value=visibility_km,
            unit="km",
            limit=boat.min_visibility_km,
            score=round(vis_score, 1),
            weight=WEIGHTS["visibility"],
            contribution=round(WEIGHTS["visibility"] * vis_score, 1),
            formula=vis_formula,
            exceeded=visibility_km is not None and visibility_km < boat.min_visibility_km,
        ),
        Component(
            name="lightning",
            value=convective_value,
            unit=convective_unit,
            limit=convective_limit,
            score=round(light_score, 1),
            weight=WEIGHTS["lightning"],
            contribution=round(WEIGHTS["lightning"] * light_score, 1),
            formula=light_formula,
            exceeded=convective_exceeded,
            band=cape_band,
        ),
    ]

    # ---- confidence, and the refusal to guess harder ----
    decision_grade = [e for e in evidence if e.provenance in DECISION_GRADE and e.value is not None]
    simulated = [e for e in evidence if e.provenance is Provenance.SIMULATED]

    confidence: Literal["high", "low"] = "high"
    escalation_reasons: list[str] = []
    if data_age_hours > CONFIDENCE_AGE_LIMIT_H:
        confidence = "low"
        escalation_reasons.append(
            f"the forecast data is {data_age_hours:.1f} h old, past the "
            f"{CONFIDENCE_AGE_LIMIT_H:.0f} h limit"
        )
    if missing:
        confidence = "low"
        escalation_reasons.append(f"missing input(s): {', '.join(missing)}")
    if simulated:
        confidence = "low"
        escalation_reasons.append(
            f"{len(simulated)} input(s) are SIMULATED and cannot support a safety verdict"
        )
    if evidence and not decision_grade:
        confidence = "low"
        escalation_reasons.append("no decision-grade evidence was available")

    # ---- verdict ----
    if vetoes:
        verdict: Verdict = "NO-GO"
    elif missing or (confidence == "low" and not evidence):
        # Cannot verify. Not the same as safe, and not the same as dangerous.
        verdict = "UNVERIFIABLE"
    elif index >= GO_THRESHOLD:
        verdict = "GO"
    elif index >= CAUTION_THRESHOLD:
        verdict = "CAUTION"
    else:
        verdict = "NO-GO"

    escalate = confidence == "low"
    escalation_message = None
    if escalate:
        escalation_message = (
            "ORCA cannot verify this with confidence — "
            + "; ".join(escalation_reasons)
            + ". Confirm with your fisheries office or the coastal VHF channel before sailing."
        )

    return _attach_environmental_models(
        RiskResult(
            verdict=verdict,
            index=index,
            vetoes=vetoes,
            components=components,
            boat_class_code=boat.code,
            boat_class_label=boat.label,
            loa_m=loa_m,
            confidence=confidence,
            escalate=escalate,
            escalation_message=escalation_message,
            data_age_hours=round(data_age_hours, 2),
            evaluated_at=utcnow().isoformat(),
            evidence=evidence,
            citations=list(boat.citations),
            what_would_change_it=_what_would_change_it(
                verdict, components, boat, wave_m, wind_kn, index
            ),
            vessel_source=vessel_source,
        ),
        wave_m=wave_m,
        period_s=wave_period_s,
        directional_spread=directional_spread,
        hsea_initial_m=hsea_initial_m,
        hsea_final_m=hsea_final_m,
        beam_m=beam_m,
    )


def _what_would_change_it(
    verdict: Verdict,
    components: list[Component],
    boat: BoatClass,
    wave_m: float | None,
    wind_kn: float | None,
    index: float,
) -> list[str]:
    """Plain-language statements of what would flip the verdict.

    This is the part a fisherman actually acts on, and it is computed from the
    same numbers as the verdict rather than written by a language model.
    """
    if verdict == "GO":
        return []
    out: list[str] = []
    if wave_m is not None and wave_m >= boat.max_wave_m:
        out.append(
            f"Hs would need to fall below {_num(boat.max_wave_m)} m "
            f"(currently {_num(wave_m)} m, a drop of {_margin(wave_m - boat.max_wave_m, 'm')})"
        )
    if wind_kn is not None and wind_kn >= boat.max_wind_kn:
        out.append(
            f"wind would need to fall below {_num(boat.max_wind_kn)} kn "
            f"(currently {_num(wind_kn)} kn, a drop of {_margin(wind_kn - boat.max_wind_kn)})"
        )
    if not out and verdict in ("CAUTION", "NO-GO"):
        weakest = min(
            (c for c in components if c.value is not None), key=lambda c: c.score, default=None
        )
        if weakest is not None:
            needed = GO_THRESHOLD - index
            label = "convective potential" if weakest.name == "lightning" else weakest.name
            out.append(
                f"the index would need to rise {needed:.0f} points to reach GO; "
                f"{label} is the weakest component at {weakest.score:.0f}/100"
            )
    if verdict == "UNVERIFIABLE":
        out.append("the missing forecast inputs would need to become available")
    out.append(
        f"a larger vessel class would have a higher limit "
        f"(your class allows Hs {_num(boat.max_wave_m)} m, wind {_num(boat.max_wind_kn)} kn), "
        "but that is not something to change for a single trip"
    )
    return out


def assess_from_evidence(
    evidence: dict[str, Evidence],
    *,
    loa_m: float | None = None,
    boat_class: BoatClass | None = None,
    boat_class_code: str | None = None,
    directional_spread: float | None = None,
    hsea_initial_m: float | None = None,
    hsea_final_m: float | None = None,
    beam_m: float | None = None,
) -> RiskResult:
    """Convenience wrapper: pull the engine's inputs out of an Evidence mapping.

    Vessel precedence: ``boat_class`` / ``boat_class_code`` → ``loa_m`` → UNKNOWN.
    Keeps unit handling in one place. The engine itself stays a pure function of
    plain numbers so it is trivially testable and has no provenance dependency.

    BSI inputs: wave period is taken from evidence when present. Directional
    spread and 6 h wind-sea pair must be supplied explicitly — they are not
    invented from unrelated variables.
    """

    def value(name: str) -> float | None:
        item = evidence.get(name)
        if item is None or item.value is None or not item.is_decision_grade:
            return None
        return float(item.value)

    visibility_m = value("visibility")
    # `staleness_age_hours` floors a forecast's negative age at zero. A value
    # valid at 06:00 tomorrow has age -14 h, which is lead time rather than
    # freshness; letting it through unclamped would report "data -14.0 h old"
    # and sail past the confidence check as if it were unusually fresh.
    ages = [
        e.freshness.staleness_age_hours
        for e in evidence.values()
        if e.value is not None and e.freshness.age_hours != float("inf")
    ]

    # Prefer an explicitly supplied 6 h wind-sea pair; otherwise leave None so
    # rapid-development stays UNAVAILABLE rather than substituting total Hs.
    if hsea_initial_m is None and hsea_final_m is None:
        # A single current wind_wave_height cannot form Z6h alone.
        pass

    return assess(
        wave_m=value("wave_height"),
        wind_kn=value("wind_speed"),
        visibility_km=None if visibility_m is None else visibility_m / 1000.0,
        cape_j_kg=value("convective_energy"),
        loa_m=loa_m,
        boat_class=boat_class,
        boat_class_code=boat_class_code,
        data_age_hours=max(ages) if ages else 0.0,
        evidence=list(evidence.values()),
        wave_period_s=value("wave_period"),
        directional_spread=directional_spread,
        hsea_initial_m=hsea_initial_m,
        hsea_final_m=hsea_final_m,
        beam_m=beam_m,
    )


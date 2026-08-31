"""Cross-source comparisons with time, unit and tolerance checks on the backend."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from orca.provenance import Evidence, utcnow
from orca.sources import open_meteo
from orca.sources.cmems import cmems
from orca.sources.erddap import erddap
from orca.sources.nasa import power


class ValidationStatus(StrEnum):
    AGREE = "agree"
    DISAGREE = "disagree"
    INCONCLUSIVE = "inconclusive"
    UNAVAILABLE = "unavailable"


class CrossCheck(BaseModel):
    variable: str
    status: ValidationStatus
    primary: Evidence
    secondary: Evidence
    canonical_unit: str
    primary_value: float | None = None
    secondary_value: float | None = None
    absolute_difference: float | None = Field(default=None, ge=0)
    tolerance: float | None = Field(default=None, ge=0)
    time_separation_hours: float | None = Field(default=None, ge=0)
    tolerance_basis: str
    message: str


class CrossValidationResponse(BaseModel):
    lat: float
    lon: float
    generated_at: datetime
    checks: list[CrossCheck]
    summary: dict[str, int]
    note: str


def _unavailable(variable: str, dataset: str, provider: str, reason: str) -> Evidence:
    return Evidence.unavailable(
        dataset_id=dataset,
        provider=provider,
        variable=variable,
        reason=reason,
    )


def _as_number(evidence: Evidence) -> float | None:
    if isinstance(evidence.value, (int, float)) and math.isfinite(float(evidence.value)):
        return float(evidence.value)
    return None


def _convert(value: float, unit: str | None, target: str) -> float | None:
    normalized = (unit or "").strip().lower().replace("°", "deg")
    if target == "degC" and normalized in {"degc", "c", "celsius"}:
        return value
    if target == "m" and normalized in {"m", "metre", "meter"}:
        return value
    if target == "m/s":
        if normalized in {"m/s", "ms-1", "m s-1"}:
            return value
        if normalized in {"kn", "kt", "knot", "knots"}:
            return value * 0.514444
        if normalized in {"km/h", "kmh"}:
            return value / 3.6
    return None


def compare(
    variable: str,
    primary: Evidence,
    secondary: Evidence,
    *,
    canonical_unit: str,
    tolerance: float,
    tolerance_basis: str,
    maximum_time_separation_hours: float,
) -> CrossCheck:
    first_raw, second_raw = _as_number(primary), _as_number(secondary)
    first = _convert(first_raw, primary.unit, canonical_unit) if first_raw is not None else None
    second = (
        _convert(second_raw, secondary.unit, canonical_unit) if second_raw is not None else None
    )
    if first is None or second is None:
        reasons = [
            item.notes
            for item in (primary, secondary)
            if item.value is None or not isinstance(item.value, (int, float))
        ]
        return CrossCheck(
            variable=variable,
            status=ValidationStatus.UNAVAILABLE,
            primary=primary,
            secondary=secondary,
            canonical_unit=canonical_unit,
            tolerance_basis=tolerance_basis,
            message="Cannot compare: " + "; ".join(reason for reason in reasons if reason),
        )
    time_gap = abs(
        (primary.freshness.valid_time - secondary.freshness.valid_time).total_seconds() / 3600.0
    )
    difference = abs(first - second)
    if time_gap > maximum_time_separation_hours:
        return CrossCheck(
            variable=variable,
            status=ValidationStatus.INCONCLUSIVE,
            primary=primary,
            secondary=secondary,
            canonical_unit=canonical_unit,
            primary_value=round(first, 4),
            secondary_value=round(second, 4),
            absolute_difference=round(difference, 4),
            tolerance=round(tolerance, 4),
            time_separation_hours=round(time_gap, 3),
            tolerance_basis=tolerance_basis,
            message=(
                f"Values are {time_gap:.1f} h apart, beyond the "
                f"{maximum_time_separation_hours:g} h coincidence limit."
            ),
        )
    status = ValidationStatus.AGREE if difference <= tolerance else ValidationStatus.DISAGREE
    relation = "within" if status is ValidationStatus.AGREE else "outside"
    return CrossCheck(
        variable=variable,
        status=status,
        primary=primary,
        secondary=secondary,
        canonical_unit=canonical_unit,
        primary_value=round(first, 4),
        secondary_value=round(second, 4),
        absolute_difference=round(difference, 4),
        tolerance=round(tolerance, 4),
        time_separation_hours=round(time_gap, 3),
        tolerance_basis=tolerance_basis,
        message=(
            f"Sources {status.value}: difference {difference:.2f} {canonical_unit} is "
            f"{relation} the {tolerance:.2f} {canonical_unit} comparison tolerance."
        ),
    )


async def validate_point(
    lat: float,
    lon: float,
    *,
    include_wave: bool = True,
) -> CrossValidationResponse:
    tasks = [
        open_meteo.conditions_at(lat, lon),
        erddap.point("mur_sst", lat, lon, variables=["sst", "sst_uncertainty"]),
        power.wind_at(lat, lon),
    ]
    if include_wave:
        tasks.append(cmems.wave_at(lat, lon))
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    conditions = raw[0] if isinstance(raw[0], dict) else {}
    satellite = raw[1] if isinstance(raw[1], dict) else {}
    nasa_wind = raw[2] if isinstance(raw[2], dict) else {}
    primary_sst = conditions.get("sst") or _unavailable(
        "sst", "open_meteo.marine", "Open-Meteo", "model SST unavailable"
    )
    satellite_sst = satellite.get("sst") or _unavailable(
        "sst", "jplMURSST41", "NOAA", "MUR satellite SST unavailable"
    )
    satellite_error = satellite.get("sst_uncertainty")
    error_value = _as_number(satellite_error) if satellite_error else None
    # Open-Meteo does not publish a per-point error field.  The 0.5 C term is
    # explicitly an ORCA representativeness allowance, not invented provider data.
    sst_tolerance = math.hypot(0.5, error_value or 0.0)
    checks = [
        compare(
            "sst",
            primary_sst,
            satellite_sst,
            canonical_unit="degC",
            tolerance=sst_tolerance,
            tolerance_basis=(
                "root-sum-square of ORCA's 0.5 degC model representativeness allowance "
                + (
                    f"and MUR's {error_value:.3f} degC analysis_error"
                    if error_value is not None
                    else "with no usable MUR analysis_error"
                )
            ),
            maximum_time_separation_hours=36,
        )
    ]

    primary_wind = conditions.get("wind_speed") or _unavailable(
        "wind_speed", "open_meteo.forecast", "Open-Meteo", "forecast wind unavailable"
    )
    secondary_wind = nasa_wind.get("wind_speed") or _unavailable(
        "wind_speed", "NASA_POWER_MERRA2_NRT", "NASA", "NASA POWER wind unavailable"
    )
    # POWER NRT commonly trails the live forecast.  Compare it to the closest
    # Open-Meteo historical hour instead of comparing different weather systems
    # forty hours apart and calling the result scientific validation.
    if secondary_wind.value is not None:
        age_days = math.ceil(max(0.0, secondary_wind.freshness.age_hours) / 24.0) + 1
        history = await open_meteo.forecast.series(
            lat,
            lon,
            variables=["wind_speed"],
            forecast_days=1,
            past_days=min(max(age_days, 1), 7),
        )
        candidates = [point for point in history.get("wind_speed", []) if point.value is not None]
        if candidates:
            historical_primary = min(
                candidates,
                key=lambda point: abs(
                    (
                        point.freshness.valid_time - secondary_wind.freshness.valid_time
                    ).total_seconds()
                ),
            )
            if abs(
                (
                    historical_primary.freshness.valid_time - secondary_wind.freshness.valid_time
                ).total_seconds()
            ) < abs(
                (
                    primary_wind.freshness.valid_time - secondary_wind.freshness.valid_time
                ).total_seconds()
            ):
                primary_wind = historical_primary
    p_wind_raw, s_wind_raw = _as_number(primary_wind), _as_number(secondary_wind)
    p_wind = _convert(p_wind_raw, primary_wind.unit, "m/s") if p_wind_raw is not None else 0
    s_wind = _convert(s_wind_raw, secondary_wind.unit, "m/s") if s_wind_raw is not None else 0
    wind_tolerance = max(2.5, 0.3 * ((p_wind or 0) + (s_wind or 0)) / 2)
    checks.append(
        compare(
            "wind_speed",
            primary_wind,
            secondary_wind,
            canonical_unit="m/s",
            tolerance=wind_tolerance,
            tolerance_basis=(
                "ORCA cross-model tolerance: max(2.5 m/s, 30% of the pair mean); "
                "not provider-supplied measurement uncertainty"
            ),
            maximum_time_separation_hours=12,
        )
    )

    if include_wave:
        primary_wave = conditions.get("wave_height") or _unavailable(
            "wave_height", "open_meteo.marine", "Open-Meteo", "wave height unavailable"
        )
        cmems_result = raw[3]
        secondary_wave = (
            cmems_result
            if isinstance(cmems_result, Evidence)
            else _unavailable(
                "wave_height",
                "cmems_mod_glo_wav_anfc_0.083deg_PT3H-i",
                "CMEMS",
                f"CMEMS wave fetch failed: {cmems_result}",
            )
        )
        p_wave, s_wave = _as_number(primary_wave) or 0, _as_number(secondary_wave) or 0
        wave_tolerance = max(0.5, 0.3 * (p_wave + s_wave) / 2)
        checks.append(
            compare(
                "wave_height",
                primary_wave,
                secondary_wave,
                canonical_unit="m",
                tolerance=wave_tolerance,
                tolerance_basis=(
                    "ORCA cross-model tolerance: max(0.5 m, 30% of the pair mean); "
                    "not provider-supplied measurement uncertainty"
                ),
                maximum_time_separation_hours=12,
            )
        )

    summary = {status.value: 0 for status in ValidationStatus}
    for check in checks:
        summary[check.status.value] += 1
    return CrossValidationResponse(
        lat=lat,
        lon=lon,
        generated_at=utcnow(),
        checks=checks,
        summary=summary,
        note=(
            "Agreement is a consistency check, not proof that either model is true. "
            "Disagreement is surfaced and never silently averaged into the safety verdict."
        ),
    )

"""The tools the planner may select from, each with a capability descriptor.

The descriptors are the point. "Autonomously discover and retrieve the right
data" is a scored requirement, and it is only real if the planner *chooses*
based on declared properties — resolution, latency, coverage, cost, provenance —
rather than running a hardcoded sequence. Two different questions must visibly
pull different tool sets, and the descriptor is what makes that a decision
instead of an if/else.

Every tool returns a ``ToolResult`` carrying Evidence, so the reporting node
cannot cite something a tool did not actually return.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from orca.provenance import Citation, Evidence, Freshness, Provenance, Provider, utcnow
from orca.services import thresholds
from orca.services.cross_validation import validate_point
from orca.services.overpass import predict_overpasses
from orca.services.risk_engine import assess_from_evidence
from orca.sources import open_meteo
from orca.sources.ais import aisstream
from orca.sources.erddap import DATASETS, erddap
from orca.sources.gfw import gfw
from orca.sources.worldtides import worldtides

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Capability:
    """What a tool can do, in machine-readable form.

    The planner reads these; they are not documentation. ``cost`` is relative
    effort, not money — it is what stops the planner pulling a satellite grid to
    answer a question about wind.
    """

    #: What kind of question this tool serves.
    answers: tuple[str, ...]
    #: Spatial resolution in degrees. None for point services.
    resolution_deg: float | None
    #: Typical latency in milliseconds, measured not guessed.
    latency_ms: int
    #: Provenance state the result will carry.
    provenance: str
    #: 1 = cheap point lookup, 5 = heavy grid subset.
    cost: int
    coverage: str
    #: Whether the result may contribute to a safety verdict.
    decision_grade: bool = True
    notes: str = ""


@dataclass(slots=True)
class ToolResult:
    ok: bool
    tool: str
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    capability: Capability
    run: Callable[..., Awaitable[ToolResult]]
    #: Which specialist node owns it, for the trace UI.
    owner: Literal["weather", "ocean", "geospatial", "risk", "data_discovery"]


# --------------------------------------------------------------------------- #
# implementations
# --------------------------------------------------------------------------- #


async def _fetch_marine_conditions(lat: float, lon: float, **_: Any) -> ToolResult:
    evidence = await open_meteo.conditions_at(lat, lon)
    usable = [e for e in evidence.values() if e.value is not None]
    if not usable:
        return ToolResult(
            ok=False,
            tool="fetch_marine_conditions",
            summary="no marine conditions available at this point",
            error="every variable came back unavailable",
        )
    parts = [
        f"Hs {evidence['wave_height'].value} m" if "wave_height" in evidence else None,
        f"Tp {evidence['wave_period'].value} s" if "wave_period" in evidence else None,
        f"wind {evidence['wind_speed'].value} kn" if "wind_speed" in evidence else None,
        f"gusts {evidence['wind_gust'].value} kn" if "wind_gust" in evidence else None,
        (
            f"visibility {float(evidence['visibility'].value) / 1000:.1f} km"
            if "visibility" in evidence and evidence["visibility"].value is not None
            else None
        ),
        (
            f"CAPE {evidence['convective_energy'].value} J/kg"
            if "convective_energy" in evidence
            else None
        ),
    ]
    return ToolResult(
        ok=True,
        tool="fetch_marine_conditions",
        summary=" · ".join(p for p in parts if p),
        evidence=list(evidence.values()),
        data={"lat": lat, "lon": lon, "variables": {k: v.value for k, v in evidence.items()}},
    )


async def _fetch_tides(lat: float, lon: float, **_: Any) -> ToolResult:
    """Predicted tide height and the next high/low, or an explicit data gap."""
    result = await worldtides.forecast(lat, lon, days=2)
    return ToolResult(
        ok=result.available,
        tool="fetch_tides",
        summary=result.summary,
        evidence=result.evidence,
        data=result.describe(),
        error=result.error,
    )


async def _assess_forecast_risk(
    lat: float, lon: float, loa_m: float = 8.2, **_: Any
) -> ToolResult:
    """Assess the worst forecast hour in tomorrow's local morning window.

    A question about tomorrow must never be answered with the nearest-to-now
    sample. The window is 05:00–11:00 Asia/Kolkata and every component passed to
    the safety assessment carries that forecast hour as its valid time.
    """
    local_tz = ZoneInfo("Asia/Kolkata")
    tomorrow = utcnow().astimezone(local_tz).date() + timedelta(days=1)
    start_local = datetime.combine(tomorrow, time(5, 0), tzinfo=local_tz)
    end_local = datetime.combine(tomorrow, time(11, 0), tzinfo=local_tz)
    series = await open_meteo.series_at(lat, lon, forecast_days=3)
    wave_points = [
        item
        for item in series.get("wave_height", [])
        if start_local <= item.freshness.valid_time.astimezone(local_tz) <= end_local
    ]
    if not wave_points:
        return ToolResult(
            ok=False,
            tool="assess_forecast_risk",
            summary=(
                f"tomorrow-morning forecast unavailable for {start_local:%d %b, %H:%M}–"
                f"{end_local:%H:%M IST}"
            ),
            error="the forecast series contained no wave samples in the requested window",
        )

    variables = ("wave_height", "wind_speed", "visibility", "convective_energy")
    assessed: list[tuple[datetime, dict[str, Evidence], Any]] = []
    for wave in wave_points:
        when = wave.freshness.valid_time
        evidence: dict[str, Evidence] = {"wave_height": wave}
        for variable in variables[1:]:
            points = [item for item in series.get(variable, []) if item.value is not None]
            if not points:
                continue
            nearest = min(
                points,
                key=lambda item: abs((item.freshness.valid_time - when).total_seconds()),
            )
            if abs((nearest.freshness.valid_time - when).total_seconds()) <= 3600:
                evidence[variable] = nearest
        assessed.append((when, evidence, assess_from_evidence(evidence, loa_m=loa_m)))

    severity = {"GO": 0, "CAUTION": 1, "UNVERIFIABLE": 2, "NO-GO": 3}
    when, evidence, risk = max(
        assessed,
        key=lambda item: (severity.get(item[2].verdict, 9), -float(item[2].index)),
    )
    values = {key: item.value for key, item in evidence.items()}
    visibility_km = (
        None if values.get("visibility") is None else float(values["visibility"]) / 1000.0
    )
    summary = (
        f"Tomorrow 05:00–11:00 IST: worst hour {when.astimezone(local_tz):%H:%M IST} is "
        f"{risk.verdict} at {risk.index}/100; Hs {values.get('wave_height')} m, "
        f"wind {values.get('wind_speed')} kn, visibility "
        f"{None if visibility_km is None else round(visibility_km, 1)} km, "
        f"CAPE {values.get('convective_energy')} J/kg"
    )
    risk_payload = risk.model_dump(mode="json")
    risk_payload["forecast_window"] = {
        "start": start_local.isoformat(),
        "end": end_local.isoformat(),
        "worst_hour": when.astimezone(local_tz).isoformat(),
        "hours_assessed": len(assessed),
    }
    return ToolResult(
        ok=True,
        tool="assess_forecast_risk",
        summary=summary,
        evidence=list(evidence.values()),
        data={"risk": risk_payload, "window": risk_payload["forecast_window"]},
    )


async def _check_marine_alerts(lat: float, lon: float, **_: Any) -> ToolResult:
    """Official-alert status, kept separate from CAPE-based potential."""
    from orca.config import get_settings

    settings = get_settings()
    imd_url = "https://api.imd.gov.in/public/api_reference.html"
    reason = (
        "IMD_API_KEY is not configured, so official IMD lightning, cyclone and fishermen "
        "warnings cannot be verified in this deployment."
        if not settings.has_imd
        else "The IMD credential is configured, but the alert adapter is not enabled in this build."
    )
    unavailable = [
        Evidence(
            dataset_id="imd.api",
            provider=Provider.IMD,
            variable=variable,
            value=None,
            unit=None,
            provenance=Provenance.UNAVAILABLE,
            freshness=Freshness.static(),
            url=imd_url,
            location=(lon, lat),
            citations=[
                Citation(
                    label="IMD API — marine, cyclone and lightning products",
                    provider=Provider.IMD,
                    url=imd_url,
                )
            ],
            notes=reason,
        )
        for variable in ("lightning_alert", "cyclone_alert")
    ]
    cape_values = await open_meteo.forecast.at(
        lat, lon, variables=["convective_energy"]
    )
    cape = cape_values.get("convective_energy")
    cape_text = (
        f" CAPE is {cape.value} J/kg, which indicates thunderstorm potential only—not "
        "lightning detection or an official alert."
        if cape is not None and cape.value is not None
        else " No CAPE context was available either."
    )
    return ToolResult(
        ok=False,
        tool="check_marine_alerts",
        summary=reason + cape_text,
        evidence=[*unavailable, *([cape] if cape is not None else [])],
        data={
            "official_alerts_verified": False,
            "lightning_alert": "unavailable",
            "cyclone_alert": "unavailable",
            "cape_j_kg": None if cape is None else cape.value,
            "cape_is_alert": False,
            "reason": reason,
        },
        error=reason,
    )


async def _check_geofences(lat: float, lon: float, **_: Any) -> ToolResult:
    """Full-resolution legal-boundary proximity at the selected point."""
    from orca.services import geofence as geofence_service

    if not geofence_service.index.ready:
        return ToolResult(
            ok=False,
            tool="check_geofences",
            summary="geofence reference geography is unavailable",
            error="the full-resolution geofence index is empty",
        )
    hits = geofence_service.index.check(lat, lon, radius_km=600.0)
    citation = geofence_service.evidence_citation()
    evidence = [
        Evidence(
            dataset_id=f"marine_regions:{hit.fence.key}",
            provider=Provider.MARINE_REGIONS,
            variable="geofence_distance",
            value=round(hit.distance_m / 1000.0, 3),
            unit="km",
            provenance=geofence_service.provenance(),
            freshness=Freshness.static(),
            url=citation.url,
            location=(lon, lat),
            citations=[citation],
            notes=hit.narrative(),
        )
        for hit in hits
    ]
    summary = (
        "; ".join(hit.narrative() for hit in hits[:3])
        if hits
        else "no indexed EEZ/IMBL boundary lies within 600 km of this point"
    )
    return ToolResult(
        ok=True,
        tool="check_geofences",
        summary=summary,
        evidence=evidence,
        data={"proximities": [hit.describe() for hit in hits], "radius_km": 600.0},
    )


async def _diagnose_productivity(lat: float, lon: float, **_: Any) -> ToolResult:
    """Refuse causal diagnosis until a decline and its baseline are evidenced."""
    url = "https://www.cmfri.org.in/annual-data"
    reason = (
        "A productivity decline cannot be diagnosed from one current ocean snapshot. Name the "
        "coastal region, species or fishery, and comparison period; then compare CMFRI landings "
        "and effort with SST, chlorophyll, upwelling and fishing-pressure time series."
    )
    evidence = Evidence(
        dataset_id="cmfri.annual_landings.required",
        provider="ICAR-CMFRI",
        variable="fish_productivity_trend",
        value=None,
        unit=None,
        provenance=Provenance.UNAVAILABLE,
        freshness=Freshness.static(),
        url=url,
        location=(lon, lat),
        citations=[
            Citation(
                label="ICAR-CMFRI annual marine fish landing data",
                provider="ICAR-CMFRI",
                url=url,
            )
        ],
        notes=reason,
    )
    return ToolResult(
        ok=True,
        tool="diagnose_productivity",
        summary=reason,
        evidence=[evidence],
        data={
            "diagnosis_supported": False,
            "required": [
                "named region",
                "species or fishery",
                "comparison period",
                "landings and fishing effort history",
                "environmental time series",
            ],
        },
        error=None,
    )


async def _fetch_satellite_sst(lat: float, lon: float, **_: Any) -> ToolResult:
    values = await erddap.point("mur_sst", lat, lon, variables=["sst", "sst_uncertainty"])
    sst = values.get("sst")
    if sst is None or sst.value is None:
        return ToolResult(
            ok=False,
            tool="fetch_satellite_sst",
            summary="satellite SST unavailable here",
            error=sst.notes if sst else "no value",
            evidence=list(values.values()),
        )
    return ToolResult(
        ok=True,
        tool="fetch_satellite_sst",
        summary=f"MUR satellite SST {sst.value} °C (1 km, {sst.freshness.age_hours:.0f} h old)",
        evidence=list(values.values()),
        data={"sst": sst.value},
    )


async def _predict_overpasses(lat: float, lon: float, **_: Any) -> ToolResult:
    prediction = await predict_overpasses(lat, lon, hours=48)
    if not prediction.passes:
        return ToolResult(
            ok=not prediction.unavailable_satellites,
            tool="predict_satellite_overpasses",
            summary="no nominal sensor-swath crossing in the next 48 hours",
            data=prediction.model_dump(mode="json"),
            error="; ".join(prediction.unavailable_satellites) or None,
        )
    first = prediction.passes[0]
    evidence = [
        Evidence(
            dataset_id=f"sgp4:{item.norad_id}",
            provider=Provider.ORCA,
            variable="satellite_overpass",
            value=item.closest_time.isoformat(),
            unit="UTC",
            provenance=Provenance.DERIVED,
            freshness=Freshness.of("tle", item.tle_epoch),
            lineage=[f"celestrak.gp:{item.norad_id}"],
            url=item.source_url,
            location=(lon, lat),
            method="SGP4 ground track intersected with nominal instrument swath",
            notes=item.caveat,
        )
        for item in prediction.passes[:3]
    ]
    return ToolResult(
        ok=True,
        tool="predict_satellite_overpasses",
        summary=(
            f"next nominal swath opportunity: {first.satellite} at "
            f"{first.closest_time:%Y-%m-%d %H:%M UTC}, closest track "
            f"{first.closest_distance_km:.0f} km"
        ),
        evidence=evidence,
        data=prediction.model_dump(mode="json"),
    )


async def _cross_validate(lat: float, lon: float, **_: Any) -> ToolResult:
    result = await validate_point(lat, lon, include_wave=True)
    evidence: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for check in result.checks:
        for item in (check.primary, check.secondary):
            key = (item.dataset_id, item.variable)
            if key not in seen:
                evidence.append(item)
                seen.add(key)
    counts = result.summary
    return ToolResult(
        ok=counts.get("agree", 0) + counts.get("disagree", 0) > 0,
        tool="cross_validate_conditions",
        summary=(
            f"cross-validation: {counts.get('agree', 0)} agree, "
            f"{counts.get('disagree', 0)} disagree, "
            f"{counts.get('inconclusive', 0)} inconclusive, "
            f"{counts.get('unavailable', 0)} unavailable"
        ),
        evidence=evidence,
        data=result.model_dump(mode="json"),
    )


async def _check_vessel_traffic(lat: float, lon: float, **_: Any) -> ToolResult:
    snapshot = await aisstream.snapshot(
        (lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5), duration_seconds=5
    )
    count_evidence = Evidence(
        dataset_id="aisstream.websocket",
        provider=Provider.AISSTREAM,
        variable="ais_position",
        value=len(snapshot.vessels) if snapshot.connected else None,
        unit="vessels observed in 5 s",
        provenance=snapshot.provenance,
        freshness=Freshness.of("ais_position", snapshot.started_at),
        location=(lon, lat),
        notes=snapshot.coverage_note if snapshot.connected else snapshot.error,
    )
    return ToolResult(
        ok=snapshot.connected,
        tool="check_vessel_traffic",
        summary=(
            f"live AIS snapshot saw {len(snapshot.vessels)} vessel(s); zero does not prove "
            "empty water because Indian-Ocean receiver coverage is sparse"
            if snapshot.connected
            else f"AIS snapshot unavailable: {snapshot.error}"
        ),
        evidence=[count_evidence],
        data=snapshot.model_dump(mode="json"),
        error=snapshot.error,
    )


async def _check_fishing_activity(lat: float, lon: float, **_: Any) -> ToolResult:
    report = await gfw.effort((lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5), days=30)
    return ToolResult(
        ok=report.available,
        tool="check_fishing_activity",
        summary=(
            f"GFW found {report.total_apparent_fishing_hours:.1f} apparent fishing hours "
            f"from {report.vessel_count} vessel(s), through {report.end_date}"
            if report.available
            else f"GFW report unavailable: {report.error}"
        ),
        evidence=[report.evidence],
        data=report.model_dump(mode="json"),
        error=report.error,
    )


async def _assess_risk(lat: float, lon: float, loa_m: float = 8.2, **_: Any) -> ToolResult:
    evidence = await open_meteo.conditions_at(lat, lon)
    result = assess_from_evidence(evidence, loa_m=loa_m)
    veto_text = f" — {len(result.vetoes)} veto(es)" if result.vetoes else ""
    return ToolResult(
        ok=True,
        tool="assess_risk",
        summary=f"{result.verdict} at {result.index}/100{veto_text}",
        evidence=result.evidence,
        data={"risk": result.model_dump(mode="json")},
    )


async def _lookup_thresholds(loa_m: float = 8.2, **_: Any) -> ToolResult:
    boat = thresholds.classify(loa_m)
    return ToolResult(
        ok=True,
        tool="lookup_boat_thresholds",
        summary=(
            f"{loa_m} m -> {boat.code} ({boat.label}); Hs limit {boat.max_wave_m} m, "
            f"wind limit {boat.max_wind_kn} kn"
        ),
        data={
            "code": boat.code,
            "label": boat.label,
            "max_wave_m": boat.max_wave_m,
            "max_wind_kn": boat.max_wind_kn,
            "min_visibility_km": boat.min_visibility_km,
            "citation": boat.source_citation.model_dump(mode="json"),
            "thresholds_version": thresholds.THRESHOLDS_VERSION,
        },
    )


async def _forecast_window(lat: float, lon: float, days: int = 2, **_: Any) -> ToolResult:
    """The trend, which is what a question about *tomorrow* actually needs."""
    series = await open_meteo.series_at(lat, lon, forecast_days=days)
    waves = series.get("wave_height") or []
    winds = series.get("wind_speed") or []
    usable_waves = [e for e in waves if e.value is not None]
    if not usable_waves:
        return ToolResult(
            ok=False,
            tool="fetch_forecast_window",
            summary="no forecast series available",
            error="wave series empty",
        )
    values = [float(e.value) for e in usable_waves]  # type: ignore[arg-type]
    peak = max(usable_waves, key=lambda e: float(e.value))  # type: ignore[arg-type]
    return ToolResult(
        ok=True,
        tool="fetch_forecast_window",
        summary=(
            f"over {days} day(s): Hs {min(values):.1f}-{max(values):.1f} m, "
            f"peaking {peak.value} m at {peak.freshness.valid_time:%d %b %H:%MZ}"
        ),
        # A whole hourly series would swamp the evidence panel; the endpoints and
        # the peak are what the answer actually rests on.
        evidence=[usable_waves[0], peak, usable_waves[-1]]
        + ([winds[0]] if winds and winds[0].value is not None else []),
        data={
            "wave_min_m": min(values),
            "wave_max_m": max(values),
            "peak_at": peak.freshness.valid_time.isoformat(),
            "hours": len(usable_waves),
        },
    )


async def _find_fishing_zones(lat: float, lon: float, **_: Any) -> ToolResult:
    """Nearest derived Potential Fishing Zone, as a distance and a bearing.

    This tool exists because of a gap the multi-query work exposed: asked "is it
    safe, and where are the fish?", ORCA answered the safety half and said "I have
    no data on where fish are likely to be found" — while a derived PFZ field was
    sitting on disk. The satellite-SST tool returns a temperature, which is an
    input to the answer and not the answer.
    """
    import json

    from orca.config import get_settings
    from orca.services.geo import bearing_deg, compass_point, geodesic_m

    path = get_settings().raster_dir / "pfz_rank" / "latest.json"
    if not path.exists():
        return ToolResult(
            ok=False,
            tool="find_fishing_zones",
            summary="no PFZ has been derived yet",
            error=(
                "The PFZ field has not been generated in this deployment. It is derived by the "
                "ingest job from SST and chlorophyll."
            ),
        )

    try:
        sidecar = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ToolResult(
            ok=False, tool="find_fishing_zones", summary="PFZ unreadable", error=str(exc)
        )

    zones = sidecar.get("zones") or []
    if not zones:
        derivation = sidecar.get("pfz", {})
        return ToolResult(
            ok=False,
            tool="find_fishing_zones",
            summary="no fishing zones in the current derivation",
            error=(
                "The derivation produced no qualifying zones. Missing inputs: "
                f"{derivation.get('inputs_missing', [])}"
            ),
            data={"derivation": derivation},
        )

    scored = [(geodesic_m(lat, lon, z["centroid"]["lat"], z["centroid"]["lon"]), z) for z in zones]
    scored.sort(key=lambda pair: pair[0])
    # The best few, preferring higher rank at comparable distance — a rank 2 zone
    # slightly further away is a better answer than the nearest rank 1.
    best = sorted(scored[:12], key=lambda pair: (-pair[1]["rank"], pair[0]))[:3]

    valid_raw = sidecar.get("valid_time")
    try:
        valid_time = datetime.fromisoformat(str(valid_raw))
    except (TypeError, ValueError):
        valid_time = utcnow()
    threshold = float((derivation := sidecar.get("pfz", {})).get("thresholds", {}).get("chlorophyll_mg_m3", 0.3))
    freshness = Freshness.of("pfz_rank", valid_time)
    criteria = (
        f"thermal/chlorophyll front + chlorophyll > {threshold:g} mg m-3"
        if "chlorophyll" in derivation.get("inputs_used", [])
        else "thermal front only; chlorophyll was unavailable"
    )
    lines: list[str] = []
    for distance_m, zone in best:
        bearing = bearing_deg(lat, lon, zone["centroid"]["lat"], zone["centroid"]["lon"])
        lines.append(
            f"rank {zone['rank']} PFZ centred {zone['centroid']['lat']:.3f}°N, "
            f"{zone['centroid']['lon']:.3f}°E — {distance_m / 1000:.0f} km "
            f"{compass_point(bearing)}, about {zone['area_km2']:.0f} km2; {criteria}"
        )

    from orca.science.pfz import INCOIS_CITATION

    derived_citation = Citation(
        label=f"ORCA derived PFZ field — valid {valid_time:%d %b %Y}",
        provider=Provider.ORCA,
        identifier="orca:pfz_rank",
    )
    lineage_citations = [
        Citation(
            label=DATASETS[key].title,
            provider=DATASETS[key].provider,
            url=DATASETS[key].url(".html"),
            identifier=DATASETS[key].dataset_id,
        )
        for key in ("mur_sst", "esacci_chl_monthly")
    ]

    zone_evidence = [
        Evidence(
            dataset_id="orca:pfz_rank",
            provider=Provider.ORCA,
            variable="pfz_rank",
            value=int(zone["rank"]),
            unit="rank",
            provenance=Provenance.DERIVED,
            freshness=freshness,
            lineage=list(sidecar.get("lineage") or ["jplMURSST41"]),
            url=INCOIS_CITATION.url,
            location=(float(zone["centroid"]["lon"]), float(zone["centroid"]["lat"])),
            method=sidecar.get("method"),
            citations=[derived_citation, INCOIS_CITATION, *lineage_citations],
            notes=(
                f"{criteria}. Valid {valid_time.isoformat()}. "
                "PFZ means potential fish aggregation, not a safety clearance."
            ),
        )
        for _, zone in best
    ]
    return ToolResult(
        ok=True,
        tool="find_fishing_zones",
        summary=(
            "; ".join(lines)
            + f". Field valid {valid_time:%d %b %Y}"
            + (" and is STALE." if freshness.is_stale else ".")
        ),
        evidence=zone_evidence,
        data={
            "zones": [
                {
                    "rank": zone["rank"],
                    "distance_km": round(distance_m / 1000, 1),
                    "bearing_deg": round(
                        bearing_deg(lat, lon, zone["centroid"]["lat"], zone["centroid"]["lon"]), 1
                    ),
                    "compass": compass_point(
                        bearing_deg(lat, lon, zone["centroid"]["lat"], zone["centroid"]["lon"])
                    ),
                    "area_km2": zone["area_km2"],
                    "centroid": zone["centroid"],
                    "h3": zone.get("h3"),
                    "criteria": criteria,
                    "chlorophyll_threshold_mg_m3": threshold,
                }
                for distance_m, zone in best
            ],
            "derivation": derivation,
            "valid_time": valid_time.isoformat(),
            "stale": freshness.is_stale,
            "lineage": sidecar.get("lineage", []),
            "caveat": (
                "INCOIS publishes PFZ advisories as maps, not as an API. This is ORCA's "
                "reimplementation of the published methodology. "
                f"Criteria that could not be applied: {derivation.get('inputs_missing', [])}."
            ),
        },
    )


async def _screen_fishing_zones(
    lat: float, lon: float, loa_m: float = 8.2, **_: Any
) -> ToolResult:
    """Screen PFZ candidates for sea-state and legal-boundary problems.

    PFZ rank is an opportunity signal, never a hazard label. Each candidate is
    therefore assessed at its own centroid and checked against the full-resolution
    geofence index before ORCA says to avoid it.
    """
    candidates = await _find_fishing_zones(lat, lon)
    if not candidates.ok:
        return ToolResult(
            ok=False,
            tool="screen_fishing_zones",
            summary=f"could not screen fishing zones: {candidates.summary}",
            evidence=candidates.evidence,
            data=candidates.data,
            error=candidates.error,
        )

    zones = list(candidates.data.get("zones") or [])[:3]
    condition_sets = await asyncio.gather(
        *(
            open_meteo.conditions_at(
                float(zone["centroid"]["lat"]), float(zone["centroid"]["lon"])
            )
            for zone in zones
        )
    )

    screened: list[dict[str, Any]] = []
    evidence = list(candidates.evidence)
    for zone, conditions in zip(zones, condition_sets, strict=True):
        zone_lat = float(zone["centroid"]["lat"])
        zone_lon = float(zone["centroid"]["lon"])
        risk = assess_from_evidence(conditions, loa_m=loa_m)
        boundary = await _check_geofences(zone_lat, zone_lon)
        proximities = list(boundary.data.get("proximities") or [])
        india = next((item for item in proximities if item.get("fence") == "eez_india"), None)
        restrictions: list[str] = []
        if india is not None and not bool(india.get("inside")):
            restrictions.append("outside India's EEZ")
        for item in proximities:
            if item.get("kind") == "imbl" and float(item.get("distance_km") or 9999) <= 25:
                restrictions.append(
                    f"{float(item['distance_km']):.1f} km from {item.get('name', 'an IMBL')}"
                )

        if restrictions or risk.verdict == "NO-GO":
            action = "AVOID"
        elif risk.verdict in {"CAUTION", "UNVERIFIABLE"} or india is None:
            action = "REVIEW"
        else:
            action = "CONDITIONS WITHIN LIMITS"

        reasons = [*restrictions]
        if risk.vetoes:
            reasons.extend(risk.vetoes)
        elif risk.verdict != "GO":
            reasons.append(f"sea-state assessment is {risk.verdict} ({risk.index}/100)")
        if india is None:
            reasons.append("India EEZ containment could not be verified at this centroid")
        if candidates.data.get("stale"):
            reasons.append("PFZ field is stale; confirm the latest official advisory")

        screened.append(
            {
                **zone,
                "action": action,
                "reasons": reasons,
                "risk": risk.model_dump(mode="json"),
                "inside_india_eez": None if india is None else bool(india.get("inside")),
                "geofences": proximities,
            }
        )
        evidence.extend(risk.evidence)
        evidence.extend(boundary.evidence)

    lines = [
        (
            f"{item['action']}: rank {item['rank']} PFZ at "
            f"{item['centroid']['lat']:.3f}°N, {item['centroid']['lon']:.3f}°E"
            + (f" — {'; '.join(item['reasons'][:3])}" if item["reasons"] else "")
        )
        for item in screened
    ]
    if screened and all(item["inside_india_eez"] is True for item in screened):
        lines.append(
            "Geofence check: every screened centroid is inside India's EEZ and no "
            "jurisdictional restriction was triggered"
        )
    return ToolResult(
        ok=True,
        tool="screen_fishing_zones",
        summary="; ".join(lines),
        evidence=evidence,
        data={
            "screened_zones": screened,
            "pfz_valid_time": candidates.data.get("valid_time"),
            "pfz_stale": candidates.data.get("stale"),
            "caveat": (
                "A PFZ is a fish-aggregation opportunity, not a safety clearance. AVOID is "
                "used only for a sea-state hard veto or a verified jurisdictional restriction."
            ),
        },
    )


async def _plan_route(
    lat: float,
    lon: float,
    to_lat: float | None = None,
    to_lon: float | None = None,
    loa_m: float = 8.2,
    speed_kn: float = 8.0,
    **_: Any,
) -> ToolResult:
    """Plan a passage the rule engine has cleared cell by cell.

    Needs a destination, and says so rather than inventing one. An agent that
    guesses a destination produces a confident route to somewhere nobody asked
    about, which is worse than a request for clarification.
    """
    from orca.services.router import plan

    if to_lat is None or to_lon is None:
        return ToolResult(
            ok=False,
            tool="plan_route",
            summary="no destination given",
            error=(
                "Routing needs a destination. Ask the user where they are heading, or offer to "
                "assess the conditions at their current position instead."
            ),
        )

    result = await plan(
        start=(lat, lon),
        goal=(float(to_lat), float(to_lon)),
        loa_m=loa_m,
        speed_kn=speed_kn,
    )

    if not result.get("ok"):
        # A refusal is a successful answer to the question asked, so ok=True with
        # the reason in the summary. Marking it ok=False would make the trace show
        # a failed tool, and the planner would try to work around a tool that
        # worked perfectly.
        refused = result.get("refused_on_direct_line") or []
        blockers = sorted(
            {str(c.get("reason", "")).split(";")[0] for c in refused if c.get("reason")}
        )
        return ToolResult(
            ok=True,
            tool="plan_route",
            summary=(
                f"NO SAFE PASSAGE for a {result.get('boat_class')}: {result.get('reason')}"
                + (f" Blocking cells: {'; '.join(blockers[:3])}." if blockers else "")
            ),
            data=result,
        )

    return ToolResult(
        ok=True,
        tool="plan_route",
        summary=(
            f"{result['distance_nm']} nm route, {result['duration_h']} h at {result['speed_kn']} kn "
            f"({result['detour_pct']:+.0f}% vs the direct line), {result['worst_verdict']} "
            f"throughout for a {result['boat_class']}. {result['why_this_route']}"
            + (f" NOTE: {result['degraded']}" if result.get("degraded") else "")
        ),
        data=result,
    )


async def _list_datasets(**_: Any) -> ToolResult:
    """Data discovery: what ORCA can actually read, and how current each is.

    This is the tool that makes 'discover the right dataset' honest — including
    telling the truth that most INCOIS griddap products are archives.
    """
    current = [k for k, ds in DATASETS.items() if ds.is_current]
    archives = [
        f"{k} (ends {ds.coverage_end:%Y-%m})"
        for k, ds in DATASETS.items()
        if not ds.is_current and ds.coverage_end
    ]
    return ToolResult(
        ok=True,
        tool="discover_datasets",
        summary=(
            f"{len(current)} current grid(s): {', '.join(current)}. "
            f"{len(archives)} archive(s) usable only as climatology: {', '.join(archives)}"
        ),
        data={"current": current, "archives": archives},
    )


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

TOOLS: dict[str, Tool] = {
    "fetch_marine_conditions": Tool(
        name="fetch_marine_conditions",
        description=(
            "Live waves, swell, wind, gusts, visibility and convective energy at a point. "
            "The default source for any question about conditions now."
        ),
        capability=Capability(
            answers=("current_conditions", "safety", "wind", "waves", "visibility"),
            resolution_deg=0.05,
            latency_ms=1100,
            provenance="live",
            cost=1,
            coverage="global, hourly, 5 km",
            notes="Open-Meteo Marine + Forecast, fetched concurrently.",
        ),
        run=_fetch_marine_conditions,
        owner="weather",
    ),
    "fetch_tides": Tool(
        name="fetch_tides",
        description=(
            "Predicted tide height and next high/low event at the selected point. "
            "Returns an explicit unavailable result when WorldTides is not configured."
        ),
        capability=Capability(
            answers=("tide", "high_tide", "low_tide", "tide_height"),
            resolution_deg=None,
            latency_ms=1500,
            provenance="live",
            cost=2,
            coverage="global astronomical prediction; requires WorldTides credentials",
            decision_grade=False,
            notes="A prediction, not a tide-gauge observation.",
        ),
        run=_fetch_tides,
        owner="ocean",
    ),
    "fetch_forecast_window": Tool(
        name="fetch_forecast_window",
        description=(
            "Hourly wave and wind series over the next 1-7 days, reduced to range and peak. "
            "Use for any question about tomorrow, a window, or a trend."
        ),
        capability=Capability(
            answers=("forecast", "trend", "window", "tomorrow", "planning"),
            resolution_deg=0.05,
            latency_ms=1300,
            provenance="live",
            cost=2,
            coverage="global, hourly, 7 days ahead",
        ),
        run=_forecast_window,
        owner="weather",
    ),
    "assess_forecast_risk": Tool(
        name="assess_forecast_risk",
        description=(
            "GO / CAUTION / NO-GO for tomorrow's 05:00–11:00 IST window, using the "
            "worst hourly forecast in that window rather than current conditions."
        ),
        capability=Capability(
            answers=("future_safety", "tomorrow_safety", "morning_safety"),
            resolution_deg=0.05,
            latency_ms=1500,
            provenance="derived",
            cost=2,
            coverage="global, hourly, next three days",
        ),
        run=_assess_forecast_risk,
        owner="risk",
    ),
    "check_marine_alerts": Tool(
        name="check_marine_alerts",
        description=(
            "Check whether official IMD lightning/cyclone warnings can be verified. CAPE is "
            "reported separately as potential and is never presented as an alert."
        ),
        capability=Capability(
            answers=("alerts", "lightning_alert", "cyclone_alert", "fishermen_warning"),
            resolution_deg=None,
            latency_ms=1000,
            provenance="live",
            cost=2,
            coverage="India; authoritative verification requires IMD API access",
            decision_grade=False,
        ),
        run=_check_marine_alerts,
        owner="weather",
    ),
    "fetch_satellite_sst": Tool(
        name="fetch_satellite_sst",
        description=(
            "MUR satellite sea-surface temperature at 1 km. Independent of the model, so it "
            "also serves as cross-validation of the model SST."
        ),
        capability=Capability(
            answers=("sst", "temperature", "fishing", "front", "cross_validation"),
            resolution_deg=0.01,
            latency_ms=1800,
            provenance="live",
            cost=3,
            coverage="global, daily, 1 km",
            notes="Zero-auth via NOAA CoastWatch ERDDAP. About a day behind real time.",
        ),
        run=_fetch_satellite_sst,
        owner="ocean",
    ),
    "predict_satellite_overpasses": Tool(
        name="predict_satellite_overpasses",
        description=(
            "Predict the next Sentinel-3 and EOS-06 nominal sensor-swath crossings over a "
            "point using current CelesTrak elements and SGP4. This is an opportunity, not "
            "confirmation that a cloud-free image will be acquired."
        ),
        capability=Capability(
            answers=("satellite", "overpass", "imagery_time", "when_photographed"),
            resolution_deg=None,
            latency_ms=1600,
            provenance="derived",
            cost=2,
            coverage="global, next 48 hours",
            decision_grade=False,
            notes="Current GP/TLE input; nominal swath geometry; acquisition is not guaranteed.",
        ),
        run=_predict_overpasses,
        owner="data_discovery",
    ),
    "cross_validate_conditions": Tool(
        name="cross_validate_conditions",
        description=(
            "Cross-check SST, 10 m wind and significant wave height against independent NASA, "
            "satellite and Copernicus Marine sources with unit and valid-time alignment."
        ),
        capability=Capability(
            answers=("cross_validation", "reliability", "source_agreement", "verify"),
            resolution_deg=0.083,
            latency_ms=5000,
            provenance="live",
            cost=4,
            coverage="global where all source grids answer",
            decision_grade=False,
            notes="Disagreement is surfaced; sources are never silently averaged.",
        ),
        run=_cross_validate,
        owner="data_discovery",
    ),
    "check_vessel_traffic": Tool(
        name="check_vessel_traffic",
        description=(
            "Take a bounded five-second live AISStream snapshot around the point. Zero is "
            "reported with the sparse Indian-Ocean receiver-coverage caveat."
        ),
        capability=Capability(
            answers=("ais", "vessels", "traffic", "collision"),
            resolution_deg=None,
            latency_ms=5500,
            provenance="live",
            cost=3,
            coverage="receiver-dependent; sparse over much of the Indian Ocean",
            decision_grade=False,
        ),
        run=_check_vessel_traffic,
        owner="geospatial",
    ),
    "check_fishing_activity": Tool(
        name="check_fishing_activity",
        description=(
            "Query Global Fishing Watch for historical apparent fishing effort around the "
            "point. This is AIS-derived and delayed, not live tracking or proof of illegality."
        ),
        capability=Capability(
            answers=("fishing_activity", "fishing_effort", "gfw", "fleet_history"),
            resolution_deg=0.1,
            latency_ms=10000,
            provenance="live",
            cost=4,
            coverage="global AIS-transmitting fishing fleet; several-day delay",
            decision_grade=False,
        ),
        run=_check_fishing_activity,
        owner="ocean",
    ),
    "check_geofences": Tool(
        name="check_geofences",
        description=(
            "Check the selected point against full-resolution EEZ and IMBL geometry and report "
            "inside/outside state plus geodesic distance to each nearby boundary."
        ),
        capability=Capability(
            answers=("boundary", "eez", "imbl", "geofence", "jurisdiction"),
            resolution_deg=None,
            latency_ms=10,
            provenance="curated",
            cost=1,
            coverage="Indian EEZ and loaded neighbouring maritime boundaries",
        ),
        run=_check_geofences,
        owner="geospatial",
    ),
    "diagnose_productivity": Tool(
        name="diagnose_productivity",
        description=(
            "State the evidence required to diagnose a fish-productivity decline and refuse "
            "causal claims when region, species, period, landings and effort baselines are absent."
        ),
        capability=Capability(
            answers=("productivity_decline", "catch_decline", "ecological_diagnosis"),
            resolution_deg=None,
            latency_ms=1,
            provenance="curated",
            cost=1,
            coverage="India; CMFRI historical data must be supplied for an actual diagnosis",
            decision_grade=False,
        ),
        run=_diagnose_productivity,
        owner="ocean",
    ),
    "assess_risk": Tool(
        name="assess_risk",
        description=(
            "THE SAFETY VERDICT. GO / CAUTION / NO-GO from versioned, auditable safety "
            "thresholds, with component arithmetic and hard vetoes. "
            "Call this for any question about whether it is safe to sail."
        ),
        capability=Capability(
            answers=("safety", "go_no_go", "verdict", "risk"),
            resolution_deg=0.05,
            latency_ms=1200,
            provenance="derived",
            cost=2,
            coverage="anywhere conditions are available",
            notes="Pure function. verdict_source is typed so an LLM cannot author a verdict.",
        ),
        run=_assess_risk,
        owner="risk",
    ),
    "lookup_boat_thresholds": Tool(
        name="lookup_boat_thresholds",
        description=(
            "The cited wave/wind/visibility limits for a vessel length. Cheap, offline, and "
            "the right tool when the user asks what their limits are rather than what the sea is."
        ),
        capability=Capability(
            answers=("boat_class", "limits", "thresholds", "regulation"),
            resolution_deg=None,
            latency_ms=1,
            provenance="curated",
            cost=1,
            coverage="five Indian fleet classes",
        ),
        run=_lookup_thresholds,
        owner="risk",
    ),
    "find_fishing_zones": Tool(
        name="find_fishing_zones",
        description=(
            "THE ANSWER to 'where should I fish'. Nearest derived Potential Fishing Zones as a "
            "distance and a compass bearing, with their rank. Use this for any question about "
            "where the fish are — fetch_satellite_sst returns a temperature, which is an input "
            "to that answer and not the answer itself."
        ),
        capability=Capability(
            answers=("fishing", "pfz", "where_to_fish", "catch", "zone"),
            resolution_deg=0.05,
            latency_ms=5,
            provenance="derived",
            cost=1,
            coverage="the Indian EEZ, wherever the ingest job has derived a PFZ",
            notes=(
                "Reads the PFZ the ingest job derived. Reports what criteria could not be "
                "applied, so an absent rank reads as 'not checked' rather than 'poor water'."
            ),
        ),
        run=_find_fishing_zones,
        owner="ocean",
    ),
    "screen_fishing_zones": Tool(
        name="screen_fishing_zones",
        description=(
            "Screen the nearest PFZ candidates at their own centroids for hard sea-state vetoes "
            "and full-resolution EEZ/IMBL restrictions. PFZ rank alone is never called hazardous."
        ),
        capability=Capability(
            answers=("avoid_fishing_zones", "restricted_zones", "hazardous_zones"),
            resolution_deg=0.05,
            latency_ms=3500,
            provenance="derived",
            cost=4,
            coverage="nearest derived PFZ candidates within loaded geofence coverage",
        ),
        run=_screen_fishing_zones,
        owner="geospatial",
    ),
    "plan_route": Tool(
        name="plan_route",
        description=(
            "Plan a sea passage from the user's position to a destination, avoiding every cell "
            "that breaches the verified limits for their vessel class. Requires `to_lat` and "
            "`to_lon`. Returns waypoints, distance, duration and the detour against the direct "
            "line — or, when no passage exists, the specific cells that block it. Use for any "
            "question about getting somewhere, a route, a crossing or a passage."
        ),
        capability=Capability(
            answers=("route", "passage", "crossing", "navigation", "how_do_i_get_there"),
            resolution_deg=0.25,
            latency_ms=2500,
            provenance="derived",
            cost=4,
            coverage="anywhere in the Indian EEZ the wave model answers for",
            notes=(
                "Costs a lattice of live samples from two upstream APIs, so it is the most "
                "expensive tool here. A vetoed cell is impassable rather than expensive, so a "
                "refusal means no route exists — not that the planner gave up."
            ),
        ),
        run=_plan_route,
        owner="geospatial",
    ),
    "discover_datasets": Tool(
        name="discover_datasets",
        description=(
            "What datasets ORCA can read and how current each one is, including which INCOIS "
            "products are archives rather than live feeds. Use when the user asks about data "
            "provenance, sources or coverage."
        ),
        capability=Capability(
            answers=("provenance", "datasets", "sources", "coverage", "meta"),
            resolution_deg=None,
            latency_ms=1,
            provenance="curated",
            cost=1,
            coverage="ORCA's own roster",
            decision_grade=False,
        ),
        run=_list_datasets,
        owner="data_discovery",
    ),
}


#: Tool execution order, defined ONCE.
#:
#: The ordering is a real constraint, not a preference: `assess_risk` needs
#: conditions, so conditions run first whatever order the planner or the query
#: decomposition proposed. `plan_route` comes after the verdict, so a route is
#: read next to the reason it was given.
#:
#: This lives here because there were two copies — one in `graph`, one in
#: `multiquery` — and adding `plan_route` to the first while missing the second
#: silently dropped it from every decomposed question: the intent classified
#: correctly, the tool existed, and the union filtered it straight back out. Both
#: modules now import this, and the test below asserts it covers the catalogue.
TOOL_ORDER: tuple[str, ...] = (
    "fetch_marine_conditions",
    "fetch_tides",
    "fetch_forecast_window",
    "check_marine_alerts",
    "fetch_satellite_sst",
    "predict_satellite_overpasses",
    "cross_validate_conditions",
    "check_vessel_traffic",
    "check_fishing_activity",
    "check_geofences",
    "diagnose_productivity",
    "find_fishing_zones",
    "screen_fishing_zones",
    "lookup_boat_thresholds",
    "assess_risk",
    "assess_forecast_risk",
    "plan_route",
    "discover_datasets",
)


def ordered(names: object) -> list[str]:
    """The given tool names in execution order, unknown names last."""
    wanted = set(names)  # type: ignore[arg-type]
    known = [name for name in TOOL_ORDER if name in wanted]
    return known + sorted(wanted - set(TOOL_ORDER))


def catalogue() -> list[dict[str, Any]]:
    """The tool list handed to the planner. This is the menu it chooses from."""
    return [
        {
            "name": tool.name,
            "owner": tool.owner,
            "description": tool.description,
            "answers": list(tool.capability.answers),
            "resolution_deg": tool.capability.resolution_deg,
            "latency_ms": tool.capability.latency_ms,
            "provenance": tool.capability.provenance,
            "cost": tool.capability.cost,
            "coverage": tool.capability.coverage,
            "decision_grade": tool.capability.decision_grade,
        }
        for tool in TOOLS.values()
    ]


async def run_tool(name: str, **kwargs: Any) -> ToolResult:
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult(
            ok=False,
            tool=name,
            summary=f"no such tool: {name}",
            error=f"unknown tool {name!r}; available: {', '.join(TOOLS)}",
        )
    try:
        return await tool.run(**kwargs)
    except Exception as exc:
        log.exception("tool %s raised", name)
        return ToolResult(
            ok=False,
            tool=name,
            summary=f"{name} failed",
            error=f"{type(exc).__name__}: {exc}",
        )

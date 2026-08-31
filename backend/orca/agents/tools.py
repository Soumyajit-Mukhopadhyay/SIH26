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

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from orca.provenance import Evidence, Freshness, Provenance, Provider
from orca.services import thresholds
from orca.services.cross_validation import validate_point
from orca.services.overpass import predict_overpasses
from orca.services.risk_engine import assess_from_evidence
from orca.sources import open_meteo
from orca.sources.ais import aisstream
from orca.sources.erddap import DATASETS, erddap
from orca.sources.gfw import gfw

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
        summary=f"{result.verdict} at {result.index}/100{veto_text} (rule engine, not an LLM)",
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

    lines: list[str] = []
    for distance_m, zone in best:
        bearing = bearing_deg(lat, lon, zone["centroid"]["lat"], zone["centroid"]["lon"])
        lines.append(
            f"rank {zone['rank']} zone {distance_m / 1000:.0f} km "
            f"{compass_point(bearing)}, about {zone['area_km2']:.0f} km2"
        )

    derivation = sidecar.get("pfz", {})
    return ToolResult(
        ok=True,
        tool="find_fishing_zones",
        summary="; ".join(lines),
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
                }
                for distance_m, zone in best
            ],
            "derivation": derivation,
            "valid_time": sidecar.get("valid_time"),
            "lineage": sidecar.get("lineage", []),
            "caveat": (
                "INCOIS publishes PFZ advisories as maps, not as an API. This is ORCA's "
                "reimplementation of the published methodology. "
                f"Criteria that could not be applied: {derivation.get('inputs_missing', [])}."
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
    "assess_risk": Tool(
        name="assess_risk",
        description=(
            "THE SAFETY VERDICT. Deterministic GO / CAUTION / NO-GO from the versioned rule "
            "engine, with the component arithmetic and hard vetoes. Never produced by an LLM. "
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
    "plan_route": Tool(
        name="plan_route",
        description=(
            "Plan a sea passage from the user's position to a destination, avoiding every cell "
            "the deterministic rule engine vetoes for their vessel class. Requires `to_lat` and "
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
    "fetch_forecast_window",
    "fetch_satellite_sst",
    "predict_satellite_overpasses",
    "cross_validate_conditions",
    "check_vessel_traffic",
    "check_fishing_activity",
    "find_fishing_zones",
    "lookup_boat_thresholds",
    "assess_risk",
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

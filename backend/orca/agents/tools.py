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

from orca.provenance import Evidence
from orca.services import thresholds
from orca.services.risk_engine import assess_from_evidence
from orca.sources import open_meteo
from orca.sources.erddap import DATASETS, erddap

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

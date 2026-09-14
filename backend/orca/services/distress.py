"""Someone is in the water. What happens in the next ninety seconds.

## The shape of the answer

A distress report arrives as a position and a time. Four things have to come back
together, because separately none of them is actionable:

1. **Where to look** — the drift model's containment areas, which already move
   with the live current and wind.
2. **How to look** — track spacing, pattern, hours and the odds, from
   :mod:`orca.services.searchplan` and the published sweep-width tables.
3. **Who to call** — the nearest MRSC and its coordinating MRCC, with the
   telephone numbers from the National Maritime SAR Plan.
4. **How to get there** — a routed transit from that centre to the datum, costed
   on the same live sea state as everything else, with an honest ETA.

## Why the transit is routed and not divided

Distance over speed is wrong twice over. It ignores land, so a centre on the far
side of a headland reads as closer than it is; and it ignores the sea, so a leg
through a 4 m swell reads the same as a leg through a calm. ORCA already has an
A* router that costs every cell on live conditions and refuses cells outright
above a vessel's limits. This calls it with a **Coast Guard unit's** limits,
not a fishing boat's — a cutter that goes out in weather a trawler is refused in
is the whole point of a rescue service, and routing it under trawler thresholds
would have the system announce that nobody can reach the casualty.

## The clock is the message

A drift search is a race against the area growing. The response reports, side by
side, **how long the transit takes** and **how far the object drifts in that
time** — because a plan whose search area doubles before the first unit arrives
needs the datum recomputed on arrival, and saying so is more use than a tidy
number.

## What this is not

It is not a dispatch. ORCA sends nothing to anyone: it assembles the call, and a
human makes it. Nothing in this module has an outbound side, and that is
deliberate — an automated system that phones a Coast Guard station on a
misclicked map is a worse failure than one that does nothing.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from orca.provenance import Citation, utcnow
from orca.services import authorities, searchplan
from orca.services import drift as drift_service
from orca.services.thresholds import BoatClass

log = logging.getLogger(__name__)

DISTRESS_VERSION = "orca-distress-2026.09"

#: The vessel limits the transit is routed under.
#:
#: These are NOT one of the five fishing classes in `thresholds.py`. A Coast
#: Guard patrol vessel puts to sea in conditions that refuse a trawler, and
#: routing a rescue under trawler limits would produce the single most dangerous
#: output this system could emit: "no safe route to the casualty" when there is
#: one. The wave and wind figures below are the operating envelope of an
#: offshore patrol hull rather than a comfort threshold.
SAR_UNIT_CLASS = BoatClass(
    code="ICG-SAR-UNIT",
    label="Coast Guard search and rescue unit (patrol vessel)",
    loa_min_m=30.0,
    loa_max_m=110.0,
    max_wave_m=6.0,
    max_wind_kn=47.0,
    min_visibility_km=0.2,
    source_citation=Citation(
        label="ORCA transit envelope for an ICG patrol vessel",
        provider="ORCA",
        quote=(
            "Sea state 6 (about 6 m significant height) and storm-force wind. Radar-equipped, "
            "so the visibility floor is navigational rather than visual. Set so a rescue is not "
            "refused a route under thresholds written for fishing boats."
        ),
    ),
    notes=(
        "A rescue unit is routed on what it can survive, not on what would be comfortable. "
        "Above these figures the router still refuses, and a refusal here is real: it means "
        "the sea is beyond a patrol hull and the answer is aviation or waiting."
    ),
)

#: How much of the transit is spent not transiting. A centre does not sail the
#: instant the phone rings: the duty crew is called, the boat is manned, lines
#: are let go. Half an hour is optimistic for a manned unit and wildly optimistic
#: for one alongside overnight, and it is stated rather than folded silently into
#: the ETA.
LAUNCH_DELAY_H = 0.5


def _condition(samples: list[dict[str, Any]], key: str) -> float | None:
    """The first non-null value of ``key`` across the drift's field samples.

    The drift model samples the environment as it integrates, so those samples
    are already the conditions at the datum — fetching them again would be a
    second round trip for numbers we hold.
    """
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


async def _conditions_at(lat: float, lon: float) -> dict[str, float | None]:
    """Wave, wind and visibility at the datum, for the search plan."""
    from orca.sources.open_meteo import conditions_at

    try:
        evidence = await conditions_at(lat, lon)
    except Exception as exc:  # noqa: BLE001
        log.warning("distress: conditions unavailable at %.3f,%.3f: %s", lat, lon, exc)
        return {"wave_m": None, "wind_kn": None, "visibility_km": None}

    def value(name: str) -> float | None:
        item = evidence.get(name)
        if item is None or item.value is None:
            return None
        return float(item.value)

    visibility_m = value("visibility")
    return {
        "wave_m": value("wave_height"),
        "wind_kn": value("wind_speed"),
        "visibility_km": None if visibility_m is None else visibility_m / 1000.0,
    }


async def respond(
    *,
    lat: float,
    lon: float,
    hours_since: float = 1.0,
    object_class: str = "PIW-VERTICAL",
    unit_code: str = "ICG-FPV",
    units: int = 1,
    particles: int = 1200,
    seed: int | None = None,
    route_transit: bool = True,
) -> dict[str, Any]:
    """Everything a duty officer needs, assembled from one position and one time."""
    raised_at = utcnow()

    result = await drift_service.simulate(
        lat=lat,
        lon=lon,
        hours=hours_since,
        object_class=object_class,
        particles=particles,
        seed=seed,
    )
    drift_report = drift_service.describe(result)

    # The 95% containment area is what the search is planned against. Planning
    # against the 50% would halve the hours and quietly throw away half the
    # probability of the object being in there at all.
    areas = drift_report["areas"]
    outer = areas[-1]
    datum = drift_report["mean_position"]  # [lon, lat]
    datum_lat, datum_lon = float(datum[1]), float(datum[0])

    conditions = await _conditions_at(datum_lat, datum_lon)

    plan = searchplan.plan_search(
        area_km2=float(outer["area_km2"]),
        drift_object_class=object_class,
        unit_code=unit_code,
        units=units,
        visibility_km=conditions["visibility_km"],
        wind_kn=conditions["wind_kn"],
        wave_m=conditions["wave_m"],
    )

    # --- who is responsible ------------------------------------------------
    contacts = authorities.nearest(datum_lat, datum_lon, limit=3)
    primary, primary_km, primary_bearing = contacts[0]
    mrcc = authorities.responsible_mrcc(datum_lat, datum_lon)

    notify = [authorities.describe_contact(c, km, bearing) for c, km, bearing in contacts]
    if mrcc.name not in {entry["name"] for entry in notify}:
        mrcc_km = authorities.nearest(datum_lat, datum_lon, limit=len(authorities.CENTRES))
        for centre, km, bearing in mrcc_km:
            if centre.name == mrcc.name:
                notify.append(authorities.describe_contact(centre, km, bearing))
                break

    # --- getting there -----------------------------------------------------
    transit: dict[str, Any] = {"routed": False}
    if route_transit:
        transit = await _transit(
            centre_lat=primary.lat,
            centre_lon=primary.lon,
            datum_lat=datum_lat,
            datum_lon=datum_lon,
            unit_code=unit_code,
        )

    # --- how much the box grows while they steam ---------------------------
    growth = _growth_while_transiting(
        drift_report=drift_report,
        hours_since=hours_since,
        transit_hours=transit.get("total_hours"),
    )

    return {
        "distress_version": DISTRESS_VERSION,
        "raised_at": raised_at.isoformat(),
        "incident": {
            "last_known_position": {"lat": lat, "lon": lon},
            "hours_since_last_known": hours_since,
            "object_class": drift_report["object_class"],
            "datum": {"lat": round(datum_lat, 4), "lon": round(datum_lon, 4)},
            "datum_note": (
                "The datum is the drift cloud's mean position, not a prediction of where the "
                "object is. Searching the datum alone is the classic way to miss."
            ),
        },
        "search_area": {
            "containment": [
                {
                    "fraction": area.get("fraction"),
                    "area_km2": area.get("area_km2"),
                    "ring": area.get("ring"),
                }
                for area in areas
            ],
            "displacement_km": drift_report["displacement_km"],
            "bearing": drift_report["bearing"],
            "spread_sources": drift_report.get("spread_sources"),
        },
        "search_plan": plan,
        "conditions_at_datum": conditions,
        "notify": notify,
        "coordinating_mrcc": mrcc.name,
        "first_call": {
            "number": authorities.MSAR_DISTRESS_NUMBER,
            "why": (
                f"{authorities.MSAR_DISTRESS_NUMBER} is the nationwide maritime distress line and "
                f"reaches the responsible centre directly — here {primary.name}, "
                f"{primary_km:.0f} km away. Dial it first; the direct landlines below are for "
                "the follow-up, and the Inmarsat-C IDs are for when the mobile network is gone."
            ),
            "nearest_centre": primary.name,
            "nearest_centre_km": primary_km,
            "nearest_centre_bearing_deg": primary_bearing,
        },
        "transit": transit,
        "datum_growth": growth,
        "not_a_dispatch": (
            "ORCA has notified nobody. This assembles the call — position, drift, search plan and "
            "the correct centre with its numbers — for a human to make. There is no outbound "
            "path from this endpoint by design."
        ),
        "citations": [
            authorities.NMSAR_CITATION.model_dump(mode="json"),
            authorities.POSITION_CITATION.model_dump(mode="json"),
        ],
    }


#: Roughly how many lattice cells the transit leg should span.
#:
#: Eight, not twelve. At twelve a short leg built a 180-225 node lattice, and
#: three transits in a row exhausted Open-Meteo's per-minute budget — at which
#: point every node came back uncosted and the router reported "there is no
#: water in this corridor at all", which is a true statement about the lattice
#: and a false one about the sea. A rescue ETA is not the place to spend the
#: call budget on grid resolution.
_CELLS_ALONG_LEG = 8

#: Bounds on the lattice step. The floor is navigational: below about 4 km the
#: wave model has no independent information to give, so a finer grid would be
#: interpolating its own interpolation. The ceiling is the router's own default.
_MIN_STEP_DEG = 0.04
_MAX_STEP_DEG = 0.25


def _lattice_for_leg(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> tuple[float, float]:
    """Lattice step and corridor sized to this leg, not to a default.

    The router's 0.25 deg default is ~28 km a cell, which is right for a
    day-long fishing passage and badly wrong for a 16 km dash to a casualty: the
    first routed transit came back 28.5 NM against a 14.7 NM direct line, a 94%
    overhead that was pure grid quantisation and not a single metre of
    avoidance. An ETA inflated to twice the truth is worse than no ETA, because
    a coordinator would launch the wrong unit on it.
    """
    span_deg = max(abs(a_lat - b_lat), abs(a_lon - b_lon) * math.cos(math.radians(a_lat)))
    step = min(_MAX_STEP_DEG, max(_MIN_STEP_DEG, span_deg / _CELLS_ALONG_LEG))
    # Corridor wide enough to go round an obstacle, proportional to the leg.
    # A fixed 1.1 deg on a 16 km leg samples a 250 km box to route 16 km.
    corridor = min(router_default_corridor(), max(3.0 * step, span_deg * 0.6))
    return round(step, 4), round(corridor, 4)


def router_default_corridor() -> float:
    from orca.services.router import DEFAULT_CORRIDOR_DEG

    return DEFAULT_CORRIDOR_DEG


async def _transit(
    *,
    centre_lat: float,
    centre_lon: float,
    datum_lat: float,
    datum_lon: float,
    unit_code: str,
) -> dict[str, Any]:
    """Route a rescue unit from its centre to the datum, and time it honestly."""
    from orca.services import router

    unit = searchplan.UNITS_BY_CODE.get(unit_code) or searchplan.UNITS_BY_CODE["ICG-FPV"]
    step, corridor = _lattice_for_leg(centre_lat, centre_lon, datum_lat, datum_lon)

    try:
        route = await router.plan(
            start=(centre_lat, centre_lon),
            goal=(datum_lat, datum_lon),
            boat_class=SAR_UNIT_CLASS,
            speed_kn=unit.transit_speed_kn,
            step_deg=step,
            corridor_deg=corridor,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("distress: transit routing failed: %s", exc)
        return {
            "routed": False,
            "reason": f"Routing failed ({type(exc).__name__}). Fall back to a direct bearing.",
        }

    if not route.get("ok"):
        # A refusal and a failure to sample are completely different answers and
        # must not be presented as the same one. "No safe route" means the sea is
        # beyond the hull. "Could not cost the lattice" means we do not know —
        # and a duty officer still needs a number, so fall back to the straight
        # line and label it loudly as uncosted.
        return {
            **_direct_line_transit(centre_lat, centre_lon, datum_lat, datum_lon, unit),
            "reason": route.get("reason", "no route"),
            "router": route,
            "what_a_refusal_means": (
                "The router refused under Coast Guard limits, not fishing limits. If it refused "
                "because cells were genuinely above a patrol hull's envelope, that is a real "
                "answer and the option is aviation. If it refused because it could not sample "
                "the corridor, the figures below are a great-circle fallback and nothing has "
                "been checked about the water in between."
            ),
        }

    distance_nm = float(route.get("distance_nm") or 0.0)
    steaming_h = distance_nm / unit.transit_speed_kn if unit.transit_speed_kn else math.inf
    total_h = LAUNCH_DELAY_H + steaming_h

    return {
        "routed": True,
        "unit": unit.label,
        "transit_speed_kn": unit.transit_speed_kn,
        "distance_nm": round(distance_nm, 1),
        "distance_km": round(distance_nm * searchplan.KM_PER_NM, 1),
        "launch_delay_h": LAUNCH_DELAY_H,
        "steaming_hours": round(steaming_h, 2),
        "total_hours": round(total_h, 2),
        "eta_note": (
            f"{LAUNCH_DELAY_H * 60:.0f} min to call out the crew and slip, then "
            f"{steaming_h:.1f} h steaming at {unit.transit_speed_kn:.0f} kn. The launch delay is "
            "stated separately because it is the part a duty officer can actually shorten."
        ),
        "path": route.get("path"),
        "waypoints": route.get("waypoints"),
        "direct_nm": route.get("direct_nm"),
        "detour_pct": route.get("detour_pct"),
        "worst_verdict": route.get("worst_verdict"),
        "why_this_route": route.get("why_this_route"),
        "degraded": route.get("degraded"),
        "router_note": (
            "Costed on live sea state under Coast Guard limits, not fishing-boat thresholds. "
            "The route may be longer than the direct line because the planner detoured around "
            "the worst cells — that detour is the reason to trust the ETA."
        ),
    }


def _direct_line_transit(
    centre_lat: float,
    centre_lon: float,
    datum_lat: float,
    datum_lon: float,
    unit: searchplan.SearchUnit,
) -> dict[str, Any]:
    """Great-circle distance and time, when the corridor could not be costed.

    Deliberately marked ``routed: False``. It is an ETA, not a route: it crosses
    land without noticing and takes no account of the sea state. It exists
    because "we could not check" is not an acceptable answer to "how long until
    somebody gets here", and the alternative — returning nothing — leaves the
    coordinator with less than they started with.
    """
    distance_km = authorities.haversine_km(centre_lat, centre_lon, datum_lat, datum_lon)
    distance_nm = distance_km / searchplan.KM_PER_NM
    steaming_h = distance_nm / unit.transit_speed_kn if unit.transit_speed_kn else math.inf
    return {
        "routed": False,
        "estimate": "great-circle",
        "unit": unit.label,
        "transit_speed_kn": unit.transit_speed_kn,
        "distance_nm": round(distance_nm, 1),
        "distance_km": round(distance_km, 1),
        "launch_delay_h": LAUNCH_DELAY_H,
        "steaming_hours": round(steaming_h, 2),
        "total_hours": round(LAUNCH_DELAY_H + steaming_h, 2),
        "eta_note": (
            f"UNCOSTED great-circle estimate: {distance_nm:.1f} NM at "
            f"{unit.transit_speed_kn:.0f} kn plus {LAUNCH_DELAY_H * 60:.0f} min to slip. This "
            "line has NOT been checked for land or for sea state, so treat it as a floor on the "
            "time, never as a track to steer."
        ),
    }


def _growth_while_transiting(
    *,
    drift_report: dict[str, Any],
    hours_since: float,
    transit_hours: float | None,
) -> dict[str, Any]:
    """How much bigger the box gets before anyone arrives.

    The estimate is deliberately crude and labelled as such: containment area
    grows roughly with the square of elapsed time, because the spread is a
    random walk in each axis. Re-running the full particle model for the arrival
    time would be more exact, and it is what the coordinator should do on
    arrival — this is the number that tells them they have to.
    """
    if transit_hours is None or transit_hours <= 0 or hours_since <= 0:
        return {
            "estimated": False,
            "why": "No transit time available, so no growth estimate.",
        }

    outer = drift_report["areas"][-1]
    now_km2 = float(outer["area_km2"])
    on_arrival_h = hours_since + transit_hours
    factor = (on_arrival_h / hours_since) ** 2
    arrival_km2 = now_km2 * factor

    return {
        "estimated": True,
        "area_now_km2": round(now_km2, 1),
        "hours_at_arrival": round(on_arrival_h, 2),
        "area_on_arrival_km2": round(arrival_km2, 1),
        "growth_factor": round(factor, 2),
        "why_it_matters": (
            f"The 95% box is {now_km2:.0f} km² now and about {arrival_km2:.0f} km² when the first "
            f"unit arrives — {factor:.1f}x. Every search plan above is computed for the box as it "
            "is today; recompute the datum on arrival rather than steaming to this one."
        ),
        "method": (
            "Containment area scales roughly as elapsed time squared, because the spread is a "
            "random walk in each axis and area goes as the square of the radius. This is an "
            "order-of-magnitude figure. Re-running the particle model at the arrival time is "
            "exact and is what the coordinator should do."
        ),
    }

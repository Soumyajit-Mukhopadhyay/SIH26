"""The advisory a fisheries officer actually issues.

## Why a board and not a verdict

ORCA answers "is it safe for my boat, here". A District Fisheries Officer has a
different question and no surface for it: **which stretches of my coast are
unsafe today, and for whom.** They do not issue one verdict; they issue an
advisory that names classes and stretches, and the boats that obey it are the
whole fleet rather than one caller.

Running the existing rule engine at every harbour, for every boat class, produces
exactly that. No new model, no new thresholds, no new data — the same
deterministic engine that answers a fisherman's point query, run across the
register instead of at one coordinate.

## The insight this surfaces and nothing else does

The interesting thing is not any single cell. It is that **the boundary between
classes moves up and down the coast**, and it moves for a reason — swell arriving
on one shore and not the other, a low over one basin. "Traditional craft should
not sail anywhere between Kochi and Mangaluru today" is the actual form of a
fisheries advisory, and it only exists once verdicts are laid out in coastal
order. `stretches()` does that: contiguous runs of the same verdict, named by
their endpoints.

## Coastal order is not alphabetical and not latitude

A board sorted by name interleaves Gujarat and Tamil Nadu. A board sorted by
latitude puts Porbandar next to Digha, which are on opposite coasts a thousand
kilometres apart. Harbours are therefore ordered **along the shore**: down the
west coast from Kutch to Kanyakumari, then up the east coast to the Sundarbans —
the order a boat would pass them, and the order an advisory reads in.

## The board is cached, but not for the reason you would guess

It is **not** protecting the Open-Meteo quota. `sample_conditions` already
keeps a 20-minute per-point cache, so a second viewer within that window was
always costing zero upstream calls. This cache sits above that and buys three
smaller things:

- **Work, not quota.** A board runs the rule engine 300 times (60 harbours x 5
  classes). On a 1 GB instance that is worth not repeating for every viewer.
- **An honest timestamp.** The response carries `cache.age_s`, so a coastal
  officer can see whether a verdict was computed now or nine minutes ago
  rather than assuming it is live.
- **A refresh button that refreshes.** `?fresh=true` bypasses this cache AND
  the sampler's, which is the only way to actually get new numbers inside the
  20-minute window.

Ten minutes is not arbitrary: the models behind these fields publish hourly,
so a ten-minute-old board is the same board.

The cache is per-process and in memory. One machine serves this, so that is
enough; if ORCA ever runs more than one worker they will each keep their own,
which costs a little repeated work but cannot serve anything stale or wrong.

## Two requests, not a hundred and twenty

Conditions come from `sample_conditions`, which batches many points into one
upstream call and preserves order. Sixty harbours cost two requests — one to the
marine endpoint for wave height, one to the forecast endpoint for wind,
visibility and CAPE — rather than one per harbour per API.

The marine endpoint is queried with `cell_selection="sea"`, so it answers from
the nearest sea cell to a harbour; the forecast endpoint uses
`cell_selection="land"`, so wind and visibility are the values at the coast
rather than twenty miles out. That is the same pairing the router and every
point verdict already use. A board built on it is about conditions at the
harbour mouth, which is the right question for "should the fleet sail", and it
is not an offshore forecast.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from orca.provenance import utcnow
from orca.services.harbours import HARBOURS, PMMSY_CITATION, POSITION_CITATION, Harbour
from orca.services.risk_engine import RiskResult, assess
from orca.services.thresholds import BOAT_CLASSES

log = logging.getLogger(__name__)

BOARD_VERSION = "orca-harbour-board-2026.09"

#: How long a computed board stays good for, in seconds.
#:
#: The wave, wind and visibility models behind it publish hourly, so anything
#: under an hour returns the same numbers. Ten minutes is short enough that the
#: reported `cache.age_s` never surprises anyone, and long enough to spare the
#: 300 rule-engine evaluations a board costs.
BOARD_TTL_S = 600.0

#: (state, classes) -> (monotonic time computed, rows). Deliberately keyed on
#: the query too: a Kerala-only board is a different, cheaper request than the
#: whole coast, and serving one from the other's entry would be wrong.
_CACHE: dict[tuple[str | None, tuple[str, ...] | None], tuple[float, list[HarbourVerdicts]]] = {}


def _cache_key(
    state: str | None, classes: list[str] | None
) -> tuple[str | None, tuple[str, ...] | None]:
    return (state.lower() if state else None, tuple(sorted(classes)) if classes else None)


def cache_age_s(state: str | None = None, classes: list[str] | None = None) -> float | None:
    """Seconds since this board was computed, or None if it is not cached."""
    hit = _CACHE.get(_cache_key(state, classes))
    return None if hit is None else time.monotonic() - hit[0]


def clear_cache() -> None:
    """Drop every cached board. Used by tests, and safe at runtime."""
    _CACHE.clear()


#: Verdicts in descending severity, so a "worst first" sort is a lookup.
_SEVERITY = {"NO-GO": 0, "UNVERIFIABLE": 1, "CAUTION": 2, "GO": 3}


@dataclass(frozen=True, slots=True)
class HarbourVerdicts:
    """One harbour, and what the engine says for each class there."""

    harbour: Harbour
    #: Conditions as sampled. None means the upstream had nothing.
    wave_m: float | None
    wind_kn: float | None
    visibility_km: float | None
    cape_j_kg: float | None
    #: boat class code -> the engine's result
    verdicts: dict[str, RiskResult]

    @property
    def on_water(self) -> bool:
        """Whether the wave model answered here at all.

        With `cell_selection="sea"` the Marine API snaps a coastal request to
        the nearest sea cell, so a null here does not mean "this point is on
        land" — it means no sea cell was reachable at all. Measured: a harbour
        town returns a wave height, Nashik 120 km inland returns null. A null is
        therefore a position that has drifted well away from the coast, which is
        worth reporting as a registry problem rather than as weather.
        """
        return self.wave_m is not None

    @property
    def worst(self) -> str:
        """The most severe verdict across all classes at this harbour."""
        return min(
            (result.verdict for result in self.verdicts.values()),
            key=lambda v: _SEVERITY.get(v, 1),
            default="UNVERIFIABLE",
        )


async def board(
    *,
    state: str | None = None,
    classes: list[str] | None = None,
    fresh: bool = False,
) -> list[HarbourVerdicts]:
    """Run the rule engine at every harbour, for every class, in coastal order.

    Served from :data:`_CACHE` when a board for the same query was computed
    less than :data:`BOARD_TTL_S` ago. Pass ``fresh=True`` to recompute — that
    is what the refresh button does, and it is the only way to spend the 60
    upstream calls deliberately.
    """
    from orca.sources.open_meteo import sample_conditions

    key = _cache_key(state, classes)
    if not fresh:
        hit = _CACHE.get(key)
        if hit is not None and time.monotonic() - hit[0] < BOARD_TTL_S:
            log.debug("harbour board served from cache (%s)", key)
            return hit[1]

    selected = [h for h in HARBOURS if state is None or h.state.lower() == state.lower()]
    if not selected:
        return []

    wanted = [b for b in BOAT_CLASSES if classes is None or b.code in classes]

    # `fresh` has to reach the sampler too. It keeps its own 20-minute per-point
    # cache, so bypassing only the board cache would recompute the verdicts from
    # the very same numbers and the refresh button would be decorative.
    samples = await sample_conditions(
        [h.lat for h in selected],
        [h.lon for h in selected],
        use_cache=not fresh,
    )

    out: list[HarbourVerdicts] = []
    for harbour, sample in zip(selected, samples, strict=True):
        wave = sample.get("wave_height")
        wind = sample.get("wind_speed")
        visibility_m = sample.get("visibility")
        cape = sample.get("convective_energy")
        visibility_km = None if visibility_m is None else float(visibility_m) / 1000.0

        verdicts = {
            boat.code: assess(
                wave_m=wave,
                wind_kn=wind,
                visibility_km=visibility_km,
                cape_j_kg=cape,
                boat_class=boat,
            )
            for boat in wanted
        }
        out.append(
            HarbourVerdicts(
                harbour=harbour,
                wave_m=wave,
                wind_kn=wind,
                visibility_km=visibility_km,
                cape_j_kg=cape,
                verdicts=verdicts,
            )
        )

    # Cached only on success. An empty result is not worth holding for ten
    # minutes -- it usually means the upstream was briefly down, and caching it
    # would turn a blip into a ten-minute outage.
    if out:
        _CACHE[key] = (time.monotonic(), out)
    return out


def stretches(rows: list[HarbourVerdicts], boat_class: str) -> list[dict[str, Any]]:
    """Contiguous runs of the same verdict along the coast, for one class.

    This is the advisory sentence. A run of four NO-GO harbours between Kochi
    and Mangaluru is "traditional craft should not sail anywhere between Kochi
    and Mangaluru", which is a thing an officer can broadcast — where the same
    information as four separate rows is not.

    Runs of one harbour are kept. A single isolated NO-GO is not noise: it is
    usually a harbour with a bar or a shallow approach, and dropping it because
    it is short would drop exactly the cases worth knowing.
    """
    ordered = [row for row in rows if row.on_water]
    runs: list[dict[str, Any]] = []
    for row in ordered:
        result = row.verdicts.get(boat_class)
        if result is None:
            continue
        verdict = result.verdict
        if runs and runs[-1]["verdict"] == verdict and runs[-1]["coast"] == row.harbour.coast:
            runs[-1]["to"] = row.harbour.name
            runs[-1]["harbours"].append(row.harbour.name)
        else:
            runs.append(
                {
                    "verdict": verdict,
                    "coast": row.harbour.coast,
                    "from": row.harbour.name,
                    "to": row.harbour.name,
                    "harbours": [row.harbour.name],
                }
            )

    for run in runs:
        count = len(run["harbours"])
        if count == 1:
            run["sentence"] = f"{run['verdict']} at {run['from']}"
        else:
            run["sentence"] = (
                f"{run['verdict']} at all {count} harbours from {run['from']} to {run['to']}"
            )
    return runs


def describe(rows: list[HarbourVerdicts], *, age_s: float | None = None) -> dict[str, Any]:
    """The board, shaped for an API response and for a table.

    ``age_s`` is how old the underlying computation is. It is reported rather
    than hidden: a board is cached for up to ten minutes, and a coastal officer
    reading a verdict deserves to know whether it was computed now or nine
    minutes ago.
    """
    classes = [
        {"code": b.code, "label": b.label, "max_wave_m": b.max_wave_m, "max_wind_kn": b.max_wind_kn}
        for b in BOAT_CLASSES
        if rows and b.code in rows[0].verdicts
    ]

    on_water = [row for row in rows if row.on_water]
    dry = [row for row in rows if not row.on_water]

    counts: dict[str, dict[str, int]] = {}
    for entry in classes:
        tally: dict[str, int] = {}
        for row in on_water:
            result = row.verdicts.get(entry["code"])
            if result is not None:
                tally[result.verdict] = tally.get(result.verdict, 0) + 1
        counts[entry["code"]] = tally

    return {
        "board_version": BOARD_VERSION,
        "generated_at": utcnow().isoformat(),
        "cache": {
            "age_s": None if age_s is None else round(age_s, 1),
            "ttl_s": BOARD_TTL_S,
            "hit": age_s is not None and age_s > 1.0,
            "note": (
                f"Boards are computed at most once every {BOARD_TTL_S / 60:.0f} minutes. The "
                "models behind them publish hourly, so a cached board is the same board. Add "
                "?fresh=true to recompute, which also bypasses the sampler's own 20-minute "
                "per-point cache."
            ),
        },
        "harbours_assessed": len(on_water),
        "classes": classes,
        "rows": [
            {
                "name": row.harbour.name,
                "district": row.harbour.district,
                "state": row.harbour.state,
                "coast": row.harbour.coast,
                "lat": row.harbour.lat,
                "lon": row.harbour.lon,
                "conditions": {
                    "wave_m": row.wave_m,
                    "wind_kn": None if row.wind_kn is None else round(row.wind_kn, 1),
                    "visibility_km": (
                        None if row.visibility_km is None else round(row.visibility_km, 1)
                    ),
                    "cape_j_kg": row.cape_j_kg,
                },
                "worst": row.worst,
                "verdicts": {
                    code: {
                        "verdict": result.verdict,
                        "index": result.index,
                        "confidence": result.confidence,
                        # The first veto is the actionable half of a NO-GO: an
                        # officer needs to know it is the swell rather than the
                        # wind, because those have different durations.
                        "reason": result.vetoes[0] if result.vetoes else None,
                    }
                    for code, result in row.verdicts.items()
                },
            }
            for row in on_water
        ],
        "stretches": {entry["code"]: stretches(on_water, entry["code"]) for entry in classes},
        "counts": counts,
        "not_assessed": [
            {
                "name": row.harbour.name,
                "state": row.harbour.state,
                "why": (
                    "The wave model returned nothing here, which is its land mask. The recorded "
                    "position is too far inside the harbour for a marine grid to answer."
                ),
            }
            for row in dry
        ],
        "how_to_read": (
            "One row per harbour in coastal order — down the west coast from Kutch, round "
            "Kanyakumari, up the east coast to the Sundarbans. One column per boat class. The "
            "column where the verdict changes is the advisory: everything smaller than that "
            "class should stay in today."
        ),
        "what_this_is_not": (
            "Not an official advisory. ORCA's rule engine applied to model conditions at "
            "positions ORCA geocoded. The State Fisheries Department and IMD issue the advisory "
            "that has legal force, and this must never be presented as replacing one."
        ),
        "citations": [
            PMMSY_CITATION.model_dump(mode="json"),
            POSITION_CITATION.model_dump(mode="json"),
        ],
    }

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
from dataclasses import dataclass
from typing import Any

from orca.provenance import utcnow
from orca.services.harbours import HARBOURS, PMMSY_CITATION, POSITION_CITATION, Harbour
from orca.services.risk_engine import RiskResult, assess
from orca.services.thresholds import BOAT_CLASSES

log = logging.getLogger(__name__)

BOARD_VERSION = "orca-harbour-board-2026.09"

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
) -> list[HarbourVerdicts]:
    """Run the rule engine at every harbour, for every class, in coastal order."""
    from orca.sources.open_meteo import sample_conditions

    selected = [h for h in HARBOURS if state is None or h.state.lower() == state.lower()]
    if not selected:
        return []

    wanted = [b for b in BOAT_CLASSES if classes is None or b.code in classes]

    samples = await sample_conditions(
        [h.lat for h in selected],
        [h.lon for h in selected],
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


def describe(rows: list[HarbourVerdicts]) -> dict[str, Any]:
    """The board, shaped for an API response and for a table."""
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

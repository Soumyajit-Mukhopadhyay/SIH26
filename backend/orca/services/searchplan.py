"""Turning a drift cloud into a search a crew can actually fly or steam.

## What was wrong with "here is a 300 km² ellipse"

A drift model tells you where the object *could* be. That is not yet a search.
A search coordinator needs to know how far apart to run the legs, which pattern
to run, how long one unit will take to cover the area, and what the odds are of
walking past the target anyway. Handing over an area and stopping is the part
that made the old output feel like a picture rather than a plan.

## Everything here is from the published tables, not invented

Two numbers do all the work, and both are looked up, not guessed:

**Sweep width (W)** — how far either side of its track a unit can actually see
the object. Table H-19 of the *U.S. Coast Guard Addendum to the National SAR
Supplement* (COMDTINST M16130.2F), "Uncorrected Visual Sweep Width — Vessels and
Boats", transcribed below in full for the rows ORCA can produce a target class
for. It is indexed on **meteorological visibility**, which ORCA fetches live.

**Weather correction (fw)** — Table H-10 of the same document, which the text
notes corresponds to IAMSAR Manual Volume II Table N-7. It is indexed on **wind
and sea state**, which ORCA also fetches live. For a person in the water it is
brutal: above 25 knots or 5 feet of sea, sweep width is cut to a **quarter**.

That is what makes this plan move with conditions instead of sitting still. The
same 200 km² box off Chennai takes one unit three hours to search in a calm and
twelve in a fresh breeze, because the legs have to be four times closer together.

## The relations, verbatim

- ``C = W / S`` for parallel tracks (H.5.4). Coverage is sweep width over track
  spacing.
- ``C = (W x V x T) / A`` (H.5.5.2), with W in NM, V in knots, T in hours and A
  in square nautical miles. This one holds "regardless of the type of search
  pattern or lack of one, so long as the searching effort is spread over the area
  covered in a reasonably uniform fashion".
- ``POS = POC x POD`` (H.5.6.1). POD alone is not the measure of a search.
- Initial searches are planned at **coverage no less than 1.0** (3.1.7 / H
  guidance for initial search areas).
- When the search object cannot be determined, the default is a **20-foot power
  boat**.
- For persons in the water, **0.1 NM is the lower practical limit** on track
  spacing for accurate surface navigation.

## The one modelled step, named as such

POD against coverage is published as a *curve*, not a table, so it cannot be
transcribed. The "normal search conditions" curve is the exponential detection
function ``POD = 1 - exp(-C)`` — 63% at C = 1.0. ORCA uses that and labels it a
model. The "ideal conditions" curve is higher and ORCA does not claim it: ideal
means perfect parallel sweeps relative to the object, which is not what a boat
in a 2 m sea is doing.

## What this module does not cover

**Aircraft.** Sweep widths for fixed-wing and helicopter SRUs are Tables H-11 to
H-18, indexed on search altitude as well as visibility, and they are not
transcribed here. An ICG Dornier is by far the fastest way to cover a large area,
and a plan that ignores it understates what is available — so the output says so
explicitly rather than implying surface units are the whole answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from orca.provenance import Citation, Provider

SRUType = Literal["vessel", "small_boat"]

USCG_SWEEP_CITATION = Citation(
    label="Uncorrected Visual Sweep Width — Vessels and Boats (Table H-19)",
    provider=Provider.NOAA,
    url="https://rdept.cgaux.org/documents/ManualsTemp/USCG_SAR_Addendum.pdf",
    identifier="COMDTINST M16130.2F, Appendix H, Table H-19",
    quote=(
        "Person in Water, Vessel SRU: 0.3 / 0.4 / 0.5 / 0.5 / 0.5 / 0.5 NM at visibility "
        "1 / 3 / 5 / 10 / 15 / 20 NM."
    ),
)

USCG_WEATHER_CITATION = Citation(
    label="Weather Correction Factor (Table H-10)",
    provider=Provider.NOAA,
    url="https://rdept.cgaux.org/documents/ManualsTemp/USCG_SAR_Addendum.pdf",
    identifier="COMDTINST M16130.2F, Appendix H, Table H-10 — corresponds to IAMSAR Vol II Table N-7",
    quote=(
        "Winds > 25 kts or seas > 5 ft: 0.25 for person in water, raft or boat < 30 ft; "
        "0.9 for other search objects."
    ),
)

COVERAGE_CITATION = Citation(
    label="Coverage factor and its relation to sweep width, speed, endurance and area",
    provider=Provider.NOAA,
    url="https://rdept.cgaux.org/documents/ManualsTemp/USCG_SAR_Addendum.pdf",
    identifier="COMDTINST M16130.2F, Appendix H, H.5.4 and H.5.5.2",
    quote=(
        "Coverage is computed as the ratio of the corrected sweep width to the track spacing "
        "(C = W/S). ... Coverage Factor (C) = (W x V x T) / A."
    ),
)

#: The visibility columns of Table H-19, in nautical miles.
VISIBILITY_COLUMNS_NM: tuple[float, ...] = (1.0, 3.0, 5.0, 10.0, 15.0, 20.0)

#: Table H-19, transcribed. Each row is (vessel SRU, small boat SRU) across the
#: six visibility columns above. Only the rows ORCA can map a target class onto
#: are carried — transcribing the 25-row table in full would invite selecting a
#: row ORCA has no way to identify from a distress report.
_SWEEP_WIDTH_NM: dict[str, dict[SRUType, tuple[float, ...]]] = {
    "person_in_water": {
        "vessel": (0.3, 0.4, 0.5, 0.5, 0.5, 0.5),
        "small_boat": (0.2, 0.2, 0.3, 0.3, 0.3, 0.3),
    },
    "raft_1_person": {
        "vessel": (0.9, 1.8, 2.3, 3.1, 3.4, 3.7),
        "small_boat": (0.7, 1.3, 1.7, 2.3, 2.6, 2.7),
    },
    "raft_4_person": {
        "vessel": (1.0, 2.2, 3.0, 4.0, 4.6, 5.0),
        "small_boat": (0.7, 1.7, 2.2, 3.1, 3.5, 3.9),
    },
    "raft_6_person": {
        "vessel": (1.1, 2.5, 3.4, 4.7, 5.5, 6.0),
        "small_boat": (0.8, 1.9, 2.6, 3.6, 4.3, 4.7),
    },
    # A kattumaram or a small FRP boat. 15 ft = 4.6 m.
    "boat_under_15ft": {
        "vessel": (0.5, 1.1, 1.4, 1.9, 2.1, 2.3),
        "small_boat": (0.4, 0.8, 1.1, 1.5, 1.6, 1.8),
    },
    # 20 ft = 6.1 m. The USCG default when the object is unknown.
    "boat_20ft": {
        "vessel": (1.0, 2.0, 2.9, 4.3, 5.2, 5.8),
        "small_boat": (0.8, 1.5, 2.2, 3.3, 4.0, 4.5),
    },
    # 33 ft = 10 m. Covers the mechanised inshore fleet.
    "boat_33ft": {
        "vessel": (1.1, 2.5, 3.8, 6.1, 7.7, 8.8),
        "small_boat": (0.8, 1.9, 2.9, 4.7, 5.9, 6.8),
    },
    # 53 ft = 16 m.
    "boat_53ft": {
        "vessel": (1.2, 3.1, 5.1, 9.1, 12.1, 14.4),
        "small_boat": (0.9, 2.4, 3.9, 7.0, 9.3, 11.1),
    },
}

#: USCG default when the search object cannot be determined (H, initial search).
DEFAULT_SEARCH_OBJECT = "boat_20ft"

#: How ORCA's drift object classes map onto Table H-19 rows. The drift module
#: classifies by leeway behaviour; the sweep table classifies by how visible a
#: thing is. They are not the same axis, so the mapping is explicit.
DRIFT_CLASS_TO_SEARCH_OBJECT: dict[str, str] = {
    "PIW-VERTICAL": "person_in_water",
    "PIW-SURVIVAL-SUIT": "person_in_water",
    "LIFE-RAFT-CANOPY": "raft_4_person",
    # Under 10 m spans the table's 15 ft and 33 ft rows. The smaller row is
    # taken because it gives the narrower sweep width, and the cautious error in
    # a search plan is the one that runs the legs too close together.
    "FISHING-VESSEL-SMALL": "boat_under_15ft",
    "FISHING-VESSEL-MEDIUM": "boat_33ft",
    # Wreckage has no row of its own. A swamped hull sits low and is easy to
    # lose in a sea, so the smallest boat row is the honest stand-in — and
    # `substituted_default` flags that a substitution happened.
    "DEBRIS": "boat_under_15ft",
}

#: Lower practical limit on track spacing for surface navigation (H.5.4.4).
MIN_TRACK_SPACING_NM = 0.1

#: What an initial search is planned to (H guidance). Below this the search is
#: not "quick", it is unlikely to find anything.
INITIAL_COVERAGE = 1.0

#: 1 NM in km, for converting the drift model's metric areas.
KM_PER_NM = 1.852
SQ_KM_PER_SQ_NM = KM_PER_NM**2

#: Crew fatigue multiplier, applied only when a caller says the crew is on a
#: repeat sortie. H.3.5.3(c): "If feedback from on scene SRUs indicates search
#: crews were excessively fatigued, reduce sweep width values by 10 percent".
FATIGUE_FACTOR = 0.9


@dataclass(frozen=True, slots=True)
class SearchUnit:
    """A rescue unit, with the numbers a search plan needs from it.

    Speeds are *search* speeds, not transit speeds. A unit searches slower than
    it steams, and using a transit speed here would overstate the area covered
    per hour — the error that makes a plan look affordable and then run out of
    daylight.
    """

    code: str
    label: str
    sru_type: SRUType
    search_speed_kn: float
    transit_speed_kn: float
    #: Sea state the unit can operate a search in, as significant wave height.
    #: Above it the unit is not refused — that is the coordinator's call — but
    #: the plan says the limit was passed.
    max_search_wave_m: float
    #: Hours it can stay on scene searching before it has to go home. The single
    #: most under-appreciated constraint: a large area is worthless if nothing
    #: can stay long enough to cover it.
    on_scene_endurance_h: float
    note: str


#: Indian Coast Guard surface units, in the three broad shapes that matter to a
#: search plan. Speeds are conservative *search* figures for the class, not the
#: maximum speeds quoted in press releases — a hull that makes 33 knots does not
#: search at 33 knots, and planning as if it did is how an area gets declared
#: covered when it was not.
SEARCH_UNITS: tuple[SearchUnit, ...] = (
    SearchUnit(
        "ICG-OPV",
        "Offshore Patrol Vessel (ICG)",
        "vessel",
        12.0,
        16.0,
        6.0,
        24.0,
        "Large hull, high bridge, helicopter deck. The high eye height is what puts it in "
        "the 'Vessel SRU' column of the sweep table rather than the small-boat column.",
    ),
    SearchUnit(
        "ICG-FPV",
        "Fast Patrol Vessel / Inshore Patrol Vessel (ICG)",
        "vessel",
        12.0,
        22.0,
        4.0,
        12.0,
        "The coastal workhorse. Still a vessel-class eye height, but a shorter leash: "
        "endurance and sea-keeping both bind sooner than an OPV's.",
    ),
    SearchUnit(
        "ICG-IB",
        "Interceptor Boat / harbour craft (ICG or marine police)",
        "small_boat",
        10.0,
        25.0,
        2.5,
        6.0,
        "Fastest to the scene and first to be beaten back by the sea. Low eye height, so it "
        "takes the reduced small-boat sweep widths.",
    ),
    SearchUnit(
        "FISHING-FLEET",
        "Fishing vessel of opportunity",
        "small_boat",
        8.0,
        9.0,
        2.0,
        8.0,
        "NMSAR Plan 2022 para 55(c) recognises vessels of opportunity as SAR resources. Often "
        "on scene hours before anything official, and in Indian waters frequently the unit "
        "that actually makes the recovery.",
    ),
)

UNITS_BY_CODE: dict[str, SearchUnit] = {u.code: u for u in SEARCH_UNITS}


def _interpolate(visibility_nm: float, row: tuple[float, ...]) -> float:
    """Table H-19 with interpolation, which H.3.5.4(a) explicitly directs.

    Clamped at both ends: below 1 NM visibility the table has nothing to say, and
    reading it as zero would produce an infinite search time. Below 1 NM the
    honest answer is that a visual search is not viable, which
    :func:`plan_search` reports separately.
    """
    if visibility_nm <= VISIBILITY_COLUMNS_NM[0]:
        return row[0]
    if visibility_nm >= VISIBILITY_COLUMNS_NM[-1]:
        return row[-1]
    for i in range(1, len(VISIBILITY_COLUMNS_NM)):
        hi = VISIBILITY_COLUMNS_NM[i]
        if visibility_nm <= hi:
            lo = VISIBILITY_COLUMNS_NM[i - 1]
            span = (visibility_nm - lo) / (hi - lo)
            return row[i - 1] + span * (row[i] - row[i - 1])
    return row[-1]


def weather_correction(
    *, wind_kn: float | None, wave_m: float | None, small_object: bool
) -> tuple[float, str]:
    """Table H-10, with its own tie-break rule applied.

    The table's note is followed exactly: "If weather conditions in more than one
    row apply, i.e. winds 10 knots and seas 4 feet, use the lower row for more
    correction." So the worse of the two governs, which is the cautious
    direction and also the correct one.
    """
    wind = 0.0 if wind_kn is None else float(wind_kn)
    # Table H-10 is stated in feet of sea. 1 m = 3.28084 ft.
    seas_ft = 0.0 if wave_m is None else float(wave_m) * 3.28084

    if wind > 25.0 or seas_ft > 5.0:
        band = "winds over 25 kn or seas over 5 ft"
        factor = 0.25 if small_object else 0.9
    elif wind > 15.0 or seas_ft > 3.0:
        band = "winds 15-25 kn or seas 3-5 ft"
        factor = 0.5 if small_object else 0.9
    else:
        band = "winds under 15 kn and seas under 3 ft"
        factor = 1.0

    if wind_kn is None and wave_m is None:
        return 1.0, (
            "No wind or sea state available, so no weather correction was applied. The plan "
            "is therefore OPTIMISTIC: Table H-10 only ever reduces sweep width, never raises it."
        )
    return factor, f"Table H-10, {band}: sweep width x {factor}"


def corrected_sweep_width_nm(
    *,
    search_object: str,
    sru: SearchUnit,
    visibility_nm: float | None,
    wind_kn: float | None,
    wave_m: float | None,
    fatigued: bool = False,
) -> dict[str, Any]:
    """W, and every step that produced it.

    The breakdown is returned rather than just the number because a coordinator
    who disagrees with the plan needs to see which factor they disagree with.
    """
    row_key = search_object if search_object in _SWEEP_WIDTH_NM else DEFAULT_SEARCH_OBJECT
    substituted = row_key != search_object
    row = _SWEEP_WIDTH_NM[row_key][sru.sru_type]

    # Visibility unknown: the table needs a column, so take 10 NM — the column
    # the USCG's own worked examples use — and say that it was assumed.
    visibility_used = 10.0 if visibility_nm is None else float(visibility_nm)
    uncorrected = _interpolate(visibility_used, row)

    small_object = row_key in (
        "person_in_water",
        "raft_1_person",
        "raft_4_person",
        "raft_6_person",
        "boat_under_15ft",
        "boat_20ft",
    )
    fw, fw_note = weather_correction(wind_kn=wind_kn, wave_m=wave_m, small_object=small_object)
    fatigue = FATIGUE_FACTOR if fatigued else 1.0

    corrected = uncorrected * fw * fatigue
    return {
        "search_object": row_key,
        "substituted_default": substituted,
        "sru_column": sru.sru_type,
        "visibility_nm": round(visibility_used, 1),
        "visibility_assumed": visibility_nm is None,
        "uncorrected_nm": round(uncorrected, 3),
        "weather_factor": fw,
        "weather_note": fw_note,
        "fatigue_factor": fatigue,
        "corrected_nm": round(corrected, 3),
        "corrected_km": round(corrected * KM_PER_NM, 2),
    }


def _pattern_for(area_sq_nm: float, units: int, object_is_person: bool) -> dict[str, str]:
    """Which IAMSAR pattern fits, and why that one.

    The selection rules are the standard ones: expanding square when the datum is
    tight and the area small, sector search when the datum is tight and the
    object is hard to see, parallel track when the area is large or the position
    only approximate.
    """
    if object_is_person and area_sq_nm <= 30.0:
        return {
            "code": "VS",
            "name": "Sector search",
            "why": (
                "A person in the water is the hardest object on the table to see and the datum "
                "here is tight. A sector search passes the datum repeatedly from different "
                "angles, which is what beats sun glare and wave shadow — the two things that "
                "make a head disappear on one heading and reappear on another."
            ),
        }
    if area_sq_nm <= 50.0 and units == 1:
        return {
            "code": "SS",
            "name": "Expanding square",
            "why": (
                "Small area, one unit, and the highest probability density is at the datum. "
                "An expanding square spends its first and best minutes there. It demands "
                "accurate navigation — the legs are short and an error compounds."
            ),
        }
    if units > 1:
        return {
            "code": "PS",
            "name": "Parallel track, multiple units abreast",
            "why": (
                f"{units} units can be assigned adjacent lanes and cover the area in a fraction "
                "of the single-unit time. Lane boundaries must be assigned by the coordinator, "
                "not agreed on the radio."
            ),
        }
    return {
        "code": "PS",
        "name": "Parallel track",
        "why": (
            "The area is too large for an expanding square to reach its corners in useful time. "
            "Parallel tracks cover a rectangle uniformly, which is the assumption the coverage "
            "formula is built on."
        ),
    }


def plan_search(
    *,
    area_km2: float,
    drift_object_class: str,
    unit_code: str = "ICG-FPV",
    units: int = 1,
    visibility_km: float | None = None,
    wind_kn: float | None = None,
    wave_m: float | None = None,
    coverage: float = INITIAL_COVERAGE,
    fatigued: bool = False,
    daylight_hours_remaining: float | None = None,
) -> dict[str, Any]:
    """A search plan for an area the drift model produced.

    ``area_km2`` should be the drift model's own 95% containment area — the
    probability of containment the plan inherits comes from that figure, and
    quoting a POS against a smaller box would be claiming credit the search has
    not earned.
    """
    sru = UNITS_BY_CODE.get(unit_code) or UNITS_BY_CODE["ICG-FPV"]
    search_object = DRIFT_CLASS_TO_SEARCH_OBJECT.get(drift_object_class, DEFAULT_SEARCH_OBJECT)
    object_is_person = search_object == "person_in_water"

    visibility_nm = None if visibility_km is None else visibility_km / KM_PER_NM
    width = corrected_sweep_width_nm(
        search_object=search_object,
        sru=sru,
        visibility_nm=visibility_nm,
        wind_kn=wind_kn,
        wave_m=wave_m,
        fatigued=fatigued,
    )
    w_nm = width["corrected_nm"]

    # S = W / C, floored at the navigational limit.
    #
    # When the floor binds, coverage comes out WORSE than requested, not better:
    # the floor forces the legs FURTHER apart than the sweep width wants, so the
    # unit cannot search as thoroughly as asked. That is the case a gale
    # produces for a person in the water — sweep width collapses to 0.08 NM and
    # no surface unit can navigate legs that close. It is reported as a limit
    # below rather than silently returning the requested coverage.
    raw_spacing = w_nm / coverage if coverage > 0 else w_nm
    spacing_nm = max(MIN_TRACK_SPACING_NM, raw_spacing)
    spacing_floored = spacing_nm > raw_spacing + 1e-9
    achieved_coverage = (w_nm / spacing_nm) if spacing_nm > 0 else 0.0

    area_sq_nm = area_km2 / SQ_KM_PER_SQ_NM

    # T = A / (S x V x units), rearranged from C = (W x V x T) / A with C = W/S.
    sweep_rate_sq_nm_per_h = spacing_nm * sru.search_speed_kn * max(1, units)
    hours = area_sq_nm / sweep_rate_sq_nm_per_h if sweep_rate_sq_nm_per_h > 0 else math.inf

    # POD from the normal-conditions curve. Labelled a model, not a table.
    pod = 1.0 - math.exp(-achieved_coverage)

    track_length_nm = area_sq_nm / spacing_nm if spacing_nm > 0 else math.inf

    # --- What binds, and does the plan actually close? --------------------
    limits: list[str] = []
    if wave_m is not None and wave_m > sru.max_search_wave_m:
        limits.append(
            f"{wave_m:.1f} m sea is above the {sru.max_search_wave_m:.1f} m limit for "
            f"{sru.label}. A larger unit, or a helicopter, is the realistic option."
        )
    endurance_shortfall = hours > sru.on_scene_endurance_h
    if endurance_shortfall:
        limits.append(
            f"One sortie covers the area in {hours:.1f} h against {sru.on_scene_endurance_h:.0f} h "
            f"of on-scene endurance. It takes {math.ceil(hours / sru.on_scene_endurance_h)} "
            "sorties or that many units, and the datum will have moved between them."
        )
    if spacing_floored:
        limits.append(
            f"Conditions want legs {raw_spacing * KM_PER_NM * 1000:.0f} m apart, below the "
            f"{MIN_TRACK_SPACING_NM * KM_PER_NM * 1000:.0f} m floor for accurate surface "
            f"navigation. The achievable coverage is {achieved_coverage:.2f}, not "
            f"{coverage:.2f}, and the detection odds below are that lower figure. A surface "
            "unit cannot search this thoroughly — this is an aircraft or multiple-pass problem."
        )
    if visibility_nm is not None and visibility_nm < 1.0:
        limits.append(
            f"{visibility_km:.1f} km visibility is below the bottom of Table H-19. Unaided visual "
            "search is not viable — this becomes a radar and detection-aid problem."
        )
    if daylight_hours_remaining is not None and hours > daylight_hours_remaining:
        limits.append(
            f"{daylight_hours_remaining:.1f} h of daylight left against {hours:.1f} h of searching. "
            "Sunset is the usual cut-off for unaided visual search; after it the plan needs "
            "night detection aids or radar, and different track spacing."
        )

    return {
        "unit": {
            "code": sru.code,
            "label": sru.label,
            "search_speed_kn": sru.search_speed_kn,
            "transit_speed_kn": sru.transit_speed_kn,
            "on_scene_endurance_h": sru.on_scene_endurance_h,
            "max_search_wave_m": sru.max_search_wave_m,
            "note": sru.note,
        },
        "units_assigned": max(1, units),
        "sweep_width": width,
        "track_spacing_nm": round(spacing_nm, 3),
        "track_spacing_km": round(spacing_nm * KM_PER_NM, 2),
        "track_spacing_floored": spacing_floored,
        "coverage_requested": coverage,
        "coverage_achieved": round(achieved_coverage, 3),
        "area_km2": round(area_km2, 1),
        "area_sq_nm": round(area_sq_nm, 1),
        "search_hours": round(hours, 2),
        "track_length_nm": round(track_length_nm, 1),
        "probability_of_detection": round(pod, 3),
        "pattern": _pattern_for(area_sq_nm, max(1, units), object_is_person),
        "limits": limits,
        "closes": not limits,
        "conditions_used": {
            "visibility_km": visibility_km,
            "wind_kn": wind_kn,
            "wave_m": wave_m,
        },
        "how_to_read": (
            f"Run legs {spacing_nm * KM_PER_NM:.1f} km apart at {sru.search_speed_kn:.0f} kn. "
            f"That is {track_length_nm:.0f} NM of track and about {hours:.1f} h for "
            f"{max(1, units)} unit(s), and if the object is in this box you would expect to "
            f"find it {pod * 100:.0f}% of the time."
        ),
        "pod_is_modelled": (
            "Probability of detection is read off the IAMSAR 'normal search conditions' curve, "
            "which is the exponential detection function POD = 1 - exp(-C). It is a model, not "
            "a transcribed table. The 'ideal conditions' curve is higher and is NOT used: ideal "
            "means perfect parallel sweeps relative to the object, which no boat in a seaway is "
            "achieving."
        ),
        "pos_note": (
            "POD is not the measure of a search. POS = POC x POD, and the containment "
            "probability comes from the drift model's 95% area — so a 95% containment with a "
            f"{pod * 100:.0f}% detection is about a {0.95 * pod * 100:.0f}% chance this one "
            "search finds the object."
        ),
        "aircraft_note": (
            "Surface units only. An ICG Dornier covers a large area far faster than any hull, "
            "but its sweep widths come from Tables H-11 to H-18, which are indexed on search "
            "altitude and are not carried here. Do not read this plan as the full set of options."
        ),
        "citations": [
            USCG_SWEEP_CITATION.model_dump(mode="json"),
            USCG_WEATHER_CITATION.model_dump(mode="json"),
            COVERAGE_CITATION.model_dump(mode="json"),
        ],
    }


def units() -> list[dict[str, Any]]:
    """The unit catalogue, for a UI that wants to offer the choice."""
    return [
        {
            "code": u.code,
            "label": u.label,
            "sru_column": u.sru_type,
            "search_speed_kn": u.search_speed_kn,
            "transit_speed_kn": u.transit_speed_kn,
            "max_search_wave_m": u.max_search_wave_m,
            "on_scene_endurance_h": u.on_scene_endurance_h,
            "note": u.note,
        }
        for u in SEARCH_UNITS
    ]

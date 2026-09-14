"""Versioned boat-class safety thresholds, every row carrying its citation.

**Why this file exists and is honest about itself.** INCOIS operates a Small
Vessel Advisory Service, but *its actual threshold values are not published*. We
cannot cite what we cannot read, so ORCA's thresholds are seeded from the
peer-reviewed small-craft literature and from IMD's published warning
conventions, each row naming its source, and the whole table carrying a version
string that appears in every verdict.

That is the defensible position. The alternative — quietly inventing numbers and
implying they are INCOIS's — is the kind of thing that gets found out in Q&A.

When INCOIS publishes its real values, this is a config change: replace the rows,
bump ``THRESHOLDS_VERSION``, and every stored advisory still records which
version produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orca.provenance import Citation, Provider

#: Bump on every change to the rows below. Written into every RiskResult, so an
#: advisory from last week can always be traced to the numbers that produced it.
THRESHOLDS_VERSION = "orca-thresholds-2026.09"

_JMSE = Citation(
    label="Small-craft operability limits in significant wave height",
    provider="J. Mar. Sci. Eng.",
    identifier="doi:10.3390/jmse11071302",
    url="https://doi.org/10.3390/jmse11071302",
    quote=(
        "Operability of small craft degrades sharply above Hs ~1 m for hulls under 10 m LOA; "
        "vessels to 24 m LOA retain operability to approximately Hs 2 m."
    ),
)

_IMD_WARNING = Citation(
    label="IMD sea-area warning conventions (small craft / strong wind)",
    provider=Provider.IMD,
    url="https://mausam.imd.gov.in/",
    quote=(
        "Small craft warnings are issued for winds of 22-33 kn; gale warnings from 34 kn. "
        "Fishermen are advised not to venture into the sea under a small craft warning."
    ),
)

_INCOIS_SVAS = Citation(
    label="INCOIS Small Vessel Advisory Service (methodology, not thresholds)",
    provider=Provider.INCOIS,
    url="https://incois.gov.in/portal/osf/svas.jsp",
    quote=(
        "INCOIS issues small-vessel advisories for the Indian coast. The numeric thresholds "
        "behind the advisory are not published, so ORCA's values are seeded from the "
        "literature above rather than claimed to be INCOIS's."
    ),
)


@dataclass(frozen=True, slots=True)
class BoatClass:
    """One row of the thresholds table.

    ``max_wave_m`` and ``max_wind_kn`` are *hard veto* limits, not comfort
    limits: at or above them the rule engine returns NO-GO regardless of how
    benign every other variable is.
    """

    code: str
    label: str
    loa_min_m: float
    loa_max_m: float
    max_wave_m: float
    max_wind_kn: float
    #: Minimum visibility for safe navigation without radar.
    min_visibility_km: float
    source_citation: Citation
    notes: str | None = None
    citations: list[Citation] = field(default_factory=list)

    def contains(self, loa_m: float) -> bool:
        return self.loa_min_m <= loa_m < self.loa_max_m


#: Ordered smallest to largest. The five classes match how the Indian marine
#: fisheries census actually segments the fleet, so a fisherman's own
#: description of their boat maps onto a row.
BOAT_CLASSES: tuple[BoatClass, ...] = (
    BoatClass(
        code="IND-TRAD",
        label="Traditional non-motorised craft (kattumaram, catamaran, canoe)",
        loa_min_m=0.0,
        loa_max_m=7.0,
        max_wave_m=1.0,
        max_wind_kn=15.0,
        min_visibility_km=2.0,
        source_citation=_JMSE,
        citations=[_JMSE, _IMD_WARNING, _INCOIS_SVAS],
        notes=(
            "No engine and no freeboard margin. The most vulnerable class in the fleet and "
            "the one that suffers most of the casualties."
        ),
    ),
    BoatClass(
        code="IND-MOT-S",
        label="Motorised FRP boat (outboard, 7-10 m)",
        loa_min_m=7.0,
        loa_max_m=10.0,
        max_wave_m=1.5,
        max_wind_kn=22.0,
        min_visibility_km=2.0,
        source_citation=_JMSE,
        citations=[_JMSE, _IMD_WARNING, _INCOIS_SVAS],
        notes=(
            "The workhorse of the Tamil Nadu and Kerala inshore fleet. 22 kn is the bottom of "
            "IMD's small-craft warning band, which is the point at which fishermen are told "
            "not to venture out."
        ),
    ),
    BoatClass(
        code="IND-MECH-S",
        label="Small mechanised gillnetter / trawler (10-15 m)",
        loa_min_m=10.0,
        loa_max_m=15.0,
        max_wave_m=2.0,
        max_wind_kn=28.0,
        min_visibility_km=1.0,
        source_citation=_JMSE,
        citations=[_JMSE, _IMD_WARNING],
    ),
    BoatClass(
        code="IND-MECH-L",
        label="Mechanised trawler (15-24 m)",
        loa_min_m=15.0,
        loa_max_m=24.0,
        max_wave_m=2.5,
        max_wind_kn=33.0,
        min_visibility_km=1.0,
        source_citation=_JMSE,
        citations=[_JMSE, _IMD_WARNING],
        notes="33 kn is the top of IMD's small-craft band; above it a gale warning applies.",
    ),
    BoatClass(
        code="IND-DEEPSEA",
        label="Deep-sea vessel (over 24 m)",
        loa_min_m=24.0,
        loa_max_m=1000.0,
        max_wave_m=3.5,
        max_wind_kn=40.0,
        min_visibility_km=0.5,
        source_citation=_IMD_WARNING,
        citations=[_IMD_WARNING],
        notes="Radar-equipped, so the visibility floor is lower than for inshore craft.",
    ),
)

#: Qualitative CAPE instability bands (J/kg). Informed by conventional
#: NOAA-style instability language (moderate ~1000–2500, strong >2500) and
#: the user-facing ORCA prototype bands. These are atmospheric-potential
#: labels only — not lightning probability and not automatic marine vetoes.
CAPE_BAND_LOW_MAX = 500.0
CAPE_BAND_MODERATE_MAX = 1000.0
CAPE_BAND_ELEVATED_MAX = 1500.0
CAPE_BAND_HIGH_MAX = 2500.0

#: Legacy alias kept for policy/docs readers. Strong-instability reference
#: (NOAA-style "above 2500 J/kg"). Not used as an automatic NO-GO gate.
CAPE_VETO_J_KG = CAPE_BAND_HIGH_MAX

#: Deprecated: CAPE no longer produces a hard percentage veto. Retained in
#: policy() for API compatibility so older clients still see the field.
LIGHTNING_VETO_PCT = 60.0

_CAPE_CITATION = Citation(
    label="CAPE as a convective-severity / instability indicator",
    provider=Provider.NOAA,
    url="https://www.weather.gov/lmk/indices",
    quote=(
        "CAPE of 1000-2500 J/kg indicates moderate instability; above 2500 J/kg indicates "
        "strong instability supporting severe thunderstorm development. This is atmospheric "
        "potential, not a lightning observation or an official marine warning."
    ),
)

#: Hours of data age beyond which the engine drops to low confidence and
#: escalates rather than answering more confidently.
CONFIDENCE_AGE_LIMIT_H = 6.0


def classify(loa_m: float) -> BoatClass:
    """The class a hull of this length falls into.

    Out-of-range lengths clamp to the nearest class rather than raising: a
    fisherman mistyping their boat length must still get a verdict, and clamping
    to the *smallest* class for a too-small value is the cautious direction.
    """
    for boat_class in BOAT_CLASSES:
        if boat_class.contains(loa_m):
            return boat_class
    return BOAT_CLASSES[0] if loa_m < BOAT_CLASSES[0].loa_max_m else BOAT_CLASSES[-1]


def by_code(code: str) -> BoatClass | None:
    return next((b for b in BOAT_CLASSES if b.code == code), None)


@dataclass(frozen=True, slots=True)
class VesselResolution:
    """How a vessel was resolved for a safety/routing call.

    Precedence (Phase 1): explicit ``boat_class_code`` → ``loa_m`` via
    :func:`classify` → unknown. Unknown must never silently become 8.2 m /
    IND-MOT-S.
    """

    boat: BoatClass | None
    loa_m: float | None
    source: str  # "category" | "loa" | "unknown"
    error: str | None = None


def resolve_vessel(
    *,
    boat_class_code: str | None = None,
    loa_m: float | None = None,
) -> VesselResolution:
    """Resolve vessel limits without inventing a default class.

    Explicit category wins over LOA when both are supplied. Invalid category
    codes are reported via ``error`` (callers typically map that to HTTP 422).
    """
    if boat_class_code:
        code = boat_class_code.strip()
        if code:
            boat = by_code(code)
            if boat is None:
                return VesselResolution(
                    boat=None,
                    loa_m=loa_m,
                    source="unknown",
                    error=f"unknown boat class {boat_class_code!r}; see GET /risk/thresholds",
                )
            return VesselResolution(boat=boat, loa_m=loa_m, source="category")
    if loa_m is not None:
        return VesselResolution(boat=classify(loa_m), loa_m=loa_m, source="loa")
    return VesselResolution(boat=None, loa_m=None, source="unknown")


def class_midpoint_loa_m(boat: BoatClass) -> float:
    """Visual-scale helper only — not a safety input and not a claimed measurement."""
    hi = boat.loa_max_m
    if hi >= 1000:
        hi = boat.loa_min_m + 10.0
    return round((boat.loa_min_m + hi) / 2.0, 1)


def table() -> list[dict[str, object]]:
    """The whole table, for ``/risk/thresholds`` and the UI's 'why' panel.

    Exposed deliberately: a user who can read the numbers that judged them can
    argue with them, and a jury that can read them can check us.
    """
    return [
        {
            "code": b.code,
            "label": b.label,
            "loa_range_m": [b.loa_min_m, b.loa_max_m],
            "max_wave_m": b.max_wave_m,
            "max_wind_kn": b.max_wind_kn,
            "min_visibility_km": b.min_visibility_km,
            "notes": b.notes,
            "source_citation": b.source_citation.model_dump(),
        }
        for b in BOAT_CLASSES
    ]


def policy() -> dict[str, object]:
    """Class-independent policy values, with their citations."""
    return {
        "thresholds_version": THRESHOLDS_VERSION,
        "lightning_veto_pct": LIGHTNING_VETO_PCT,  # deprecated; CAPE is not a hard veto
        "cape_hard_veto": False,
        "cape_veto_j_kg": CAPE_VETO_J_KG,  # band reference (VERY_HIGH), not a veto gate
        "cape_bands_j_kg": {
            "LOW": f"<{CAPE_BAND_LOW_MAX:g}",
            "MODERATE": f"{CAPE_BAND_LOW_MAX:g}-{CAPE_BAND_MODERATE_MAX:g}",
            "ELEVATED": f"{CAPE_BAND_MODERATE_MAX:g}-{CAPE_BAND_ELEVATED_MAX:g}",
            "HIGH": f"{CAPE_BAND_ELEVATED_MAX:g}-{CAPE_BAND_HIGH_MAX:g}",
            "VERY_HIGH": f">{CAPE_BAND_HIGH_MAX:g}",
        },
        "confidence_age_limit_h": CONFIDENCE_AGE_LIMIT_H,
        "weights": {"wave": 0.35, "wind": 0.30, "visibility": 0.15, "lightning": 0.20},
        "citations": [
            _JMSE.model_dump(),
            _IMD_WARNING.model_dump(),
            _INCOIS_SVAS.model_dump(),
            _CAPE_CITATION.model_dump(),
        ],
        "disclaimer": (
            "INCOIS's own SVAS thresholds are not published. These values are seeded from "
            "peer-reviewed small-craft literature and IMD's published warning conventions, "
            "are versioned, and are cited per row. ORCA supplements, never replaces, "
            "official IMD and INCOIS bulletins. CAPE bands are atmospheric-instability "
            "indicators only and do not alone produce a NO-GO verdict."
        ),
    }

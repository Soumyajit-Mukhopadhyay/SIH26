"""The contract every value in ORCA obeys.

Nothing reaches the agent layer as a bare number. A value arrives wrapped in
:class:`Evidence`, which names the dataset it came from, which of the provenance
states it is in, and how old it is. ``Advisory.evidence`` is declared with
``min_length=1``, so an uncited answer fails *validation* — a type-level
guarantee, not a politely-worded prompt.

This module is deliberately dependency-free apart from pydantic: it is imported
by sources, science, services, agents and the API alike, so it must never import
any of them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    """One clock for the whole system. Always timezone-aware, always UTC."""
    return datetime.now(UTC)


class Provenance(StrEnum):
    """The provenance badge that appears on every value in the UI.

    The distinction is the point: a judge can see at a glance whether a number
    was measured, remembered, hand-curated, computed, or invented for the demo.
    """

    LIVE = "live"
    """Fetched from the upstream provider within this request's lifetime."""

    CACHED = "cached"
    """Served from our store. Real data from a real provider, just not this second."""

    CURATED = "curated"
    """Hand-checked reference geography or a versioned threshold table. Not a measurement."""

    DERIVED = "derived"
    """Computed by ORCA from other Evidence. ``lineage`` names every input."""

    SIMULATED = "simulated"
    """Synthetic. Rendered hatched, labelled, and never silently mixed with LIVE."""

    UNAVAILABLE = "unavailable"
    """The source was asked and could not answer. Carried explicitly rather than
    dropped, so a Bhuvan or IMD outage degrades the answer visibly instead of
    quietly changing what the answer means."""


#: Provenance states a safety verdict may be computed from. ``SIMULATED`` is
#: excluded on purpose: a GO/NO-GO must never rest on invented data.
DECISION_GRADE: frozenset[Provenance] = frozenset(
    {Provenance.LIVE, Provenance.CACHED, Provenance.CURATED, Provenance.DERIVED}
)


class Provider(StrEnum):
    """Who produced the data. An enum so the freshness screen and the citation
    footers cannot drift apart through typos."""

    CMEMS = "CMEMS"
    INCOIS = "INCOIS"
    IMD = "IMD"
    NDMA = "NDMA"
    NASA = "NASA"
    NOAA = "NOAA"
    OPEN_METEO = "Open-Meteo"
    BHUVAN = "Bhuvan"
    MOSDAC = "MOSDAC"
    USGS = "USGS"
    GFW = "GFW"
    AISSTREAM = "AISStream"
    WORLD_TIDES = "WorldTides"
    MARINE_REGIONS = "Marine Regions"
    PROTECTED_PLANET = "Protected Planet"
    GEBCO = "GEBCO"
    SARVAM = "Sarvam"
    CELESTRAK = "Celestrak"
    ORCA = "ORCA"


# --------------------------------------------------------------------------- #
# Staleness policy
# --------------------------------------------------------------------------- #
# Per-variable, because "old" means completely different things for a wave
# forecast and for a chlorophyll composite that has to wait for a cloud-free
# pass. One table, consulted by every adapter, so no source gets to invent its
# own idea of stale.

STALENESS_HOURS: dict[str, float] = {
    # atmosphere / sea state — hourly model output, so a few hours is already old
    "wave_height": 6.0,
    "wave_period": 6.0,
    "wave_direction": 6.0,
    "swell_height": 6.0,
    "wind_wave_height": 6.0,
    "wind_speed": 6.0,
    "wind_direction": 6.0,
    "wind_gust": 3.0,
    "visibility": 3.0,
    "precipitation": 3.0,
    "lightning_probability": 3.0,
    # CAPE and CIN — thunderstorm potential, the honest stand-in for a lightning
    # feed India has no free authoritative source for.
    "convective_energy": 3.0,
    "convective_inhibition": 3.0,
    # ocean — daily to multi-day products
    "sst": 24.0,
    "sst_anomaly": 24.0,
    "sea_surface_current": 12.0,
    "sea_surface_current_direction": 12.0,
    "sea_level_anomaly": 24.0,
    "mixed_layer_depth": 24.0,
    "chlorophyll": 72.0,  # cloud gaps make anything tighter dishonest
    "turbidity": 72.0,
    # ORCA-derived fields
    "thermal_front": 48.0,
    # Daily SST plus typical MUR publish lag, and chlorophyll is monthly.
    # A just-ingested PFZ must not look stale because the satellite scene
    # already carried a day of latency — we cannot fetch a newer field.
    "pfz_rank": 72.0,
    "marine_heatwave": 24.0,
    "upwelling_index": 24.0,
    # operational feeds
    "cap_alert": 1.0,
    "cyclone_track": 3.0,
    "earthquake": 1.0,
    "ais_position": 0.25,
    "fishing_effort": 168.0,
    "tide_height": 6.0,
    "tide_extreme": 24.0,
    # orbital elements — SGP4 accuracy decays with TLE age
    "tle": 72.0,
}

#: Reference geography and versioned threshold tables do not go stale on a clock.
NEVER_STALE: frozenset[str] = frozenset(
    {"eez", "imbl", "mpa", "coastline", "port", "bathymetry", "boat_class", "threshold"}
)

_DEFAULT_STALENESS_HOURS = 6.0


def staleness_hours(variable: str) -> float:
    """Hours before ``variable`` is considered stale. Unknown variables get a
    deliberately conservative 6 h rather than an optimistic default."""
    if variable in NEVER_STALE:
        return float("inf")
    return STALENESS_HOURS.get(variable, _DEFAULT_STALENESS_HOURS)


class Freshness(BaseModel):
    """How old a value is, and whether that is a problem.

    ``note`` exists because of a UI rule we hold ourselves to: when a value is
    stale, the sentence explaining that is surfaced *before* the number, not in a
    footnote under it.
    """

    model_config = ConfigDict(frozen=True)

    valid_time: datetime = Field(description="The instant the value describes.")
    retrieved_at: datetime = Field(default_factory=utcnow, description="When ORCA obtained it.")
    age_hours: float = Field(description="Hours between valid_time and retrieved_at.")
    is_stale: bool
    stale_after: datetime
    note: str | None = Field(
        default=None, description="Human sentence, surfaced before the value when stale."
    )

    @classmethod
    def of(
        cls,
        variable: str,
        valid_time: datetime,
        *,
        retrieved_at: datetime | None = None,
        threshold_hours: float | None = None,
        label: str | None = None,
    ) -> Freshness:
        """Build a Freshness from a variable name and the instant it describes.

        The only sanctioned constructor — adapters must not compute staleness
        themselves, or the policy stops being one policy.
        """
        retrieved = _as_utc(retrieved_at or utcnow())
        valid = _as_utc(valid_time)

        limit = threshold_hours if threshold_hours is not None else staleness_hours(variable)
        age = (retrieved - valid).total_seconds() / 3600.0

        if limit == float("inf"):
            return cls(
                valid_time=valid,
                retrieved_at=retrieved,
                age_hours=round(age, 3),
                is_stale=False,
                stale_after=_FOREVER,
                note=None,
            )

        stale_after = valid + timedelta(hours=limit)
        is_stale = retrieved > stale_after
        note = None
        if is_stale:
            what = label or variable.replace("_", " ")
            note = (
                f"This {what} reading is {humanise_hours(age)} old — past the "
                f"{humanise_hours(limit)} freshness limit for {what}. "
                "Treat it as indicative and check the latest official bulletin."
            )
        return cls(
            valid_time=valid,
            retrieved_at=retrieved,
            age_hours=round(age, 3),
            is_stale=is_stale,
            stale_after=stale_after,
            note=note,
        )

    @property
    def is_forecast(self) -> bool:
        """Whether this value describes a time that has not happened yet.

        Forecast points legitimately have a *negative* ``age_hours``: the value
        describes 06:00 tomorrow and we fetched it today. That is not an error and
        not staleness — it is lead time, and it must be presented as such rather
        than as "-14.0 h old", which is meaningless to a reader.
        """
        return self.age_hours < 0

    @property
    def lead_hours(self) -> float:
        """Hours into the future this value describes. 0 for past observations."""
        return max(0.0, -self.age_hours)

    @property
    def staleness_age_hours(self) -> float:
        """Age for staleness purposes, floored at zero.

        A forecast for three days out is uncertain, not stale, so its negative
        age must not be allowed to sail through an age check as if it were
        exceptionally fresh data.
        """
        return max(0.0, self.age_hours)

    @classmethod
    def static(cls, *, as_of: datetime | None = None) -> Freshness:
        """For CURATED reference data, which has an edition date but no clock."""
        t = _as_utc(as_of or utcnow())
        return cls(
            valid_time=t,
            retrieved_at=t,
            age_hours=0.0,
            is_stale=False,
            stale_after=_FOREVER,
            note=None,
        )


class Citation(BaseModel):
    """A pointer a human can follow to check us.

    Distinct from :class:`Evidence`: Evidence carries a value, a Citation carries
    an authority. Threshold rows cite peer-reviewed literature; they have no
    value to report.
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(description="Short human name, e.g. 'INCOIS PFZ methodology'.")
    provider: Provider | str
    url: str | None = None
    identifier: str | None = Field(
        default=None, description="DOI, dataset id, bulletin number or CAP identifier."
    )
    accessed_at: datetime | None = None
    quote: str | None = Field(default=None, description="The specific clause relied on.")


class Evidence(BaseModel):
    """A single value, and everything needed to defend it.

    Every number that reaches an advisory, a chart, a map layer or an LLM prompt
    is one of these. ``lineage`` is mandatory for ``DERIVED`` values (enforced
    below), which is what makes the PFZ layer auditable rather than magical.
    """

    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(
        description="Provider's own identifier, e.g. cmems_mod_glo_wav_anfc_0.083deg_PT3H-i."
    )
    provider: Provider | str
    variable: str
    value: float | str | bool | None = None
    unit: str | None = None
    provenance: Provenance
    freshness: Freshness
    lineage: list[str] = Field(
        default_factory=list, description="For DERIVED: the dataset_ids it was computed from."
    )
    url: str | None = None
    location: tuple[float, float] | None = Field(
        default=None, description="(lon, lat) the value applies to, if point-like."
    )
    method: str | None = Field(
        default=None, description="For DERIVED: the algorithm, e.g. 'Canny + SIED front detector'."
    )
    uncertainty: float | None = None
    citations: list[Citation] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _check_provenance_invariants(self) -> Self:
        if self.provenance is Provenance.DERIVED and not self.lineage:
            raise ValueError(
                f"DERIVED evidence for {self.variable!r} must declare its lineage: a computed "
                "value with no named inputs is not auditable."
            )
        if self.provenance is Provenance.UNAVAILABLE and self.value is not None:
            raise ValueError(
                f"UNAVAILABLE evidence for {self.variable!r} carries a value; that is a "
                "contradiction — use CACHED if a fallback value was substituted."
            )
        return self

    @property
    def is_decision_grade(self) -> bool:
        """Whether this value may contribute to a deterministic safety verdict."""
        return self.provenance in DECISION_GRADE and self.value is not None

    def display(self) -> str:
        """One-line rendering used in trace logs and the evidence panel."""
        unit = f" {self.unit}" if self.unit else ""
        val = "None" if self.value is None else f"{self.value}{unit}"
        stale = " !stale" if self.freshness.is_stale else ""
        badge = self.provenance.value.upper()
        return f"{self.variable} = {val} [{badge}{stale}] {self.provider}/{self.dataset_id}"

    @classmethod
    def unavailable(
        cls,
        *,
        dataset_id: str,
        provider: Provider | str,
        variable: str,
        reason: str,
        url: str | None = None,
    ) -> Evidence:
        """The honest failure case. A source that cannot answer says so, in band."""
        return cls(
            dataset_id=dataset_id,
            provider=provider,
            variable=variable,
            value=None,
            provenance=Provenance.UNAVAILABLE,
            freshness=Freshness.static(),
            url=url,
            notes=reason,
        )


class SourceHealth(BaseModel):
    """One row of ``GET /freshness`` — the demo screen that proves the pipeline is
    real. A source that has never succeeded says so rather than being absent."""

    source: str
    provider: Provider | str
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    circuit_open: bool = False
    provenance: Provenance = Provenance.UNAVAILABLE
    variables: list[str] = Field(default_factory=list)

    @property
    def age_hours(self) -> float | None:
        if self.last_success is None:
            return None
        return round((utcnow() - _as_utc(self.last_success)).total_seconds() / 3600.0, 3)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_FOREVER = datetime(9999, 12, 31, tzinfo=UTC)


def _as_utc(dt: datetime) -> datetime:
    """Naive datetimes are assumed UTC. Silent local-time drift is a whole class
    of bug we are not interested in having."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def humanise_hours(hours: float) -> str:
    if hours == float("inf"):
        return "unlimited"
    if hours < 1:
        return f"{round(hours * 60)} min"
    if hours < 48:
        return f"{hours:.1f} h".replace(".0 h", " h")
    return f"{hours / 24:.1f} days".replace(".0 days", " days")


Lon = Annotated[float, Field(ge=-180, le=180)]
Lat = Annotated[float, Field(ge=-90, le=90)]

#: Worst-first, so the aggregate badge on a composite card is the most cautious
#: state present rather than the most flattering one.
_PROVENANCE_WORST_FIRST: tuple[Provenance, ...] = (
    Provenance.UNAVAILABLE,
    Provenance.SIMULATED,
    Provenance.DERIVED,
    Provenance.CURATED,
    Provenance.CACHED,
    Provenance.LIVE,
)


def evidence_summary(evidence: list[Evidence]) -> dict[str, Any]:
    """Aggregate badge for a set of Evidence: the worst provenance state present
    and the worst freshness. Drives the single badge on a composite card."""
    if not evidence:
        return {"count": 0, "provenance": None, "mix": [], "stale": False, "max_age_hours": None}
    present = {e.provenance for e in evidence}
    worst = next(p for p in _PROVENANCE_WORST_FIRST if p in present)
    ages = [e.freshness.age_hours for e in evidence]
    return {
        "count": len(evidence),
        "provenance": worst.value,
        "mix": sorted(p.value for p in present),
        "stale": any(e.freshness.is_stale for e in evidence),
        "max_age_hours": round(max(ages), 3) if ages else None,
    }

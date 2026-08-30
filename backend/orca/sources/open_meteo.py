"""Open-Meteo — waves, swell, currents, SST from the Marine API; wind, gusts,
visibility, precipitation and convective energy from the Forecast API.

Zero auth, generous free tier, and it answers in under two seconds, which makes
it ORCA's default for anything the risk engine needs. CMEMS and INCOIS carry the
scientific and India-specific provenance; this carries the latency budget.

Three details that matter downstream:

* Every returned series is a list of ``Evidence``, one per hour, each with its
  own ``valid_time``. A single "current" value would make the risk engine's
  freshness contract unenforceable.
* **Units are read from the response, not assumed.** Open-Meteo applies
  ``wind_speed_unit`` to ocean current velocity as well, so asking for knots for
  wind silently returns currents in knots too. Trusting our own table there
  labelled a 0.9 kn current as 0.9 m/s — a 2x error that would have corrupted the
  router's current-assist and the SAR drift field. Every value is converted from
  the unit upstream declares, and a unit we cannot convert yields
  ``UNAVAILABLE`` rather than a plausible wrong number.
* There is **no lightning field**. Open-Meteo has no thunderstorm probability,
  and no free authoritative lightning source covers India (GOES/GLM does not
  reach it). We carry ``convective_energy`` (CAPE, J/kg) — the standard
  thunderstorm-potential proxy — and let the risk engine map it with a cited
  threshold, so what we have is labelled as what it is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, ClassVar

from orca.provenance import Citation, Evidence, Freshness, Provider, utcnow
from orca.sources.base import Fetched, Source

log = logging.getLogger(__name__)

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: ORCA variable name -> Open-Meteo Marine hourly field.
MARINE_FIELDS: dict[str, str] = {
    "wave_height": "wave_height",
    "wave_period": "wave_period",
    "wave_direction": "wave_direction",
    "swell_height": "swell_wave_height",
    "sst": "sea_surface_temperature",
    "sea_surface_current": "ocean_current_velocity",
    "sea_surface_current_direction": "ocean_current_direction",
}

#: ORCA variable name -> Open-Meteo Forecast hourly field.
FORECAST_FIELDS: dict[str, str] = {
    "wind_speed": "wind_speed_10m",
    "wind_direction": "wind_direction_10m",
    "wind_gust": "wind_gusts_10m",
    "visibility": "visibility",
    "precipitation": "precipitation",
    # CAPE, not a lightning probability. See the module docstring.
    "convective_energy": "cape",
    "convective_inhibition": "convective_inhibition",
}

#: ORCA's canonical unit per variable. Everything downstream — the risk engine,
#: the router, the raster colormaps — assumes these, so conversion happens once,
#: here, at the boundary.
UNITS: dict[str, str] = {
    "wave_height": "m",
    "wave_period": "s",
    "wave_direction": "deg",
    "swell_height": "m",
    "sst": "degC",
    "sea_surface_current": "m/s",
    "sea_surface_current_direction": "deg",
    "wind_speed": "kn",
    "wind_direction": "deg",
    "wind_gust": "kn",
    "visibility": "m",
    "precipitation": "mm",
    "convective_energy": "J/kg",
    "convective_inhibition": "J/kg",
}

#: Multiplicative conversions into ORCA's canonical units. Only physically
#: meaningful pairs appear; anything absent is a refusal, not a guess.
_CONVERSIONS: dict[tuple[str, str], float] = {
    ("km/h", "m/s"): 1 / 3.6,
    ("kn", "m/s"): 0.514444,
    ("mph", "m/s"): 0.44704,
    ("m/s", "kn"): 1.943844,
    ("km/h", "kn"): 0.539957,
    ("mph", "kn"): 0.868976,
    ("km", "m"): 1000.0,
    ("cm", "m"): 0.01,
    ("ft", "m"): 0.3048,
    ("inch", "mm"): 25.4,
    # degF -> degC is affine, not multiplicative, so it lives in `convert`
    # rather than here. A factor of 0.0 sitting in this table would be a trap.
}

_DEGREE_SIGN = "°"


def normalise_unit(raw: str | None) -> str | None:
    """Fold Open-Meteo's typographic variants (``°C``, ``°``) so the conversion
    table stays readable. Dimensionless markers become ``None``."""
    if raw is None:
        return None
    cleaned = raw.strip().replace(f"{_DEGREE_SIGN}C", "degC").replace(_DEGREE_SIGN, "deg")
    if cleaned.lower() in {"", "wmo code", "iso8601"}:
        return None
    return cleaned


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Value in ORCA's canonical unit, or ``None`` when the conversion is unknown.

    Returning ``None`` rather than the raw value is the whole point: an
    unconvertible unit must surface as UNAVAILABLE, never as a number that looks
    right and is not.
    """
    if to_unit is None or from_unit is None or from_unit == to_unit:
        return value
    if (from_unit, to_unit) == ("degF", "degC"):
        return (value - 32.0) * 5.0 / 9.0
    factor = _CONVERSIONS.get((from_unit, to_unit))
    return None if factor is None else value * factor


def _convertible(from_unit: str | None, to_unit: str | None) -> bool:
    """Whether a conversion exists, checked without a magic sentinel value —
    ``convert(1.0, 'degF', 'degC')`` legitimately returns a number."""
    if to_unit is None or from_unit is None or from_unit == to_unit:
        return True
    if (from_unit, to_unit) == ("degF", "degC"):
        return True
    return (from_unit, to_unit) in _CONVERSIONS


class _OpenMeteoBase(Source):
    provider = Provider.OPEN_METEO
    docs_url = "https://open-meteo.com/en/docs"

    url: ClassVar[str] = ""
    fields: ClassVar[dict[str, str]] = {}
    #: Open-Meteo picks the nearest grid cell; marine variables must come from a
    #: sea cell or a coastal query silently returns land.
    cell_selection: ClassVar[str] = "land"

    async def series(
        self,
        lat: float,
        lon: float,
        *,
        variables: list[str] | None = None,
        forecast_days: int = 3,
        past_days: int = 0,
    ) -> dict[str, list[Evidence]]:
        """Hourly series per variable, each point carrying its own Evidence.

        On failure returns one ``UNAVAILABLE`` Evidence per requested variable
        rather than an empty dict, so a caller cannot mistake "the source is
        down" for "the value is zero".
        """
        wanted = [v for v in (variables or list(self.fields)) if v in self.fields]
        if not wanted:
            return {}

        params: dict[str, Any] = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "hourly": ",".join(self.fields[v] for v in wanted),
            "timezone": "UTC",
            "forecast_days": forecast_days,
            "wind_speed_unit": "kn",  # knots: what a mariner and the rule engine use
            "cell_selection": self.cell_selection,
        }
        if past_days:
            params["past_days"] = past_days

        result = await self.fetch(self.url, params=params)
        if not result.ok:
            return {v: [self._unavailable(v, result)] for v in wanted}

        try:
            payload = result.json()
            hourly = payload["hourly"]
            times = [datetime.fromisoformat(t) for t in hourly["time"]]
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("%s: unparseable payload: %s", self.name, exc)
            bad = Fetched(ok=False, source=self.name, url=result.url, error=f"parse error: {exc}")
            return {v: [self._unavailable(v, bad)] for v in wanted}

        reported = payload.get("hourly_units", {})
        out: dict[str, list[Evidence]] = {}

        for variable in wanted:
            field = self.fields[variable]
            values = hourly.get(field)
            if values is None:
                out[variable] = [
                    self._unavailable(
                        variable,
                        Fetched(
                            ok=False,
                            source=self.name,
                            url=result.url,
                            error=f"{field} absent from the response",
                        ),
                    )
                ]
                continue

            canonical = UNITS.get(variable)
            upstream = normalise_unit(reported.get(field))
            if not _convertible(upstream, canonical):
                # Refuse rather than emit. A wrong unit in a safety verdict is
                # worse than a missing one, and this is the exact bug the check
                # was written to catch.
                log.error(
                    "%s: cannot convert %s from %r to %r — refusing the value",
                    self.name,
                    variable,
                    upstream,
                    canonical,
                )
                out[variable] = [
                    self._unavailable(
                        variable,
                        Fetched(
                            ok=False,
                            source=self.name,
                            url=result.url,
                            error=(
                                f"unconvertible unit for {variable}: upstream reports "
                                f"{upstream!r}, ORCA needs {canonical!r}"
                            ),
                        ),
                    )
                ]
                continue

            points: list[Evidence] = []
            for when, value in zip(times, values, strict=False):
                if value is None:
                    continue
                converted = convert(float(value), upstream, canonical)
                if converted is None:
                    continue
                points.append(self._evidence(variable, converted, when, lat, lon, result, upstream))
            out[variable] = points

        return out

    async def at(
        self, lat: float, lon: float, *, variables: list[str] | None = None
    ) -> dict[str, Evidence]:
        """The value nearest to now, per variable — what the risk engine wants."""
        series = await self.series(lat, lon, variables=variables, forecast_days=1)
        now = utcnow()
        out: dict[str, Evidence] = {}
        for variable, points in series.items():
            if not points:
                continue
            out[variable] = min(
                points, key=lambda e: abs((e.freshness.valid_time - now).total_seconds())
            )
        return out

    # ------------------------------------------------------------- internals
    def _evidence(
        self,
        variable: str,
        value: float,
        when: datetime,
        lat: float,
        lon: float,
        result: Fetched,
        upstream_unit: str | None = None,
    ) -> Evidence:
        canonical = UNITS.get(variable)
        if variable == "convective_energy":
            note = (
                "CAPE (convective available potential energy) — a thunderstorm-potential "
                "proxy, not lightning detection. No free authoritative lightning source "
                "covers India."
            )
        elif upstream_unit and upstream_unit != canonical:
            note = f"converted from {upstream_unit}, as declared by the upstream response"
        else:
            note = None

        return Evidence(
            dataset_id=self.name,
            provider=Provider.OPEN_METEO,
            variable=variable,
            value=round(value, 4),
            unit=canonical,
            provenance=result.provenance,
            freshness=Freshness.of(variable, when),
            url=self.docs_url,
            location=(lon, lat),
            notes=note,
            citations=[
                Citation(
                    label="Open-Meteo (ECMWF IFS / GFS Wave)",
                    provider=Provider.OPEN_METEO,
                    url=self.docs_url,
                    identifier=self.name,
                )
            ],
        )

    def _unavailable(self, variable: str, result: Fetched) -> Evidence:
        return Evidence.unavailable(
            dataset_id=self.name,
            provider=Provider.OPEN_METEO,
            variable=variable,
            reason=result.error or "unknown failure",
            url=self.docs_url,
        )


class OpenMeteoMarine(_OpenMeteoBase):
    """Waves, swell, SST and surface currents."""

    name = "open_meteo.marine"
    variables = tuple(MARINE_FIELDS)
    url = MARINE_URL
    fields = MARINE_FIELDS
    docs_url = "https://open-meteo.com/en/docs/marine-weather-api"
    cell_selection = "sea"


class OpenMeteoForecast(_OpenMeteoBase):
    """Wind, gusts, visibility, precipitation, convective energy."""

    name = "open_meteo.forecast"
    variables = tuple(FORECAST_FIELDS)
    url = FORECAST_URL
    fields = FORECAST_FIELDS
    docs_url = "https://open-meteo.com/en/docs"
    cell_selection = "land"


marine = OpenMeteoMarine()
forecast = OpenMeteoForecast()


async def conditions_at(lat: float, lon: float) -> dict[str, Evidence]:
    """Everything the risk engine needs at a point, from both APIs at once.

    Gathered concurrently because the two calls are independent and the demo's
    latency budget is visible on stage.
    """
    sea, air = await asyncio.gather(marine.at(lat, lon), forecast.at(lat, lon))
    return {**sea, **air}


async def series_at(lat: float, lon: float, *, forecast_days: int = 3) -> dict[str, list[Evidence]]:
    """Full hourly series from both APIs — the input to the trip monitor and the
    forecast charts."""
    sea, air = await asyncio.gather(
        marine.series(lat, lon, forecast_days=forecast_days),
        forecast.series(lat, lon, forecast_days=forecast_days),
    )
    return {**sea, **air}


# --------------------------------------------------------------------------- #
# coarse vector-field sampling
# --------------------------------------------------------------------------- #

#: Points per multi-point request. Measured, not guessed: 500 points is a ~5.6 KB
#: URL and returns 200; 1000 points is ~13 KB and returns 414 URI Too Long.
MAX_POINTS_PER_REQUEST = 500

#: Open-Meteo's free tier is 600 calls per MINUTE and 5000 per hour, and a
#: multi-point request counts each LOCATION as a call — not each request. So a
#: 400-point batch spends 400 of the minute's budget.
#:
#: This is metered on our side rather than discovered from 429s. Learning the
#: limit by hitting it costs the whole batch, and a lost batch leaves a hole in a
#: flow field that looks like slack water rather than missing data. Repeated
#: testing exhausted the hourly budget entirely, which on demo day would mean no
#: flow layer and no explanation.
CALLS_PER_MINUTE_BUDGET = 500
CALLS_PER_HOUR_BUDGET = 4000


class _CallBudget:
    """Sliding-window counter for Open-Meteo's location-cost model."""

    def __init__(self) -> None:
        self._events: list[tuple[float, int]] = []

    def _prune(self, now: float) -> None:
        self._events = [(t, n) for t, n in self._events if now - t < 3600]

    def spent(self, window_s: float) -> int:
        now = time.monotonic()
        self._prune(now)
        return sum(n for t, n in self._events if now - t < window_s)

    def can_afford(self, calls: int) -> tuple[bool, str | None]:
        if self.spent(60) + calls > CALLS_PER_MINUTE_BUDGET:
            return False, (
                f"would exceed the per-minute budget "
                f"({self.spent(60)} spent of {CALLS_PER_MINUTE_BUDGET})"
            )
        if self.spent(3600) + calls > CALLS_PER_HOUR_BUDGET:
            return False, (
                f"would exceed the hourly budget "
                f"({self.spent(3600)} spent of {CALLS_PER_HOUR_BUDGET})"
            )
        return True, None

    def charge(self, calls: int) -> None:
        self._events.append((time.monotonic(), calls))

    def describe(self) -> dict[str, Any]:
        return {
            "spent_last_minute": self.spent(60),
            "minute_budget": CALLS_PER_MINUTE_BUDGET,
            "spent_last_hour": self.spent(3600),
            "hour_budget": CALLS_PER_HOUR_BUDGET,
            "note": (
                "A multi-point request costs one call PER LOCATION, so a 240-point "
                "lattice spends 240 of the budget, not 1."
            ),
        }


budget = _CallBudget()

#: Pause between batches. Requesting back-to-back earned a 429 during testing —
#: and a 429 silently costs a whole batch, so one of three vanished and an
#: 800-sample field looked like a complete 1000-sample one. Open-Meteo's free
#: tier is a courtesy we would rather not lose, so this is deliberately generous.
BATCH_PAUSE_S = 1.5


async def sample_vectors(
    lats: list[float],
    lons: list[float],
    *,
    kind: str = "wind",
) -> list[tuple[float, float, float, float]]:
    """Sample a vector field at many points, returning ``(lat, lon, u, v)``.

    ``kind`` is ``"wind"`` or ``"current"``. The direction convention differs
    between them — wind is reported as the direction it comes FROM, current as
    the direction it flows TO — and the conversion is delegated to
    :func:`orca.science.vectorfield.to_uv` rather than inlined here, because
    getting the sign wrong produces a field that looks fine and points backwards.

    Requests are batched and paced. Called by the ingest job, never in a request
    path.
    """
    from orca.science.vectorfield import DIRECTION_CONVENTION, to_uv

    if len(lats) != len(lons):
        raise ValueError("lats and lons must be the same length")

    if kind == "wind":
        source: _OpenMeteoBase = forecast
        speed_field, direction_field = "wind_speed_10m", "wind_direction_10m"
        extra: dict[str, Any] = {"wind_speed_unit": "ms"}
    elif kind == "current":
        source = marine
        speed_field, direction_field = "ocean_current_velocity", "ocean_current_direction"
        # No wind_speed_unit here: Open-Meteo would apply it to the current
        # velocity too, which is exactly the bug that had a 0.9 kn current
        # labelled 0.9 m/s. The response's declared unit is converted instead.
        extra = {}
    else:
        raise ValueError(f"unknown vector kind {kind!r}")

    convention = DIRECTION_CONVENTION[kind]
    out: list[tuple[float, float, float, float]] = []

    for start in range(0, len(lats), MAX_POINTS_PER_REQUEST):
        chunk_lats = lats[start : start + MAX_POINTS_PER_REQUEST]
        chunk_lons = lons[start : start + MAX_POINTS_PER_REQUEST]

        affordable, reason = budget.can_afford(len(chunk_lats))
        if not affordable:
            log.warning(
                "%s vector batch %d-%d skipped: %s. The field will be incomplete rather "
                "than risk a 429 that costs the whole batch.",
                kind,
                start,
                start + len(chunk_lats),
                reason,
            )
            continue

        params: dict[str, Any] = {
            "latitude": ",".join(f"{v:.3f}" for v in chunk_lats),
            "longitude": ",".join(f"{v:.3f}" for v in chunk_lons),
            "current": f"{speed_field},{direction_field}",
            "timezone": "UTC",
            "cell_selection": source.cell_selection,
            **extra,
        }
        budget.charge(len(chunk_lats))
        result = await source.fetch(source.url, params=params, conditional=False)
        if not result.ok:
            # Loud, and counted: a dropped batch leaves a hole in the field, and
            # a hole in a flow field looks like slack water rather than missing
            # data. The caller compares the sample count to what it asked for.
            log.warning(
                "%s vector batch %d-%d of %d FAILED (%s) — the field will have a gap",
                kind,
                start,
                start + len(chunk_lats),
                len(lats),
                result.error,
            )
            continue

        try:
            payload = result.json()
        except ValueError as exc:
            log.warning("%s vector batch unparseable: %s", kind, exc)
            continue

        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            current = entry.get("current") or {}
            units = entry.get("current_units") or {}
            speed = current.get(speed_field)
            direction = current.get(direction_field)
            if speed is None or direction is None:
                continue

            # The response declares its own unit; convert rather than assume.
            speed_unit = normalise_unit(units.get(speed_field))
            speed_ms = convert(float(speed), speed_unit, "m/s")
            if speed_ms is None:
                log.error(
                    "%s: cannot convert speed from %r to m/s — refusing the sample",
                    kind,
                    speed_unit,
                )
                continue

            u, v = to_uv(speed_ms, float(direction), convention)
            out.append((float(entry["latitude"]), float(entry["longitude"]), u, v))

        if start + MAX_POINTS_PER_REQUEST < len(lats):
            await asyncio.sleep(BATCH_PAUSE_S)

    if len(out) < len(lats) * 0.9:
        log.warning(
            "%s vector field is incomplete: %d of %d points returned",
            kind,
            len(out),
            len(lats),
        )
    return out


def sample_lattice(grid: Any, *, step_deg: float = 2.0) -> tuple[list[float], list[float]]:
    """A coarse lattice over a grid, for vector sampling.

    Deliberately much coarser than the raster grid: the GPU interpolates between
    texture cells for free, so a 1.5-degree lattice advects particles smoothly
    while costing one API request rather than forty.

    2 degrees over the AOI is ~240 points, so BOTH fields together are ~480 calls
    and fit inside Open-Meteo's 600-per-minute budget in a single ingest pass —
    where each *location* counts as a call, not each request. 1.5 degrees was
    tried first and left the two fields unable to run in the same minute.
    """
    import numpy as np

    out_lats: list[float] = []
    out_lons: list[float] = []
    for lat in np.arange(grid.south + step_deg / 2, grid.north, step_deg):
        for lon in np.arange(grid.west + step_deg / 2, grid.east, step_deg):
            out_lats.append(float(lat))
            out_lons.append(float(lon))
    return out_lats, out_lons

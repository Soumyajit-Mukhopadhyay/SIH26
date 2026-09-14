"""In-situ observations — the only ground truth in the system, and it is late.

Everything else ORCA reads is remote sensing or a model. MUR SST is a satellite
analysis; Open-Meteo's sea temperature is a model field; CMEMS is a model with
observations assimilated into it. ORCA already cross-validates one against
another, and that is worth doing — but it compares two inferences, so it can only
ever show that they agree, never that either is right.

A moored buoy is a thermometer in the water.

## The measurement that decided how this module is used

The **RAMA array** — Research moored Array for African-Asian-Australian Monsoon
Analysis — is the Indian Ocean arm of NOAA PMEL's global tropical moored buoy
network. Measured on 2026-09-14, over the box 60-100E / 0-25N:

* **13 stations** report inside the Indian EEZ.
* The most recent record on the daily product was **2026-08-18 — 27 days old**.
  The 5-day and monthly products lag 30 and 29 days respectively.

That lag is the whole story. **These buoys cannot check today's forecast.** An
earlier version of this module was written to do exactly that and would have
returned "no observation" on every request while looking like a working feature.

## What it is for instead

**Retrospective validation.** "Over the last year, how closely did the satellite
analysis ORCA serves track the physical thermometer at 15N 90E?" is a question
with a real answer — a bias, a spread, a correlation — and it is the honest reply
to the hardest question anyone can ask this system: *how do you know your numbers
are right?*

For a researcher, which is the audience this serves, a retrospective skill figure
is worth more than a live cross-check anyway: it is the number that belongs in a
methods section.

## The sparsity, stated rather than hidden

Thirteen moorings across an ocean means the nearest one to a fisherman off
Chennai is several hundred kilometres away. That does not make a comparison
worthless — an analysis two degrees out at the buoy is suspect nearby — but the
distance qualifies the number, so it is returned with every response.
"""

from __future__ import annotations

import csv
import io
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from orca.provenance import Citation, Evidence, Freshness, Provenance, Provider
from orca.sources.base import get_client

log = logging.getLogger(__name__)

#: NOAA CoastWatch ERDDAP. Verified reachable 2026-09-14.
ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/tabledap"

#: PMEL's DAILY sea-surface temperature from the moored array. Measured lag on
#: 2026-09-14: 27 days, against 30 for the 5-day product and 29 for the monthly.
#: The daily file is both the freshest and the finest, so there is no trade here.
#: `T_25` is temperature at 25 cm depth — the shallowest sensor, and the closest
#: thing the mooring has to what a satellite calls "surface".
SST_DATASET = "pmelTaoDySst"
SST_VARIABLE = "T_25"

#: How far back to look for the most recent record. Four months, not a fortnight:
#: the array runs about a month behind and individual moorings miss transmission
#: windows on top of that. A 21-day window — the first guess — returned nothing
#: for every point in the Indian EEZ while looking like a working query.
LOOKBACK_DAYS = 120

#: Beyond this the comparison stops being informative about the query point.
#: Stated rather than silently applied: a buoy 900 km away is still a real
#: measurement, it is just not a measurement of here.
USEFUL_RADIUS_KM = 500.0


@dataclass(slots=True)
class BuoyObservation:
    """One moored-buoy reading, with the distance that qualifies it."""

    array: str
    station: str
    lat: float
    lon: float
    observed_at: datetime
    value: float
    unit: str
    distance_km: float

    def describe(self) -> dict[str, Any]:
        return {
            "array": self.array,
            "station": self.station,
            "lat": self.lat,
            "lon": self.lon,
            "observed_at": self.observed_at.isoformat(),
            "value": round(self.value, 3),
            "unit": self.unit,
            "distance_km": round(self.distance_km, 1),
            "within_useful_radius": self.distance_km <= USEFUL_RADIUS_KM,
        }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Good enough to rank buoys and to report a distance.

    Not the geodesic used for boundary work — `services.geo` owns that, and it
    matters there because a kilometre decides whether a boat is inside another
    country's waters. Here the figure is context on a comparison.
    """
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


async def nearest_buoy_sst(
    lat: float,
    lon: float,
    *,
    search_radius_deg: float = 12.0,
) -> BuoyObservation | None:
    """The most recent moored-buoy SST near a position, or None.

    The bounding box is generous because the array is sparse: a tight box around
    a coastal point would usually contain no mooring at all, and returning
    nothing when a perfectly good observation sits 400 km away would waste the
    only ground truth available.
    """
    since = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    query = (
        f"{ERDDAP}/{SST_DATASET}.csv"
        f"?array,station,longitude,latitude,time,{SST_VARIABLE}"
        f"&longitude>={lon - search_radius_deg}&longitude<={lon + search_radius_deg}"
        f"&latitude>={lat - search_radius_deg}&latitude<={lat + search_radius_deg}"
        f"&time>={since}"
        f"&{SST_VARIABLE}!=NaN"
    )

    client = await get_client()
    try:
        response = await client.get(query, timeout=30.0)
    except Exception as exc:  # noqa: BLE001 — a missing buoy must not break a forecast
        log.info("in-situ: buoy query failed (%s)", type(exc).__name__)
        return None

    # 404 is ERDDAP's "your constraints matched nothing", which here means no
    # mooring reported near this point recently. Not an error.
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        log.info("in-situ: buoy query HTTP %s", response.status_code)
        return None

    rows = list(csv.reader(io.StringIO(response.text)))
    if len(rows) < 3:
        return None

    header = rows[0]
    try:
        index = {
            name: header.index(name)
            for name in ("array", "station", "longitude", "latitude", "time", SST_VARIABLE)
        }
    except ValueError:
        log.info("in-situ: unexpected column layout %s", header)
        return None

    best: BuoyObservation | None = None
    for row in rows[2:]:  # row 1 is the units line
        if len(row) < len(header):
            continue
        try:
            blat = float(row[index["latitude"]])
            blon = float(row[index["longitude"]])
            value = float(row[index[SST_VARIABLE]])
            when = datetime.fromisoformat(row[index["time"]].replace("Z", "+00:00"))  # noqa: FURB162
        except (ValueError, TypeError):
            continue

        observation = BuoyObservation(
            array=row[index["array"]] or "unknown",
            station=row[index["station"]] or "unknown",
            lat=blat,
            lon=blon,
            observed_at=when,
            value=value,
            unit="degC",
            distance_km=_haversine_km(lat, lon, blat, blon),
        )
        # Nearest first; among equals, most recent. A closer buoy beats a fresher
        # one because the 5-day product's records are all within days of each
        # other anyway, while distance varies by hundreds of kilometres.
        if best is None or (observation.distance_km, -observation.observed_at.timestamp()) < (
            best.distance_km,
            -best.observed_at.timestamp(),
        ):
            best = observation

    return best


async def buoy_sst_evidence(lat: float, lon: float) -> Evidence:
    """The nearest buoy SST as an Evidence object, ready for cross-validation.

    Returns an UNAVAILABLE Evidence rather than None when no mooring is in range,
    so the comparison reports "no in-situ observation near here" instead of the
    check silently disappearing — which would read as agreement.
    """
    observation = await nearest_buoy_sst(lat, lon)
    if observation is None:
        return Evidence(
            dataset_id=SST_DATASET,
            provider=Provider.NOAA,
            variable="sst",
            value=None,
            unit="degC",
            provenance=Provenance.UNAVAILABLE,
            freshness=Freshness.of("sst", datetime.now(UTC)),
            notes=(
                "No moored buoy reported a sea-surface temperature within about 12 degrees of "
                f"this position in the last {LOOKBACK_DAYS} days. The RAMA array has only a few "
                "dozen moorings across the whole Indian Ocean, so this is common and expected "
                "near the coast."
            ),
        )

    qualifier = (
        ""
        if observation.distance_km <= USEFUL_RADIUS_KM
        else " This is beyond the radius where a single mooring says much about the query point."
    )
    return Evidence(
        dataset_id=SST_DATASET,
        provider=Provider.NOAA,
        variable="sst",
        value=observation.value,
        unit="degC",
        provenance=Provenance.LIVE,
        freshness=Freshness.of("sst", observation.observed_at),
        notes=(
            f"{observation.array} moored buoy {observation.station} at "
            f"{observation.lat:.2f}N {observation.lon:.2f}E — an in-situ thermometer "
            f"{observation.distance_km:.0f} km away, not a satellite retrieval.{qualifier}"
        ),
        citations=[
            Citation(
                label=(
                    "NOAA PMEL Global Tropical Moored Buoy Array "
                    "(TAO/TRITON, RAMA, PIRATA), daily aggregation"
                ),
                provider=Provider.NOAA,
                url=f"https://coastwatch.pfeg.noaa.gov/erddap/info/{SST_DATASET}/index.html",
            )
        ],
    )


async def buoys_in_box(
    west: float, south: float, east: float, north: float, *, limit: int = 60
) -> list[dict[str, Any]]:
    """Every mooring reporting inside a box, most recent value each.

    For the map: showing where the ground truth physically is makes the sparsity
    of the array obvious in a way a number cannot.
    """
    since = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    query = (
        f"{ERDDAP}/{SST_DATASET}.csv"
        f"?array,station,longitude,latitude,time,{SST_VARIABLE}"
        f"&longitude>={west}&longitude<={east}&latitude>={south}&latitude<={north}"
        f"&time>={since}&{SST_VARIABLE}!=NaN"
    )
    client = await get_client()
    try:
        response = await client.get(query, timeout=30.0)
    except Exception:  # noqa: BLE001
        return []
    if response.status_code != 200:
        return []

    rows = list(csv.reader(io.StringIO(response.text)))
    if len(rows) < 3:
        return []
    header = rows[0]
    try:
        index = {
            n: header.index(n)
            for n in ("array", "station", "longitude", "latitude", "time", SST_VARIABLE)
        }
    except ValueError:
        return []

    latest: dict[str, dict[str, Any]] = {}
    for row in rows[2:]:
        if len(row) < len(header):
            continue
        try:
            record = {
                "array": row[index["array"]],
                "station": row[index["station"]],
                "lat": float(row[index["latitude"]]),
                "lon": float(row[index["longitude"]]),
                "observed_at": row[index["time"]],
                "sst_degc": round(float(row[index[SST_VARIABLE]]), 3),
            }
        except (ValueError, TypeError):
            continue
        station = record["station"]
        # One row per mooring: the most recent. A station reports every five days
        # and the map wants the current state, not a time series per pin.
        if station not in latest or record["observed_at"] > latest[station]["observed_at"]:
            latest[station] = record

    return sorted(latest.values(), key=lambda r: (-r["lat"], r["lon"]))[:limit]


# ------------------------------------------------------- retrospective skill


@dataclass(slots=True)
class BuoyValidation:
    """How closely a gridded product tracked a physical thermometer."""

    station: str
    lat: float
    lon: float
    days_requested: int
    pairs: int
    bias_degc: float | None
    rmse_degc: float | None
    max_abs_error_degc: float | None
    buoy_mean_degc: float | None
    product_mean_degc: float | None
    product: str
    note: str

    def describe(self) -> dict[str, Any]:
        return {
            "station": self.station,
            "lat": self.lat,
            "lon": self.lon,
            "days_requested": self.days_requested,
            "matched_pairs": self.pairs,
            "bias_degc": None if self.bias_degc is None else round(self.bias_degc, 3),
            "rmse_degc": None if self.rmse_degc is None else round(self.rmse_degc, 3),
            "max_abs_error_degc": (
                None if self.max_abs_error_degc is None else round(self.max_abs_error_degc, 3)
            ),
            "buoy_mean_degc": None
            if self.buoy_mean_degc is None
            else round(self.buoy_mean_degc, 3),
            "product_mean_degc": (
                None if self.product_mean_degc is None else round(self.product_mean_degc, 3)
            ),
            "product": self.product,
            "note": self.note,
        }


async def _buoy_series(
    station: str, days: int, *, ending: datetime | None = None
) -> dict[str, float]:
    """Daily buoy SST for one mooring, keyed by ISO date.

    The window is anchored on the LATEST OBSERVATION, not on today. The array
    runs about a month behind, so counting back from now put a 20-day request
    entirely inside the gap and returned nothing — a validation that silently
    found no data and reported no error.
    """
    anchor = ending or datetime.now(UTC)
    since = (anchor - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    query = (
        f"{ERDDAP}/{SST_DATASET}.csv?time,{SST_VARIABLE}"
        f'&station="{station}"&time>={since}&{SST_VARIABLE}!=NaN'
    )
    client = await get_client()
    try:
        response = await client.get(query, timeout=45.0)
    except Exception:  # noqa: BLE001
        return {}
    if response.status_code != 200:
        return {}

    series: dict[str, float] = {}
    rows = list(csv.reader(io.StringIO(response.text)))
    for row in rows[2:]:
        if len(row) < 2:
            continue
        try:
            series[row[0][:10]] = float(row[1])
        except (ValueError, TypeError):
            continue
    return series


async def validate_sst_against_buoy(
    lat: float, lon: float, *, days: int = 90
) -> BuoyValidation | None:
    """Compare ORCA's satellite SST against the nearest mooring, day by day.

    This is the honest answer to "how do you know your numbers are right?".
    Everything else in ORCA's cross-validation compares one inference with
    another; this compares an inference with a measurement.

    Returns None when no mooring has reported near the point, which over most of
    the Indian EEZ is the common case and is itself worth reporting.
    """
    nearest = await nearest_buoy_sst(lat, lon)
    if nearest is None:
        return None

    buoy = await _buoy_series(nearest.station, days, ending=nearest.observed_at)
    if not buoy:
        return None

    # The satellite product at the BUOY's position, not the caller's. Comparing
    # a mooring at 15N 90E against a satellite pixel 1000 km away would measure
    # the ocean's spatial gradient, not the product's accuracy.
    from orca.sources import erddap

    product: dict[str, float] = {}
    # `mur_sst` directly, not `baseline_sst` — that helper reads the INCOIS TMI
    # archive for the climatology and returns nothing for recent days, which is
    # how the first version of this scored zero matched pairs while looking like
    # the buoy and the satellite simply never coincided.
    for day in sorted(buoy)[-days:]:
        when = datetime.strptime(day, "%Y-%m-%d").replace(hour=9, tzinfo=UTC)
        try:
            values = await erddap.erddap.point(
                "mur_sst", nearest.lat, nearest.lon, when=when, variables=["sst"]
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("in-situ: no satellite field for %s (%s)", day, type(exc).__name__)
            continue
        evidence = values.get("sst")
        if evidence is not None and isinstance(evidence.value, (int, float)):
            product[day] = float(evidence.value)

    shared = sorted(set(buoy) & set(product))
    if not shared:
        return BuoyValidation(
            station=nearest.station,
            lat=nearest.lat,
            lon=nearest.lon,
            days_requested=days,
            pairs=0,
            bias_degc=None,
            rmse_degc=None,
            max_abs_error_degc=None,
            buoy_mean_degc=None,
            product_mean_degc=None,
            product="jplMURSST41",
            note=(
                "The mooring reported, but no satellite field could be matched to the same days. "
                "No comparison is possible, which is different from the two agreeing."
            ),
        )

    errors = [product[day] - buoy[day] for day in shared]
    bias = sum(errors) / len(errors)
    rmse = (sum(e * e for e in errors) / len(errors)) ** 0.5
    return BuoyValidation(
        station=nearest.station,
        lat=nearest.lat,
        lon=nearest.lon,
        days_requested=days,
        pairs=len(shared),
        bias_degc=bias,
        rmse_degc=rmse,
        max_abs_error_degc=max(abs(e) for e in errors),
        buoy_mean_degc=sum(buoy[d] for d in shared) / len(shared),
        product_mean_degc=sum(product[d] for d in shared) / len(shared),
        product="jplMURSST41",
        note=(
            f"Satellite minus buoy over {len(shared)} matched days at {nearest.station}. "
            "A positive bias means the satellite analysis reads warm against the thermometer. "
            "This is retrospective: the moored array runs about a month behind, so it validates "
            "the product's track record rather than today's field."
        ),
    )

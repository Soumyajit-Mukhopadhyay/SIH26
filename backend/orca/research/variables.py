"""What the dataset builder can actually deliver, and where each value comes from.

## Why this registry exists

The builder's first version chose sources from the catalogue by resolution and
delivered two variables out of seventeen. Everything else came back as a column
of blanks, for two reasons that were knowable in advance: several INCOIS registry
entries carry no variable mapping at all, and three are archives that stopped in
2014, 2020 and 2023.

So the rule here is the inverse of the old one: **a variable appears in this
registry only if it has been fetched successfully at least once.** The list is
short and true rather than long and aspirational. Advertising a variable and
handing back an empty column is worse than omitting it — a researcher cannot
tell "no data that day" from "never possible".

## Two kinds of source, and why the distinction matters

**Range sources** answer for a whole date span in one request. Open-Meteo's
marine and ERA5-archive endpoints take `start_date` and `end_date` and return the
series. Ninety days of wave height is ONE call.

**Per-day sources** answer for one timestamp. ERDDAP's griddap is one, so ninety
days of MUR SST is ninety calls.

Treating them identically is what made the first builder slow: it looped days and
issued a request per day per variable regardless, so a range source was hit
ninety times for data it would have returned in one response. The cost model in
`plan()` counts them differently for the same reason.

## Provenance travels with the variable

Every entry carries its provider, licence and the caveat that belongs to it, so
the workbook's Provenance sheet is assembled from the variables actually
delivered rather than from a guess about which dataset was used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceKind = Literal["openmeteo_marine", "openmeteo_era5", "erddap"]


@dataclass(frozen=True, slots=True)
class BuildVariable:
    """One deliverable column."""

    name: str
    kind: SourceKind
    #: The provider's own parameter name. For ERDDAP, the ORCA variable name.
    api_name: str
    unit: str
    description: str
    provider: str
    licence: str
    endpoint: str
    caveat: str
    #: Earliest date the source can answer for, as ISO. None = no stated limit.
    available_from: str | None = None

    @property
    def is_range_source(self) -> bool:
        """True when one request covers the whole span."""
        return self.kind in ("openmeteo_marine", "openmeteo_era5")


_OM_LICENCE = "CC BY 4.0 — attribution required (Open-Meteo)"
_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_ERA5_URL = "https://archive-api.open-meteo.com/v1/archive"

#: Every variable verified by an actual fetch on 2026-09-14. Two that the API
#: advertises are absent because they returned nulls for every day tested:
#: `ocean_current_velocity_max` on the marine endpoint, and `cape_max` /
#: `visibility_min` on the ERA5 archive. They are recorded in UNVERIFIED below
#: rather than deleted, so nobody re-adds them hopefully.
BUILD_VARIABLES: tuple[BuildVariable, ...] = (
    # ---- sea state, from the marine model ----
    BuildVariable(
        "wave_height", "openmeteo_marine", "wave_height_max", "m",
        "Daily maximum significant wave height",
        "Open-Meteo marine", _OM_LICENCE, _MARINE_URL,
        "A model forecast/reanalysis, not a buoy measurement. Daily MAXIMUM, not a daily mean — "
        "the figure a safety threshold should be compared against, and higher than a mean.",
    ),
    BuildVariable(
        "wave_period", "openmeteo_marine", "wave_period_max", "s",
        "Daily maximum peak wave period",
        "Open-Meteo marine", _OM_LICENCE, _MARINE_URL,
        "Period drives wavelength through L = gT^2/2pi, so a long period at modest height is a "
        "swell, not a chop.",
    ),
    BuildVariable(
        "wave_direction", "openmeteo_marine", "wave_direction_dominant", "deg",
        "Dominant wave direction",
        "Open-Meteo marine", _OM_LICENCE, _MARINE_URL,
        "Direction waves come FROM, the meteorological convention — the opposite of the current "
        "convention. Mixing the two silently reverses a drift calculation.",
    ),
    BuildVariable(
        "swell_height", "openmeteo_marine", "swell_wave_height_max", "m",
        "Daily maximum swell height",
        "Open-Meteo marine", _OM_LICENCE, _MARINE_URL,
        "The long-period component only. Total sea state is swell and wind-wave combined, so "
        "this is always at or below wave_height.",
    ),
    BuildVariable(
        "swell_period", "openmeteo_marine", "swell_wave_period_max", "s",
        "Daily maximum swell period",
        "Open-Meteo marine", _OM_LICENCE, _MARINE_URL,
        "Long swell periods can arrive from storms thousands of kilometres away under a locally "
        "calm sky.",
    ),
    BuildVariable(
        "wind_wave_height", "openmeteo_marine", "wind_wave_height_max", "m",
        "Daily maximum locally wind-generated wave height",
        "Open-Meteo marine", _OM_LICENCE, _MARINE_URL,
        "The locally generated component. Separating it from swell is what tells you whether a "
        "rough sea is local weather or a distant storm.",
    ),
    BuildVariable(
        "sea_surface_temperature", "openmeteo_marine", "sea_surface_temperature_max", "degC",
        "Daily maximum modelled sea-surface temperature",
        "Open-Meteo marine", _OM_LICENCE, _MARINE_URL,
        "A MODEL field, distinct from the `sst` column which is NASA's MUR satellite analysis. "
        "Request both to compare them — that comparison is the point of having both.",
    ),
    # ---- atmosphere, from ERA5 reanalysis ----
    BuildVariable(
        "wind_speed", "openmeteo_era5", "wind_speed_10m_max", "km/h",
        "Daily maximum 10 m wind speed",
        "Open-Meteo / ECMWF ERA5", _OM_LICENCE, _ERA5_URL,
        "ERA5 reanalysis at roughly 0.25 deg, so it will not resolve a coastal sea breeze. "
        "Units are km/h here, NOT knots — divide by 1.852 to compare with ORCA's verdict "
        "thresholds, which are in knots.",
        available_from="1940-01-01",
    ),
    BuildVariable(
        "wind_gust", "openmeteo_era5", "wind_gusts_10m_max", "km/h",
        "Daily maximum wind gust",
        "Open-Meteo / ECMWF ERA5", _OM_LICENCE, _ERA5_URL,
        "Gusts, not sustained wind. Also km/h rather than knots.",
        available_from="1940-01-01",
    ),
    BuildVariable(
        "precipitation", "openmeteo_era5", "precipitation_sum", "mm",
        "Daily total precipitation",
        "Open-Meteo / ECMWF ERA5", _OM_LICENCE, _ERA5_URL,
        "A daily SUM, unlike the maxima around it. Over the Indian coast the monsoon signal "
        "dominates this column entirely.",
        available_from="1940-01-01",
    ),
    BuildVariable(
        "air_temperature", "openmeteo_era5", "temperature_2m_max", "degC",
        "Daily maximum 2 m air temperature",
        "Open-Meteo / ECMWF ERA5", _OM_LICENCE, _ERA5_URL,
        "Air, not sea. Over water the two diverge most in the pre-monsoon months.",
        available_from="1940-01-01",
    ),
    BuildVariable(
        "solar_radiation", "openmeteo_era5", "shortwave_radiation_sum", "MJ/m2",
        "Daily shortwave radiation total",
        "Open-Meteo / ECMWF ERA5", _OM_LICENCE, _ERA5_URL,
        "Useful as a cloud-cover proxy and as a driver term in productivity work.",
        available_from="1940-01-01",
    ),
    # ---- satellite observation, from ERDDAP ----
    BuildVariable(
        "sst", "erddap", "mur_sst", "degC",
        "NASA MUR satellite sea-surface temperature analysis",
        "NASA JPL / NOAA CoastWatch", "Public domain (NASA/NOAA)",
        "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41",
        "A gap-filled satellite ANALYSIS, not a raw radiometer swath. ORCA validated it against "
        "a RAMA moored buoy: bias -0.05 degC, RMSE 0.167 degC over 30 matched days. One call per "
        "day, so long ranges are slower than the Open-Meteo columns.",
        available_from="2002-06-01",
    ),
    BuildVariable(
        "chlorophyll", "erddap", "esacci_chl_monthly", "mg m-3",
        "ESA CCI ocean colour chlorophyll-a",
        "Plymouth Marine Laboratory", "ESA CCI open licence",
        "https://rsg.pml.ac.uk/erddap/griddap/pmlEsaCCI60OceanColorMonthly",
        "MONTHLY composites. A daily request returns one value per month at best, and the "
        "monsoon months are heavily cloud-gapped over the Bay of Bengal even after compositing.",
        available_from="1997-09-01",
    ),
)

BY_NAME: dict[str, BuildVariable] = {v.name: v for v in BUILD_VARIABLES}

#: Advertised by a provider but returning nulls for every day tested on
#: 2026-09-14. Recorded so they are not re-added hopefully, and so the next
#: person knows the absence was checked rather than overlooked.
UNVERIFIED: dict[str, str] = {
    "ocean_current_velocity_max": "Open-Meteo marine — accepted, returns null for every day",
    "cape_max": "Open-Meteo ERA5 archive — accepted, returns null for every day",
    "visibility_min": "Open-Meteo ERA5 archive — accepted, returns null for every day",
}


def names() -> list[str]:
    return sorted(BY_NAME)


def grouped() -> dict[str, list[str]]:
    """Variables by source, for a UI that wants to explain where each came from."""
    out: dict[str, list[str]] = {}
    for variable in BUILD_VARIABLES:
        out.setdefault(variable.kind, []).append(variable.name)
    return {k: sorted(v) for k, v in out.items()}

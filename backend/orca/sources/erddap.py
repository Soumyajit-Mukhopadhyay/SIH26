"""ERDDAP griddap — one adapter for every ERDDAP server we use.

INCOIS and NOAA CoastWatch speak the identical protocol, so there is one client
and a dataset registry rather than two adapters that drift apart.

**The finding that shaped this module.** The plan assumed INCOIS ERDDAP would
carry ORCA's live Indian-satellite provenance. It cannot: of the fifteen griddap
datasets INCOIS publishes, only ARGO 10-day is still updating. Measured
2026-08-30:

    incois_argo_10day_McCreary       ... 2026-07-30   current
    ascat_daily_datasets             ... 2023-05-21   3 years stale
    incois_oceansat2_datasets        ... 2020-05-01   Oceansat-2 OCM ended
    incois_valueadded_products       ... 2019-03-30
    incois_tmi_3day_datasets         ... 2014-12-31   TMI instrument died 2015
    NOAA_AVHRR_AMSR_datasets         ... 2011-10-04
    incois_argo_sst_weekly           ... 2010-12-29
    IRS_chlorophyll_datasets         ... 2006-03-21

That is not a dead end, it is a reassignment. A 1997–2014 SST archive is exactly
what the anomaly and Hobday marine-heatwave computation need — you cannot compute
an anomaly without a long baseline — so the INCOIS archives become ORCA's
**climatology**, tagged ``CURATED``, and today's field comes from ``jplMURSST41``
(MUR SST 1 km on NOAA CoastWatch, zero auth, current to yesterday) and CMEMS.

Two consequences enforced in code:

* Every dataset declares its ``coverage_end``. Asking an archive for "now"
  returns ``UNAVAILABLE`` with the real reason, never a 2014 value wearing
  today's timestamp.
* Evidence always carries the grid cell's true ``valid_time``, so the freshness
  contract flags a decade-old baseline as decade-old. That is the system working,
  not the system failing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from orca.provenance import Citation, Evidence, Freshness, Provenance, Provider
from orca.sources.base import Fetched, Source

log = logging.getLogger(__name__)

INCOIS_SERVER = "https://erddap.incois.gov.in/erddap"
NOAA_SERVER = "https://coastwatch.pfeg.noaa.gov/erddap"


class Role(str):
    """What a dataset is *for*. Keeps "live field" and "climatological baseline"
    from being confused at the call site."""


LIVE = "live"
CLIMATOLOGY = "climatology"
ARCHIVE = "archive"


@dataclass(frozen=True, slots=True)
class GridDataset:
    """One griddap dataset, with the metadata needed to use it honestly."""

    dataset_id: str
    server: str
    provider: Provider
    #: ERDDAP variable name -> ORCA variable name.
    variables: dict[str, str]
    #: ORCA variable -> canonical unit, after conversion.
    units: dict[str, str]
    title: str
    role: str
    #: Last timestamp the dataset actually contains, measured not assumed.
    #: ``None`` means "rolling / still updating".
    coverage_end: datetime | None
    coverage_start: datetime | None = None
    #: Nominal update cadence in hours, used to decide how far back to look for
    #: the most recent populated slice.
    cadence_hours: float = 24.0
    #: Longitudes 0..360 rather than -180..180. Getting this wrong silently
    #: returns an empty grid, which the plan flags as a classic own-goal.
    lon_0_360: bool = False
    notes: str | None = None

    @property
    def is_current(self) -> bool:
        if self.coverage_end is None:
            return True
        return (datetime.now(UTC) - self.coverage_end) < timedelta(days=30)

    @property
    def provenance(self) -> Provenance:
        """An archive is CURATED reference material, not a stale measurement.
        A live grid served from our HTTP cache is CACHED, decided per-fetch."""
        return Provenance.CURATED if self.role in (CLIMATOLOGY, ARCHIVE) else Provenance.LIVE

    def url(self, path: str) -> str:
        return f"{self.server}/griddap/{self.dataset_id}{path}"


#: Coverage ends are the measured values from the module docstring, so a dataset
#: silently resuming (or a new archive appearing) shows up as a mismatch rather
#: than as a wrong answer.
DATASETS: dict[str, GridDataset] = {
    # ---------------------------------------------------------------- LIVE
    "mur_sst": GridDataset(
        dataset_id="jplMURSST41",
        server=NOAA_SERVER,
        provider=Provider.NASA,
        variables={"analysed_sst": "sst", "analysis_error": "sst_uncertainty"},
        units={"sst": "degC", "sst_uncertainty": "degC"},
        title="JPL MUR SST, Global, 0.01 deg, daily",
        role=LIVE,
        coverage_end=None,
        cadence_hours=24.0,
        notes="1 km multi-scale ultra-high-resolution SST. ORCA's live SST field.",
    ),
    "viirs_chl_monthly": GridDataset(
        dataset_id="erdVHNchlamday",
        server=NOAA_SERVER,
        provider=Provider.NOAA,
        variables={"chla": "chlorophyll"},
        units={"chlorophyll": "mg m-3"},
        title="VIIRS chlorophyll-a, Northeast Pacific + global, monthly",
        role=LIVE,
        coverage_end=None,
        cadence_hours=24.0 * 30,
        notes=(
            "Monthly composite — the zero-auth chlorophyll fallback. CMEMS is the "
            "primary for a current daily field."
        ),
    ),
    # ------------------------------------------- INDIAN, CURRENT (the one)
    "incois_argo_10day": GridDataset(
        dataset_id="incois_argo_10day_McCreary",
        server=INCOIS_SERVER,
        provider=Provider.INCOIS,
        variables={},  # resolved from the DAS on first use
        units={},
        title="INCOIS ARGO 10-day objective analysis (McCreary)",
        role=LIVE,
        coverage_end=None,
        cadence_hours=24.0 * 10,
        lon_0_360=True,
        notes="The only INCOIS griddap dataset still updating. Measured current 2026-07-30.",
    ),
    # ---------------------------------------- INDIAN ARCHIVES / BASELINE
    "incois_tmi_sst": GridDataset(
        dataset_id="incois_tmi_3day_datasets",
        server=INCOIS_SERVER,
        provider=Provider.INCOIS,
        variables={"SST": "sst"},
        units={"sst": "degC"},
        title="INCOIS TMI 3-day SST",
        role=CLIMATOLOGY,
        coverage_start=datetime(1997, 12, 7, tzinfo=UTC),
        coverage_end=datetime(2014, 12, 31, tzinfo=UTC),
        cadence_hours=72.0,
        lon_0_360=True,
        notes=(
            "TMI ended in 2015. 17 years of Indian-Ocean SST is ORCA's climatological "
            "baseline for the anomaly and Hobday marine-heatwave flag — the job it is "
            "genuinely best at."
        ),
    ),
    "incois_oceansat2": GridDataset(
        dataset_id="incois_oceansat2_datasets",
        server=INCOIS_SERVER,
        provider=Provider.INCOIS,
        variables={},
        units={},
        title="INCOIS Oceansat-2 OCM",
        role=ARCHIVE,
        coverage_start=datetime(2011, 2, 2, tzinfo=UTC),
        coverage_end=datetime(2020, 5, 1, tzinfo=UTC),
        lon_0_360=True,
        notes="ISRO Oceansat-2 ocean colour. Archive only — ends 2020-05-01.",
    ),
    "incois_irs_chl": GridDataset(
        dataset_id="IRS_chlorophyll_datasets",
        server=INCOIS_SERVER,
        provider=Provider.INCOIS,
        variables={},
        units={},
        title="IRS P4 OCM chlorophyll",
        role=ARCHIVE,
        coverage_start=datetime(2003, 1, 5, tzinfo=UTC),
        coverage_end=datetime(2006, 3, 21, tzinfo=UTC),
        lon_0_360=True,
        notes="ISRO IRS-P4 ocean colour. Archive only — ends 2006-03-21.",
    ),
    "incois_ascat_wind": GridDataset(
        dataset_id="ascat_daily_datasets",
        server=INCOIS_SERVER,
        provider=Provider.INCOIS,
        variables={},
        units={},
        title="ASCAT daily global wind field",
        role=ARCHIVE,
        coverage_end=datetime(2023, 5, 21, tzinfo=UTC),
        lon_0_360=True,
        notes="Ends 2023-05-21. Useful for a wind-stress climatology, not for today.",
    ),
}


def _encode_selector(selector: str) -> str:
    """Percent-encode a griddap subset selector.

    INCOIS runs ERDDAP behind Tomcat, which rejects raw ``[`` and ``]`` in a
    query string with a bare 400 before ERDDAP ever sees the request. NOAA's
    front end tolerates them, which is why this only shows up on the Indian
    server. Encoding is correct for both.
    """
    return selector.replace("[", "%5B").replace("]", "%5D")


class Erddap(Source):
    """griddap point and subset queries against any ERDDAP server."""

    name = "erddap"
    provider = Provider.NOAA
    variables = ("sst", "chlorophyll", "sst_uncertainty")
    docs_url = "https://coastwatch.pfeg.noaa.gov/erddap/information.html"
    timeout_s = 30.0  # ERDDAP subsets are slow; a short leash causes false failures

    _das_cache: ClassVar[dict[str, dict[str, Any]]] = {}

    # ------------------------------------------------------------------ time
    async def latest_time(self, key: str) -> datetime | None:
        """The most recent timestamp the dataset actually holds."""
        ds = DATASETS[key]
        # The subset selector must be inside the query string, not a param:
        # httpx would encode `?time[last]=` and ERDDAP rejects the `=`.
        result = await self.fetch(ds.url(".json?time%5Blast%5D"))
        if not result.ok:
            return None
        try:
            rows = result.json()["table"]["rows"]
            return datetime.fromisoformat(rows[-1][0])
        except (KeyError, IndexError, ValueError) as exc:
            log.warning("%s: cannot read time[last]: %s", ds.dataset_id, exc)
            return None

    # ----------------------------------------------------------------- point
    async def point(
        self,
        key: str,
        lat: float,
        lon: float,
        *,
        when: datetime | None = None,
        variables: list[str] | None = None,
    ) -> dict[str, Evidence]:
        """Grid values at a point, each as Evidence carrying the cell's real time.

        ``when=None`` means "the most recent slice this dataset has" — which for
        an archive is a decade ago, and is labelled as such rather than dressed
        up as current.
        """
        ds = DATASETS[key]
        wanted_orca = variables or list(ds.units) or None

        target = when
        if target is None:
            target = await self.latest_time(key)
            if target is None:
                return self._all_unavailable(
                    ds, wanted_orca, "could not determine the dataset's latest timestamp"
                )
        elif ds.coverage_end is not None and target > ds.coverage_end:
            # The honest refusal. Returning the nearest slice would hand back a
            # 2014 SST wearing today's date, which is the exact failure the
            # provenance model exists to prevent.
            return self._all_unavailable(
                ds,
                wanted_orca,
                f"{ds.dataset_id} coverage ends {ds.coverage_end:%Y-%m-%d}; "
                f"{target:%Y-%m-%d} is outside it. "
                f"{'Use mur_sst for a current field.' if 'sst' in ds.units else ''}".strip(),
            )

        erddap_vars = list(ds.variables) or await self._discover_variables(ds)
        if not erddap_vars:
            return self._all_unavailable(ds, wanted_orca, "no data variables found in the DAS")

        query_lon = lon % 360 if ds.lon_0_360 and lon < 0 else lon
        stamp = target.strftime("%Y-%m-%dT%H:%M:%SZ")
        selector = _encode_selector(f"[({stamp})][({lat})][({query_lon})]")
        query = ",".join(f"{v}{selector}" for v in erddap_vars)
        url = ds.url(f".json?{query}")

        result = await self.fetch(url)
        if not result.ok:
            return self._all_unavailable(ds, wanted_orca, result.error or "fetch failed")

        try:
            table = result.json()["table"]
            names = table["columnNames"]
            units = table.get("columnUnits", [None] * len(names))
            row = table["rows"][0]
        except (KeyError, IndexError, ValueError) as exc:
            return self._all_unavailable(ds, wanted_orca, f"unparseable response: {exc}")

        actual_time = target
        if "time" in names:
            try:
                actual_time = datetime.fromisoformat(str(row[names.index("time")]))
            except ValueError:
                pass

        out: dict[str, Evidence] = {}
        for erddap_name, orca_name in (ds.variables or {v: v for v in erddap_vars}).items():
            if erddap_name not in names:
                continue
            idx = names.index(erddap_name)
            value = row[idx]
            if value is None:
                continue
            upstream_unit = _normalise_erddap_unit(units[idx] if idx < len(units) else None)
            canonical = ds.units.get(orca_name, upstream_unit)
            converted = _convert_erddap(float(value), upstream_unit, canonical)
            if converted is None:
                log.error(
                    "%s: cannot convert %s from %r to %r — refusing",
                    ds.dataset_id,
                    orca_name,
                    upstream_unit,
                    canonical,
                )
                continue
            out[orca_name] = Evidence(
                dataset_id=ds.dataset_id,
                provider=ds.provider,
                variable=orca_name,
                value=round(converted, 4),
                unit=canonical,
                provenance=ds.provenance if ds.role != LIVE else result.provenance,
                freshness=Freshness.of(orca_name, actual_time),
                url=ds.url(".html"),
                location=(lon, lat),
                notes=ds.notes,
                citations=[
                    Citation(
                        label=ds.title,
                        provider=ds.provider,
                        url=ds.url(".html"),
                        identifier=ds.dataset_id,
                    )
                ],
            )
        if not out:
            return self._all_unavailable(
                ds, wanted_orca, "grid cell is empty (land mask, cloud gap, or outside coverage)"
            )
        return out

    # ---------------------------------------------------------------- subset
    async def subset_csv(
        self,
        key: str,
        *,
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
        when: datetime | None = None,
        stride: int = 1,
    ) -> Fetched:
        """A bbox slice as CSV, for the raster pipeline to parse into a grid.

        CSV rather than netCDF on purpose: the AOI slice is small, ERDDAP's
        ``.csv`` needs no netCDF library round-trip, and a failure is readable.
        """
        ds = DATASETS[key]
        target = when or await self.latest_time(key)
        if target is None:
            return Fetched(
                ok=False, source=self.name, url=ds.url(""), error="no timestamp available"
            )

        erddap_vars = list(ds.variables) or await self._discover_variables(ds)
        if ds.lon_0_360:
            lon_min = lon_min % 360
            lon_max = lon_max % 360

        stamp = target.strftime("%Y-%m-%dT%H:%M:%SZ")
        selector = _encode_selector(
            f"[({stamp})][({lat_min}):{stride}:({lat_max})][({lon_min}):{stride}:({lon_max})]"
        )
        query = ",".join(f"{v}{selector}" for v in erddap_vars)
        return await self.fetch(ds.url(f".csv?{query}"))

    # ------------------------------------------------------------- internals
    async def _discover_variables(self, ds: GridDataset) -> list[str]:
        """Read the DAS for datasets whose variable names we have not pinned.

        Cached per process: the DAS of a decade-old archive is not going to move.
        """
        if ds.dataset_id in self._das_cache:
            return self._das_cache[ds.dataset_id]["variables"]
        result = await self.fetch(ds.url(".das"))
        if not result.ok or result.text is None:
            return []
        variables = _parse_das_variables(result.text)
        self._das_cache[ds.dataset_id] = {"variables": variables}
        return variables

    def _all_unavailable(
        self, ds: GridDataset, variables: list[str] | None, reason: str
    ) -> dict[str, Evidence]:
        names = variables or list(ds.units) or [ds.dataset_id]
        return {
            name: Evidence.unavailable(
                dataset_id=ds.dataset_id,
                provider=ds.provider,
                variable=name,
                reason=reason,
                url=ds.url(".html"),
            )
            for name in names
        }


_COORDS = {"time", "latitude", "longitude", "lat", "lon", "altitude", "depth", "LEV"}


def _parse_das_variables(das: str) -> list[str]:
    """Data variable names from an ERDDAP DAS, coordinates excluded.

    A tolerant brace-depth scan rather than a real parser: the DAS grammar is
    stable and this only needs the top-level names under ``Attributes {}``.
    """
    names: list[str] = []
    depth = 0
    for raw in das.splitlines():
        line = raw.strip()
        if line.endswith("{"):
            candidate = line[:-1].strip()
            if depth == 1 and candidate and candidate not in _COORDS:
                names.append(candidate)
            depth += 1
        elif line.startswith("}"):
            depth -= 1
    return [n for n in names if n != "NC_GLOBAL"]


_ERDDAP_UNIT_ALIASES = {
    "degree c": "degC",
    "degree_c": "degC",
    "degrees c": "degC",
    "degree celsius": "degC",
    "celsius": "degC",
    "deg c": "degC",
    "degc": "degC",
    "mg m^-3": "mg m-3",
    "mg/m^3": "mg m-3",
    "mg m-3": "mg m-3",
    "mg/m3": "mg m-3",
    "m s-1": "m/s",
    "m/s": "m/s",
    "m s^-1": "m/s",
}


def _normalise_erddap_unit(raw: str | None) -> str | None:
    """ERDDAP unit strings are free text — ``Degree C``, ``degree_C``,
    ``mg m^-3`` all appear across the servers we use."""
    if raw is None:
        return None
    cleaned = raw.strip()
    if cleaned.lower() in {"", "utc", "iso8601", "degrees_north", "degrees_east", "count"}:
        return None
    return _ERDDAP_UNIT_ALIASES.get(cleaned.lower(), cleaned)


_ERDDAP_CONVERSIONS: dict[tuple[str, str], float] = {
    ("K", "degC"): 1.0,  # affine; handled in _convert_erddap
    ("mg m-3", "mg m-3"): 1.0,
}


def _convert_erddap(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    if to_unit is None or from_unit is None or from_unit == to_unit:
        return value
    if (from_unit, to_unit) == ("K", "degC"):
        return value - 273.15
    if (from_unit, to_unit) == ("degC", "K"):
        return value + 273.15
    factor = _ERDDAP_CONVERSIONS.get((from_unit, to_unit))
    return None if factor is None else value * factor


erddap = Erddap()


async def live_sst(lat: float, lon: float) -> Evidence | None:
    """ORCA's live SST field: MUR SST at 1 km, zero auth."""
    values = await erddap.point("mur_sst", lat, lon, variables=["sst", "sst_uncertainty"])
    return values.get("sst")


async def baseline_sst(lat: float, lon: float, *, when: datetime) -> Evidence | None:
    """A historical SST value from the INCOIS TMI archive, for the climatology."""
    values = await erddap.point("incois_tmi_sst", lat, lon, when=when)
    return values.get("sst")

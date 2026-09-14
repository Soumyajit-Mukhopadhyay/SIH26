"""The researcher's catalogue, and natural-language dataset discovery.

## The one design rule

**A language model parses the request. Deterministic code does the matching.**

The model's only job is to turn "surface temperature fronts off Kerala during
last year's monsoon" into a structured intent — variables, a bounding box, a time
range. It never chooses a dataset and it never writes a URL. The matching runs
against a registry of datasets ORCA has actually integrated, so the worst a bad
parse can do is return the wrong *real* dataset. It cannot invent a plausible
dataset id, a coverage window, or an endpoint that does not exist.

That matters more here than anywhere else in ORCA. A fisherman can tell when an
advisory is wrong because he can see the sea. A researcher handed a fabricated
dataset identifier finds out weeks later, after building on it.

Every entry also declares what it is NOT: a dataset ORCA reads at 0.05 deg is
recorded at 0.05 deg even when the upstream product is 1 km, because a researcher
who copies our resolution into a methods section has been misled by the
difference.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from orca.provenance import Provenance

log = logging.getLogger(__name__)

CATALOGUE_VERSION = "orca-research-catalogue-2026.09"

Kind = Literal["observation", "reanalysis", "forecast", "derived", "model"]
Access = Literal["open", "free-signup", "credentialed", "unavailable"]


@dataclass(slots=True)
class Variable:
    name: str
    unit: str
    description: str


@dataclass(slots=True)
class Dataset:
    """One dataset a researcher can actually obtain through ORCA."""

    id: str
    title: str
    provider: str
    kind: Kind
    variables: list[Variable]
    #: Resolution AS ORCA SERVES IT, in degrees. Not the upstream native
    #: resolution — see the module docstring.
    resolution_deg: float
    native_resolution: str
    cadence: str
    coverage: str
    access: Access
    licence: str
    provenance: Provenance
    endpoint: str
    #: What this is not good for. Absent caveats are how a catalogue misleads.
    caveats: str
    keywords: tuple[str, ...] = ()
    #: True when ORCA can subset and return the data itself rather than only
    #: pointing at the upstream.
    servable: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "provider": self.provider,
            "kind": self.kind,
            "variables": [
                {"name": v.name, "unit": v.unit, "description": v.description}
                for v in self.variables
            ],
            "resolution_deg": self.resolution_deg,
            "native_resolution": self.native_resolution,
            "cadence": self.cadence,
            "coverage": self.coverage,
            "access": self.access,
            "licence": self.licence,
            "provenance": self.provenance.value,
            "endpoint": self.endpoint,
            "caveats": self.caveats,
            "keywords": list(self.keywords),
            "servable": self.servable,
        }


# --------------------------------------------------------------- the registry
#
# Hand-written rather than generated from the source modules. A generated
# catalogue would inherit whatever the adapter happens to expose and would drift
# into listing things ORCA cannot actually deliver; every row here is one
# somebody checked.

CATALOGUE: tuple[Dataset, ...] = (
    Dataset(
        id="mur_sst",
        title="JPL MUR sea-surface temperature",
        provider="NASA JPL / NOAA CoastWatch ERDDAP",
        kind="observation",
        variables=[Variable("sst", "degC", "Foundation sea-surface temperature")],
        resolution_deg=0.05,
        native_resolution="0.01 deg (~1 km)",
        cadence="daily",
        coverage="2002-06 to yesterday, global",
        access="open",
        licence="Public domain (NASA/NOAA)",
        provenance=Provenance.LIVE,
        endpoint="https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41",
        caveats=(
            "ORCA subsets at 0.05 deg by server-side stride, so fine coastal structure present "
            "in the native 1 km product is not in what ORCA returns. Request the upstream "
            "directly if you need 1 km. Gap-filled by the producer — it is an analysis, not a "
            "raw radiometer swath."
        ),
        keywords=("sst", "temperature", "thermal", "front", "warm", "cool"),
        servable=True,
    ),
    Dataset(
        id="esacci_chl_monthly",
        title="ESA CCI ocean colour — chlorophyll-a, monthly",
        provider="Plymouth Marine Laboratory ERDDAP",
        kind="observation",
        variables=[Variable("chlorophyll", "mg m-3", "Chlorophyll-a concentration")],
        resolution_deg=0.05,
        native_resolution="1/24 deg (~4 km)",
        cadence="monthly",
        coverage="1997-09 onward, global",
        access="open",
        licence="ESA CCI open licence",
        provenance=Provenance.CACHED,
        endpoint="https://rsg.pml.ac.uk/erddap/griddap/pmlEsaCCI60OceanColorMonthly",
        caveats=(
            "Monthly composites. Useless for a daily bloom question, and the monsoon months are "
            "heavily cloud-gapped over the Bay of Bengal even after compositing."
        ),
        keywords=("chlorophyll", "chl", "ocean colour", "productivity", "bloom", "plankton"),
        servable=True,
    ),
    Dataset(
        id="incois_tmi_sst",
        title="INCOIS TMI 3-day sea-surface temperature",
        provider="INCOIS ERDDAP",
        kind="observation",
        variables=[Variable("sst", "degC", "Microwave SST, 3-day composite")],
        resolution_deg=0.25,
        native_resolution="0.25 deg",
        cadence="3-day composite",
        coverage="Indian Ocean",
        access="open",
        licence="INCOIS terms — attribution required",
        provenance=Provenance.LIVE,
        endpoint="https://erddap.incois.gov.in/erddap/griddap/incois_tmi_3day_datasets",
        caveats=(
            "Microwave, so it sees through cloud where MUR cannot — but far coarser. INCOIS's "
            "TLS chain is incomplete and ORCA ships the missing intermediate certificate; a "
            "plain requests/xarray call from your own machine will fail verification until you "
            "do the same."
        ),
        keywords=("sst", "temperature", "incois", "microwave", "india"),
        servable=True,
    ),
    Dataset(
        id="incois_oceansat2",
        title="INCOIS Oceansat-2 derived products",
        provider="INCOIS ERDDAP",
        kind="observation",
        variables=[Variable("sst", "degC", "Oceansat-derived fields")],
        resolution_deg=0.25,
        native_resolution="0.25 deg",
        cadence="daily",
        coverage="Indian Ocean",
        access="open",
        licence="INCOIS terms — attribution required",
        provenance=Provenance.LIVE,
        endpoint="https://erddap.incois.gov.in/erddap/griddap/incois_oceansat2_datasets",
        caveats="Indian-mission data; coverage gaps are common outside the core Indian Ocean box.",
        keywords=("oceansat", "isro", "india", "sst"),
        servable=True,
    ),
    Dataset(
        id="ascat_winds",
        title="ASCAT daily scatterometer winds",
        provider="INCOIS ERDDAP",
        kind="observation",
        variables=[
            Variable("wind_speed", "m s-1", "10 m wind speed"),
            Variable("wind_direction", "deg", "Direction the wind blows FROM"),
        ],
        resolution_deg=0.25,
        native_resolution="0.25 deg",
        cadence="daily",
        coverage="Indian Ocean",
        access="open",
        licence="INCOIS terms — attribution required",
        provenance=Provenance.LIVE,
        endpoint="https://erddap.incois.gov.in/erddap/griddap/ascat_daily_datasets",
        caveats=(
            "Scatterometer winds are unreliable in heavy rain and within roughly 30 km of the "
            "coast. Direction is the meteorological convention (FROM), the opposite of the "
            "current convention (TO) — mixing them silently reverses a drift calculation."
        ),
        keywords=("wind", "scatterometer", "ascat", "speed", "direction"),
        servable=True,
    ),
    Dataset(
        id="open_meteo_marine",
        title="Open-Meteo marine forecast",
        provider="Open-Meteo",
        kind="forecast",
        variables=[
            Variable("wave_height", "m", "Significant wave height"),
            Variable("wave_period", "s", "Peak wave period"),
            Variable("wave_direction", "deg", "Mean wave direction"),
            Variable("swell_height", "m", "Swell component height"),
            Variable("sea_surface_current", "m s-1", "Surface current speed"),
        ],
        resolution_deg=0.08,
        native_resolution="~0.08 deg",
        cadence="hourly, 7-day horizon",
        coverage="global ocean, rolling",
        access="open",
        licence="CC BY 4.0 (attribution)",
        provenance=Provenance.LIVE,
        endpoint="https://marine-api.open-meteo.com/v1/marine",
        caveats=(
            "A FORECAST, not an observation — do not treat it as ground truth for validating "
            "another model. The free tier meters per LOCATION, not per request, so a 300-point "
            "grid spends 300 units."
        ),
        keywords=("wave", "swell", "sea state", "hs", "current", "forecast", "period"),
        servable=True,
    ),
    Dataset(
        id="open_meteo_atmos",
        title="Open-Meteo atmospheric forecast",
        provider="Open-Meteo",
        kind="forecast",
        variables=[
            Variable("wind_speed", "kn", "10 m wind speed"),
            Variable("wind_gust", "kn", "Gust"),
            Variable("visibility", "m", "Horizontal visibility"),
            Variable("convective_energy", "J kg-1", "CAPE"),
            Variable("convective_inhibition", "J kg-1", "CIN"),
            Variable("precipitation", "mm", "Precipitation"),
        ],
        resolution_deg=0.1,
        native_resolution="~0.1 deg",
        cadence="hourly, 7-day horizon",
        coverage="global, rolling",
        access="open",
        licence="CC BY 4.0 (attribution)",
        provenance=Provenance.LIVE,
        endpoint="https://api.open-meteo.com/v1/forecast",
        caveats=(
            "CAPE measures thunderstorm POTENTIAL, not occurrence. Over the Indian coast in "
            "monsoon it routinely exceeds ORCA's veto threshold across whole regions; reading it "
            "as 'a storm is here' overstates it badly."
        ),
        keywords=("wind", "gust", "visibility", "cape", "cin", "lightning", "rain", "storm"),
        servable=True,
    ),
    Dataset(
        id="cmems_wave",
        title="Copernicus Marine global wave analysis and forecast",
        provider="CMEMS / Mercator Ocean",
        kind="forecast",
        variables=[Variable("wave_height", "m", "Significant wave height (VHM0)")],
        resolution_deg=0.2,
        native_resolution="1/5 deg",
        cadence="3-hourly",
        coverage="global, rolling + multi-year reanalysis",
        access="free-signup",
        licence="Copernicus Marine licence — free, attribution required",
        provenance=Provenance.LIVE,
        endpoint="copernicusmarine toolbox (GLOBAL_ANALYSISFORECAST_WAV_001_027)",
        caveats=(
            "Needs a free Copernicus Marine account. ORCA fetches point values through the "
            "official toolbox; bulk subsetting should go through the toolbox directly rather "
            "than through ORCA."
        ),
        keywords=("wave", "cmems", "copernicus", "hs", "vhm0", "reanalysis"),
        servable=False,
    ),
    Dataset(
        id="nasa_power_wind",
        title="NASA POWER surface meteorology",
        provider="NASA LaRC",
        kind="reanalysis",
        variables=[Variable("wind_speed", "m s-1", "10 m wind speed, MERRA-2 derived")],
        resolution_deg=0.5,
        native_resolution="0.5 x 0.625 deg",
        cadence="daily / hourly",
        coverage="1981 onward, global",
        access="open",
        licence="Public domain (NASA)",
        provenance=Provenance.LIVE,
        endpoint="https://power.larc.nasa.gov/api/temporal/daily/point",
        caveats=(
            "Reanalysis at half a degree. ORCA uses it as an INDEPENDENT second opinion for "
            "cross-validating wind, not as a primary field — it is too coarse to resolve a "
            "coastal sea breeze."
        ),
        keywords=("wind", "reanalysis", "nasa", "power", "merra", "climate"),
        servable=False,
    ),
    Dataset(
        id="sentinel2_l2a",
        title="Sentinel-2 L2A surface reflectance",
        provider="Copernicus Data Space Ecosystem",
        kind="observation",
        variables=[Variable("reflectance", "1", "12-band multispectral surface reflectance")],
        resolution_deg=0.0001,
        native_resolution="10-60 m",
        cadence="~5-day revisit",
        coverage="2015 onward, global land + coastal",
        access="free-signup",
        licence="Copernicus open licence",
        provenance=Provenance.LIVE,
        endpoint="https://sh.dataspace.copernicus.eu (STAC + Processing API)",
        caveats=(
            "Optical, so cloud blocks it — during the south-west monsoon, which is exactly when "
            "coastal questions matter most, usable scenes over the Indian coast are scarce. Use "
            "Sentinel-1 SAR when cloud is the constraint."
        ),
        keywords=("sentinel", "optical", "imagery", "coastal", "turbidity", "chip", "high-res"),
        servable=False,
    ),
    Dataset(
        id="sentinel1_grd",
        title="Sentinel-1 GRD synthetic-aperture radar",
        provider="Copernicus Data Space Ecosystem",
        kind="observation",
        variables=[Variable("backscatter", "dB", "VV/VH radar backscatter")],
        resolution_deg=0.0002,
        native_resolution="~10-20 m",
        cadence="~6-12 day revisit",
        coverage="2014 onward",
        access="free-signup",
        licence="Copernicus open licence",
        provenance=Provenance.LIVE,
        endpoint="https://sh.dataspace.copernicus.eu (STAC + Processing API)",
        caveats=(
            "Sees through cloud, which is why it matters here. Backscatter is sensitive to wind "
            "roughening, so a dark patch is an oil-spill CANDIDATE and equally a low-wind "
            "slick — it is not a detection on its own."
        ),
        keywords=("sentinel", "sar", "radar", "oil spill", "vessel", "all-weather"),
        servable=False,
    ),
    Dataset(
        id="gfw_fishing_effort",
        title="Global Fishing Watch apparent fishing effort",
        provider="Global Fishing Watch",
        kind="derived",
        variables=[Variable("fishing_hours", "h", "Apparent fishing hours per cell")],
        resolution_deg=0.1,
        native_resolution="0.01-0.1 deg",
        cadence="daily aggregates",
        coverage="2012 onward, global",
        access="free-signup",
        licence="CC BY-NC-SA 4.0 — non-commercial",
        provenance=Provenance.DERIVED,
        endpoint="https://gateway.api.globalfishingwatch.org (4Wings reports)",
        caveats=(
            "Apparent effort inferred from AIS, so it under-represents exactly the fleet ORCA "
            "serves: most Indian small craft carry no AIS transponder at all. Absence of effort "
            "here is not absence of fishing."
        ),
        keywords=("fishing", "effort", "ais", "vessel", "activity", "labels"),
        servable=False,
    ),
    # ---- ORCA's own derived products -------------------------------------
    Dataset(
        id="orca_fronts_sied",
        title="ORCA thermal fronts — Cayula-Cornillon SIED",
        provider="ORCA (derived)",
        kind="derived",
        variables=[Variable("front_mask", "1", "Binary front presence")],
        resolution_deg=0.05,
        native_resolution="0.05 deg",
        cadence="per request",
        coverage="Indian EEZ, 60-100E 0-25N",
        access="open",
        licence="Derived product — cite ORCA and the MUR SST source",
        provenance=Provenance.DERIVED,
        endpoint="/rasters/sst_gradient  ·  /research/export",
        caveats=(
            "SIED's 32x32 window is fixed in PIXELS, so its behaviour depends on the grid it is "
            "run on: at 0.2 deg that window spans 6.4 deg and the detector returns an EMPTY "
            "mask. Measured, not assumed — do not run it on a coarsened field without checking."
        ),
        keywords=("front", "sied", "cayula", "gradient", "edge", "thermal"),
        servable=True,
    ),
    Dataset(
        id="orca_pfz",
        title="ORCA potential fishing zones (reimplementation)",
        provider="ORCA (derived)",
        kind="derived",
        variables=[Variable("pfz_rank", "1", "Zone rank, 1 (best) to 3")],
        resolution_deg=0.05,
        native_resolution="0.05 deg",
        cadence="daily",
        coverage="Indian EEZ",
        access="open",
        licence="Derived product — cite ORCA",
        provenance=Provenance.DERIVED,
        endpoint="/pfz/zones  ·  /pfz/nearest",
        caveats=(
            "A REIMPLEMENTATION of the published INCOIS methodology, not the official advisory. "
            "INCOIS publishes PFZ as picture maps that software cannot read, so ORCA recomputes "
            "from the same inputs. Do not cite it as INCOIS's product, and the response reports "
            "which criteria could not be applied on any given day."
        ),
        keywords=("pfz", "fishing zone", "fisheries", "incois", "advisory"),
        servable=True,
    ),
    Dataset(
        id="orca_frontcast",
        title="ORCA FrontCast — forecast thermal fronts (+1/+2/+3 days)",
        provider="ORCA (learned model)",
        kind="model",
        variables=[Variable("front_probability", "1", "Probability of a front zone, per lead")],
        resolution_deg=0.1,
        native_resolution="0.1 deg",
        cadence="per request",
        coverage="Indian EEZ",
        access="open",
        licence="Derived product — cite ORCA; labels derive from MUR SST",
        provenance=Provenance.DERIVED,
        endpoint="/ml/frontcast/predict",
        caveats=(
            "Trained against ORCA's own SIED labels, so it inherits SIED's definition of a front "
            "including its blind spots. Its skill is published beside it WITH a persistence "
            "baseline; read both before using it. It cannot move a safety verdict."
        ),
        keywords=("front", "forecast", "prediction", "model", "deep learning", "frontcast"),
        servable=True,
    ),
)

BY_ID: dict[str, Dataset] = {d.id: d for d in CATALOGUE}


# ------------------------------------------------------------- intent parsing


@dataclass(slots=True)
class Intent:
    """A parsed research request. Every field is optional — an unparsed field is
    left None so the matcher can say what it had to guess."""

    variables: list[str] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None  # w, s, e, n
    place: str | None = None
    start: str | None = None
    end: str | None = None
    kinds: list[str] = field(default_factory=list)
    purpose: str | None = None
    assumptions: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "variables": self.variables,
            "bbox": list(self.bbox) if self.bbox else None,
            "place": self.place,
            "start": self.start,
            "end": self.end,
            "kinds": self.kinds,
            "purpose": self.purpose,
            "assumptions": self.assumptions,
        }


#: Named sea areas a researcher is likely to type, as (west, south, east, north).
#: Deliberately coarse and deliberately finite — a model that can return any
#: place name can return a hallucinated one, and a wrong bounding box is a silent
#: error the researcher discovers only in their results.
REGIONS: dict[str, tuple[float, float, float, float]] = {
    "indian eez": (60.0, 0.0, 100.0, 25.0),
    "arabian sea": (60.0, 5.0, 78.0, 25.0),
    "bay of bengal": (78.0, 5.0, 95.0, 23.0),
    "laccadive sea": (70.0, 5.0, 78.0, 14.0),
    "gulf of mannar": (78.0, 8.0, 80.0, 10.0),
    "palk bay": (79.0, 9.0, 80.5, 10.5),
    "andaman sea": (92.0, 5.0, 98.0, 16.0),
    "kerala coast": (74.0, 8.0, 77.5, 13.0),
    "tamil nadu coast": (77.5, 8.0, 81.0, 13.5),
    "gujarat coast": (68.0, 20.0, 73.0, 24.0),
    "goa coast": (73.0, 14.0, 74.5, 16.0),
    "odisha coast": (85.0, 17.5, 88.0, 21.5),
    "west bengal coast": (86.5, 20.5, 89.0, 22.5),
    "andhra coast": (80.0, 13.0, 85.5, 19.5),
    "maharashtra coast": (72.0, 15.5, 74.0, 20.5),
    "karnataka coast": (73.5, 12.5, 75.0, 15.0),
}


def resolve_region(text: str) -> tuple[str, tuple[float, float, float, float]] | None:
    """Match a named sea area in free text.

    Longest name first, so "gulf of mannar" is not swallowed by a shorter key.
    Then a second pass without the "coast" suffix, because researchers write
    "off Tamil Nadu" far more often than "Tamil Nadu coast" and silently
    returning no region turns a specific request into a whole-EEZ one.
    """
    low = text.lower()
    for name in sorted(REGIONS, key=len, reverse=True):
        if name in low:
            return name, REGIONS[name]
    for name in sorted(REGIONS, key=len, reverse=True):
        bare = name.removesuffix(" coast")
        if bare != name and bare in low:
            return name, REGIONS[name]
    return None


_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}


def parse_heuristic(question: str) -> Intent:
    """Parse without a model. The fallback, and the safety net over the model.

    Runs on every request regardless: its findings are UNIONED with the model's,
    so a keyword the model dropped is still honoured. Discovery failing quietly
    because an LLM was unavailable would make the whole feature unreliable in
    exactly the conditions a demo runs under.
    """
    low = question.lower()
    intent = Intent(purpose=question.strip()[:280])

    for dataset in CATALOGUE:
        for keyword in dataset.keywords:
            if keyword in low:
                for variable in dataset.variables:
                    if variable.name not in intent.variables:
                        intent.variables.append(variable.name)
                break

    region = resolve_region(low)
    if region:
        intent.place, intent.bbox = region[0], region[1]

    # Explicit bbox, e.g. "75E to 80E, 8N to 13N"
    numbers = re.findall(r"(\d{1,3}(?:\.\d+)?)\s*([NSEW])", question, re.IGNORECASE)
    if len(numbers) >= 4:
        lons = [float(v) for v, d in numbers if d.upper() in "EW"]
        lats = [float(v) for v, d in numbers if d.upper() in "NS"]
        if len(lons) >= 2 and len(lats) >= 2:
            intent.bbox = (min(lons), min(lats), max(lons), max(lats))
            intent.place = None
            intent.assumptions.append("bounding box read from coordinates in the question")

    now = datetime.now(UTC)
    year = re.search(r"\b(19|20)\d{2}\b", question)
    month = next((m for m in _MONTHS if m in low), None)
    if year and month:
        y, mo = int(year.group(0)), _MONTHS[month]
        nxt = datetime(y + (mo == 12), (mo % 12) + 1, 1, tzinfo=UTC)
        intent.start = f"{y:04d}-{mo:02d}-01"
        intent.end = (nxt - timedelta(days=1)).strftime("%Y-%m-%d")
    elif year:
        intent.start, intent.end = f"{year.group(0)}-01-01", f"{year.group(0)}-12-31"
    elif "monsoon" in low:
        intent.start = f"{now.year}-06-01"
        intent.end = f"{now.year}-09-30"
        intent.assumptions.append(
            "'monsoon' read as the south-west monsoon, June to September of this year"
        )
    else:
        match = re.search(r"last\s+(\d+)\s*(day|week|month|year)", low)
        span = {"day": 1, "week": 7, "month": 30, "year": 365}
        if match:
            days = int(match.group(1)) * span[match.group(2)]
            intent.start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
            intent.end = now.strftime("%Y-%m-%d")

    for word, kind in (
        ("forecast", "forecast"),
        ("reanalysis", "reanalysis"),
        ("observ", "observation"),
        ("satellite", "observation"),
        ("model", "model"),
    ):
        if word in low and kind not in intent.kinds:
            intent.kinds.append(kind)

    return intent


async def parse_with_model(question: str) -> Intent | None:
    """Ask the LLM for a structured intent. Returns None if no provider answers.

    The prompt gives it the closed vocabularies it is allowed to draw from. It
    still cannot name a dataset — that is the matcher's job — and anything it
    returns outside these lists is discarded below.
    """
    import json

    from orca.agents.llm import LlmUnavailable, complete

    variables = sorted({v.name for d in CATALOGUE for v in d.variables})
    system = (
        "You convert a marine researcher's request into a JSON search intent. "
        "Reply with ONLY a JSON object, no prose and no code fence.\n\n"
        f"variables MUST be drawn from this list: {variables}\n"
        f"place MUST be one of: {sorted(REGIONS)} — or null.\n"
        "kinds may contain: observation, reanalysis, forecast, derived, model.\n"
        "Dates are ISO YYYY-MM-DD, or null when the request does not imply one.\n"
        "You must NOT name a dataset, a product id, or a URL. Selecting the data "
        "is not your job; describing the request is.\n\n"
        '{"variables": [...], "place": null, "start": null, "end": null, '
        '"kinds": [...], "assumptions": ["anything you had to guess"]}'
    )
    try:
        reply = await complete(
            [{"role": "system", "content": system}, {"role": "user", "content": question}],
            temperature=0.0,
            max_tokens=400,
        )
    except LlmUnavailable as exc:
        log.info("research: intent parse fell back to heuristic (%s)", exc)
        return None

    text = reply.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    allowed = {v.name for d in CATALOGUE for v in d.variables}
    intent = Intent(
        variables=[v for v in raw.get("variables") or [] if v in allowed],
        kinds=[
            k
            for k in raw.get("kinds") or []
            if k in {"observation", "reanalysis", "forecast", "derived", "model"}
        ],
        start=_iso_or_none(raw.get("start")),
        end=_iso_or_none(raw.get("end")),
        purpose=question.strip()[:280],
        assumptions=[str(a)[:160] for a in (raw.get("assumptions") or [])][:4],
    )
    place = raw.get("place")
    if isinstance(place, str) and place.lower() in REGIONS:
        intent.place = place.lower()
        intent.bbox = REGIONS[intent.place]
    return intent


def _iso_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


def merge(model: Intent | None, heuristic: Intent) -> Intent:
    """Union the two parses, model first but never at the cost of a keyword hit."""
    if model is None:
        heuristic.assumptions.append("parsed without a language model (none was available)")
        return heuristic
    merged = Intent(
        variables=list(dict.fromkeys([*model.variables, *heuristic.variables])),
        bbox=model.bbox or heuristic.bbox,
        place=model.place or heuristic.place,
        start=model.start or heuristic.start,
        end=model.end or heuristic.end,
        kinds=list(dict.fromkeys([*model.kinds, *heuristic.kinds])),
        purpose=model.purpose or heuristic.purpose,
        assumptions=list(dict.fromkeys([*model.assumptions, *heuristic.assumptions])),
    )
    return merged


# ------------------------------------------------------------------ matching


@dataclass(slots=True)
class Match:
    dataset: Dataset
    score: float
    why: list[str]

    def describe(self) -> dict[str, Any]:
        return {**self.dataset.describe(), "score": round(self.score, 3), "why": self.why}


def match(intent: Intent, *, limit: int = 8) -> list[Match]:
    """Rank the catalogue against a parsed intent. Pure function, no model."""
    wanted = set(intent.variables)
    matches: list[Match] = []

    for dataset in CATALOGUE:
        score = 0.0
        why: list[str] = []

        provided = {v.name for v in dataset.variables} & wanted
        if provided:
            score += 3.0 * len(provided)
            why.append(f"serves {', '.join(sorted(provided))}")

        if intent.kinds and dataset.kind in intent.kinds:
            score += 1.5
            article = "an" if dataset.kind[0] in "aeiou" else "a"
            why.append(f"is {article} {dataset.kind} product, which the request asked for")

        # A time range the dataset cannot cover is a hard penalty, not a filter:
        # the researcher is better served by seeing it ranked low with the reason
        # than by it vanishing and looking like ORCA has no such data.
        if intent.start and dataset.kind == "forecast" and intent.start < "2026-01-01":
            score -= 2.5
            why.append("is a forecast product and cannot serve a historical range")
        if intent.end and dataset.id == "esacci_chl_monthly" and intent.start == intent.end:
            score -= 1.0
            why.append("is monthly, so a single-day request will return one composite")

        if dataset.access == "open":
            score += 0.5
        elif dataset.access == "free-signup":
            score += 0.1
            why.append("needs a free account with the provider")

        if dataset.servable:
            score += 0.75
            why.append("ORCA can subset and return this directly")

        if score > 0:
            matches.append(Match(dataset=dataset, score=score, why=why))

    matches.sort(key=lambda m: (-m.score, m.dataset.id))
    return matches[:limit]


def snippet(dataset: Dataset, intent: Intent) -> str:
    """A runnable snippet for the top match. Python, because that is what a
    marine researcher already has open."""
    west, south, east, north = intent.bbox or REGIONS["indian eez"]
    start = intent.start or "2026-01-01"
    end = intent.end or "2026-01-31"

    if dataset.servable and dataset.endpoint.startswith("/"):
        return (
            "import httpx\n\n"
            'r = httpx.get("http://127.0.0.1:8000/research/export", params={\n'
            f'    "dataset": "{dataset.id}",\n'
            f'    "west": {west}, "south": {south}, "east": {east}, "north": {north},\n'
            f'    "start": "{start}", "end": "{end}", "format": "csv",\n'
            "}, timeout=120)\n"
            "print(r.text[:500])"
        )
    if "erddap" in dataset.endpoint:
        variable = dataset.variables[0].name
        return (
            "# ERDDAP griddap subset. ORCA reads this same endpoint.\n"
            "import pandas as pd\n\n"
            f'url = ("{dataset.endpoint}.csv?"\n'
            f'       "{variable}[({start}T00:00:00Z):1:({end}T00:00:00Z)]"\n'
            f'       "[({south}):1:({north})][({west}):1:({east})]")\n'
            "df = pd.read_csv(url, skiprows=[1])\n"
            "print(df.head())"
        )
    return (
        f"# {dataset.title}\n"
        f"# {dataset.endpoint}\n"
        f"# Access: {dataset.access} · Licence: {dataset.licence}\n"
        f"# Requested box: {west},{south},{east},{north}  {start}..{end}\n"
        f"# Caveat: {dataset.caveats}"
    )


async def discover(question: str, *, limit: int = 8, federate: bool = True) -> dict[str, Any]:
    """Natural language in, ranked real datasets out.

    Two tiers, kept visibly separate:

    * **Curated** — the fifteen datasets ORCA has integrated, each carrying
      caveats a person wrote and, for ten of them, a subsetting path ORCA serves
      itself.
    * **Federated** — live search across the public ERDDAP network, which is
      thousands of datasets nobody here has reviewed.

    Merging them into one list would imply the same level of vetting for both.
    A researcher deserves to know which rows somebody checked.
    """
    heuristic = parse_heuristic(question)
    model = await parse_with_model(question)
    intent = merge(model, heuristic)

    if not intent.variables:
        intent.assumptions.append(
            "no variable could be identified in the request, so the whole catalogue is ranked "
            "by general suitability rather than by what was asked for"
        )

    matches = match(intent, limit=limit)

    federated: dict[str, Any] | None = None
    if federate:
        from orca.research import federation

        # Search terms are the parsed variables when we have them, because
        # "wave_height" matches far better against dataset titles than the
        # user's whole sentence does. The raw question is the fallback.
        terms = " ".join(v.replace("_", " ") for v in intent.variables) or question
        try:
            federated = await federation.search(
                terms=terms,
                bbox=intent.bbox,
                start=intent.start,
                end=intent.end,
                limit=limit * 2,
            )
        except Exception as exc:  # noqa: BLE001 — federation is an enhancement
            log.warning("federated search failed: %s", exc)
            federated = None

    return {
        "catalogue_version": CATALOGUE_VERSION,
        "question": question,
        "intent": intent.describe(),
        "parsed_by": "llm+heuristic" if model else "heuristic",
        "matches": [m.describe() for m in matches],
        "federated": federated,
        "snippet": snippet(matches[0].dataset, intent) if matches else None,
        "note": (
            "A language model parsed the request; the datasets were selected by deterministic "
            "matching against a registry of sources ORCA has actually integrated. The model "
            "cannot name a dataset, so nothing here is invented."
        ),
    }

"""Near-real-time NASA browse imagery from Global Imagery Browse Services.

GIBS is deliberately kept separate from NASA CMR.  CMR proves that science
granules exist; GIBS serves already-rendered, attributable imagery suitable for
the browser.  The daily corrected-reflectance layers do not expose an exact
overpass timestamp through WMS, so this adapter reports date precision rather
than inventing a time of day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from orca.provenance import Provider, utcnow
from orca.sources.base import Source

WMS_URL = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"


@dataclass(frozen=True, slots=True)
class GibsLayer:
    layer_id: str
    satellite: str
    instrument: str
    resolution_m: int


# Prefer the newest VIIRS platform, then an independent VIIRS platform, then
# MODIS.  Trying more than three layers adds latency without adding a materially
# different view for this small point preview.
TRUE_COLOUR_LAYERS = (
    GibsLayer(
        "VIIRS_NOAA21_CorrectedReflectance_TrueColor", "NOAA-21", "VIIRS", 750
    ),
    GibsLayer(
        "VIIRS_NOAA20_CorrectedReflectance_TrueColor", "NOAA-20", "VIIRS", 750
    ),
    GibsLayer("MODIS_Aqua_CorrectedReflectance_TrueColor", "Aqua", "MODIS", 250),
)


@dataclass(frozen=True, slots=True)
class GibsPreview:
    content: bytes
    observation_date: date
    layer_id: str
    satellite: str
    instrument: str
    resolution_m: int
    provenance: str


def _contains_observation(content: bytes) -> bool:
    """Reject the opaque black PNG GIBS returns before a daily swath exists."""

    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    try:
        with Image.open(BytesIO(content)) as image:
            rgba = image.convert("RGBA")
            alpha_extrema = rgba.getchannel("A").getextrema()
            rgb_extrema = rgba.convert("RGB").getextrema()
    except (OSError, UnidentifiedImageError):
        return False
    if alpha_extrema is None or alpha_extrema[1] == 0:
        return False
    # A missing current-day GIBS render is an all-zero RGB image with an opaque
    # alpha channel.  Real corrected-reflectance ocean imagery is dark in places
    # but has non-zero signal in at least one channel.
    return any(maximum > 8 for _minimum, maximum in rgb_extrema)


class NasaGibsSource(Source):
    name = "nasa.gibs.corrected_reflectance"
    provider = Provider.NASA
    variables = ("satellite_imagery",)
    docs_url = "https://nasa-gibs.github.io/gibs-api-docs/"

    async def preview(
        self,
        lat: float,
        lon: float,
        *,
        days: int = 3,
        radius_deg: float = 0.2,
        size: int = 768,
    ) -> GibsPreview:
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("latitude/longitude outside WGS84 range")
        if not 1 <= days <= 7 or not 128 <= size <= 768:
            raise ValueError("days must be 1-7 and size must be 128-768 pixels")

        bbox = (
            max(-180.0, lon - radius_deg),
            max(-90.0, lat - radius_deg),
            min(180.0, lon + radius_deg),
            min(90.0, lat + radius_deg),
        )
        latest = utcnow().date()
        failures: list[str] = []

        for age_days in range(days):
            observed_on = latest - timedelta(days=age_days)
            for layer in TRUE_COLOUR_LAYERS:
                result = await self.fetch(
                    WMS_URL,
                    params={
                        "service": "WMS",
                        "request": "GetMap",
                        "version": "1.1.1",
                        "layers": layer.layer_id,
                        "styles": "",
                        "format": "image/png",
                        "transparent": "true",
                        "width": size,
                        "height": size,
                        "srs": "EPSG:4326",
                        "bbox": ",".join(f"{value:.6f}" for value in bbox),
                        "time": observed_on.isoformat(),
                    },
                    conditional=False,
                    retries=1,
                    timeout_s=35,
                    expect_binary=True,
                )
                if not result.ok or result.content is None:
                    failures.append(result.error or f"HTTP {result.status}")
                    continue
                if not _contains_observation(result.content):
                    continue
                return GibsPreview(
                    content=result.content,
                    observation_date=observed_on,
                    layer_id=layer.layer_id,
                    satellite=layer.satellite,
                    instrument=layer.instrument,
                    resolution_m=layer.resolution_m,
                    provenance=result.provenance.value,
                )

        suffix = f" Last upstream error: {failures[-1]}." if failures else ""
        raise LookupError(
            f"no NASA VIIRS/MODIS true-colour browse image covers this point in "
            f"the last {days} days.{suffix}"
        )


nasa_gibs = NasaGibsSource()

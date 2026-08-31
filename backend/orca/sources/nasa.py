"""NASA discovery and meteorology adapters.

CMR is the archive catalogue; POWER supplies a compact hourly point value from
NASA's meteorological analysis.  Keeping them separate prevents a successful
metadata search from being presented as a successful measurement fetch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from orca.provenance import Citation, Evidence, Freshness, Provider, utcnow
from orca.sources.base import Source

CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
POWER_HOURLY = "https://power.larc.nasa.gov/api/temporal/hourly/point"


class NasaGranule(BaseModel):
    concept_id: str
    title: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    updated_at: datetime | None = None
    size_mb: float | None = Field(default=None, ge=0)
    data_links: list[str] = Field(default_factory=list)


class NasaSearchResponse(BaseModel):
    short_name: str
    bbox: tuple[float, float, float, float]
    start_time: datetime
    end_time: datetime
    hits: int = Field(ge=0)
    granules: list[NasaGranule]
    authenticated_downloads: bool
    provenance: str
    note: str


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _size_megabytes(value: Any, unit: Any) -> float | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    normalized = str(unit or "MB").strip().upper().replace(" ", "")
    factors = {
        "BYTE": 1 / 1_000_000,
        "BYTES": 1 / 1_000_000,
        "KB": 1 / 1_000,
        "KILOBYTE": 1 / 1_000,
        "KILOBYTES": 1 / 1_000,
        "MB": 1.0,
        "MEGABYTE": 1.0,
        "MEGABYTES": 1.0,
        "GB": 1_000.0,
        "GIGABYTE": 1_000.0,
        "GIGABYTES": 1_000.0,
        "TB": 1_000_000.0,
        "TERABYTE": 1_000_000.0,
        "TERABYTES": 1_000_000.0,
    }
    factor = factors.get(normalized)
    return float(value) * factor if factor is not None else None


class NasaCmrSource(Source):
    name = "nasa.cmr"
    provider = Provider.NASA
    variables = ("granule_catalogue",)
    docs_url = "https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html"

    async def search(
        self,
        short_name: str,
        bbox: tuple[float, float, float, float],
        start: datetime,
        end: datetime,
        *,
        limit: int = 10,
    ) -> NasaSearchResponse:
        result = await self.fetch(
            CMR_GRANULES,
            params={
                "short_name": short_name,
                "bounding_box": ",".join(str(v) for v in bbox),
                "temporal": f"{start.isoformat()},{end.isoformat()}",
                "page_size": min(max(limit, 1), 100),
                "sort_key[]": "-start_date",
            },
            headers={"Accept": "application/vnd.nasa.cmr.umm_results+json"},
            conditional=False,
        )
        if not result.ok:
            raise RuntimeError(result.error or f"NASA CMR returned HTTP {result.status}")
        payload = result.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        granules: list[NasaGranule] = []
        for item in items:
            meta = item.get("meta") or {}
            umm = item.get("umm") or {}
            temporal = umm.get("TemporalExtent", {}).get("RangeDateTime", {})
            links = [
                link.get("URL")
                for link in (umm.get("RelatedUrls") or [])
                if isinstance(link, dict)
                and isinstance(link.get("URL"), str)
                and link.get("Type") in {"GET DATA", "USE SERVICE API"}
            ]
            size = None
            for archive in umm.get("DataGranule", {}).get("ArchiveAndDistributionInformation", []):
                if not isinstance(archive, dict):
                    continue
                size = _size_megabytes(archive.get("Size"), archive.get("SizeUnit"))
                if size is not None:
                    break
            granules.append(
                NasaGranule(
                    concept_id=str(meta.get("concept-id") or meta.get("concept_id") or "unknown"),
                    title=str(umm.get("GranuleUR") or meta.get("native-id") or "untitled granule"),
                    start_time=_parse_datetime(temporal.get("BeginningDateTime")),
                    end_time=_parse_datetime(temporal.get("EndingDateTime")),
                    updated_at=_parse_datetime(meta.get("revision-date")),
                    size_mb=size,
                    data_links=[str(link) for link in links if link],
                )
            )
        raw_hits = result.headers.get("cmr-hits", len(granules))
        try:
            hits = int(raw_hits)
        except (TypeError, ValueError):
            hits = len(granules)
        from orca.config import get_settings

        return NasaSearchResponse(
            short_name=short_name,
            bbox=bbox,
            start_time=start,
            end_time=end,
            hits=hits,
            granules=granules,
            authenticated_downloads=get_settings().has_earthdata,
            provenance=result.provenance.value,
            note=(
                "CMR discovery is public. Earthdata credentials are only used by a later "
                "download request; their presence does not mean a granule was downloaded."
            ),
        )


class NasaPowerSource(Source):
    name = "nasa.power"
    provider = Provider.NASA
    variables = ("wind_speed", "wind_direction")
    docs_url = "https://power.larc.nasa.gov/docs/services/api/temporal/hourly/"

    async def wind_at(
        self, lat: float, lon: float, *, lookback_days: int = 7
    ) -> dict[str, Evidence]:
        now = utcnow()
        start = now - timedelta(days=max(2, min(lookback_days, 30)))
        result = await self.fetch(
            POWER_HOURLY,
            params={
                "parameters": "WS10M,WD10M",
                "community": "AG",
                "longitude": lon,
                "latitude": lat,
                "start": start.strftime("%Y%m%d"),
                "end": now.strftime("%Y%m%d"),
                "format": "JSON",
                "time-standard": "UTC",
            },
            conditional=False,
            timeout_s=20,
        )
        if not result.ok:
            reason = result.error or f"NASA POWER returned HTTP {result.status}"
            return {
                variable: Evidence.unavailable(
                    dataset_id="NASA_POWER_MERRA2_NRT",
                    provider=Provider.NASA,
                    variable=variable,
                    reason=reason,
                    url=self.docs_url,
                )
                for variable in self.variables
            }

        payload = result.json()
        parameters = payload.get("properties", {}).get("parameter", {})
        speed_values = parameters.get("WS10M") or {}
        direction_values = parameters.get("WD10M") or {}
        common = sorted(set(speed_values) & set(direction_values), reverse=True)
        selected: tuple[str, float, float] | None = None
        for stamp in common:
            try:
                speed = float(speed_values[stamp])
                direction = float(direction_values[stamp])
            except (TypeError, ValueError):
                continue
            if speed <= -900 or direction <= -900:
                continue
            if 0 <= speed <= 100 and 0 <= direction <= 360:
                selected = stamp, speed, direction
                break
        if selected is None:
            reason = "NASA POWER returned no finite WS10M/WD10M pair in the requested window"
            return {
                variable: Evidence.unavailable(
                    dataset_id="NASA_POWER_MERRA2_NRT",
                    provider=Provider.NASA,
                    variable=variable,
                    reason=reason,
                    url=self.docs_url,
                )
                for variable in self.variables
            }

        stamp, speed, direction = selected
        when = datetime.strptime(stamp, "%Y%m%d%H").replace(tzinfo=UTC)
        common_kwargs: dict[str, Any] = {
            "dataset_id": "NASA_POWER_MERRA2_NRT",
            "provider": Provider.NASA,
            "provenance": result.provenance,
            "freshness": Freshness.of("wind_speed", when),
            "url": self.docs_url,
            "location": (lon, lat),
            "notes": (
                "NASA POWER hourly 10 m wind; analysis grid is about 0.5 x 0.625 degrees. "
                "This is an independent broad-scale check, not a coastal anemometer."
            ),
            "citations": [
                Citation(
                    label="NASA POWER Hourly API",
                    provider=Provider.NASA,
                    url=self.docs_url,
                    identifier="WS10M/WD10M",
                )
            ],
        }
        return {
            "wind_speed": Evidence(
                **common_kwargs,
                variable="wind_speed",
                value=round(speed, 3),
                unit="m/s",
            ),
            "wind_direction": Evidence(
                **{**common_kwargs, "freshness": Freshness.of("wind_direction", when)},
                variable="wind_direction",
                value=round(direction, 2),
                unit="degree",
            ),
        }


cmr = NasaCmrSource()
power = NasaPowerSource()

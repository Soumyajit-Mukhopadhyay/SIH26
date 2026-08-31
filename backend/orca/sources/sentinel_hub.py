"""Copernicus Data Space / Sentinel Hub OAuth and STAC catalogue adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from orca.config import get_settings
from orca.provenance import utcnow
from orca.sources.base import Source

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"

TRUE_COLOUR_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: ["B08", "B06", "B04", "dataMask"],
    output: { bands: 4, sampleType: "AUTO" }
  };
}
function evaluatePixel(sample) {
  return [2.5 * sample.B08, 2.5 * sample.B06, 2.5 * sample.B04, sample.dataMask];
}
"""


class SentinelItem(BaseModel):
    item_id: str
    collection: str
    acquired_at: datetime | None = None
    cloud_cover_percent: float | None = Field(default=None, ge=0, le=100)
    bbox: list[float] = Field(default_factory=list)
    catalogue_url: str | None = None


class SentinelSearchResponse(BaseModel):
    collection: str
    bbox: tuple[float, float, float, float]
    start_time: datetime
    end_time: datetime
    returned: int = Field(ge=0)
    items: list[SentinelItem]
    provenance: str
    note: str


@dataclass(frozen=True, slots=True)
class SentinelPreview:
    content: bytes
    item_id: str
    acquired_at: datetime
    bbox: tuple[float, float, float, float]
    provenance: str


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class SentinelHubSource(Source):
    name = "cdse.sentinel_hub_catalog"
    provider = "Copernicus Data Space"
    variables = ("satellite_catalogue",)
    requires = "has_cdse"
    docs_url = "https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Catalog.html"

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires = datetime.min.replace(tzinfo=UTC)
        self._token_lock = asyncio.Lock()

    async def _access_token(self) -> str:
        if self._token and utcnow() < self._token_expires - timedelta(seconds=30):
            return self._token
        async with self._token_lock:
            if self._token and utcnow() < self._token_expires - timedelta(seconds=30):
                return self._token
            settings = get_settings()
            if not settings.has_cdse:
                raise RuntimeError("CDSE OAuth client is not configured")
            assert settings.cdse_client_id and settings.cdse_client_secret
            result = await self.fetch(
                TOKEN_URL,
                method="POST",
                form_body={
                    "grant_type": "client_credentials",
                    "client_id": settings.cdse_client_id,
                    "client_secret": settings.cdse_client_secret.get_secret_value(),
                },
                conditional=False,
            )
            if not result.ok:
                raise RuntimeError(result.error or f"CDSE token endpoint returned {result.status}")
            payload = result.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise ValueError("CDSE token response did not contain access_token")
            expires_in = max(60, int(payload.get("expires_in", 600)))
            self._token = token
            self._token_expires = utcnow() + timedelta(seconds=expires_in)
            return token

    async def search(
        self,
        collection: str,
        bbox: tuple[float, float, float, float],
        start: datetime,
        end: datetime,
        *,
        limit: int = 10,
    ) -> SentinelSearchResponse:
        token = await self._access_token()
        result = await self.fetch(
            CATALOG_URL,
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
            json_body={
                "bbox": list(bbox),
                "datetime": f"{start.isoformat()}/{end.isoformat()}",
                "collections": [collection],
                "limit": min(max(limit, 1), 100),
            },
            conditional=False,
        )
        if not result.ok:
            raise RuntimeError(result.error or f"Sentinel Hub Catalog returned {result.status}")
        payload = result.json()
        features = payload.get("features", []) if isinstance(payload, dict) else []
        items: list[SentinelItem] = []
        for feature in features:
            properties = feature.get("properties") or {}
            links = feature.get("links") or []
            self_link = next(
                (
                    link.get("href")
                    for link in links
                    if isinstance(link, dict) and link.get("rel") == "self"
                ),
                None,
            )
            cloud = properties.get("eo:cloud_cover")
            items.append(
                SentinelItem(
                    item_id=str(feature.get("id") or "unknown"),
                    collection=str(feature.get("collection") or collection),
                    acquired_at=_dt(properties.get("datetime")),
                    cloud_cover_percent=float(cloud) if isinstance(cloud, (int, float)) else None,
                    bbox=[float(value) for value in feature.get("bbox", [])],
                    catalogue_url=str(self_link) if self_link else None,
                )
            )
        return SentinelSearchResponse(
            collection=collection,
            bbox=bbox,
            start_time=start,
            end_time=end,
            returned=len(items),
            items=items,
            provenance=result.provenance.value,
            note=(
                "Catalogue matches prove that products exist for this area and time. They do "
                "not mean ORCA downloaded or processed those products."
            ),
        )

    async def preview(
        self,
        lat: float,
        lon: float,
        *,
        days: int = 7,
        radius_deg: float = 0.2,
        size: int = 384,
    ) -> SentinelPreview:
        """Process a small true-colour PNG from the latest catalogued OLCI acquisition."""
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("latitude/longitude outside WGS84 range")
        if not 1 <= days <= 90 or not 128 <= size <= 768:
            raise ValueError("days must be 1-90 and size must be 128-768 pixels")
        now = utcnow()
        bbox = (
            max(-180.0, lon - radius_deg),
            max(-90.0, lat - radius_deg),
            min(180.0, lon + radius_deg),
            min(90.0, lat + radius_deg),
        )
        catalogue = await self.search(
            "sentinel-3-olci", bbox, now - timedelta(days=days), now, limit=20
        )
        candidates = [item for item in catalogue.items if item.acquired_at is not None]
        if not candidates:
            raise LookupError("no Sentinel-3 OLCI product covers this area in the requested window")
        selected = max(
            candidates, key=lambda item: item.acquired_at or datetime.min.replace(tzinfo=UTC)
        )
        assert selected.acquired_at is not None
        token = await self._access_token()
        result = await self.fetch(
            PROCESS_URL,
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
            json_body={
                "input": {
                    "bounds": {
                        "bbox": list(bbox),
                        "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                    },
                    "data": [
                        {
                            "type": "sentinel-3-olci",
                            "dataFilter": {
                                "timeRange": {
                                    "from": (
                                        selected.acquired_at - timedelta(minutes=15)
                                    ).isoformat(),
                                    "to": (
                                        selected.acquired_at + timedelta(minutes=15)
                                    ).isoformat(),
                                },
                                "mosaickingOrder": "mostRecent",
                            },
                        }
                    ],
                },
                "output": {
                    "width": size,
                    "height": size,
                    "responses": [{"format": {"type": "image/png"}}],
                },
                "evalscript": TRUE_COLOUR_EVALSCRIPT,
            },
            conditional=False,
            retries=1,
            timeout_s=40,
            expect_binary=True,
        )
        if not result.ok:
            raise RuntimeError(result.error or f"Sentinel Hub Process returned {result.status}")
        if not result.content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Sentinel Hub Process response was not a valid PNG")
        return SentinelPreview(
            content=result.content,
            item_id=selected.item_id,
            acquired_at=selected.acquired_at,
            bbox=bbox,
            provenance=result.provenance.value,
        )


sentinel_hub = SentinelHubSource()

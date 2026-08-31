"""Copernicus Data Space / Sentinel Hub OAuth and STAC catalogue adapter."""

from __future__ import annotations

import asyncio
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


sentinel_hub = SentinelHubSource()

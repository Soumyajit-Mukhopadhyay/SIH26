"""Global Fishing Watch apparent-fishing-effort report adapter."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from orca.config import get_settings
from orca.provenance import Citation, Evidence, Freshness, Provider, utcnow
from orca.sources.base import Source

REPORT_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
DATASET = "public-global-fishing-effort:latest"
DOCS_URL = "https://api-doc.globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report"


class FishingEntry(BaseModel):
    vessel_id: str | None = None
    mmsi: str | None = None
    name: str | None = None
    flag: str | None = None
    gear_type: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    apparent_fishing_hours: float = Field(ge=0)


class FishingEffortResponse(BaseModel):
    bbox: tuple[float, float, float, float]
    start_date: str
    end_date: str
    available: bool
    entries: list[FishingEntry]
    total_apparent_fishing_hours: float = Field(ge=0)
    vessel_count: int = Field(ge=0)
    evidence: Evidence
    error: str | None = None
    caveat: str


class GlobalFishingWatchSource(Source):
    name = "gfw.4wings"
    provider = Provider.GFW
    variables = ("fishing_effort",)
    requires = "has_gfw"
    docs_url = DOCS_URL
    timeout_s = 35

    async def effort(
        self,
        bbox: tuple[float, float, float, float],
        *,
        days: int = 30,
    ) -> FishingEffortResponse:
        west, south, east, north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox must be west,south,east,north in WGS84")
        settings = get_settings()
        # Public GFW effort normally trails real time by roughly 72 hours. Ending
        # four days back avoids asking for a period the dataset cannot contain.
        end = (utcnow() - timedelta(days=4)).date()
        start = end - timedelta(days=min(max(days, 1), 366))
        polygon = {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        }
        if not settings.has_gfw:
            evidence = Evidence.unavailable(
                dataset_id=DATASET,
                provider=Provider.GFW,
                variable="fishing_effort",
                reason="GFW_API_TOKEN is not configured",
                url=DOCS_URL,
            )
            return FishingEffortResponse(
                bbox=bbox,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                available=False,
                entries=[],
                total_apparent_fishing_hours=0,
                vessel_count=0,
                evidence=evidence,
                error=evidence.notes,
                caveat="No GFW request was attempted.",
            )
        assert settings.gfw_api_token
        result = await self.fetch(
            REPORT_URL,
            method="POST",
            params={
                "format": "JSON",
                "group-by": "VESSEL_ID",
                "temporal-resolution": "ENTIRE",
                "datasets[0]": DATASET,
                "date-range": f"{start.isoformat()},{end.isoformat()}",
                "spatial-aggregation": "false",
                "spatial-resolution": "LOW",
            },
            headers={"Authorization": f"Bearer {settings.gfw_api_token.get_secret_value()}"},
            json_body={"geojson": polygon},
            conditional=False,
            retries=0,
        )
        if not result.ok:
            error = result.error or f"GFW returned HTTP {result.status}"
            evidence = Evidence.unavailable(
                dataset_id=DATASET,
                provider=Provider.GFW,
                variable="fishing_effort",
                reason=error,
                url=DOCS_URL,
            )
            return FishingEffortResponse(
                bbox=bbox,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                available=False,
                entries=[],
                total_apparent_fishing_hours=0,
                vessel_count=0,
                evidence=evidence,
                error=error,
                caveat="The upstream report was unavailable; zero is not being asserted.",
            )
        payload = result.json()
        raw_groups = payload.get("entries", []) if isinstance(payload, dict) else []
        raw_entries: list[dict[str, Any]] = []
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            nested = [value for value in group.values() if isinstance(value, list)]
            if nested:
                for rows in nested:
                    raw_entries.extend(row for row in rows if isinstance(row, dict))
            else:
                raw_entries.append(group)
        entries: list[FishingEntry] = []
        for row in raw_entries:
            try:
                hours = float(row.get("hours", 0))
            except (TypeError, ValueError):
                continue
            lat = row.get("lat")
            lon = row.get("lon")
            parsed_lat = float(lat) if isinstance(lat, (int, float)) and -90 <= lat <= 90 else None
            parsed_lon = (
                float(lon) if isinstance(lon, (int, float)) and -180 <= lon <= 180 else None
            )
            if hours < 0:
                continue
            entries.append(
                FishingEntry(
                    vessel_id=str(row["vesselId"]) if row.get("vesselId") else None,
                    mmsi=str(row["mmsi"]) if row.get("mmsi") else None,
                    name=str(row["shipName"]) if row.get("shipName") else None,
                    flag=str(row["flag"]) if row.get("flag") else None,
                    gear_type=str(row["geartype"]) if row.get("geartype") else None,
                    lat=parsed_lat,
                    lon=parsed_lon,
                    apparent_fishing_hours=round(hours, 3),
                )
            )
        total = round(sum(entry.apparent_fishing_hours for entry in entries), 3)
        evidence = Evidence(
            dataset_id=DATASET,
            provider=Provider.GFW,
            variable="fishing_effort",
            value=total,
            unit="apparent fishing hours",
            provenance=result.provenance,
            freshness=Freshness.of("fishing_effort", utcnow() - timedelta(days=4)),
            url=DOCS_URL,
            method="sum of GFW apparent-fishing hours in the requested polygon and period",
            notes=(
                "GFW classifies apparent fishing from AIS movement; it is not proof of fishing "
                "and it normally excludes the most recent roughly 72 hours."
            ),
            citations=[
                Citation(
                    label="Global Fishing Watch 4Wings apparent fishing effort",
                    provider=Provider.GFW,
                    url=DOCS_URL,
                    identifier=DATASET,
                )
            ],
        )
        unique_vessels = {entry.vessel_id or entry.mmsi for entry in entries}
        unique_vessels.discard(None)
        return FishingEffortResponse(
            bbox=bbox,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            available=True,
            entries=entries,
            total_apparent_fishing_hours=total,
            vessel_count=len(unique_vessels),
            evidence=evidence,
            caveat=(
                "Apparent fishing is an algorithmic classification from AIS, not proof of "
                "fishing or illegal activity. Coverage depends on vessels transmitting AIS."
            ),
        )


gfw = GlobalFishingWatchSource()

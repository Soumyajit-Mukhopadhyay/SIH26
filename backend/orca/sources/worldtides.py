"""WorldTides v3 astronomical tide predictions.

The adapter is deliberately dormant without ``WORLDTIDES_API_KEY``. A missing
credential is returned as unavailable evidence, because silently omitting tide
from a question that explicitly asks for it would make the answer look more
complete than it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from orca.config import get_settings
from orca.provenance import Citation, Evidence, Freshness, Provider, utcnow
from orca.sources.base import Source

API_URL = "https://www.worldtides.info/api/v3"
DOCS_URL = "https://www.worldtides.info/apidocs"

_CITATION = Citation(
    label="WorldTides API v3 — tide heights and extremes",
    provider=Provider.WORLD_TIDES,
    url=DOCS_URL,
    identifier="api/v3",
)


@dataclass(slots=True)
class TideForecast:
    available: bool
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    current_height_m: float | None = None
    current_time: str | None = None
    next_high: dict[str, Any] | None = None
    next_low: dict[str, Any] | None = None
    datum: str | None = None
    station: str | None = None
    atlas: str | None = None
    copyright: str | None = None
    error: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "current_height_m": self.current_height_m,
            "current_time": self.current_time,
            "next_high": self.next_high,
            "next_low": self.next_low,
            "datum": self.datum,
            "station": self.station,
            "atlas": self.atlas,
            "copyright": self.copyright,
            "error": self.error,
        }


class WorldTides(Source):
    name = "worldtides.v3"
    provider = Provider.WORLD_TIDES
    variables = ("tide_height", "tide_extreme")
    requires = "has_worldtides"
    docs_url = DOCS_URL

    async def forecast(self, lat: float, lon: float, *, days: int = 2) -> TideForecast:
        settings = get_settings()
        if not settings.has_worldtides:
            reason = (
                "WORLDTIDES_API_KEY is not configured. ORCA cannot verify tide height or the "
                "next high/low tide for this point."
            )
            return TideForecast(
                available=False,
                summary=reason,
                evidence=[
                    Evidence.unavailable(
                        dataset_id=self.name,
                        provider=self.provider,
                        variable="tide_height",
                        reason=reason,
                        url=DOCS_URL,
                    )
                ],
                error=reason,
            )

        # POST keeps the API key out of URLs, response objects and health logs.
        fetched = await self.fetch(
            API_URL,
            method="POST",
            form_body={
                "heights": "",
                "extremes": "",
                "localtime": "",
                "date": "today",
                "days": str(max(1, min(days, 7))),
                "lat": f"{lat:.5f}",
                "lon": f"{lon:.5f}",
                "key": settings.worldtides_api_key.get_secret_value(),  # type: ignore[union-attr]
            },
            conditional=False,
            retries=1,
            timeout_s=15.0,
        )
        if not fetched.ok:
            reason = fetched.error or f"WorldTides HTTP {fetched.status}"
            return TideForecast(
                available=False,
                summary=f"Tide prediction unavailable: {reason}",
                evidence=[
                    Evidence.unavailable(
                        dataset_id=self.name,
                        provider=self.provider,
                        variable="tide_height",
                        reason=reason,
                        url=DOCS_URL,
                    )
                ],
                error=reason,
            )

        try:
            payload = fetched.json()
        except (ValueError, TypeError) as exc:
            reason = f"WorldTides returned unreadable JSON: {exc}"
            return TideForecast(
                available=False,
                summary=reason,
                evidence=[
                    Evidence.unavailable(
                        dataset_id=self.name,
                        provider=self.provider,
                        variable="tide_height",
                        reason=reason,
                        url=DOCS_URL,
                    )
                ],
                error=reason,
            )

        if int(payload.get("status", 200)) != 200:
            reason = str(payload.get("error") or "WorldTides rejected the request")
            return TideForecast(
                available=False,
                summary=f"Tide prediction unavailable: {reason}",
                evidence=[
                    Evidence.unavailable(
                        dataset_id=self.name,
                        provider=self.provider,
                        variable="tide_height",
                        reason=reason,
                        url=DOCS_URL,
                    )
                ],
                error=reason,
            )

        heights = [_normalise(item) for item in payload.get("heights", [])]
        heights = [item for item in heights if item is not None]
        extremes = [_normalise(item) for item in payload.get("extremes", [])]
        extremes = [item for item in extremes if item is not None]
        now = utcnow()
        current = min(heights, key=lambda item: abs((item["when"] - now).total_seconds()), default=None)
        future = [item for item in extremes if item["when"] >= now]
        high = next((item for item in future if str(item.get("type", "")).lower() == "high"), None)
        low = next((item for item in future if str(item.get("type", "")).lower() == "low"), None)

        evidence: list[Evidence] = []
        common_notes = "Astronomical tide prediction, not a measured gauge observation."
        if current is not None:
            evidence.append(
                Evidence(
                    dataset_id=self.name,
                    provider=self.provider,
                    variable="tide_height",
                    value=round(current["height"], 3),
                    unit="m",
                    provenance=fetched.provenance,
                    freshness=Freshness.of("tide_height", current["when"]),
                    url=DOCS_URL,
                    location=(lon, lat),
                    citations=[_CITATION],
                    notes=common_notes,
                )
            )
        for item in (high, low):
            if item is None:
                continue
            evidence.append(
                Evidence(
                    dataset_id=self.name,
                    provider=self.provider,
                    variable="tide_extreme",
                    value=round(item["height"], 3),
                    unit="m",
                    provenance=fetched.provenance,
                    freshness=Freshness.of("tide_extreme", item["when"]),
                    url=DOCS_URL,
                    location=(lon, lat),
                    citations=[_CITATION],
                    notes=f"Predicted {item.get('type', 'tide')} relative to {payload.get('responseDatum') or 'MSL'}. {common_notes}",
                )
            )

        if current is None and not future:
            reason = "WorldTides returned no height or high/low events for this location."
            return TideForecast(
                available=False,
                summary=reason,
                evidence=evidence,
                error=reason,
            )

        parts: list[str] = []
        if current is not None:
            parts.append(f"predicted tide {current['height']:.2f} m at {_clock(current['when'])}")
        if high is not None:
            parts.append(f"next high {high['height']:.2f} m at {_clock(high['when'])}")
        if low is not None:
            parts.append(f"next low {low['height']:.2f} m at {_clock(low['when'])}")

        def public(item: dict[str, Any] | None) -> dict[str, Any] | None:
            if item is None:
                return None
            return {
                "type": item.get("type"),
                "height_m": round(item["height"], 3),
                "time": item["when"].isoformat(),
            }

        return TideForecast(
            available=True,
            summary=" · ".join(parts),
            evidence=evidence,
            current_height_m=None if current is None else round(current["height"], 3),
            current_time=None if current is None else current["when"].isoformat(),
            next_high=public(high),
            next_low=public(low),
            datum=payload.get("responseDatum"),
            station=payload.get("station"),
            atlas=payload.get("atlas"),
            copyright=payload.get("copyright"),
        )


def _normalise(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("height") is None:
        return None
    when: datetime | None = None
    if item.get("date"):
        try:
            when = datetime.fromisoformat(str(item["date"]))
        except ValueError:
            when = None
    if when is None and item.get("dt") is not None:
        try:
            when = datetime.fromtimestamp(float(item["dt"]), tz=UTC)
        except (TypeError, ValueError, OSError):
            return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return {
        "when": when.astimezone(UTC),
        "height": float(item["height"]),
        "type": item.get("type"),
    }


def _clock(when: datetime) -> str:
    return when.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %H:%M IST")


worldtides = WorldTides()

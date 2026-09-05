"""IMD official-warning adapter — parsers and honesty rules.

Live JSON needs a registered key (401 without one). These tests never hit the
keyed gateway; CAP RSS and JSON bodies are fixtures.
"""

from __future__ import annotations

import json

import pytest

from orca.sources.base import Fetched
from orca.sources.imd import (
    CAP_RSS_URL,
    IMD_ENDPOINTS,
    MARINE_ALERT_PRODUCTS,
    alerts_at,
    classify_alert_text,
    coastal_area_relevant,
    is_active_alert,
    parse_cap_rss,
    _cyclone_from_payload,
)


CAP_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Latest alerts from India Meteorological Department</title>
    <item>
      <title>Cyclonic Storm over Bay of Bengal</title>
      <link>https://cap-sources.s3.amazonaws.com/in-imd-en/storm.xml</link>
      <description>Fishermen are advised not to venture into the sea along the Andhra coast.</description>
      <guid>urn:oid:2.49.0.1.356.0.test.1</guid>
      <pubDate>Thu, 03 Sep 2026 07:21:21 +0000</pubDate>
    </item>
    <item>
      <title>Heavy to very heavy rainfall</title>
      <description>Heavy rainfall very likely over East Madhya Pradesh.</description>
      <guid>urn:oid:2.49.0.1.356.0.test.2</guid>
      <pubDate>Thu, 03 Sep 2026 07:19:24 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

INLAND_RAIN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Heavy to very heavy rainfall</title>
      <description>Heavy rainfall very likely over East Madhya Pradesh.</description>
      <pubDate>Thu, 03 Sep 2026 07:21:21 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


def test_public_catalogue_includes_marine_warning_paths() -> None:
    assert "fishermenwarning" in IMD_ENDPOINTS
    assert "cyclone_track" in IMD_ENDPOINTS
    assert "lightning" in IMD_ENDPOINTS
    assert set(MARINE_ALERT_PRODUCTS) <= set(IMD_ENDPOINTS)


def test_classify_alert_text() -> None:
    assert classify_alert_text("Cyclonic Storm over Bay of Bengal") == "cyclone"
    assert classify_alert_text("Fishermen warning for Kerala coast") == "fishermen"
    assert classify_alert_text("Thunderstorm & Lightning") == "lightning"
    assert classify_alert_text("Heavy to very heavy rainfall") == "rainfall"


def test_nil_and_none_are_not_active_alerts() -> None:
    assert is_active_alert("NIL") is False
    assert is_active_alert("none") is False
    assert is_active_alert("unavailable") is False
    assert is_active_alert("Fishermen advised not to venture") is True


def test_inland_rainfall_is_not_relevant_off_chennai() -> None:
    assert coastal_area_relevant("East Madhya Pradesh", 13.1, 80.3) is False
    assert coastal_area_relevant("Odisha coast", 19.8, 86.7) is True


def test_parse_cap_rss_items() -> None:
    items = parse_cap_rss(CAP_RSS)
    assert len(items) == 2
    assert items[0]["title"].startswith("Cyclonic")


def test_cyclone_track_payload() -> None:
    payload = {
        "status": True,
        "data": {
            "observed": [{"CYCLONE_NAME": "BULBUL", "lat": "13.2", "lon": "91.2"}],
            "forecast": [],
        },
    }
    assert "BULBUL" in (_cyclone_from_payload(payload) or "")
    assert _cyclone_from_payload({"status": True, "data": {"observed": [], "forecast": []}}) is None


def _cap_ok(text: str = CAP_RSS) -> Fetched:
    return Fetched(ok=True, source="imd.cap_rss", url=CAP_RSS_URL, status=200, text=text)


def _dormant(url: str) -> Fetched:
    return Fetched(ok=False, source="imd.api", url=url, error="dormant: has_imd is not configured")


@pytest.mark.asyncio
async def test_alerts_at_uses_cap_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cap_fetch(self, url, **kwargs):  # noqa: ANN001
        return _cap_ok()

    async def fake_api_fetch(self, url, **kwargs):  # noqa: ANN001
        return _dormant(url)

    monkeypatch.setattr("orca.sources.imd.ImdCap.fetch", fake_cap_fetch)
    monkeypatch.setattr("orca.sources.imd.ImdApi.fetch", fake_api_fetch)

    result = await alerts_at(13.1, 80.3)
    assert result.verified is True
    assert result.cap_ok is True
    assert result.keyed_api_ok is False
    blob = f"{result.cyclone_alert} {result.fishermen_warning}".lower()
    assert "cyclonic" in blob
    assert "fishermen" in blob
    assert result.as_tool_data()["official_alerts_verified"] is True


@pytest.mark.asyncio
async def test_inland_rain_only_is_verified_none_at_sea(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cap_fetch(self, url, **kwargs):  # noqa: ANN001
        return _cap_ok(INLAND_RAIN_RSS)

    monkeypatch.setattr("orca.sources.imd.ImdCap.fetch", fake_cap_fetch)

    result = await alerts_at(13.1, 80.3)
    assert result.verified is True
    assert result.cyclone_alert == "none"
    assert result.fishermen_warning == "none"
    assert result.lightning_alert == "none"
    assert result.rainfall_advisory == "none"


@pytest.mark.asyncio
async def test_failed_feeds_stay_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cap_fetch(self, url, **kwargs):  # noqa: ANN001
        return Fetched(ok=False, source="imd.cap_rss", url=url, error="HTTP 503")

    monkeypatch.setattr("orca.sources.imd.ImdCap.fetch", fake_cap_fetch)

    result = await alerts_at(13.1, 80.3)
    assert result.verified is False
    assert result.cyclone_alert == "unavailable"
    assert result.as_tool_data()["official_alerts_verified"] is False


@pytest.mark.asyncio
async def test_keyed_json_cyclone_merges_when_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orca.config import reload_settings

    monkeypatch.setenv("IMD_API_KEY", "test-imd-key-not-real")
    reload_settings()

    async def fake_cap_fetch(self, url, **kwargs):  # noqa: ANN001
        return _cap_ok(INLAND_RAIN_RSS)

    async def fake_api_fetch(self, url, **kwargs):  # noqa: ANN001
        if url.endswith("cyclone_track"):
            return Fetched(
                ok=True,
                source="imd.api",
                url=url,
                status=200,
                text=json.dumps(
                    {
                        "status": True,
                        "data": {
                            "observed": [{"CYCLONE_NAME": "BULBUL"}],
                            "forecast": [],
                        },
                    }
                ),
            )
        if url.endswith("portwarning"):
            return Fetched(
                ok=True,
                source="imd.api",
                url=url,
                status=200,
                text=json.dumps([{"Port Name": "Chennai", "Warning": "NIL"}]),
            )
        return Fetched(
            ok=True,
            source="imd.api",
            url=url,
            status=200,
            text=json.dumps({"Warning": "NIL"}),
        )

    monkeypatch.setattr("orca.sources.imd.ImdCap.fetch", fake_cap_fetch)
    monkeypatch.setattr("orca.sources.imd.ImdApi.fetch", fake_api_fetch)

    try:
        result = await alerts_at(13.1, 80.3)
        assert result.verified is True
        assert result.keyed_api_ok is True
        assert "BULBUL" in result.cyclone_alert
        assert result.port_warning == "none"
    finally:
        monkeypatch.delenv("IMD_API_KEY", raising=False)
        reload_settings()

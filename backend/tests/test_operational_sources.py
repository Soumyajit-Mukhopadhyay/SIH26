"""Provider response contracts and value-validation rules for the new adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
import xarray as xr
from pydantic import SecretStr
from sgp4.api import Satrec

from orca.provenance import Evidence, Freshness, Provenance
from orca.services.cross_validation import ValidationStatus, compare
from orca.services.overpass import SatelliteSpec, _daylight_at, predict_for_tle, subpoint
from orca.sources.ais import VesselPosition, assess_collision, parse_position
from orca.sources.base import Fetched
from orca.sources.celestrak import TleRecord
from orca.sources.nasa import _size_megabytes

LINE1 = "1 25544U 98067A   24001.00000000  .00000000  00000+0  00000+0 0  9999"
LINE2 = "2 25544  51.6400  10.0000 0005000  20.0000  40.0000 15.50000000123456"
EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def _evidence(value: float, unit: str, when: datetime, dataset: str = "test") -> Evidence:
    return Evidence(
        dataset_id=dataset,
        provider="test",
        variable="wind_speed",
        value=value,
        unit=unit,
        provenance=Provenance.LIVE,
        freshness=Freshness.of("wind_speed", when, retrieved_at=when),
    )


def test_sgp4_subpoint_is_a_physical_wgs84_position() -> None:
    lon, lat, altitude_km = subpoint(Satrec.twoline2rv(LINE1, LINE2), EPOCH)
    assert -180 <= lon <= 180
    assert -90 <= lat <= 90
    assert 350 <= altitude_km <= 500


def test_pass_prediction_detects_a_track_point_inside_the_swath() -> None:
    satellite = Satrec.twoline2rv(LINE1, LINE2)
    lon, lat, _altitude = subpoint(satellite, EPOCH)
    tle = TleRecord(
        name="ISS",
        norad_id=25544,
        line1=LINE1,
        line2=LINE2,
        epoch=EPOCH,
        retrieved_at=EPOCH,
        provenance=Provenance.LIVE,
    )
    spec = SatelliteSpec("test", 25544, "test sensor", 100.0, False, "https://example.test")
    passes = predict_for_tle(
        spec,
        tle,
        lat=lat,
        lon=lon,
        start=EPOCH,
        horizon_hours=1,
        step_seconds=30,
    )
    assert passes
    assert passes[0].closest_distance_km < 0.1


def test_daylight_uses_target_solar_position() -> None:
    equinox = datetime(2026, 3, 20, tzinfo=UTC)
    assert _daylight_at(0, 0, equinox.replace(hour=12)) is True
    assert _daylight_at(0, 0, equinox) is False


def test_nasa_archive_sizes_are_converted_to_megabytes() -> None:
    assert _size_megabytes(1.5, "GB") == 1500
    assert _size_megabytes(250, "KB") == 0.25
    assert _size_megabytes(2, "unknown") is None


def test_cross_validation_converts_knots_and_checks_time_alignment() -> None:
    first = _evidence(10.0, "kn", EPOCH, "primary")
    second = _evidence(5.14444, "m/s", EPOCH + timedelta(minutes=30), "secondary")
    result = compare(
        "wind_speed",
        first,
        second,
        canonical_unit="m/s",
        tolerance=0.1,
        tolerance_basis="test tolerance",
        maximum_time_separation_hours=1,
    )
    assert result.status is ValidationStatus.AGREE
    assert result.absolute_difference == pytest.approx(0, abs=0.001)

    late = second.model_copy(
        update={
            "freshness": Freshness.of(
                "wind_speed", EPOCH + timedelta(hours=4), retrieved_at=EPOCH + timedelta(hours=4)
            )
        }
    )
    result = compare(
        "wind_speed",
        first,
        late,
        canonical_unit="m/s",
        tolerance=0.1,
        tolerance_basis="test tolerance",
        maximum_time_separation_hours=1,
    )
    assert result.status is ValidationStatus.INCONCLUSIVE


def test_ais_position_parser_rejects_sentinels_and_preserves_live_coordinates() -> None:
    position = parse_position(
        {
            "MessageType": "PositionReport",
            "MetaData": {
                "MMSI": 419000001,
                "ShipName": "TEST BOAT",
                "latitude": 13.1,
                "longitude": 80.4,
                "time_utc": "2026-08-31T18:00:00Z",
            },
            "Message": {"PositionReport": {"Sog": 8.2, "Cog": 92.5, "TrueHeading": 511}},
        }
    )
    assert position is not None
    assert position.mmsi == "419000001"
    assert position.speed_kn == 8.2
    assert position.heading_deg is None
    assert position.provenance is Provenance.LIVE


def test_collision_screen_detects_a_head_on_closest_approach() -> None:
    target = VesselPosition(
        mmsi="419000002",
        lat=0,
        lon=0.1,
        speed_kn=10,
        course_deg=270,
        message_type="PositionReport",
        received_at=EPOCH,
    )
    advisory = assess_collision(
        0,
        0,
        target,
        own_speed_kn=10,
        own_course_deg=90,
    )
    assert advisory.level == "danger"
    assert advisory.dcpa_nm == pytest.approx(0, abs=0.01)
    assert advisory.tcpa_minutes == pytest.approx(18.03, abs=0.1)


@pytest.mark.asyncio
async def test_gfw_flattens_dataset_groups_and_sums_only_nonnegative_hours(monkeypatch) -> None:
    import orca.sources.gfw as module

    source = module.GlobalFishingWatchSource()
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(has_gfw=True, gfw_api_token=SecretStr("test-token")),
    )
    payload = {
        "entries": [
            {
                "public-global-fishing-effort:v3": [
                    {
                        "vesselId": "v1",
                        "mmsi": "4191",
                        "shipName": "A",
                        "hours": 1.25,
                        "lat": 13.1,
                        "lon": 80.4,
                    },
                    {"vesselId": "v1", "hours": 0.75, "lat": 13.2, "lon": 80.5},
                    {"vesselId": "bad", "hours": -3},
                ]
            }
        ]
    }

    async def fake_fetch(*_args, **_kwargs):
        return Fetched(
            ok=True,
            source=source.name,
            url=module.REPORT_URL,
            status=200,
            text=json.dumps(payload),
        )

    monkeypatch.setattr(source, "fetch", fake_fetch)
    report = await source.effort((79.9, 12.6, 80.9, 13.6), days=30)
    assert report.available is True
    assert report.total_apparent_fishing_hours == 2.0
    assert report.vessel_count == 1
    assert len(report.entries) == 2
    assert "not proof" in report.caveat.lower()


@pytest.mark.asyncio
async def test_cmems_wave_chooses_nearest_time_and_validates_range(monkeypatch) -> None:
    import copernicusmarine

    from orca.sources.cmems import cmems

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    dataset = xr.Dataset(
        {"VHM0": (("time", "latitude", "longitude"), np.array([[[0.7]], [[1.4]]]))},
        coords={
            "time": [
                np.datetime64(now.replace(tzinfo=None) - timedelta(hours=1)),
                np.datetime64(now.replace(tzinfo=None) + timedelta(hours=6)),
            ],
            "latitude": [13.125],
            "longitude": [80.375],
        },
    )
    monkeypatch.setattr(cmems, "installed", lambda: True)
    monkeypatch.setattr(cmems, "_credentials", lambda: ("user", "password"))
    monkeypatch.setattr(copernicusmarine, "open_dataset", lambda **_kwargs: dataset)
    value = await cmems.wave_at(13.1, 80.4)
    assert value.value == 0.7
    assert value.unit == "m"
    assert value.provenance is Provenance.LIVE


@pytest.mark.asyncio
async def test_sentinel_oauth_and_catalogue_shapes_are_parsed(monkeypatch) -> None:
    import orca.sources.sentinel_hub as module

    source = module.SentinelHubSource()
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            has_cdse=True,
            cdse_client_id="client",
            cdse_client_secret=SecretStr("secret"),
        ),
    )
    responses = iter(
        [
            Fetched(
                ok=True,
                source=source.name,
                url=module.TOKEN_URL,
                status=200,
                text=json.dumps({"access_token": "token", "expires_in": 600}),
            ),
            Fetched(
                ok=True,
                source=source.name,
                url=module.CATALOG_URL,
                status=200,
                text=json.dumps(
                    {
                        "features": [
                            {
                                "id": "S3-test",
                                "collection": "sentinel-3-olci",
                                "bbox": [79, 12, 81, 14],
                                "properties": {"datetime": "2026-08-31T04:30:00Z"},
                                "links": [],
                            }
                        ]
                    }
                ),
            ),
        ]
    )

    async def fake_fetch(*_args, **_kwargs):
        return next(responses)

    monkeypatch.setattr(source, "fetch", fake_fetch)
    end = datetime(2026, 8, 31, tzinfo=UTC)
    result = await source.search("sentinel-3-olci", (79, 12, 81, 14), end - timedelta(days=1), end)
    assert result.returned == 1
    assert result.items[0].item_id == "S3-test"
    assert result.items[0].acquired_at == datetime(2026, 8, 31, 4, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sentinel_preview_uses_catalogued_acquisition_window(monkeypatch) -> None:
    import orca.sources.sentinel_hub as module

    source = module.SentinelHubSource()
    acquired = datetime(2026, 8, 31, 4, 30, tzinfo=UTC)

    async def fake_search(*_args, **_kwargs):
        return module.SentinelSearchResponse(
            collection="sentinel-3-olci",
            bbox=(80.2, 12.9, 80.6, 13.3),
            start_time=acquired - timedelta(days=1),
            end_time=acquired,
            returned=1,
            items=[
                module.SentinelItem(
                    item_id="S3-test",
                    collection="sentinel-3-olci",
                    acquired_at=acquired,
                    bbox=[80.2, 12.9, 80.6, 13.3],
                )
            ],
            provenance="live",
            note="test",
        )

    async def fake_token():
        return "token"

    captured: dict[str, object] = {}

    async def fake_fetch(*_args, **kwargs):
        captured.update(kwargs)
        return Fetched(
            ok=True,
            source=source.name,
            url=module.PROCESS_URL,
            status=200,
            content=b"\x89PNG\r\n\x1a\npreview",
        )

    monkeypatch.setattr(source, "search", fake_search)
    monkeypatch.setattr(source, "_access_token", fake_token)
    monkeypatch.setattr(source, "fetch", fake_fetch)
    preview = await source.preview(13.1, 80.4)
    assert preview.item_id == "S3-test"
    assert preview.content.startswith(b"\x89PNG")
    body = captured["json_body"]
    assert isinstance(body, dict)
    assert body["input"]["data"][0]["type"] == "sentinel-3-olci"

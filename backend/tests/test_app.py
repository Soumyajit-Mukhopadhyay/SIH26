"""Step 1's other stated test: the server boots and ``/healthz`` returns 200.

Uses the real lifespan, so the PostGIS and Redis probes actually run — a green
result here means the infrastructure selection logic works against live
infrastructure, not against a mock of it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orca.config import reload_settings
from orca.main import create_app


@pytest.fixture(scope="module")
def client():
    reload_settings()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def restore_settings():
    """Tests that reconfigure the environment must not leak that into the
    module-scoped client, whose settings are cached process-wide."""
    yield
    reload_settings()


class TestHealth:
    def test_healthz_is_ok(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200, r.json().get("degraded")
        body = r.json()
        assert body["status"] == "ok"
        assert body["degraded"] == []
        assert body["service"] == "orca"

    def test_healthz_reports_the_infrastructure_it_actually_selected(self, client):
        infra = client.get("/healthz").json()["infrastructure"]
        assert infra["db"] in {"postgis", "sqlite"}
        assert infra["cache"] in {"redis", "memory"}
        assert infra["queue"] in {"arq", "apscheduler"}
        # The geofence index must match the store — an STRtree over a PostGIS
        # fence set would be answering from the wrong place.
        assert infra["geofence_index"] == (
            "postgis" if infra["db"] == "postgis" else "shapely-strtree"
        )

    def test_healthz_lists_dormant_capabilities_rather_than_hiding_them(self, client):
        caps = client.get("/healthz").json()["capabilities"]
        assert caps["groq"] is True
        assert caps["imd"] is False  # documented gap, reported not omitted

    def test_a_failing_upstream_source_does_not_turn_healthz_red(self, client):
        # The design commitment is that a Bhuvan or IMD outage degrades one layer
        # visibly and the answer still computes. If a single dead optional source
        # made /healthz return 503, every orchestrator would restart a perfectly
        # healthy ORCA. Source trouble belongs in `notes` and /freshness.
        from orca.obs.health import registry

        registry.declare("imd.rest", provider="IMD", variables=["cyclone_track"])
        registry.record_failure("imd.rest", "401 Unauthorized: key not whitelisted")

        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["degraded"] == []
        assert any("source(s) have never succeeded" in n for n in body["notes"])

    def test_root_points_at_the_ops_endpoints(self, client):
        body = client.get("/").json()
        assert body["health"] == "/healthz"
        assert body["freshness"] == "/freshness"


class TestFreshness:
    def test_freshness_is_well_formed_before_any_source_has_run(self, client):
        body = client.get("/freshness").json()
        assert "generated_at" in body
        assert isinstance(body["sources"], list)
        assert "untried" in body["legend"]

    def test_freshness_reflects_a_recorded_success(self, client):
        from orca.obs.health import registry
        from orca.provenance import Provenance, Provider

        registry.declare(
            "open_meteo.marine",
            provider=Provider.OPEN_METEO,
            variables=["wave_height", "wind_speed"],
        )
        registry.record_success("open_meteo.marine", provenance=Provenance.LIVE, latency_ms=812.0)

        rows = {r["source"]: r for r in client.get("/freshness").json()["sources"]}
        row = rows["open_meteo.marine"]
        assert row["status"] == "ok"
        assert row["provenance"] == "live"
        assert row["age_hours"] < 0.01
        assert row["median_latency_ms"] == 812.0

    def test_freshness_reports_a_failing_source_honestly(self, client):
        from orca.obs.health import registry

        registry.declare("bhuvan.wms", provider="Bhuvan", variables=["chlorophyll"])
        registry.record_failure("bhuvan.wms", "ConnectTimeout: 8s")

        rows = {r["source"]: r for r in client.get("/freshness").json()["sources"]}
        assert rows["bhuvan.wms"]["status"] == "failing"
        assert "ConnectTimeout" in rows["bhuvan.wms"]["last_error"]


class TestConfigEndpoint:
    def test_effective_config_masks_every_secret(self, client):
        from orca.config import get_settings

        raw = client.get("/config").text
        for secret in get_settings().secret_values():
            assert secret not in raw, "a raw secret was served over HTTP"

    def test_effective_config_still_reports_the_driver(self, client):
        s = client.get("/config").json()["settings"]
        assert s["orca_db_driver"] == "auto"


class TestInfrastructureFallback:
    """ORCA must run with no infrastructure at all.

    This is not a consolation prize for a laptop with Docker off — it is the
    property that lets the demo run on a judge's machine, and it decays silently
    unless something asserts it. Postgres and Redis are accelerators; these tests
    say so in code.
    """

    def test_runs_with_no_infrastructure_at_all(self, monkeypatch, restore_settings):
        monkeypatch.setenv("ORCA_DB_DRIVER", "sqlite")
        monkeypatch.setenv("REDIS_URL", "")
        reload_settings()

        with TestClient(create_app()) as c:
            r = c.get("/healthz")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert body["degraded"] == []
            assert body["infrastructure"] == {
                **body["infrastructure"],
                "db": "sqlite",
                "cache": "memory",
                "queue": "apscheduler",
                "geofence_index": "shapely-strtree",
            }

    def test_unreachable_infrastructure_degrades_instead_of_crashing(
        self, monkeypatch, restore_settings
    ):
        # Under `auto`, a dead Postgres and a dead Redis are a documented
        # fallback, not an outage. The reason must be recorded, not swallowed.
        monkeypatch.setenv("ORCA_DB_DRIVER", "auto")
        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrongpassword@127.0.0.1:59999/nope")
        monkeypatch.setenv("DATABASE_URL_LOCAL", "")
        monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:59998/0")
        reload_settings()

        with TestClient(create_app()) as c:
            body = c.get("/healthz").json()
            infra = body["infrastructure"]
            assert body["status"] == "ok", "a dead accelerator must not fail the service"
            assert infra["db"] == "sqlite"
            assert infra["cache"] == "memory"
            assert infra["queue"] == "apscheduler"
            assert infra["db_fallback_reason"] is not None
            assert infra["redis_reason"] is not None

    def test_explicit_postgis_driver_refuses_to_start_on_an_unreachable_db(
        self, monkeypatch, restore_settings
    ):
        # `auto` may fall back silently; `postgis` may not. Someone who asked for
        # Postgres explicitly needs to hear that they did not get it.
        monkeypatch.setenv("ORCA_DB_DRIVER", "postgis")
        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrongpassword@127.0.0.1:59999/nope")
        monkeypatch.setenv("DATABASE_URL_LOCAL", "")
        reload_settings()

        with pytest.raises(RuntimeError, match="unreachable"), TestClient(create_app()):
            pass


class TestStreamingHeaders:
    def test_openapi_documents_the_streaming_paths_when_they_land(self, client):
        # Placeholder assertion with a purpose: it fails loudly at Step 8 if the
        # SSE route is mounted outside STREAMING_PATH_PREFIXES and would
        # therefore be silently buffered.
        from orca.main import STREAMING_PATH_PREFIXES

        paths = client.get("/openapi.json").json()["paths"]
        streaming = [p for p in paths if p.startswith(("/agent/stream", "/alerts/stream"))]
        for path in streaming:
            assert path.startswith(STREAMING_PATH_PREFIXES)

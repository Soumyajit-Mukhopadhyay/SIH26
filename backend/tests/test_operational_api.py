"""The browser-facing operational endpoints are mounted and typed."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orca.main import create_app


def test_operational_endpoints_are_in_openapi() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert {
            "/integrations/status",
            "/satellites/overpasses",
            "/catalog/nasa",
            "/catalog/sentinel",
            "/catalog/cmems",
            "/traffic/ais",
            "/traffic/fishing-effort",
            "/validation/point",
        } <= set(paths)


def test_integration_status_never_exposes_credentials() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/integrations/status")
        assert response.status_code == 200
        body = response.json()
        assert all(item["implemented"] is True for item in body.values())
        raw = response.text.lower()
        assert "password" not in raw
        assert "api_key" not in raw

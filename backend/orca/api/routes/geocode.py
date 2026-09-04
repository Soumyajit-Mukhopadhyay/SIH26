"""Prototype location search via OpenStreetMap Nominatim.

Thin proxy so ORCA can set a proper User-Agent (Nominatim usage policy) and
so the frontend never talks to Nominatim directly. Does not change safety,
risk, or decision logic — only answers "where is this place?".
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Query

from orca.sources.base import get_client

log = logging.getLogger(__name__)

router = APIRouter(tags=["geocode"])

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: India-biased viewbox (lon_min, lat_min, lon_max, lat_max). Soft bias only —
#: ``bounded=0`` so results outside India can still appear.
INDIA_VIEWBOX = "68.0,6.0,97.5,37.5"

#: Tiny in-process cache to avoid repeating identical prototype queries.
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_S = 300.0
_CACHE_MAX = 64


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    hit = _CACHE.get(key)
    if hit is None:
        return None
    expires, payload = hit
    if time.monotonic() > expires:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: list[dict[str, Any]]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # Drop an arbitrary oldest-ish entry.
        oldest = next(iter(_CACHE))
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.monotonic() + _CACHE_TTL_S, payload)


def _split_display_name(display: str) -> tuple[str, str]:
    parts = [p.strip() for p in display.split(",") if p.strip()]
    if not parts:
        return display, ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], ", ".join(parts[1:3])


@router.get("/geocode/search", summary="Prototype place search (OpenStreetMap Nominatim)")
async def geocode_search(
    q: str = Query(..., min_length=2, max_length=120, description="Place name to search"),
    limit: int = Query(5, ge=1, le=8),
) -> dict[str, Any]:
    """Search named places. Returns lat/lon only — never a safety verdict."""
    query = " ".join(q.strip().split())
    if len(query) < 2:
        return {"results": [], "provider": "OpenStreetMap/Nominatim", "ok": True}

    cache_key = f"{query.lower()}|{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {"results": cached, "provider": "OpenStreetMap/Nominatim", "ok": True, "cached": True}

    client = await get_client()
    params = {
        "q": query,
        "format": "json",
        "addressdetails": 1,
        "limit": limit,
        "countrycodes": "in",
        "viewbox": INDIA_VIEWBOX,
        "bounded": 0,
    }
    try:
        response = await client.get(
            NOMINATIM_URL,
            params=params,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        raw = response.json()
    except Exception as exc:
        log.warning("nominatim search failed for %r: %s", query, exc)
        return {
            "results": [],
            "provider": "OpenStreetMap/Nominatim",
            "ok": False,
            "error": "Location search temporarily unavailable",
        }

    results: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        display = str(item.get("display_name") or "").strip()
        name, address = _split_display_name(display)
        results.append(
            {
                "name": name,
                "address": address,
                "lat": lat,
                "lon": lon,
                "provider": "OpenStreetMap/Nominatim",
            }
        )

    _cache_put(cache_key, results)
    return {"results": results, "provider": "OpenStreetMap/Nominatim", "ok": True, "cached": False}

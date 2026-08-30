"""``/healthz`` and ``/freshness``.

``/freshness`` is not a monitoring afterthought — it goes on a demo screen. The
argument it makes to a judge is that ORCA knows the provenance and age of
everything it says, and can show you the table.
"""

from __future__ import annotations

import platform
import sys
import time

from fastapi import APIRouter, Request, Response, status

from orca.config import Settings, get_settings
from orca.obs.health import registry
from orca.provenance import utcnow

router = APIRouter(tags=["ops"])

_STARTED_AT = time.monotonic()

#: An LLM provider is the one external dependency the agent plane cannot work
#: around: with none configured, ORCA can still compute a deterministic verdict
#: but cannot answer a question. Anything else has a documented fallback.
_LLM_CAPABILITIES = ("groq", "gemini", "openrouter")


@router.get("/healthz", summary="Liveness, capability roster and infrastructure state")
async def healthz(request: Request, response: Response) -> dict[str, object]:
    settings: Settings = get_settings()
    caps = settings.capabilities()

    infra = getattr(request.app.state, "infra", {}) or {}
    sources = registry.summary()

    # `degraded` means "ORCA cannot do its job", and it drives a 503. An upstream
    # source being down is emphatically NOT that: the whole design says a Bhuvan
    # or IMD outage degrades one layer visibly and the answer still computes. So
    # source trouble is reported under `notes` and lives in /freshness, and only
    # a failure that actually stops ORCA serving turns this red.
    degraded: list[str] = []
    if not any(caps.get(name) for name in _LLM_CAPABILITIES):
        degraded.append("no LLM provider configured — the agent plane cannot answer")
    if infra.get("db") not in ("postgis", "sqlite"):
        degraded.append(f"no persistence driver selected: {infra.get('db')!r}")

    notes: list[str] = []
    if failing := sources.get("failing"):
        notes.append(f"{failing} upstream source(s) have never succeeded — see /freshness")
    if open_circuits := sources.get("circuit_open"):
        notes.append(f"{open_circuits} source(s) have an open circuit breaker")
    if infra.get("db") == "sqlite" and infra.get("db_fallback_reason"):
        notes.append(f"PostGIS was requested but unreachable: {infra['db_fallback_reason']}")

    state = "ok" if not degraded else "degraded"
    response.status_code = (
        status.HTTP_200_OK if state == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return {
        "status": state,
        "degraded": degraded,
        "notes": notes,
        "service": "orca",
        "version": request.app.version,
        "env": settings.orca_env,
        "time": utcnow(),
        "uptime_s": round(time.monotonic() - _STARTED_AT, 1),
        "runtime": {
            "python": sys.version.split()[0],
            "platform": f"{platform.system()} {platform.release()}",
        },
        "infrastructure": infra,
        "capabilities": caps,
        "sources": sources,
    }


@router.get("/freshness", summary="Per-source last-success timestamps and provenance")
async def freshness() -> dict[str, object]:
    rows = registry.detail()
    return {
        "generated_at": utcnow(),
        "summary": registry.summary(),
        "sources": rows,
        "legend": {
            "ok": "last attempt succeeded",
            "degraded": "succeeded before, currently failing",
            "failing": "has never succeeded in this process",
            "untried": "declared but not yet called",
            "circuit_open": "breaker tripped; calls short-circuit until it resets",
            "dormant": "credential or endpoint not available — adapter is written but idle",
        },
    }


@router.get("/config", summary="Effective configuration, secrets masked")
async def effective_config() -> dict[str, object]:
    """Every value that shapes ORCA's behaviour, with credentials fingerprinted.

    Exposed because "which key is it actually using?" is the first question in
    every integration bug, and the masked view makes answering it safe.
    """
    settings = get_settings()
    return {"generated_at": utcnow(), "settings": settings.redacted()}

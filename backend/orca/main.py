"""FastAPI application factory.

Three things happen here that matter later:

* **Infrastructure is probed at startup, not assumed.** PostGIS and Redis are
  optional accelerators; the lifespan tries each one, records what it got, and
  falls back to SQLite / in-process with a logged reason. ``app.state.infra``
  then tells ``/healthz`` the truth rather than a guess.
* **SSE is protected from buffering.** ``X-Accel-Buffering: no`` and an explicit
  gzip exclusion, because an agent trace that arrives in one lump at the end
  looks exactly like a fake, and it is the thing we most need to show live.
* **Secrets are masked before the first log line.** ``configure_logging`` runs
  before anything else touches a credential.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from orca.api.routes import forecast as forecast_routes
from orca.api.routes import health as health_routes
from orca.config import DbDriver, Settings, get_settings, mask_dsn
from orca.obs.logging import configure_logging
from orca.provenance import utcnow

log = logging.getLogger("orca")

__version__ = "0.1.0"

#: Paths that stream. Buffering middleware must leave these alone.
STREAMING_PATH_PREFIXES = ("/agent/stream", "/alerts/stream")


async def _probe_postgis(settings: Settings) -> tuple[str, str | None]:
    """Return ``(driver, reason_if_degraded)``.

    Under ``auto`` a Postgres failure is not an error: it is a documented
    fallback, and the reason is surfaced rather than swallowed.
    """
    dsn = settings.effective_database_url
    if settings.orca_db_driver is DbDriver.SQLITE or dsn is None:
        return "sqlite", None

    try:
        import asyncpg
    except ModuleNotFoundError:
        reason = "asyncpg not installed (pip install -e 'backend[postgis]')"
        if settings.orca_db_driver is DbDriver.POSTGIS:
            raise RuntimeError(f"ORCA_DB_DRIVER=postgis but {reason}") from None
        return "sqlite", reason

    try:
        conn = await asyncpg.connect(dsn, ssl="prefer", timeout=10)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        if settings.orca_db_driver is DbDriver.POSTGIS:
            raise RuntimeError(
                f"ORCA_DB_DRIVER=postgis but {mask_dsn(dsn)} is unreachable — {reason}"
            ) from exc
        log.warning("PostGIS unreachable at %s, falling back to SQLite: %s", mask_dsn(dsn), reason)
        return "sqlite", reason

    try:
        has_postgis = await conn.fetchval(
            "select exists (select 1 from pg_extension where extname = 'postgis')"
        )
        if not has_postgis:
            reason = "connected, but the postgis extension is not installed"
            if settings.orca_db_driver is DbDriver.POSTGIS:
                raise RuntimeError(reason)
            log.warning("Falling back to SQLite: %s", reason)
            return "sqlite", reason
        version = await conn.fetchval("select postgis_version()")
        log.info("PostGIS %s at %s", str(version).split()[0], mask_dsn(dsn))
        return "postgis", None
    finally:
        await conn.close()


async def _probe_redis(settings: Settings) -> tuple[bool, str | None]:
    if not settings.has_redis:
        return False, "REDIS_URL not set — using the in-process TTL cache and APScheduler"
    assert settings.redis_url is not None
    dsn = settings.redis_url.get_secret_value()
    try:
        import redis.asyncio as aioredis
    except ModuleNotFoundError:
        return False, "redis package not installed"
    client = aioredis.from_url(dsn, socket_connect_timeout=5, socket_timeout=5)
    try:
        await client.ping()
        log.info("Redis reachable at %s", mask_dsn(dsn))
        return True, None
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        log.warning(
            "Redis unreachable at %s, using in-process fallbacks: %s", mask_dsn(dsn), reason
        )
        return False, reason
    finally:
        await client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()

    for directory in (settings.raster_dir, settings.static_dir, settings.checkpoint_file.parent):
        directory.mkdir(parents=True, exist_ok=True)

    driver, db_reason = await _probe_postgis(settings)
    redis_ok, redis_reason = await _probe_redis(settings)

    app.state.infra = {
        "db": driver,
        "db_fallback_reason": db_reason,
        "cache": "redis" if redis_ok else "memory",
        "queue": "arq" if redis_ok else "apscheduler",
        "redis_reason": redis_reason,
        "geofence_index": "postgis" if driver == "postgis" else "shapely-strtree",
        "started_at": utcnow(),
    }

    caps = settings.capabilities()
    log.info(
        "ORCA %s starting — env=%s db=%s cache=%s queue=%s",
        __version__,
        settings.orca_env,
        driver,
        app.state.infra["cache"],
        app.state.infra["queue"],
    )
    log.info(
        "capabilities available: %s",
        ", ".join(sorted(k for k, v in caps.items() if v)) or "none",
    )
    dormant = sorted(k for k, v in caps.items() if not v)
    if dormant:
        log.info(
            "capabilities dormant (adapters written, credentials absent): %s", ", ".join(dormant)
        )

    try:
        yield
    finally:
        from orca.sources.base import close_client

        await close_client()
        log.info("ORCA shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="ORCA",
        version=__version__,
        summary="Marine EcOsystem Reasoning with Collaborative Agents",
        description=(
            "Agentic marine decision support for the Indian EEZ.\n\n"
            "**ORCA supplements, never replaces, official IMD and INCOIS bulletins.** "
            "Safety verdicts come from a deterministic, versioned rule engine — never "
            "from a language model. Every value carries a provenance state and an age."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Orca-Request-Id", "X-Orca-Provenance"],
    )

    @app.middleware("http")
    async def _streaming_and_provenance_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.startswith(STREAMING_PATH_PREFIXES):
            # nginx and most PaaS proxies buffer by default, which would batch an
            # SSE trace into a single frame at the end and destroy the one thing
            # the trace panel exists to show.
            response.headers["X-Accel-Buffering"] = "no"
            response.headers["Cache-Control"] = "no-cache, no-transform"
            response.headers["Connection"] = "keep-alive"
        return response

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log with a traceback (the scrubber has already been installed, so no
        # credential can ride out in the message), but never return internals.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": "ORCA hit an internal error. The failure is logged with a traceback.",
                "path": request.url.path,
            },
        )

    app.include_router(health_routes.router)
    app.include_router(forecast_routes.router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "orca",
            "version": __version__,
            "docs": "/docs",
            "health": "/healthz",
            "freshness": "/freshness",
        }

    return app


app = create_app()

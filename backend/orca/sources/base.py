"""The one HTTP client every upstream adapter uses.

Adapters must not create their own ``httpx.AsyncClient``, because five things
have to be true of every outbound call and none of them are interesting enough
to reimplement per source:

* a **hard timeout**, so a flaky ``.gov.in`` TLS handshake cannot hang a request;
* **bounded retries** with jittered backoff, only on errors worth retrying;
* a **circuit breaker**, so a source that is down stops being asked;
* **conditional requests** (ETag / If-Modified-Since), so we are a polite client
  and a 304 is served from cache for free;
* **health reporting**, so ``/freshness`` reflects reality without adapters
  remembering to log.

The other rule: :meth:`Source.fetch` never raises for an upstream failure. It
returns a :class:`Fetched` whose ``ok`` is False, and the adapter turns that into
``Evidence.unavailable``. A dead source must degrade one layer of the answer, not
the request.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, ClassVar, Self

import httpx

from orca.config import get_settings
from orca.obs.health import registry
from orca.provenance import Provenance, Provider, utcnow
from orca.sources.tls import ssl_context

log = logging.getLogger(__name__)

#: Status codes worth retrying. A 404 or a 401 will not fix itself.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})

_TRANSIENT_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
)


@dataclass(slots=True)
class Fetched:
    """The outcome of one upstream call. Never an exception."""

    ok: bool
    source: str
    url: str
    status: int | None = None
    text: str | None = None
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    latency_ms: float | None = None
    from_cache: bool = False
    attempts: int = 1

    @property
    def provenance(self) -> Provenance:
        """A 304-backed or cache-served body is real data that is not this
        second's — that is exactly what CACHED means."""
        if not self.ok:
            return Provenance.UNAVAILABLE
        return Provenance.CACHED if self.from_cache else Provenance.LIVE

    def json(self) -> Any:
        if self.text is None:
            raise ValueError(f"{self.source}: no body to parse ({self.error})")
        import json

        return json.loads(self.text)


@dataclass(slots=True)
class _CacheEntry:
    etag: str | None
    last_modified: str | None
    text: str
    content: bytes
    headers: dict[str, str]
    stored_at: Any


class _ConditionalCache:
    """A small in-process store keyed by URL, holding the validators needed to
    make the next request conditional.

    Deliberately not the response cache — that is a separate concern with a TTL
    policy per variable. This exists so that re-fetching an unchanged ERDDAP grid
    or CAP feed costs a 304 rather than a payload, which is what keeps us a
    well-behaved client of somebody else's free service.
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._max = max_entries

    def validators(self, url: str) -> dict[str, str]:
        entry = self._entries.get(url)
        if entry is None:
            return {}
        headers = {}
        if entry.etag:
            headers["If-None-Match"] = entry.etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        return headers

    def get(self, url: str) -> _CacheEntry | None:
        return self._entries.get(url)

    def put(self, url: str, response: httpx.Response) -> None:
        etag = response.headers.get("etag")
        last_modified = response.headers.get("last-modified")
        if not etag and not last_modified:
            return  # nothing to revalidate with, so nothing worth storing
        if len(self._entries) >= self._max and url not in self._entries:
            self._entries.pop(next(iter(self._entries)))
        self._entries[url] = _CacheEntry(
            etag=etag,
            last_modified=last_modified,
            text=response.text,
            content=response.content,
            headers=dict(response.headers),
            stored_at=utcnow(),
        )

    def clear(self) -> None:
        self._entries.clear()


_cache = _ConditionalCache()

#: One connection pool for the whole process. Created lazily so importing an
#: adapter does not open sockets, and closed by the app's lifespan.
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                settings = get_settings()
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(settings.http_timeout_s, connect=6.0),
                    # Verification stays ON; the bundle just completes the chain
                    # for .gov.in hosts that omit their intermediate CA.
                    verify=ssl_context(),
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=24, max_keepalive_connections=8),
                    headers={
                        "User-Agent": (
                            "ORCA/0.1 (Smart India Hackathon 2026; marine decision support)"
                        ),
                        "Accept-Encoding": "gzip, deflate",
                    },
                )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class Source:
    """Base class for one upstream provider.

    Subclasses set the class attributes and call :meth:`fetch`. Declaring the
    source in ``__init_subclass__`` means ``/freshness`` lists every adapter that
    exists, including ones that have never been called — which is the row you
    want when an ingest job silently never fired.
    """

    #: Stable identifier, e.g. ``open_meteo.marine``. Appears in /freshness.
    name: ClassVar[str] = ""
    provider: ClassVar[Provider | str] = Provider.ORCA
    #: Variables this source can supply. Drives the freshness screen.
    variables: ClassVar[tuple[str, ...]] = ()
    #: Capability flag on Settings that must be true, e.g. ``"has_imd"``.
    #: ``None`` means zero-auth.
    requires: ClassVar[str | None] = None
    #: Documentation link, carried onto Evidence so a citation has somewhere to go.
    docs_url: ClassVar[str | None] = None
    #: Some hosts (the .gov.in estate, notably) need a shorter leash than the
    #: global default so one bad TLS chain cannot dominate a request's latency.
    timeout_s: ClassVar[float | None] = None

    _registered: ClassVar[bool] = False

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if not cls.name:
            return
        settings = get_settings()
        dormant = None
        if cls.requires and not getattr(settings, cls.requires, False):
            dormant = f"{cls.requires} is false — credential not supplied"
        registry.declare(
            cls.name,
            provider=cls.provider,
            variables=list(cls.variables),
            requires=cls.requires,
            dormant_reason=dormant,
        )
        cls._registered = True

    @classmethod
    def is_available(cls) -> bool:
        """Whether this adapter has what it needs to be called at all."""
        if cls.requires is None:
            return True
        return bool(getattr(get_settings(), cls.requires, False))

    # ------------------------------------------------------------------ fetch
    async def fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        json_body: Any = None,
        conditional: bool = True,
        retries: int | None = None,
        timeout_s: float | None = None,
        expect_binary: bool = False,
    ) -> Fetched:
        """One upstream call, with every guarantee in the module docstring.

        Returns a :class:`Fetched`; never raises for an upstream problem.
        """
        settings = get_settings()
        name = self.name or type(self).__name__

        if not self.is_available():
            return Fetched(
                ok=False,
                source=name,
                url=url,
                error=f"dormant: {self.requires} is not configured",
            )

        if registry.is_circuit_open(name):
            # The point of a breaker is to stop paying the timeout. Failing
            # immediately is the behaviour, not a shortcut.
            return Fetched(
                ok=False, source=name, url=url, error="circuit breaker open — source is down"
            )

        max_attempts = (retries if retries is not None else settings.http_retries) + 1
        effective_timeout = timeout_s or self.timeout_s or settings.http_timeout_s
        client = await get_client()

        request_headers = dict(headers or {})
        if conditional and method == "GET":
            request_headers.update(_cache.validators(url))

        last_error = "unknown"
        for attempt in range(1, max_attempts + 1):
            started = asyncio.get_running_loop().time()
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=request_headers,
                    json=json_body,
                    timeout=effective_timeout,
                )
            except _TRANSIENT_EXC as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < max_attempts:
                    await self._backoff(attempt)
                    continue
                break
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                break  # not transient — retrying will not help

            latency_ms = (asyncio.get_running_loop().time() - started) * 1000

            if response.status_code == 304:
                entry = _cache.get(url)
                if entry is not None:
                    registry.record_success(
                        name, provenance=Provenance.CACHED, latency_ms=latency_ms
                    )
                    return Fetched(
                        ok=True,
                        source=name,
                        url=url,
                        status=304,
                        text=entry.text,
                        content=entry.content,
                        headers=entry.headers,
                        latency_ms=latency_ms,
                        from_cache=True,
                        attempts=attempt,
                    )
                # A 304 with nothing cached means our validator outlived the
                # body. Drop the validators and ask again unconditionally.
                request_headers.pop("If-None-Match", None)
                request_headers.pop("If-Modified-Since", None)
                last_error = "304 with no cached body"
                if attempt < max_attempts:
                    continue
                break

            if response.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}"
                if attempt < max_attempts:
                    await self._backoff(attempt, response=response)
                    continue
                break

            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                break

            if conditional and method == "GET":
                _cache.put(url, response)
            registry.record_success(name, provenance=Provenance.LIVE, latency_ms=latency_ms)
            return Fetched(
                ok=True,
                source=name,
                url=str(response.url),
                status=response.status_code,
                text=None if expect_binary else response.text,
                content=response.content,
                headers=dict(response.headers),
                latency_ms=latency_ms,
                attempts=attempt,
            )

        # Every attempt failed.
        open_until = None
        settings = get_settings()
        entry_failures = 1
        snapshot = {row.source: row for row in registry.snapshot()}
        if name in snapshot:
            entry_failures = snapshot[name].consecutive_failures + 1
        if entry_failures >= settings.circuit_breaker_failures:
            open_until = utcnow() + timedelta(seconds=settings.circuit_breaker_reset_s)
        registry.record_failure(name, last_error, open_circuit_until=open_until)
        log.warning("%s failed after %d attempt(s): %s", name, max_attempts, last_error)
        return Fetched(ok=False, source=name, url=url, error=last_error, attempts=attempt)

    @staticmethod
    async def _backoff(attempt: int, *, response: httpx.Response | None = None) -> None:
        """Exponential with full jitter, and ``Retry-After`` wins when present —
        Open-Meteo and ERDDAP both send it, and ignoring it is how a free tier
        gets revoked."""
        if response is not None and (retry_after := response.headers.get("retry-after")):
            try:
                await asyncio.sleep(min(float(retry_after), 10.0))
                return
            except ValueError:
                pass
        delay = min(0.4 * (2 ** (attempt - 1)), 4.0)
        # Jitter, not cryptography.
        await asyncio.sleep(random.uniform(0, delay))

    # ------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def clear_http_cache() -> None:
    """For tests, and for the demo's 'force refresh' control."""
    _cache.clear()

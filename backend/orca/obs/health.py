"""The source registry behind ``GET /freshness``.

Every upstream adapter registers itself at import time and reports the outcome of
each attempt here. That gives us three things for one small amount of
bookkeeping:

* a demo screen showing, per source, when it last succeeded and how old that is;
* the circuit breaker's state, shared rather than re-implemented per adapter;
* an honest ``/healthz``, which reports *degraded* when a source is down instead
  of a green tick that means only "the process is running".

Declaration is separated from outcome on purpose: a source that has never been
called still appears in ``/freshness`` as "never succeeded", which is exactly the
row you want to see when an ingest job silently never fired.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from orca.provenance import Provenance, Provider, SourceHealth, utcnow


@dataclass
class _Entry:
    source: str
    provider: Provider | str
    variables: list[str]
    requires: str | None  # capability flag name, e.g. "has_imd"; None = zero-auth
    dormant_reason: str | None = None
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    successes: int = 0
    failures: int = 0
    circuit_open_until: datetime | None = None
    last_provenance: Provenance = Provenance.UNAVAILABLE
    latencies_ms: list[float] = field(default_factory=list)


class SourceRegistry:
    """Thread-safe, in-process. Deliberately not persisted: it describes *this*
    process's contact with the outside world, and a restart genuinely has no
    knowledge of it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}

    # ------------------------------------------------------------- declaring
    def declare(
        self,
        source: str,
        *,
        provider: Provider | str,
        variables: list[str],
        requires: str | None = None,
        dormant_reason: str | None = None,
    ) -> None:
        """Announce a source exists. Safe to call repeatedly (module reimport)."""
        with self._lock:
            existing = self._entries.get(source)
            if existing is None:
                self._entries[source] = _Entry(
                    source=source,
                    provider=provider,
                    variables=variables,
                    requires=requires,
                    dormant_reason=dormant_reason,
                )
            else:
                existing.provider = provider
                existing.variables = variables
                existing.requires = requires
                existing.dormant_reason = dormant_reason

    # ------------------------------------------------------------ reporting
    def record_success(
        self,
        source: str,
        *,
        provenance: Provenance = Provenance.LIVE,
        latency_ms: float | None = None,
    ) -> None:
        with self._lock:
            e = self._entries.get(source) or self._auto(source)
            now = utcnow()
            e.last_attempt = now
            e.last_success = now
            e.last_error = None
            e.consecutive_failures = 0
            e.circuit_open_until = None
            e.successes += 1
            e.last_provenance = provenance
            if latency_ms is not None:
                e.latencies_ms.append(latency_ms)
                del e.latencies_ms[:-50]  # a rolling window, not a growing list

    def record_failure(
        self, source: str, error: str, *, open_circuit_until: datetime | None = None
    ) -> None:
        with self._lock:
            e = self._entries.get(source) or self._auto(source)
            e.last_attempt = utcnow()
            e.last_error = error[:400]
            e.consecutive_failures += 1
            e.failures += 1
            if open_circuit_until is not None:
                e.circuit_open_until = open_circuit_until

    def is_circuit_open(self, source: str) -> bool:
        with self._lock:
            e = self._entries.get(source)
            if e is None or e.circuit_open_until is None:
                return False
            if utcnow() >= e.circuit_open_until:
                e.circuit_open_until = None
                return False
            return True

    def _auto(self, source: str) -> _Entry:
        """A source that reports before declaring still gets a row rather than an
        exception — losing the observability is worse than a vague label."""
        entry = _Entry(source=source, provider=Provider.ORCA, variables=[], requires=None)
        self._entries[source] = entry
        return entry

    # ------------------------------------------------------------- reading
    def snapshot(self) -> list[SourceHealth]:
        with self._lock:
            return [
                SourceHealth(
                    source=e.source,
                    provider=e.provider,
                    last_success=e.last_success,
                    last_attempt=e.last_attempt,
                    last_error=e.last_error,
                    consecutive_failures=e.consecutive_failures,
                    circuit_open=e.circuit_open_until is not None
                    and utcnow() < e.circuit_open_until,
                    provenance=e.last_provenance,
                    variables=list(e.variables),
                )
                for e in sorted(self._entries.values(), key=lambda x: x.source)
            ]

    def detail(self) -> list[dict[str, object]]:
        """Richer rows for the freshness screen: counts, latency, dormancy."""
        rows: list[dict[str, object]] = []
        with self._lock:
            for e in sorted(self._entries.values(), key=lambda x: x.source):
                lat = e.latencies_ms
                rows.append(
                    {
                        "source": e.source,
                        "provider": str(e.provider),
                        "variables": list(e.variables),
                        "status": self._status(e),
                        "dormant_reason": e.dormant_reason,
                        "last_success": e.last_success,
                        "last_attempt": e.last_attempt,
                        "age_hours": (
                            None
                            if e.last_success is None
                            else round((utcnow() - e.last_success).total_seconds() / 3600, 3)
                        ),
                        "last_error": e.last_error,
                        "successes": e.successes,
                        "failures": e.failures,
                        "consecutive_failures": e.consecutive_failures,
                        "circuit_open": e.circuit_open_until is not None
                        and utcnow() < e.circuit_open_until,
                        "provenance": e.last_provenance.value,
                        "median_latency_ms": (
                            round(sorted(lat)[len(lat) // 2], 1) if lat else None
                        ),
                    }
                )
        return rows

    @staticmethod
    def _status(e: _Entry) -> str:
        if e.dormant_reason is not None:
            return "dormant"
        if e.circuit_open_until is not None and utcnow() < e.circuit_open_until:
            return "circuit_open"
        if e.last_attempt is None:
            return "untried"
        if e.last_success is None:
            return "failing"
        if e.consecutive_failures > 0:
            return "degraded"
        return "ok"

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for e in self._entries.values():
                key = self._status(e)
                counts[key] = counts.get(key, 0) + 1
        return counts


#: One registry per process. Adapters import this directly.
registry = SourceRegistry()

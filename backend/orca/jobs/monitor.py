"""The trip monitor: the one part of ORCA that speaks without being asked.

Everything else in this system answers a question. This polls the positions
someone has said they care about and raises an alert when the answer *changes*
for the worse — which is the difference between a decision-support tool and a
dashboard you have to remember to look at.

## Alerts fire on transitions, never on state

This is the whole design, and getting it wrong is the obvious failure mode. A job
that emits "NO-GO at Kasimedu" every five minutes has produced a feed, and a feed
is something people learn to ignore — which means the one alert that mattered
scrolls past with the ninety that did not. So an alert is raised only when
something crossed a line since the last poll:

* the verdict got worse (GO → CAUTION → NO-GO), or became UNVERIFIABLE;
* a new hard veto appeared that was not there before;
* the risk index fell by more than a set margin while staying in the same band;
* a geofence transition happened (approaching, or crossed);
* data that was fresh went stale.

Every alert carries the previous value as well as the current one, because
"NO-GO" on its own is not actionable and "CAUTION → NO-GO, wave height rose from
1.3 m to 1.9 m" is.

## Improvement is reported too, but quietly

A verdict getting *better* is also a transition and also worth knowing — a
fisherman who was told NO-GO at 04:00 wants to hear that 07:00 is GO. Those are
raised at `info` severity so they cannot outrank a deterioration in any sane
sorting.

## What it does not do

It does not send anything anywhere. No SMS, no push, no email — ORCA has no
authority to contact anyone, and a prototype that quietly acquires a notification
channel is a prototype that can spam a real fisherman. Alerts land in an
in-process ring buffer that the UI reads over SSE, and that is the whole delivery
mechanism.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from orca.provenance import utcnow

log = logging.getLogger(__name__)

MONITOR_VERSION = "orca-monitor-2026.08"

#: How often each watch is re-assessed. Two minutes is a compromise: the upstream
#: models update hourly so anything faster learns nothing new, but a demo needs
#: an unprompted alert to arrive inside a few minutes rather than inside an hour.
POLL_SECONDS = 120.0

#: Verdict ordering, worst last. Used to decide whether a change is a
#: deterioration or an improvement — string comparison would put CAUTION after
#: NO-GO alphabetically and invert every alert in the system.
VERDICT_RANK: dict[str, int] = {"GO": 0, "CAUTION": 1, "UNVERIFIABLE": 2, "NO-GO": 3}

#: An index drop this large is worth reporting even inside the same verdict band.
#: 12 points is roughly a third of a band, which is enough to mean something and
#: large enough not to fire on model noise between two hourly cycles.
INDEX_DROP_ALERT = 12.0

Severity = Literal["info", "advisory", "warning", "critical"]

#: Verdict -> severity for a deterioration INTO that verdict.
_SEVERITY: dict[str, Severity] = {
    "GO": "info",
    "CAUTION": "advisory",
    "UNVERIFIABLE": "warning",
    "NO-GO": "critical",
}


@dataclass(slots=True)
class Watch:
    """A position someone has asked to be told about."""

    id: str
    lat: float
    lon: float
    loa_m: float
    label: str | None = None
    heading_deg: float | None = None
    speed_kn: float | None = None
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    #: Last observed state, for the transition comparison.
    last_verdict: str | None = None
    last_index: float | None = None
    last_vetoes: tuple[str, ...] = ()
    last_stale: bool = False
    last_fence_states: dict[str, str] = field(default_factory=dict)
    last_checked_at: str | None = None
    checks: int = 0
    errors: int = 0

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lat": self.lat,
            "lon": self.lon,
            "loa_m": self.loa_m,
            "label": self.label,
            "created_at": self.created_at,
            "last_checked_at": self.last_checked_at,
            "checks": self.checks,
            "errors": self.errors,
            "verdict": self.last_verdict,
            "index": self.last_index,
        }


@dataclass(slots=True)
class Alert:
    id: str
    at: str
    watch_id: str
    severity: Severity
    kind: str
    headline: str
    detail: str
    lat: float
    lon: float
    label: str | None
    #: What changed, as before/after. The reason this exists at all: a verdict
    #: with no previous value is a status line, not an alert.
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    acknowledged: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "watch_id": self.watch_id,
            "severity": self.severity,
            "kind": self.kind,
            "headline": self.headline,
            "detail": self.detail,
            "lat": self.lat,
            "lon": self.lon,
            "label": self.label,
            "before": self.before,
            "after": self.after,
            "acknowledged": self.acknowledged,
        }


class Monitor:
    """Owns the watches, the alert ring buffer and the poll loop."""

    def __init__(self, *, capacity: int = 200) -> None:
        self.watches: dict[str, Watch] = {}
        self.alerts: deque[Alert] = deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue[Alert]] = set()
        self._task: asyncio.Task[None] | None = None
        #: The event loop `_task` belongs to. See `start`.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started_at: str | None = None
        self.polls = 0
        self.last_error: str | None = None

    # ---------------------------------------------------------------- watches

    def watch(
        self,
        *,
        lat: float,
        lon: float,
        loa_m: float = 8.2,
        label: str | None = None,
        heading_deg: float | None = None,
        speed_kn: float | None = None,
    ) -> Watch:
        entry = Watch(
            id=uuid.uuid4().hex[:10],
            lat=lat,
            lon=lon,
            loa_m=loa_m,
            label=label,
            heading_deg=heading_deg,
            speed_kn=speed_kn,
        )
        self.watches[entry.id] = entry
        log.info("monitor: watching %s (%.3f, %.3f)", label or entry.id, lat, lon)
        return entry

    def unwatch(self, watch_id: str) -> bool:
        return self.watches.pop(watch_id, None) is not None

    # ---------------------------------------------------------------- alerts

    def _raise(self, alert: Alert) -> None:
        self.alerts.append(alert)
        for queue in list(self._subscribers):
            # Never block the poll loop on a slow reader: a client that has
            # stopped draining loses alerts rather than stalling the monitor for
            # everyone else.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(alert)

    def acknowledge(self, alert_id: str) -> bool:
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                return True
        return False

    async def subscribe(self) -> AsyncIterator[Alert]:
        queue: asyncio.Queue[Alert] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    # ------------------------------------------------------------------- poll

    async def check(self, entry: Watch) -> list[Alert]:
        """Re-assess one watch and return whatever transitions it produced."""
        from orca.services.risk_engine import assess_from_evidence
        from orca.sources.open_meteo import conditions_at

        raised: list[Alert] = []
        now = utcnow().isoformat()

        try:
            evidence = await conditions_at(entry.lat, entry.lon)
            risk = assess_from_evidence(evidence, loa_m=entry.loa_m)
        except Exception as exc:  # noqa: BLE001 — a watch must survive a bad poll
            entry.errors += 1
            entry.last_checked_at = now
            log.warning("monitor: watch %s failed: %s", entry.id, exc)
            return raised

        entry.checks += 1
        entry.last_checked_at = now

        verdict = risk.verdict
        index = float(risk.index)
        vetoes = tuple(risk.vetoes)
        stale = any(e.freshness.is_stale for e in evidence.values() if e.value is not None)

        previous_verdict = entry.last_verdict
        previous_index = entry.last_index

        def make(
            *, severity: Severity, kind: str, headline: str, detail: str, before: Any, after: Any
        ) -> Alert:
            return Alert(
                id=uuid.uuid4().hex[:10],
                at=now,
                watch_id=entry.id,
                severity=severity,
                kind=kind,
                headline=headline,
                detail=detail,
                lat=entry.lat,
                lon=entry.lon,
                label=entry.label,
                before=before,
                after=after,
            )

        # --- the first poll establishes a baseline and raises nothing ---------
        #
        # Alerting on the first observation would mean every new watch instantly
        # produces an alert for a condition that was already true when the user
        # asked to be watched. They just looked at it.
        if previous_verdict is None:
            entry.last_verdict = verdict
            entry.last_index = index
            entry.last_vetoes = vetoes
            entry.last_stale = stale
            return raised

        # --- verdict transition ----------------------------------------------
        if verdict != previous_verdict:
            worse = VERDICT_RANK.get(verdict, 2) > VERDICT_RANK.get(previous_verdict, 2)
            reason = risk.escalation_message or (
                "; ".join(vetoes) if vetoes else "the blended risk index moved between bands"
            )
            raised.append(
                make(
                    severity=_SEVERITY.get(verdict, "advisory") if worse else "info",
                    kind="verdict_worse" if worse else "verdict_better",
                    headline=(
                        f"{previous_verdict} → {verdict}"
                        + (f" at {entry.label}" if entry.label else "")
                    ),
                    detail=(
                        f"The safety assessment moved from {previous_verdict} ({previous_index:.1f}/100) "
                        f"to {verdict} ({index:.1f}/100) for a {risk.boat_class_label}. {reason}"
                    ),
                    before={"verdict": previous_verdict, "index": previous_index},
                    after={"verdict": verdict, "index": index},
                )
            )

        # --- a new veto, even without a band change --------------------------
        new_vetoes = [v for v in vetoes if v not in entry.last_vetoes]
        if new_vetoes:
            raised.append(
                make(
                    severity="critical",
                    kind="new_veto",
                    headline=f"New hard limit exceeded{f' at {entry.label}' if entry.label else ''}",
                    detail=(
                        f"{len(new_vetoes)} limit(s) newly exceeded: {'; '.join(new_vetoes)}. "
                        "A hard veto overrides the blended index."
                    ),
                    before={"vetoes": list(entry.last_vetoes)},
                    after={"vetoes": list(vetoes)},
                )
            )

        # --- a material drop inside the same band ----------------------------
        if (
            verdict == previous_verdict
            and previous_index is not None
            and previous_index - index >= INDEX_DROP_ALERT
        ):
            raised.append(
                make(
                    severity="advisory",
                    kind="index_drop",
                    headline=(
                        f"Conditions worsening{f' at {entry.label}' if entry.label else ''} "
                        f"— {previous_index:.0f} → {index:.0f}"
                    ),
                    detail=(
                        f"Still {verdict}, but the index fell {previous_index - index:.0f} points. "
                        "The band has not changed; the margin inside it has."
                    ),
                    before={"index": previous_index},
                    after={"index": index},
                )
            )

        # --- freshness -------------------------------------------------------
        if stale and not entry.last_stale:
            raised.append(
                make(
                    severity="warning",
                    kind="went_stale",
                    headline="Data for this position went stale",
                    detail=(
                        "At least one input is now past its staleness limit. The verdict shown is "
                        "computed from data ORCA no longer considers current — treat it as "
                        "indicative and check an official bulletin."
                    ),
                    before={"stale": False},
                    after={"stale": True},
                )
            )

        # --- geofence transitions -------------------------------------------
        if entry.heading_deg is not None or entry.last_fence_states:
            raised.extend(await self._check_fences(entry, make))

        entry.last_verdict = verdict
        entry.last_index = index
        entry.last_vetoes = vetoes
        entry.last_stale = stale
        return raised

    async def _check_fences(self, entry: Watch, make: Any) -> list[Alert]:
        """Geofence transitions, using the same state machine the API exposes."""
        raised: list[Alert] = []
        try:
            from orca.services.geofence import index as fence_index

            # `check` takes lat/lon positionally and returns a list of
            # Proximity objects, not a report dict.
            proximities = fence_index.check(
                entry.lat,
                entry.lon,
                heading_deg=entry.heading_deg,
                speed_kn=entry.speed_kn,
                previous=entry.last_fence_states,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("monitor: fence check skipped for %s: %s", entry.id, exc)
            return raised

        states: dict[str, str] = {}
        for proximity in proximities:
            described = proximity.describe()
            # Keyed by the FENCE KEY, because that is what `check(previous=...)`
            # looks itself up by. Keying the stored states by display name would
            # make every poll look like a first observation and the state machine
            # would never see a transition at all.
            key = str(described["fence"])
            name = str(described["name"])
            state = str(described["state"])
            states[key] = state
            was = entry.last_fence_states.get(key)
            # Only transitions. A boat sitting inside an "approaching" band must
            # not generate an alert every two minutes for an hour.
            if was == state or state in {"clear", "far"}:
                continue
            ttc = described.get("time_to_cross_min")
            raised.append(
                make(
                    severity="critical" if state == "crossed" else "warning",
                    kind=f"fence_{state}",
                    headline=f"{name}: {state}",
                    detail=(
                        f"The boundary state changed from {was or 'clear'} to {state}."
                        + (f" Time to cross about {ttc:.0f} minutes." if ttc else "")
                    ),
                    before={"state": was},
                    after={"state": state, "time_to_cross_min": ttc},
                )
            )

        entry.last_fence_states = states
        return raised

    async def _loop_body(self) -> None:
        log.info("monitor: poll loop started, every %.0fs", POLL_SECONDS)
        while True:
            try:
                await asyncio.sleep(POLL_SECONDS)
                self.polls += 1
                for entry in list(self.watches.values()):
                    for alert in await self.check(entry):
                        log.info("monitor: ALERT %s %s", alert.severity, alert.headline)
                        self._raise(alert)
            except asyncio.CancelledError:
                log.info("monitor: poll loop stopped")
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("monitor: poll cycle failed")

    def start(self) -> None:
        """Start the poll loop on the CURRENT event loop.

        The loop is recorded because this object is a module-level singleton while
        the app that owns it is not: under ``uvicorn --reload``, and in any test
        that builds more than one app, the lifespan runs again on a brand new
        event loop. A task created on the old one cannot be awaited from the new
        one, and the failure is a bare ``RuntimeError`` about a future attached to
        a different loop.
        """
        running = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._loop is running:
            return
        if self._task is not None and self._loop is not running:
            # Its loop is gone, so cancelling is all that can be done and all
            # that is needed: a dead loop runs nothing.
            self._task.cancel()
        self._loop = running
        self._task = asyncio.create_task(self._loop_body())
        self._started_at = utcnow().isoformat()

    async def stop(self) -> None:
        task, loop = self._task, self._loop
        self._task = None
        self._loop = None
        if task is None:
            return
        task.cancel()
        # Only await it if we are on the loop that owns it. Awaiting across loops
        # raises rather than cleaning anything up.
        if loop is not None and loop is asyncio.get_running_loop():
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def status(self) -> dict[str, Any]:
        return {
            "monitor_version": MONITOR_VERSION,
            "running": self._task is not None and not self._task.done(),
            "started_at": self._started_at,
            "poll_seconds": POLL_SECONDS,
            "polls": self.polls,
            "watches": len(self.watches),
            "alerts_held": len(self.alerts),
            "subscribers": len(self._subscribers),
            "last_error": self.last_error,
            "policy": (
                "Alerts fire on TRANSITIONS, never on state. A job that emits the current verdict "
                "every cycle produces a feed, and a feed is something people learn to ignore — "
                "which means the one alert that mattered scrolls past with the ninety that did "
                "not. Every alert carries its previous value as well as its current one."
            ),
            "delivery": (
                "In-process ring buffer, read by the UI over SSE. ORCA sends no SMS, push or "
                "email: it has no authority to contact anyone, and a prototype that quietly "
                "acquires a notification channel is one that can spam a real fisherman."
            ),
        }


monitor = Monitor()

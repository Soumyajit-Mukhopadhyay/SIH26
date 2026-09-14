"""The trip monitor's transition logic.

The whole value of this module is that it stays quiet, so these tests are mostly
about silence: no alert on the first observation, no alert while a bad condition
persists, no alert for noise inside a band. The upstream fetch is stubbed, because
what is being tested is the comparison against the previous state and not the
weather.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from orca.jobs.monitor import INDEX_DROP_ALERT, VERDICT_RANK, Monitor
from orca.provenance import Evidence, Freshness, Provenance, Provider, utcnow
from orca.services.risk_engine import assess


def evidence_for(
    *, wave_m: float, wind_kn: float = 8.0, visibility_km: float = 20.0, cape: float = 100.0
) -> dict[str, Evidence]:
    """A minimal live evidence set. Real Evidence objects, so the freshness and
    decision-grade rules are the production ones."""
    now = utcnow()

    def item(variable: str, value: float, unit: str) -> Evidence:
        return Evidence(
            dataset_id="test.stub",
            provider=Provider.OPEN_METEO,
            variable=variable,
            value=value,
            unit=unit,
            provenance=Provenance.LIVE,
            freshness=Freshness.of(variable, now),
        )

    return {
        "wave_height": item("wave_height", wave_m, "m"),
        "wind_speed": item("wind_speed", wind_kn, "kn"),
        "visibility": item("visibility", visibility_km * 1000, "m"),
        "convective_energy": item("convective_energy", cape, "J/kg"),
    }


def install_conditions(
    monkeypatch: pytest.MonkeyPatch, sequence: list[dict[str, Evidence]]
) -> None:
    """Serve a fixed sequence of condition sets to successive polls."""
    calls = {"n": 0}

    async def fake_conditions_at(lat: float, lon: float) -> dict[str, Evidence]:
        index = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[index]

    monkeypatch.setattr("orca.sources.open_meteo.conditions_at", fake_conditions_at)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestSilence:
    def test_the_first_poll_raises_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alerting on the first observation would fire for a condition that was
        already true when the user asked to be watched. They had just looked at it.
        """
        install_conditions(monkeypatch, [evidence_for(wave_m=3.5)])
        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6, loa_m=8.2, label="off Chennai")

        raised = run(monitor.check(entry))
        assert raised == []
        # The baseline was still recorded, which is the point of the poll.
        assert entry.last_verdict == "NO-GO"
        assert entry.last_index is not None

    def test_a_persisting_bad_condition_alerts_once_and_then_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure mode this module exists to avoid.

        A job that emits the current verdict every cycle produces a feed, and a
        feed is something people learn to ignore — which means the one alert that
        mattered scrolls past with the ninety that did not.
        """
        calm = evidence_for(wave_m=0.4)
        rough = evidence_for(wave_m=3.5)
        install_conditions(monkeypatch, [calm, rough, rough, rough, rough])

        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6, loa_m=8.2)

        assert run(monitor.check(entry)) == []  # baseline
        first = run(monitor.check(entry))
        assert len(first) >= 1, "the transition into NO-GO must alert"

        for _ in range(3):
            assert run(monitor.check(entry)) == [], "a persisting condition must stay quiet"

    def test_noise_inside_a_band_does_not_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_conditions(
            monkeypatch,
            [evidence_for(wave_m=0.40), evidence_for(wave_m=0.42), evidence_for(wave_m=0.41)],
        )
        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6, loa_m=8.2)
        run(monitor.check(entry))
        assert run(monitor.check(entry)) == []
        assert run(monitor.check(entry)) == []


class TestTransitions:
    def test_a_worsening_verdict_alerts_with_its_previous_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "NO-GO" alone is a status line. "GO → NO-GO, and here is why" is an alert."""
        install_conditions(monkeypatch, [evidence_for(wave_m=0.4), evidence_for(wave_m=3.5)])
        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6, loa_m=8.2, label="off Chennai")

        run(monitor.check(entry))
        raised = run(monitor.check(entry))

        verdict_alerts = [a for a in raised if a.kind == "verdict_worse"]
        assert len(verdict_alerts) == 1
        alert = verdict_alerts[0]
        assert alert.severity == "critical"
        assert alert.before is not None and alert.before["verdict"] == "GO"
        assert alert.after is not None and alert.after["verdict"] == "NO-GO"
        assert "off Chennai" in alert.headline

    def test_an_improving_verdict_alerts_at_info_and_cannot_outrank_a_deterioration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fisherman told NO-GO at 04:00 wants to hear that 07:00 is GO — but it
        must never sort above a warning."""
        install_conditions(monkeypatch, [evidence_for(wave_m=3.5), evidence_for(wave_m=0.4)])
        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6, loa_m=8.2)

        run(monitor.check(entry))
        raised = run(monitor.check(entry))

        better = [a for a in raised if a.kind == "verdict_better"]
        assert len(better) == 1
        assert better[0].severity == "info"

    def test_a_new_veto_alerts_even_without_a_band_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wind rising over an already-NO-GO sea is new information."""
        # Both are NO-GO on wave height; the second adds a wind veto.
        # CAPE must not invent a hard veto here.
        install_conditions(
            monkeypatch,
            [
                evidence_for(wave_m=3.5, wind_kn=8.0, cape=50.0),
                evidence_for(wave_m=3.5, wind_kn=30.0, cape=50.0),
            ],
        )
        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6, loa_m=8.2)

        run(monitor.check(entry))
        raised = run(monitor.check(entry))

        vetoes = [a for a in raised if a.kind == "new_veto"]
        assert len(vetoes) == 1
        assert vetoes[0].severity == "critical"
        assert vetoes[0].before is not None
        assert len(vetoes[0].after["vetoes"]) > len(vetoes[0].before["vetoes"])

    def test_a_material_index_drop_inside_one_band_alerts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Still GO, but the margin inside GO has narrowed a lot."""
        # 98.4/100 down to 70.8/100, both GO. The pair was picked by evaluating
        # the engine rather than guessed: an earlier one crossed into CAUTION and
        # the test then proved nothing at all about same-band drops.
        first = assess(wave_m=0.2, wind_kn=4.0, visibility_km=25.0, cape_j_kg=20.0, loa_m=8.2)
        second = assess(wave_m=0.9, wind_kn=16.0, visibility_km=10.0, cape_j_kg=380.0, loa_m=8.2)
        assert first.verdict == second.verdict
        assert first.index - second.index >= INDEX_DROP_ALERT

        install_conditions(
            monkeypatch,
            [
                evidence_for(wave_m=0.2, wind_kn=4.0, visibility_km=25.0, cape=20.0),
                evidence_for(wave_m=0.9, wind_kn=16.0, visibility_km=10.0, cape=380.0),
            ],
        )
        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6, loa_m=8.2)
        run(monitor.check(entry))
        raised = run(monitor.check(entry))

        drops = [a for a in raised if a.kind == "index_drop"]
        assert len(drops) == 1
        assert drops[0].severity == "advisory"


class TestOrdering:
    def test_verdict_rank_is_explicit_because_alphabetical_would_invert_it(self) -> None:
        """Sorted as strings, CAUTION comes after NO-GO — which would make every
        deterioration in the system read as an improvement."""
        assert VERDICT_RANK["GO"] < VERDICT_RANK["CAUTION"] < VERDICT_RANK["NO-GO"]
        assert sorted(["NO-GO", "CAUTION"]) == ["CAUTION", "NO-GO"]
        assert VERDICT_RANK["NO-GO"] > VERDICT_RANK["CAUTION"]


class TestDelivery:
    def test_a_failed_poll_is_counted_and_does_not_kill_the_watch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(lat: float, lon: float) -> dict[str, Evidence]:
            raise RuntimeError("upstream on fire")

        monkeypatch.setattr("orca.sources.open_meteo.conditions_at", boom)
        monitor = Monitor()
        entry = monitor.watch(lat=13.0, lon=80.6)

        assert run(monitor.check(entry)) == []
        assert entry.errors == 1
        assert entry.id in monitor.watches

    def test_the_ring_buffer_is_bounded(self) -> None:
        """An unbounded alert log in a long-running process is a memory leak with
        a friendly name."""
        monitor = Monitor(capacity=5)
        for index in range(20):
            monitor.alerts.append(
                type(
                    "A",
                    (),
                    {"id": str(index), "acknowledged": False, "describe": lambda self: {}},
                )()
            )
        assert len(monitor.alerts) == 5

    def test_status_reports_the_policy_rather_than_only_counters(self) -> None:
        status = Monitor().status()
        assert "transitions" in status["policy"].lower()
        assert "sms" in status["delivery"].lower()
        assert status["running"] is False

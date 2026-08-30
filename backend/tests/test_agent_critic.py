"""The critic. The component that stops a language model softening a NO-GO.

Two failure directions, and both are real:

* **False negative** — a draft that hedges a hard veto gets through. That is the
  safety failure the whole architecture exists to prevent.
* **False positive** — a correct draft gets bounced. Less dangerous but not
  harmless: in testing, a substring check on the veto figure rejected a *correct*
  draft three times (the veto rounded 72.7% to "73%", the model quoted 72.7%),
  burning three LLM rounds and forcing a spurious escalation onto a good answer.

The tests below pin both directions.
"""

from __future__ import annotations

import pytest

from orca.agents.graph import _numbers, _quotes_figure, critic


def _risk(
    verdict: str = "NO-GO",
    vetoes: list[str] | None = None,
    index: float = 25.7,
) -> dict[str, object]:
    return {
        "verdict": verdict,
        "index": index,
        "vetoes": vetoes
        if vetoes is not None
        else ["Hs 2.64 m is at or over the 1.5 m limit for your 8.2 m boat"],
        "what_would_change_it": [],
    }


def _state(draft: str, risk: dict[str, object] | None = None, rounds: int = 0) -> dict[str, object]:
    return {
        "draft": draft,
        "risk": risk if risk is not None else _risk(),
        "critic_rounds": rounds,
        "tool_results": [{"tool": "assess_risk", "ok": True, "summary": "x", "error": None}],
    }


class TestFigureMatching:
    def test_extracts_the_measured_value(self):
        assert _numbers("high lightning probability (72.7% CAPE-derived)") == [72.7]

    @pytest.mark.parametrize("quoted", ["72.7", "73", "72.8"])
    def test_rounding_of_the_same_figure_is_accepted(self, quoted):
        # The regression: the veto says 72.7, the draft may reasonably write 73.
        assert _quotes_figure(
            f"Lightning probability {quoted} % is over the 60 % limit.",
            "high lightning probability (72.7% CAPE-derived)",
        )

    @pytest.mark.parametrize("quoted", ["40", "12.5", "100"])
    def test_a_genuinely_different_figure_is_rejected(self, quoted):
        assert not _quotes_figure(
            f"Lightning probability {quoted} %.",
            "high lightning probability (72.7% CAPE-derived)",
        )

    def test_it_checks_the_measured_value_not_the_limit(self):
        # The veto's first number is the measurement; quoting only the limit is
        # not quoting the finding.
        veto = "Hs 2.64 m is at or over the 1.5 m limit for your 8.2 m boat"
        assert not _quotes_figure("The limit for your class is 1.5 m.", veto)
        assert _quotes_figure("Waves are running at 2.64 m.", veto)

    def test_a_veto_with_no_figure_is_not_held_against_the_draft(self):
        assert _quotes_figure("anything", "conditions unsuitable")


class TestCriticRejects:
    async def test_it_rejects_a_draft_that_softens_a_no_go(self):
        result = await critic(
            _state(
                "Conditions are marginal but you should be fine if you stay close to shore. "
                "Waves 2.64 m."
            )
        )
        assert result["critic_verdict"] == "revise"
        assert "softens" in result["critic_reason"]

    async def test_it_rejects_a_draft_that_omits_the_verdict(self):
        result = await critic(_state("Waves are 2.64 m today, wind is light."))
        assert result["critic_verdict"] == "revise"
        assert "does not state it" in result["critic_reason"]

    async def test_it_rejects_a_draft_that_omits_the_veto_figure(self):
        result = await critic(_state("**NO-GO** — the seas are too high for your boat."))
        assert result["critic_verdict"] == "revise"
        assert "omits the veto figure" in result["critic_reason"]

    async def test_it_reports_total_tool_failure(self):
        state = _state("**NO-GO** at 2.64 m.", risk=_risk())
        state["tool_results"] = [
            {"tool": "fetch_marine_conditions", "ok": False, "summary": "", "error": "timeout"}
        ]
        result = await critic(state)
        assert result["critic_verdict"] == "revise"
        assert "every tool failed" in result["critic_reason"]


class TestCriticApproves:
    async def test_it_approves_a_correct_draft(self):
        result = await critic(
            _state("**NO-GO** — wave height 2.64 m is over the 1.5 m limit for your 8.2 m boat.")
        )
        assert result["critic_verdict"] == "approve"
        assert result["answer"].startswith("**NO-GO**")

    async def test_it_does_not_bounce_a_draft_that_rounds_the_figure(self):
        # The exact regression, end to end through the critic.
        result = await critic(
            _state(
                "**NO-GO** — lightning probability 73 % exceeds the 60 % limit.",
                risk=_risk(vetoes=["high lightning probability (72.7% CAPE-derived)"]),
            )
        )
        assert result["critic_verdict"] == "approve"

    async def test_a_go_draft_needs_no_veto_figures(self):
        result = await critic(
            _state("**GO** — everything is within your limits.", risk=_risk("GO", [], 75.5))
        )
        assert result["critic_verdict"] == "approve"


class TestCriticEscalation:
    async def test_it_stops_bouncing_after_the_round_limit(self):
        # Must not loop forever on a model that will not comply.
        result = await critic(_state("Conditions should be fine.", rounds=2))
        assert result["critic_verdict"] == "escalate"
        assert "answer" in result

    async def test_escalation_prepends_the_engines_own_verdict(self):
        # If the critic gives up, the user must still see what the engine said —
        # shipping only the non-compliant draft would be the worst outcome.
        result = await critic(_state("Conditions should be fine.", rounds=2))
        answer = str(result["answer"])
        assert answer.startswith("**NO-GO**")
        assert "2.64 m" in answer

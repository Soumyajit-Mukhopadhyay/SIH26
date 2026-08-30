"""Every prompt, in one versioned place.

The system prompts carry one non-negotiable rule, stated three different ways
because it is the single most important constraint in the product: **the model
may explain a verdict, never issue or soften one.** The type system enforces it
(``verdict_source`` is a ``Literal``) and the critic checks it, but saying it
plainly in the prompt makes the model's job unambiguous rather than adversarial.
"""

from __future__ import annotations

import json
from typing import Any

PROMPTS_VERSION = "orca-prompts-2026.08"

_SAFETY_CONTRACT = """\
ABSOLUTE RULES — these are enforced by code, not just requested:
1. You NEVER issue a safety verdict. A deterministic rule engine computes
   GO / CAUTION / NO-GO. You explain what it decided and why.
2. You NEVER soften, hedge or contradict that verdict. If the engine says NO-GO,
   you say NO-GO plainly. Phrases like "should be fine", "probably safe" or
   "conditions are marginal" over a NO-GO will be rejected by a critic and the
   answer will be regenerated.
3. You NEVER invent a number. Every figure in your answer must appear in the
   tool results you were given. If you do not have a value, say you do not.
4. You always state that ORCA supplements, never replaces, official IMD and
   INCOIS bulletins.
"""

PLANNER_SYSTEM = f"""\
You are ORCA's planner. You choose which tools to run to answer a marine question
for a fisherman or a maritime authority on the Indian coast.

You are given a catalogue of tools with machine-readable capabilities:
`answers` (question kinds it serves), `resolution_deg`, `latency_ms`,
`provenance`, `cost` (1 cheap .. 5 heavy) and `coverage`.

Choose the SMALLEST set of tools that genuinely answers the question. Selecting
everything is a wrong answer: it wastes latency and shows no judgement. Prefer
low `cost` when two tools would serve equally.

Hard requirements:
- If the question touches safety, whether to sail, or risk, you MUST include
  `assess_risk`. It is the only source of a verdict.
- `assess_risk` needs live conditions, so put `fetch_marine_conditions` before it.
- If the question is about tomorrow, a window, a trend or planning, include
  `fetch_forecast_window`.
- If the question is about temperature, fishing grounds or fronts, include
  `fetch_satellite_sst`.
- If the question is about where the data comes from, include `discover_datasets`.

Reply with ONLY a JSON object, no prose and no code fence:
{{
  "rationale": "one or two sentences on why this set and not more",
  "steps": [{{"tool": "<exact tool name>", "why": "<what this contributes>"}}]
}}

{_SAFETY_CONTRACT}"""


def planner_user(
    *, question: str, tools: list[dict[str, Any]], lat: float, lon: float, loa_m: float
) -> str:
    return f"""\
QUESTION: {question}

CONTEXT: position {lat:.3f}N {lon:.3f}E, vessel length overall {loa_m} m.

TOOL CATALOGUE:
{json.dumps(tools, indent=2)}

Choose the minimal tool set and return the JSON object."""


REPORTING_SYSTEM = f"""\
You are ORCA's reporting agent. You turn tool results into a short, direct answer
for someone who may be about to take a small boat to sea.

Style:
- Lead with the verdict, in bold, on its own line. Never bury it.
- Then the reason, quoting the actual figures and the actual limits.
- Then what would change it, if the answer is not GO.
- Six sentences maximum. This may be read on a phone, at a harbour, in a hurry.
- Plain language. No jargon a fisherman would not use. No filler openings.
- Use markdown sparingly: bold for the verdict, a short bullet list for reasons.

{_SAFETY_CONTRACT}"""


def reporting_user(
    *,
    question: str,
    results: list[dict[str, Any]],
    risk: dict[str, Any] | None,
    place: str | None,
    loa_m: float,
    revision_note: str | None = None,
) -> str:
    sections = [f"QUESTION: {question}", f"VESSEL: {loa_m} m length overall"]
    if place:
        sections.append(f"LOCATION: {place}")

    if risk:
        sections.append(
            "RULE ENGINE VERDICT (authoritative — you may not change it):\n"
            + json.dumps(
                {
                    "verdict": risk["verdict"],
                    "index": risk["index"],
                    "vetoes": risk["vetoes"],
                    "components": [
                        {
                            "name": c["name"],
                            "value": c["value"],
                            "unit": c["unit"],
                            "limit": c["limit"],
                            "exceeded": c["exceeded"],
                        }
                        for c in risk["components"]
                    ],
                    "confidence": risk["confidence"],
                    "escalation_message": risk.get("escalation_message"),
                    "what_would_change_it": risk.get("what_would_change_it", []),
                    "boat_class": risk["boat_class_label"],
                    "thresholds_version": risk["thresholds_version"],
                },
                indent=2,
            )
        )

    sections.append(
        "TOOL RESULTS (the only figures you may quote):\n"
        + json.dumps(
            [
                {"tool": r["tool"], "ok": r["ok"], "summary": r["summary"], "error": r["error"]}
                for r in results
            ],
            indent=2,
        )
    )

    if revision_note:
        sections.append(
            "YOUR PREVIOUS DRAFT WAS REJECTED BY THE CRITIC.\n"
            f"Reason: {revision_note}\n"
            "Fix exactly that. State the verdict explicitly and quote the veto figures."
        )

    return "\n\n".join(sections)

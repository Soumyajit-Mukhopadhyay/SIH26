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
1. You NEVER issue a safety verdict. ORCA's verified safety service computes
   GO / CAUTION / NO-GO. You explain the returned result and why.
2. You NEVER soften, hedge or contradict that verdict. If the safety result says NO-GO,
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
- If the question asks about safety NOW, include `assess_risk`.
- `assess_risk` needs live conditions, so put `fetch_marine_conditions` before it.
- If the question asks whether TOMORROW or a future window is safe, use
  `assess_forecast_risk`; do not substitute the current `assess_risk` result.
- If the question asks for tide, include `fetch_tides`. A missing credential is
  an answerable data gap and the tool must be called so the gap is visible.
- For lightning/cyclone alerts or fishermen warnings, use `check_marine_alerts`.
  CAPE is potential, not an official alert.
- If the question is about WHERE TO FISH, include `find_fishing_zones` — that is
  the tool that answers it. `fetch_satellite_sst` returns a temperature, which is
  context for the answer and not the answer.
- If the question is about sea temperature or thermal fronts, include
  `fetch_satellite_sst`.
- If the question is about GETTING SOMEWHERE — a route, a passage, a crossing,
  "can I reach X" — include `plan_route`. If no destination is named, its
  structured refusal lets the answer ask for the missing destination.
- If asked why productivity declined, use `diagnose_productivity`; do not infer a
  cause from one SST, CAPE or wave snapshot.
- If asked which fishing zones are hazardous or restricted, use
  `screen_fishing_zones`. PFZ rank is an opportunity signal, not a hazard label.
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
- If a VERIFIED SAFETY RESULT is provided, lead with its verdict in bold. If no
  safety result is provided, answer the question directly and NEVER invent
  "NO VERDICT", GO, CAUTION or NO-GO.
- Then give the relevant reason, quoting only supplied figures and limits.
- Then what would change it, if the answer is not GO.
- Six sentences maximum, or eight for a compound question. This may be read on a
  phone, at a harbour, in a hurry.
- For a compound question, answer each part in the order asked, one short
  paragraph or bullet each. Do not merge them into a single paragraph.
- Plain language. No jargon a fisherman would not use. No filler openings.
- Use markdown sparingly: bold for the verdict, a short bullet list for reasons.
- End with `**In short:**` followed by one or two warm, natural sentences that
  tell the person what the evidence means and what they should do next. This is
  mandatory: values without an interpretation are not an answer.
- Cite factual claims with the numbered REFERENCES supplied below, for example
  `[1]`. Never invent a source or URL.

Question-specific truth rules:
- CAPE is a thunderstorm-potential proxy. Never call a CAPE-derived percentage
  a detected lightning strike, an official lightning probability or an alert.
- A failed official-alert check means "cannot verify", not "there is no alert".
- When official alert access is unavailable, say "ORCA cannot verify whether a
  warning exists"; do not say "there are no confirmed warnings".
- A missing route destination means "tell me the destination", never NO-GO.
- Without a VERIFIED SAFETY RESULT, never say "safe", "you can head out" or
  otherwise turn descriptive conditions into permission to sail.
- A current SST value cannot prove why productivity declined. Causality needs a
  named region/species/period plus historical landings, effort and environment.
- Do not call one SST value "favourable" without a named species, season and
  supported temperature range. A PFZ thermal front is not a universal ideal SST.
- PFZ candidates are fishing opportunities. Do not tell the user to avoid them
  unless `screen_fishing_zones` explicitly returns AVOID for that candidate.
- If PFZ data are marked stale, say so in the answer before recommending a zone.
- IMD warning products do not support tide timing. If tide access is unavailable,
  say to use a trusted local tide source; do not invent "IMD tide tables".

{_SAFETY_CONTRACT}"""


def reporting_user(
    *,
    question: str,
    results: list[dict[str, Any]],
    risk: dict[str, Any] | None,
    place: str | None,
    loa_m: float,
    revision_note: str | None = None,
    sub_questions: list[str] | None = None,
    references: list[dict[str, Any]] | None = None,
) -> str:
    sections = [f"QUESTION: {question}", f"VESSEL: {loa_m} m length overall"]
    if sub_questions and len(sub_questions) > 1:
        numbered = "\n".join(f"  {i + 1}. {q}" for i, q in enumerate(sub_questions))
        sections.append(
            "THIS IS A COMPOUND QUESTION. You MUST address every part below. "
            "Answering only the first is the most common way to look like you were "
            "not listening.\n" + numbered
        )
    if place:
        sections.append(f"LOCATION: {place}")

    if risk:
        sections.append(
            "VERIFIED SAFETY RESULT (authoritative — you may not change it):\n"
            + json.dumps(
                {
                    "verdict": risk["verdict"],
                    "index": risk["index"],
                    "vetoes": risk["vetoes"],
                    "components": [
                        {
                            "name": (
                                "CAPE-derived convective-risk proxy"
                                if c["name"] == "lightning"
                                else c["name"]
                            ),
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
                    "forecast_window": risk.get("forecast_window"),
                    "cape_j_kg": next(
                        (
                            item.get("value")
                            for item in risk.get("evidence", [])
                            if item.get("variable") == "convective_energy"
                        ),
                        None,
                    ),
                },
                indent=2,
            )
        )

    sections.append(
        "TOOL RESULTS (the only figures you may quote):\n"
        + json.dumps(
            [
                {
                    "tool": r["tool"],
                    "ok": r["ok"],
                    "summary": r["summary"],
                    "error": r["error"],
                    "details": _reportable_data(r["tool"], r.get("data") or {}),
                }
                for r in results
            ],
            indent=2,
        )
    )

    if references:
        sections.append(
            "REFERENCES (cite only these numbers, and only for the claims listed in supports):\n"
            + json.dumps(references, indent=2, ensure_ascii=False)
        )

    if revision_note:
        sections.append(
            "YOUR PREVIOUS DRAFT WAS REJECTED BY THE CRITIC.\n"
            f"Reason: {revision_note}\n"
            "Fix exactly that. State the verdict explicitly and quote the veto figures."
        )

    return "\n\n".join(sections)


def _reportable_data(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    """Keep the prompt useful without dumping route grids or whole source payloads."""
    keys: dict[str, tuple[str, ...]] = {
        "fetch_forecast_window": ("wave_min_m", "wave_max_m", "peak_at", "hours"),
        "assess_forecast_risk": ("window",),
        "fetch_tides": (
            "available",
            "current_height_m",
            "current_time",
            "next_high",
            "next_low",
            "datum",
            "station",
            "error",
        ),
        "check_marine_alerts": (
            "official_alerts_verified",
            "lightning_alert",
            "cyclone_alert",
            "cape_j_kg",
            "cape_is_alert",
            "reason",
        ),
        "find_fishing_zones": ("zones", "valid_time", "stale", "caveat"),
        "screen_fishing_zones": (
            "screened_zones",
            "pfz_valid_time",
            "pfz_stale",
            "caveat",
        ),
        "check_geofences": ("proximities", "radius_km"),
        "diagnose_productivity": ("diagnosis_supported", "required"),
        "plan_route": (
            "ok",
            "reason",
            "distance_nm",
            "duration_h",
            "detour_pct",
            "worst_verdict",
            "degraded",
        ),
    }
    wanted = keys.get(tool, ())
    return {key: data[key] for key in wanted if key in data}

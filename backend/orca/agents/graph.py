"""The agent graph: a LangGraph ``StateGraph`` with a supervisor we wrote.

    interaction -> planner -> [specialists, chosen by the planner]
                                 -> visualisation -> reporting -> critic
                                                                   |
                                                    approve -> END |
                                                    revise -> reporting

Three things here are the demo, and each is a deliberate design choice rather
than an artefact of the framework:

1. **The planner emits an explicit plan before executing anything.** That object
   is streamed to the UI and rendered. It is the visible difference between an
   agent and a chatbot, and it is scored.

2. **Tool selection is a decision, not a script.** The planner is handed the
   capability catalogue and picks; two different questions demonstrably pull
   different tool sets. There is no if/else on the question text.

3. **The critic can veto.** It re-reads the draft against the rule engine's
   actual verdict and bounces anything that softens or contradicts it. A draft
   saying "conditions are marginal" over a NO-GO gets rejected — which is
   exactly the failure mode of letting an LLM near a safety answer, caught by
   construction.

``langgraph-supervisor`` is deliberately not used: its handoff semantics do not
give clean control over plan emission or the critic bounce, which are the two
things that matter most here.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from orca.agents import prompts
from orca.agents.llm import LlmUnavailable, complete
from orca.agents.multiquery import Decomposition, decompose
from orca.agents.tools import TOOL_ORDER, catalogue, run_tool
from orca.provenance import Evidence, evidence_summary, utcnow

log = logging.getLogger(__name__)

MAX_CRITIC_ROUNDS = 2


def _append(left: list[Any], right: list[Any]) -> list[Any]:
    """Reducer so parallel specialist nodes accumulate rather than overwrite."""
    return [*left, *right]


class PlanStep(TypedDict):
    id: int
    tool: str
    why: str
    status: Literal["pending", "running", "done", "failed", "skipped"]


class OrcaState(TypedDict, total=False):
    """The graph's state. Everything the UI renders comes from here."""

    question: str
    locale: str
    lat: float
    lon: float
    place: str | None
    loa_m: float

    plan: list[PlanStep]
    plan_rationale: str
    tool_results: Annotated[list[dict[str, Any]], _append]
    evidence: Annotated[list[Evidence], _append]
    events: Annotated[list[dict[str, Any]], _append]

    #: The question, split into answerable parts. A compound question that gets
    #: one answer reads as the system not listening.
    decomposition: dict[str, Any]

    draft: str
    answer: str
    ui_spec: dict[str, Any]
    critic_rounds: int
    critic_verdict: str
    critic_reason: str
    llm_provider: str
    risk: dict[str, Any] | None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _event(kind: str, **payload: Any) -> dict[str, Any]:
    return {"type": kind, "at": utcnow().isoformat(), **payload}


def _emit(event: dict[str, Any]) -> None:
    """Push an event out of a node *mid-execution*.

    LangGraph's ``values`` stream mode only emits when a node finishes, so all
    three tool calls in `execute` arrived in the same frame — the trace was
    technically incremental but visually a lump, which defeats the point of
    showing it. The custom stream writer gets each tool_call and tool_result onto
    the wire the moment it happens.

    Outside a graph run there is no writer, and that is fine: the events are also
    accumulated in state, so a direct call still records everything.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    if writer is not None:
        writer(event)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model response.

    Models wrap JSON in prose and fences no matter how firmly you ask them not
    to, so parse defensively rather than trusting the instruction.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #


async def interaction(state: OrcaState) -> OrcaState:
    """Normalise the request, and split a compound question into its parts."""
    split: Decomposition = await decompose(state["question"])

    detail = (
        f"question received · {state.get('lat', 0):.3f}°N "
        f"{state.get('lon', 0):.3f}°E · vessel {state.get('loa_m', 8.2)} m"
    )
    if split.is_compound:
        detail += f" · {len(split.parts)} sub-questions detected"

    events = [_event("step", node="interaction", status="done", detail=detail)]
    if split.is_compound:
        events.append(
            _event(
                "decomposition",
                parts=[p.describe() for p in split.parts],
                method=split.method,
                tools=split.tools,
            )
        )

    return {
        "decomposition": split.describe(),
        "events": events,
        "critic_rounds": 0,
    }


async def planner(state: OrcaState) -> OrcaState:
    """Emit an explicit plan, chosen from the capability catalogue.

    On LLM failure this falls back to a fixed safety-first plan rather than
    failing the request: a fisherman asking whether to sail must get an answer.
    """
    question = state["question"]
    tools = catalogue()

    messages = [
        {"role": "system", "content": prompts.PLANNER_SYSTEM},
        {
            "role": "user",
            "content": prompts.planner_user(
                question=question,
                tools=tools,
                lat=state.get("lat", 0),
                lon=state.get("lon", 0),
                loa_m=state.get("loa_m", 8.2),
            ),
        },
    ]

    split = state.get("decomposition") or {}
    required: list[str] = list(split.get("tools") or [])

    plan: list[PlanStep] = []
    rationale = ""
    provider = "fallback"
    try:
        reply = await complete(messages, temperature=0.1, max_tokens=700)
        provider = reply.provider
        parsed = _extract_json(reply.text)
        if parsed:
            rationale = str(parsed.get("rationale", ""))[:600]
            for index, raw in enumerate(parsed.get("steps", [])[:6]):
                name = str(raw.get("tool", "")).strip()
                if name in {t["name"] for t in tools}:
                    plan.append(
                        {
                            "id": index + 1,
                            "tool": name,
                            "why": str(raw.get("why", ""))[:220],
                            "status": "pending",
                        }
                    )
    except LlmUnavailable as exc:
        log.warning("planner had no LLM, using the safety-first fallback: %s", exc)

    # Every tool the decomposition needs must be in the plan. The planner may add
    # to this but not drop from it: a sub-question whose tool never runs is a
    # question the user asked and ORCA silently ignored.
    if required:
        present = {step["tool"] for step in plan}
        for tool in required:
            if tool not in present:
                plan.append(
                    {
                        "id": len(plan) + 1,
                        "tool": tool,
                        "why": "required by a sub-question the planner did not cover",
                        "status": "pending",
                    }
                )
        # Re-establish the ordering constraint: conditions before the verdict.
        order = {name: i for i, name in enumerate(TOOL_ORDER)}
        plan.sort(key=lambda step: order.get(step["tool"], 99))
        for index, step in enumerate(plan):
            step["id"] = index + 1

    if not plan:
        # Safety-first default. Conditions, then the deterministic verdict.
        plan = [
            {
                "id": 1,
                "tool": "fetch_marine_conditions",
                "why": "live sea state is required before any verdict",
                "status": "pending",
            },
            {
                "id": 2,
                "tool": "assess_risk",
                "why": "the deterministic rule engine owns the GO/NO-GO",
                "status": "pending",
            },
        ]
        rationale = rationale or (
            "No planner model was available, so ORCA fell back to its fixed safety-first "
            "plan: fetch live conditions, then compute the deterministic verdict."
        )

    return {
        "plan": plan,
        "plan_rationale": rationale,
        "llm_provider": provider,
        "events": [
            _event("plan", steps=plan, rationale=rationale, planner_provider=provider),
        ],
    }


async def execute(state: OrcaState) -> OrcaState:
    """Run the planned tools in order, emitting a trace event per call.

    Sequential rather than parallel on purpose: the trace is meant to be watched,
    and steps landing one at a time is what makes it legible. The cost is a
    second or two, which the plan's latency budget can afford.
    """
    plan = list(state.get("plan", []))
    results: list[dict[str, Any]] = []
    evidence: list[Evidence] = []
    events: list[dict[str, Any]] = []
    risk: dict[str, Any] | None = None

    kwargs = {
        "lat": state.get("lat", 0.0),
        "lon": state.get("lon", 0.0),
        "loa_m": state.get("loa_m", 8.2),
    }

    for step in plan:
        call_event = _event("tool_call", step_id=step["id"], tool=step["tool"], args=kwargs)
        events.append(call_event)
        _emit(call_event)
        started = time.perf_counter()
        result = await run_tool(step["tool"], **kwargs)
        elapsed = round((time.perf_counter() - started) * 1000, 1)

        step["status"] = "done" if result.ok else "failed"
        results.append(
            {
                "step_id": step["id"],
                "tool": result.tool,
                "ok": result.ok,
                "summary": result.summary,
                "error": result.error,
                "latency_ms": elapsed,
                "data": result.data,
            }
        )
        evidence.extend(result.evidence)
        if "risk" in result.data:
            risk = result.data["risk"]

        result_event = _event(
            "tool_result",
            step_id=step["id"],
            tool=result.tool,
            ok=result.ok,
            summary=result.summary,
            error=result.error,
            latency_ms=elapsed,
            evidence_count=len(result.evidence),
        )
        events.append(result_event)
        _emit(result_event)

    return {
        "plan": plan,
        "tool_results": results,
        "evidence": evidence,
        "risk": risk,
        "events": events,
    }


async def visualisation(state: OrcaState) -> OrcaState:
    """Emit the ``ui_spec``: what the map should do about this answer.

    Deterministic, not generated. The camera moves to the point in question and
    the layers follow from which tools ran — so the map reacting to the chat is a
    consequence of the plan rather than a scripted flourish.
    """
    tools_run = {r["tool"] for r in state.get("tool_results", [])}
    layers: list[str] = []
    if "fetch_marine_conditions" in tools_run or "fetch_forecast_window" in tools_run:
        layers += ["wave_height", "wind"]
    if "fetch_satellite_sst" in tools_run:
        layers.append("sst")
    if "assess_risk" in tools_run:
        layers.append("verdict_marker")

    lat = state.get("lat", 0.0)
    lon = state.get("lon", 0.0)
    span = 3.0
    spec = {
        "camera": {"lat": lat, "lon": lon, "zoom": 7},
        "bbox": [lon - span, lat - span, lon + span, lat + span],
        "layers": layers,
        "charts": (["wave_forecast_48h"] if "fetch_forecast_window" in tools_run else []),
        "card": ((state.get("risk") or {}).get("verdict", "").lower().replace("-", "") or None),
    }
    return {"ui_spec": spec, "events": [_event("ui_spec", spec=spec)]}


async def reporting(state: OrcaState) -> OrcaState:
    """Draft the answer. The LLM writes prose; it does not decide anything."""
    risk = state.get("risk")
    evidence = state.get("evidence", [])

    messages = [
        {"role": "system", "content": prompts.REPORTING_SYSTEM},
        {
            "role": "user",
            "content": prompts.reporting_user(
                question=state["question"],
                results=state.get("tool_results", []),
                risk=risk,
                place=state.get("place"),
                loa_m=state.get("loa_m", 8.2),
                revision_note=state.get("critic_reason") if state.get("critic_rounds") else None,
            ),
        },
    ]

    try:
        # 800 truncated a compound answer mid-figure ("about 428" for 428 km²),
        # and a cut-off number is the one output this system must never produce.
        # The style rules bound the length; this only bounds the failure mode.
        reply = await complete(messages, temperature=0.25, max_tokens=1200)
        draft = reply.text.strip()
        provider = reply.provider
    except LlmUnavailable:
        # Degrade to a deterministic rendering rather than failing. Less fluent,
        # equally correct, and it still cites everything.
        draft = _deterministic_answer(risk, state.get("tool_results", []))
        provider = "deterministic-fallback"

    return {
        "draft": draft,
        "llm_provider": provider,
        "events": [
            _event(
                "step",
                node="reporting",
                status="done",
                detail=f"draft written by {provider}",
                evidence=evidence_summary(evidence),
            )
        ],
    }


def _deterministic_answer(risk: dict[str, Any] | None, results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    if risk:
        lines.append(f"**{risk['verdict']}** — index {risk['index']}/100.")
        for veto in risk.get("vetoes", []):
            lines.append(f"- {veto}")
        for change in risk.get("what_would_change_it", [])[:2]:
            lines.append(f"- What would change it: {change}")
    for result in results:
        if result["ok"]:
            lines.append(f"- {result['tool']}: {result['summary']}")
    lines.append(
        "\nThis answer was assembled without a language model because no provider was "
        "reachable. The verdict is unaffected — it always comes from the rule engine."
    )
    return "\n".join(lines)


async def critic(state: OrcaState) -> OrcaState:
    """Re-read the draft against the rule engine. Approve, or bounce it.

    The check that matters is mechanical and runs before any LLM opinion: if the
    engine said NO-GO, the draft must contain NO-GO and must not contain hedging
    language. That is not a matter of taste — softening a NO-GO is the specific
    failure this whole architecture exists to prevent.
    """
    draft = state.get("draft", "")
    risk = state.get("risk")
    rounds = state.get("critic_rounds", 0)

    problems: list[str] = []

    if risk:
        verdict = risk["verdict"]
        if verdict not in draft.upper():
            problems.append(
                f"the rule engine returned {verdict} but the draft does not state it explicitly"
            )
        if verdict == "NO-GO":
            softeners = [
                "should be fine",
                "probably safe",
                "conditions are marginal",
                "generally safe",
                "you may proceed",
                "it is safe",
            ]
            hit = [s for s in softeners if s in draft.lower()]
            if hit:
                problems.append(
                    f"the draft softens a NO-GO with {hit!r}; a hard veto may not be hedged"
                )
        for veto in risk.get("vetoes", []):
            # The number and the limit are the substance of a veto; a draft that
            # omits them has described a mood rather than a measurement.
            #
            # Compared NUMERICALLY, not as a substring. A substring check bounced
            # a correct draft three times in testing: the veto string rounds
            # 72.7% to "73%", the model quoted the precise 72.7%, and "73" was
            # absent from an answer that was right. Three wasted LLM rounds and a
            # spurious escalation, caused by the checker rather than the draft.
            if not _quotes_figure(draft, veto):
                figure = _first_number(veto)
                problems.append(f"the draft omits the veto figure {figure} from: {veto}")
                break

    if not any(r["ok"] for r in state.get("tool_results", [])) and "could not" not in draft.lower():
        problems.append("every tool failed but the draft does not say so")

    approved = not problems or rounds >= MAX_CRITIC_ROUNDS
    verdict_label = "approve" if not problems else ("escalate" if approved else "revise")
    reason = "; ".join(problems) if problems else "draft is consistent with the rule engine"

    events = [
        _event(
            "critic",
            verdict=verdict_label,
            reason=reason,
            round=rounds + 1,
            problems=problems,
        )
    ]

    if verdict_label == "revise":
        return {
            "critic_rounds": rounds + 1,
            "critic_verdict": verdict_label,
            "critic_reason": reason,
            "events": events,
        }

    answer = draft
    if problems and approved:
        # Out of rounds with problems outstanding. Do not ship a draft that
        # contradicts the engine — prepend the engine's own words.
        answer = _engine_preamble(risk) + "\n\n" + draft
        events.append(
            _event(
                "step",
                node="critic",
                status="escalated",
                detail="critic could not get a clean draft; the engine's verdict was prepended",
            )
        )

    return {
        "answer": answer,
        "critic_verdict": verdict_label,
        "critic_reason": reason,
        "critic_rounds": rounds + 1,
        "events": events,
    }


#: Relative tolerance when matching a figure the draft quotes against the figure
#: a veto states. 2% absorbs rounding (72.7 vs 73) without accepting a genuinely
#: different number.
_FIGURE_TOLERANCE = 0.02


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for match in re.findall(r"\d+(?:\.\d+)?", text):
        try:
            out.append(float(match))
        except ValueError:
            continue
    return out


def _first_number(text: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return match.group(0) if match else "?"


def _quotes_figure(draft: str, veto: str) -> bool:
    """Whether the draft states the figure the veto is about.

    The veto's FIRST number is the measured value — the thing the reader must
    see. Later numbers are the limit and the boat length, which the draft may
    reasonably paraphrase.
    """
    wanted = _numbers(veto)
    if not wanted:
        return True
    target = wanted[0]
    for candidate in _numbers(draft):
        if abs(candidate - target) <= max(_FIGURE_TOLERANCE * abs(target), 0.05):
            return True
    return False


def _engine_preamble(risk: dict[str, Any] | None) -> str:
    if not risk:
        return ""
    lines = [f"**{risk['verdict']}** (deterministic rule engine, index {risk['index']}/100)."]
    lines += [f"- {v}" for v in risk.get("vetoes", [])]
    return "\n".join(lines)


def route_after_critic(state: OrcaState) -> Literal["reporting", "__end__"]:
    return "reporting" if state.get("critic_verdict") == "revise" else END


# --------------------------------------------------------------------------- #
# graph
# --------------------------------------------------------------------------- #


def build_graph() -> Any:
    graph = StateGraph(OrcaState)

    graph.add_node("interaction", interaction)
    graph.add_node("planner", planner)
    graph.add_node("execute", execute)
    graph.add_node("visualisation", visualisation)
    graph.add_node("reporting", reporting)
    graph.add_node("critic", critic)

    graph.set_entry_point("interaction")
    graph.add_edge("interaction", "planner")
    graph.add_edge("planner", "execute")
    graph.add_edge("execute", "visualisation")
    graph.add_edge("visualisation", "reporting")
    graph.add_edge("reporting", "critic")
    graph.add_conditional_edges("critic", route_after_critic, {"reporting": "reporting", END: END})

    return graph.compile()


_compiled: Any = None


def compiled_graph() -> Any:
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


async def run(
    question: str,
    *,
    lat: float,
    lon: float,
    loa_m: float = 8.2,
    place: str | None = None,
    locale: str = "en",
) -> AsyncIterator[dict[str, Any]]:
    """Run the graph, yielding trace events as each node completes.

    Yields our own event schema rather than LangGraph's raw stream, so the SSE
    contract is ours and does not move when the framework's does.
    """
    initial: OrcaState = {
        "question": question,
        "lat": lat,
        "lon": lon,
        "loa_m": loa_m,
        "place": place,
        "locale": locale,
        "plan": [],
        "tool_results": [],
        "evidence": [],
        "events": [],
        "critic_rounds": 0,
    }

    seen = 0
    final: dict[str, Any] = {}
    #: Events already delivered by the custom writer, so the per-node `values`
    #: pass does not repeat them. Keyed on (type, step_id) rather than identity,
    #: since the dict crossing the two streams is not the same object.
    delivered: set[tuple[str, Any]] = set()

    async for mode, chunk in compiled_graph().astream(initial, stream_mode=["values", "custom"]):
        if mode == "custom":
            # Mid-node: a tool call or result, on the wire the instant it happens.
            delivered.add((str(chunk.get("type")), chunk.get("step_id")))
            yield chunk
            continue

        final = chunk
        events = chunk.get("events", [])
        for event in events[seen:]:
            if (str(event.get("type")), event.get("step_id")) in delivered:
                continue
            yield event
        seen = len(events)

    evidence = final.get("evidence", [])
    yield _event(
        "final",
        answer=final.get("answer") or final.get("draft", ""),
        decomposition=final.get("decomposition"),
        risk=final.get("risk"),
        ui_spec=final.get("ui_spec"),
        plan=final.get("plan", []),
        evidence=[e.model_dump(mode="json") for e in evidence],
        evidence_summary=evidence_summary(evidence),
        critic={
            "verdict": final.get("critic_verdict"),
            "reason": final.get("critic_reason"),
            "rounds": final.get("critic_rounds", 0),
        },
        llm_provider=final.get("llm_provider"),
    )

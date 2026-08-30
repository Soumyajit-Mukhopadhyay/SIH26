"""Decomposing a compound question into answerable parts.

A real question is rarely one question. *"Is it safe to go out tomorrow, where
are the fish, and how far am I from the Sri Lankan line?"* is three, with three
different tool sets and three different kinds of answer — a verdict, a bearing,
and a distance. A single-intent planner answers the first and silently drops the
other two, which reads as the system not listening.

So this runs before the planner: split the question into **sub-questions**, each
with its own intent, and let the graph answer each one before the reporting node
composes a single reply that addresses all of them.

Two design choices worth stating:

* **The split is a model call with a deterministic fallback.** Splitting on "and"
  and "?" gets most compound questions right and costs nothing, so it runs when
  no LLM is reachable — the feature degrades rather than disappearing.
* **Intents are a closed set.** The planner already selects tools from a
  capability catalogue; giving the splitter free rein to invent intent names
  would put a second, unvalidated vocabulary between the question and the tools.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from orca.agents.tools import TOOL_ORDER, ordered

log = logging.getLogger(__name__)

Intent = Literal[
    "safety",
    "forecast",
    "fishing",
    "routing",
    "boundary",
    "provenance",
    "thresholds",
    "location",
    "other",
]

#: Intent -> the tools that answer it. Mirrors the capability catalogue's
#: `answers` field so the two cannot drift apart silently.
INTENT_TOOLS: dict[Intent, tuple[str, ...]] = {
    "safety": ("fetch_marine_conditions", "assess_risk"),
    "forecast": ("fetch_forecast_window",),
    # The PFZ tool answers the question; satellite SST is context for it.
    "fishing": ("find_fishing_zones", "fetch_satellite_sst"),
    # `plan_route` needs a destination, so the conditions tool goes with it: if no
    # destination can be resolved the answer still has something to say about the
    # water where the user actually is.
    "routing": ("plan_route", "fetch_marine_conditions"),
    "boundary": ("fetch_marine_conditions",),
    "provenance": ("discover_datasets",),
    "thresholds": ("lookup_boat_thresholds",),
    "location": ("fetch_marine_conditions",),
    "other": ("fetch_marine_conditions",),
}

#: Keyword hints per intent, across English and romanised Indic. Used by the
#: deterministic fallback, and to sanity-check the model's own labelling.
_HINTS: dict[Intent, tuple[str, ...]] = {
    "safety": (
        "safe",
        "safety",
        "danger",
        "dangerous",
        "go out",
        "venture",
        "risk",
        "capsize",
        "should i",
        "can i go",
        "paathukaapa",
        "surakshit",
        "suraksha",
    ),
    "forecast": (
        "tomorrow",
        "tonight",
        "next",
        "later",
        "window",
        "when will",
        "forecast",
        "improve",
        "worse",
        "naalai",
        "kal",
        "aane wala",
    ),
    "fishing": (
        "fish",
        "fishing",
        "catch",
        "meen",
        "shoal",
        "pfz",
        "zone",
        "warmest",
        "chlorophyll",
        "where should i fish",
        "machhli",
        "chepa",
    ),
    "routing": (
        "route",
        "routing",
        "passage",
        "way to",
        "get to",
        "reach",
        "sail to",
        "head to",
        "heading to",
        "cross to",
        "crossing",
        "how do i get",
        "best way",
        "vazhi",  # Tamil/Malayalam: way, route
        "pogum vazhi",
        "raasta",  # Hindi/Urdu: road, route
        "kaise jaun",
        "marg",  # Marathi/Hindi: route
    ),
    "boundary": (
        "border",
        "boundary",
        "imbl",
        "sri lanka",
        "srilanka",
        "eez",
        "cross into",
        "arrest",
        "maritime",
        "ellai",
    ),
    "provenance": (
        "where does",
        "data come from",
        "source",
        "how do you know",
        "which satellite",
        "provenance",
        "reliable",
        "trust",
    ),
    "thresholds": (
        "limit",
        "limits",
        "my boat",
        "boat class",
        "how big",
        "allowed",
        "rules",
        "regulation",
        "who says",
        "seemai",
        "सीमा",
        "எனது படகு",
    ),
    "location": ("where am i", "position", "nearest port", "harbour", "harbor", "how far"),
}

#: Conjunctions that separate clauses, in English and the Indic languages we
#: support. A romanised or native "and" is a genuine clause boundary.
_SPLITTERS = (
    r"\band also\b",
    r"\band\b",
    r"\balso\b",
    r"\bplus\b",
    r"\bthen\b",
    r"\bmattum\b",
    r"\bmarrum\b",  # Tamil "and", romanised
    r"\baur\b",
    r"\baani\b",
    r"\bebong\b",
    r"\bmariyu\b",
    r"மற்றும்",
    r"और",
    r"आणि",
    r"এবং",
    r"మరియు",
    r"ಮತ್ತು",
    r"കൂടാതെ",
    r"અને",
    r"ଏବଂ",
)


@dataclass(slots=True)
class SubQuestion:
    id: int
    text: str
    intent: Intent
    #: Tools this part needs, derived from the intent.
    tools: list[str]

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "intent": self.intent, "tools": self.tools}


@dataclass(slots=True)
class Decomposition:
    original: str
    parts: list[SubQuestion]
    method: str
    #: Every tool needed across all parts, deduplicated and ordered.
    tools: list[str]

    @property
    def is_compound(self) -> bool:
        return len(self.parts) > 1

    def describe(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "compound": self.is_compound,
            "method": self.method,
            "parts": [p.describe() for p in self.parts],
            "tools": self.tools,
        }


def _classify(text: str) -> Intent:
    """Best intent for one clause, by keyword weight.

    Ties break towards ``safety``, deliberately: mislabelling a safety question
    as something else loses the verdict, which is the one answer that must never
    be dropped.
    """
    lowered = text.lower()
    scores: dict[Intent, int] = {}
    for intent, hints in _HINTS.items():
        scores[intent] = sum(1 for hint in hints if hint in lowered)

    best = max(scores, key=lambda k: (scores[k], k == "safety"))
    return best if scores[best] > 0 else "other"


def _collect_tools(parts: list[SubQuestion]) -> list[str]:
    """Union of the parts' tools, in execution order."""
    needed = {tool for part in parts for tool in part.tools}
    return ordered(needed)


def split_heuristic(question: str) -> Decomposition:
    """Split on conjunctions and sentence boundaries. No model required.

    This is the fallback, and it is genuinely serviceable: most compound
    questions are literally joined by "and" or a question mark.
    """
    # Sentence boundaries first, then conjunctions within each sentence.
    sentences = [s.strip() for s in re.split(r"(?<=[?।।.!])\s+", question) if s.strip()]
    clauses: list[str] = []
    for sentence in sentences or [question]:
        pieces = re.split("|".join(_SPLITTERS), sentence, flags=re.IGNORECASE)
        for piece in pieces:
            cleaned = piece.strip(" ,;?।.!\t")
            # A fragment shorter than this is a stray conjunction tail, not a
            # question — splitting "wind and waves" into "wind" and "waves"
            # would produce two intents where the user asked one thing.
            if len(cleaned) >= 12:
                clauses.append(cleaned)

    if not clauses:
        clauses = [question.strip()]

    parts = [
        SubQuestion(
            id=i + 1,
            text=text,
            intent=(intent := _classify(text)),
            tools=list(INTENT_TOOLS[intent]),
        )
        for i, text in enumerate(clauses[:4])
    ]
    return Decomposition(
        original=question,
        parts=parts,
        method="heuristic",
        tools=_collect_tools(parts),
    )


async def decompose(question: str) -> Decomposition:
    """Split a question into answerable parts, preferring the model.

    Falls back to :func:`split_heuristic` on any LLM failure, so a compound
    question is still handled as compound when no provider is reachable.
    """
    from orca.agents.llm import LlmUnavailable, complete

    fallback = split_heuristic(question)

    # A short question with no conjunction is not worth a model call.
    if len(question) < 40 and not fallback.is_compound:
        return fallback

    system = (
        "Split a marine question into its separate answerable parts.\n\n"
        'Return ONLY JSON: {"parts": [{"text": "...", "intent": "..."}]}\n\n'
        "intent must be exactly one of: safety, forecast, fishing, boundary, "
        "provenance, thresholds, location, other.\n\n"
        "Rules:\n"
        "- One part per distinct thing the user wants to know. Most questions are ONE part.\n"
        "- Do NOT split a single request that merely lists variables "
        '("wind and waves" is one part, not two).\n'
        "- Keep each part's wording close to the user's own.\n"
        "- The question may be in any Indian language; keep each part in that language.\n"
        "- At most 4 parts."
    )

    try:
        reply = await complete(
            [{"role": "system", "content": system}, {"role": "user", "content": question}],
            temperature=0.0,
            max_tokens=400,
        )
    except LlmUnavailable as exc:
        log.info("decomposition falling back to the heuristic: %s", exc)
        return fallback

    parsed = _extract_json(reply.text)
    if not parsed or not isinstance(parsed.get("parts"), list) or not parsed["parts"]:
        return fallback

    parts: list[SubQuestion] = []
    for index, raw in enumerate(parsed["parts"][:4]):
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        intent = str(raw.get("intent", "other"))
        if intent not in INTENT_TOOLS:
            # The model invented an intent. Fall back to our own classifier for
            # this part rather than accepting a label the tool map cannot serve.
            intent = _classify(text)
        parts.append(
            SubQuestion(
                id=index + 1,
                text=text,
                intent=intent,  # type: ignore[arg-type]
                tools=list(INTENT_TOOLS[intent]),  # type: ignore[index]
            )
        )

    if not parts:
        return fallback

    # UNION the model's tools with the keyword classifier's, rather than trusting
    # the model's labelling alone.
    #
    # The model gets the split right and the label occasionally wrong: it labelled
    # "meen enga irukku" (Tamil for "where are the fish") as `location` rather
    # than `fishing`, which dropped the satellite-SST tool and lost a question the
    # user had explicitly asked. Including one extra cheap tool costs a second of
    # latency; dropping a sub-question makes ORCA look like it was not listening.
    # The planner still narrows from here.
    merged = sorted(
        {*_collect_tools(parts), *fallback.tools},
        key=lambda tool: TOOL_ORDER.index(tool) if tool in TOOL_ORDER else 99,
    )

    return Decomposition(
        original=question,
        parts=parts,
        method=f"llm:{reply.provider}",
        tools=merged,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

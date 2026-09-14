"""The researcher plane: the dataset catalogue and natural-language discovery.

Its own package, and not a member of ``orca.services``, for a reason the test
suite enforces. ``services/`` is the deterministic core — the rule engine, the
router, the drift model — and an automated test asserts it never imports the
agent plane, so the safety verdict cannot acquire a dependency on a language
model even by accident.

Discovery legitimately *does* call a model: it parses prose into a structured
intent. That is fine, and it is exactly why this does not belong in
``services/``. Putting it there would have forced the boundary test to grow an
exception, and an architectural rule with an exception is a rule that will grow
a second one.

The model's reach stops at parsing. Dataset selection is deterministic matching
over a registry of sources ORCA has actually integrated.
"""

from __future__ import annotations

from orca.research import federation
from orca.research.catalogue import (
    BY_ID,
    CATALOGUE,
    CATALOGUE_VERSION,
    REGIONS,
    Dataset,
    Intent,
    Variable,
    discover,
    match,
    merge,
    parse_heuristic,
    parse_with_model,
    resolve_region,
    snippet,
)

__all__ = [
    "BY_ID",
    "CATALOGUE",
    "CATALOGUE_VERSION",
    "REGIONS",
    "Dataset",
    "Intent",
    "Variable",
    "discover",
    "federation",
    "match",
    "merge",
    "parse_heuristic",
    "parse_with_model",
    "resolve_region",
    "snippet",
]

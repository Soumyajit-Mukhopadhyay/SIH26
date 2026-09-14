"""ORCA's learned models.

Everything in this package obeys the same rule as the rest of ORCA: a model's
output is evidence, not a verdict. Nothing here is allowed to move a GO/NO-GO —
the deterministic rule engine in ``orca.services`` does that, and a test asserts
``services/`` never imports this package any more than it imports ``agents/``.

What a model IS allowed to do is tell a fisherman or a researcher something no
rule can: where a thermal front will be tomorrow, rather than only where it was
this morning.
"""

from __future__ import annotations

__all__ = ["FRONTCAST_VERSION"]

#: Bumped whenever the architecture, the label definition or the training set
#: changes in a way that makes stored metrics incomparable. Served with every
#: prediction so a number on a slide can be traced to the weights that produced it.
FRONTCAST_VERSION = "orca-frontcast-2026.09"

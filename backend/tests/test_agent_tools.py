# --- one tool ordering, not two ---------------------------------------------


def test_tool_order_covers_every_tool_in_the_catalogue() -> None:
    """A tool absent from the ordering is silently dropped from every decomposed
    question.

    This is not hypothetical. `plan_route` was added to the catalogue and to one
    of the two copies of the ordering that existed at the time; the intent
    classified correctly, the tool ran fine on its own, and the union in
    `_collect_tools` filtered it straight back out of every compound question.
    There is one definition now, and this is the test that keeps it complete.
    """
    from orca.agents.tools import TOOL_ORDER, TOOLS

    assert set(TOOL_ORDER) == set(TOOLS), (
        f"in TOOLS but not ordered: {sorted(set(TOOLS) - set(TOOL_ORDER))}; "
        f"ordered but not a tool: {sorted(set(TOOL_ORDER) - set(TOOLS))}"
    )


def test_conditions_are_ordered_before_the_verdict() -> None:
    """`assess_risk` consumes conditions, so this is a correctness constraint on
    the ordering rather than a presentational preference."""
    from orca.agents.tools import TOOL_ORDER

    assert TOOL_ORDER.index("fetch_marine_conditions") < TOOL_ORDER.index("assess_risk")
    assert TOOL_ORDER.index("assess_risk") < TOOL_ORDER.index("plan_route")


def test_ordered_puts_unknown_names_last_rather_than_dropping_them() -> None:
    """Dropping is what caused the bug above; an unrecognised name must survive
    and be visible."""
    from orca.agents.tools import ordered

    got = ordered({"assess_risk", "made_up_tool", "fetch_marine_conditions"})
    assert got == ["fetch_marine_conditions", "assess_risk", "made_up_tool"]


def test_every_intent_maps_to_real_tools() -> None:
    from orca.agents.multiquery import INTENT_TOOLS
    from orca.agents.tools import TOOLS

    for intent, names in INTENT_TOOLS.items():
        unknown = [n for n in names if n not in TOOLS]
        assert not unknown, f"intent {intent!r} maps to non-existent tool(s) {unknown}"


def test_plan_route_refuses_without_a_destination() -> None:
    """An agent that guesses a destination produces a confident route to
    somewhere nobody asked about."""
    import asyncio

    from orca.agents.tools import run_tool

    result = asyncio.run(run_tool("plan_route", lat=13.0, lon=80.5))
    assert result.ok is False
    assert "destination" in (result.error or "").lower()

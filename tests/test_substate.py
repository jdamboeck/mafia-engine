"""Contract tests for the ``LoadSubState`` nested sub-state machine (U1, KTD-1).

Written proof-first: authored to observe a red failure (the driver still raises
``NotImplementedError`` for ``LoadSubState``), then the driver + ``engine.substates``
are implemented to green.

The sub-state runner (in :func:`engine.interactions.run`'s loop, NOT ``_resolve``):
on ``LoadSubState(kind, params)`` it looks up the factory registered under ``kind``
in :data:`engine.substates.SUBSTATES`, drives the child generator with the SAME
``Ctx`` (shared-buffer model — child effects/events merge into the parent action),
and ``.send()``s the child's return value back into the parent generator. A
``Cancelled`` thrown at a child prompt unwinds the WHOLE action atomically.
"""

from __future__ import annotations

import pytest

from engine.interactions import (
    CANCEL,
    LoadSubState,
    PromptInt,
    ShowMessage,
    StartCombat,
    run,
)
from engine.state import Fighter
from engine.substates import SUBSTATES, register_substate
from tests.helpers import scripted




@pytest.fixture(autouse=True)
def _clean_substates():
    """Snapshot/restore SUBSTATES so per-test registrations don't leak."""
    saved = dict(SUBSTATES)
    try:
        yield
    finally:
        SUBSTATES.clear()
        SUBSTATES.update(saved)


# --------------------------------------------------------------------------- #
# Scenario 1 — the sub-state return value threads back into the parent        #
# --------------------------------------------------------------------------- #
def test_substate_return_value_threads_into_parent():
    @register_substate("give_seven")
    def _give_seven(ctx, params):
        yield ShowMessage("spec.screen", params)
        return 7

    received = {}

    def parent(ctx):
        got = yield LoadSubState("give_seven", {"weapon": "revolver"})
        received["got"] = got
        return []

    result = run(parent, scripted(), state=None)

    assert result.status == "completed"
    assert received["got"] == 7


# --------------------------------------------------------------------------- #
# Scenario 2 — sub-state effects buffer into the parent's committed effects    #
# --------------------------------------------------------------------------- #
def test_substate_effects_merge_into_parent_action():
    @register_substate("buffers_effect")
    def _buffers(ctx, params):
        ctx.apply(("from_substate", 1))
        yield ShowMessage("spec")
        return None

    def parent(ctx):
        ctx.apply(("from_parent", 0))
        yield LoadSubState("buffers_effect", {})
        ctx.apply(("from_parent", 2))
        return []

    result = run(parent, scripted(), state=None)

    assert result.status == "completed"
    # Ordered by apply-time: parent, then child (mid-yield), then parent again.
    assert result.effects == [
        ("from_parent", 0),
        ("from_substate", 1),
        ("from_parent", 2),
    ]


# --------------------------------------------------------------------------- #
# Scenario 3 — sub-state events buffer into the parent action                 #
# --------------------------------------------------------------------------- #
def test_substate_events_merge_into_parent_action():
    @register_substate("records_event")
    def _records(ctx, params):
        ctx.record(("child_event",))
        yield ShowMessage("spec")
        return None

    def parent(ctx):
        ctx.record(("parent_event",))
        yield LoadSubState("records_event", {})
        return []

    result = run(parent, scripted(), state=None)

    assert result.status == "completed"
    assert result.events == [("parent_event",), ("child_event",)]


# --------------------------------------------------------------------------- #
# Scenario 4 — a sub-state that PROMPTS is driven by the same input_source     #
# (forward-investment path, KTD-1: no content consumer this run)              #
# --------------------------------------------------------------------------- #
def test_substate_prompt_driven_by_shared_input_source():
    @register_substate("asks_int")
    def _asks(ctx, params):
        # The child prompt is validated/re-prompted by the same driver machinery.
        n = yield PromptInt("child.pick", min=1, max=10)
        return n * 2

    received = {}

    def parent(ctx):
        got = yield LoadSubState("asks_int", {})
        received["got"] = got
        return []

    # "0" is out of range → re-prompt; then "4" is accepted → child returns 8.
    result = run(parent, scripted(0, 4), state=None)

    assert result.status == "completed"
    assert received["got"] == 8


# --------------------------------------------------------------------------- #
# Scenario 5 — cancel INSIDE a sub-state unwinds the whole action atomically   #
# (the design driver for shared-buffer + inline-loop, KTD-1)                   #
# --------------------------------------------------------------------------- #
def test_cancel_inside_substate_unwinds_whole_action():
    reached_after = {"parent": False}

    @register_substate("cancellable_child")
    def _child(ctx, params):
        ctx.apply(("child_effect_before_cancel",))
        # Cancellable child prompt: CANCEL here must unwind the ENTIRE action.
        yield PromptInt("child.pick", min=1, max=10, cancellable=True)
        return 1  # pragma: no cover - never reached on cancel

    def parent(ctx):
        ctx.apply(("parent_effect",))
        yield LoadSubState("cancellable_child", {})
        reached_after["parent"] = True  # pragma: no cover - never reached on cancel
        return []

    result = run(parent, scripted(CANCEL), state=None)

    assert result.status == "cancelled"
    assert result.effects == []
    assert result.events == []
    assert reached_after["parent"] is False


# --------------------------------------------------------------------------- #
# Scenario 6 — unknown kind is a config bug: ValueError (not an error result)  #
# --------------------------------------------------------------------------- #
def test_unknown_substate_kind_raises_value_error():
    def parent(ctx):
        yield LoadSubState("does_not_exist", {})
        return []

    with pytest.raises(ValueError):
        run(parent, scripted(), state=None)


# --------------------------------------------------------------------------- #
# Scenario 7 — StartCombat runs at top level, but is asserted OUT of sub-states #
# --------------------------------------------------------------------------- #
def test_startcombat_from_a_top_level_handler_runs_the_fight():
    """U5: the raise is gone at top level — the driver runs the combat sub-protocol."""
    got = {}

    def parent(ctx):
        got["winner"] = yield StartCombat(
            sides=((Fighter(name="hero", position=10),), (Fighter(name="thug", position=300),)),
        )
        return []

    run(parent, lambda interaction: CANCEL, state=None)  # CANCEL == surrender (KTD-2)
    assert got["winner"] == 2


def test_startcombat_inside_a_substate_is_asserted_out():
    """KTD-1: combat is only ever yielded from TOP-LEVEL handlers this slice.

    The driver asserts this rather than supporting nesting, because a fight inside a
    sub-state would need cancel semantics this slice has not decided (combat is
    non-cancellable; a cancelled sub-state unwinds the whole action).
    """

    @register_substate("u5_combat_in_substate")
    def child(ctx, params):
        yield StartCombat(
            sides=((Fighter(name="hero", position=10),), (Fighter(name="thug", position=300),)),
        )
        return None

    def parent(ctx):
        yield LoadSubState(kind="u5_combat_in_substate", params={})
        return []

    try:
        with pytest.raises(AssertionError):
            run(parent, lambda interaction: CANCEL, state=None)
    finally:
        SUBSTATES.pop("u5_combat_in_substate", None)

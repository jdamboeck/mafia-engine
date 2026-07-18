"""Contract tests for the interaction protocol + synchronous driver (U4 — THE SPINE).

Written proof-first: this file is authored BEFORE ``engine/interactions.py`` exists,
run to observe a red failure (import error), then the module is implemented to green.

The driver's job is to advance a handler generator, turn each yielded Interaction
into a response obtained from a scripted ``input_source`` callable, and ``.send()``
that response back. It enforces validation/re-prompting, cancellation via
``gen.throw(Cancelled)``, and atomic effect commit/discard.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from unittest import mock

import pytest

import engine.effects

from engine.interactions import (
    CANCEL,
    Ack,
    Cancelled,
    Confirm,
    LoadSubState,
    PromptChoice,
    PromptInt,
    ShowMessage,
    StartCombat,
    run,
)
from engine.state import Clock, Gangster, GameState, MapState, Player
from tests.helpers import run_pure


# --------------------------------------------------------------------------- #
# input_source helper                                                         #
# --------------------------------------------------------------------------- #
def scripted(*responses):
    """Return an input_source callable that yields the given responses in order.

    The driver calls ``input_source(interaction)`` each time it needs a value.
    We also record every interaction it was consulted for, so tests can assert
    on the exact sequence and count of prompts.
    """
    it = iter(responses)
    seen = []

    def source(interaction):
        seen.append(interaction)
        try:
            return next(it)
        except StopIteration:  # pragma: no cover - indicates a test scripting bug
            raise AssertionError(
                f"input_source exhausted; driver asked again for {interaction!r}"
            )

    source.seen = seen
    return source


# --------------------------------------------------------------------------- #
# Scenario 1 — scripted handler happy path                                    #
# --------------------------------------------------------------------------- #
def test_happy_path_sequence_responses_and_effects():
    received = {}

    def handler(ctx):
        yield ShowMessage("welcome")
        n = yield PromptInt("how_many", min=1, max=10)
        received["n"] = n
        ctx.apply(("bought", n))
        ok = yield Confirm("sure")
        received["ok"] = ok
        return ["event_done"]

    src = scripted(3, True)
    result = run(handler, src)

    # ShowMessage does not consult input_source: only the two real prompts do.
    assert [type(i).__name__ for i in src.seen] == ["PromptInt", "Confirm"]
    assert received == {"n": 3, "ok": True}
    assert result.status == "completed"
    assert result.effects == [("bought", 3)]
    assert result.payload.returned == ["event_done"]


# --------------------------------------------------------------------------- #
# Scenario 2 — PromptInt re-prompt happens in the DRIVER, not the handler     #
# --------------------------------------------------------------------------- #
def test_promptint_reprompts_in_driver_only():
    entries = {"count": 0, "value": None}

    def handler(ctx):
        entries["count"] += 1
        v = yield PromptInt("pick", min=1, max=5)
        entries["value"] = v
        return []

    # out-of-range (9) -> non-numeric ("x") -> valid (4)
    src = scripted(9, "x", 4)
    result = run(handler, src)

    assert entries["count"] == 1  # handler entered exactly once
    assert entries["value"] == 4  # only the final valid int reaches the handler
    assert len(src.seen) == 3  # driver consulted the source three times
    assert all(type(i).__name__ == "PromptInt" for i in src.seen)
    assert result.status == "completed"


# --------------------------------------------------------------------------- #
# Scenario 3 — Cancel atomicity (THE KEY TEST)                                #
# --------------------------------------------------------------------------- #
def test_cancel_discards_effects_and_runs_finally():
    flags = {"finally_ran": False}

    def handler(ctx):
        ctx.apply(("spent", 100))  # a partial effect BEFORE the cancel
        try:
            yield PromptInt("confirm_amount", min=1, max=999, cancellable=True)
            ctx.apply(("should_not_commit", 1))  # unreachable after cancel
        finally:
            flags["finally_ran"] = True
        return ["never_returned"]

    src = scripted(CANCEL)
    result = run(handler, src)

    assert result.status == "cancelled"
    assert result.effects == []  # atomic discard: zero effects commit
    assert flags["finally_ran"] is True  # try/finally cleanup ran
    assert result.payload.returned is None


def test_cancel_does_not_swallow_via_finally_only():
    # A handler with only a finally (no except) still cancels cleanly.
    def handler(ctx):
        try:
            yield PromptChoice("menu", options=["a", "b"], cancellable=True)
        finally:
            pass
        return ["x"]

    result = run(handler, scripted(CANCEL))
    assert result.status == "cancelled"
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Scenario 4 — Confirm yields bool; PromptChoice yields chosen index          #
# --------------------------------------------------------------------------- #
def test_confirm_yields_bool():
    got = {}

    def handler(ctx):
        got["a"] = yield Confirm("q1")
        got["b"] = yield Confirm("q2")
        return []

    run(handler, scripted(True, False))
    assert got == {"a": True, "b": False}
    assert isinstance(got["a"], bool) and isinstance(got["b"], bool)


def test_promptchoice_yields_chosen_index():
    got = {}

    def handler(ctx):
        got["idx"] = yield PromptChoice("menu", options=["pub", "bank", "docks"])
        return []

    run(handler, scripted(2))
    assert got["idx"] == 2


def test_promptchoice_reprompts_on_out_of_range_index():
    got = {}

    def handler(ctx):
        got["idx"] = yield PromptChoice("menu", options=["a", "b"])
        return []

    # 5 (out of range) -> -1 (out of range) -> 1 (valid)
    src = scripted(5, -1, 1)
    run(handler, src)
    assert got["idx"] == 1
    assert len(src.seen) == 3


# --------------------------------------------------------------------------- #
# Scenario 5 — StartCombat still raises; LoadSubState now runs sub-states      #
# (the LoadSubState nested-runner contract lives in tests/test_substate.py)    #
# --------------------------------------------------------------------------- #
def test_startcombat_raises_not_implemented():
    def handler(ctx):
        yield StartCombat(fighters=["g1"], arena="alley")
        return []

    with pytest.raises(NotImplementedError):
        run(handler, scripted())


def test_loadsubstate_unknown_kind_raises_value_error():
    # LoadSubState is implemented (U1); an UNREGISTERED kind is a config bug → ValueError.
    def handler(ctx):
        yield LoadSubState(kind="safecrack", params={})
        return []

    with pytest.raises(ValueError):
        run(handler, scripted())


# --------------------------------------------------------------------------- #
# Scenario 6 — effect ordering on clean completion                            #
# --------------------------------------------------------------------------- #
def test_effects_commit_in_order():
    def handler(ctx):
        ctx.apply("first")
        ctx.apply("second")
        yield ShowMessage("mid")
        ctx.apply("third")
        return []

    result = run(handler, scripted())
    assert result.effects == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# Edges — ShowMessage auto-acks; ctx exposes state/rng; non-cancellable CANCEL #
# --------------------------------------------------------------------------- #
def test_showmessage_autoacks_without_consulting_input_source():
    acks = {}

    def handler(ctx):
        r = yield ShowMessage("hi")
        acks["r"] = r
        return []

    src = scripted()  # no responses scripted at all
    run(handler, src)
    assert src.seen == []  # ShowMessage never consulted input_source
    assert acks["r"] is Ack  # the Ack sentinel is sent back


def test_ctx_exposes_state_and_rng():
    seen = {}
    sentinel_state = object()
    sentinel_rng = object()

    def handler(ctx):
        seen["state"] = ctx.state
        seen["rng"] = ctx.rng
        return []
        yield  # pragma: no cover - make it a generator

    run(handler, scripted(), state=sentinel_state, rng=sentinel_rng)
    assert seen["state"] is sentinel_state
    assert seen["rng"] is sentinel_rng


def test_handler_that_swallows_cancelled_and_continues_fails_loud():
    # A handler must let Cancelled unwind it. If it catches Cancelled and yields
    # again, the driver must raise (not silently feed the next Interaction back in
    # as a response). Guards the gen.throw() return-value corruption path.
    def bad_handler(ctx):
        try:
            yield PromptInt("amount", min=1, max=9, cancellable=True)
        except Cancelled:
            # Contract violation: swallow the cancel and keep going.
            yield PromptInt("retry", min=1, max=9)
        return []

    with pytest.raises(RuntimeError, match="caught Cancelled and continued"):
        run(bad_handler, scripted(CANCEL, 3))


def test_coerce_int_rejects_underscores_and_non_ascii_digits():
    # "1_000" and full-width "３" are int()-literal niceties a player prompt should
    # not honor; the driver re-prompts until a plain ASCII decimal arrives.
    got = {}

    def handler(ctx):
        got["v"] = yield PromptInt("pick", min=1, max=2000)
        return []

    src = scripted("1_000", "３", "1000")  # rejected, rejected, accepted
    run(handler, src)
    assert got["v"] == 1000
    assert len(src.seen) == 3


def test_cancel_at_non_cancellable_prompt_is_treated_as_invalid_and_reprompts():
    # CANCEL sent to a non-cancellable prompt must NOT throw; it is invalid input
    # and the driver re-prompts.
    got = {}

    def handler(ctx):
        got["v"] = yield PromptInt("pick", min=1, max=9)  # cancellable defaults False
        return []

    src = scripted(CANCEL, 7)
    result = run(handler, src)
    assert got["v"] == 7
    assert result.status == "completed"
    assert len(src.seen) == 2


# --------------------------------------------------------------------------- #
# Scenario 7 — semantic events: ctx.record buffers; surfaced on completion,   #
#              discarded on cancel (atomic, like effects).                     #
# --------------------------------------------------------------------------- #
def test_recorded_events_surface_on_clean_completion():
    def handler(ctx):
        ctx.record(("rented", 2))
        yield ShowMessage("mid")
        ctx.record(("charged", 100))
        return ["ret"]

    result = run(handler, scripted())
    assert result.status == "completed"
    assert result.events == [("rented", 2), ("charged", 100)]
    # Events are audit-only records — never mixed into the committed effects.
    assert result.effects == []


def test_recorded_events_discarded_on_cancel():
    def handler(ctx):
        ctx.record(("rented", 2))  # a partial event BEFORE the cancel
        yield PromptInt("confirm", min=1, max=9, cancellable=True)
        ctx.record(("should_not_record", 1))  # unreachable
        return ["never"]

    result = run(handler, scripted(CANCEL))
    assert result.status == "cancelled"
    assert result.events == []  # atomic discard: zero events surface
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Scenario 8 — purity harness (tests/helpers.run_pure) SELF-PROOF             #
#                                                                             #
# The harness is test infrastructure; prove it WORKS by showing it CATCHES a  #
# handler that mutates ctx.state directly instead of buffering an effect.     #
# --------------------------------------------------------------------------- #
def _harness_state(ka=5000):
    """A minimal one-player GameState the purity harness can compare against."""
    return GameState(
        players=(Player(ka=ka, roster=(Gangster(),)),),
        clock=Clock(active_player=0, player_count=1),
    )


def test_direct_state_mutation_raises_at_the_offending_line():
    """A handler that writes ctx.state directly now fails AT THE WRITE (R1).

    Pre-freeze this was caught after the fact, by run_pure comparing result.state
    against an independent replay of result.effects. The frozen graph upgrades that
    to a language-level guarantee: the illegal write raises where it is written, so
    the corruption can never reach a save file in the first place.
    """

    def rigged_handler(ctx):
        ctx.state.players[0].ka += 1  # illegal direct mutation, no effect buffered
        return []
        yield  # pragma: no cover - make this a generator

    st = _harness_state(ka=5000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        run_pure(rigged_handler, scripted(), state=st)


def test_run_pure_catches_a_mutation_that_bypasses_frozen():
    """The purity harness itself must fail on a genuinely-mutating handler.

    ``object.__setattr__`` is the one escape a frozen dataclass cannot close, so it
    is exactly what run_pure is the compensating control for. This test pins the
    harness's own fidelity: an earlier refactor reduced run_pure's snapshot to
    ``snapshot = state``, which made its assertions compare an object to itself —
    the whole suite stayed green while the harness silently checked nothing.
    """

    def sneaky_handler(ctx):
        # Bypasses frozen-ness; no effect is buffered, so result.effects cannot
        # explain the changed value.
        object.__setattr__(ctx.state.players[0], "ka", 999_999)
        return []
        yield  # pragma: no cover - make this a generator

    st = _harness_state(ka=5000)
    with pytest.raises(AssertionError, match="mutated the INPUT state"):
        run_pure(sneaky_handler, scripted(), state=st)


def test_run_pure_catches_a_readonly_collection_downgrade():
    """Swapping a read-only collection for a mutable one must fail the harness.

    This is the R2 false floor reopening: `map.tenancy` going from
    `MappingProxyType` back to a plain `dict` restores the write path the frozen
    graph exists to close. Both flatten to the same JSON, so the harness's value
    comparison alone cannot see it — only the type fingerprint can.
    """

    def downgrading_handler(ctx):
        object.__setattr__(ctx.state.map, "tenancy", {1: 0})
        return []
        yield  # pragma: no cover - make this a generator

    st = dataclasses.replace(
        _harness_state(ka=5000),
        map=MapState(tenancy=MappingProxyType({1: 0})),
    )
    with pytest.raises(AssertionError, match="changed the TYPE"):
        run_pure(downgrading_handler, scripted(), state=st)


def test_run_pure_catches_result_state_unexplained_by_effects():
    """Assertion (b) must be able to fail — it is the harness's real purity claim.

    Proves (b) is wired up and can fire: a `result.state` carrying a change the
    effect log does not account for is rejected.

    Scope limit, stated honestly: this does NOT pin (b)'s *baseline independence*.
    The tamper here diverges from any replay, vacuous baseline or not, so this test
    still passes if the baseline is reverted to the driver's own input. Guarding
    that specific regression needs a handler whose result matches a same-object
    replay but not an independent one — an argument for keeping the
    `_state_from_dict(snapshot)` baseline on the strength of the reasoning in
    `tests/helpers.py`, not on this test alone.
    """
    from engine.effects import MoneyChange

    def honest_handler(ctx):
        ctx.apply(MoneyChange(-100))
        return []
        yield  # pragma: no cover - make this a generator

    st = _harness_state(ka=5000)

    # Patch the DRIVER's commit so `result.state` carries a change the effect log
    # does not account for, while the input state stays pristine — assertion (a)
    # must not fire, leaving (b) as the only thing that can catch this.
    real_commit = engine.effects.commit

    def commit_with_extra_change(state, effects):
        result = real_commit(state, effects)
        tampered = dataclasses.replace(
            result.state,
            players=(dataclasses.replace(result.state.players[0], ka=42),)
            + tuple(result.state.players[1:]),
        )
        return dataclasses.replace(result, state=tampered)

    with mock.patch.object(engine.effects, "commit", commit_with_extra_change):
        with pytest.raises(AssertionError, match="NOT explained by result.effects"):
            run_pure(honest_handler, scripted(), state=st)


def test_run_pure_passes_a_well_behaved_handler():
    # A handler that changes state ONLY through effects passes the harness cleanly,
    # and the harness returns the EngineResult for further assertions.
    from engine.effects import MoneyChange

    def good_handler(ctx):
        ctx.apply(MoneyChange(-100))
        return []
        yield  # pragma: no cover - make this a generator

    st = _harness_state(ka=5000)
    result = run_pure(good_handler, scripted(), state=st)
    assert result.status == "completed"
    assert result.effects == [MoneyChange(-100)]
    assert result.state.players[0].ka == 4900
    assert st.players[0].ka == 5000  # input state untouched


def test_run_pure_holds_on_cancel_identity():
    # On cancel the driver returns the ORIGINAL state object with empty effects;
    # both harness assertions hold and the cancel-identity check passes.
    from engine.effects import MoneyChange

    def cancelling_handler(ctx):
        ctx.apply(MoneyChange(-100))  # buffered but discarded on the cancel below
        yield PromptInt("amount", min=1, max=9, cancellable=True)
        return []

    st = _harness_state(ka=5000)
    result = run_pure(cancelling_handler, scripted(CANCEL), state=st)
    assert result.status == "cancelled"
    assert result.state is st  # original object handed back unchanged
    assert result.effects == []

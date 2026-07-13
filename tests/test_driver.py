"""Contract tests for the interaction protocol + synchronous driver (U4 — THE SPINE).

Written proof-first: this file is authored BEFORE ``engine/interactions.py`` exists,
run to observe a red failure (import error), then the module is implemented to green.

The driver's job is to advance a handler generator, turn each yielded Interaction
into a response obtained from a scripted ``input_source`` callable, and ``.send()``
that response back. It enforces validation/re-prompting, cancellation via
``gen.throw(Cancelled)``, and atomic effect commit/discard.
"""

from __future__ import annotations

import pytest

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
# Scenario 5 — StartCombat / LoadSubState raise NotImplementedError           #
# --------------------------------------------------------------------------- #
def test_startcombat_raises_not_implemented():
    def handler(ctx):
        yield StartCombat(fighters=["g1"], arena="alley")
        return []

    with pytest.raises(NotImplementedError):
        run(handler, scripted())


def test_loadsubstate_raises_not_implemented():
    def handler(ctx):
        yield LoadSubState(kind="safecrack", params={})
        return []

    with pytest.raises(NotImplementedError):
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

"""Contract tests for ``run_option()`` (T7) — the location-aware option dispatcher.

Proof-first: authored BEFORE ``engine.actions.run_option`` exists, run to observe a red
failure (ImportError), then implemented to green.

``run_option`` is the single entry point that takes a loaded ``Location`` + an option id +
a ``GameState`` and returns exactly one :class:`EngineResult`. It owns the three dispatch
paths and the generic location lifecycle events:

* **guard-denied** -> a ``"blocked"`` result: ``OptionDenied`` event + ``DeniedResult``
  payload, ZERO effects, the UNCHANGED input state object;
* **consequence option** -> convert dicts + commit -> ``"completed"`` result with the
  committed effects and a ``LocationActionCompleted`` event;
* **handler option** -> delegate to :func:`engine.interactions.run`, then append the
  generic lifecycle event (``LocationActionCompleted`` on clean completion,
  ``LocationActionCancelled`` on driver-cancel).

Lifecycle events are emitted by ``run_option`` (location-aware), NOT by the bare ``run``
driver (which stays handler-protocol-focused and location-agnostic).
"""

from __future__ import annotations

import pytest

from engine.actions import DeniedResult, EngineResult, run_option
from engine.effects import MoneyChange, MsChange, SetTenancy
from engine.events import (
    LocationActionCancelled,
    LocationActionCompleted,
    OptionDenied,
)
from engine.interactions import CANCEL, Confirm, PromptInt, ShowMessage
from engine.locations import Location, Option
from engine.state import Clock, Config, Gangster, GameState, MapState, Player, freeze


# --------------------------------------------------------------------------- #
# Fixtures / builders                                                          #
# --------------------------------------------------------------------------- #
def _state(*, ka=5000, ln=2, active=0, players=1, tenancy=None):
    # The graph is frozen (R1/R2): build the players tuple with the active player's
    # ln already in place rather than assigning it after construction.
    plist = tuple(
        Player(ka=ka, roster=(Gangster(),), last_location=ln if i == active else 0)
        for i in range(players)
    )
    return GameState(
        players=plist,
        clock=Clock(active_player=active, player_count=players),
        config=Config(
            formula_params=freeze({"fnm": {"base": 50, "overrides": {1: -50}}})
        ),
        map=MapState(tenancy=freeze(tenancy or {})),
    )


def _scripted(*answers):
    it = iter(answers)

    def source(interaction):
        return next(it)

    return source


# --------------------------------------------------------------------------- #
# Unknown option id                                                           #
# --------------------------------------------------------------------------- #
def test_unknown_option_raises_value_error():
    loc = Location(key="slw", options=[Option(id="rent")])
    with pytest.raises(ValueError):
        run_option(loc, "nope", _state(), ln=2, input_source=_scripted())


# --------------------------------------------------------------------------- #
# Guard-denied path                                                           #
# --------------------------------------------------------------------------- #
def test_guard_denied_returns_blocked_with_no_effects_and_unchanged_state():
    # tenancy guard fails: tile 2 taken by a different player -> option denied.
    loc = Location(
        key="slw",
        options=[
            Option(
                id="rent",
                guard={"var": "tenancy", "op": "=", "value": 0},
                on_denied="locations.slw.no_room",
                consequences=[{"type": "ms_change", "amount": 0}],
            )
        ],
    )
    st = _state(ln=2, tenancy={2: 1})
    result = run_option(loc, "rent", st, ln=2, input_source=_scripted())

    assert isinstance(result, EngineResult)
    assert result.status == "blocked"
    assert result.effects == []
    assert result.state is st  # exact unchanged input object
    assert result.events == [
        OptionDenied(
            location_key="slw", option_id="rent", reason_key="locations.slw.no_room"
        )
    ]
    assert result.payload == DeniedResult(
        location_key="slw",
        option_id="rent",
        reason_key="locations.slw.no_room",
        guard={"var": "tenancy", "op": "=", "value": 0},
    )


def test_guard_denied_ln_participates_in_guard_context():
    # Same guard, but tile 5 is the one occupied; ln=5 must be the tile consulted.
    loc = Location(
        key="slw",
        options=[
            Option(
                id="rent",
                guard={"var": "tenancy", "op": "=", "value": 0},
                consequences=[{"type": "ms_change", "amount": 0}],
            )
        ],
    )
    st = _state(ln=5, tenancy={5: 1})
    result = run_option(loc, "rent", st, ln=5, input_source=_scripted())
    assert result.status == "blocked"

    # ln=2 (a free tile) -> the same option now PASSES the guard.
    st2 = _state(ln=2, tenancy={5: 1})
    result2 = run_option(loc, "rent", st2, ln=2, input_source=_scripted())
    assert result2.status == "completed"


# --------------------------------------------------------------------------- #
# Consequence path                                                            #
# --------------------------------------------------------------------------- #
def test_consequence_option_commits_effects_and_completes():
    loc = Location(
        key="slw",
        options=[
            Option(
                id="leave",
                consequences=[
                    {"type": "ms_change", "amount": -3},
                    {"type": "money_change", "amount": 10},
                ],
            )
        ],
    )
    st = _state(ka=5000, ln=2)
    result = run_option(loc, "leave", st, ln=2, input_source=None)

    assert result.status == "completed"
    assert result.effects == [MsChange(-3), MoneyChange(10)]
    assert result.events == [
        LocationActionCompleted(location_key="slw", option_id="leave")
    ]
    # Effects were committed against a fresh copy: input unchanged, new state mutated.
    assert st.players[0].ka == 5000  # input untouched
    assert result.state.players[0].ka == 5010
    assert result.state is not st


def test_consequence_option_with_bad_config_raises():
    loc = Location(
        key="slw",
        options=[Option(id="oops", consequences=[{"type": "no_such_effect"}])],
    )
    with pytest.raises(ValueError):
        run_option(loc, "oops", _state(), ln=2, input_source=None)


# --------------------------------------------------------------------------- #
# Handler path                                                                #
# --------------------------------------------------------------------------- #
def _rent_like_handler(ctx):
    """A tiny handler mirroring the slw.rent shape (prompt -> effects -> return)."""
    yield ShowMessage("quote", {"price": 50})
    x = yield PromptInt("months", min=0, max=999)
    if x <= 0:
        return []
    ctx.apply(MoneyChange(-50 * x))
    ctx.apply(SetTenancy(ctx.state.players[ctx.state.clock.active_player].last_location))
    yield ShowMessage("success")
    return []


def _cancellable_handler(ctx):
    yield Confirm("sure?")
    x = yield PromptInt("n", min=0, max=9, cancellable=True)
    ctx.apply(MoneyChange(-x))
    return []


def test_handler_option_completes_and_appends_lifecycle_event():
    loc = Location(key="slw", options=[Option(id="rent", handler=_rent_like_handler)])
    st = _state(ka=5000, ln=2)
    result = run_option(loc, "rent", st, ln=2, input_source=_scripted(2))

    assert result.status == "completed"
    assert result.effects == [MoneyChange(-100), SetTenancy(2)]
    assert result.state.players[0].ka == 4900
    # The generic lifecycle event is APPENDED after the handler's own events (none here).
    assert result.events[-1] == LocationActionCompleted(
        location_key="slw", option_id="rent"
    )


def test_handler_option_requires_input_source():
    loc = Location(key="slw", options=[Option(id="rent", handler=_rent_like_handler)])
    with pytest.raises((ValueError, TypeError)):
        run_option(loc, "rent", _state(), ln=2, input_source=None)


def test_handler_cancel_appends_cancelled_event_with_empty_effects():
    loc = Location(
        key="slw", options=[Option(id="c", handler=_cancellable_handler)]
    )
    st = _state(ka=5000, ln=2)
    # Confirm -> True, then cancel the PromptInt -> driver unwinds the handler.
    result = run_option(loc, "c", st, ln=2, input_source=_scripted(True, CANCEL))

    assert result.status == "cancelled"
    assert result.effects == []
    assert result.state is st  # driver returns the ORIGINAL state on cancel
    assert result.events == [
        LocationActionCancelled(location_key="slw", option_id="c")
    ]


def test_handler_completed_preserves_handler_payload():
    loc = Location(key="slw", options=[Option(id="rent", handler=_rent_like_handler)])
    result = run_option(
        loc, "rent", _state(ka=5000, ln=2), ln=2, input_source=_scripted(0)
    )
    # x<=0 quiet return: completed, zero effects, payload carries handler return.
    assert result.status == "completed"
    assert result.effects == []
    assert result.payload is not None  # HandlerResult passthrough preserved
    assert result.events[-1] == LocationActionCompleted(
        location_key="slw", option_id="rent"
    )

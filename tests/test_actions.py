"""Contract tests for the action-result types (T1).

Written proof-first: authored BEFORE ``engine/actions.py`` exists, run to observe a red
failure (import error), then the module is implemented to green.

These types are the action/result spine: :class:`EngineResult` carries the outcome of
running an option (new state, semantic events, primitive effects, a status, and an
optional payload/error); :class:`HandlerResult` and :class:`DeniedResult` are small
frozen carriers. All are pure, frozen data — no behavior.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.actions import DeniedResult, EngineResult, HandlerResult
from engine.state import GameState


def test_engine_result_is_frozen_with_defaults():
    state = GameState()
    result = EngineResult(
        state=state,
        events=[],
        effects=[],
        status="completed",
    )
    assert result.state is state
    assert result.events == []
    assert result.effects == []
    assert result.status == "completed"
    assert result.payload is None  # default
    assert result.error is None  # default
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "blocked"  # type: ignore[misc]


def test_engine_result_carries_payload_and_error():
    state = GameState()
    err = RuntimeError("boom")
    result = EngineResult(
        state=state,
        events=["e"],
        effects=["fx"],
        status="error",
        payload={"k": "v"},
        error=err,
    )
    assert result.payload == {"k": "v"}
    assert result.error is err
    assert result.events == ["e"]
    assert result.effects == ["fx"]


def test_handler_result_is_frozen_with_default():
    hr = HandlerResult()
    assert hr.returned is None  # default
    hr2 = HandlerResult(returned=["done"])
    assert hr2.returned == ["done"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        hr.returned = 1  # type: ignore[misc]


def test_denied_result_is_frozen_with_defaults():
    dr = DeniedResult(location_key="pub", option_id="drink")
    assert dr.location_key == "pub"
    assert dr.option_id == "drink"
    assert dr.reason_key is None  # default
    assert dr.guard is None  # default
    with pytest.raises(dataclasses.FrozenInstanceError):
        dr.reason_key = "x"  # type: ignore[misc]


def test_denied_result_carries_reason_and_guard():
    dr = DeniedResult(
        location_key="pub",
        option_id="drink",
        reason_key="not_enough_money",
        guard={"lhs": "ka", "op": ">=", "rhs": 100},
    )
    assert dr.reason_key == "not_enough_money"
    assert dr.guard == {"lhs": "ka", "op": ">=", "rhs": 100}

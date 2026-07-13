"""Contract tests for the semantic event catalog (T3).

Written proof-first: this file is authored BEFORE ``engine/events.py`` exists, run to
observe a red failure (import error), then the module is implemented to green.

Semantic **events** are pure-data audit/UI records — never applied to state, never part
of the replay log (replay = effects + logged RNG draws). Events may exist with no matching
effect (e.g. a blocked move emits :class:`MoveBlocked` but mutates nothing). Each event is
a frozen dataclass: immutable, no behavior, no reference to state-application machinery.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.events import (
    EnterLocation,
    LocationActionCancelled,
    LocationActionCompleted,
    LocationActionRejected,
    MoveBlocked,
    MoveStep,
    OptionDenied,
)


# --------------------------------------------------------------------------- #
# Construction — expected fields & defaults                                   #
# --------------------------------------------------------------------------- #
def test_move_step_fields():
    e = MoveStep(player=0, from_cell=18, to_cell=19, delta=1)
    assert (e.player, e.from_cell, e.to_cell, e.delta) == (0, 18, 19, 1)


def test_enter_location_fields():
    e = EnterLocation(player=1, from_cell=568, door_cell=569, delta=1, la=13, ln=5)
    assert (e.player, e.from_cell, e.door_cell, e.delta, e.la, e.ln) == (
        1,
        568,
        569,
        1,
        13,
        5,
    )


def test_move_blocked_fields():
    e = MoveBlocked(player=0, from_cell=18, target=None, delta=-40, reason="oob")
    assert (e.player, e.from_cell, e.target, e.delta, e.reason) == (
        0,
        18,
        None,
        -40,
        "oob",
    )


def test_move_blocked_target_may_be_a_cell():
    e = MoveBlocked(player=0, from_cell=18, target=17, delta=-1, reason="wall")
    assert e.target == 17
    assert e.reason == "wall"


def test_option_denied_defaults_reason_key_none():
    e = OptionDenied(location_key="slw", option_id="rent")
    assert e.location_key == "slw"
    assert e.option_id == "rent"
    assert e.reason_key is None


def test_option_denied_with_reason_key():
    e = OptionDenied(location_key="slw", option_id="rent", reason_key="too_poor")
    assert e.reason_key == "too_poor"


def test_location_action_completed_fields():
    e = LocationActionCompleted(location_key="slw", option_id="rent")
    assert (e.location_key, e.option_id) == ("slw", "rent")


def test_location_action_rejected_defaults_reason_key_none():
    e = LocationActionRejected(location_key="slw", option_id="rent")
    assert e.reason_key is None


def test_location_action_rejected_with_reason_key():
    e = LocationActionRejected(location_key="slw", option_id="rent", reason_key="no_room")
    assert e.reason_key == "no_room"


def test_location_action_cancelled_fields():
    e = LocationActionCancelled(location_key="slw", option_id="rent")
    assert (e.location_key, e.option_id) == ("slw", "rent")


# --------------------------------------------------------------------------- #
# Immutability — events are frozen (assignment raises)                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "event",
    [
        MoveStep(player=0, from_cell=18, to_cell=19, delta=1),
        EnterLocation(player=0, from_cell=568, door_cell=569, delta=1, la=13, ln=5),
        MoveBlocked(player=0, from_cell=18, target=None, delta=-40, reason="oob"),
        OptionDenied(location_key="slw", option_id="rent"),
        LocationActionCompleted(location_key="slw", option_id="rent"),
        LocationActionRejected(location_key="slw", option_id="rent"),
        LocationActionCancelled(location_key="slw", option_id="rent"),
    ],
)
def test_events_are_frozen(event):
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.player = 99  # type: ignore[misc]  # attribute may not exist; frozen still wins


def test_frozen_blocks_existing_field_assignment():
    e = OptionDenied(location_key="slw", option_id="rent")
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.location_key = "bnk"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# OptionDenied carries no raw guard data (guard debug lives on DeniedResult)  #
# --------------------------------------------------------------------------- #
def test_option_denied_has_no_guard_field():
    field_names = {f.name for f in dataclasses.fields(OptionDenied)}
    assert "guard" not in field_names


def test_option_denied_has_no_guard_attribute():
    e = OptionDenied(location_key="slw", option_id="rent")
    assert not hasattr(e, "guard")

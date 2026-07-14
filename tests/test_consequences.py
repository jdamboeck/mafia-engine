"""Strict YAML consequence conversion (T6): raw config dicts -> typed effects.

Proof-first tests for :mod:`engine.consequences`. Consequence dicts come from a game
config's declarative shell (an option's ``resolve.consequences`` list). Because a config
is authored at build time, a malformed consequence is a *config bug* and RAISES
``ValueError`` — it is never funnelled into an :class:`EngineResult`.
"""

from __future__ import annotations

import copy

import pytest

from engine.consequences import effect_from_dict, effects_from_dicts
from engine.effects import (
    AssignWeapon,
    MoneyChange,
    MsChange,
    ScoreAndRank,
    ScoreChange,
    SetEntryContext,
    SetPosition,
    StatChangeCapped,
    Teleport,
)


# --------------------------------------------------------------------------- #
# Known types convert to the expected effect dataclass (all six)             #
# --------------------------------------------------------------------------- #
def test_ms_change_converts():
    assert effect_from_dict({"type": "ms_change", "amount": 0}) == MsChange(amount=0)


def test_money_change_converts():
    assert effect_from_dict({"type": "money_change", "amount": -50}) == MoneyChange(
        amount=-50
    )


def test_score_change_converts():
    assert effect_from_dict({"type": "score_change", "amount": 3}) == ScoreChange(
        amount=3
    )


def test_stat_change_capped_converts():
    assert effect_from_dict(
        {"type": "stat_change_capped", "stat": "kraft", "amount": 5, "cap": 99}
    ) == StatChangeCapped(stat="kraft", amount=5, cap=99)


def test_stat_change_capped_missing_required_cap_raises():
    with pytest.raises(ValueError):
        effect_from_dict({"type": "stat_change_capped", "stat": "kraft", "amount": 5})


def test_assign_weapon_converts():
    assert effect_from_dict(
        {"type": "assign_weapon", "weapon": 5}
    ) == AssignWeapon(weapon=5)


def test_score_and_rank_converts():
    assert effect_from_dict(
        {"type": "score_and_rank", "amount": 2, "rank_divisor": 11.1}
    ) == ScoreAndRank(amount=2, rank_divisor=11.1)


def test_score_and_rank_missing_required_divisor_raises():
    with pytest.raises(ValueError):
        effect_from_dict({"type": "score_and_rank", "amount": 2})


def test_teleport_converts():
    assert effect_from_dict({"type": "teleport", "cell": 569}) == Teleport(cell=569)


def test_set_position_converts():
    assert effect_from_dict({"type": "set_position", "cell": 42}) == SetPosition(
        cell=42
    )


def test_set_entry_context_converts():
    assert effect_from_dict(
        {"type": "set_entry_context", "la": 1, "ln": 4}
    ) == SetEntryContext(la=1, ln=4)


# --------------------------------------------------------------------------- #
# Optional fields: present overrides default, absent uses the default        #
# --------------------------------------------------------------------------- #
def test_optional_player_present():
    assert effect_from_dict(
        {"type": "money_change", "amount": 10, "player": 1}
    ) == MoneyChange(amount=10, player=1)


def test_optional_player_absent_uses_default():
    assert effect_from_dict({"type": "money_change", "amount": 10}).player is None


# --------------------------------------------------------------------------- #
# Strict errors: unknown type / missing field / extra field / no type        #
# --------------------------------------------------------------------------- #
def test_unknown_type_raises_and_names_type():
    with pytest.raises(ValueError) as exc:
        effect_from_dict({"type": "explode", "amount": 1})
    msg = str(exc.value)
    assert "explode" in msg
    # error names at least one of the known types
    assert "ms_change" in msg


def test_missing_type_key_raises():
    with pytest.raises(ValueError):
        effect_from_dict({"amount": 1})


def test_missing_required_field_raises():
    with pytest.raises(ValueError) as exc:
        effect_from_dict({"type": "ms_change"})
    assert "amount" in str(exc.value)


def test_extra_unknown_field_raises():
    with pytest.raises(ValueError) as exc:
        effect_from_dict({"type": "ms_change", "amount": 1, "bogus": 9})
    assert "bogus" in str(exc.value)


# --------------------------------------------------------------------------- #
# Purity: inputs unmutated, no state touched                                 #
# --------------------------------------------------------------------------- #
def test_input_dict_not_mutated():
    raw = {"type": "money_change", "amount": 10, "player": 1}
    before = copy.deepcopy(raw)
    effect_from_dict(raw)
    assert raw == before


# --------------------------------------------------------------------------- #
# effects_from_dicts: order preserved, list of typed effects                 #
# --------------------------------------------------------------------------- #
def test_effects_from_dicts_preserves_order():
    raws = [
        {"type": "ms_change", "amount": 1},
        {"type": "money_change", "amount": 2},
        {"type": "score_change", "amount": 3},
    ]
    assert effects_from_dicts(raws) == [
        MsChange(amount=1),
        MoneyChange(amount=2),
        ScoreChange(amount=3),
    ]


def test_effects_from_dicts_empty():
    assert effects_from_dicts([]) == []


def test_effects_from_dicts_does_not_mutate_inputs():
    raws = [{"type": "ms_change", "amount": 1}]
    before = copy.deepcopy(raws)
    effects_from_dicts(raws)
    assert raws == before

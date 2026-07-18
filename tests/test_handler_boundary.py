"""The handler-API read-only contract, stated as executable assertions (U6, R6).

CLAUDE.md § 5.2a says a config's handlers may touch only ``ctx.state`` (READ-ONLY),
``ctx.rng``, ``yield <Interaction>``, and ``ctx.apply(<Effect>)``. Until the state
graph was frozen, the "read-only" half of that sentence was enforced by a docstring:
a handler could write ``ctx.state.players[sp].ka += 100`` instead of applying a
``MoneyChange``, and the code would work perfectly in play while silently producing
a DIFFERENT result on replay — surfacing only when someone loaded a saved game.

This file is that contract's proof. It asserts the write fails at every depth a
handler can actually reach:

* a top-level field on a nested dataclass  (``player.ka``),
* a field one level deeper                 (``player.wanted.x5``),
* a nested collection                      (``config.formula_params``, ``map.tenancy``),
* the top-level player collection          (``state.players``).

The positive controls at the bottom matter just as much: reads still work, and a
legitimate ``commit`` of an effect still updates state. The graph is immutable, not
inert.

Why this is worth its own file: it is the greppable answer to "what stops a config
author from corrupting a save?" — see
docs/plans/2026-07-18-001-refactor-immutable-state-graph-plan.md (R1/R2/R6).
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
sys.path.insert(0, str(_CONFIG_DIR))

from setup import new_game  # noqa: E402

from engine.effects import MoneyChange, commit  # noqa: E402

SEED = 42


def _state():
    return new_game(
        seed=SEED, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )


# --------------------------------------------------------------------------- #
# R1 — a direct attribute write raises, at every depth                        #
# --------------------------------------------------------------------------- #
def test_top_level_player_field_write_raises():
    """The canonical mistake: bumping cash directly instead of applying MoneyChange."""
    state = _state()
    with pytest.raises(FrozenInstanceError):
        state.players[0].ka = 999


def test_nested_dataclass_field_write_raises():
    """One level deeper — the win flags a handler must set through an effect."""
    state = _state()
    with pytest.raises(FrozenInstanceError):
        state.players[0].wanted.x5 = True


def test_clock_field_write_raises():
    """The turn/calendar state is off-limits too (advance_turn returns a new state)."""
    state = _state()
    with pytest.raises(FrozenInstanceError):
        state.clock.active_player = 1


# --------------------------------------------------------------------------- #
# R2 — nested collections are read-only (the "false floor" a shallow freeze     #
# would leave: a frozen dataclass still handing out a mutable dict/list)       #
# --------------------------------------------------------------------------- #
def test_nested_config_mapping_write_raises():
    state = _state()
    with pytest.raises(TypeError):
        state.config.formula_params["fnm"] = {}


def test_map_tenancy_mapping_write_raises():
    """Tenancy moves only via SetTenancy — never by writing the mapping."""
    state = _state()
    with pytest.raises(TypeError):
        state.map.tenancy[1] = 0


def test_players_collection_is_not_appendable():
    state = _state()
    with pytest.raises(AttributeError):
        state.players.append(object())


def test_roster_collection_is_not_appendable():
    state = _state()
    with pytest.raises(AttributeError):
        state.players[0].roster.append(object())


def test_a_mutable_collection_cannot_be_smuggled_in_at_construction():
    """R2 holds by construction, not by the caller's good manners.

    Handing a plain dict/list to a state dataclass must not reopen the write path —
    the graph coerces it to a read-only form.
    """
    from engine.state import Config, MapState

    cfg = Config(formula_params={"fnm": {1: -50}})
    with pytest.raises(TypeError):
        cfg.formula_params["fnm"] = {}
    # ...including one level down, where a shallow coercion would leave a hole.
    with pytest.raises(TypeError):
        cfg.formula_params["fnm"][1] = 0

    m = MapState(grid=[[1, 2], [3, 4]])
    with pytest.raises(AttributeError):
        m.grid.append([5, 6])


# --------------------------------------------------------------------------- #
# Positive controls — immutable, not inert                                    #
# --------------------------------------------------------------------------- #
def test_reads_still_work_at_every_depth():
    state = _state()
    assert isinstance(state.players[0].ka, int)
    assert state.players[0].wanted.x5 is False
    assert state.map.tenancy.get(1) is None
    assert "fnm" in state.config.formula_params


def test_effect_driven_write_still_updates_state():
    """The sanctioned path still works — and leaves the caller's state untouched."""
    state = _state()
    before = state.players[0].ka

    result = commit(state, [MoneyChange(-500)])

    assert result.state.players[0].ka == before - 500  # the effect landed
    assert state.players[0].ka == before  # the input is unchanged

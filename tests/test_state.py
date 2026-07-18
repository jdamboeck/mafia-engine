"""Tests for the modular GameState dataclasses (U3, scaffolding-only).

Test expectation: happy-path construction only for stub subsystems.
"""

from dataclasses import FrozenInstanceError, replace

import pytest

from engine.state import (
    Clock,
    CombatState,
    Config,
    Debt,
    Flags,
    GameState,
    MapState,
    Gangster,
    Player,
    Wanted,
)


def test_gamestate_default_construction():
    gs = GameState()
    assert gs.clock.year == 1928
    assert gs.clock.end_year == 1978
    assert gs.clock.player_count == 1
    assert gs.config.score_mult == 1.0
    assert gs.players == ()
    assert isinstance(gs.map, MapState)
    assert isinstance(gs.combat, CombatState)
    assert isinstance(gs.config, Config)
    assert isinstance(gs.flags, Flags)
    assert isinstance(gs.clock, Clock)


def test_player_defaults():
    p = Player()
    assert p.po == 18
    assert p.rank == 1
    assert p.nr == 1
    assert p.ms == 0
    assert p.gf == 0.0
    assert p.roster == ()
    assert p.last_location == 0
    assert p.last_la == 0


def test_gangster_defaults():
    g = Gangster()
    assert g.energie == 5
    assert g.kraft == 0
    assert g.intelligenz == 0
    assert g.brutalitaet == 0


def test_debt_kraft_name_collision_separation():
    """The kr(sp)->debt rename removes collision with gangster stat kraft."""
    p = Player(roster=(Gangster(kraft=42),), debt=Debt(amount=500))
    assert p.debt.amount == 500
    assert p.roster[0].kraft == 42
    # independently addressable — no aliasing between the two
    assert p.debt.amount != p.roster[0].kraft


def test_coordinate_space_independence():
    """City map (40×25) and combat grid (40×13) are different spaces.

    Populated independently: a city grid never leaks into the combat grid. (Empty
    defaults are the interned ``()``, so identity cannot carry this — assert on
    separately-addressable content instead.)
    """
    m = MapState(grid=((1, 2), (3, 4)))
    c = CombatState(grid=((9,),))
    assert m.grid == ((1, 2), (3, 4))
    assert c.grid == ((9,),)
    assert m.grid != c.grid


def test_nested_default_isolation():
    """Two Player() instances must not share nested defaults."""
    p1 = Player()
    p2 = Player()
    assert p1.debt is not p2.debt
    assert p1.wanted is not p2.wanted

    # Frozen: a per-instance change is a NEW object, so siblings cannot be aliased.
    p1_richer = replace(p1, debt=Debt(amount=999))
    assert p1_richer.debt.amount == 999
    assert p1.debt.amount == 0
    assert p2.debt.amount == 0


def test_state_dataclasses_are_frozen():
    """R1: a direct attribute write anywhere in the graph raises."""
    for obj, field_name, value in (
        (Player(), "ka", 999),
        (Gangster(), "kraft", 42),
        (Debt(), "amount", 500),
        (Wanted(), "x5", True),
        (Clock(), "year", 1930),
        (Config(), "score_mult", 2.0),
        (Flags(), "loaded", True),
        (MapState(), "tenancy", {}),
        (GameState(), "players", ()),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field_name, value)


def test_frozen_construction_and_replace_still_work():
    """Freezing constrains writes, not construction: keyword build + replace work."""
    p = Player(name="Al", ka=5000, roster=(Gangster(name="Joe"),))
    assert p.ka == 5000
    assert p.roster[0].name == "Joe"

    bumped = replace(p, ka=p.ka + 100)
    assert bumped.ka == 5100
    assert p.ka == 5000  # original untouched

"""Tests for the modular GameState dataclasses (U3, scaffolding-only).

Test expectation: happy-path construction only for stub subsystems.
"""

from engine.state import (
    Clock,
    CombatState,
    Config,
    Flags,
    GameState,
    MapState,
    Gangster,
    Player,
)


def test_gamestate_default_construction():
    gs = GameState()
    assert gs.clock.year == 1928
    assert gs.clock.end_year == 1978
    assert gs.clock.player_count == 1
    assert gs.config.score_mult == 1.0
    assert gs.players == []
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
    assert p.roster == []
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
    p = Player()
    p.roster.append(Gangster())
    p.debt.amount = 500
    p.roster[0].kraft = 42
    assert p.debt.amount == 500
    assert p.roster[0].kraft == 42
    # independently addressable — no aliasing between the two
    assert p.debt.amount != p.roster[0].kraft


def test_coordinate_space_independence():
    """City map (40×25) and combat grid (40×13) are different spaces."""
    m = MapState()
    c = CombatState()
    assert m.grid == []
    assert c.grid == []
    assert m.grid is not c.grid


def test_nested_default_isolation():
    """Two Player() instances must not share nested mutable defaults."""
    p1 = Player()
    p2 = Player()
    assert p1.roster is not p2.roster
    assert p1.debt is not p2.debt
    assert p1.wanted is not p2.wanted

    p1.roster.append(Gangster())
    assert p2.roster == []

    p1.debt.amount = 999
    assert p2.debt.amount == 0

    p1.wanted.x5 = True
    assert p2.wanted.x5 is False

"""Tests for the modular GameState dataclasses (U3, scaffolding-only).

Test expectation: happy-path construction only for stub subsystems.
"""

from dataclasses import FrozenInstanceError, replace

import pytest

from engine.state import (
    Clock,
    CombatState,
    Config,
    GameState,
    Player,
    json_safe,
)
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.state import (
    SCHEMA,
    Business,
    Debt,
    Wanted,
    business,
    debt,
    hired_ids,
    next_rank,
    values_of,
)


def test_gamestate_default_construction():
    gs = GameState()
    assert gs.clock.year == 1925  # mf-prg.bas:1000 ja=1925
    assert gs.clock.end_year == 1978
    assert gs.clock.player_count == 1
    assert gs.players == ()
    assert isinstance(gs.combat, CombatState)
    assert isinstance(gs.config, Config)
    assert isinstance(gs.clock, Clock)
    assert gs.values == {}


def test_player_defaults():
    p = Player()
    assert p.po == 18
    assert p.rank == 1
    assert next_rank(p) == 1  # the declared default, read through the config accessor
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
    p = Player(roster=(Gangster(kraft=42),), values=values_of(Debt(amount=500)))
    assert debt(p).amount == 500
    assert p.roster[0].attrs["kraft"] == 42
    # independently addressable — no aliasing between the two
    assert debt(p).amount != p.roster[0].attrs["kraft"]


def test_nested_default_isolation():
    """Two Player() instances must not share their value maps."""
    p1 = Player()
    p2 = Player()

    # Frozen: a per-instance change is a NEW object, so siblings cannot be aliased.
    p1_richer = replace(p1, values=values_of(Debt(amount=999)))
    assert debt(p1_richer).amount == 999
    assert debt(p1).amount == 0
    assert debt(p2).amount == 0


def test_state_dataclasses_are_frozen():
    """R1: a direct attribute write anywhere in the graph raises."""
    for obj, field_name, value in (
        (Player(), "ka", 999),
        (Gangster(), "kraft", 42),
        (Debt(), "amount", 500),
        (Wanted(), "x5", True),
        (Clock(), "year", 1930),
        (Config(), "formula_params", {}),
        (GameState(), "players", ()),
        (GameState(), "values", {}),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field_name, value)


def test_hired_set_defaults_empty_and_reads_back_from_the_global_map():
    """The global sg(i) set (mf-prg.bas:12106/12165) is one bool per candidate in the
    global value map; the accessor reads it back as ascending ids."""
    assert hired_ids(GameState()) == ()
    marked = GameState(values={"hired.29": True, "hired.2": True, "hired.5": True})
    assert hired_ids(marked) == (2, 5, 29)


def test_frozen_construction_and_replace_still_work():
    """Freezing constrains writes, not construction: keyword build + replace work."""
    p = Player(name="Al", ka=5000, roster=(Gangster(name="Joe"),))
    assert p.ka == 5000
    assert p.roster[0].name == "Joe"

    bumped = replace(p, ka=p.ka + 100)
    assert bumped.ka == 5100
    assert p.ka == 5000  # original untouched


# --------------------------------------------------------------------------- #
# U2 groundwork: clock month granularity, the shop tile, json_safe           #
# round-trips (KTD-4, KTD-7).                                                 #
# --------------------------------------------------------------------------- #
def test_clock_default_has_month_zero():
    """Month is a first-class, visible clock field (KTD-4), defaulting to 0."""
    c = Clock()
    assert c.month == 0
    assert c.year == 1925  # mf-prg.bas:1000 ja=1925


def test_clock_month_is_frozen():
    with pytest.raises(FrozenInstanceError):
        Clock().month = 5  # pyright: ignore[reportAttributeAccessIssue]  # the write is the test: it must raise


def test_business_shop_tile_replaces_shop_owner_bool():
    """Business tracks WHICH tile is owned (0 = none), not a bare boolean (KTD-7)."""
    b = Business()
    assert b.shop_tile == 0  # default: no shop
    assert b.shop_capital == 0
    owned = replace(b, shop_tile=2, shop_capital=1500)
    assert owned.shop_tile == 2
    assert owned.shop_capital == 1500
    assert b.shop_tile == 0  # original untouched (frozen graph)


def test_new_state_fields_survive_json_safe_round_trip():
    """shop_tile + clock.month survive the json_safe walk (the persistence primitive)."""
    p = Player(values=values_of(Business(shop_tile=3, shop_capital=750)))
    state = GameState(players=(p,), clock=Clock(year=1930, month=7))

    safe = json_safe(state)
    assert safe["players"][0]["values"]["business.shop_tile"] == 3
    assert safe["players"][0]["values"]["business.shop_capital"] == 750
    assert safe["clock"]["month"] == 7
    assert safe["clock"]["year"] == 1930
    # json_safe erases read-only types to plain containers, JSON-shaped.
    assert isinstance(safe["players"], list)
    assert isinstance(safe["players"][0], dict)


def test_new_state_fields_survive_persistence_round_trip(tmp_path, mafia_config):
    """The full persistence round-trip (not just json_safe) preserves the new fields."""
    from engine import persistence

    p = Player(
        values={**SCHEMA.player_defaults(), **values_of(Business(shop_tile=1, shop_capital=200))}
    )
    state = GameState(
        players=(p,), clock=Clock(year=1932, month=3), values=SCHEMA.global_defaults()
    )

    save_path = tmp_path / "u2_groundwork.jsonl"
    persistence.save_game(save_path, state, effect_log=[], rng_log=[], seed=1)
    loaded = persistence.load_game(save_path, mafia_config.registries)

    assert business(loaded.state.players[0]) == Business(shop_tile=1, shop_capital=200)
    assert loaded.state.clock.month == 3
    assert loaded.state.clock.year == 1932
    assert loaded.state == state

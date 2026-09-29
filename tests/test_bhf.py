"""Tests for the bhf (Bahnhof, railway station) handlers — ports ``mf-prg.bas:19000-19050``.

::

    19005 onwgoto19010,19050,19015
    19010 ln=5:la=2:goto3000
    19015 iftp(sp)<>1thenprint"kein postzug zu sehen...":goto1100
    19016 ifgz(sp)<3thenprint"du hast zu wenig gangster!":tp(sp)=0:goto1100
    19020 syslh,"pzug-pic"
    19025-19027 du stuermst in den panzerwaggon und ... drei nette herren ...:gosub1100
    19030 bn$(0)="wachen":gz(0)=3:w=7:e=30:kf$="kpzug":gosub5000:ifs=2goto26020
    19040 goto20050
    19050 w=1:goto18035
    20050 p=int(rnd(1)*3000)+4000-500*(la=10andln=1):x=tp(sp)
    20051 if(x=1andla=9)or(x=2andla=10andln=2)or(x=3andla=13)thentp(sp)=0:p=p+3000
    20055-20060 du hast es geschafft! deine beute betraegt p $!:ka(sp)=ka(sp)+p:x=4:gosub1160

The station pub runs through the turn runner (the ``goto3000`` moves the visit); the
rest through ``run_pure`` with the strict ``StubRng``. Every refusal leaves the state as
it was, except the one that clears the tip.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.effects import (
    Jail,
    JobClear,
    ScoreAndRank,
    TipClear,
)
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.setup import load_combat_backdrop
from data.game_configs.mafia_1920s.state import (
    SCHEMA,
    Wanted,
    contraband,
    tip_target,
    values_of,
)
from engine.combat import CombatResult
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, SetEntryContext, SetMovementPoints, Teleport
from engine.interactions import (
    Ctx,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptInt,
    StartCombat,
)
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from engine.turns import WALKING, TurnRunner
from tests.helpers import StubRng, run_pure, scripted
from tests.test_turn_runner import _approach, _door

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)
_PARAMS = {**load_config(_CONFIG_DIR / "config.yaml")["formula_params"], "score_mult": 1.0}

_LA = 9  # bhf's location id (:3105)
_PUB_LA = 2
_SURRENDER_FIGHT = ("surrender", None)
#: The capture menu's surrender key (``:26022-26023``, 0-based here).
_SURRENDER = 2

_G = Gangster(name="alcapone", energie=40, kraft=30, intelligenz=40, brutalitaet=30)
_GANG3 = (_G, _G, _G)

_SENTENCED = [
    TipClear(),
    ScoreAndRank(amount=2, rank_divisor=11.1),
    Jail(months=1),
    SetMovementPoints(0),
    JobClear(),
    ScoreAndRank(amount=-10, rank_divisor=11.1),
    Teleport(911),
]


def _rules() -> dict[str, str]:
    return {
        "intelligence_or_30": "faithful",
        "shared_direction_memory": "faithful",
        "stale_bribe_price": "faithful",
        "flight_odds_by_seat": "faithful",
        "chief_bribe_negative_months": "faithful",
        "chief_bribe_empty_answer": "faithful",
    }


def _state(*, tip: int = 1, bribe_months: int = 0, **fields) -> GameState:
    fields.setdefault("ka", 10_000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("ms", 20)
    fields.setdefault("rank", 1)
    fields.setdefault("roster", _GANG3)
    values = {
        **SCHEMA.player_defaults(),
        **values_of(Wanted(bribe_months=bribe_months), tip_target=tip),
    }
    player = Player(name="alcapone", values=values, last_la=_LA, last_location=1, **fields)
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS, house_rules=_rules()),
    )


def _run(key: str, state: GameState, answers=(), draws=()):
    rng = StubRng(*draws)
    source = scripted(*answers)
    result = run_pure(HANDLERS[f"bhf.{key}"], source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _won_fight(monkeypatch):
    """Stand in for the guards' fight: won. Returns the encounters fought."""
    fought = []

    def fight(ctx, encounter, **kwargs):
        fought.append((encounter.key, kwargs))
        return CombatResult(winner=1, losses=(0, 3))
        yield  # a generator, as run_encounter is

    module = sys.modules[HANDLERS["bhf.mail_train"].__module__]
    monkeypatch.setattr(module, "run_encounter", fight)
    return fought


# --------------------------------------------------------------------------- #
# The mail train's two refusals — :19015, :19016                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tip", [0, 2, 3, 4, 5])
def test_without_tip_1_the_mail_train_is_refused_with_the_sources_text(tip):
    """:19015 ``iftp(sp)<>1thenprint"kein postzug zu sehen...":goto1100``: nothing
    changes, the tip held stays."""
    st = _state(tip=tip)
    result, source = _run("mail_train", st)
    assert result.effects == []
    assert result.state == st
    assert source.message_keys() == ["locations.bhf.no_train"]


@pytest.mark.parametrize("gang", [0, 1, 2])
def test_a_gang_of_fewer_than_3_is_refused_and_loses_the_tip(gang):
    """:19016 ``ifgz(sp)<3thenprint"du hast zu wenig gangster!":tp(sp)=0``: the tip
    is gone (house rule ``mail_train_small_gang_loses_tip``), nothing else changes."""
    st = _state(roster=(_G,) * gang)
    result, source = _run("mail_train", st)
    assert result.effects == [TipClear()]
    assert tip_target(result.state.players[0]) == 0
    assert source.message_keys() == ["locations.bhf.too_few"]
    assert result.state.players[0].ka == st.players[0].ka


def _fight_of(state: GameState):
    """The ``StartCombat`` the mail train yields for ``state``, or ``None``."""
    gen = HANDLERS["bhf.mail_train"](Ctx(state=state, rng=StubRng()))
    try:
        interaction = next(gen)
        while not isinstance(interaction, StartCombat):
            interaction = gen.send(None)
    except StopIteration:
        return None
    finally:
        gen.close()
    return interaction


@pytest.mark.parametrize(("gang", "fights"), [(2, False), (3, True), (4, True)])
def test_a_gang_of_exactly_3_can_rob_the_mail_train(gang, fights):
    """``gz(sp)<3`` refuses 2; the boss and two more storm the waggon."""
    assert (_fight_of(_state(roster=(_G,) * gang)) is not None) == fights


def test_the_guards_are_three_wachen_with_maschinenpistolen_on_kpzug():
    """:19030 ``bn$(0)="wachen":gz(0)=3:w=7:e=30:kf$="kpzug"``."""
    start = _fight_of(_state())
    assert start is not None
    scenario = start.scenario
    assert scenario is not None and scenario.sides is not None
    assert [(f.name, f.weapon, f.vitality) for f in scenario.sides[1]] == [("wachen", 7, 30)] * 3
    assert scenario.grid == load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / "kpzug.yaml")


# --------------------------------------------------------------------------- #
# The fight's outcome — :19030 ifs=2goto26020, :19040 goto20050                #
# --------------------------------------------------------------------------- #
def test_a_lost_mail_train_fight_leads_to_capture():
    """:19030 ``ifs=2goto26020``: the arrest menu, no police fight. Surrender at rank
    1: the trial, which clears the tip (:26045)."""
    st = _state()
    result, source = _run("mail_train", st, answers=(_SURRENDER_FIGHT, _SURRENDER), draws=(0,))
    assert result.effects == _SENTENCED
    keys = source.message_keys()
    assert keys[0] == "locations.bhf.storm"
    assert "police.caught" in keys
    assert "locations.bhf.loot" not in keys
    assert tip_target(result.state.players[0]) == 0


def test_after_a_lost_fight_the_auto_bribe_charges_0_and_the_tip_survives():
    """:26021 -> :26037 reads the ``p`` the fight left, which the port reports as 0
    (house-rules header); bribed, the turn goes on and the tip is still 1 (only the
    trial clears it), so the train can be tried again."""
    st = _state(bribe_months=1)
    # the :26021 skip roll (1: to :26037), then the :26038 bribe roll (let go)
    result, source = _run("mail_train", st, answers=(_SURRENDER_FIGHT,), draws=(1, 1))
    assert result.effects == [MoneyChange(0)]
    assert tip_target(result.state.players[0]) == 1
    assert source.message_keys()[-1] == "police.let_go"


@pytest.mark.parametrize(("roll", "p"), [(0, 7000), (1234, 8234), (2999, 9999)])
def test_a_won_mail_train_fight_pays_the_dossiers_reward(monkeypatch, roll, p):
    """:20050 ``p=int(rnd(1)*3000)+4000``; :20051 tip 1 at la=9 clears the tip and adds
    3000: 7000..9999. :20060 ``ka(sp)=ka(sp)+p:x=4:gosub1160``."""
    fought = _won_fight(monkeypatch)
    st = _state()
    rng = StubRng(roll)
    source = scripted()
    result = run_pure(HANDLERS["bhf.mail_train"], source, state=st, rng=rng)
    assert rng.calls == [("range", 3000)]
    assert fought == [("bhf_guards", {})]
    assert result.effects == [
        TipClear(),
        MoneyChange(p),
        ScoreAndRank(amount=4, rank_divisor=11.1),
    ]
    (loot,) = [m for m in source.messages() if m.key == "locations.bhf.loot"]
    assert loot.params == {"p": p}
    player = result.state.players[0]
    assert (player.ka, tip_target(player), player.gf) == (10_000 + p, 0, 50.0 + 4)


# --------------------------------------------------------------------------- #
# Pickpocketing — :19050 w=1:goto18035                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("roll", "item", "cash"),
    [(0, "handbag", 0), (1, "camera", 50), (2, "pearls", 0), (3, "watch", 100)],
)
def test_pickpocketing_at_the_station_is_the_subways_body_with_w_1(roll, item, cash):
    """``w=1`` and ``la=9``: :18045's index is the roll alone, items 0..3."""
    st = _state()
    result, source = _run("pickpocket", st, answers=(1,), draws=(0, 10, roll))
    expected: list[object] = [ScoreAndRank(amount=1, rank_divisor=11.1)]
    if cash:
        expected.append(MoneyChange(cash))
    assert result.effects == expected
    assert source.message_keys()[-1] == f"locations.sub.loot_{item}"


def test_a_caught_station_pickpocket_is_arrested_with_the_station_doors_p():
    """:18042 ``goto26020`` with the map step's ``p``: 52224 plus the station's door,
    cell 68 (house rule ``stale_bribe_price``)."""
    st = _state(ka=52_224 + 68, bribe_months=1)
    result, source = _run("pickpocket", st, answers=(1,), draws=(0, 0, 1, 1))
    assert result.effects == [
        ScoreAndRank(amount=1, rank_divisor=11.1),
        MoneyChange(-(52_224 + 68)),
    ]
    assert source.message_keys()[-1] == "police.let_go"


# --------------------------------------------------------------------------- #
# The station pub — :19010 ln=5:la=2:goto3000                                  #
# --------------------------------------------------------------------------- #
def test_the_station_pub_moves_the_entry_context_to_the_pub_on_tile_5():
    """The handler only sets ``la=2:ln=5``; the runner opens the menu (``goto3000``)."""
    st = _state()
    result, source = _run("pub", st)
    assert result.effects == [SetEntryContext(la=_PUB_LA, ln=5)]
    assert source.seen == []


def _walking(*, ms: int = 60) -> tuple[TurnRunner, str]:
    """A runner on the map beside the station's door, and the step into it."""
    door, la = _door("bhf")
    assert (door, la) == (68, _LA)
    start, into = _approach(door)
    state = _CONFIG.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    assert state is not None
    player = replace(state.players[0], po=start, ms=ms, ka=10_000)
    state = replace(state, players=(player,), clock=replace(state.clock, turn_phase=WALKING))
    runner = TurnRunner(
        state, StubRng(150, 7), city=_CONFIG.city, shells=_CONFIG.shells, turn_menu=None
    )
    return runner, into


def _into_the_station_pub(runner: TurnRunner, into: str):
    """Step through the door, pick the station pub; return the generator at the pub's
    menu and that menu."""
    gen = runner.run(WALKING)
    assert isinstance(next(gen), MapMove)
    menu = gen.send(into)
    assert isinstance(menu, LocationMenu) and menu.location == "bhf"
    done = gen.send(menu.options.index("pub"))
    assert isinstance(done, OptionDone) and done.location == "bhf"
    pub = gen.send(None)
    return gen, pub


def test_the_station_pub_opens_the_pub_menu_on_tile_5_in_the_same_visit():
    runner, into = _walking()
    gen, pub = _into_the_station_pub(runner, into)
    assert isinstance(pub, LocationMenu)
    assert (pub.location, pub.ln) == ("pub", 5)
    assert pub.options == ("drink", "recruit", "tip", "job", "leave")
    # No second door charge: the handler ran on the points from before the door.
    assert runner.state.players[0].ms == 60
    gen.close()


def test_leaving_the_station_pub_lands_on_the_map_for_10_points_with_the_pub_as_previous():
    """The pub's leave (:3045 ``ms=ms-5``), then the door's (:2060): 10. :2055 writes
    ``ll(sp)=20*la+ln`` with the pub's ``la=2``, ``ln=5``."""
    runner, into = _walking()
    gen, pub = _into_the_station_pub(runner, into)
    done = gen.send(pub.options.index("leave"))
    assert isinstance(done, OptionDone) and done.location == "pub"
    assert isinstance(gen.send(None), MapMove)
    player = runner.state.players[0]
    assert player.ms == 60 - 10
    assert player.previous_tile == (_PUB_LA, 5)
    gen.close()


def test_a_pub_action_from_the_station_ends_the_visit():
    """A pub option returns to :2055 (the ``gosub3000`` the door opened): back on the
    map after the one action, with the door's 5 points only. Tile 5 sells alcohol
    (:12010): 10 barrels at 7 $."""
    runner, into = _walking()
    gen, pub = _into_the_station_pub(runner, into)
    offer = gen.send(pub.options.index("drink"))
    assert (offer.key, offer.params) == ("locations.pub.drink_offer", {"stock": 50, "price": 7})
    ask = gen.send(None)
    assert isinstance(ask, PromptInt) and ask.key == "locations.pub.drink_quantity_prompt"
    done = gen.send(10)
    assert isinstance(done, OptionDone) and done.location == "pub"
    assert isinstance(gen.send(None), MapMove)
    player = runner.state.players[0]
    assert player.ms == 60 - 5
    assert player.previous_tile == (_PUB_LA, 5)
    assert (player.ka, contraband(player).alcohol_barrels) == (10_000 - 70, 10)
    gen.close()


def test_the_bhf_leave_is_the_stations_own_and_costs_10():
    """Leaving the station itself: :3045 and :2060, the previous tile the station's."""
    runner, into = _walking()
    gen = runner.run(WALKING)
    next(gen)
    menu = gen.send(into)
    assert isinstance(gen.send(menu.options.index("leave")), OptionDone)
    assert isinstance(gen.send(None), MapMove)
    player = runner.state.players[0]
    assert (player.ms, player.previous_tile) == (50, (_LA, 1))
    gen.close()

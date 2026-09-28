"""Tests for the aut (Automobil-Haendler) handlers — ports ``mf-prg.bas:14000-14131``.

::

    14010 x=2:ifln=2thenx=x+1
    14025 y=val(x$):ify=0thenpokev+21,0:return
    14030 if(y-1)>xgoto14020
    14035 pokev+21,0:print"{clr}":p=3000+1000*(y-1):ifka(sp)<pthengosub1125:goto14006
    14040 iftm(sp)=0thenq=0:goto14050
    14045 q=1000+1000*tm(sp):iftm(sp)=5thenq=1000
    14047 print"{down}schaukel. ";:gosub1110:ifx$="n"goto14006
    14050 ka(sp)=ka(sp)-p+q:ms=tr(y)-tr(tm(sp))+ms:tm(sp)=y
    14100 ifln<>4andint(rnd(1)*3)<>0thenprint"es sind zuviele leute hier!":goto1100
    14101 print"wer soll den wagen aufbrechen:":gosub1130:ify=0thenreturn
    14110 ifint(rnd(1)*(in/40+kr/30))=0goto14120
    14118 tm(sp)=5:goto1100
    14125 bn$(0)="wagenbesitzer":gz(0)=1:e=30:w=5:kf$="ks":gosub5000:ifs=2goto26020
     1130 ifgz(sp)=0theny=0:return
     1145 input"{down}nummer:";y:ify>gz(sp)thenprint"{up}{up}";:goto1145
     1150 ify=0thenreturn

Vehicles (``entities/vehicles.yaml``, ``:50300-50305``): 0 fuesse tr 25 tank 50, 1 talbot
tr 35 tank 100, 2 chevy tr 40 tank 120, 3 buick tr 40 tank 200, 4 auburn tr 60 tank
150, 5 citroen tr 35 tank 100. The rng is the strict ``StubRng``; every refusal leaves
the state as it was.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import data.game_configs.mafia_1920s.state as game
from data.game_configs.mafia_1920s.effects import (
    Jail,
    JobClear,
    ScoreAndRank,
    TipClear,
    VehicleSet,
)
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.setup import pick_gangster
from data.game_configs.mafia_1920s.state import SCHEMA, Contraband, values_of
from engine.combat import STEP_RIGHT
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, MsChange, SetMovementPoints, Teleport
from engine.interactions import (
    Acknowledge,
    CANCEL,
    Confirm,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptInt,
    StartCombat,
)
from engine.locations import HANDLERS
from engine.rng import Rng
from engine.state import Clock, Config, GameState, Player
from engine.turns import TURN_OVER_SCREEN, WALKING, TurnRunner
from tests.helpers import StubRng, is_effect, run_pure, scripted
from tests.test_turn_runner import _approach, _door

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)
_PARAMS = {**load_config(_CONFIG_DIR / "config.yaml")["formula_params"], "score_mult": 1.0}

_PICK = "turn.picker.prompt"
_MODEL = "locations.aut.model_prompt"
#: The capture menu's surrender key (``:26022-26023``, 0-based here).
_SURRENDER = 2


def _rules() -> dict[str, str]:
    return {
        "intelligence_or_30": "faithful",
        "shared_direction_memory": "faithful",
        "stale_bribe_price": "faithful",
        "flight_odds_by_seat": "faithful",
        "chief_bribe_negative_months": "faithful",
        "chief_bribe_empty_answer": "faithful",
    }


_THIEF = Gangster(name="alcapone", energie=40, kraft=30, intelligenz=40, brutalitaet=30)


def _state(*, barrels: int = 0, **fields) -> GameState:
    fields.setdefault("ka", 10_000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("ms", 20)
    fields.setdefault("vehicle", 0)
    fields.setdefault("last_location", 1)
    fields.setdefault("roster", (_THIEF,))
    values = {**SCHEMA.player_defaults(), **values_of(Contraband(alcohol_barrels=barrels))}
    player = Player(name="alcapone", values=values, **fields)
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS, house_rules=_rules()),
    )


def _run(key: str, state: GameState, answers=(), draws=()):
    rng = StubRng(*draws)
    source = scripted(*answers)
    result = run_pure(HANDLERS[key], source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _asked(source, key: str) -> list:
    return [i for i in source.seen if getattr(i, "key", None) == key]


def _bought(price: int, trade_in: int, points: int, model: int) -> list:
    """:14050 ``ka(sp)=ka(sp)-p+q:ms=tr(y)-tr(tm(sp))+ms:tm(sp)=y``."""
    return [MoneyChange(trade_in - price), MsChange(points), VehicleSet(model)]


# --------------------------------------------------------------------------- #
# Buying — :14006-14052                                                        #
# --------------------------------------------------------------------------- #
def test_a_player_on_foot_buys_a_talbot():
    """No old car, no trade-in (:14040): 3000 $, and ms moves by 35-25 at once."""
    result, source = _run("aut.buy", _state(), answers=(1,))
    assert result.effects == _bought(3000, 0, 10, 1)
    player = result.state.players[0]
    assert (player.ka, player.ms, player.vehicle) == (7000, 30, 1)
    assert source.message_keys()[-1] == "locations.aut.sold"
    assert not any(isinstance(i, Confirm) for i in source.seen)


@pytest.mark.parametrize(
    ("old", "trade_in"), [(1, 2000), (2, 3000), (3, 4000), (4, 5000), (5, 1000)]
)
def test_the_trade_in_is_1000_plus_1000_per_model_and_1000_for_the_stolen_car(old, trade_in):
    """:14045 ``q=1000+1000*tm(sp):iftm(sp)=5thenq=1000``."""
    tr = {1: 35, 2: 40, 3: 40, 4: 60, 5: 35}
    result, source = _run("aut.buy", _state(vehicle=old), answers=(2, True))
    assert result.effects == _bought(4000, trade_in, 40 - tr[old], 2)
    offer = next(m for m in source.messages() if m.key == "locations.aut.trade_in_offer")
    assert offer.params == {"amount": trade_in}


def test_buying_needs_the_full_price_in_cash_even_with_a_trade_in():
    """:14035 ``ifka(sp)<p`` comes before the trade-in: 4999 $ and a talbot worth 2000 $
    cannot buy the 5000 $ buick; the player is sent back to the showroom."""
    st = _state(vehicle=1, ka=4999)
    result, source = _run("aut.buy", st, answers=(3, 0))
    assert result.effects == []
    assert result.state == st
    assert "system.not_enough_money" in source.message_keys()
    assert not any(isinstance(i, Confirm) for i in source.seen)

    result, _ = _run("aut.buy", _state(vehicle=1, ka=5000), answers=(3, True))
    assert result.effects == _bought(5000, 2000, 5, 3)
    assert result.state.players[0].ka == 2000


def test_a_player_who_cannot_afford_the_car_goes_back_to_the_showroom():
    """:14035 ``gosub1125:goto14006``: the broke text, then the showroom again (not the
    map), where 0 leaves (:14025)."""
    st = _state(ka=2999)
    result, source = _run("aut.buy", st, answers=(1, 0))
    assert result.effects == []
    assert result.state == st
    keys = source.message_keys()
    assert keys.count("locations.aut.showroom") == 2
    assert keys.index("system.not_enough_money") < len(keys) - 1 - keys[::-1].index(
        "locations.aut.showroom"
    )
    assert len(_asked(source, _MODEL)) == 2


def test_declining_the_trade_in_returns_to_the_showroom_without_buying():
    """:14047 ``ifx$="n"goto14006``: no car is sold without the old one traded in."""
    st = _state(vehicle=2)
    result, source = _run("aut.buy", st, answers=(3, False, 0))
    assert result.effects == []
    assert result.state == st
    assert source.message_keys().count("locations.aut.showroom") == 2


def test_zero_or_return_leaves_the_showroom_quietly():
    """:14025 ``ify=0thenreturn``; RETURN is ``val``'d to 0 too."""
    for answer in (0, ""):
        result, source = _run("aut.buy", _state(), answers=(answer,))
        assert result.effects == []
        assert source.message_keys()[-1] == "locations.aut.model"


@pytest.mark.parametrize(("ln", "models"), [(1, 3), (2, 4), (3, 3), (4, 3)])
def test_the_fourth_model_shows_only_on_tile_2(ln, models):
    """:14010 ``x=2:ifln=2thenx=x+1``: models 1..x+1, and a higher key is read again
    (:14030)."""
    result, source = _run("aut.buy", _state(last_location=ln), answers=(0,))
    shown = [m.params for m in source.messages() if m.key == "locations.aut.model"]
    names = ["talbot 90", "chevy roadster", "buick century", "auburn mod.120"]
    assert shown == [
        {"number": n, "name": names[n - 1], "price": 3000 + 1000 * (n - 1)}
        for n in range(1, models + 1)
    ]
    (prompt,) = _asked(source, _MODEL)
    assert (prompt.min, prompt.max, prompt.blank) == (0, models, 0)


def test_the_auburn_on_tile_2_costs_6000():
    result, _ = _run("aut.buy", _state(last_location=2), answers=(4,))
    assert result.effects == _bought(6000, 0, 35, 4)


def test_a_smaller_tank_keeps_the_barrel_count():
    """Neither the buy (:14050) nor the steal (:14118) writes the barrels: 150 stay in
    a talbot (tank 100), bought or stolen."""
    result, _ = _run("aut.buy", _state(vehicle=3, barrels=150), answers=(1, True))
    assert game.contraband(result.state.players[0]).alcohol_barrels == 150
    assert result.state.players[0].vehicle == 1

    result, _ = _run("aut.steal", _state(vehicle=3, barrels=150), answers=(1,), draws=(0, 120))
    assert game.contraband(result.state.players[0]).alcohol_barrels == 150
    assert result.state.players[0].vehicle == 5


# --------------------------------------------------------------------------- #
# Through the turn runner: the purchase's movement points                     #
# --------------------------------------------------------------------------- #
def test_buying_a_faster_car_with_3_points_left_continues_the_map_with_the_new_total():
    """:14050 moves ms at once: 3 + (35 - 25) = 13, then the door's 5 (:2060) leaves 8,
    so the map goes on (:2060 ``ifms>0goto2000``)."""
    door, _la = _door("aut")
    start, into = _approach(door)
    state = _CONFIG.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    player = replace(state.players[0], po=start, ms=3, ka=10_000, vehicle=0)
    state = replace(state, players=(player,), clock=replace(state.clock, turn_phase=WALKING))
    runner = TurnRunner(
        state, Rng(42), city=_CONFIG.city, shells=_CONFIG.shells, turn_menu=_CONFIG.menus["turn"]
    )
    gen = runner.run()
    seen: list = []
    plan = [into, "buy"]
    interaction = next(gen)
    while True:
        seen.append(interaction)
        if isinstance(interaction, Acknowledge) and interaction.key == TURN_OVER_SCREEN:
            break
        if isinstance(interaction, MapMove) and not plan:
            break
        if isinstance(interaction, MapMove):
            response = plan.pop(0)
        elif isinstance(interaction, LocationMenu):
            response = interaction.options.index(plan.pop(0))
        elif isinstance(interaction, PromptInt) and interaction.key == _MODEL:
            response = 1  # the talbot
        else:
            response = None
        interaction = gen.send(response)
    gen.close()

    assert isinstance(seen[-1], MapMove), "the turn ended instead of going on"
    assert any(isinstance(i, OptionDone) for i in seen)
    player = runner.state.players[0]
    assert (player.vehicle, player.ms, player.ka) == (1, 8, 7000)


# --------------------------------------------------------------------------- #
# Stealing — :14100-14131                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ln", [1, 2, 3, 4])
@pytest.mark.parametrize("crowd", [0, 1, 2])
def test_the_crowd_refuses_2_times_in_3_on_every_tile_but_4(ln, crowd):
    """:14100 ``ifln<>4andint(rnd(1)*3)<>0``: the roll is drawn on every tile."""
    st = _state(last_location=ln)
    refused = ln != 4 and crowd != 0
    if refused:
        result, source = _run("aut.steal", st, draws=(crowd,))
        assert result.effects == []
        assert result.state == st
        assert source.message_keys() == ["locations.aut.crowded"]
    else:
        result, source = _run("aut.steal", st, answers=(1,), draws=(crowd, 120))
        assert result.effects == [VehicleSet(5)]
        assert "locations.aut.crowded" not in source.message_keys()


def test_any_steal_roll_but_0_gives_the_citroen_with_the_movement_points_unchanged():
    """:14115-14118 ``tm(sp)=5``, no ``ms`` change (unlike :14050); the old car stays
    behind (:14117)."""
    for vehicle, message in ((0, "locations.aut.stolen"), (4, "locations.aut.stolen_old_car_left")):
        st = _state(vehicle=vehicle, ms=7)
        result, source = _run("aut.steal", st, answers=(1,), draws=(0, 120))
        assert result.effects == [VehicleSet(5)]
        player = result.state.players[0]
        assert (player.vehicle, player.ms, player.ka, player.gf) == (5, 7, 10_000, 50.0)
        assert source.message_keys()[-1] == message


def test_a_stolen_car_keeps_the_movement_points():
    """The catalogue's faithful-only entry: a stolen car's range counts next turn."""
    st = _state(vehicle=0, ms=3)
    result, _ = _run("aut.steal", st, answers=(1,), draws=(0, 120))
    assert result.state.players[0].ms == 3
    assert not any(isinstance(e, MsChange) for e in result.effects)


@pytest.mark.parametrize(
    ("intelligenz", "kraft", "bound"), [(40, 30, 240), (40, 0, 120), (0, 0, 1), (99, 99, 693)]
)
def test_the_steal_roll_is_caught_below_120_out_of_3_in_plus_4_kr(intelligenz, kraft, bound):
    """:14110 ``int(rnd(1)*(in/40+kr/30))=0`` iff ``rnd(1)*(3*in+4*kr) < 120``."""
    from engine.interactions import Ctx

    thief = Gangster(name="t", energie=40, kraft=kraft, intelligenz=intelligenz, brutalitaet=30)
    for roll in sorted({0, bound - 1, 119, 120} & set(range(bound))):
        rng = StubRng(0, roll)
        ctx = Ctx(state=_state(roster=(thief,)), rng=rng)
        gen = HANDLERS["aut.steal"](ctx)
        interaction = next(gen)
        keys = []
        try:
            while not isinstance(interaction, StartCombat):
                keys.append(getattr(interaction, "key", None))
                interaction = gen.send(1 if isinstance(interaction, PromptInt) else None)
        except StopIteration:
            pass
        gen.close()
        assert rng.calls == [("range", 3), ("range", bound)]
        caught = "locations.aut.caught" in keys
        assert caught == (roll < 120), roll
        assert ("locations.aut.stolen" in keys) == (roll >= 120), roll


_WINNER = Gangster(name="alcapone", weapon=7, energie=40, kraft=30, intelligenz=40, brutalitaet=290)


def test_a_steal_roll_of_0_then_a_lost_owner_fight_leads_to_capture():
    """:14120, :14125 ``ifs=2goto26020``: the player gives up the fight, and the police
    take him (surrender at the arrest menu: the trial, no lawyer at rank 1)."""
    st = _state(rank=1)
    result, source = _run(
        "aut.steal",
        st,
        answers=(1, ("surrender", None), _SURRENDER),
        draws=(0, 0, 0),  # crowd, the steal roll (caught), the :26021 roll
    )
    keys = source.message_keys()
    assert keys.index("locations.aut.caught") < keys.index("police.caught")
    assert "locations.aut.owner_killed" not in keys
    assert result.effects[-7:] == [
        TipClear(),
        ScoreAndRank(amount=2, rank_divisor=11.1),
        Jail(months=1),
        SetMovementPoints(0),
        JobClear(),
        ScoreAndRank(amount=-10, rank_divisor=11.1),
        Teleport(911),
    ]
    assert not any(is_effect(e, VehicleSet) for e in result.effects)
    assert result.state.players[0].vehicle == 0


def test_the_owner_is_one_wagenbesitzer_with_a_revolver_and_30_energy_on_ks():
    """:14125 ``bn$(0)="wagenbesitzer":gz(0)=1:e=30:w=5:kf$="ks"``."""
    from engine.interactions import Ctx

    ctx = Ctx(state=_state(), rng=StubRng(0, 0))
    gen = HANDLERS["aut.steal"](ctx)
    interaction = next(gen)
    while not isinstance(interaction, StartCombat):
        interaction = gen.send(1 if isinstance(interaction, PromptInt) else None)
    scenario = interaction.scenario
    assert scenario is not None and scenario.sides is not None
    (owner,) = scenario.sides[1]
    assert (owner.weapon, owner.vitality) == (5, 30)
    assert scenario.sides[0][0].name == "alcapone"
    gen.close()


def test_a_won_owner_fight_gives_no_car_no_cash_and_no_score():
    """:14130-14131: the player flees on foot, as he came."""
    st = _state(roster=(_WINNER,), vehicle=0)
    result, source = _run(
        "aut.steal",
        st,
        answers=(1, ("shoot", STEP_RIGHT)),
        draws=(0, 0, 1, 10, 0),  # crowd, caught, then one killing shot
    )
    keys = source.message_keys()
    assert keys[-1] == "locations.aut.owner_killed"
    assert "police.caught" not in keys
    assert not any(is_effect(e, VehicleSet, MoneyChange, ScoreAndRank) for e in result.effects)
    player = result.state.players[0]
    assert (player.vehicle, player.ka, player.gf) == (0, 10_000, 50.0)


# --------------------------------------------------------------------------- #
# The gangster picker — :1130-1155 (setup.pick_gangster)                       #
# --------------------------------------------------------------------------- #
def test_the_picker_returns_at_once_for_an_empty_gang():
    """:1130 ``ifgz(sp)=0theny=0:return``: no list, no prompt, and :14101 returns."""
    result, source = _run("aut.steal", _state(roster=()), draws=(0,))
    assert result.effects == []
    assert source.message_keys() == ["locations.aut.steal_prompt"]
    assert _asked(source, _PICK) == []


def test_the_picker_with_0_returns():
    """:1150 ``ify=0thenreturn``, and :14101 returns: no steal roll is drawn."""
    st = _state()
    result, source = _run("aut.steal", st, answers=(0,), draws=(0,))
    assert result.effects == []
    assert result.state == st
    (prompt,) = _asked(source, _PICK)
    assert (prompt.min, prompt.max, prompt.cancellable) == (0, 1, True)


def test_the_picker_lists_each_gangster_numbered_from_1():
    """:1135 ``mid$(str$(i),2)" "`` then :1300-1320: name, stats, weapon."""
    gang = (_THIEF, Gangster(name="luigi", weapon=5, energie=9, kraft=12, intelligenz=3))
    result, source = _run("aut.steal", _state(roster=gang), answers=(0,), draws=(0,))
    listed = [m.params for m in source.messages() if m.key == "turn.picker.gangster"]
    assert listed == [
        {
            "index": 1,
            "name": "alcapone",
            "energie": 40,
            "kraft": 30,
            "intelligenz": 40,
            "brutalitaet": 30,
            "weapon": "haende",
        },
        {
            "index": 2,
            "name": "luigi",
            "energie": 9,
            "kraft": 12,
            "intelligenz": 3,
            "brutalitaet": 0,
            "weapon": "revolver",
        },
    ]


def test_a_number_above_the_gang_is_asked_again():
    """:1145 ``ify>gz(sp)thenprint"{up}{up}";:goto1145``; so is a negative (the C64
    stops with an error there)."""
    result, source = _run("aut.steal", _state(), answers=(2, -1, 1), draws=(0, 120))
    assert result.effects == [VehicleSet(5)]
    assert len(_asked(source, _PICK)) == 3


def test_the_picked_gangster_is_the_one_that_tries_the_lock():
    """:1155 ``gosub1350`` loads the chosen gangster's ``in``/``kr`` for :14110."""
    gang = (_THIEF, Gangster(name="luigi", kraft=90, intelligenz=80))
    rng = StubRng(0, 120)
    run_pure(HANDLERS["aut.steal"], scripted(2), state=_state(roster=gang), rng=rng)
    assert rng.calls[1] == ("range", 3 * 80 + 4 * 90)


def test_an_empty_answer_at_a_cancellable_picker_commits_nothing():
    result, _ = _run("aut.steal", _state(), answers=(CANCEL,), draws=(0,))
    assert result.status == "cancelled"
    assert result.effects == []


def _paid_then_picked(ctx):
    """A caller that has paid before it picks, with a picker it may not cancel."""
    ctx.apply(MoneyChange(-100))
    picked = yield from pick_gangster(ctx, cancellable=False)
    return picked


def test_a_non_cancellable_picker_returns_on_0_and_the_callers_effects_stand():
    result = run_pure(_paid_then_picked, scripted(0), state=_state(), rng=StubRng())
    assert result.status == "completed"
    assert result.payload.returned is None
    assert result.effects == [MoneyChange(-100)]


def test_a_non_cancellable_picker_asks_again_on_an_empty_answer():
    source = scripted("", 1)
    result = run_pure(_paid_then_picked, source, state=_state(), rng=StubRng())
    assert result.payload.returned == 0
    prompts = _asked(source, _PICK)
    assert len(prompts) == 2 and not prompts[0].cancellable

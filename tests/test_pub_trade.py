"""Tests for the pub.drink handler — U8, alcohol trade.

Proof-first: written and observed RED (BarrelChange NotImplementedError from the U2
groundwork stub, and no ``pub.drink`` handler at all) before implementation.

Ports the alcohol block ``mf-prg.bas:12010-12075`` verbatim:
- pub tiles ln=4 and ln=5 serve (``:12010 ifln=4orln=5goto12020``; ln=5 is the railway
  station's pub, opened by bhf's ``:19010 ln=5:la=2:goto3000``); every other tile
  falls into a 50%-refusal-or-sell-offer branch.
- BUY (ln=4, ln=5): stock 100-299 barrels, price 5-9$/barrel, capacity-capped by
  vehicle.tank - carried barrels (on foot tank=50); afford check runs before any
  write; settle is +barrels/-cash/+2 score-and-rank.
- SELL (elsewhere, after the 50% "he wants to buy" roll): price 10-29$/barrel,
  quantity capped by current barrels; settles unconditionally, no score effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config_loader import load_game_config
from engine.effects import MoneyChange
from engine.interactions import PromptInt
from data.game_configs.mafia_1920s.effects import BarrelChange, ScoreAndRank
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.state import Contraband
from data.game_configs.mafia_1920s.gangster import Gangster
from tests.helpers import StubRng as _StubRng, run_pure, scripted as _scripted
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

#: The umbrella house rule for a negative answer the C64 INPUT takes (U2's id).
_NEGATIVES = "c64_input_negatives"

_PARAMS = {
    "rank_divisor": 11.1,
    "pub_alcohol_stock_min": 100,
    "pub_alcohol_stock_max": 299,
    "pub_alcohol_buy_price_min": 5,
    "pub_alcohol_buy_price_max": 9,
    "pub_alcohol_sell_price_min": 10,
    "pub_alcohol_sell_price_max": 29,
}


def _state(*, ka=100000, ln=4, vehicle=0, barrels=0, score_mult=1.0, gf=0.0, negatives=None):
    """``negatives`` sets the ``c64_input_negatives`` house rule; ``None`` leaves the
    map without it, which reads faithful (as a hand-built state's missing switch does)."""
    active = Player(
        name="p0",
        ka=ka,
        gf=gf,
        roster=(Gangster(name="g0"),),
        last_location=ln,
        vehicle=vehicle,
        values=game.values_of(Contraband(alcohol_barrels=barrels)),
    )
    return GameState(
        players=(active,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(
            formula_params={**_PARAMS, "score_mult": score_mult},
            house_rules={} if negatives is None else {_NEGATIVES: negatives},
        ),
    )


# --------------------------------------------------------------------------- #
# Branch matrix: tile-4 buy vs elsewhere 50/50 sell-or-refuse                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ln", [4, 5])
def test_tiles_4_and_5_always_enter_the_buy_path_no_rng_branch_roll(ln):
    # :12010 ifln=4orln=5goto12020 skips the 50% refusal roll entirely -- stock/price
    # are the FIRST rng draws.
    st = _state(ln=ln, ka=100000)
    rng = _StubRng(150, 7, 0)  # stock=150, price=7, quantity 0 -> quiet abort
    result = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    # First two draws are the hit() stock/price rolls, not a range(2) branch roll.
    assert rng.calls[0][0] == "hit"
    assert rng.calls[1][0] == "hit"


def test_the_station_pub_sells_alcohol_on_tile_5():
    """bhf's pub is the pub menu on tile 5 (``:19010``): :12020-12035 buy, +2 score."""
    st = _state(ln=5, ka=100000)
    rng = _StubRng(60, 5, 10)  # stock=60, price=5, buy 10 -> cost 50
    result = run_pure(HANDLERS["pub.drink"], _scripted(10), state=st, rng=rng)
    assert result.effects == [
        BarrelChange(10),
        MoneyChange(-50),
        ScoreAndRank(amount=2, rank_divisor=11.1),
    ]
    assert game.contraband(result.state.players[0]).alcohol_barrels == 10


@pytest.mark.parametrize("ln", [1, 2, 3, 6])
def test_every_other_tile_rolls_the_refusal_first(ln):
    """:12015 ``ifint(rnd(1)*2)=0goto12050``: not 4 or 5, the 50% roll comes first."""
    rng = _StubRng(0)
    result = run_pure(HANDLERS["pub.drink"], _scripted(), state=_state(ln=ln), rng=rng)
    assert result.effects == []
    assert rng.calls == [("range", 2)]


def test_elsewhere_refusal_branch_shows_message_no_effects():
    st = _state(ln=2, ka=100000)
    rng = _StubRng(0)  # range(2)==0 -> refusal
    result = run_pure(HANDLERS["pub.drink"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert rng.calls == [("range", 2)]


def test_elsewhere_sell_offer_branch_reached_on_the_other_50():
    st = _state(ln=2, ka=100000, barrels=10)
    rng = _StubRng(1, 15)  # range(2)!=0 -> sell path; price=15
    result = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)  # y=0 abort
    assert result.status == "completed"
    assert result.effects == []
    assert rng.calls[0] == ("range", 2)
    assert rng.calls[1] == ("hit", 10, 29)


# --------------------------------------------------------------------------- #
# Seeded formula ranges: stock, prices, capacity cap at on-foot 50            #
# --------------------------------------------------------------------------- #
def test_buy_stock_and_price_rolled_in_documented_ranges():
    st = _state(ln=4, ka=100000, vehicle=0, barrels=0)  # on foot, tank=50
    rng = _StubRng(299, 9, 0)  # max stock 299, capped to 50 (on foot) -- max price 9
    run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert rng.calls == [("hit", 100, 299), ("hit", 5, 9)]


def test_buy_capacity_capped_at_on_foot_50():
    # on foot (vehicle=0) tank=50 (entities/vehicles.yaml); stock rolled 200 -> capped
    # to 50 - carried barrels. carried=0 -> cap 50.
    st = _state(ln=4, ka=100000, vehicle=0, barrels=0)
    rng = _StubRng(200, 5, 50)  # stock=200 (capped to 50), price=5, buy all 50
    result = run_pure(HANDLERS["pub.drink"], _scripted(50), state=st, rng=rng)
    assert result.effects[0] == BarrelChange(50)
    assert game.contraband(result.state.players[0]).alcohol_barrels == 50


def test_buy_capacity_capped_by_carried_barrels_already_on_foot():
    # on foot tank=50, already carrying 30 -> free capacity 20, even though the roll is 200.
    st = _state(ln=4, ka=100000, vehicle=0, barrels=30)
    rng = _StubRng(200, 5, 20)
    result = run_pure(HANDLERS["pub.drink"], _scripted(20), state=st, rng=rng)
    assert game.contraband(result.state.players[0]).alcohol_barrels == 50  # 30 + 20


def test_buy_stock_under_capacity_is_not_capped():
    # stock roll 100 stays 100 when capacity (on-foot 50 minus 0 carried = 50) -- wait,
    # 100 > 50, so it WOULD cap. Use a vehicle with more room instead (talbot 90, tank=100).
    st = _state(ln=4, ka=100000, vehicle=1, barrels=0)  # talbot 90, tank=100
    rng = _StubRng(60, 5, 60)  # stock=60 < 100 capacity -> uncapped
    result = run_pure(HANDLERS["pub.drink"], _scripted(60), state=st, rng=rng)
    assert game.contraband(result.state.players[0]).alcohol_barrels == 60


def test_sell_price_rolled_in_documented_range():
    st = _state(ln=2, ka=100000, barrels=10)
    rng = _StubRng(1, 29)  # forced sell path, max sell price 29
    run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert rng.calls[1] == ("hit", 10, 29)


# --------------------------------------------------------------------------- #
# Broke paths -> insufficient-funds message, NO state change                  #
# --------------------------------------------------------------------------- #
def test_buy_broke_path_no_state_change():
    st = _state(ln=4, ka=10)  # can't afford even 1 barrel at price >=5
    rng = _StubRng(299, 9, 50)  # stock capped to 50 (on foot), price=9, buy 50 -> 450$
    result = run_pure(HANDLERS["pub.drink"], _scripted(50), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert result.state.players[0].ka == 10
    assert game.contraband(result.state.players[0]).alcohol_barrels == 0


def test_buy_quantity_zero_is_quiet_abort():
    st = _state(ln=4, ka=100000)
    rng = _StubRng(200, 7)
    result = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert result.effects == []
    assert result.state.players[0].ka == 100000


def test_sell_quantity_zero_is_quiet_abort():
    st = _state(ln=2, ka=100000, barrels=5)
    rng = _StubRng(1, 20)
    result = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert result.effects == []
    assert game.contraband(result.state.players[0]).alcohol_barrels == 5


# --------------------------------------------------------------------------- #
# Settlement: buy scores +2 (score-and-rank); sell does NOT                   #
# --------------------------------------------------------------------------- #
def test_buy_settle_moves_money_and_barrels_and_scores_plus_2():
    st = _state(ln=4, ka=1000, vehicle=1, barrels=0)  # talbot 90, tank=100
    rng = _StubRng(60, 5, 10)  # stock=60, price=5, buy 10 -> cost 50
    result = run_pure(HANDLERS["pub.drink"], _scripted(10), state=st, rng=rng)
    assert result.effects == [
        BarrelChange(10),
        MoneyChange(-50),
        ScoreAndRank(amount=2, rank_divisor=11.1),
    ]
    assert result.state.players[0].ka == 950
    assert game.contraband(result.state.players[0]).alcohol_barrels == 10


def test_sell_settle_moves_money_and_barrels_no_score_effect():
    st = _state(ln=2, ka=1000, barrels=20)
    rng = _StubRng(1, 15)  # sell path, price=15
    result = run_pure(HANDLERS["pub.drink"], _scripted(5), state=st, rng=rng)
    assert result.effects == [MoneyChange(75), BarrelChange(-5)]
    assert result.state.players[0].ka == 1075
    assert game.contraband(result.state.players[0]).alcohol_barrels == 15
    assert result.state.players[0].gf == 0.0  # unchanged -- no score effect on sell


def test_a_sale_shows_the_greed_line_before_its_key_wait():
    """:12070 ``print"{down}'besorg noch mehr{$a0}(gier)!'":goto12075`` -- printed on
    every sale (y<>0) after the count, then :12075 settles and waits; a count of 0
    returns at :12065 before it."""
    source = _scripted(5)
    run_pure(
        HANDLERS["pub.drink"], source, state=_state(ln=2, ka=1000, barrels=20), rng=_StubRng(1, 15)
    )
    keys = source.message_keys()
    assert keys[-1] == "locations.pub.sell_greed"
    assert source.ends_in_key_wait()
    zero = _scripted(0)
    run_pure(
        HANDLERS["pub.drink"], zero, state=_state(ln=2, ka=1000, barrels=20), rng=_StubRng(1, 15)
    )
    assert "locations.pub.sell_greed" not in zero.message_keys()


def test_run_pure_clean_for_buy_and_sell():
    st = _state(ln=4, ka=100000)
    rng = _StubRng(150, 7, 0)
    result = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert result.status == "completed"

    st2 = _state(ln=2, ka=100000, barrels=3)
    rng2 = _StubRng(1, 20, 0)
    result2 = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st2, rng=rng2)
    assert result2.status == "completed"


# --------------------------------------------------------------------------- #
# A negative count (house rule c64_input_negatives): :12027 and :12060 INPUT  #
# --------------------------------------------------------------------------- #
# The C64 INPUT stores "-5" as -5 (tests/fixtures/c64_input/vice_capture.txt), and no
# line of the buy or the sell refuses it. Faithful plays that; intent asks again.


def _prompts(source) -> list[PromptInt]:
    return [i for i in source.seen if isinstance(i, PromptInt)]


def test_a_negative_buy_sells_barrels_to_the_pub_and_still_scores_when_faithful():
    """:12028 ``ify>x`` and :12029 ``ify=0`` pass -5; :12030 ``ifka(sp)<y*p`` is
    ``1000<-25``, false; :12035 ``ta(sp)=ta(sp)+y:ka(sp)=ka(sp)-p*y:x=2:gosub1160``
    takes 5 barrels, pays 5p and scores 2."""
    st = _state(ln=4, ka=1000, vehicle=1, barrels=0, negatives="faithful")
    source = _scripted(-5)
    result = run_pure(HANDLERS["pub.drink"], source, state=st, rng=_StubRng(60, 5))
    assert result.effects == [
        BarrelChange(-5),
        MoneyChange(25),
        ScoreAndRank(amount=2, rank_divisor=11.1),
    ]
    after = result.state.players[0]
    assert (after.ka, game.contraband(after).alcohol_barrels, after.gf) == (1025, -5, 2.0)
    assert len(_prompts(source)) == 1


def test_a_negative_sell_buys_barrels_from_the_pub_when_faithful():
    """:12061 ``ify>ta(sp)`` and :12065 ``ify=0`` pass -5; :12075
    ``ka(sp)=ka(sp)+y*x:ta(sp)=ta(sp)-y`` takes 5x cash and adds 5 barrels."""
    st = _state(ln=2, ka=1000, barrels=20, negatives="faithful")
    result = run_pure(HANDLERS["pub.drink"], _scripted(-5), state=st, rng=_StubRng(1, 15))
    assert result.effects == [MoneyChange(-75), BarrelChange(5)]
    after = result.state.players[0]
    assert (after.ka, game.contraband(after).alcohol_barrels, after.gf) == (925, 25, 0.0)


def test_a_sell_takes_cash_the_player_does_not_have_when_faithful():
    """:12075 checks no cash: a negative sale drives ``ka`` below 0."""
    st = _state(ln=2, ka=10, barrels=0, negatives="faithful")
    result = run_pure(HANDLERS["pub.drink"], _scripted(-5), state=st, rng=_StubRng(1, 15))
    after = result.state.players[0]
    assert (after.ka, game.contraband(after).alcohol_barrels) == (-65, 5)


def test_with_barrels_below_zero_a_sell_of_zero_is_asked_again_when_faithful():
    """ta(sp)=-5: :12061 ``ify>ta(sp)`` asks 0 again (0>-5); only y<=-5 passes, and
    -5 buys the barrels back to 0."""
    st = _state(ln=2, ka=1000, barrels=-5, negatives="faithful")
    source = _scripted(0, -4, -5)
    result = run_pure(HANDLERS["pub.drink"], source, state=st, rng=_StubRng(1, 15))
    assert result.effects == [MoneyChange(-75), BarrelChange(5)]
    assert game.contraband(result.state.players[0]).alcohol_barrels == 0
    assert len(_prompts(source)) == 3


def test_with_barrels_above_the_tank_a_buy_of_zero_is_asked_again_when_faithful():
    """On foot (tank 50) with 60 barrels, :12025 ``q=tk(tm(sp))-ta(sp):ifq<xthenx=q``
    makes the offer -10, and :12028 ``ify>x`` asks 0 again; -10 sells 10 barrels."""
    st = _state(ln=4, ka=1000, vehicle=0, barrels=60, negatives="faithful")
    source = _scripted(0, -10)
    result = run_pure(HANDLERS["pub.drink"], source, state=st, rng=_StubRng(200, 5))
    assert result.effects == [
        BarrelChange(-10),
        MoneyChange(50),
        ScoreAndRank(amount=2, rank_divisor=11.1),
    ]
    assert [p.max for p in _prompts(source)] == [-10, -10]


@pytest.mark.parametrize(
    ("ln", "barrels", "draws"),
    [(4, 0, (60, 5)), (2, 20, (1, 15))],
    ids=["buy", "sell"],
)
def test_a_negative_count_is_asked_again_and_zero_still_returns_under_intent(ln, barrels, draws):
    """Intent: a count below 0 is asked again at either prompt; 0 still returns."""
    st = _state(ln=ln, ka=1000, vehicle=1, barrels=barrels, negatives="intent")
    source = _scripted(-5, 0)
    result = run_pure(HANDLERS["pub.drink"], source, state=st, rng=_StubRng(*draws))
    assert result.effects == []
    assert [p.min for p in _prompts(source)] == [0, 0]


@pytest.mark.parametrize(
    ("ln", "barrels", "draws", "answer", "effects"),
    [
        (4, 0, (60, 5), 3, [BarrelChange(3), MoneyChange(-15)]),
        (2, 20, (1, 15), 3, [MoneyChange(45), BarrelChange(-3)]),
    ],
    ids=["buy", "sell"],
)
def test_after_a_refused_negative_the_next_count_trades_under_intent(
    ln, barrels, draws, answer, effects
):
    st = _state(ln=ln, ka=1000, vehicle=1, barrels=barrels, negatives="intent")
    result = run_pure(HANDLERS["pub.drink"], _scripted(-5, answer), state=st, rng=_StubRng(*draws))
    assert result.effects[:2] == effects


def test_the_switch_reaches_only_the_pubs_two_prompts_and_the_chiefs():
    """AE3: the motel's months (:10030 ``ifx=0orx<0thennm=1:return``) and the loan
    shark's amounts (:15021 ``ifx<0orx>5000``, :15055 ``ifx<0orx>kr(sp)``) refuse a
    negative in the BASIC already, so they ask the same under both settings."""
    from data.game_configs.mafia_1920s.state import Debt, SCHEMA, values_of
    from engine.config_loader import load_config

    params = load_config(_CONFIG_DIR / "config.yaml")["formula_params"]
    seen: dict[str, list[tuple]] = {}
    for setting in ("faithful", "intent"):
        for key, debt in (("slw.rent", 0), ("kdh.borrow", 0), ("kdh.repay", 900)):
            player = Player(
                name="p0",
                ka=5000,
                roster=(Gangster(name="g0"),),
                last_location=2,
                values={**SCHEMA.player_defaults(), **values_of(Debt(amount=debt))},
            )
            st = GameState(
                players=(player,),
                clock=Clock(active_player=0, player_count=1),
                config=Config(formula_params=params, house_rules={_NEGATIVES: setting}),
            )
            source = _scripted(-3, 0)
            result = run_pure(HANDLERS[key], source, state=st)
            assert result.effects == [], key
            seen.setdefault(key, []).append(tuple((p.key, p.min, p.max) for p in _prompts(source)))
    for key, (faithful, intent) in seen.items():
        assert faithful == intent, key


# --------------------------------------------------------------------------- #
# The :1100 key wait at each exit (#160): ``goto1100``/``goto1125`` waits,      #
# ``return`` does not                                                          #
# --------------------------------------------------------------------------- #
_DRINK_EXITS = [
    # id, state kwargs, rng draws, answers, waits
    # :12016 print"'was? alkohol? ist doch verboten!'":goto1100
    ("12016-refused", {"ln": 1}, (0,), (), 1),
    # :12029 ify=0thenreturn
    ("12029-buy-nothing", {"ln": 4}, (150, 5), (0,), 0),
    # :12030 ifka(sp)<y*pgoto1125
    ("12030-too-poor", {"ln": 4, "ka": 10}, (150, 5), (10,), 1),
    # :12035 ta(sp)=ta(sp)+y:ka(sp)=ka(sp)-p*y:x=2:gosub1160:return
    ("12035-bought", {"ln": 4}, (150, 5), (10,), 0),
    # :12065 ify=0thenreturn
    ("12065-sell-nothing", {"ln": 1, "barrels": 10}, (1, 20), (0,), 0),
    # :12075 ka(sp)=ka(sp)+y*x:ta(sp)=ta(sp)-y:goto1100
    ("12075-sold", {"ln": 1, "barrels": 10}, (1, 20), (5,), 1),
]


@pytest.mark.parametrize(
    ("kwargs", "draws", "answers", "waits"),
    [case[1:] for case in _DRINK_EXITS],
    ids=[case[0] for case in _DRINK_EXITS],
)
def test_each_drink_exit_waits_for_a_key_where_the_source_does(kwargs, draws, answers, waits):
    src = _scripted(*answers)
    run_pure(HANDLERS["pub.drink"], src, state=_state(**kwargs), rng=_StubRng(*draws))
    assert src.key_waits() == waits
    if waits:
        assert src.ends_in_key_wait(), "the exit did not end in the :1100 key wait"

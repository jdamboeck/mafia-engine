"""Tests for the pub.drink handler — U8, alcohol trade.

Proof-first: written and observed RED (BarrelChange NotImplementedError from the U2
groundwork stub, and no ``pub.drink`` handler at all) before implementation.

Ports the alcohol block ``mf-prg.bas:12010-12075`` verbatim:
- only pub tile ln=4 serves (ln=5 is confirmed dead code this slice — see pub.py's
  module docstring: it is set only by the out-of-scope bhf handler); every other tile
  falls into a 50%-refusal-or-sell-offer branch.
- BUY (ln=4): stock 100-299 barrels, price 5-9$/barrel, capacity-capped by
  vehicle.tank - carried barrels (on foot tank=50); afford check runs before any
  write; settle is +barrels/-cash/+2 score-and-rank.
- SELL (elsewhere, after the 50% "he wants to buy" roll): price 10-29$/barrel,
  quantity capped by current barrels; settles unconditionally, no score effect.
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import BarrelChange, MoneyChange, ScoreAndRank
from engine.locations import HANDLERS
from engine.state import Clock, Config, Contraband, Gangster, GameState, Player
from tests.helpers import StubRng as _StubRng, run_pure, scripted as _scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PARAMS = {
    "rank_divisor": 11.1,
    "pub_alcohol_stock_min": 100,
    "pub_alcohol_stock_max": 299,
    "pub_alcohol_buy_price_min": 5,
    "pub_alcohol_buy_price_max": 9,
    "pub_alcohol_sell_price_min": 10,
    "pub_alcohol_sell_price_max": 29,
}


def _state(*, ka=100000, ln=4, vehicle=0, barrels=0, score_mult=1.0, gf=0.0):
    active = Player(
        name="p0",
        ka=ka,
        gf=gf,
        roster=(Gangster(name="g0"),),
        last_location=ln,
        vehicle=vehicle,
        contraband=Contraband(alcohol_barrels=barrels),
    )
    return GameState(
        players=(active,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(score_mult=score_mult, formula_params=_PARAMS),
    )




# --------------------------------------------------------------------------- #
# Branch matrix: tile-4 buy vs elsewhere 50/50 sell-or-refuse                  #
# --------------------------------------------------------------------------- #
def test_tile_4_always_enters_the_buy_path_no_rng_branch_roll():
    # ln=4 skips the 50% refusal roll entirely -- stock/price are the FIRST rng draws.
    st = _state(ln=4, ka=100000)
    rng = _StubRng(150, 7, 0)  # stock=150, price=7, quantity 0 -> quiet abort
    result = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    # First two draws are the hit() stock/price rolls, not a range(2) branch roll.
    assert rng.calls[0][0] == "hit"
    assert rng.calls[1][0] == "hit"


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
    assert result.state.players[0].contraband.alcohol_barrels == 50


def test_buy_capacity_capped_by_carried_barrels_already_on_foot():
    # on foot tank=50, already carrying 30 -> free capacity 20, even though the roll is 200.
    st = _state(ln=4, ka=100000, vehicle=0, barrels=30)
    rng = _StubRng(200, 5, 20)
    result = run_pure(HANDLERS["pub.drink"], _scripted(20), state=st, rng=rng)
    assert result.state.players[0].contraband.alcohol_barrels == 50  # 30 + 20


def test_buy_stock_under_capacity_is_not_capped():
    # stock roll 100 stays 100 when capacity (on-foot 50 minus 0 carried = 50) -- wait,
    # 100 > 50, so it WOULD cap. Use a vehicle with more room instead (talbot 90, tank=100).
    st = _state(ln=4, ka=100000, vehicle=1, barrels=0)  # talbot 90, tank=100
    rng = _StubRng(60, 5, 60)  # stock=60 < 100 capacity -> uncapped
    result = run_pure(HANDLERS["pub.drink"], _scripted(60), state=st, rng=rng)
    assert result.state.players[0].contraband.alcohol_barrels == 60


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
    assert result.state.players[0].contraband.alcohol_barrels == 0


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
    assert result.state.players[0].contraband.alcohol_barrels == 5


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
    assert result.state.players[0].contraband.alcohol_barrels == 10


def test_sell_settle_moves_money_and_barrels_no_score_effect():
    st = _state(ln=2, ka=1000, barrels=20)
    rng = _StubRng(1, 15)  # sell path, price=15
    result = run_pure(HANDLERS["pub.drink"], _scripted(5), state=st, rng=rng)
    assert result.effects == [MoneyChange(75), BarrelChange(-5)]
    assert result.state.players[0].ka == 1075
    assert result.state.players[0].contraband.alcohol_barrels == 15
    assert result.state.players[0].gf == 0.0  # unchanged -- no score effect on sell


def test_run_pure_clean_for_buy_and_sell():
    st = _state(ln=4, ka=100000)
    rng = _StubRng(150, 7, 0)
    result = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st, rng=rng)
    assert result.status == "completed"

    st2 = _state(ln=2, ka=100000, barrels=3)
    rng2 = _StubRng(1, 20, 0)
    result2 = run_pure(HANDLERS["pub.drink"], _scripted(0), state=st2, rng=rng2)
    assert result2.status == "completed"

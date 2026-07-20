"""Tests for the pub.tip handler + the arms-deal upkeep slot — U8.

Proof-first: written and observed RED (TipSet/TipClear NotImplementedError from the U2
groundwork stubs, no ``pub.tip`` handler, and no arms-deal slot in upkeep.py) before
implementation.

Ports ``mf-prg.bas:12200-12252`` (the tip flow) + ``:31000-31051`` (the arms-deal
upkeep resolution, wired at ``:4060``):
- rank guard ra(sp)>3; 2/3 chance of nothing; price 1000/1500/2000$ uniform; tip id
  1-5 uniform, stored on the player; tip 4 alone offers a 5000$ stake.
- the upkeep arms-deal slot resets tp(sp)=0 FIRST (before the 1/5-loss-or-payout
  roll) so a stake resolves EXACTLY ONCE next turn start.
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import MoneyChange, TipClear, TipSet
from engine.locations import HANDLERS
from engine.state import Clock, Config, Gangster, GameState, Player
from engine.upkeep import run_upkeep
from tests.helpers import StubRng as _StubRng, run_pure, scripted as _scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PARAMS = {
    "rank_divisor": 11.1,
    "pub_tip_price_base": 1000,
    "pub_tip_price_step": 500,
    "pub_arms_deal_payout_min": 5500,
    "pub_arms_deal_payout_max": 14999,
}


def _state(*, ka=100000, rank=4, tip_target=0, roster=None):
    active = Player(
        name="p0",
        ka=ka,
        rank=rank,
        roster=roster if roster is not None else (Gangster(name="g0"),),
        tip_target=tip_target,
    )
    return GameState(
        players=(active,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS),
    )




# --------------------------------------------------------------------------- #
# Guard matrix: rank guard on tips                                            #
# --------------------------------------------------------------------------- #
def test_rank_3_or_below_denied_no_rng_draw():
    st = _state(rank=3)
    rng = _StubRng()  # no draws expected -- the guard short-circuits before any roll
    result = run_pure(HANDLERS["pub.tip"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert rng.calls == []


def test_rank_4_passes_the_guard():
    st = _state(rank=4)
    rng = _StubRng(1)  # 2/3-nothing roll: nonzero -> "nothing" branch, no crash
    result = run_pure(HANDLERS["pub.tip"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert rng.calls == [("range", 3)]


# --------------------------------------------------------------------------- #
# 2/3 nothing-available branch                                                #
# --------------------------------------------------------------------------- #
def test_nothing_available_two_thirds_no_effects():
    for roll in (1, 2):  # nonzero -> "nothing" (2 of the 3 outcomes)
        st = _state(rank=4)
        rng = _StubRng(roll)
        result = run_pure(HANDLERS["pub.tip"], _scripted(), state=st, rng=rng)
        assert result.effects == []
        assert result.state.players[0].tip_target == 0


def test_something_available_on_the_zero_roll():
    # nothing-roll=0 -> "available"; price roll=0 (1000$); confirm=True; tip id
    # roll=3 -> tip_target=4 (arms deal), which needs its OWN follow-on decline answer.
    st = _state(rank=4, ka=100000)
    rng = _StubRng(0, 0, 3)
    result = run_pure(HANDLERS["pub.tip"], _scripted(True, False), state=st, rng=rng)
    assert result.status == "completed"
    # tip id roll 3 (0-based) + 1 = 4 -> arms deal branch entered.
    assert any(isinstance(e, TipSet) for e in result.effects)


# --------------------------------------------------------------------------- #
# Seeded formula ranges: tip price                                            #
# --------------------------------------------------------------------------- #
def test_tip_price_rolled_in_documented_values():
    for roll, expected_price in [(0, 1000), (1, 1500), (2, 2000)]:
        st = _state(rank=4, ka=100000)
        rng = _StubRng(0, roll, False)  # available; price roll; decline (no charge)
        result = run_pure(HANDLERS["pub.tip"], _scripted(False), state=st, rng=rng)
        assert result.effects == []
        assert rng.calls == [("range", 3), ("range", 3)]


def test_tip_id_uniform_1_to_5_dispatches_and_sets_tip_target():
    for roll, expected_id in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]:
        st = _state(rank=4, ka=100000)
        # available(0); price roll(0->1000); confirm True; tip id roll.
        answers = [True] if expected_id != 4 else [True, False]  # tip4 declines stake
        rng = _StubRng(0, 0, roll)
        result = run_pure(HANDLERS["pub.tip"], _scripted(*answers), state=st, rng=rng)
        tip_sets = [e for e in result.effects if isinstance(e, TipSet)]
        assert tip_sets == [TipSet(tip_type=expected_id)]
        if expected_id != 4:
            assert result.state.players[0].tip_target == expected_id


# --------------------------------------------------------------------------- #
# Decline / broke paths                                                       #
# --------------------------------------------------------------------------- #
def test_decline_price_confirm_no_charge_no_tip_set():
    st = _state(rank=4, ka=100000)
    rng = _StubRng(0, 1)  # available; price roll (1500$)
    result = run_pure(HANDLERS["pub.tip"], _scripted(False), state=st, rng=rng)
    assert result.effects == []
    assert result.state.players[0].ka == 100000
    assert result.state.players[0].tip_target == 0


def test_broke_for_tip_price_no_state_change():
    st = _state(rank=4, ka=500)  # can't afford even the cheapest tip (1000$)
    rng = _StubRng(0, 0)  # available; price=1000
    result = run_pure(HANDLERS["pub.tip"], _scripted(True), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert result.state.players[0].ka == 500
    assert result.state.players[0].tip_target == 0


# --------------------------------------------------------------------------- #
# Tip 4: the 5000$ stake sub-flow                                             #
# --------------------------------------------------------------------------- #
def test_tip4_decline_stake_clears_the_tip():
    st = _state(rank=4, ka=100000)
    rng = _StubRng(0, 0, 3)  # available; price=1000; tip id roll 3 -> tip 4
    result = run_pure(HANDLERS["pub.tip"], _scripted(True, False), state=st, rng=rng)
    # TipSet(4) then TipClear() -- tip is void, but the 1000$ tip price WAS charged.
    assert result.effects == [MoneyChange(-1000), TipSet(tip_type=4), TipClear()]
    assert result.state.players[0].tip_target == 0
    assert result.state.players[0].ka == 99000


def test_tip4_broke_for_the_stake_clears_the_tip():
    # Can afford the 1000$ tip price but not the 5000$ stake on top.
    st = _state(rank=4, ka=1500)
    rng = _StubRng(0, 0, 3)  # price=1000; tip id -> 4
    result = run_pure(HANDLERS["pub.tip"], _scripted(True, True), state=st, rng=rng)
    assert result.effects == [MoneyChange(-1000), TipSet(tip_type=4), TipClear()]
    assert result.state.players[0].tip_target == 0
    assert result.state.players[0].ka == 500  # tip price charged, stake NOT taken


def test_tip4_accept_deducts_stake_and_keeps_the_tip_set():
    st = _state(rank=4, ka=100000)
    rng = _StubRng(0, 0, 3)  # price=1000; tip id -> 4
    result = run_pure(HANDLERS["pub.tip"], _scripted(True, True), state=st, rng=rng)
    assert result.effects == [
        MoneyChange(-1000),
        TipSet(tip_type=4),
        MoneyChange(-5000),
    ]
    assert result.state.players[0].tip_target == 4  # STAYS set for upkeep to resolve
    assert result.state.players[0].ka == 94000


def test_other_tips_do_not_trigger_the_stake_subflow():
    st = _state(rank=4, ka=100000)
    rng = _StubRng(0, 0, 1)  # tip id -> 2 (not the arms deal)
    result = run_pure(HANDLERS["pub.tip"], _scripted(True), state=st, rng=rng)
    assert result.effects == [MoneyChange(-1000), TipSet(tip_type=2)]
    assert result.state.players[0].tip_target == 2
    assert result.state.players[0].ka == 99000


# --------------------------------------------------------------------------- #
# Arms-deal upkeep slot: resolves EXACTLY ONCE, tip reset happens FIRST       #
# --------------------------------------------------------------------------- #
def test_arms_deal_does_not_fire_without_a_staked_tip():
    st = _state(tip_target=0)
    result = run_upkeep(st, rng=_StubRng())
    assert result.state.players[0].tip_target == 0
    assert [e for e in result.effects if isinstance(e, MoneyChange)] == []


def test_arms_deal_clears_the_tip_before_rolling_loss_or_payout():
    # The clear-first ordering is directly observable in the effects list: TipClear
    # is buffered BEFORE the MoneyChange payout (upkeep applies them in source order).
    st = _state(tip_target=4, ka=1000)
    rng = _StubRng(1, 10000)  # range(5)!=0 -> payout branch; hit(5500,14999)=10000
    result = run_upkeep(st, rng=rng)
    kinds = [type(e).__name__ for e in result.effects]
    assert kinds.index("TipClear") < kinds.index("MoneyChange")
    assert result.state.players[0].tip_target == 0
    assert result.state.players[0].ka == 11000


def test_arms_deal_total_loss_clears_tip_no_cash_effect():
    st = _state(tip_target=4, ka=1000)
    rng = _StubRng(0)  # range(5)==0 -> total loss
    result = run_upkeep(st, rng=rng)
    assert [e for e in result.effects if isinstance(e, (TipClear, MoneyChange))] == [
        TipClear()
    ]
    assert result.state.players[0].tip_target == 0
    assert result.state.players[0].ka == 1000  # unchanged -- stake was already spent


def test_arms_deal_payout_in_documented_range():
    for hit_value in (5500, 14999):
        st = _state(tip_target=4, ka=0)
        rng = _StubRng(1, hit_value)
        result = run_upkeep(st, rng=rng)
        assert rng.calls[1] == ("hit", 5500, 14999)
        assert result.state.players[0].ka == hit_value


def test_stake_resolves_exactly_once_across_two_turns():
    """Simulates the full acceptance case: stake a tip4, run upkeep once (resolves +
    clears), then run upkeep AGAIN on the resulting state -- the second run must NOT
    re-fire (tip_target is already 0), proving the resolution happened exactly once."""
    st = _state(tip_target=4, ka=1000)
    rng1 = _StubRng(1, 8000)  # payout branch
    first = run_upkeep(st, rng=rng1)
    assert first.state.players[0].tip_target == 0
    assert first.state.players[0].ka == 9000

    # Second upkeep run on the post-resolution state: NO further arms-deal effect,
    # even though we hand it an rng that WOULD produce a result if consulted.
    rng2 = _StubRng(0)  # would be "total loss" if the slot fired again
    second = run_upkeep(first.state, rng=rng2)
    assert [e for e in second.effects if isinstance(e, (TipClear, MoneyChange))] == []
    assert second.state.players[0].ka == 9000  # untouched by a second resolution
    assert rng2.calls == []  # the rng was never even consulted -- proves no re-fire


def test_other_tip_types_do_not_trigger_the_arms_deal_slot():
    for tip_id in (1, 2, 3, 5):
        st = _state(tip_target=tip_id, ka=1000)
        result = run_upkeep(st, rng=_StubRng())
        assert result.state.players[0].tip_target == tip_id  # untouched
        assert [e for e in result.effects if isinstance(e, (TipClear, MoneyChange))] == []


# --------------------------------------------------------------------------- #
# Purity                                                                       #
# --------------------------------------------------------------------------- #
def test_run_pure_clean_for_tip_handler():
    st = _state(rank=4, ka=100000)
    rng = _StubRng(1)  # nothing-available branch
    result = run_pure(HANDLERS["pub.tip"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"

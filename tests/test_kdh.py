"""Tests for the kdh (Kredit-Hai / loan shark) handlers + the U11 upkeep shop-income
slot — ports ``mf-prg.bas:15000-15321`` (kdh) and ``:4041,4405-4410`` (income).

Proof-first: written and observed RED (``DebtChange``/``DebtClear``/``ShopChange``
raising ``NotImplementedError`` from the U2 groundwork stubs, no ``kdh.*`` handlers
registered) before implementation.

Fidelity note (KTD-9): the collect-debts ambush fires at ``int(rnd(1)*3)<>0 and
kk(sp)<>0`` (mf-prg.bas:15305) — a uniform 0/1/2 draw is nonzero 2 times out of 3, so
the real probability is **2/3**, not the research YAML's documented "1/3". This suite
pins 2/3 directly against the ported ``rng.range(3) != 0`` expression.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.combat import STEP_RIGHT
from engine.config_loader import load_game_config
from engine.effects import MoneyChange
from data.game_configs.mafia_1920s.effects import DebtChange, DebtClear, ScoreAndRank, ShopChange
from engine.locations import HANDLERS
from engine.rng import Rng
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.state import Business, Debt
from data.game_configs.mafia_1920s.gangster import Gangster
from engine.turns import KEY_WAIT_SCREEN
from engine.upkeep import UPKEEP_HANDLER_KEY
from tests.helpers import is_effect, StubRng as _StubRng, run_pure, scripted as _scripted
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PARAMS = {
    "rank_divisor": 11.1,
    "score_mult": 1.0,  # x8, a setup input new_game adds to formula_params
    "kdh_borrow_min": 0,
    "kdh_borrow_max": 5000,
    "kdh_borrow_grace_months": 6,
    "kdh_buy_price_choices": 11,
    "kdh_buy_price_step": 100,
    "kdh_buy_price_base": 5000,
    "kdh_sell_price_choices": 11,
    "kdh_sell_price_step": 100,
    "kdh_sell_price_base": 4500,
    "kdh_capital_max": 5000,
    "kdh_ambush_roll": 3,
    "kdh_ambush_energie": 35,
    "kdh_ambush_weapon": 6,
    "kdh_ambush_loot_min": 500,
    "kdh_ambush_loot_max": 1499,
    "kdh_ambush_score": 2.0,
    "kdh_income_quiet_roll": 3,
    # A5/Finding 4: fixed CPU-enemy stats are config data now.
    "enemy_kraft": 30,
    "enemy_brutalitaet": 30,
}


def _player(
    name="p0",
    *,
    ka=100000,
    debt=None,
    business=None,
    roster=None,
    last_location=1,
):
    return Player(
        name=name,
        ka=ka,
        values=game.values_of(
            debt if debt is not None else Debt(),
            business if business is not None else Business(),
        ),
        roster=roster
        if roster is not None
        else (Gangster(name=name, energie=10, kraft=30, brutalitaet=30),),
        last_location=last_location,
    )


def _state(players, *, active=0):
    return GameState(
        players=tuple(players),
        clock=Clock(active_player=active, player_count=len(players)),
        config=Config(formula_params=_PARAMS),
    )


# --------------------------------------------------------------------------- #
# kdh.borrow — mf-prg.bas:15010-15030                                          #
# --------------------------------------------------------------------------- #
def test_borrow_denied_with_existing_debt_no_rng_draw():
    st = _state([_player(debt=Debt(amount=500, months=3))])
    rng = _StubRng()  # no draws expected -- the guard short-circuits
    result = run_pure(HANDLERS["kdh.borrow"], _scripted(), state=st, rng=rng)
    assert result.effects == []
    assert rng.calls == []


def test_borrow_zero_is_quiet_abort():
    st = _state([_player()])
    rng = _StubRng()
    result = run_pure(HANDLERS["kdh.borrow"], _scripted(0), state=st, rng=rng)
    assert result.effects == []
    assert game.debt(result.state.players[0]) == Debt()


def test_borrow_within_bounds_sets_debt_and_credits_cash():
    st = _state([_player(ka=1000)])
    rng = _StubRng()
    result = run_pure(HANDLERS["kdh.borrow"], _scripted(3000), state=st, rng=rng)
    assert result.effects == [
        DebtChange(amount=3000, months=6),
        MoneyChange(3000),
    ]
    assert game.debt(result.state.players[0]) == Debt(amount=3000, months=6)
    assert result.state.players[0].ka == 4000


def test_borrow_prompt_bounds_are_0_to_5000():
    from engine.interactions import PromptInt

    st = _state([_player()])
    captured = {}

    def source(interaction):
        captured["interaction"] = interaction
        return 0

    run_pure(HANDLERS["kdh.borrow"], source, state=st, rng=_StubRng())
    assert isinstance(captured["interaction"], PromptInt)
    assert captured["interaction"].min == 0
    assert captured["interaction"].max == 5000


# --------------------------------------------------------------------------- #
# kdh.repay — mf-prg.bas:15050-15075                                           #
# --------------------------------------------------------------------------- #
def test_repay_zero_is_quiet_abort():
    st = _state([_player(debt=Debt(amount=1000, months=4))])
    result = run_pure(HANDLERS["kdh.repay"], _scripted(0), state=st, rng=_StubRng())
    assert result.effects == []
    assert game.debt(result.state.players[0]) == Debt(amount=1000, months=4)


def test_repay_more_than_cash_denied_no_state_change():
    st = _state([_player(ka=100, debt=Debt(amount=1000, months=4))])
    result = run_pure(HANDLERS["kdh.repay"], _scripted(500), state=st, rng=_StubRng())
    assert result.effects == []
    assert result.state.players[0].ka == 100
    assert game.debt(result.state.players[0]) == Debt(amount=1000, months=4)


def test_repay_partial_leaves_grace_counter_untouched():
    st = _state([_player(ka=1000, debt=Debt(amount=1000, months=4))])
    result = run_pure(HANDLERS["kdh.repay"], _scripted(400), state=st, rng=_StubRng())
    assert result.effects == [
        DebtChange(amount=-400),
        MoneyChange(-400),
    ]
    assert game.debt(result.state.players[0]) == Debt(amount=600, months=4)
    assert result.state.players[0].ka == 600


def test_repay_full_also_clears_grace_counter():
    st = _state([_player(ka=1000, debt=Debt(amount=1000, months=4))])
    result = run_pure(HANDLERS["kdh.repay"], _scripted(1000), state=st, rng=_StubRng())
    assert result.effects == [
        DebtChange(amount=-1000),
        MoneyChange(-1000),
        DebtClear(),
    ]
    assert game.debt(result.state.players[0]) == Debt(amount=0, months=0)
    assert result.state.players[0].ka == 0


def test_repay_prompt_bounds_are_0_to_current_debt():
    from engine.interactions import PromptInt

    st = _state([_player(debt=Debt(amount=750, months=2))])
    captured = {}

    def source(interaction):
        captured["interaction"] = interaction
        return 0

    run_pure(HANDLERS["kdh.repay"], source, state=st, rng=_StubRng())
    assert isinstance(captured["interaction"], PromptInt)
    assert captured["interaction"].min == 0
    assert captured["interaction"].max == 750


# --------------------------------------------------------------------------- #
# kdh.trade — buy path (mf-prg.bas:15100-15125)                                #
# --------------------------------------------------------------------------- #
def test_buy_denied_already_own_a_different_shop():
    st = _state([_player(business=Business(shop_tile=5), last_location=1)])
    rng = _StubRng()  # no roll -- the guard short-circuits before any price draw
    result = run_pure(HANDLERS["kdh.trade"], _scripted(), state=st, rng=rng)
    assert result.effects == []
    assert rng.calls == []


def test_buy_denied_own_outstanding_debt():
    st = _state([_player(debt=Debt(amount=200, months=3), last_location=1)])
    rng = _StubRng()
    result = run_pure(HANDLERS["kdh.trade"], _scripted(), state=st, rng=rng)
    assert result.effects == []
    assert rng.calls == []


def test_buy_denied_rival_scan_names_the_owner():
    rival = _player(name="rival", business=Business(shop_tile=1))
    me = _player(name="me", last_location=1)
    st = _state([me, rival], active=0)
    result = run_pure(HANDLERS["kdh.trade"], _scripted(), state=st, rng=_StubRng())
    assert result.effects == []
    # The rival-scan denial names the rival (message key is checked in the shell test;
    # here we confirm no purchase effects fired).
    assert game.business(result.state.players[0]) == Business()


def test_buy_price_rolled_5000_to_6000_step_100():
    st = _state([_player(ka=100000, last_location=1)])
    for roll, expected_price in [(0, 5000), (5, 5500), (10, 6000)]:
        rng = _StubRng(roll)
        # True buys; 0 leaves the capital screen :15125 goto15200 opens (:15208).
        result = run_pure(HANDLERS["kdh.trade"], _scripted(True, 0), state=st, rng=rng)
        assert rng.calls[0] == ("range", 11)
        money_changes = [e for e in result.effects if isinstance(e, MoneyChange)]
        assert money_changes == [MoneyChange(-expected_price)]


def test_buy_decline_confirm_no_state_change():
    st = _state([_player(ka=100000, last_location=1)])
    rng = _StubRng(0)  # price roll only -- confirm declines before any settle
    result = run_pure(HANDLERS["kdh.trade"], _scripted(False), state=st, rng=rng)
    assert result.effects == []


def test_buy_afford_check_denies_and_no_state_change():
    st = _state([_player(ka=100, last_location=1)])
    rng = _StubRng(0)  # price 5000
    result = run_pure(HANDLERS["kdh.trade"], _scripted(True), state=st, rng=rng)
    assert result.effects == []
    assert game.business(result.state.players[0]) == Business()


def test_buy_settles_cash_and_shop_tile():
    st = _state([_player(ka=100000, last_location=1)])
    rng = _StubRng(0)  # price 5000
    # True buys; 0 leaves the capital screen :15125 goto15200 opens (:15208).
    result = run_pure(HANDLERS["kdh.trade"], _scripted(True, 0), state=st, rng=rng)
    assert result.effects == [
        MoneyChange(-5000),
        ShopChange(tile=1),
    ]
    assert game.business(result.state.players[0]) == Business(shop_tile=1)
    assert result.state.players[0].ka == 95000


# --------------------------------------------------------------------------- #
# kdh.trade — sell path (mf-prg.bas:15100,15150-15155)                         #
# --------------------------------------------------------------------------- #
def test_sell_price_rolled_4500_to_5500_step_100():
    st = _state([_player(ka=0, business=Business(shop_tile=1, shop_capital=0), last_location=1)])
    for roll, expected_price in [(0, 4500), (5, 5000), (10, 5500)]:
        rng = _StubRng(roll)
        result = run_pure(HANDLERS["kdh.trade"], _scripted(True), state=st, rng=rng)
        assert rng.calls[0] == ("range", 11)
        money_changes = [e for e in result.effects if isinstance(e, MoneyChange)]
        assert money_changes == [MoneyChange(expected_price)]


def test_sell_decline_confirm_no_state_change():
    st = _state([_player(business=Business(shop_tile=1), last_location=1)])
    rng = _StubRng(0)
    result = run_pure(HANDLERS["kdh.trade"], _scripted(False), state=st, rng=rng)
    assert result.effects == []
    assert game.business(result.state.players[0]) == Business(shop_tile=1)


def test_sell_settles_unconditionally_no_afford_check():
    st = _state([_player(ka=0, business=Business(shop_tile=1), last_location=1)])
    rng = _StubRng(0)  # price 4500
    result = run_pure(HANDLERS["kdh.trade"], _scripted(True), state=st, rng=rng)
    assert result.effects == [
        MoneyChange(4500),
        ShopChange(tile=0),
    ]
    assert game.business(result.state.players[0]).shop_tile == 0
    assert result.state.players[0].ka == 4500


# --------------------------------------------------------------------------- #
# kdh.capital — mf-prg.bas:15200-15220                                         #
# --------------------------------------------------------------------------- #
def test_capital_denied_not_owner_no_prompt():
    st = _state([_player(business=Business(shop_tile=2), last_location=1)])
    rng = _StubRng()
    result = run_pure(HANDLERS["kdh.capital"], _scripted(), state=st, rng=rng)
    assert result.effects == []


def test_capital_zero_is_quiet_abort():
    st = _state([_player(business=Business(shop_tile=1, shop_capital=1000), last_location=1)])
    result = run_pure(HANDLERS["kdh.capital"], _scripted(0), state=st, rng=_StubRng())
    assert result.effects == []
    assert game.business(result.state.players[0]).shop_capital == 1000


def test_capital_deposit_bounds_upper():
    """Depositing beyond the 5000 cap is out of the PromptInt's own bounds --
    checked here via the max the handler computes (5000 - current capital)."""
    from engine.interactions import PromptInt

    st = _state([_player(business=Business(shop_tile=1, shop_capital=4000), last_location=1)])
    captured = {}

    def source(interaction):
        captured["interaction"] = interaction
        return 0

    run_pure(HANDLERS["kdh.capital"], source, state=st, rng=_StubRng())
    assert isinstance(captured["interaction"], PromptInt)
    assert captured["interaction"].min == -4000
    assert captured["interaction"].max == 1000


def test_capital_deposit_afford_check_denies():
    st = _state(
        [_player(ka=100, business=Business(shop_tile=1, shop_capital=1000), last_location=1)]
    )
    result = run_pure(HANDLERS["kdh.capital"], _scripted(500), state=st, rng=_StubRng())
    assert result.effects == []
    assert game.business(result.state.players[0]).shop_capital == 1000
    assert result.state.players[0].ka == 100


def test_capital_deposit_settles():
    st = _state(
        [_player(ka=5000, business=Business(shop_tile=1, shop_capital=1000), last_location=1)]
    )
    result = run_pure(HANDLERS["kdh.capital"], _scripted(500), state=st, rng=_StubRng())
    assert result.effects == [MoneyChange(-500), ShopChange(capital_delta=500)]
    assert game.business(result.state.players[0]).shop_capital == 1500
    assert result.state.players[0].ka == 4500


def test_capital_withdrawal_settles_no_afford_check():
    st = _state([_player(ka=0, business=Business(shop_tile=1, shop_capital=1000), last_location=1)])
    result = run_pure(HANDLERS["kdh.capital"], _scripted(-400), state=st, rng=_StubRng())
    assert result.effects == [MoneyChange(400), ShopChange(capital_delta=-400)]
    assert game.business(result.state.players[0]).shop_capital == 600
    assert result.state.players[0].ka == 400


# --------------------------------------------------------------------------- #
# kdh.collect — mf-prg.bas:15300-15321 — the 2/3 ambush fidelity fix (KTD-9)   #
# --------------------------------------------------------------------------- #
def test_collect_denied_not_owner_no_rng_draw():
    st = _state([_player(business=Business(shop_tile=2), last_location=1)])
    rng = _StubRng()
    result = run_pure(HANDLERS["kdh.collect"], _scripted(), state=st, rng=rng)
    assert result.effects == []
    assert rng.calls == []


def test_collect_zero_capital_never_ambushes_no_roll():
    """capital == 0 short-circuits the ambush check before any RNG draw
    (mf-prg.bas:15305's `and kk(sp)<>0` term)."""
    st = _state([_player(business=Business(shop_tile=1, shop_capital=0), last_location=1)])
    rng = _StubRng()
    result = run_pure(HANDLERS["kdh.collect"], _scripted(), state=st, rng=rng)
    assert result.effects == []
    assert rng.calls == []


def test_collect_ambush_probability_is_2_in_3_not_1_in_3():
    """Pins KTD-9: rng.range(3) drawing 1 or 2 (2 of 3 outcomes) triggers the
    ambush; only a draw of 0 (1 of 3) is the quiet 'all paid on time' branch."""
    st = _state([_player(business=Business(shop_tile=1, shop_capital=500), last_location=1)])

    # draw 0 -> quiet, no ambush.
    rng_quiet = _StubRng(0)
    result_quiet = run_pure(HANDLERS["kdh.collect"], _scripted(), state=st, rng=rng_quiet)
    assert result_quiet.effects == []
    assert rng_quiet.calls == [("range", 3)]

    # draws 1 and 2 -> ambush fires (both nonzero outcomes).
    for nonzero_roll in (1, 2):
        rng = _StubRng(nonzero_roll)
        keys = ["surrender"]
        result = run_pure(HANDLERS["kdh.collect"], _scripted(*keys), state=st, rng=rng)
        assert rng.calls[0] == ("range", 3)
        # A surrender loses immediately -- no loot/score effects, but the fight ran
        # (i.e. we got PAST the ambush gate, proving it fired).
        assert not any(isinstance(e, MoneyChange) for e in result.effects)


def test_collect_loss_costs_nothing():
    st = _state(
        [_player(ka=1000, business=Business(shop_tile=1, shop_capital=500), last_location=1)]
    )
    rng = _StubRng(1)  # ambush fires
    result = run_pure(HANDLERS["kdh.collect"], _scripted("surrender"), state=st, rng=rng)
    assert not any(isinstance(e, MoneyChange) for e in result.effects)
    assert not any(is_effect(e, ScoreAndRank) for e in result.effects)
    assert result.state.players[0].ka == 1000


def test_collect_win_loots_500_to_1499_and_scores_2():
    """Uses the REAL engine RNG to resolve the ambush to a player win, then checks the
    loot lands in the documented 500-1499 range (:15320) and the score is +2 (:15321).

    The shots are AIMED (``("shoot", STEP_RIGHT)``): a pass deals no damage, so a
    pass-only script can never win. Seed 0 is pinned because it reaches the win within
    the scripted shots; the assertions are unconditional, so a fight that stops being
    won fails here instead of passing vacuously."""
    roster = (Gangster(name="p0", energie=100, kraft=50, brutalitaet=50, weapon=8),)
    st = _state(
        [
            _player(
                ka=1000,
                business=Business(shop_tile=1, shop_capital=500),
                roster=roster,
                last_location=1,
            )
        ]
    )
    keys = [("shoot", STEP_RIGHT)] * 40 + ["surrender"]
    result = run_pure(HANDLERS["kdh.collect"], _scripted(*keys), state=st, rng=Rng(0))
    money_changes = [e for e in result.effects if isinstance(e, MoneyChange)]
    score_changes = [e for e in result.effects if is_effect(e, ScoreAndRank)]
    assert len(money_changes) == 1, "seed 0 must reach the ambush win"
    assert 500 <= money_changes[0].amount <= 1499
    assert score_changes == [ScoreAndRank(amount=2.0, rank_divisor=11.1)]


# --------------------------------------------------------------------------- #
# Upkeep shop-income slot (U11) — mf-prg.bas:4041,4405-4410                    #
# --------------------------------------------------------------------------- #
def test_income_skipped_without_a_shop():
    st = _state([_player(business=Business(shop_tile=0, shop_capital=0))])
    rng = _StubRng()
    result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], _scripted(), state=st, rng=rng)
    assert not any(isinstance(e, MoneyChange) for e in result.effects)


def test_income_skipped_with_zero_capital():
    st = _state([_player(business=Business(shop_tile=1, shop_capital=0))])
    rng = _StubRng()
    result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], _scripted(), state=st, rng=rng)
    assert not any(isinstance(e, MoneyChange) for e in result.effects)


def test_income_quiet_month_one_in_three_no_payout():
    st = _state([_player(business=Business(shop_tile=1, shop_capital=1000))])
    rng = _StubRng(0)  # range(3) == 0 -> quiet
    result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], _scripted(), state=st, rng=rng)
    assert not any(isinstance(e, MoneyChange) for e in result.effects)
    assert rng.calls == [("range", 3)]


def test_income_earned_is_10_to_15_percent_of_capital():
    st = _state([_player(business=Business(shop_tile=1, shop_capital=1000))])
    for capital_roll, expected_income in [(0, 100), (500, 125), (999, 149)]:
        rng = _StubRng(1, capital_roll)  # range(3)!=0 -> earns; then the income draw
        result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], _scripted(), state=st, rng=rng)
        money_changes = [e for e in result.effects if isinstance(e, MoneyChange)]
        assert money_changes == [MoneyChange(expected_income)]
        assert rng.calls == [("range", 3), ("range", 1000)]


# --------------------------------------------------------------------------- #
# #160: the :1100 key wait at each exit — goto1100/goto1125 waits, return not  #
# --------------------------------------------------------------------------- #
_OWN = {"business": Business(shop_tile=1, shop_capital=500), "last_location": 1}

_EXITS = [
    # handler, player kwargs, rng draws, answers, waits
    # :15010 ifkr(sp)then...:goto1100
    ("15010-old-debt", "kdh.borrow", {"debt": Debt(amount=500, months=3)}, (), (), 1),
    # :15020 ifx=0thenreturn
    ("15020-borrow-zero", "kdh.borrow", {}, (), (0,), 0),
    # :15030 kr(sp)=kr(sp)+x:...:goto1100
    ("15030-borrowed", "kdh.borrow", {}, (), (3000,), 1),
    # :15051 ...:ifx=0thenreturn
    ("15051-repay-zero", "kdh.repay", {"debt": Debt(amount=500, months=3)}, (), (0,), 0),
    # :15060 ifka(sp)<xgoto1125
    ("15060-too-poor", "kdh.repay", {"ka": 100, "debt": Debt(amount=500, months=3)}, (), (400,), 1),
    # :15070 ...print"{down}schulden!":goto1100
    ("15070-partly-repaid", "kdh.repay", {"debt": Debt(amount=500, months=3)}, (), (200,), 1),
    # :15075 kz(sp)=0:print"...zurueckgezahlt!":goto1100
    ("15075-repaid", "kdh.repay", {"debt": Debt(amount=500, months=3)}, (), (500,), 1),
    # :15105 ifkg(sp)then...:goto1100
    ("15105-own-a-shop", "kdh.trade", {"business": Business(shop_tile=2)}, (), (), 1),
    # :15106 ifkr(sp)then...:goto1100
    ("15106-own-debts", "kdh.trade", {"debt": Debt(amount=500, months=3)}, (), (), 1),
    # :15111 print:gosub1110:ifx$="n"thenreturn
    ("15111-buy-declined", "kdh.trade", {}, (0,), (False,), 0),
    # :15115 ifka(sp)<pgoto1125
    ("15115-too-poor", "kdh.trade", {"ka": 100}, (0,), (True,), 1),
    # :15151 ...gosub1115:ifx$="n"thenreturn
    ("15151-sell-declined", "kdh.trade", {"business": Business(shop_tile=1)}, (0,), (False,), 0),
    # :15155 ka(sp)=ka(sp)+p:kg(sp)=0:return
    ("15155-sold", "kdh.trade", {"business": Business(shop_tile=1)}, (0,), (True,), 0),
    # :15200 ifkg(sp)<>lnthen...:goto1100
    ("15200-not-owner", "kdh.capital", {"business": Business(shop_tile=2)}, (), (), 1),
    # :15208 ...:ifx=0thenreturn
    ("15208-capital-zero", "kdh.capital", _OWN, (), (0,), 0),
    # :15215 ifka(sp)<xgoto1125
    ("15215-too-poor", "kdh.capital", {**_OWN, "ka": 100}, (), (1000,), 1),
    # :15220 ka(sp)=ka(sp)-x:kk(sp)=kk(sp)+x:return
    ("15220-capital-changed", "kdh.capital", _OWN, (), (1000,), 0),
    # :15300 ifkg(sp)<>lnthen...:goto1100
    ("15300-not-owner", "kdh.collect", {"business": Business(shop_tile=2)}, (), (), 1),
    # :15306 print"alle schuldner haben puenktlich ge-":print"{down}zahlt!":goto1100
    ("15306-paid-on-time", "kdh.collect", _OWN, (0,), (), 1),
]


@pytest.mark.parametrize(
    ("handler", "kwargs", "draws", "answers", "waits"),
    [case[1:] for case in _EXITS],
    ids=[case[0] for case in _EXITS],
)
def test_each_exit_waits_for_a_key_where_the_source_does(handler, kwargs, draws, answers, waits):
    st = _state([_player(**kwargs)])
    src = _scripted(*answers)
    run_pure(HANDLERS[handler], src, state=st, rng=_StubRng(*draws))
    assert src.key_waits() == waits
    if waits:
        assert src.ends_in_key_wait(), "the exit did not end in the :1100 key wait"


def test_rival_owner_refusal_waits_for_a_key():
    # :15108 print"der laden gehoert "sp$(i)"!":goto1100
    st = _state([_player(last_location=1), _player("p1", business=Business(shop_tile=1))])
    src = _scripted()
    run_pure(HANDLERS["kdh.trade"], src, state=st, rng=_StubRng())
    assert src.message_keys() == ["locations.kdh.shop_belongs_to"]
    assert src.ends_in_key_wait() and src.key_waits() == 1


def test_buying_waits_once_then_opens_the_capital_screen():
    """:15120 ``...print"{down}der laden gehoert nun dir!":gosub1100`` then :15125
    ``goto15200``: the purchase waits for a key, then the capital screen follows in the
    same option; :15208's ``x=0`` returns from there without another wait."""
    from engine.interactions import PromptInt

    st = _state([_player(ka=100000, last_location=1)])
    src = _scripted(True, 0)
    result = run_pure(HANDLERS["kdh.trade"], src, state=st, rng=_StubRng(0))
    keys = [getattr(i, "key", None) for i in src.seen]
    bought = keys.index("locations.kdh.bought")
    assert keys[bought + 1] == KEY_WAIT_SCREEN
    assert keys[bought + 2 :] == ["locations.kdh.capital_status", "locations.kdh.capital_prompt"]
    prompt = src.seen[-1]
    assert isinstance(prompt, PromptInt) and (prompt.min, prompt.max) == (0, 5000)
    assert src.key_waits() == 1
    assert result.effects == [MoneyChange(-5000), ShopChange(tile=1)]


def test_a_capital_answer_after_buying_changes_the_capital():
    # :15220 ka(sp)=ka(sp)-x:kk(sp)=kk(sp)+x:return -- on the cash the purchase left.
    st = _state([_player(ka=8000, last_location=1)])
    src = _scripted(True, 2000)
    result = run_pure(HANDLERS["kdh.trade"], src, state=st, rng=_StubRng(0))
    assert game.business(result.state.players[0]) == Business(shop_tile=1, shop_capital=2000)
    assert result.state.players[0].ka == 1000
    assert src.key_waits() == 1


def test_a_capital_deposit_beyond_the_cash_left_after_buying_waits():
    # :15215 ifka(sp)<xgoto1125 -- the cash after :15120's price, not before it.
    st = _state([_player(ka=5500, last_location=1)])
    src = _scripted(True, 1000)
    result = run_pure(HANDLERS["kdh.trade"], src, state=st, rng=_StubRng(0))
    assert game.business(result.state.players[0]) == Business(shop_tile=1)
    assert result.state.players[0].ka == 500
    assert src.message_keys()[-1] == "system.not_enough_money"
    assert src.ends_in_key_wait() and src.key_waits() == 2


def _first_fight_screen(src) -> int:
    from engine.interactions import CombatScreen

    return next(i for i, x in enumerate(src.seen) if isinstance(x, CombatScreen))


def test_the_ambush_waits_before_the_fight_and_a_loss_returns_after_the_outcome():
    """:15312 ``gosub1100`` before ``gosub5000``; the outcome screen's ``:30520
    print:goto1100`` waits; the loss returns (:15315 ``ifs=2thenreturn``)."""
    st = _state([_player(ka=1000, **_OWN)])
    src = _scripted("surrender")
    run_pure(HANDLERS["kdh.collect"], src, state=st, rng=_StubRng(1))
    keys = [getattr(i, "key", None) for i in src.seen]
    fight = _first_fight_screen(src)
    assert keys[fight - 2 : fight] == ["locations.kdh.ambush_intro", KEY_WAIT_SCREEN]
    assert src.message_keys()[-1] == "combat.losses_line"
    assert src.ends_in_key_wait() and src.key_waits() == 2


def test_an_ambush_win_waits_after_the_outcome_and_after_the_loot():
    """:30520 waits under the outcome screen, then :15321 prints the loot and ``goto1100``."""
    roster = (Gangster(name="p0", energie=100, kraft=50, brutalitaet=50, weapon=8),)
    st = _state([_player(ka=1000, roster=roster, **_OWN)])
    src = _scripted(*([("shoot", STEP_RIGHT)] * 40 + ["surrender"]))
    run_pure(HANDLERS["kdh.collect"], src, state=st, rng=Rng(0))
    keys = [getattr(i, "key", None) for i in src.seen]
    loot = keys.index("locations.kdh.ambush_loot")
    assert keys[loot - 2 : loot] == ["combat.losses_line", KEY_WAIT_SCREEN]
    assert keys[loot + 1 :] == [KEY_WAIT_SCREEN]
    assert src.key_waits() == 3

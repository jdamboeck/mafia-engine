"""Tests for the sph (Spielhoelle / casino) handler — U4, the warm-up handler.

Proof-first: written and observed RED (handler module missing) before implementation.

Ports the casino block ``mf-prg.bas:16010-16040`` verbatim:
- 3-game menu (poker x=1 / black jack x=2 / roulette x=3); choice 0 cancels (16015).
- wager against displayed cash; wager <= 0 aborts (16020); wager > cash -> not enough (16025).
- single RNG resolve: win iff ``rng.range(1+x)==0`` (16030); gross payout
  ``int(stake*(0.5+x))`` re-added on win (16040). Net delta = payout-stake (win) / -stake (loss).

Cash math (16026 deducts stake up front, 16040 re-adds gross on win) is modelled as one
``MoneyChange(payout-stake)`` on win and ``MoneyChange(-stake)`` on loss.
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import MoneyChange
from engine.interactions import PromptChoice, PromptInt
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster
from tests.helpers import run_pure, scripted as _scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# Load the config by path so "sph" registers.
load_game_config(_CONFIG_DIR)

_FORMULA_PARAMS = {"casino_payout_offset": 0.5}


class _StubRng:
    """A minimal rng exposing range(); returns a scripted value per call."""

    def __init__(self, *values):
        self._it = iter(values)
        self.calls = []

    def range(self, n):
        self.calls.append(n)
        return next(self._it)


def _state(*, ka=5000, active=0, players=1):
    plist = [Player(ka=ka, roster=[Gangster()]) for _ in range(players)]
    return GameState(
        players=plist,
        clock=Clock(active_player=active, player_count=players),
        config=Config(formula_params=_FORMULA_PARAMS),
    )


def test_menu_cancel_returns_no_effect():
    # PromptChoice cancelled (choice 0 in the source == cancel via CANCEL sentinel).
    st = _state(ka=5000)
    from engine.interactions import CANCEL

    result = run_pure(HANDLERS["sph"], _scripted(CANCEL), state=st, rng=_StubRng())
    assert result.status == "cancelled"
    assert result.effects == []
    assert result.state.players[0].ka == 5000


def test_wager_zero_aborts_no_money_change():
    # Choose poker (index 0 -> x=1), then wager 0 -> quiet abort (16020).
    st = _state(ka=5000)
    result = run_pure(HANDLERS["sph"], _scripted(0, 0), state=st, rng=_StubRng())
    assert result.status == "completed"
    assert result.effects == []
    assert result.state.players[0].ka == 5000


def test_wager_above_cash_shows_not_enough_money():
    # Choose poker, wager 6000 > cash 5000 -> not_enough_money, abort (16025).
    # run_pure confirms zero effects; then drive the generator directly to observe the
    # auto-acked ShowMessage (the driver never consults input_source for a ShowMessage).
    st = _state(ka=5000)
    result = run_pure(HANDLERS["sph"], _scripted(0, 6000), state=st, rng=_StubRng())
    assert result.status == "completed"
    assert result.effects == []
    assert result.state.players[0].ka == 5000

    from engine.interactions import Ack, Ctx

    ctx = Ctx(state=st, rng=_StubRng())
    gen = HANDLERS["sph"](ctx)
    emitted = []
    interaction = next(gen)
    # Drive by interaction type: choice 0 (poker), wager 6000, ack every ShowMessage.
    try:
        while True:
            emitted.append(interaction)
            if isinstance(interaction, PromptChoice):
                interaction = gen.send(0)
            elif isinstance(interaction, PromptInt):
                interaction = gen.send(6000)
            else:  # ShowMessage -> ack
                interaction = gen.send(Ack)
    except StopIteration:
        pass

    keys = [getattr(i, "key", None) for i in emitted]
    assert "system.not_enough_money" in keys
    assert ctx._buffer == []  # no MoneyChange buffered on the abort path


def test_forced_win_roulette_pays_net_plus_250():
    # Roulette x=3, stake 100. Forced win: rng.range(1+3)=rng.range(4)==0.
    # payout = int(100*(0.5+3)) = int(350) = 350; net delta = payout-stake = +250.
    st = _state(ka=5000)
    rng = _StubRng(0)  # win
    result = run_pure(HANDLERS["sph"], _scripted(2, 100), state=st, rng=rng)  # choice 2 -> roulette
    assert result.status == "completed"
    assert result.effects == [MoneyChange(+250)]
    assert result.state.players[0].ka == 5250
    assert rng.calls == [4]  # rng.range(1+x) == range(4)


def test_forced_loss_costs_stake():
    # Poker x=1, stake 100. Forced loss: rng.range(2) != 0. Net delta = -stake = -100.
    st = _state(ka=5000)
    rng = _StubRng(1)  # non-zero -> loss
    result = run_pure(HANDLERS["sph"], _scripted(0, 100), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == [MoneyChange(-100)]
    assert result.state.players[0].ka == 4900
    assert rng.calls == [2]  # rng.range(1+1)


def test_each_game_index_maps_to_right_x():
    # Verify poker=1, blackjack=2, roulette=3 by the rng.range(1+x) call each makes.
    for choice, expected_n in [(0, 2), (1, 3), (2, 4)]:
        st = _state(ka=5000)
        rng = _StubRng(1)  # loss (keeps math simple; we only assert the range arg)
        run_pure(HANDLERS["sph"], _scripted(choice, 100), state=st, rng=rng)
        assert rng.calls == [expected_n]


def test_forced_win_blackjack_payout():
    # Blackjack x=2, stake 200. Win: payout = int(200*(0.5+2)) = int(500) = 500; net +300.
    st = _state(ka=5000)
    rng = _StubRng(0)
    result = run_pure(HANDLERS["sph"], _scripted(1, 200), state=st, rng=rng)
    assert result.effects == [MoneyChange(+300)]

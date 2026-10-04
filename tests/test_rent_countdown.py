"""Tests for the rent countdown and the late-rent consequence (#99) — the upkeep
rent slot, ports ``mf-prg.bas:4045-4046`` and ``4600-4652``.

Proof-first: written and observed RED (the upkeep generator had no rent slot at all —
``rented_months`` never moved, a room once rented was paid forever) before the port.

The source, in the order it runs at turn start:

* ``:4045`` ``ifum(sp)=0goto4050`` — no prepaid months, nothing happens.
* ``:4046`` ``um(sp)=um(sp)-1:ifum(sp)=0thenum(sp)=1:gosub4600`` — count one month
  down; on reaching 0 the counter is put back to 1 and the late-rent routine runs. So
  ``um`` never leaves 1 by itself: every later turn start fines again.
* ``:4605`` ``p=int(rnd(1)*100)+200:ifp>ka(sp)thenp=ka(sp):ifp=0goto4650`` — a fine
  of 200..299$, capped at the cash on hand; only a capped fine of 0 (no cash) evicts.
* ``:4620`` ``ka(sp)=ka(sp)-p:goto1100`` — the fine is taken.
* ``:4651`` ``gz(sp)=1`` — the eviction: every gangster but the boss leaves.
* ``goto1100`` (``:4620``/``:4652``) lands on ``:1100``
  ``print"{down}taste druecken!":poke198,0:wait198,1:poke198,0:return`` — a
  press-a-key pause whose ``return`` closes ``gosub4600``. It is NOT an early exit:
  turn start continues at ``:4050`` and the arms deal (``:4060``) still resolves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.gangster import Gangster
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, RosterTruncate
from data.game_configs.mafia_1920s.effects import RentAccrue
from engine.interactions import ShowMessage
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.state import Business
from engine.strings import Resolver
from engine.upkeep import UPKEEP_HANDLER_KEY, run_upkeep
from tests.helpers import is_effect, StubRng, run_pure, with_tenancy
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PARAMS = dict(load_config(_CONFIG_DIR / "config.yaml")["formula_params"])

_BOSS = Gangster(name="boss", energie=5, kraft=10, brutalitaet=10)
_HIRE_A = Gangster(name="hire-a", energie=5, kraft=20, brutalitaet=20)
_HIRE_B = Gangster(name="hire-b", energie=5, kraft=30, brutalitaet=30)

_RENT_KEYS = ("upkeep.rent_late", "upkeep.rent_evicted")


def _state(
    *,
    um: int,
    ka: int = 1000,
    roster=(_BOSS, _HIRE_A, _HIRE_B),
    business: Business = Business(),
    tip_target: int = 0,
) -> GameState:
    values = game.values_of(business, rented_months=um, tip_target=tip_target)
    player = Player(name="p", ka=ka, roster=roster, values=values)
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS),
    )


def _run(state: GameState, *draws: int):
    """Run upkeep with a scripted RNG; record every narrated message."""
    shown: list[ShowMessage] = []

    def source(interaction):
        if isinstance(interaction, ShowMessage):
            shown.append(interaction)
            return None
        raise AssertionError(f"upkeep must not prompt: {interaction!r}")

    rng = StubRng(*draws)
    result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], source, state=state, rng=rng)
    assert result.status == "completed"
    return result, shown, rng


def _rent_messages(shown):
    return [m for m in shown if m.key in _RENT_KEYS]


# --------------------------------------------------------------------------- #
# :4045 — no prepaid months, no rent slot                                      #
# --------------------------------------------------------------------------- #
def test_no_rent_slot_when_nothing_is_prepaid():
    # :4045 `ifum(sp)=0goto4050` — um stays 0, no draw, no message, cash untouched.
    result, shown, rng = _run(_state(um=0))
    p = result.state.players[0]
    assert game.rented_months(p) == 0
    assert p.ka == 1000
    assert rng.calls == []
    assert _rent_messages(shown) == []
    assert not any(is_effect(e, RentAccrue, MoneyChange, RosterTruncate) for e in result.effects)


# --------------------------------------------------------------------------- #
# :4046 — the countdown                                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("um", [2, 3, 7])
def test_countdown_takes_one_month_per_turn_start(um):
    # :4046 `um(sp)=um(sp)-1` — above 1 the tick is all that happens.
    result, shown, rng = _run(_state(um=um))
    p = result.state.players[0]
    assert game.rented_months(p) == um - 1
    assert p.ka == 1000
    assert rng.calls == []  # no fine rolled while months remain
    assert _rent_messages(shown) == []


def test_countdown_runs_down_turn_by_turn_until_the_fine():
    state = _state(um=3)
    months = []
    for _ in range(2):
        state = _run(state)[0].state
        months.append(game.rented_months(state.players[0]))
    assert months == [2, 1]
    # The third turn start is the last prepaid month running out: the fine.
    result, shown, rng = _run(state, 40)
    assert game.rented_months(result.state.players[0]) == 1
    assert result.state.players[0].ka == 1000 - 240


# --------------------------------------------------------------------------- #
# :4046 -> :4605-4620 — the fine at the last month, and every turn after       #
# --------------------------------------------------------------------------- #
def test_fine_when_the_last_month_runs_out():
    # :4046 `ifum(sp)=0thenum(sp)=1:gosub4600`; :4605 `p=int(rnd(1)*100)+200`
    # -> range(100)=37 gives 237; :4620 `ka(sp)=ka(sp)-p`.
    result, shown, rng = _run(_state(um=1), 37)
    p = result.state.players[0]
    assert rng.calls == [("range", 100)]
    assert p.ka == 1000 - 237
    assert game.rented_months(p) == 1  # put back to 1, never 0
    assert len(p.roster) == 3  # a fine, not an eviction
    assert MoneyChange(-237) in result.effects
    assert _rent_messages(shown) == [ShowMessage("upkeep.rent_late", {"amount": 237}, player=0)]


@pytest.mark.parametrize("draw, fine", [(0, 200), (99, 299)])
def test_fine_spans_200_to_299(draw, fine):
    result, _, _ = _run(_state(um=1), draw)
    assert result.state.players[0].ka == 1000 - fine


def test_fine_recurs_every_turn_after_the_last_month():
    # um is put back to 1, so every later turn start ticks 1 -> 0 and fines again.
    state = _state(um=1)
    fines = []
    for draw in (10, 55, 99):
        before = state.players[0].ka
        result, shown, _ = _run(state, draw)
        state = result.state
        fines.append(before - state.players[0].ka)
        assert game.rented_months(state.players[0]) == 1
    assert fines == [210, 255, 299]


# --------------------------------------------------------------------------- #
# :4605 — the cap at cash                                                      #
# --------------------------------------------------------------------------- #
def test_fine_is_capped_at_the_cash_on_hand():
    # :4605 `ifp>ka(sp)thenp=ka(sp)` — 250 > 150, so only 150 is taken.
    result, shown, _ = _run(_state(um=1, ka=150), 50)
    p = result.state.players[0]
    assert p.ka == 0
    assert len(p.roster) == 3  # capped to a nonzero fine: no eviction
    assert _rent_messages(shown) == [ShowMessage("upkeep.rent_late", {"amount": 150}, player=0)]


def test_fine_equal_to_cash_is_not_capped():
    # `p>ka(sp)` is strict: a 250 fine against exactly 250 takes all of it, no eviction.
    result, shown, _ = _run(_state(um=1, ka=250), 50)
    assert result.state.players[0].ka == 0
    assert len(result.state.players[0].roster) == 3


def test_fine_reads_the_cash_after_this_turns_shop_income():
    # :4041 runs before :4045, so the cap sees the income already paid in. ka=0 plus
    # an income of int((2*100 + 0)/20) = 10 -> the fine is capped at 10, not an eviction.
    state = _state(um=1, ka=0, business=Business(shop_tile=1, shop_capital=100))
    result, shown, rng = _run(state, 1, 0, 50)
    p = result.state.players[0]
    assert rng.calls == [("range", 3), ("range", 100), ("range", 100)]
    assert p.ka == 0
    assert len(p.roster) == 3
    assert _rent_messages(shown) == [ShowMessage("upkeep.rent_late", {"amount": 10}, player=0)]


# --------------------------------------------------------------------------- #
# :4605 -> :4650-4652 — eviction at zero cash                                  #
# --------------------------------------------------------------------------- #
def test_eviction_at_zero_cash_leaves_only_the_boss():
    # :4605 `ifp=0goto4650`; :4651 `gz(sp)=1` — only gangster 1 (the boss) remains.
    state = _state(um=1, ka=0)
    state = with_tenancy(state, ln=2, owner=0)
    result, shown, rng = _run(state, 50)
    p = result.state.players[0]
    assert rng.calls == [("range", 100)]  # the fine is rolled before the cap evicts
    assert [g.name for g in p.roster] == ["boss"]
    assert p.ka == 0
    assert game.rented_months(p) == 1  # :4046 already put it back to 1
    assert game.tenant(result.state, 2) == 0  # uk(ln) is never cleared by the source
    assert RosterTruncate(size=1) in result.effects
    assert not any(isinstance(e, MoneyChange) for e in result.effects)
    assert _rent_messages(shown) == [ShowMessage("upkeep.rent_evicted", player=0)]


def test_eviction_recurs_harmlessly_while_broke():
    state = _state(um=1, ka=0)
    for _ in range(2):
        result, shown, _ = _run(state, 0)
        state = result.state
        assert _rent_messages(shown) == [ShowMessage("upkeep.rent_evicted", player=0)]
    assert [g.name for g in state.players[0].roster] == ["boss"]
    assert game.rented_months(state.players[0]) == 1


# --------------------------------------------------------------------------- #
# goto1100 is a press-key-and-return, NOT an early exit                        #
# --------------------------------------------------------------------------- #
def test_turn_start_continues_after_the_fine():
    # :1100 ends in `return`, which closes gosub4600; :4060's arms deal still runs.
    state = _state(um=1, tip_target=4)
    result, shown, rng = _run(state, 37, 1, 6000)
    p = result.state.players[0]
    assert rng.calls == [("range", 100), ("range", 5), ("hit", 5500, 14999)]
    assert game.tip_target(p) == 0
    assert p.ka == 1000 - 237 + 6000
    keys = [m.key for m in shown]
    assert keys.index("upkeep.rent_late") < keys.index("upkeep.arms_deal_won")


def test_turn_start_continues_after_the_eviction():
    state = _state(um=1, ka=0, tip_target=4)
    result, shown, rng = _run(state, 37, 1, 6000)
    p = result.state.players[0]
    assert rng.calls == [("range", 100), ("range", 5), ("hit", 5500, 14999)]
    assert p.ka == 6000
    assert [g.name for g in p.roster] == ["boss"]
    keys = [m.key for m in shown]
    assert keys.index("upkeep.rent_evicted") < keys.index("upkeep.arms_deal_won")


def test_rent_slot_runs_after_shop_income():
    # :4041 (shop income) precedes :4045 (rent): the income message and draw come first.
    state = _state(um=1, business=Business(shop_tile=1, shop_capital=100))
    result, shown, rng = _run(state, 1, 0, 50)
    assert rng.calls == [("range", 3), ("range", 100), ("range", 100)]
    keys = [m.key for m in shown]
    assert keys.index("upkeep.shop_income_earned") < keys.index("upkeep.rent_late")


# --------------------------------------------------------------------------- #
# messages                                                                     #
# --------------------------------------------------------------------------- #
def test_rent_messages_resolve_to_the_source_text():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    late = resolver.resolve("upkeep.rent_late", {"amount": 237})
    # :4610-4620
    assert "du hast deine miete nicht puenktlich" in late
    assert "gezahlt. man hat dir moebel im wert von" in late
    assert "von 237 $ gepfaendet!" in late
    evicted = resolver.resolve("upkeep.rent_evicted", {})
    # :4650-4651
    assert "deine wohnung wird dir gekuendigt!" in evicted
    assert "deine gangster suchen sich einen anderen" in evicted
    assert "boss..." in evicted


def test_run_upkeep_entry_point_applies_the_rent_slot():
    result = run_upkeep(_state(um=4))
    assert game.rented_months(result.state.players[0]) == 3

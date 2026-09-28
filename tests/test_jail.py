"""The jail skip — ``mf-prg.bas:1013``, ``:1500-1515``, and upkeep's ``:4040`` jail gate.

::

    1011 gosub4000:ifra(sp)=10andx5%(sp)>0andx6%(sp)>0thensyslh,"sieg-pic":goto40000
    1013 gf(sp)=int(gf(sp)*100)/100:ifgs(sp)thengosub1500:goto1010
    1500 gs(sp)=gs(sp)-1:pokera,0:pokera+1,6
    1510 print"{clr}{down}{gry3}du sitzt im knast."gs(sp)+1"monat(e) hast"
    1515 print"{down}du noch vor dir...":goto1100
    4040 ifkr(sp)andgs(sp)=0thengosub4300

A jailed player's turn runs upkeep (``:1011``) and the score truncation (``:1013``),
then shows the jail screen with the months left before the decrement and ends. The
tests drive the engine turn runner headlessly over the loaded config, with a strict
rng: nothing here may draw.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CONFIG_DIR
from data.game_configs.mafia_1920s.handlers.police import ACQUITTED, SENTENCED, Arrest, sentence
from data.game_configs.mafia_1920s.handlers.turn import JAIL_SCREEN
from data.game_configs.mafia_1920s.state import Debt, Job
from engine.config_loader import load_game_config
from engine.interactions import (
    Acknowledge,
    Confirm,
    Heading,
    PromptInt,
    ShowMessage,
    TurnMenu,
    run,
)
from engine.persistence import load_game, save_game
from engine.turns import (
    GAME_OVER,
    JOB_SHIFT_SCREEN,
    MENU,
    NEXT_PLAYER,
    TURN_OVER,
    UPKEEP,
    UPKEEP_SCREEN,
    YEAR_END_SCREEN,
    TurnRunner,
)
from tests.helpers import StubRng, with_clock, with_player, with_values

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks
_TWO = [("alcapone", "the outfit"), ("moran", "north side")]


def _game(**clock):
    state = _CONFIG.module.new_game(seed=42, end_year=1930, score_weight=1.0, players=_TWO)
    return with_clock(state, **clock) if clock else state


def _jailed(state, months: int, idx: int = 0):
    return with_values(state, replace(game.wanted(state.players[idx]), jail_months=months), idx=idx)


def _drive(state, *, entry=None, stop=lambda seen: False):
    """Run the turn runner, every turn menu answered "4" (next player).

    Returns ``(seen, runner, outcome)``: ``seen`` holds ``(player, month, interaction)``
    for every interaction, the player and month as the clock stood when it was asked.
    ``stop(seen)`` ends the drive after the interaction just recorded.
    """
    runner = TurnRunner(
        state,
        StubRng(),
        city=_CONFIG.city,
        shells=_CONFIG.shells,
        turn_menu=_CONFIG.menus["turn"],
    )
    gen = runner.run(entry)
    seen: list = []
    try:
        interaction = next(gen)
        while True:
            clock = runner.state.clock
            seen.append((clock.active_player, clock.month, interaction))
            if stop(seen):
                gen.close()
                return seen, runner, "stopped"
            interaction = gen.send("4" if isinstance(interaction, TurnMenu) else None)
    except StopIteration as done:
        return seen, runner, done.value


def _menu_of(player: int, count: int = 1):
    """Stop at the ``count``-th turn menu of ``player``."""

    def stop(seen) -> bool:
        return len([i for p, _m, i in seen if isinstance(i, TurnMenu) and p == player]) >= count

    return stop


def _jail_screens(seen) -> list[tuple[int, int, int]]:
    """``(player, month, months shown)`` for every jail screen."""
    return [
        (p, m, i.params["months"])
        for p, m, i in seen
        if isinstance(i, Acknowledge) and i.key == JAIL_SCREEN
    ]


def _messages(seen, player: int, month: int) -> list[str]:
    return [i.key for p, m, i in seen if isinstance(i, ShowMessage) and (p, m) == (player, month)]


# --------------------------------------------------------------------------- #
# AE3 — the skipped turns                                                     #
# --------------------------------------------------------------------------- #
def test_ae3_a_three_month_sentence_skips_three_turns_then_the_player_moves():
    seen, runner, _ = _drive(_jailed(_game(), 3), entry=UPKEEP, stop=_menu_of(0))

    # Rounds 1-3 (months 0-2): the jail screen with the months left, and no turn.
    assert _jail_screens(seen) == [(0, 0, 3), (0, 1, 2), (0, 2, 1)]
    assert all(i.player == 0 for _p, _m, i in seen if getattr(i, "key", None) == JAIL_SCREEN)
    # Round 4: the player moves normally; the other player had each round's turn.
    player, month, menu = seen[-1]
    assert (player, month, type(menu)) == (0, 3, TurnMenu)
    assert [m for p, m, i in seen if isinstance(i, TurnMenu) and p == 1] == [0, 1, 2]
    assert game.wanted(runner.state.players[0]).jail_months == 0


def test_a_jailed_players_score_is_still_truncated():
    state = with_player(_jailed(_game(), 2), 0, gf=12.345)
    seen, runner, _ = _drive(state, entry=UPKEEP, stop=_menu_of(1))

    assert _jail_screens(seen) == [(0, 0, 2)]
    assert runner.state.players[0].gf == 12.34


# --------------------------------------------------------------------------- #
# :4040 — the debt check waits out the sentence                               #
# --------------------------------------------------------------------------- #
def test_a_jailed_debtors_countdown_is_frozen_and_resumes_after_release():
    state = with_values(_jailed(_game(), 1), Debt(amount=500, months=2))
    seen, runner, _ = _drive(state, entry=UPKEEP, stop=_menu_of(0))

    assert _jail_screens(seen) == [(0, 0, 1)]
    assert "upkeep.debt_warning" not in _messages(seen, 0, 0)
    # Released: the countdown ticks 2 -> 1 and warns, kz(sp)+1 months left (:4308).
    assert "upkeep.debt_warning" in _messages(seen, 0, 1)
    warning = next(i for p, m, i in seen if getattr(i, "key", None) == "upkeep.debt_warning")
    assert warning.params["months"] == 2
    assert game.debt(runner.state.players[0]) == Debt(amount=500, months=1)


def test_a_jailed_debtor_whose_time_is_up_meets_no_collectors():
    state = with_values(_jailed(_game(), 2), Debt(amount=500, months=0))
    seen, runner, _ = _drive(state, entry=UPKEEP, stop=_menu_of(1, count=2))

    assert _jail_screens(seen) == [(0, 0, 2), (0, 1, 1)]
    assert "upkeep.debt_collectors_intro" not in [getattr(i, "key", None) for _p, _m, i in seen]
    assert game.debt(runner.state.players[0]) == Debt(amount=500, months=0)
    assert runner.state.players[0].ka == state.players[0].ka


# --------------------------------------------------------------------------- #
# A sentence, then the jailed turn's upkeep                                   #
# --------------------------------------------------------------------------- #
def _sentenced_game(state, answers=(), draws=()):
    """Player 0 sentenced (``:26045-26080``) mid-turn, the turn about to end."""
    pending = list(answers)

    def handler(ctx):
        return (yield from sentence(ctx, Arrest()))

    def answer(interaction):
        return pending.pop(0) if isinstance(interaction, (Confirm, PromptInt)) else None

    result = run(handler, answer, state=state, rng=StubRng(*draws))
    return result


def test_a_sentences_score_loss_shows_the_demotion_screen_at_the_jailed_turn():
    # Rank 3 at 30 points; the sentence (+2, then -10) leaves 22: nr 2 (:1165).
    state = with_values(with_player(_game(), 0, rank=3, gf=30.0), nr=3, rented_months=3)
    result = _sentenced_game(state)
    assert result.payload.returned == SENTENCED
    assert game.next_rank(result.state.players[0]) == 2

    seen, runner, _ = _drive(result.state, entry=TURN_OVER, stop=lambda s: bool(_jail_screens(s)))

    promotion = next(i for p, m, i in seen if getattr(i, "key", None) == "upkeep.rank_promotion")
    assert (promotion.player, promotion.params["rank_name"]) == (0, "schlaeger")
    assert _jail_screens(seen) == [(0, 1, 2)]
    jailed = runner.state.players[0]
    assert jailed.rank == 2
    assert game.rented_months(jailed) == 2  # :4046 um(sp)=um(sp)-1


def test_an_acquittal_keeps_the_job_and_the_next_turn_runs_the_shift():
    # Rank 5: the lawyer is offered; a 5000$ fee and a draw of 2500 cut 3 months to 0.
    state = with_player(_game(), 0, rank=5, gf=50.0, ka=10_000)
    state = with_values(state, Job(type=1, pending_pay=1000, months_left=3))
    result = _sentenced_game(state, answers=(True, 5000), draws=(2500,))
    assert result.payload.returned == ACQUITTED

    seen, runner, _ = _drive(
        result.state,
        entry=TURN_OVER,
        stop=lambda s: isinstance(s[-1][2], Heading) and s[-1][2].key == JOB_SHIFT_SCREEN,
    )
    assert seen[-1][:2] == (0, 1)
    assert _jail_screens(seen) == []
    assert game.job(runner.state.players[0]).type == 1


def test_a_jailed_player_with_the_top_score_wins_at_year_end():
    state = _jailed(_game(year=1929, month=11), 3)
    state = with_player(with_player(state, 0, gf=90.0), 1, gf=10.0)
    seen, _runner, outcome = _drive(state, entry=UPKEEP)

    assert outcome == GAME_OVER
    assert _jail_screens(seen) == [(0, 11, 3)]
    year_end = next(i for _p, _m, i in seen if getattr(i, "key", None) == YEAR_END_SCREEN)
    assert ("game_end.winner", {"name": "alcapone"}) in year_end.params["lines"]


# --------------------------------------------------------------------------- #
# Save and resume                                                             #
# --------------------------------------------------------------------------- #
def test_a_save_with_another_player_jailed_resumes_the_countdown(tmp_path: Path):
    state = with_clock(_jailed(_game(), 2, idx=1), turn_phase=MENU)
    save = tmp_path / "jailed.jsonl"
    save_game(save, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=42)
    loaded = load_game(save, _CONFIG.registries).state
    assert game.wanted(loaded.players[1]).jail_months == 2

    seen, _runner, _ = _drive(loaded, stop=_menu_of(1))

    assert _jail_screens(seen) == [(1, 0, 2), (1, 1, 1)]
    # The resume re-entered the saved menu: no upkeep ran for player 0 first.
    first_upkeep = next(i for _p, _m, i in seen if isinstance(i, Heading))
    assert (first_upkeep.key, first_upkeep.player) == (UPKEEP_SCREEN, 1)


# --------------------------------------------------------------------------- #
# The client                                                                  #
# --------------------------------------------------------------------------- #
def test_the_client_shows_the_jail_screen(monkeypatch, tmp_path: Path):
    from tests.test_client_loop import _resume_at

    state = _jailed(_game(), 2, idx=1)
    # Player 0's turn is over: player 1's upkeep, the jail screen, the turn-over, then
    # the round standings, player 0's upkeep and turn menu, where "q" quits.
    output, (end, _rng) = _resume_at(
        monkeypatch, tmp_path, state, NEXT_PLAYER, ["x", "x", "x", "x", "x", "q"]
    )

    assert "du sitzt im knast. 2 monat(e) hast\ndu noch vor dir..." in output
    assert game.wanted(end.players[1]).jail_months == 1

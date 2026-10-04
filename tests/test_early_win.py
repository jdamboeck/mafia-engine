"""The early win — ``mf-prg.bas:1011`` into the ending ``:40000-40166``.

::

    1010 sp=sp+1:ifsp=sz+1thensp=1:gosub4500:ja=ja+1/12:ifint(ja)=x9goto40100
    1011 gosub4000:ifra(sp)=10andx5%(sp)>0andx6%(sp)>0thensyslh,"sieg-pic":goto40000
    1012 ms=tr(tm(sp)):nr(sp)=ra(sp):ll(sp)=0:ifjo(sp)thengosub25000:goto1010
    1013 gf(sp)=int(gf(sp)*100)/100:ifgs(sp)thengosub1500:goto1010
    40000 rem ende
    40100 gosub4500:pokera,1:pokera+1,1:g=1:g(g)=1
    40105 fori=2tosz:ifgf(i)>gf(g(g))theng=1:g(g)=i:goto40110
    40106 ifgf(i)=gf(g(g))theng=g+1:g(g)=i

At a turn start, after upkeep, a rank-10 player holding both win flags shows the
victory picture and the game ends. ``:40000`` is a ``rem``: the early win falls into
the same ranking as the year end (``:40100``), so the winner is whoever has the top
score, and the triggering player can lose. The tests drive the engine turn runner
headlessly over the loaded config with a strict rng (nothing here may draw), and one
drives the terminal client's ``play()``.
"""

from __future__ import annotations

import io
import sys
from dataclasses import replace

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CLEAR, CONFIG_DIR, play
from data.game_configs.mafia_1920s.handlers.turn import JAIL_SCREEN, VICTORY_SCREEN
from data.game_configs.mafia_1920s.state import Job
from engine.config_loader import load_game_config
from engine.interactions import Acknowledge, Heading, ShowMessage, TurnMenu
from engine.persistence import save_game
from engine.turns import (
    GAME_OVER,
    JOB_SHIFT_SCREEN,
    MENU,
    NEXT_PLAYER,
    STANDINGS_SCREEN,
    TURN_OVER_SCREEN,
    UPKEEP,
    UPKEEP_SCREEN,
    YEAR_END_SCREEN,
    TurnRunner,
)
from tests.helpers import StubRng, deadline, with_clock, with_player, with_values

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks
_TWO = [("alcapone", "the outfit"), ("moran", "north side")]


def _game(**clock):
    state = _CONFIG.module.new_game(seed=42, end_year=1930, score_weight=1.0, players=_TWO)
    return with_clock(state, **clock) if clock else state


def _eligible(state, idx: int = 0, *, rank: int = 10, x5: bool = True, x6: bool = True, gf=100.0):
    """``players[idx]`` at ``rank`` (``nr`` the same, so upkeep's :4030 commits nothing)
    with the win flags ``x5``/``x6`` as given."""
    state = with_player(state, idx, rank=rank, gf=gf)
    flags = replace(game.wanted(state.players[idx]), x5=x5, x6=x6)
    return with_values(state, flags, idx=idx, nr=rank)


def _drive(state, *, entry=UPKEEP, turn_menus: int = 3):
    """Run the turn runner, every turn menu answered "4" (next player).

    Returns ``(seen, runner, outcome)``; ``seen`` holds ``(player, interaction)``. The
    drive stops (outcome ``"stopped"``) at the ``turn_menus``-th turn menu, so a game
    that does not end cannot run on.
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
    menus = 0
    try:
        interaction = next(gen)
        while True:
            seen.append((runner.state.clock.active_player, interaction))
            if isinstance(interaction, TurnMenu):
                menus += 1
                if menus >= turn_menus:
                    gen.close()
                    return seen, runner, "stopped"
            interaction = gen.send("4" if isinstance(interaction, TurnMenu) else None)
    except StopIteration as done:
        return seen, runner, done.value


def _screens(seen) -> list[str]:
    """The keys of every Acknowledge / Heading screen, in order."""
    return [i.key for _p, i in seen if isinstance(i, (Acknowledge, Heading))]


def _result_lines(seen) -> list[tuple[str, dict]]:
    (lines,) = [
        i.params["lines"]
        for _p, i in seen
        if isinstance(i, Acknowledge) and i.key == YEAR_END_SCREEN
    ]
    return lines


def _keys(lines) -> list[str]:
    return [key for key, _params in lines]


# --------------------------------------------------------------------------- #
# AE4 — the early win ends the game at the turn start                          #
# --------------------------------------------------------------------------- #
def test_ae4_a_rank_10_player_with_both_flags_ends_the_game_at_their_turn_start():
    state = _game(month=4)
    seen, runner, outcome = _drive(_eligible(state))

    assert outcome == GAME_OVER
    # Upkeep first (:1011 gosub4000), then the victory picture, then the ranking.
    assert _screens(seen) == [UPKEEP_SCREEN, UPKEEP_SCREEN, VICTORY_SCREEN, YEAR_END_SCREEN]
    assert not [i for _p, i in seen if isinstance(i, TurnMenu)], "the turn menu opened"
    victory = next(i for _p, i in seen if getattr(i, "key", None) == VICTORY_SCREEN)
    assert victory.player == 0
    # :40100 gosub4500: the standings at the date as it stands (no month advanced), then
    # the sole winner (:40115).
    lines = _result_lines(seen)
    assert lines[0] == ("game_end.standings_header", {"year": 1925, "month": 5})
    assert _keys(lines)[-1] == "game_end.winner"
    assert lines[-1][1] == {"name": "alcapone"}
    assert (runner.state.clock.year, runner.state.clock.month) == (1925, 4)


def test_a_rival_with_a_higher_score_is_named_winner():
    state = with_player(_eligible(_game()), 1, gf=150.0)
    seen, _runner, outcome = _drive(state)

    assert outcome == GAME_OVER
    lines = _result_lines(seen)
    assert lines[-1] == ("game_end.winner", {"name": "moran"})


def test_a_score_tie_lists_all_tied_players():
    state = with_player(_eligible(_game()), 1, gf=100.0)
    seen, _runner, outcome = _drive(state)

    assert outcome == GAME_OVER
    lines = _result_lines(seen)
    tail = lines[-4:]
    assert tail == [
        ("game_end.tie_header", {}),
        ("game_end.tie_name", {"name": "alcapone"}),
        ("game_end.tie_name", {"name": "moran"}),
        ("game_end.tie_footer", {}),
    ]


# --------------------------------------------------------------------------- #
# Not eligible: the turn goes on                                               #
# --------------------------------------------------------------------------- #
def test_rank_9_with_both_flags_does_not_end_the_game():
    seen, _runner, outcome = _drive(_eligible(_game(), rank=9, gf=90.0))

    assert outcome == "stopped"
    assert VICTORY_SCREEN not in _screens(seen)
    assert YEAR_END_SCREEN not in _screens(seen)


def test_rank_10_with_one_flag_does_not_end_the_game():
    for flags in ({"x5": True, "x6": False}, {"x5": False, "x6": True}):
        seen, _runner, outcome = _drive(_eligible(_game(), **flags))

        assert outcome == "stopped", flags
        assert VICTORY_SCREEN not in _screens(seen), flags
        assert YEAR_END_SCREEN not in _screens(seen), flags


# --------------------------------------------------------------------------- #
# The order: before the job shift and the jail skip, after the year-end check #
# --------------------------------------------------------------------------- #
def test_an_eligible_jailed_player_wins_before_the_jail_screen():
    state = _eligible(_game())
    state = with_values(state, replace(game.wanted(state.players[0]), jail_months=3))
    seen, runner, outcome = _drive(state)

    assert outcome == GAME_OVER
    assert JAIL_SCREEN not in _screens(seen)
    assert _screens(seen)[-2:] == [VICTORY_SCREEN, YEAR_END_SCREEN]
    assert game.wanted(runner.state.players[0]).jail_months == 3, "the sentence counted down"


def test_an_eligible_employed_player_wins_before_the_job_shift():
    state = with_values(_eligible(_game()), Job(type=1, pending_pay=1000, months_left=3))
    seen, runner, outcome = _drive(state)

    assert outcome == GAME_OVER
    assert JOB_SHIFT_SCREEN not in _screens(seen)
    assert _screens(seen)[-2:] == [VICTORY_SCREEN, YEAR_END_SCREEN]
    assert game.job(runner.state.players[0]).months_left == 3, "the shift ran"


def test_at_the_wrap_to_the_end_year_the_year_end_wins_over_an_eligible_player_1():
    # :1010 `ifint(ja)=x9goto40100` runs before :1011: the early win is never checked.
    state = _eligible(_game(year=1929, month=11, active_player=1))
    seen, runner, outcome = _drive(state, entry=NEXT_PLAYER)

    assert outcome == GAME_OVER
    assert _screens(seen) == [STANDINGS_SCREEN, YEAR_END_SCREEN]
    assert "upkeep.turn_banner" not in [i.key for _p, i in seen if isinstance(i, ShowMessage)]
    assert (runner.state.clock.year, runner.state.clock.month) == (1930, 0)


def test_flags_won_mid_turn_count_only_at_the_next_turn_start():
    # Both flags already held while the turn menu is open (won on this turn's map):
    # the turn plays on; the game ends only at the player's next turn start.
    seen, _runner, outcome = _drive(_eligible(_game()), entry=MENU)

    assert outcome == GAME_OVER
    screens = _screens(seen)
    assert screens.index(VICTORY_SCREEN) > screens.index(TURN_OVER_SCREEN)
    assert [p for p, i in seen if isinstance(i, TurnMenu)] == [0, 1]
    victory = next(i for _p, i in seen if getattr(i, "key", None) == VICTORY_SCREEN)
    assert victory.player == 0


# --------------------------------------------------------------------------- #
# The terminal client                                                          #
# --------------------------------------------------------------------------- #
def test_play_shows_the_victory_screen_then_the_ranking_and_ends(monkeypatch, tmp_path):
    state = with_player(_eligible(_game()), 1, gf=150.0)
    state = replace(state, clock=replace(state.clock, turn_phase=UPKEEP))
    save = tmp_path / "early_win.jsonl"
    save_game(save, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=42)
    out = io.StringIO()
    # Keys: the upkeep ack, the victory ack, the result ack -- then nothing is read.
    monkeypatch.setattr(sys, "stdin", io.StringIO("x\nx\nx\n"))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(30, "play() did not return (spin?)"):
        play(load=str(save))
    output = out.getvalue()

    victory_at = output.index("chef der unterwelt")
    result_at = output.index("moran hat gewonnen!")
    assert victory_at < result_at
    assert output.index(CLEAR, victory_at) < result_at, "the ranking is not its own screen"
    assert "spielstand 1925-1" in output[victory_at:]
    assert "was willst du tun" not in output, "the turn menu opened"

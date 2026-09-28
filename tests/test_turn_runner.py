"""The engine turn runner (``engine/turns.py``): the order of a turn, ``mf-prg.bas:1010-1013``.

The runner owns the turn order the terminal client used to hold: next player, the round
standings and year-end check on a wrap, upkeep, the config's turn-start hooks, the free
turn, the turn-over. These tests drive it headlessly, answering what it yields, and
hold it to the order the client loop had before it (``test_a_two_player_round...``
replays that loop with the engine's step-by-step helpers).
"""

from __future__ import annotations

import io
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CLEAR, CONFIG_DIR, play
from data.game_configs.mafia_1920s.handlers.turn import truncated_score
from data.game_configs.mafia_1920s.state import Job
from engine.config_loader import load_game_config
from engine.effects import SetScore
from engine.interactions import (
    Acknowledge,
    CombatScreen,
    Heading,
    PromptInt,
    ShowMessage,
    run,
)
from engine.locations import HANDLERS
from engine.movement import (
    DOWN,
    LEFT,
    RIGHT,
    UP,
    advance_turn,
    load_city,
    start_free_turn,
    try_move,
)
from engine.persistence import load_game
from engine.rng import Rng
from engine.turns import (
    GAME_OVER,
    NEXT_PLAYER,
    PAUSED,
    SCORE_TRUNCATION_HOOK_KEY,
    STANDINGS_SCREEN,
    TURN_OVER_SCREEN,
    TURN_START,
    UPKEEP,
    UPKEEP_SCREEN,
    WALKING,
    YEAR_END_SCREEN,
    FreeTurn,
    TurnRunner,
)
from engine.upkeep import run_upkeep
from tests.helpers import deadline, make_walk_script

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks
_VEHICLES = _CONFIG.module.load_vehicles(CONFIG_DIR / _CONFIG.config["entities"]["vehicles"])
_TWO = [("alcapone", "the outfit"), ("moran", "north side")]


def _city():
    import yaml

    raw = yaml.safe_load((CONFIG_DIR / "content" / "map" / "city.yaml").read_text("utf-8"))
    return load_city(raw)


_CITY = _city()


def _new_game(players=None, **kw):
    return _CONFIG.module.new_game(
        seed=kw.pop("seed", 42),
        end_year=kw.pop("end_year", 1930),
        score_weight=1.0,
        players=players or [("alcapone", "the outfit")],
    )


def _walk(state):
    """Walk the active player's free turn to its end: the first stepping move, repeated."""
    for _ in range(200):
        for delta in (UP, LEFT, DOWN, RIGHT):
            result = try_move(state, _CITY, delta)
            if result.payload.kind == "step":
                break
        else:
            raise AssertionError("no stepping move")
        state = result.state
        if result.payload.turn_over:
            return state
    raise AssertionError("the free turn never ended")


def _answer(interaction):
    """One client for both loops: narration seen, trick 1, a fight surrendered."""
    if isinstance(interaction, PromptInt):
        return "1"
    if isinstance(interaction, CombatScreen):
        return ("surrender", None)
    return None


def _drive(runner, gen, *, stop_at_free_turn: int | None = None):
    """Drive ``gen`` answering like a client; return ``(interactions seen, outcome)``.

    A :class:`FreeTurn` is played with :func:`_walk`; the ``stop_at_free_turn``-th one
    (1-based) stops the drive instead, with outcome ``"stopped"``.
    """
    seen: list = []
    free_turns = 0
    try:
        interaction = next(gen)
        while True:
            seen.append(interaction)
            if isinstance(interaction, FreeTurn):
                free_turns += 1
                if free_turns == stop_at_free_turn:
                    gen.close()
                    return seen, "stopped"
                response = _walk(runner.state)
            elif isinstance(interaction, (Acknowledge, Heading)):
                response = None
            else:
                response = _answer(interaction)
            interaction = gen.send(response)
    except StopIteration as stop:
        return seen, stop.value


def _keys(seen, cls: type[ShowMessage] | type[Acknowledge] = ShowMessage) -> list[str]:
    return [i.key for i in seen if isinstance(i, cls)]


# --------------------------------------------------------------------------- #
# The runner keeps the client loop's order                                     #
# --------------------------------------------------------------------------- #
def _client_loop_reference(state, rng):
    """The turn order the terminal client held before the runner, step by step.

    Replays ``clients/terminal/session.py``'s loop as it stood before the engine owned
    turn order: the first upkeep; then per turn a job shift (employed) or ``:1013``
    truncation and the map; ``advance_turn``; the standings on a wrap (display only);
    upkeep. Stops where player 0's second free turn opens.
    """
    state = run_upkeep(state, input_source=_answer, rng=rng).state
    free_turns = 0
    while True:
        active = state.players[state.clock.active_player]
        if game.job(active).type:
            state = run(HANDLERS["job.shift"], _answer, state=state, rng=rng).state
        else:
            state = start_free_turn(state)
            free_turns += 1
            if free_turns == 2:
                return state
            state = _walk(state)
        state, over = advance_turn(state, _VEHICLES)
        assert not over
        state = run_upkeep(state, input_source=_answer, rng=rng).state


def _employed_second_player():
    state = _new_game(_TWO, seed=1)
    moran = state.players[1]
    values = {**moran.values, **game.values_of(Job(type=2, pending_pay=1200, months_left=2))}
    players = (state.players[0], replace(moran, values=values, gf=25.199999))
    return replace(state, players=players)


def test_a_two_player_round_matches_the_client_loop_it_replaced():
    """Player 0 walks, player 1 works a croupier shift (it draws), the round wraps and
    player 0's next turn opens: the same state and the same RNG draws as the old loop."""
    expected_rng = Rng(1)
    expected = _client_loop_reference(_employed_second_player(), expected_rng)

    rng = Rng(1)
    runner = TurnRunner(_employed_second_player(), rng)
    seen, outcome = _drive(runner, runner.run(UPKEEP), stop_at_free_turn=2)

    assert outcome == "stopped"
    assert "job.shift_croupier_intro" in _keys(seen), "the job shift never ran"
    assert rng.log and rng.log == expected_rng.log
    assert runner.state == expected
    assert runner.state.clock.turn_phase == WALKING


# --------------------------------------------------------------------------- #
# :1010 — the wrap                                                             #
# --------------------------------------------------------------------------- #
def _last_player_on_turn(**clock):
    state = _new_game(_TWO)
    return replace(state, clock=replace(state.clock, active_player=1, **clock))


def test_wrapping_the_last_player_shows_the_standings_before_player_ones_upkeep():
    runner = TurnRunner(_last_player_on_turn(month=4), Rng(42))
    seen, outcome = _drive(runner, runner.run(NEXT_PLAYER, until=TURN_START))

    assert outcome == PAUSED
    order = [
        i.key
        for i in seen
        if isinstance(i, Acknowledge) or (isinstance(i, ShowMessage) and i.key.startswith("upkeep"))
    ]
    assert order == [STANDINGS_SCREEN, "upkeep.turn_banner", UPKEEP_SCREEN]
    # :1010 gosub4500 runs before ja=ja+1/12: the standings date is the round played.
    standings = next(i for i in seen if isinstance(i, Acknowledge) and i.key == STANDINGS_SCREEN)
    assert standings.params["lines"][0] == (
        "game_end.standings_header",
        {"year": 1925, "month": 5},
    )
    assert runner.state.clock.active_player == 0
    assert runner.state.clock.month == 5
    assert runner.state.clock.turn_phase == UPKEEP


def test_a_rotation_without_a_wrap_shows_no_standings():
    state = _new_game(_TWO)
    runner = TurnRunner(state, Rng(42))
    seen, outcome = _drive(runner, runner.run(NEXT_PLAYER, until=TURN_START))

    assert outcome == PAUSED
    assert STANDINGS_SCREEN not in _keys(seen, Acknowledge)
    assert runner.state.clock.active_player == 1
    assert runner.state.clock.month == 0


# :1010 `ifint(ja)=x9goto40100` -- after the wrap has added its twelfth.
def test_the_year_end_check_fires_when_the_wrap_reaches_the_end_year():
    runner = TurnRunner(_last_player_on_turn(year=1927, month=11, end_year=1928), Rng(42))
    seen, outcome = _drive(runner, runner.run(NEXT_PLAYER, until=TURN_START))

    assert outcome == GAME_OVER
    assert _keys(seen, Acknowledge) == [STANDINGS_SCREEN, YEAR_END_SCREEN]
    assert "upkeep.turn_banner" not in _keys(seen), "upkeep ran after the game ended"
    assert (runner.state.clock.year, runner.state.clock.month) == (1928, 0)


def test_the_year_end_check_does_not_fire_a_month_early():
    runner = TurnRunner(_last_player_on_turn(year=1927, month=10, end_year=1928), Rng(42))
    seen, outcome = _drive(runner, runner.run(NEXT_PLAYER, until=TURN_START))

    assert outcome == PAUSED
    assert YEAR_END_SCREEN not in _keys(seen, Acknowledge)
    assert (runner.state.clock.year, runner.state.clock.month) == (1927, 11)


# --------------------------------------------------------------------------- #
# The turn start                                                               #
# --------------------------------------------------------------------------- #
def test_the_turn_start_refills_movement_truncates_and_opens_the_free_turn():
    state = _new_game()
    active = replace(state.players[0], ms=0, gf=25.199999)
    runner = TurnRunner(replace(state, players=(active,)), Rng(42))
    seen, outcome = _drive(runner, runner.run(TURN_START), stop_at_free_turn=1)

    assert outcome == "stopped"
    assert isinstance(seen[-1], FreeTurn)
    assert runner.state.players[0].ms == _VEHICLES[active.vehicle]["tr"]
    assert runner.state.players[0].gf == 25.19
    assert runner.state.clock.turn_phase == WALKING


def test_the_turn_over_follows_the_free_turn():
    runner = TurnRunner(_new_game(_TWO), Rng(42))
    seen, _ = _drive(runner, runner.run(WALKING, until=NEXT_PLAYER))

    assert [type(i) for i in seen] == [FreeTurn, Acknowledge]
    assert seen[1].key == TURN_OVER_SCREEN and seen[1].player == 0


def test_the_config_truncation_hook_and_the_engine_helper_agree():
    """``engine.movement.start_free_turn`` stays for callers that step by hand; the
    runner uses the config's :1013 hook. Both must cut every score alike."""
    state = _new_game()
    for gf in (0, 0.0078125, 0.125, 0.29, 11.1, 25.199999, 25.2, 99.9990234375, -0.125, -12.345):
        engine_gf = (
            start_free_turn(replace(state, players=(replace(state.players[0], gf=gf),)))
            .players[0]
            .gf
        )
        assert truncated_score(gf) == engine_gf, gf


# --------------------------------------------------------------------------- #
# The error guard                                                              #
# --------------------------------------------------------------------------- #
def test_a_hook_that_raises_commits_nothing_and_leaves_the_phase():
    def broken_truncation(ctx):
        ctx.apply(SetScore(99.0))
        yield ShowMessage("turn.broken")
        raise RuntimeError("a bug in the hook")

    handlers = {**HANDLERS, SCORE_TRUNCATION_HOOK_KEY: broken_truncation}
    state = _new_game()
    state = replace(state, clock=replace(state.clock, turn_phase=UPKEEP))
    runner = TurnRunner(state, Rng(42), handlers=handlers)

    with pytest.raises(RuntimeError, match="a bug in the hook"):
        _drive(runner, runner.run(TURN_START))

    assert runner.state.players[0].gf == 0, "the raising hook's buffered effect committed"
    assert runner.state.clock.turn_phase == UPKEEP, "the phase moved past the failed hook"


# --------------------------------------------------------------------------- #
# The recorded phase                                                           #
# --------------------------------------------------------------------------- #
def test_a_state_on_the_map_resumes_on_the_map_without_the_turn_start():
    state = replace(_new_game(), clock=replace(_new_game().clock, turn_phase=WALKING))
    runner = TurnRunner(state, Rng(42))
    gen = runner.run()

    assert isinstance(next(gen), FreeTurn)
    gen.close()
    assert runner.state == state, "the resumed turn re-ran something before the map"


def test_a_state_at_the_turn_start_resumes_with_upkeep():
    state = replace(_new_game(), clock=replace(_new_game().clock, turn_phase=UPKEEP))
    runner = TurnRunner(state, Rng(42))
    gen = runner.run()

    first = next(gen)
    gen.close()
    assert isinstance(first, ShowMessage) and first.key == "upkeep.turn_banner"


# --------------------------------------------------------------------------- #
# Save at the map, resume: through play()                                      #
# --------------------------------------------------------------------------- #
def _play(monkeypatch, stdin, **kw) -> str:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(30, "play() did not return"):
        play(**kw)
    return out.getvalue()


def _map_without_note(screen: str, note: str) -> str:
    """A map screen cut before its note line (the save's outcome, or the hint)."""
    lines = screen.split("\n")
    return "\n".join(lines[: next(i for i, line in enumerate(lines) if note in line)])


def test_a_save_on_the_map_resumes_to_the_same_screen_without_upkeep(monkeypatch, tmp_path: Path):
    save = tmp_path / "game.jsonl"
    # Title, upkeep, two steps, save, quit.
    first = _play(
        monkeypatch,
        make_walk_script(["w", "w", "p", "q"]),
        seed=42,
        end_year=1930,
        score_weight=1.0,
        save=str(save),
    )
    saved_map = first.split(CLEAR)[-1]
    assert str(save) in saved_map, "the save did not happen on the map"
    assert load_game(save, _CONFIG.registries).state.clock.turn_phase == WALKING

    resumed = _play(monkeypatch, io.StringIO("q\n"), load=str(save))

    screens = resumed.split(CLEAR)[1:]
    assert len(screens) == 1, "the resumed game showed more than the map"
    assert "ist an der reihe" not in resumed, "upkeep ran again on resume"
    assert _map_without_note(screens[0], "move: W/A/S/D") == _map_without_note(saved_map, str(save))

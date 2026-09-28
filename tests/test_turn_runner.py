"""The engine turn runner (``engine/turns.py``): the order of a turn, ``mf-prg.bas:1010-1013``.

The runner owns the turn order the terminal client used to hold: next player, the round
standings and year-end check on a wrap, upkeep, the config's turn-start hooks, the free
turn and its map steps (``:2000-2065``), the turn-over. These tests drive it
headlessly, answering what it yields, and hold it to the order the client loop had
before it (``test_a_two_player_round...`` replays that loop with the engine's
step-by-step helpers).
"""

from __future__ import annotations

import io
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CLEAR, CONFIG_DIR, play
from data.game_configs.mafia_1920s.handlers.turn import roadblock_would_fire, truncated_score
from data.game_configs.mafia_1920s.state import Job
from engine.config_loader import load_game_config
from engine.effects import MsChange, SetMovementPoints, SetScore
from engine.interactions import (
    MAP_QUIT,
    MAP_SAVE,
    Acknowledge,
    CombatScreen,
    Heading,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptInt,
    ShowMessage,
    run,
)
from engine.locations import HANDLERS, Location, Option
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
from engine.persistence import load_game, save_game
from engine.rng import Rng
from engine.turns import (
    GAME_OVER,
    LOCATION_CLOSED_SCREEN,
    NEXT_PLAYER,
    PAUSED,
    QUIT,
    ROADBLOCK_HOOK_KEY,
    SCORE_TRUNCATION_HOOK_KEY,
    SPECIAL_CELL_HOOK_KEY,
    STANDINGS_SCREEN,
    TURN_OVER_SCREEN,
    TURN_START,
    UPKEEP,
    UPKEEP_SCREEN,
    WALKING,
    YEAR_END_SCREEN,
    TurnRunner,
)
from engine.upkeep import run_upkeep
from tests.helpers import deadline, make_walk_script

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks
_VEHICLES = _CONFIG.module.load_vehicles(CONFIG_DIR / _CONFIG.config["entities"]["vehicles"])
_TWO = [("alcapone", "the outfit"), ("moran", "north side")]


def _city_raw():
    import yaml

    return yaml.safe_load((CONFIG_DIR / "content" / "map" / "city.yaml").read_text("utf-8"))


_CITY = load_city(_city_raw())


def _runner(state, rng, **kw):
    """A runner over the loaded config's map and shells (``kw`` overrides either)."""
    kw.setdefault("city", _CONFIG.city)
    kw.setdefault("shells", _CONFIG.shells)
    return TurnRunner(state, rng, **kw)


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


#: The direction answers in the order :func:`_walk` tries their deltas.
_WALK_ORDER = (("up", UP), ("left", LEFT), ("down", DOWN), ("right", RIGHT))


def _first_step(state) -> str:
    """The direction :func:`_walk` would take from ``state``: the first that steps."""
    for name, delta in _WALK_ORDER:
        if try_move(state, _CITY, delta).payload.kind == "step":
            return name
    raise AssertionError("no stepping move")


def _answer(interaction):
    """One client for both loops: narration seen, trick 1, a fight surrendered."""
    if isinstance(interaction, PromptInt):
        return "1"
    if isinstance(interaction, CombatScreen):
        return ("surrender", None)
    return None


def _drive(runner, gen, *, stop_at_free_turn: int | None = None):
    """Drive ``gen`` answering like a client; return ``(interactions seen, outcome)``.

    A map-move prompt is answered with the step :func:`_walk` would take. The first
    prompt of the ``stop_at_free_turn``-th free turn (1-based) stops the drive instead,
    with outcome ``"stopped"``.
    """
    seen: list = []
    free_turns = 0
    try:
        interaction = next(gen)
        while True:
            seen.append(interaction)
            if isinstance(interaction, MapMove):
                if interaction.outcome is None:  # a free turn opens
                    free_turns += 1
                    if free_turns == stop_at_free_turn:
                        gen.close()
                        return seen, "stopped"
                response = _first_step(runner.state)
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
    runner = _runner(_employed_second_player(), rng)
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
    runner = _runner(replace(state, players=(active,)), Rng(42))
    seen, outcome = _drive(runner, runner.run(TURN_START), stop_at_free_turn=1)

    assert outcome == "stopped"
    assert isinstance(seen[-1], MapMove) and seen[-1].player == 0
    assert runner.state.players[0].ms == _VEHICLES[active.vehicle]["tr"]
    assert runner.state.players[0].gf == 25.19
    assert runner.state.clock.turn_phase == WALKING


def test_the_turn_over_follows_the_free_turn():
    state = _new_game(_TWO)
    runner = _runner(state, Rng(42))
    seen, _ = _drive(runner, runner.run(WALKING, until=NEXT_PLAYER))

    assert [type(i) for i in seen] == [MapMove] * state.players[0].ms + [Acknowledge]
    assert seen[-1].key == TURN_OVER_SCREEN and seen[-1].player == 0


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
    runner = _runner(state, Rng(42))
    gen = runner.run()

    assert next(gen) == MapMove(player=0)
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


# --------------------------------------------------------------------------- #
# The map step, :2000-2065                                                     #
# --------------------------------------------------------------------------- #
_DELTA_NAMES = {UP: "up", DOWN: "down", LEFT: "left", RIGHT: "right"}


def _approach(cell: int) -> tuple[int, str]:
    """A street cell next to ``cell`` and the direction that moves from it onto ``cell``."""
    for delta, name in _DELTA_NAMES.items():
        start = cell - delta
        if 0 <= start < 1000 and _CITY.code(start) == 156:
            return start, name
    raise AssertionError(f"no street next to {cell}")


def _door(location: str, ln: int = 1) -> tuple[int, int]:
    """``(cell, la)`` of ``location``'s door on tile ``ln``."""
    door = next(d for d in _city_raw()["doors"] if d.get("location") == location and d["ln"] == ln)
    return door["cell"], door["la"]


def _walking(*, po: int, ms: int, rank: int = 1):
    """A one-player game on the map at ``po`` with ``ms`` movement points left."""
    state = _new_game()
    active = replace(state.players[0], po=po, ms=ms, rank=rank)
    return replace(state, players=(active,), clock=replace(state.clock, turn_phase=WALKING))


def _script(gen, answers):
    """Drive ``gen`` with ``answers`` for its prompts (display-only ones get ``None``).

    Returns ``(interactions seen, outcome)``; stops at the first turn-over screen with
    outcome ``"turn_over"``, or when ``answers`` run out with outcome ``"open"``.
    """
    answers = list(answers)
    seen: list = []
    try:
        interaction = next(gen)
        while True:
            seen.append(interaction)
            if isinstance(interaction, Acknowledge) and interaction.key == TURN_OVER_SCREEN:
                gen.close()
                return seen, "turn_over"
            if isinstance(interaction, (MapMove, LocationMenu)):
                if not answers:
                    gen.close()
                    return seen, "open"
                response = answers.pop(0)
            else:
                response = _answer(interaction)
            interaction = gen.send(response)
    except StopIteration as stop:
        return seen, stop.value


def _types(seen) -> list[type]:
    return [type(i) for i in seen]


def test_walking_until_the_movement_points_run_out_ends_the_turn_with_no_menu():
    state = _walking(po=_approach(_door("pub")[0])[0], ms=3)
    runner = _runner(state, Rng(42))
    seen, outcome = _drive(runner, runner.run(until=NEXT_PLAYER))

    # :2042 goto2005 -> ms<=0 return: three steps, then straight to the turn-over.
    assert outcome == PAUSED
    assert _types(seen) == [MapMove] * 3 + [Acknowledge]
    assert [m.outcome for m in seen[:3]] == [None, "step", "step"]
    assert seen[-1].key == TURN_OVER_SCREEN
    assert runner.state.players[0].ms == 0


def test_a_location_handler_that_gives_points_back_continues_the_map():
    door, la = _door("pub")
    start, into = _approach(door)
    given: list = []

    def refuel(ctx):
        ctx.apply(SetMovementPoints(10))
        given.append(True)
        yield from ()

    def sit(ctx):
        yield from ()

    shells = {
        "pub": Location("pub", [Option("refuel", handler=refuel), Option("sit", handler=sit)])
    }

    # The door costs the last 5 points; the handler gives 10 back: the map goes on.
    runner = _runner(_walking(po=start, ms=5), Rng(42), shells=shells)
    seen, outcome = _script(runner.run(), [into, 0])
    assert given, "the handler never ran"
    assert outcome == "open"
    assert _types(seen) == [MapMove, LocationMenu, OptionDone, MapMove]
    assert seen[-1].outcome == "enter"
    assert runner.state.players[0].ms == 10

    # The same visit giving nothing back ends the turn after it, with no map prompt.
    runner = _runner(_walking(po=start, ms=5), Rng(42), shells=shells)
    seen, outcome = _script(runner.run(), [into, 1])
    assert outcome == "turn_over"
    assert _types(seen) == [MapMove, LocationMenu, OptionDone, Acknowledge]


def test_a_config_hook_that_zeroes_the_movement_points_ends_the_turn():
    def stopped(ctx):
        active = ctx.state.players[ctx.state.clock.active_player]
        ctx.apply(MsChange(-active.ms))
        yield from ()

    state = _walking(po=_approach(_door("pub")[0])[0], ms=10)
    runner = _runner(state, Rng(42), handlers={**HANDLERS, ROADBLOCK_HOOK_KEY: stopped})
    seen, outcome = _script(runner.run(), [_first_step(state), "up"])

    assert outcome == "turn_over"
    assert _types(seen) == [MapMove, Acknowledge]
    assert runner.state.players[0].ms == 0


def test_the_map_prompt_offers_save_and_quit():
    state = _walking(po=18, ms=10)
    runner = _runner(state, Rng(42))
    gen = runner.run()

    prompt = next(gen)
    assert isinstance(prompt, MapMove)
    assert MAP_SAVE in prompt.commands and MAP_QUIT in prompt.commands
    # The driver saves the committed state; the runner asks again, nothing moved.
    assert gen.send(MAP_SAVE) == MapMove(player=0)
    assert gen.send("north-by-northwest") == MapMove(player=0)
    assert runner.state == state
    with pytest.raises(StopIteration) as stop:
        gen.send(MAP_QUIT)
    assert stop.value.value == QUIT
    assert runner.state.clock.turn_phase == WALKING


def test_a_door_to_a_location_without_a_shell_shows_it_closed():
    door, la = _door("sgl")
    start, into = _approach(door)
    runner = _runner(_walking(po=start, ms=20), Rng(42))
    seen, _ = _script(runner.run(), [into])

    assert seen[1] == Heading(LOCATION_CLOSED_SCREEN, {"location": "sgl"})
    assert isinstance(seen[2], MapMove) and seen[2].outcome == "enter"


def test_the_location_menu_lists_only_the_options_whose_guard_passes():
    door, _ = _door("pub")
    start, into = _approach(door)
    runner = _runner(_walking(po=start, ms=20), Rng(42))
    seen, _ = _script(runner.run(), [into])

    # pub.recruit is guarded rank > 4; a rank-1 player is not offered it.
    assert seen[1] == LocationMenu(location="pub", options=("drink", "tip", "job"), ln=1, player=0)


def test_the_default_hooks_change_nothing_and_draw_nothing():
    # 569 (la=13) is a street on the map; its unarmed hook lets the player step on.
    start, into = _approach(569)
    # A rank-5 player stepping to ms=20 meets every :2041 condition but the roll: the
    # unbuilt roadblock must not draw it.
    rng = Rng(42)
    runner = _runner(_walking(po=start, ms=21, rank=5), rng)
    seen, _ = _script(runner.run(), [into])

    assert runner.state.players[0].po == 569
    assert runner.state.players[0].ms == 20
    assert seen[-1] == MapMove(outcome="step", player=0)
    assert rng.log == []


def test_the_special_cell_hook_is_asked_before_a_move_onto_an_event_cell():
    asked: list = []

    def armed(ctx, *, cell, la):
        asked.append((cell, la))
        yield from ()
        return True

    start, into = _approach(861)
    runner = _runner(
        _walking(po=start, ms=10),
        Rng(42),
        handlers={**HANDLERS, SPECIAL_CELL_HOOK_KEY: armed},
    )
    seen, _ = _script(runner.run(), [into])

    assert asked == [(861, 14)]
    assert runner.state.players[0].po == start, "the player stepped onto an armed cell"
    assert seen[-1] == MapMove(outcome="special", player=0)


def test_the_roadblock_gate_never_fires_at_rank_three_or_below():
    """:2041 ``ra(sp)>3``: the gate the config keeps for the roadblock it will build."""

    class _AlwaysHit:
        def range(self, n):
            return 0  # rnd(5)==0 would satisfy the roll

    for rank in (1, 3):
        assert roadblock_would_fire(_walking(po=18, ms=20, rank=rank), 20, _AlwaysHit()) is False
    state = _walking(po=18, ms=20, rank=4)
    assert roadblock_would_fire(state, 20, _AlwaysHit()) is True
    assert roadblock_would_fire(state, 19, _AlwaysHit()) is False


# --------------------------------------------------------------------------- #
# The previous tile, ll(sp)                                                    #
# --------------------------------------------------------------------------- #
def test_the_previous_tile_is_written_after_a_visit_and_cleared_at_the_turn_start(tmp_path):
    door, la = _door("pub", ln=2)
    start, into = _approach(door)
    runner = _runner(_walking(po=start, ms=20), Rng(42))
    # :2055 gosub3000:ll(sp)=20*la+ln -- after the visit, whatever was chosen there.
    seen, _ = _script(runner.run(), [into, None])
    assert _types(seen) == [MapMove, LocationMenu, MapMove]
    assert runner.state.players[0].previous_tile == (la, 2)

    # Saved with the game.
    save = tmp_path / "ll.jsonl"
    save_game(save, runner.state, effect_log=[], rng_log=[], seed=42)
    assert load_game(save, _CONFIG.registries).state.players[0].previous_tile == (la, 2)

    # :1012 ll(sp)=0 at the next turn start.
    runner = _runner(runner.state, Rng(42))
    seen, _ = _drive(runner, runner.run(TURN_START), stop_at_free_turn=1)
    assert runner.state.players[0].previous_tile == (0, 0)

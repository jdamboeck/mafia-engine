"""The police roadblock on the map — ``mf-prg.bas:2041`` and ``:6000-6036``.

::

    2040 po(sp)=po(sp)+x:ms=ms-1
    2041 ifms/20=int(ms/20)andint(rnd(1)*5)=0andra(sp)>3thengosub6000:goto2060
    2060 ms=ms-5:ifms>0goto2000
    6015 fort=1to2000:next:ifint(rnd(1)*3)=0goto6025
    6016 if(ag(sp)and2)<>0goto6030
    6017 ifta(sp)goto6035
    6018 if(ag(sp)and1)<>0goto6025
    6020 print"{down}er hat einen steckbrief von dir!":gosub1100:goto26020

The tests drive the engine turn runner over the loaded config from the map, one street
step at a time, with the strict :class:`~tests.helpers.StubRng` (raw draws; it raises
when drawn past its script, so a draw the test does not expect fails it). The gate's
roll is ``range(5)`` (0 stops), the clean pass ``range(3)`` (0 passes); a capture then
draws ``range(2)`` for the chief-bribe roll before its menu.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CONFIG_DIR
from data.game_configs.mafia_1920s.handlers.roadblock import ROADBLOCK_SCREEN
from data.game_configs.mafia_1920s.state import Contraband
from engine.config_loader import load_game_config
from engine.interactions import (
    MAP_QUIT,
    Acknowledge,
    Confirm,
    MapMove,
    PromptChoice,
    ShowMessage,
)
from engine.locations import HANDLERS
from engine.turns import SPECIAL_CELL_HOOK_KEY, TURN_OVER_SCREEN, WALKING, TurnRunner
from tests.helpers import StubRng, with_values
from tests.test_turn_runner import _CITY, _DELTA_NAMES, _approach

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks

_BRIBE, _FLEE, _SURRENDER = range(3)
#: :110 ``br=52224``, the map's screen base.
_BR = 52224


def _street_step() -> tuple[int, str, int]:
    """``(start, direction, target)``: a street step onto a plain street cell."""
    for target in range(1000):
        if _CITY.code(target) != 156 or target in _CITY.special_cells:
            continue
        for delta, name in _DELTA_NAMES.items():
            start = target - delta
            if 0 <= start < 1000 and _CITY.code(start) == 156:
                return start, name, target
    raise AssertionError("no street step on the map")


_START, _DIR, _TARGET = _street_step()


def _walking(*, ms: int, rank: int = 4, po: int = _START, **held):
    """A one-player game on the map at ``po``, ``ms`` points left, holding ``held``."""
    state = _CONFIG.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    state = replace(
        state,
        players=(replace(state.players[0], po=po, ms=ms, rank=rank),),
        clock=replace(state.clock, turn_phase=WALKING),
    )
    return with_values(state, Contraband(**held))


def _walk(state, rng, moves, *, menu=_SURRENDER, handlers=None):
    """Drive the runner from the map: ``moves`` answer the map prompts, then a quit.

    The capture menu gets ``menu``; every confirm (the lawyer) is refused; screens are
    acknowledged. Returns ``(seen, runner)``; stops at the turn-over screen or the quit.
    """
    kw = {} if handlers is None else {"handlers": handlers}
    runner = TurnRunner(
        state,
        rng,
        city=_CONFIG.city,
        shells=_CONFIG.shells,
        turn_menu=_CONFIG.menus["turn"],
        **kw,
    )
    moves = list(moves)
    gen = runner.run()
    seen: list = []
    try:
        interaction = next(gen)
        while True:
            seen.append(interaction)
            if isinstance(interaction, Acknowledge) and interaction.key == TURN_OVER_SCREEN:
                gen.close()
                break
            if isinstance(interaction, MapMove):
                response = moves.pop(0) if moves else MAP_QUIT
            elif isinstance(interaction, PromptChoice):
                response = menu
            elif isinstance(interaction, Confirm):
                response = False
            else:
                response = None
            interaction = gen.send(response)
    except StopIteration:
        pass
    return seen, runner


def _screens(seen) -> list[list[str]]:
    """The roadblock screens' line keys."""
    return [
        [key for key, _ in i.params["lines"]]
        for i in seen
        if isinstance(i, Acknowledge) and i.key == ROADBLOCK_SCREEN
    ]


def _finding(seen) -> str:
    (screen,) = _screens(seen)
    assert screen[:2] == ["roadblock.title", "roadblock.papers"]
    return screen[2]


def _caught(seen) -> bool:
    return any(isinstance(i, ShowMessage) and i.key == "police.caught" for i in seen)


def _map_prompts_after_stop(seen) -> int:
    idx = next(i for i, x in enumerate(seen) if isinstance(x, Acknowledge))
    return sum(isinstance(x, MapMove) for x in seen[idx:])


def _turn_over(seen) -> bool:
    return isinstance(seen[-1], Acknowledge) and seen[-1].key == TURN_OVER_SCREEN


# --------------------------------------------------------------------------- #
# The stop: :6015-6036 in the source's order                                  #
# --------------------------------------------------------------------------- #
def test_ae1_a_passport_holder_with_nothing_else_passes():
    """AE1: rank 4, a passport, no counterfeit, no alcohol; the gate fires, the
    1-in-3 clean pass misses: the officer finds nothing and the turn goes on."""
    rng = StubRng(0, 1)  # :2041 rnd(5)=0; :6015 rnd(3)=1, no clean pass
    seen, runner = _walk(_walking(ms=21, fake_papers=1), rng, [_DIR])

    assert _finding(seen) == "roadblock.nothing"
    assert not _caught(seen)
    player = runner.state.players[0]
    assert player.po == _TARGET
    assert player.ms == 20 - 5  # :2060 ms=ms-5
    assert _map_prompts_after_stop(seen) == 1, "the map did not go on"
    assert rng.calls == [("range", 5), ("range", 3)]


def test_ae2_counterfeit_money_is_caught_even_with_a_passport():
    """AE2: the same player with counterfeit money is caught for it (:6016 comes
    before the passport's :6018)."""
    rng = StubRng(0, 1, 0)  # gate, no clean pass, :26021's roll
    state = _walking(ms=21, fake_papers=1, counterfeit=1)
    seen, runner = _walk(state, rng, [_DIR])

    assert _finding(seen) == "roadblock.counterfeit"
    assert _caught(seen)
    player = runner.state.players[0]
    assert player.po == 911, "not sentenced"  # :26080 po(sp)=911
    # The marks stay: only the barrels are ever taken (:6036).
    assert game.contraband(player) == Contraband(fake_papers=1, counterfeit=1)
    assert _turn_over(seen)


def test_alcohol_is_found_the_barrels_go_and_the_player_is_caught():
    rng = StubRng(0, 1, 0)
    seen, runner = _walk(_walking(ms=21, alcohol_barrels=7), rng, [_DIR])

    assert _finding(seen) == "roadblock.alcohol"
    assert _caught(seen)
    assert game.contraband(runner.state.players[0]).alcohol_barrels == 0  # :6036 ta(sp)=0


def test_with_no_marks_and_no_alcohol_the_player_is_caught_on_a_wanted_poster():
    rng = StubRng(0, 1, 0)
    seen, runner = _walk(_walking(ms=21), rng, [_DIR])

    assert _finding(seen) == "roadblock.wanted_poster"
    assert _caught(seen)
    assert runner.state.players[0].po == 911


def test_a_passport_holder_with_alcohol_is_still_caught():
    """:6017 (alcohol) comes before :6018 (the passport)."""
    rng = StubRng(0, 1, 0)
    seen, runner = _walk(_walking(ms=21, fake_papers=1, alcohol_barrels=3), rng, [_DIR])

    assert _finding(seen) == "roadblock.alcohol"
    assert _caught(seen)
    assert game.contraband(runner.state.players[0]).alcohol_barrels == 0


def test_the_clean_pass_comes_before_anything_is_looked_at():
    """:6015's 1-in-3 pass: even counterfeit money and alcohol go through."""
    rng = StubRng(0, 0)
    seen, runner = _walk(_walking(ms=21, counterfeit=1, alcohol_barrels=5), rng, [_DIR])

    assert _finding(seen) == "roadblock.nothing"
    assert not _caught(seen)
    assert game.contraband(runner.state.players[0]).alcohol_barrels == 5


def test_the_capture_gets_the_map_steps_p():
    """KTD-10: capture is entered with :2030's ``p=br+po(sp)+x``, the cell reached. With
    chief-bribe months the :26021 roll skips the menu and :26037 compares the cash
    with that p: unpayable, straight to trial."""
    state = _walking(ms=21)
    state = with_values(state, replace(game.wanted(state.players[0]), bribe_months=2))
    state = replace(state, players=(replace(state.players[0], ka=_BR + _TARGET - 1),))
    rng = StubRng(0, 1, 1)  # gate, no clean pass, :26021 skips the menu
    seen, runner = _walk(state, rng, [_DIR])

    assert not any(isinstance(i, PromptChoice) for i in seen)
    assert any(isinstance(i, ShowMessage) and i.key == "system.not_enough_money" for i in seen)
    assert runner.state.players[0].ka == _BR + _TARGET - 1, "the stale p was paid"

    # One dollar more and the stale p is paid (then :26038's 1-in-5 lets the player go).
    state = replace(state, players=(replace(state.players[0], ka=_BR + _TARGET),))
    rng = StubRng(0, 1, 1, 1)
    seen, runner = _walk(state, rng, [_DIR])
    assert runner.state.players[0].ka == 0


# --------------------------------------------------------------------------- #
# The gate: :2041                                                             #
# --------------------------------------------------------------------------- #
def test_the_gate_never_fires_at_rank_three_or_below_nor_off_a_multiple_of_20():
    """``ra(sp)>3`` and ``ms/20=int(ms/20)``: the empty rng proves the port draws no
    roll when either term fails (its draw policy); the stop cannot happen."""
    for rank, ms in ((1, 21), (3, 21), (3, 1), (4, 22), (4, 20), (10, 2)):
        rng = StubRng()
        seen, runner = _walk(_walking(ms=ms, rank=rank), rng, [_DIR])
        assert _screens(seen) == [], (rank, ms)
        assert runner.state.players[0].ms == ms - 1, (rank, ms)
        assert rng.calls == []


def test_the_gate_fires_on_one_roll_in_five():
    for roll in (1, 2, 3, 4):
        rng = StubRng(roll)
        seen, runner = _walk(_walking(ms=41, rank=4), rng, [_DIR])
        assert _screens(seen) == []
        assert runner.state.players[0].ms == 40
    rng = StubRng(0, 0)
    seen, runner = _walk(_walking(ms=41, rank=4), rng, [_DIR])
    assert _screens(seen) == [["roadblock.title", "roadblock.papers", "roadblock.nothing"]]


# --------------------------------------------------------------------------- #
# The charge: :2060                                                           #
# --------------------------------------------------------------------------- #
def test_a_clean_pass_costs_5_points_and_one_that_takes_them_to_0_ends_the_turn():
    rng = StubRng(0, 0)
    seen, runner = _walk(_walking(ms=21), rng, [_DIR])
    assert runner.state.players[0].ms == 15
    assert not _turn_over(seen)

    # :2060 ms=ms-5:ifms>0goto2000 -- from 0 it falls through: no more map.
    rng = StubRng(0, 0)
    seen, runner = _walk(_walking(ms=1), rng, [_DIR])
    assert runner.state.players[0].ms == -5
    assert _map_prompts_after_stop(seen) == 0
    assert _turn_over(seen)


def test_the_step_that_reaches_0_points_can_still_trigger_the_roadblock():
    rng = StubRng(0, 1, 0)
    seen, runner = _walk(_walking(ms=1), rng, [_DIR])

    assert _finding(seen) == "roadblock.wanted_poster"
    assert _caught(seen)
    assert _turn_over(seen)


def test_an_escape_continues_the_map_after_one_5_point_charge():
    # :26040 ``int(rnd(1)*tr(sp)/11)=0`` misses at 20 of seat 1's 35: escaped.
    rng = StubRng(0, 1, 0, 20)
    seen, runner = _walk(_walking(ms=21), rng, [_DIR], menu=_FLEE)

    assert any(isinstance(i, ShowMessage) and i.key == "police.escaped" for i in seen)
    player = runner.state.players[0]
    assert player.ms == 15  # one charge only
    assert player.po == _TARGET
    assert _map_prompts_after_stop(seen) == 1, "the map did not go on"


def test_an_unarmed_cell_569_is_a_street_and_can_trigger_the_roadblock():
    start, into = _approach(569)
    rng = StubRng(0, 0)
    seen, runner = _walk(_walking(ms=21, po=start), rng, [into])

    assert runner.state.players[0].po == 569
    assert _finding(seen) == "roadblock.nothing"
    assert runner.state.players[0].ms == 15


def test_an_armed_event_cell_never_draws_the_gate():
    def armed(ctx, *, cell, la):
        yield from ()
        return True

    start, into = _approach(569)
    rng = StubRng()
    seen, runner = _walk(
        _walking(ms=21, po=start),
        rng,
        [into],
        handlers={**HANDLERS, SPECIAL_CELL_HOOK_KEY: armed},
    )

    assert rng.calls == []
    assert _screens(seen) == []


# --------------------------------------------------------------------------- #
# The client                                                                  #
# --------------------------------------------------------------------------- #
def test_a_seeded_client_run_shows_a_roadblock_screen(monkeypatch, tmp_path: Path):
    from tests.test_client_loop import _resume_at

    keys = {"up": "w", "down": "s", "left": "a", "right": "d"}
    # Seed 42 draws 0 from range(5) (the stop) and 0 from range(3) (the clean pass).
    state = _walking(ms=21)
    output, (end, _rng) = _resume_at(monkeypatch, tmp_path, state, WALKING, [keys[_DIR], "x", "q"])

    assert "strassensperre!!!" in output
    assert "ausweiskontrolle! der polizist verlangt\ndeine papiere!" in output
    assert "er hat nichts zu beanstanden." in output
    assert end.players[0].ms == 15

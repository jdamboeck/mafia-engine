"""The turn menu (``mf-prg.bas:1015-1050``) and the overview (``:1200-1245``).

The menu is a declarative shell (``content/menus/turn.yaml``) loaded by the same code
as the location shells; the engine turn runner offers it after the turn start and again
after every action while movement points remain (``:1045 ifms>0goto1015``)::

    1030 getx$:x=val(x$):if(x<1orx>4)andx$<>"{f1}"goto1030
    1031 ifx=4goto1010
    1035 onxgosub1200,2000,27000
    1045 ifms>0goto1015
    1050 goto1010

The map's exit key (``:2019 ifx$="_"thensysie:return``) returns from the map to the
menu. The runner is driven headlessly here; the client is driven through ``play()``.
"""

from __future__ import annotations

import io
import sys
from dataclasses import replace
from pathlib import Path

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CLEAR, CONFIG_DIR, play
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.handlers.turn import GANG_SCREEN
from data.game_configs.mafia_1920s.state import Contraband, Wanted
from engine.config_loader import load_game_config
from engine.interactions import (
    MAP_EXIT,
    MAP_QUIT,
    MAP_SAVE,
    Acknowledge,
    MapMove,
    TurnMenu,
)
from engine.persistence import load_game, save_game
from engine.rng import Rng
from engine.turns import MENU, QUIT, TURN_OVER_SCREEN, TURN_START, WALKING, TurnRunner
from tests.helpers import NEW_GAME_ACKS, deadline

_CONFIG = load_game_config(CONFIG_DIR)
_VEHICLES = _CONFIG.module.load_vehicles(CONFIG_DIR / _CONFIG.config["entities"]["vehicles"])
_TWO = [("alcapone", "the outfit"), ("moran", "north side")]


def _new_game(players=None):
    return _CONFIG.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=players or [("alcapone", "the outfit")]
    )


def _runner(state, rng=None):
    return TurnRunner(
        state,
        rng if rng is not None else Rng(42),
        city=_CONFIG.city,
        shells=_CONFIG.shells,
        turn_menu=_CONFIG.menus["turn"],
    )


def _script(gen, answers):
    """Drive ``gen``: menus and map prompts take ``answers`` in order, the rest ``None``.

    Returns ``(interactions seen, outcome)``: ``"turn_over"`` at the first turn-over
    screen, ``"open"`` when ``answers`` ran out at a prompt, else the run's return.
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
            if isinstance(interaction, (TurnMenu, MapMove)):
                if not answers:
                    gen.close()
                    return seen, "open"
                response = answers.pop(0)
            else:
                response = None
            interaction = gen.send(response)
    except StopIteration as stop:
        return seen, stop.value


def _types(seen) -> list[type]:
    return [type(i) for i in seen]


def _street_step(state) -> str:
    """A direction that steps onto a street from the active player's cell."""
    from engine.movement import DOWN, LEFT, RIGHT, UP, try_move

    assert _CONFIG.city is not None
    for name, delta in (("up", UP), ("left", LEFT), ("down", DOWN), ("right", RIGHT)):
        if try_move(state, _CONFIG.city, delta).payload.kind == "step":
            return name
    raise AssertionError("no stepping move")


# --------------------------------------------------------------------------- #
# The shell                                                                    #
# --------------------------------------------------------------------------- #
def test_the_menu_is_a_shell_with_the_sources_keys():
    menu = _CONFIG.menus["turn"]
    # :1020-1022 numbers the options 1-4; 3 is the gang war (:27000).
    assert [(o.id, o.key) for o in menu.options] == [
        ("overview", "1"),
        ("walk", "2"),
        ("gang_war", "3"),
        ("next_player", "4"),
    ]


def test_the_turn_start_opens_the_menu():
    runner = _runner(_new_game())
    seen, outcome = _script(runner.run(TURN_START), [])

    assert outcome == "open"
    assert seen[-1] == TurnMenu(
        options=("overview", "walk", "gang_war", "next_player"),
        keys=("1", "2", "3", "4"),
        player=0,
    )
    assert runner.state.clock.turn_phase == MENU


# --------------------------------------------------------------------------- #
# :1031 next player                                                            #
# --------------------------------------------------------------------------- #
def test_picking_next_player_ends_the_turn_with_movement_points_unspent():
    state = _new_game()
    runner = _runner(state)
    seen, outcome = _script(runner.run(TURN_START), ["4"])

    assert outcome == "turn_over"
    assert _types(seen) == [TurnMenu, Acknowledge]
    tr = _VEHICLES[state.players[0].vehicle]["tr"]
    assert runner.state.players[0].ms == tr, "the unspent points were touched"


# --------------------------------------------------------------------------- #
# :1030 an out-of-range key                                                    #
# --------------------------------------------------------------------------- #
def test_an_out_of_range_key_is_ignored():
    runner = _runner(_new_game())
    # 0, 5, a letter, nothing: each asks the same menu again.
    seen, outcome = _script(runner.run(TURN_START), ["0", "5", "x", None])

    assert outcome == "open"
    menus = [i for i in seen if isinstance(i, TurnMenu)]
    assert len(menus) == 5 and len(set(menus)) == 1
    assert _types(seen) == [TurnMenu] * 5
    assert runner.state.clock.turn_phase == MENU


# --------------------------------------------------------------------------- #
# :1045 / :2019 walking and returning                                          #
# --------------------------------------------------------------------------- #
def test_walking_and_returning_with_the_exit_key_reshows_the_menu():
    state = _new_game()
    runner = _runner(state)
    tr = _VEHICLES[state.players[0].vehicle]["tr"]
    seen, outcome = _script(runner.run(TURN_START), ["2", _street_step(state), MAP_EXIT])

    assert outcome == "open"
    assert _types(seen) == [TurnMenu, MapMove, MapMove, TurnMenu]
    assert MAP_EXIT in seen[1].commands
    assert runner.state.players[0].ms == tr - 1
    assert runner.state.clock.turn_phase == MENU


def test_the_menu_keeps_coming_back_while_movement_points_remain():
    state = _new_game()
    runner = _runner(state)
    step = _street_step(state)
    seen, outcome = _script(runner.run(TURN_START), ["2", MAP_EXIT, "2", step, MAP_EXIT, "4"])

    assert outcome == "turn_over"
    assert _types(seen) == [TurnMenu, MapMove, TurnMenu, MapMove, MapMove, TurnMenu, Acknowledge]


def test_when_the_points_run_out_on_the_map_the_turn_ends_with_no_menu():
    state = _new_game()
    active = replace(state.players[0], ms=2)
    state = replace(state, players=(active,), clock=replace(state.clock, turn_phase=MENU))
    runner = _runner(state)
    step = _street_step(state)
    seen, outcome = _script(runner.run(), ["2", step, step])

    # :2005 ifms<=0thenreturn, then :1045 is false: :1050 goto1010, no menu.
    assert outcome == "turn_over"
    assert _types(seen) == [TurnMenu, MapMove, MapMove, Acknowledge]
    assert runner.state.players[0].ms == 0


# --------------------------------------------------------------------------- #
# Save and quit at the menu                                                    #
# --------------------------------------------------------------------------- #
def test_the_menu_offers_save_and_quit():
    state = replace(_new_game(), clock=replace(_new_game().clock, turn_phase=MENU))
    runner = _runner(state)
    gen = runner.run()

    prompt = next(gen)
    assert isinstance(prompt, TurnMenu)
    assert MAP_SAVE in prompt.commands and MAP_QUIT in prompt.commands
    assert gen.send(MAP_SAVE) == prompt
    assert runner.state == state
    try:
        gen.send(MAP_QUIT)
    except StopIteration as stop:
        assert stop.value == QUIT
    else:
        raise AssertionError("the quit did not end the run")


def _play(monkeypatch, lines: list[str], **kw) -> str:
    return _play_returning(monkeypatch, lines, **kw)[0]


def _play_returning(monkeypatch, lines: list[str], **kw):
    """``play()`` over exactly ``lines`` of stdin; returns ``(stdout, (state, rng))``."""
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(30, "play() did not return"):
        result = play(**kw)
    return out.getvalue(), result


def test_a_save_at_the_menu_resumes_to_the_menu_with_the_same_movement_points(
    monkeypatch, tmp_path: Path
):
    save = tmp_path / "game.jsonl"
    step = {"up": "w", "left": "a", "down": "s", "right": "d"}[_street_step(_new_game())]
    # Title, house rules, upkeep, walk, a step, back to the menu, save, quit.
    first = _play(
        monkeypatch,
        [*NEW_GAME_ACKS, "2", step, "m", "p", "q"],
        seed=42,
        end_year=1930,
        score_weight=1.0,
        save=str(save),
    )
    assert str(save) in first.split(CLEAR)[-1], "the save did not happen at the menu"
    saved = load_game(save, _CONFIG.registries).state
    assert saved.clock.turn_phase == MENU
    ms = saved.players[0].ms
    assert ms == _VEHICLES[0]["tr"] - 1

    resumed, (state, _rng) = _play_returning(monkeypatch, ["2", "q"], load=str(save))
    screens = resumed.split(CLEAR)[1:]
    assert "ist an der reihe" not in resumed, "upkeep ran again on resume"
    assert "was willst du tun:" in screens[0], "the resumed game did not open at the menu"
    assert "║" in screens[1], "walking from the resumed menu did not open the map"
    assert state.players[0].ms == ms, "the resumed turn did not keep its movement points"


def test_the_map_exit_key_returns_to_the_menu_in_the_client(monkeypatch):
    output = _play(
        monkeypatch, [*NEW_GAME_ACKS, "2", "m", "q"], seed=42, end_year=1930, score_weight=1.0
    )
    screens = output.split(CLEAR)
    assert "║" in screens[-2], "the map was not shown"
    assert "was willst du tun:" in screens[-1], "the exit key did not return to the menu"


# --------------------------------------------------------------------------- #
# :1200-1245 the overview                                                      #
# --------------------------------------------------------------------------- #
def _marked_player_state():
    state = _new_game()
    boss = state.players[0].roster[0]
    hired = Gangster(name="bugs", weapon=5, energie=5, kraft=41, intelligenz=7, brutalitaet=12)
    values = {
        **state.players[0].values,
        **game.values_of(
            Contraband(fake_papers=1, counterfeit=1, alcohol_barrels=3),
            Wanted(bribe_months=2),
            rented_months=4,
        ),
    }
    active = replace(
        state.players[0], ka=6543, gf=12.5, rank=2, ms=17, roster=(boss, hired), values=values
    )
    return replace(state, players=(active,), clock=replace(state.clock, turn_phase=MENU, month=3))


def test_the_overview_shows_the_players_state_and_both_marks(monkeypatch, tmp_path):
    save = tmp_path / "marked.jsonl"
    state = _marked_player_state()
    save_game(save, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=42)

    # The summary's key, then one per gangster and the gang page's closing key (:1235).
    output = _play(monkeypatch, ["1", "", "", "", "", "q"], load=str(save))
    screens = output.split(CLEAR)[1:]
    menu, summary, gang = screens[0], screens[1], screens[4]
    boss = state.players[0].roster[0]

    # :1015-1017 the menu's head: name, gang, cash and the date.
    assert "alcapone - the outfit" in menu
    assert "kapital: 6543" in menu and "jahr:1925-4" in menu
    # :1200-1225
    assert "uebersicht fuer alcapone:" in summary
    assert "punkte: 12.5" in summary
    assert "rang: schlaeger" in summary
    assert "transportmittel: fuesse 17 s" in summary
    assert "alkohol: 3 faesser" in summary
    assert "gegenstaende: papiere,falschgeld" in summary  # :1221-1222, ag$ at :50600
    assert "schmiergelder: 2 mon." in summary
    assert "bezahlte miete: 4 mon." in summary
    # :1230-1240, :1300-1320
    assert "gangster:" in gang
    assert boss.name in gang and "bugs" in gang
    assert "e05 k41 i07 b12" in gang
    assert "w:revolver" in gang
    # :1045 back to the menu, nothing spent.
    assert "was willst du tun:" in screens[5]


def test_the_overview_shows_no_items_line_without_marks():
    state = replace(_new_game(), clock=replace(_new_game().clock, turn_phase=MENU))
    runner = _runner(state)
    seen, _ = _script(runner.run(), ["1"])

    screens = [i for i in seen if isinstance(i, Acknowledge)]
    keys = [key for key, _ in screens[0].params["lines"]]
    assert not any("items" in key for key in keys), keys  # :1220 ifag(sp)=0goto1225
    assert isinstance(seen[-1], TurnMenu)
    assert runner.state.players[0] == state.players[0]


def test_the_gang_page_waits_after_each_gangster_then_once_more():
    """:1235 ``fori=1togz(sp):print:a=sp:b=i:gosub1300:poke198,0:wait198,1`` waits for a
    key after each gangster, then :1245 ``goto1100`` for the closing key (#122).

    Each gang screen shows the gangsters printed so far, so ``gz`` gangsters take
    ``gz`` + 1 keys; the last screen is the whole gang.
    """
    runner = _runner(_marked_player_state())
    seen, _ = _script(runner.run(), ["1"])

    gang = [i for i in seen if isinstance(i, Acknowledge) and i.key == GANG_SCREEN]
    shown = [
        [p["name"] for k, p in s.params["lines"] if k == "turn.overview.gangster"] for s in gang
    ]
    boss = runner.state.players[0].roster[0].name
    assert shown == [[boss], [boss, "bugs"], [boss, "bugs"]]
    assert isinstance(seen[-1], TurnMenu)


def test_an_empty_gang_page_waits_once():
    # :1230 ``ifgz(sp)=0thenprint"{down}keine!":goto1100`` -- one key.
    state = replace(_new_game(), clock=replace(_new_game().clock, turn_phase=MENU))
    active = replace(state.players[0], roster=())
    runner = _runner(replace(state, players=(active,)))
    seen, _ = _script(runner.run(), ["1"])

    gang = [i for i in seen if isinstance(i, Acknowledge) and i.key == GANG_SCREEN]
    assert [[k for k, _ in s.params["lines"]] for s in gang] == [
        ["turn.overview.gang_title", "turn.overview.no_gang"]
    ]


def test_walking_opens_the_map_in_the_walking_phase():
    runner = _runner(replace(_new_game(), clock=replace(_new_game().clock, turn_phase=MENU)))
    seen, _ = _script(runner.run(), ["2"])
    assert isinstance(seen[-1], MapMove)
    assert runner.state.clock.turn_phase == WALKING

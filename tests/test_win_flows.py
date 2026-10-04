"""The map win flows — ``mf-prg.bas:2002-2003``, ``:2045-2046``, ``:23000-23030``,
``:24000-24020``.

::

    2002 poke646,0:iftp(sp)=3thenpokebr+569,135:pokefr+569,6
    2003 iftp(sp)=5thenpokebr+861,130:pokefr+861,2
    2035 sysie:ifpeek(p)<>156goto2045
    2045 ifpo(sp)+x=569thenla=13:ln=1:gosub23000:goto2060
    2046 ifpo(sp)+x=861thenla=14:ln=1:gosub24000:goto2060
    2060 ms=ms-5:ifms>0goto2000
    23010 ifgz(sp)<3thenprint"{down}du hast zuwenig gangster!":tp(sp)=0:goto1100
    23025 bn$(0)="eskorte":gz(0)=10:e=50:w=7:kf$="kgtp":gosub5000:ifs=2goto26020
    23030 x=4:gosub1160:x5%(sp)=1:goto20050
    24005 bn$(0)="leibwaechter":gz(0)=5:e=20:w=7:kf$="ks":gosub5000:ifs=2goto26020
    24010 bn$(0)="buergermeister":gz(0)=1:e=30:w=1:kf$="ks":gosub5000:ifs=2goto26020
    24020 ka(sp)=ka(sp)+7000:ag(sp)=ag(sp)or1:tp(sp)=0:x6%(sp)=1:goto1100

The flows run through the engine turn runner from the map (the special-cell hook), or
directly through ``run_pure``, with the strict :class:`~tests.helpers.StubRng`. The
fights are stood in for (``run_encounter`` in ``handlers/win_flows.py``) except where a
real fight is the subject.
"""

from __future__ import annotations

import functools
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from clients.terminal import CONFIG_DIR
from data.game_configs.mafia_1920s.effects import (
    MarkSet,
    ScoreAndRank,
    TipClear,
    TipSet,
    WinFlagSet,
)
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.handlers.win_flows import WIN_FLOWS_SCREEN, armed_cells
from data.game_configs.mafia_1920s.setup import load_encounter, run_encounter
from data.game_configs.mafia_1920s.state import contraband, tip_target, wanted
from engine.combat import CombatResult
from engine.config_loader import load_game_config
from engine.effects import MoneyChange, commit
from engine.interactions import (
    MAP_QUIT,
    Acknowledge,
    Confirm,
    MapMove,
    PromptChoice,
    ShowMessage,
)
from engine.locations import HANDLERS
from engine.persistence import load_game, save_game
from engine.rng import Rng
from engine.turns import SPECIAL_CELL_HOOK_KEY, TURN_OVER_SCREEN, WALKING, TurnRunner
from tests.helpers import StubRng, run_pure, scripted, with_values
from tests.test_turn_runner import _CITY, _approach

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks

_TRANSPORT, _MAYOR = 569, 861
_BRIBE, _FLEE, _SURRENDER = range(3)

_G = Gangster(name="alcapone", energie=40, kraft=30, intelligenz=40, brutalitaet=30)
_GANG3 = (_G, replace(_G, name="g2", vitality=30), replace(_G, name="g3", vitality=20))


def _walking(*, ms: int = 21, rank: int = 3, po: int = 0, tip: int = 0, roster=_GANG3):
    """A one-player game on the map at ``po`` holding tip ``tip``. Rank 3: the
    roadblock's ``ra(sp)>3`` fails, so it draws nothing."""
    state = _CONFIG.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    state = replace(
        state,
        players=(replace(state.players[0], po=po, ms=ms, rank=rank, roster=roster),),
        clock=replace(state.clock, turn_phase=WALKING),
    )
    return with_values(state, tip_target=tip)


def _walk(state, rng, moves, *, menu=_SURRENDER):
    """Drive the runner from the map: ``moves`` answer the map prompts, then a quit.

    The capture menu gets ``menu``; every confirm (the lawyer) is refused; screens are
    acknowledged. Returns ``(seen, runner)``; stops at the turn-over screen or the quit.
    """
    runner = TurnRunner(
        state, rng, city=_CONFIG.city, shells=_CONFIG.shells, turn_menu=_CONFIG.menus["turn"]
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
    """The flows' own screens' line keys."""
    return [
        [key for key, _ in i.params["lines"]]
        for i in seen
        if isinstance(i, Acknowledge) and i.key == WIN_FLOWS_SCREEN
    ]


def _fights(monkeypatch, *winners: int, vitality=()):
    """Stand in for the flows' fights, the n-th ending with ``winners[n]``. Returns the
    fights fought as ``(encounter key, kwargs)``. ``vitality`` is each fight's
    ``roster_vitality``."""
    fought: list = []
    results = list(winners)
    closing = list(vitality)

    def fight(ctx, encounter, **kwargs):
        fought.append((encounter.key, kwargs))
        return CombatResult(
            winner=results.pop(0),
            losses=(0, 1),
            roster_vitality=closing.pop(0) if closing else (),
        )
        yield  # a generator, as run_encounter is

    package = HANDLERS[SPECIAL_CELL_HOOK_KEY].__module__
    monkeypatch.setattr(sys.modules[package], "run_encounter", fight)
    return fought


def _hook(state, answers=(), draws=(), *, cell):
    """Run the special-cell hook for a move onto ``cell`` through ``run_pure``."""
    rng = StubRng(*draws)
    source = scripted(*answers)
    la = _CITY.special_cells[cell]
    handler = functools.partial(HANDLERS[SPECIAL_CELL_HOOK_KEY], cell=cell, la=la)
    result = run_pure(handler, source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _score(x: int) -> ScoreAndRank:
    return ScoreAndRank(amount=x, rank_divisor=11.1)


# --------------------------------------------------------------------------- #
# Arming (:2002-2003, :2035)                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cell", [_TRANSPORT, _MAYOR])
def test_without_the_tip_the_cell_is_a_street(cell):
    """An unarmed event cell is street code 156: the player steps onto it (``:2040``)."""
    start, into = _approach(cell)
    seen, runner = _walk(_walking(po=start, ms=21), StubRng(), [into])

    assert runner.state.players[0].po == cell
    assert runner.state.players[0].ms == 20
    assert _screens(seen) == []
    assert MapMove(outcome="step", player=0) in seen


@pytest.mark.parametrize(("cell", "tip"), [(_TRANSPORT, 5), (_MAYOR, 3)])
def test_the_wrong_tip_walks_the_cell_as_a_street(cell, tip):
    """Tip 5 pokes 861 only and tip 3 569 only: the other cell stays the street."""
    start, into = _approach(cell)
    seen, runner = _walk(_walking(po=start, ms=21, tip=tip), StubRng(), [into])

    assert runner.state.players[0].po == cell
    assert runner.state.players[0].ms == 20
    assert _screens(seen) == []
    assert tip_target(runner.state.players[0]) == tip


def test_with_tip_3_stepping_on_569_starts_the_transport(monkeypatch):
    """``:2045 gosub23000:goto2060``: the flow runs, the player stays where he was, and
    the runner charges the 5 points once after it (``:2060``)."""
    fought = _fights(monkeypatch, 2)  # lost: the arrest, surrendered
    start, into = _approach(_TRANSPORT)
    # the :26021 roll; the lawyer refused, the sentence (rank 3)
    seen, runner = _walk(_walking(po=start, ms=21, tip=3), StubRng(0), [into])

    assert _screens(seen)[0] == ["win_flows.transport_title", "win_flows.transport_escort"]
    assert [key for key, _ in fought] == ["win_escort"]
    assert runner.state.players[0].po != _TRANSPORT


def test_the_flow_ends_with_the_five_point_charge(monkeypatch):
    """``goto2060``: a flow that leaves the points as they were costs 5 (here: a gang
    too small, :23010, which also loses the tip, so the cell is a street again)."""
    start, into = _approach(_TRANSPORT)
    seen, runner = _walk(_walking(po=start, ms=21, tip=3, roster=(_G, _G)), StubRng(), [into])

    player = runner.state.players[0]
    assert _screens(seen) == [["win_flows.transport_title", "win_flows.transport_too_few"]]
    assert player.ms == 16
    assert player.po == start
    assert tip_target(player) == 0
    assert seen[-1] == MapMove(outcome="special", player=0)


def test_the_armed_cells_follow_the_tip():
    """The armed state is derived from the tip, never stored."""
    assert armed_cells(_walking()) == set()
    assert armed_cells(_walking(tip=3)) == {_TRANSPORT}
    assert armed_cells(_walking(tip=5)) == {_MAYOR}
    assert armed_cells(_walking(tip=1)) == set()


def test_buying_a_different_tip_disarms_the_old_cell():
    """``:12225 tp(sp)=...`` overwrites the held tip: one cell armed at a time."""
    state = _walking(tip=3)
    state = commit(state, [TipSet(5)]).state

    assert armed_cells(state) == {_MAYOR}


def test_the_win_flags_and_the_tip_survive_a_save_and_a_load(tmp_path: Path):
    """The flags and the tip are saved; after the load the cell is armed again."""
    state = _walking(tip=5)
    state = commit(state, [WinFlagSet("x5"), WinFlagSet("x6")]).state
    path = tmp_path / "game.jsonl"
    save_game(path, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=42)

    loaded = load_game(path, _CONFIG.registries).state

    assert loaded == state
    assert (wanted(loaded.players[0]).x5, wanted(loaded.players[0]).x6) == (True, True)
    assert armed_cells(loaded) == {_MAYOR}


# --------------------------------------------------------------------------- #
# The cash transport (:23000-23030, :20050-20060)                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("roll", "pay"), [(0, 7000), (2999, 9999)])
def test_winning_the_transport_sets_x5_and_pays_within_range(monkeypatch, roll, pay):
    """``:23030 x=4:gosub1160:x5%(sp)=1:goto20050``; ``:20050`` pays 4000..6999; the held
    tip 3 matches ``:20051`` ``(x=3andla=13)``: cleared, +3000; then +4 again
    (``:20060``)."""
    fought = _fights(monkeypatch, 1)
    result, source = _hook(_walking(tip=3), answers=(None,), draws=(roll,), cell=_TRANSPORT)

    assert [key for key, _ in fought] == ["win_escort"]
    assert result.payload.returned is True
    assert result.effects == [
        _score(4),
        WinFlagSet("x5"),
        TipClear(),
        MoneyChange(pay),
        _score(4),
    ]
    assert [(m.key, m.params) for m in source.messages()] == [("locations.ban.loot", {"p": pay})]
    player = result.state.players[0]
    assert wanted(player).x5 is True
    assert tip_target(player) == 0


def test_the_transport_can_be_won_again_after_x5():
    """Nothing clears ``x5``: a later tip 3 arms the cell again."""
    state = commit(_walking(tip=0), [WinFlagSet("x5"), TipSet(3)]).state

    assert armed_cells(state) == {_TRANSPORT}


def test_the_escort_is_the_source_fight():
    """``:23025 bn$(0)="eskorte":gz(0)=10:e=50:w=7:kf$="kgtp"``."""
    escort = load_encounter(CONFIG_DIR / "content" / "encounters" / "win_escort.yaml")
    (spec,) = escort.variants
    assert (spec.name, spec.count, spec.vitality, spec.weapon) == ("eskorte", 10, 50, 7)
    assert escort.grid == "kgtp"


# --------------------------------------------------------------------------- #
# The mayor hit (:24000-24020)                                                 #
# --------------------------------------------------------------------------- #
def test_winning_the_mayor_hit_pays_7000_a_passport_and_x6_with_no_score(monkeypatch):
    """``:24020 ka(sp)=ka(sp)+7000:ag(sp)=ag(sp)or1:tp(sp)=0:x6%(sp)=1``: no ``gosub1160``."""
    fought = _fights(monkeypatch, 1, 1)
    result, source = _hook(_walking(tip=5), answers=(None,), cell=_MAYOR)

    assert [key for key, _ in fought] == ["win_mayor_1", "win_mayor_2"]
    assert result.effects == [
        MoneyChange(7000),
        MarkSet(fake_papers=True),
        TipClear(),
        WinFlagSet("x6"),
    ]
    screens = [i for i in source.seen if isinstance(i, Acknowledge)]
    assert [k for k, _ in screens[0].params["lines"]] == [
        "win_flows.mayor_title",
        "win_flows.mayor_reward",
    ]
    player = result.state.players[0]
    assert wanted(player).x6 is True
    assert contraband(player).fake_papers


def test_the_second_mayor_fight_sees_the_first_fights_energy_loss(monkeypatch):
    """The source keeps the gang's energy between ``:24005`` and ``:24010``; the first
    fight's write-back is buffered, so the second fight's gang carries its closing
    energies."""
    fought = _fights(monkeypatch, 1, 1, vitality=[((0, 12), (1, 30), (2, 0)), ()])
    _hook(_walking(tip=5), answers=(None,), cell=_MAYOR)

    (_, first), (_, second) = fought
    assert first.get("roster") is None
    assert [g.vitality for g in second["roster"]] == [12, 30, 0]
    assert [g.name for g in second["roster"]] == ["alcapone", "g2", "g3"]


def test_a_fights_closing_vitality_is_what_it_writes_back():
    """``CombatResult.roster_vitality`` (what the second fight is built from) is the
    energy the fight's buffered write-back commits: a real fight, passed until the
    enemy lands a hit, then surrendered."""
    state = _walking(tip=5, roster=(Gangster(name="g0", energie=5, kraft=10, brutalitaet=10),))
    guards = load_encounter(CONFIG_DIR / "content" / "encounters" / "win_mayor_1.yaml")
    closing: list = []

    def handler(ctx):
        result = yield from run_encounter(ctx, guards)
        closing.append(result.roster_vitality)
        return []

    keys = [("pass", None)] * 40 + [("surrender", None)]
    result = run_pure(handler, scripted(*keys, *[None] * 5), state=state, rng=Rng(0))

    (vitality,) = closing
    after = [g.vitality for g in result.state.players[0].roster]
    assert after != [5], "the fight did no damage: the seed no longer lands a hit"
    assert list(dict(vitality).values()) == after


def test_losing_the_first_mayor_fight_skips_the_second(monkeypatch):
    fought = _fights(monkeypatch, 2)
    # the :26021 roll; surrendered, the lawyer refused
    _, source = _hook(_walking(tip=5), answers=(_SURRENDER, False), draws=(0,), cell=_MAYOR)

    assert [key for key, _ in fought] == ["win_mayor_1"]
    assert "police.caught" in source.message_keys()


@pytest.mark.parametrize(
    ("cell", "tip", "winners"),
    [(_TRANSPORT, 3, (2,)), (_MAYOR, 5, (2,)), (_MAYOR, 5, (1, 2))],
    ids=["transport", "mayor-guards", "mayor"],
)
def test_losing_either_flow_leads_to_capture(monkeypatch, cell, tip, winners):
    """``ifs=2goto26020``: the arrest, then (surrendered) the trial, which clears the
    tip and sets no win flag."""
    _fights(monkeypatch, *winners)
    answers = (None,) if cell == _TRANSPORT else ()
    result, source = _hook(
        _walking(tip=tip), answers=(*answers, _SURRENDER, False), draws=(0,), cell=cell
    )

    assert "police.caught" in source.message_keys()
    player = result.state.players[0]
    assert tip_target(player) == 0
    assert (wanted(player).x5, wanted(player).x6) == (False, False)
    assert wanted(player).jail_months > 0
    assert not any(isinstance(e, (WinFlagSet, MoneyChange)) for e in result.effects)


def test_a_lost_transport_then_an_escape_lets_the_player_restart(monkeypatch):
    """An escape keeps the tip (only the trial clears it, ``:26045``): the cell stays
    armed, and with points left the player steps onto it again."""
    fought = _fights(monkeypatch, 2, 2)
    start, into = _approach(_TRANSPORT)
    # each arrest: the :26021 roll (0), the flight's range roll (20 >= 11: away)
    seen, runner = _walk(
        _walking(po=start, ms=21, tip=3), StubRng(0, 20, 0, 20), [into, into], menu=_FLEE
    )

    player = runner.state.players[0]
    assert [key for key, _ in fought] == ["win_escort", "win_escort"]
    assert [i.key for i in seen if isinstance(i, ShowMessage)].count("police.escaped") == 2
    assert tip_target(player) == 3
    assert player.po == start
    assert player.ms == 11  # two flows, 5 each


# --------------------------------------------------------------------------- #
# The client                                                                  #
# --------------------------------------------------------------------------- #
def test_a_seeded_client_run_shows_the_armed_cell_and_runs_the_transport(
    monkeypatch, tmp_path: Path
):
    """The map draws 569's marker while tip 3 is held; a step onto it runs the flow (a
    gang too small here: the tip is lost) and the next map shows the street again."""
    from tests.test_client_loop import _resume_at

    keys = {"up": "w", "down": "s", "left": "a", "right": "d"}
    start, into = _approach(_TRANSPORT)
    state = _walking(po=start, ms=21, tip=3, roster=(_G,))
    output, (end, _rng) = _resume_at(monkeypatch, tmp_path, state, WALKING, [keys[into], "x", "q"])

    assert "geldtransport-ueberfall" in output
    assert "du hast zuwenig gangster!" in output
    maps = [frame for frame in output.split("╔") if "╚" in frame]
    assert "◌" in maps[0], "the armed cell is not drawn"
    assert "◌" not in maps[-1], "the disarmed cell is still drawn"
    assert "♛" not in output, "the unarmed mayor cell is drawn"
    assert tip_target(end.players[0]) == 0
    assert end.players[0].ms == 16

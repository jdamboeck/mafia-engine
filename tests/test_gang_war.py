"""The gang war — turn menu option 3, ``mf-prg.bas:27000-27045``.

::

    27000 ifsz=1thenprint"{clr}{down}bei solo-spiel nicht moeglich!":goto1100
    27001 ifja<1925+4/12thenprint"{clr}{down}erst ab 4/1925 moeglich!":goto1100
    27016 us=val(x$):ifus=0thenreturn
    27017 ifus<1orus>szorus=spgoto27015
    27018 ifgs(us)goto27100
    27020 ks(1)=us:ks(2)=sp:kf$="ks":gosub30000:a=ks(s):b=ks(1-(s=1))
    27025 p=int(rnd(1)*ka(b)/6)+int(ka(b)/4)
    27031 print"{down}mitnehmen (j/n) ?":gosub1115:ifx$="j"thentm(a)=tm(b):tm(b)=0
    27035 ka(a)=ka(a)+p:ka(b)=ka(b)-p:ag(a)=ag(a)or(ag(b)and1):ag(b)=ag(b)and254
    27040 x=tk(tm(a))-ta(a):ifx>ta(b)thenx=ta(b)
    27041 ta(a)=ta(a)+x:ta(b)=ta(b)-x:x=3:gosub1160:y=sp:sp=b:x=-1:gosub1160:sp=y
    27045 ms=ms-10:goto1100

The handler runs through the engine turn runner from the menu, or directly through
``run_pure``. The fight is stood in for (``run_gang_fight`` in ``handlers/gang_war.py``)
except where a real fight is the subject. The client is driven through ``play()``.
"""

from __future__ import annotations

import io
import sys
from dataclasses import replace

import pytest

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CLEAR, CONFIG_DIR, play
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.handlers.gang_war import (
    GANG_WAR_SCREEN,
    SCORE_TO_THE_ATTACKER,
    ZEROES_THE_ATTACKERS_BOSS,
)
from data.game_configs.mafia_1920s.handlers.turn import JAIL_SCREEN
from engine.combat import CombatResult
from engine.config_loader import load_game_config
from engine.interactions import (
    Acknowledge,
    CombatScreen,
    Confirm,
    PromptInt,
    StartCombat,
    TurnMenu,
)
from engine.locations import HANDLERS
from engine.persistence import save_game
from engine.turns import MENU, TURN_OVER_SCREEN, TurnRunner
from tests.helpers import StubRng, deadline, run_pure, scripted, with_config, with_values

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks
_VEHICLES = _CONFIG.module.load_vehicles(CONFIG_DIR / "entities" / "vehicles.yaml")
_TWO = [("alcapone", "the outfit"), ("moran", "north side")]
_ATTACKER, _DEFENDER = 0, 1
_G = Gangster(name="g", energie=40, kraft=30, intelligenz=40, brutalitaet=30)


def _state(*, players=_TWO, month: int = 4, year: int = 1925, ms: int = 21, **fields):
    """A game at the turn menu of player 1 (alcapone), both players at 50 points with
    6000 $; ``fields`` maps a player index to its field changes."""
    state = _CONFIG.module.new_game(seed=42, end_year=1930, score_weight=1.0, players=players)
    seats = []
    for idx, player in enumerate(state.players):
        changes = {"gf": 50.0, "ka": 6000, **fields.get(f"p{idx}", {})}
        seats.append(replace(player, **changes))
    seats[0] = replace(seats[0], ms=ms)
    return replace(
        state,
        players=tuple(seats),
        clock=replace(state.clock, year=year, month=month, turn_phase=MENU),
    )


def _with_rule(state, setting: str):
    return with_config(
        state, house_rules={**state.config.house_rules, SCORE_TO_THE_ATTACKER: setting}
    )


def _fights(monkeypatch, *winners: int):
    """Stand in for the duel, the n-th ending with ``winners[n]``; returns the fights
    fought as their keyword arguments."""
    fought: list[dict] = []
    results = list(winners)

    def fight(ctx, **kwargs):
        fought.append(kwargs)
        return CombatResult(winner=results.pop(0), losses=(0, 0))
        yield  # a generator, as run_gang_fight is

    module = sys.modules[HANDLERS["turn.gang_war"].__module__]
    monkeypatch.setattr(module, "run_gang_fight", fight)
    return fought


class _Answers(scripted):
    """A scripted source that also acknowledges screens without an answer."""

    def __call__(self, interaction):
        if isinstance(interaction, Acknowledge):
            self.seen.append(interaction)
            return None
        return super().__call__(interaction)


def _duel(state, answers=("2",), draws=(0,)):
    """Run the handler through ``run_pure``; returns ``(result, source)``."""
    rng = StubRng(*draws)
    source = _Answers(*answers)
    result = run_pure(HANDLERS["turn.gang_war"], source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _menu_run(state, answers, rng=None):
    """Drive the turn runner from the menu: ``answers`` answer the menu and the
    handler's prompts in order, screens are acknowledged. Stops at the turn-over
    screen or when the answers run out at a prompt. Returns ``(seen, runner, outcome)``."""
    runner = TurnRunner(
        state,
        rng if rng is not None else StubRng(),
        city=_CONFIG.city,
        shells=_CONFIG.shells,
        turn_menu=_CONFIG.menus["turn"],
    )
    answers = list(answers)
    gen = runner.run()
    seen: list = []
    interaction = next(gen)
    while True:
        seen.append(interaction)
        if isinstance(interaction, Acknowledge) and interaction.key == TURN_OVER_SCREEN:
            gen.close()
            return seen, runner, "turn_over"
        if isinstance(interaction, (TurnMenu, PromptInt, Confirm)):
            if not answers:
                gen.close()
                return seen, runner, "open"
            response = answers.pop(0)
        else:
            response = None
        interaction = gen.send(response)


def _screens(seen) -> list[list[str]]:
    return [
        [key for key, _ in i.params["lines"]]
        for i in seen
        if isinstance(i, Acknowledge) and i.key == GANG_WAR_SCREEN
    ]


# --------------------------------------------------------------------------- #
# The menu gates (:27000-27001) -- AE5                                         #
# --------------------------------------------------------------------------- #
def test_a_solo_game_is_refused_and_the_menu_returns(monkeypatch):
    """AE5, :27000: one player -- the source's text, no fight, the points unchanged."""
    fought = _fights(monkeypatch)
    state = _state(players=_TWO[:1])
    seen, runner, outcome = _menu_run(state, ["3"])

    assert outcome == "open"
    assert _screens(seen) == [["gang_war.solo"]]
    assert isinstance(seen[-1], TurnMenu), "the menu did not come back"
    assert fought == [] and not any(isinstance(i, StartCombat) for i in seen)
    assert runner.state.players[0].ms == 21


@pytest.mark.parametrize(("year", "month"), [(1925, 0), (1925, 3)])
def test_a_date_before_4_1925_is_refused_and_the_menu_returns(monkeypatch, year, month):
    """AE5, :27001 ``ifja<1925+4/12``: refused through the round shown as 1925-4."""
    fought = _fights(monkeypatch)
    seen, runner, outcome = _menu_run(_state(year=year, month=month), ["3"])

    assert outcome == "open"
    assert _screens(seen) == [["gang_war.too_early"]]
    assert isinstance(seen[-1], TurnMenu)
    assert fought == []
    assert runner.state.players[0].ms == 21


@pytest.mark.parametrize(("year", "month"), [(1925, 4), (1926, 0)])
def test_the_gate_opens_on_the_round_shown_as_1925_5(year, month):
    """In VICE the fourth ``ja=ja+1/12`` compares ``>=1925+4/12``: the round whose header
    reads 1925-5 (0-based month 4) offers the opponents."""
    seen, _runner, outcome = _menu_run(_state(year=year, month=month), ["3"])

    assert outcome == "open"
    assert _screens(seen) == []
    assert isinstance(seen[-1], PromptInt) and seen[-1].key == "gang_war.opponent_prompt"


def test_the_refusal_texts_are_the_sources():
    resolver = _resolver()
    assert resolver.resolve("gang_war.solo") == "bei solo-spiel nicht moeglich!"
    assert resolver.resolve("gang_war.too_early") == "erst ab 4/1925 moeglich!"
    assert resolver.resolve("turn.menu.option.gang_war") == "3 - bandenkrieg"


def _resolver():
    from engine.strings import Resolver

    return Resolver.from_config(CONFIG_DIR)


# --------------------------------------------------------------------------- #
# The opponent (:27010-27017)                                                  #
# --------------------------------------------------------------------------- #
_THREE = [*_TWO, ("bugs", "the gang")]


def test_the_attacker_is_not_listed_and_0_or_return_leaves(monkeypatch):
    """:27010 skips sp; :27016 ``ifus=0thenreturn`` -- 0, and RETURN (val of it is 0),
    leave with nothing done."""
    fought = _fights(monkeypatch)
    state = _state(players=_THREE, p1={"roster": (_G, _G)})
    for answer in ("0", ""):
        result, source = _duel(state, answers=(answer,), draws=())
        listed = [m.params for m in source.messages() if m.key == "gang_war.opponent"]
        assert [p["number"] for p in listed] == [2, 3], "the attacker was listed"
        assert [p["gang_name"] for p in listed] == ["north side", "the gang"]
        assert listed[0]["cash"] == 6000 and listed[0]["gang_size"] == 2
        assert list(result.effects) == []
        assert fought == []


def test_the_attackers_own_number_and_a_number_past_the_players_are_read_again(monkeypatch):
    """:27017 ``ifus<1orus>szorus=spgoto27015``: read again, then 3 picks bugs."""
    fought = _fights(monkeypatch, 2)
    state = _state(players=_THREE)
    result, source = _duel(state, answers=("1", "4", "9", "3"))

    prompts = [i for i in source.seen if isinstance(i, PromptInt)]
    assert len(prompts) >= 2, "the attacker's own number was taken"
    assert fought == [{"defender": 2, "attacker": 0, "grid": "ks"}]


def test_a_jailed_opponent_is_listed_and_leaves_the_duel_for_the_prison_brawl(monkeypatch):
    """:27018 ``ifgs(us)goto27100``: no duel, the brawl's offer instead; "n" at its
    :1110 leaves (:27110 ``ifx$="n"thenreturn``) with nothing done and nothing spent."""
    fought = _fights(monkeypatch)
    state = with_values(_state(), game.Wanted(jail_months=2), idx=1)
    result, source = _duel(state, answers=("2", False), draws=())

    assert [m.params["number"] for m in source.messages() if m.key == "gang_war.opponent"] == [2]
    assert "gang_war.inmate_offer" in [m.key for m in source.messages()]
    assert fought == []
    assert list(result.effects) == []


# --------------------------------------------------------------------------- #
# The duel (:27020) and its cost (:27045)                                      #
# --------------------------------------------------------------------------- #
def test_a_duel_with_8_points_ends_the_turn(monkeypatch):
    """:27045 ``ms=ms-10`` then :1045 ``ifms>0goto1015`` is false: the turn ends."""
    _fights(monkeypatch, 2)
    seen, runner, outcome = _menu_run(_state(ms=8), ["3", "2"], rng=StubRng(0))

    assert outcome == "turn_over"
    assert runner.state.players[0].ms == -2


def test_a_duel_with_points_left_returns_to_the_menu(monkeypatch):
    _fights(monkeypatch, 2)
    seen, runner, outcome = _menu_run(_state(ms=21), ["3", "2"], rng=StubRng(0))

    assert outcome == "open" and isinstance(seen[-1], TurnMenu)
    assert runner.state.players[0].ms == 11


def test_the_defender_moves_first_and_each_side_names_its_controller():
    """A real fight: side 1 is the defender's gang (``ks(1)=us``) and moves first
    (``:30100 s=1``); every screen names the player who moves it."""
    state = _state(p0={"roster": (_G,)}, p1={"roster": (_G, replace(_G, name="h"))})
    screens: list = []

    def answer(interaction):
        if isinstance(interaction, CombatScreen):
            screens.append(interaction)
            # The defender's two fighters pass, then the attacker gives up.
            return ("pass", None) if interaction.player == _DEFENDER else ("surrender", None)
        if isinstance(interaction, PromptInt):
            return "2"
        return None

    result = run_pure(HANDLERS["turn.gang_war"], answer, state=state, rng=StubRng(0))

    assert [s.player for s in screens] == [_DEFENDER, _DEFENDER, _ATTACKER]
    after = result.state.players
    # The defender won (the attacker surrendered): the plunder is the attacker's cash.
    assert (after[_DEFENDER].ka, after[_ATTACKER].ka) == (6000 + 1500, 6000 - 1500)


def test_the_fight_puts_each_gang_on_its_side_owned_by_its_player():
    """:27020 ``ks(1)=us:ks(2)=sp``; side 2 at :30000's ``129-18*(i=2)`` anchor."""
    from engine.combat_setup import SIDE1_ANCHOR, SIDE2_ANCHOR, STAGGER_OFFSETS

    state = _state(p0={"roster": (_G,) * 3}, p1={"roster": (_G,) * 2})
    gen = HANDLERS["turn.gang_war"](_ctx(state))
    interaction = next(gen)
    while not isinstance(interaction, StartCombat):
        interaction = gen.send(2 if isinstance(interaction, PromptInt) else None)
    gen.close()
    side1, side2 = interaction.scenario.sides
    assert [f.owner for f in side1] == [_DEFENDER] * 2
    assert [f.owner for f in side2] == [_ATTACKER] * 3
    assert [f.position for f in side1] == [SIDE1_ANCHOR + o for o in STAGGER_OFFSETS[:2]]
    assert [f.position for f in side2] == [SIDE2_ANCHOR + o for o in STAGGER_OFFSETS[:3]]
    assert interaction.drivers[1].player == _DEFENDER
    assert interaction.drivers[2].player == _ATTACKER


def _ctx(state):
    from engine.interactions import Ctx

    return Ctx(state=state, rng=StubRng(0))


# --------------------------------------------------------------------------- #
# The consequences (:27025-27041)                                              #
# --------------------------------------------------------------------------- #
def _scores(state):
    return [p.gf for p in state.players]


@pytest.mark.parametrize("setting", ["faithful", "intent"])
def test_a_winning_attacker_scores_3_and_the_defender_loses_1(monkeypatch, setting):
    _fights(monkeypatch, 2)
    result, _ = _duel(_with_rule(_state(), setting))
    assert _scores(result.state) == [53.0, 49.0]


def test_faithful_the_attacker_scores_3_even_when_losing(monkeypatch):
    """:27041 ``x=3:gosub1160`` scores sp before ``sp=b``: a losing attacker nets +2,
    the defender who won nothing."""
    _fights(monkeypatch, 1)
    result, _ = _duel(_with_rule(_state(), "faithful"))
    assert _scores(result.state) == [52.0, 50.0]


def test_intent_the_winning_defender_scores_3(monkeypatch):
    _fights(monkeypatch, 1)
    result, _ = _duel(_with_rule(_state(), "intent"))
    assert _scores(result.state) == [49.0, 53.0]


def test_the_two_score_steps_clamp_one_by_one(monkeypatch):
    """Each ``gosub1160`` clamps at 100 on its own: 99 +3 -> 100, then -1 -> 99."""
    _fights(monkeypatch, 1)
    result, _ = _duel(_state(p0={"gf": 99.0}))
    assert result.state.players[_ATTACKER].gf == 99.0


def test_when_the_defender_wins_the_vehicle_prompt_names_the_defender(monkeypatch):
    """:27026-27031: ``sp$(a)`` is the defender, who answers the j/n (:1115) and takes
    the attacker's car; the defender's own vehicle is gone, the attacker walks."""
    _fights(monkeypatch, 1)
    state = _state(p0={"vehicle": 3}, p1={"vehicle": 1})
    result, source = _duel(state, answers=("2", True))

    confirms = [i for i in source.seen if isinstance(i, Confirm)]
    assert [(c.key, c.player) for c in confirms] == [("gang_war.vehicle_confirm", _DEFENDER)]
    shown = {m.key: m for m in source.messages()}
    assert shown["gang_war.plunder"].player == _DEFENDER
    assert shown["gang_war.plunder"].params["name"] == "moran"
    assert shown["gang_war.vehicle_offer"].params["vehicle"] == _VEHICLES[3]["name"]
    after = result.state.players
    assert (after[_DEFENDER].vehicle, after[_ATTACKER].vehicle) == (3, 0)
    assert after[_DEFENDER].ms == state.players[_DEFENDER].ms, "the vehicle moved ms"


def test_a_refused_vehicle_stays_and_a_loser_on_foot_is_not_asked(monkeypatch):
    _fights(monkeypatch, 2, 2)
    result, _ = _duel(_state(p1={"vehicle": 2}), answers=("2", False))
    assert [p.vehicle for p in result.state.players] == [0, 2]

    result, source = _duel(_state())
    assert not any(isinstance(i, Confirm) for i in source.seen)


def test_a_winner_over_tank_capacity_loses_barrels_to_the_loser(monkeypatch):
    """:27040 ``x=tk(tm(a))-ta(a):ifx>ta(b)thenx=ta(b)`` has no floor: a winner who
    takes a smaller tank while holding more barrels hands the excess to the loser."""
    _fights(monkeypatch, 2)
    # alcapone holds 180 barrels; he takes moran's talbot (tank 100): x = 100-180 = -80.
    state = with_values(_state(p1={"vehicle": 1}), game.Contraband(alcohol_barrels=180), idx=0)
    state = with_values(state, game.Contraband(alcohol_barrels=10), idx=1)
    result, _ = _duel(state, answers=("2", True))

    barrels = [game.contraband(p).alcohol_barrels for p in result.state.players]
    assert _VEHICLES[1]["tank"] == 100
    assert barrels == [100, 90]


def test_the_winner_takes_the_loosers_stock_up_to_the_free_room(monkeypatch):
    _fights(monkeypatch, 2)
    state = with_values(_state(), game.Contraband(alcohol_barrels=30), idx=0)  # foot: 50
    state = with_values(state, game.Contraband(alcohol_barrels=40), idx=1)
    result, _ = _duel(state)
    assert [game.contraband(p).alcohol_barrels for p in result.state.players] == [50, 20]


def test_the_cash_and_the_passport_go_to_the_winner(monkeypatch):
    """:27025 ``p=int(rnd(1)*ka(b)/6)+int(ka(b)/4)``, :27035 the passport bit only."""
    _fights(monkeypatch, 2)
    state = with_values(_state(), game.Contraband(fake_papers=1, counterfeit=1), idx=1)
    # rnd(1) 0.5 of 6000: int(3000/6)=500 + int(6000/4)=1500 -> 2000.
    result, source = _duel(state, draws=(3000,))

    after = result.state.players
    assert (after[_ATTACKER].ka, after[_DEFENDER].ka) == (8000, 4000)
    (screen,) = [i for i in source.seen if isinstance(i, Acknowledge)]
    assert screen.params["lines"] == [("gang_war.plunder", {"name": "alcapone", "amount": 2000})]
    assert game.contraband(after[_ATTACKER]).fake_papers == 1
    marks = game.contraband(after[_DEFENDER])
    assert (marks.fake_papers, marks.counterfeit) == (0, 1)
    assert game.contraband(after[_ATTACKER]).counterfeit == 0


def test_an_employed_defender_fights_a_normal_duel_and_keeps_the_job(monkeypatch):
    fought = _fights(monkeypatch, 2)
    job = game.Job(type=1, pending_pay=2500, months_left=2)
    state = with_values(_state(), job, idx=1)
    result, _ = _duel(state)

    assert fought == [{"defender": _DEFENDER, "attacker": _ATTACKER, "grid": "ks"}]
    assert game.job(result.state.players[_DEFENDER]) == job
    assert result.state.players[_DEFENDER].ka == 6000 - 1500


# --------------------------------------------------------------------------- #
# The client: the defender's side is announced (KTD-8)                          #
# --------------------------------------------------------------------------- #
def test_the_defenders_board_is_announced_and_the_line_survives_the_render(monkeypatch, tmp_path):
    """A two-player duel through ``play()``: the board moran moves is drawn under the
    whose-turn line, after the screen is cleared, not before."""
    state = _state(p0={"roster": (_G,)}, p1={"roster": (_G,)})
    save = tmp_path / "menu.jsonl"
    save_game(save, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=42)
    # 3 gang war, 2 moran; moran passes, alcapone surrenders; the outcome lines
    # need no key, the plunder screen one; then quit at the menu.
    lines = ["3", "2", "p", "surrender", "", "q"]
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(30, "play() did not return"):
        after, _rng = play(load=str(save))

    output = out.getvalue()
    boards = [s for s in output.split(CLEAR) if _is_board(s)]
    assert len(boards) == 2, "not one board per activation"
    assert "spieler moran\nist an der reihe..." in boards[0], "moran's board was not announced"
    assert "ist an der reihe" not in boards[1], "alcapone, the active player, was announced"
    # moran won: his plunder screen clears too, and is announced under its clear.
    (plunder,) = [s for s in output.split(CLEAR) if "du findest beim pluendern" in s]
    assert plunder.index("spieler moran\nist an der reihe...") < plunder.index("moran, du")
    # :27025 from 6000 $: int(6000/4)=1500 plus 0..999.
    assert 6000 + 1500 <= after.players[_DEFENDER].ka <= 6000 + 2499


def _is_board(screen: str) -> bool:
    resolver = _resolver()
    return resolver.resolve("combat.action_prompt") in screen


# --------------------------------------------------------------------------- #
# The prison brawl (:27100-27150)                                              #
# --------------------------------------------------------------------------- #
_BOSS = Gangster(name="boss", weapon=5, energie=30, kraft=30, intelligenz=40, brutalitaet=30)


def _jailed(months: int = 2, **fields):
    """alcapone at the menu, moran in jail for ``months`` with a boss and two men,
    every one armed."""
    gang = (_BOSS, replace(_G, weapon=6), replace(_G, name="h", weapon=1))
    state = _state(p1={"roster": gang}, **fields)
    return with_values(state, game.Wanted(jail_months=months), idx=_DEFENDER)


def _brawls(monkeypatch, *results: CombatResult):
    """Stand in for the brawl's fight, the n-th returning ``results[n]``; returns the
    fights fought as their keyword arguments."""
    fought: list[dict] = []
    queue = list(results)

    def fight(ctx, encounter, **kwargs):
        fought.append({"encounter": encounter.key, **kwargs})
        return queue.pop(0)
        yield  # a generator, as run_encounter is

    # The turn menu holds the handler it resolved when _CONFIG loaded; a play() run
    # loads the config again and registers another copy. Patch both modules.
    menu_handlers = [o.handler for o in _CONFIG.menus["turn"].options if o.handler]
    for handler in (HANDLERS["turn.gang_war"], *menu_handlers):
        if handler.__name__ == "gang_war":
            monkeypatch.setitem(handler.__globals__, "run_encounter", fight)
    return fought


def test_a_defender_with_1_month_left_whose_boss_wins_still_sees_the_jail_screen_next_turn(
    monkeypatch,
):
    """:27140 ``x=int(rnd(1)*2)+1`` (rnd 0 -> 1), :27145 ``gs(us)=gs(us)+x``: moran's
    last month becomes two, and his next turn is the jail screen showing them."""
    _brawls(monkeypatch, CombatResult(winner=1, losses=(0, 0)))
    runner = TurnRunner(
        _jailed(months=1),
        StubRng(0),
        city=_CONFIG.city,
        shells=_CONFIG.shells,
        turn_menu=_CONFIG.menus["turn"],
    )
    # 3 gang war, 2 moran, j; then 4 ends alcapone's turn at the menu.
    answers = ["3", "2", True, "4"]
    gen = runner.run()
    interaction = next(gen)
    jail = None
    with deadline(30, "the next turn never came"):
        while jail is None:
            if isinstance(interaction, Acknowledge) and interaction.key == JAIL_SCREEN:
                jail = (runner.state.clock.active_player, interaction.params["months"])
                break
            asked = isinstance(interaction, (TurnMenu, PromptInt, Confirm))
            interaction = gen.send(answers.pop(0) if asked else None)
    gen.close()
    assert jail == (_DEFENDER, 2), "moran's sentence did not grow, or he was let out"


def test_after_the_brawl_the_jailed_roster_keeps_its_weapons_and_size():
    """:27130 ``gw(us,1)=0:gz(us)=1`` for the fight only, :27135 restores both: the
    boss fights alone and unarmed, and the roster comes out of it as it went in."""
    state = _jailed()
    before = state.players[_DEFENDER].roster
    starts: list[StartCombat] = []
    gen = HANDLERS["turn.gang_war"](_ctx(state))
    interaction = next(gen)
    while not isinstance(interaction, StartCombat):
        interaction = gen.send(2 if isinstance(interaction, PromptInt) else True)
    starts.append(interaction)
    gen.close()
    side1, side2 = starts[0].scenario.sides
    assert [(f.weapon, f.roster_id, f.owner) for f in side1] == [(0, 0, _DEFENDER)]
    assert [(f.weapon, f.vitality, f.owner) for f in side2] == [(3, 50, None)]

    def answer(interaction):
        if isinstance(interaction, CombatScreen):
            return ("surrender", None)
        return 2 if isinstance(interaction, PromptInt) else True

    result = run_pure(HANDLERS["turn.gang_war"], answer, state=state, rng=StubRng(0))
    after = result.state.players[_DEFENDER].roster
    assert len(after) == len(before) == 3
    assert [g.weapon for g in after] == [g.weapon for g in before] == [5, 6, 1]


def test_an_attacker_with_less_than_3000_is_refused_at_no_cost(monkeypatch):
    """:27115 ``ifka(sp)<3000goto1125``, asked after the j/n: "du hast zu wenig kies!",
    no fight, no cash, no points, no movement (``goto1125`` skips :27150)."""
    fought = _brawls(monkeypatch)
    seen, runner, outcome = _menu_run(_jailed(p0={"ka": 2999}), ["3", "2", True])

    assert outcome == "open" and isinstance(seen[-1], TurnMenu)
    assert _screens(seen) == [["system.not_enough_money"]]
    assert fought == []
    attacker = runner.state.players[_ATTACKER]
    assert (attacker.ka, attacker.gf, attacker.ms) == (2999, 50.0, 21)
    assert game.wanted(runner.state.players[_DEFENDER]).jail_months == 2


def test_the_jailed_players_board_is_announced(monkeypatch, tmp_path):
    """KTD-8: the jailed player moves his boss (``ks(1)=us``); his board is drawn under
    the whose-turn line, through ``play()``."""
    state = _jailed()
    save = tmp_path / "menu.jsonl"
    save_game(save, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=42)
    # 3 gang war, 2 moran, j; "verteidige dich" needs a key; moran gives up; the
    # outcome screen's key (:30520) and the lost brawl's (:27150 goto1100); then quit
    # at the menu.
    lines = ["3", "2", "j", "", "surrender", "", "", "q"]
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(30, "play() did not return"):
        after, _rng = play(load=str(save))

    output = out.getvalue()
    boards = [s for s in output.split(CLEAR) if _is_board(s)]
    assert len(boards) == 1, "not one board for moran's one activation"
    assert "spieler moran\nist an der reihe..." in boards[0], "moran's board was not announced"
    assert after.players[_ATTACKER].ka == 6000 - 3000


@pytest.mark.parametrize("setting", ["faithful", "intent"])
def test_the_lost_brawl_zeroes_one_boss_energy(monkeypatch, setting):
    """:27146 ``a=sp:b=1:gosub1350:en=0:gosub1365`` when mr.bonebreaker wins: faithful,
    the ATTACKER's boss goes to 0 and the beaten one keeps the fight's energy (21 left
    here); intent, the beaten boss goes to 0 and the attacker's keeps his 40."""
    _brawls(
        monkeypatch,
        CombatResult(winner=2, losses=(0, 0), roster_vitality=((0, 21),)),
    )
    state = with_config(
        _jailed(p0={"roster": (_G,)}),
        house_rules={**_jailed().config.house_rules, ZEROES_THE_ATTACKERS_BOSS: setting},
    )
    result, _ = _duel(state, answers=("2", True), draws=())

    attacker_boss = result.state.players[_ATTACKER].roster[0].vitality
    jailed_boss = result.state.players[_DEFENDER].roster[0].vitality
    expected = {"faithful": (0, 30), "intent": (40, 0)}[setting]
    assert (attacker_boss, jailed_boss) == expected


# --------------------------------------------------------------------------- #
# The :1100 key wait at each exit (#160)                                       #
# --------------------------------------------------------------------------- #
def _last_wait(source) -> str | None:
    """The key of the trailing acknowledgement (a gang-war screen or the :1100 wait),
    or ``None`` when the handler ended on something else."""
    last = source.seen[-1] if source.seen else None
    return last.key if isinstance(last, Acknowledge) else None


def test_each_gang_war_exit_waits_for_a_key_where_the_source_does(monkeypatch):
    """Every ``goto1100``/``goto1125`` exit ends on a key: the refusals (:27000,
    :27001, :27115) and the duel's result (:27045) are gang-war screens, closed by
    the key; the lost brawl (:27146 -> :27150 ``goto1100``) waits under the fight. The
    ``return`` exits (:27016 ``ifus=0``, :27110 ``ifx$="n"``) wait for nothing."""
    from engine.turns import KEY_WAIT_SCREEN

    _fights(monkeypatch, 2)
    _, source = _duel(_state())  # :27045 ms=ms-10:goto1100
    assert _last_wait(source) == GANG_WAR_SCREEN

    _, source = _duel(_state(players=_TWO), answers=("0",), draws=())  # :27016
    assert _last_wait(source) is None and source.key_waits() == 0

    _, source = _duel(_jailed(), answers=("2", False), draws=())  # :27110
    assert _last_wait(source) is None and source.key_waits() == 0

    _, source = _duel(_jailed(p0={"ka": 2999}), answers=("2", True), draws=())  # :27115
    assert _last_wait(source) == GANG_WAR_SCREEN and source.key_waits() == 0

    _brawls(monkeypatch, CombatResult(winner=1, losses=(0, 0)))
    _, source = _duel(_jailed(), answers=("2", True), draws=(0,))  # :27140-27150
    assert _last_wait(source) == GANG_WAR_SCREEN and source.key_waits() == 0

    _brawls(monkeypatch, CombatResult(winner=2, losses=(0, 0)))
    _, source = _duel(_jailed(), answers=("2", True), draws=())  # :27146-27150
    assert _last_wait(source) == KEY_WAIT_SCREEN and source.key_waits() == 1

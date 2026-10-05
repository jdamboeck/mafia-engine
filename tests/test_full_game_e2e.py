"""End-to-end seeded runs of the whole game through the terminal client's ``play()``.

Two runs, each from a start state built here (not a stored fixture), saved and resumed
with ``play(load=...)`` at a turn's upkeep, with every house rule at its faithful
default:

1. **The early win** (AE4, ``mf-prg.bas:1011``): a rank-9 player one cash transport
   short of rank 10, holding no tip and no win flag, buys tips at the pub until tip 3
   or 5 comes up (``:12200-12252``), walks to the cell it arms and wins the flow there
   (the cash transport at 569, ``:23000-23030``; the mayor hit at 861,
   ``:24000-24020``), until both flags are held; the transport's score lifts the
   pending rank to 10, upkeep commits it (``:4030``), and the next turn start shows the
   victory picture and the ranking (``:40000``/``:40100``).
2. **The jail** (AE3): a rank-6 player walks the street until the roadblock stops them
   (``:2041``, ``:6000-6036``), surrenders (``:26030``), takes no lawyer and is
   sentenced (``:26045-26080``); every following turn shows upkeep, then the jail
   screen (``:1500-1515``) for exactly the sentence's months, and then a normal turn.

Neither run is a fixed key list. :class:`ScreenPlayer` is the piped stdin: every key
it gives is decided from the screen the client is showing at that moment (the
interaction the session is rendering, and the state that screen shows), the way a
person reads the screen before pressing a key. So an added RNG draw elsewhere changes
which screens come and in what order, not whether the script can answer them. Walks are
planned over the live city map, as ``make_walk_script`` walks the engine. A step budget
and a deadline make a broken feature fail the run instead of looping.
"""

from __future__ import annotations

import io
import sys
from collections import deque
from dataclasses import replace
from typing import Any

import pytest

import data.game_configs.mafia_1920s.state as game
from clients.terminal import CONFIG_DIR, TerminalSession, play
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.handlers.roadblock import ROADBLOCK_SCREEN
from data.game_configs.mafia_1920s.handlers.turn import JAIL_SCREEN, VICTORY_SCREEN
from engine.combat import GRID_COLS, STEPS, blocks_shot, can_move_onto
from engine.config_loader import load_game_config
from engine.interactions import (
    Acknowledge,
    CombatScreen,
    Confirm,
    Heading,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptChoice,
    PromptInt,
    TurnMenu,
)
from engine.movement import DOWN, LEFT, RIGHT, STREET_CODE, UP, City
from engine.persistence import save_game
from engine.state import FAITHFUL
from engine.strings import Resolver
from engine.turns import STANDINGS_SCREEN, UPKEEP, UPKEEP_SCREEN, YEAR_END_SCREEN
from tests.helpers import deadline, with_player, with_values

_CONFIG = load_game_config(CONFIG_DIR)  # registers the config's handlers and hooks
assert _CONFIG.city is not None, "the mafia_1920s config has a city map"
_CITY: City = _CONFIG.city
_TEXT = Resolver.from_config(CONFIG_DIR, theme="classic")
_PRESS_ENTER = _TEXT.resolve("client.location.press_enter")

#: The map keys the client reads (``session._MOVE_KEYS``) by the step they make.
_MAP_KEYS = {UP: "w", DOWN: "s", LEFT: "a", RIGHT: "d"}
#: The combat keys (``clients.terminal._COMBAT_MOVE_KEYS``) by the step they make.
_COMBAT_KEYS = {-GRID_COLS: "w", GRID_COLS: "s", -1: "a", 1: "d"}
#: The cell each win flow's tip arms (``content/map/city.yaml`` special_cells).
_FLOW_CELL = {3: 569, 5: 861}
_FLOW_FLAG = {3: "x5", 5: "x6"}
_PUB_DOORS = frozenset(
    cell for cell, (la, _ln) in _CITY.doors.items() if _CITY.location_keys.get(la) == "pub"
)


class OutOfSteps(AssertionError):
    """The run used up its key budget: the feature it drives never got there."""


# --------------------------------------------------------------------------- #
# The screen-reading stdin                                                     #
# --------------------------------------------------------------------------- #
class ScreenPlayer:
    """A piped stdin that answers whatever screen the client shows.

    The session's ``render`` is wrapped (:meth:`install`) so that before each screen is
    drawn the player sees the interaction and the session's state, as a person sees the
    screen. ``readline`` then decides the key from that screen: subclasses implement
    :meth:`decide`. Two screens read more than one line: a location with a picture
    waits for Enter before its menu (told apart by the text printed since the last
    key), and a combat shot is ``f`` then the aim key (queued).

    ``screens`` records the key of every Acknowledge/Heading in order, ``budget`` caps
    the keys read so a run that never reaches its goal fails with :class:`OutOfSteps`.
    """

    def __init__(self, out: io.StringIO, *, budget: int) -> None:
        self.out = out
        self.budget = budget
        self.keys_read = 0
        self.screen: Any = None
        self.state: Any = None
        self.screens: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        self.combat_activations = 0
        self._mark = 0
        self._queued: list[str] = []

    def install(self, monkeypatch) -> None:
        original = TerminalSession.render
        player = self

        def render(session, interaction):
            player.screen = interaction
            player.state = session.state
            if isinstance(interaction, (Acknowledge, Heading)):
                player.screens.append(interaction.key)
            player.observe(interaction, session.state)
            return original(session, interaction)

        monkeypatch.setattr(TerminalSession, "render", render)
        monkeypatch.setattr(sys, "stdin", self)
        monkeypatch.setattr(sys, "stdout", self.out)

    def observe(self, screen, state) -> None:
        """Called with every screen before it is drawn; a subclass may take notes."""

    # The stdin surface the client uses: not a terminal, so keys come by the line.
    def fileno(self) -> int:
        raise io.UnsupportedOperation("a piped stdin, not a terminal")

    def readline(self) -> str:
        self.keys_read += 1
        if self.keys_read > self.budget:
            raise OutOfSteps(f"no end after {self.budget} keys; last screen {self.screen!r}")
        printed = self.out.getvalue()[self._mark :]
        self._mark = len(self.out.getvalue())
        if self._queued:
            return self._queued.pop(0) + "\n"
        if isinstance(self.screen, LocationMenu) and _PRESS_ENTER in printed:
            return "\n"  # the location's picture waits for Enter, then the menu
        keys = self.decide(self.screen, self.state)
        if isinstance(self.screen, (Confirm, PromptChoice, PromptInt)):
            self.prompts.append((self.screen.key, keys if isinstance(keys, str) else keys[0]))
        if isinstance(keys, list):
            self._queued.extend(keys[1:])
            keys = keys[0]
        return keys + "\n"

    # Screens every run answers the same way.
    def decide(self, screen, state) -> str | list[str]:
        if isinstance(screen, CombatScreen):
            self.combat_activations += 1
            return combat_keys(screen)
        if isinstance(screen, (Acknowledge, Heading, OptionDone)):
            # any key that is not the quit key (OptionDone: :1100's wait after a result)
            return "x"
        raise AssertionError(f"no answer for {screen!r}")

    @staticmethod
    def player(state):
        return state.players[state.clock.active_player]


def combat_keys(screen: CombatScreen) -> str | list[str]:
    """One activation, read off the board: fire at a standing enemy in line and in
    range with no wall between; else step towards lining up with the nearest one."""
    me = screen.sides[screen.active_side - 1][screen.active_fighter - 1]
    enemies = [f for f in screen.sides[2 - screen.active_side] if not f.down]
    grid = tuple(screen.grid)
    reach = me.equipment.get("range", 2)
    row, col = divmod(me.position, GRID_COLS)
    for enemy in sorted(enemies, key=lambda f: abs(f.position - me.position)):
        erow, ecol = divmod(enemy.position, GRID_COLS)
        if erow == row:
            step, distance = (1 if ecol > col else -1), abs(ecol - col)
        elif ecol == col:
            step, distance = (GRID_COLS if erow > row else -GRID_COLS), abs(erow - row)
        else:
            continue
        between = [me.position + step * i for i in range(1, distance)]
        if distance <= reach and not any(blocks_shot(c, grid) for c in between):
            return ["f", _COMBAT_KEYS[step]]
    occupied = frozenset(
        f.position for side in screen.sides for f in side if not f.down and f is not me
    )

    def misalignment(cell: int) -> tuple[int, int]:
        r, c = divmod(cell, GRID_COLS)
        return min(
            (min(abs(r - er), abs(c - ec)), abs(r - er) + abs(c - ec))
            for er, ec in (divmod(f.position, GRID_COLS) for f in enemies)
        )

    steps = [s for s in STEPS if can_move_onto(me.position + s, grid, occupied)]
    if not steps or not enemies:
        return "p"
    return _COMBAT_KEYS[min(steps, key=lambda s: misalignment(me.position + s))]


def city_path(start: int, goal: int) -> list[int]:
    """The shortest street walk from ``start`` whose last step lands on ``goal`` (a door
    or an event cell is only ever the last step); the steps, in order."""
    prev: dict[int, tuple[int, int]] = {}
    queue = deque([start])
    seen = {start}
    while queue:
        cell = queue.popleft()
        if cell == goal:
            break
        for step in (UP, DOWN, LEFT, RIGHT):
            nxt = cell + step
            if not 0 <= nxt <= 999 or nxt in seen:
                continue
            street = _CITY.code(nxt) == STREET_CODE and nxt not in _CITY.special_cells
            if nxt == goal or street:
                seen.add(nxt)
                prev[nxt] = (cell, step)
                queue.append(nxt)
    if goal not in prev:
        raise AssertionError(f"no street walk from {start} to {goal}")
    steps: list[int] = []
    cell = goal
    while cell != start:
        cell, step = prev[cell]
        steps.append(step)
    return steps[::-1]


def _street_beside(cell: int) -> int:
    for step in (UP, DOWN, LEFT, RIGHT):
        if _CITY.code(cell + step) == STREET_CODE and cell + step not in _CITY.special_cells:
            return cell + step
    raise AssertionError(f"no street beside {cell}")


def _resume(monkeypatch, tmp_path, state, player: ScreenPlayer, *, seed: int):
    """Save ``state`` at its turn's upkeep and play it on with ``play(load=...)``."""
    state = replace(state, clock=replace(state.clock, turn_phase=UPKEEP))
    save = tmp_path / "start.jsonl"
    save_game(save, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=seed)
    player.install(monkeypatch)
    with deadline(60, "play() did not finish the run in 60s"):
        return play(load=str(save))


def _new_game(seed: int):
    state = _CONFIG.module.new_game(
        seed=seed, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    assert set(state.config.house_rules.values()) == {FAITHFUL}, "every house rule faithful"
    return state


def _gang(size: int, *, stat: int, energie: int, weapon: int) -> tuple:
    return tuple(
        Gangster(
            name=f"g{i}",
            weapon=weapon,
            energie=energie,
            kraft=stat,
            intelligenz=stat,
            brutalitaet=stat,
        )
        for i in range(size)
    )


# --------------------------------------------------------------------------- #
# Run 1 — tips, both win flows, rank 10, the early win (AE4)                   #
# --------------------------------------------------------------------------- #
class EarlyWinPlayer(ScreenPlayer):
    """Buys tips until one arms a flow whose flag is still missing, walks there, wins it.

    On the map: holding a wanted tip, walk to the cell it arms; otherwise walk into the
    nearest pub. A sentence can cost score (``:26080``), so with the flags held but the
    pending rank short of 10 the transport is won again for its score (from its door step, the same key re-enters). In the pub: the tip
    option; yes to the price; no to the arms deal's stake. Caught by the police (a
    passport can fade): pay the bribe. Every other screen: a key.
    """

    def __init__(self, out, *, budget: int) -> None:
        super().__init__(out, budget=budget)
        self.tips_bought: list[int] = []
        self._tip = 0
        self.flows_entered: list[int] = []

    def wanted_tip(self, state) -> int | None:
        """The held tip, if its flow is still worth winning: its flag is missing, or
        (the transport's score) the pending rank is short of 10."""
        p = self.player(state)
        tip = game.tip_target(p)
        if tip not in _FLOW_CELL:
            return None
        if not getattr(game.wanted(p), _FLOW_FLAG[tip]):
            return tip
        return tip if tip == 3 and game.next_rank(p) < 10 else None

    def decide(self, screen, state):
        if isinstance(screen, TurnMenu):
            p = self.player(state)
            flags = game.wanted(p)
            # :1011 runs before the menu: an eligible player never gets here.
            assert not (p.rank == 10 and flags.x5 and flags.x6), "the turn menu opened"
            return screen.keys[screen.options.index("walk")]
        if isinstance(screen, MapMove):
            po = self.player(state).po
            tip = self.wanted_tip(state)
            if tip is not None:
                goal = _FLOW_CELL[tip]
                if po + city_path(po, goal)[0] == goal:
                    self.flows_entered.append(tip)
                return _MAP_KEYS[city_path(po, goal)[0]]
            # The nearest pub, never across an armed event cell.
            path = min((city_path(po, door) for door in _PUB_DOORS), key=len)
            return _MAP_KEYS[path[0]]
        if isinstance(screen, LocationMenu):
            assert screen.location == "pub", f"walked into {screen.location}"
            # :3030/:3040 the options are numbered from 1.
            return str(screen.options.index("tip") + 1)
        if isinstance(screen, Confirm):
            if screen.key == "locations.pub.tip_confirm":
                return "j"
            if screen.key == "locations.pub.arms_deal_confirm":
                return "n"
            if screen.key == "police.confirm":
                return "j"
            if screen.key == "police.lawyer_offer":
                return "n"
        if isinstance(screen, PromptChoice) and screen.key == "police.menu":
            return str(screen.options.index("police.menu_bribe"))
        if isinstance(screen, PromptInt) and screen.key == "police.lawyer_prompt":
            return "0"
        return super().decide(screen, state)

    def observe(self, screen, state) -> None:
        tip = game.tip_target(self.player(state)) if state is not None else 0
        if tip and tip != self._tip:
            self.tips_bought.append(tip)
        self._tip = tip


def _early_win_start(seed: int):
    """Rank 9, score 92: the transport's two +4 make it 100, the pending rank 10.

    No tip, no win flag. A strong, well-armed gang of ten (the stat ceiling 99, the
    regen cap 2+99//4+99//4 = 50 energy, the machine pistol), cash for many tips, the
    fastest car, a passport against the roadblock, standing beside a pub door.
    """
    state = _new_game(seed)
    pub = min(_PUB_DOORS)
    state = with_player(
        state,
        rank=9,
        gf=92.0,
        ka=200_000,
        vehicle=4,
        po=_street_beside(pub),
        roster=_gang(10, stat=99, energie=50, weapon=7),
    )
    state = with_values(state, game.Contraband(fake_papers=1), nr=9)
    p = state.players[0]
    assert game.tip_target(p) == 0
    assert not game.wanted(p).x5 and not game.wanted(p).x6
    return state


@pytest.mark.parametrize("seed", [42])
def test_tips_both_win_flows_and_rank_10_end_the_game_early(monkeypatch, tmp_path, seed):
    out = io.StringIO()
    player = EarlyWinPlayer(out, budget=6000)
    final, _rng = _resume(monkeypatch, tmp_path, _early_win_start(seed), player, seed=seed)

    p = final.players[0]
    flags = game.wanted(p)
    assert flags.x5 and flags.x6, "both win flows were won"
    assert p.rank == 10, "upkeep committed rank 10"
    assert set(player.flows_entered) == {3, 5}, "both cells were reached on a held tip"
    assert {3, 5} <= set(player.tips_bought), "the tips came from the pub"
    # AE4: the victory picture at a turn start (right after its upkeep), then the
    # ranking, and the game is over: nothing after it, no turn menu.
    assert player.screens[-2:] == [VICTORY_SCREEN, YEAR_END_SCREEN]
    assert player.screens[-3] == UPKEEP_SCREEN
    assert not isinstance(player.screen, TurnMenu)
    output = out.getvalue()
    victory_at = output.rindex(_TEXT.resolve("turn.victory", {"name": "alcapone"}))
    winner = _TEXT.resolve("game_end.winner", {"name": "alcapone"}).splitlines()[0]
    assert output.index(winner, victory_at) > victory_at
    assert _TEXT.resolve("turn.menu.prompt") not in output[victory_at:]
    assert player.combat_activations > 0, "the flows were fought on the board"


# --------------------------------------------------------------------------- #
# Run 2 — roadblock, surrender, sentence, the jail turns, release (AE3)        #
# --------------------------------------------------------------------------- #
class JailPlayer(ScreenPlayer):
    """Walks one street back and forth until the police stop it, then gives up.

    Surrender at the arrest, no lawyer at the trial, a key on every other screen; at
    the first turn menu after a jail screen (the release), it quits.
    """

    def __init__(self, out, *, budget: int, home: int) -> None:
        super().__init__(out, budget=budget)
        self.home = home
        self.away = _street_beside(home)
        self.menus_after_jail: list[Any] = []
        self.energy_at_jail: list[int] = []

    def observe(self, screen, state) -> None:
        if isinstance(screen, Acknowledge) and screen.key == JAIL_SCREEN:
            self.energy_at_jail.append(self.player(state).roster[0].vitality)

    def decide(self, screen, state):
        if isinstance(screen, TurnMenu):
            if JAIL_SCREEN in self.screens:
                self.menus_after_jail.append(screen)
                return "q"
            return screen.keys[screen.options.index("walk")]
        if isinstance(screen, MapMove):
            po = self.player(state).po
            goal = self.away if po == self.home else self.home
            return _MAP_KEYS[goal - po]
        if isinstance(screen, PromptChoice) and screen.key == "police.menu":
            return str(screen.options.index("police.menu_surrender"))
        if isinstance(screen, Confirm) and screen.key == "police.lawyer_offer":
            return "n"
        return super().decide(screen, state)


def _jail_start(seed: int):
    """Rank 6 (a sentence of int(6/2+.5) = 3 months, and a lawyer offered), no
    passport and no contraband, on foot. The boss regenerates one energy a turn
    (int(9/10)+1) up to 2+9//4+99//4 = 28, from 1: upkeep's regen shows on every turn."""
    state = _new_game(seed)
    home = _street_beside(min(_PUB_DOORS))
    state = with_player(
        state,
        rank=6,
        gf=60.0,
        po=home,
        roster=(Gangster(name="boss", energie=1, kraft=9, intelligenz=30, brutalitaet=99),),
    )
    return with_values(state, nr=6), home


@pytest.mark.parametrize("seed", [42])
def test_a_roadblock_capture_jails_for_the_sentence_then_a_normal_turn(monkeypatch, tmp_path, seed):
    out = io.StringIO()
    start, home = _jail_start(seed)
    player = JailPlayer(out, budget=3000, home=home)
    final, _rng = _resume(monkeypatch, tmp_path, start, player, seed=seed)

    screens = player.screens
    assert ROADBLOCK_SCREEN in screens
    output = out.getvalue()
    caught_at = output.index(_TEXT.resolve("roadblock.wanted_poster"))
    assert _TEXT.resolve("police.sentenced", {"months": 3}) in output[caught_at:]
    assert player.prompts == [("police.menu", "2"), ("police.lawyer_offer", "n")]
    # Exactly the sentence: three jail screens, months 3, 2, 1, each after its turn's
    # upkeep (and the round's standings), then a normal turn.
    jail_texts = [_TEXT.resolve("turn.jail", {"months": m}) for m in (3, 2, 1)]
    shown = [text for text in jail_texts if text in output[caught_at:]]
    assert shown == jail_texts, "a jail screen for each month of the sentence"
    at = [output.index(text, caught_at) for text in jail_texts]
    assert at == sorted(at)
    assert screens.count(JAIL_SCREEN) == 3
    jailed = [i for i, key in enumerate(screens) if key == JAIL_SCREEN]
    for i in jailed:
        assert screens[i - 1] == UPKEEP_SCREEN and screens[i - 2] == UPKEEP_SCREEN
        assert STANDINGS_SCREEN in screens[i - 4 : i - 2]
    assert len(player.menus_after_jail) == 1, "the turn after the sentence is a free turn"
    p = final.players[0]
    assert game.wanted(p).jail_months == 0
    assert p.po == _CONFIG.config["formula_params"]["police_jail_cell"]
    # Upkeep ran on every jail turn, before its jail screen: the boss's energy
    # regenerated by one each time.
    first = player.energy_at_jail[0]
    assert player.energy_at_jail == [first, first + 1, first + 2]
    assert p.roster[0].vitality == first + 3, "and on the turn after the release"

"""The engine turn runner: the order of a turn, with the config's rules at fixed hooks.

The engine owns the order of a turn; the config owns every rule in it
(docs/design/engine-architecture.md § "The turn runner"). :class:`TurnRunner` follows
the reference title's turn head, ``mf-prg.bas:1010-1013``::

    1010 sp=sp+1:ifsp=sz+1thensp=1:gosub4500:ja=ja+1/12:ifint(ja)=x9goto40100
    1011 gosub4000:ifra(sp)=10andx5%(sp)>0andx6%(sp)>0thensyslh,"sieg-pic":goto40000
    1012 ms=tr(tm(sp)):nr(sp)=ra(sp):ll(sp)=0:ifjo(sp)thengosub25000:goto1010
    1013 gf(sp)=int(gf(sp)*100)/100:ifgs(sp)thengosub1500:goto1010

1. ``:1010`` — next player (:class:`~engine.effects.AdvanceTurn`); on a wrap, the
   round standings of the round just played, then the year-end check (``int(ja)`` has
   reached the end year: the year-end result, and the game ends).
2. ``:1011`` — upkeep (:data:`~engine.upkeep.UPKEEP_HANDLER_KEY`) and its screen, then
   the config's turn-start check (:data:`EARLY_WIN_HOOK_KEY`).
3. ``:1012`` — movement points (:data:`MOVEMENT_POINTS_HOOK_KEY` returns the value,
   the runner writes it), the previous tile cleared, and the job skip
   (:data:`JOB_HOOK_KEY`, then the shift under :data:`JOB_SHIFT_HANDLER_KEY`).
4. ``:1013`` — score truncation (:data:`SCORE_TRUNCATION_HOOK_KEY`) and the jail skip
   (:data:`JAIL_HOOK_KEY`).
5. The free turn: the map step (below), then the turn-over screen, then 1 again.

**The map step** (``:2000-2065``)::

    2035 sysie:ifpeek(p)<>156goto2045
    2040 po(sp)=po(sp)+x:ms=ms-1
    2041 ifms/20=int(ms/20)andint(rnd(1)*5)=0andra(sp)>3thengosub6000:goto2060
    2045 ifpo(sp)+x=569thenla=13:ln=1:gosub23000:goto2060
    2046 ifpo(sp)+x=861thenla=14:ln=1:gosub24000:goto2060
    2050 syslc,p:la=peek(ua+1):ln=peek(ua+2):ifla=0goto2005
    2055 gosub3000:ll(sp)=20*la+ln
    2060 ms=ms-5:ifms>0goto2000

The runner asks for each step with :class:`~engine.interactions.MapMove` and moves
with :func:`engine.movement.try_move`. A move onto an event cell first asks the
config's special-cell hook (:data:`SPECIAL_CELL_HOOK_KEY`) whether the cell is armed;
a street step asks its roadblock hook (:data:`ROADBLOCK_HOOK_KEY`); a door runs the location visit: the shell's
:class:`~engine.interactions.LocationMenu`, the chosen option
(:func:`engine.actions.step_option`) and its :class:`~engine.interactions.OptionDone`,
then the previous tile. A door to a location with no shell shows
:data:`LOCATION_CLOSED_SCREEN`. The shells and the city are the loaded config's
(:class:`~engine.config_loader.LoadedConfig`), passed in.

The free turn ends when movement points run out, with no menu (``:2042``-``:2005``,
``:2060``). After each hook and each location visit the runner re-reads movement
points from state instead of trusting what the move left, since a handler can raise
them (the car purchase, ``:14050``) or zero them (a sentence). After a visit the turn
ends when the entry left none and the handler gave none back; a handler that zeroes
the points it found leaves one more map prompt, whose next step ends the turn, as the
terminal client always did.

Every rule is a config handler registered under a fixed key in
:data:`engine.locations.HANDLERS`, the way ``upkeep.turn_start`` is. The runner only
calls them, in order.

**A generator.** :meth:`TurnRunner.run` yields interactions to its driver, like a
handler does, so a transport can hold one runner per session. It runs each hook and
handler with ``yield from`` :func:`engine.interactions.step`, so their prompts,
sub-states and fights reach the driver through the same stream, and it yields its
own screens and prompts: :class:`~engine.interactions.Acknowledge` for the upkeep,
turn-over, standings and year-end screens, :class:`~engine.interactions.Heading` where
the job shift opens its screen and for a closed location, and the map step's
:class:`~engine.interactions.MapMove`, :class:`~engine.interactions.LocationMenu` and
:class:`~engine.interactions.OptionDone`. Each screen of its own names the active
player as its ``player``, as ``step`` does for the hooks' and handlers' interactions.

**One commit per step.** Each hook and handler commits on its own (``step`` folds its
buffer when it completes), each map move commits, and the runner's own writes — the
rotation, the movement points, the previous tile, the turn phase — are generic engine
effects committed where they happen.
:attr:`TurnRunner.state` is always the last committed state. A hook that raises
commits nothing, leaves the phase as it was, and its exception propagates: the same
policy as a location handler (a bug keeps its traceback).

**The turn phase.** The runner records where it re-enters a turn in
``clock.turn_phase`` (:data:`PHASES`): :data:`UPKEEP` with the rotation, and
:data:`WALKING` when the free turn opens. :meth:`TurnRunner.run` with no ``entry``
re-enters the recorded phase, so a game saved on the map resumes on the map without
re-running upkeep or the turn start.

``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

import functools
from collections.abc import Generator, Mapping
from typing import TYPE_CHECKING, Any

from engine.actions import step_option
from engine.effects import (
    AdvanceTurn,
    SetMovementPoints,
    SetPreviousTile,
    SetTurnPhase,
    commit,
)
from engine.game_end import (
    STANDINGS_HANDLER_KEY,
    YEAR_END_HANDLER_KEY,
    run_standings,
    run_year_end,
)
from engine.interactions import (
    MAP_QUIT,
    Acknowledge,
    Heading,
    LocationMenu,
    MapMove,
    OptionDone,
    ShowMessage,
    step,
)
from engine.locations import available_options
from engine.movement import DIRECTION_DELTAS, try_move
from engine.upkeep import UPKEEP_HANDLER_KEY

if TYPE_CHECKING:  # typing only
    from engine.locations import Location
    from engine.movement import City
    from engine.state import GameState

__all__ = [
    # Phases
    "NEXT_PLAYER",
    "UPKEEP",
    "TURN_START",
    "WALKING",
    "TURN_OVER",
    "PHASES",
    # Hook keys
    "EARLY_WIN_HOOK_KEY",
    "MOVEMENT_POINTS_HOOK_KEY",
    "JOB_HOOK_KEY",
    "JOB_SHIFT_HANDLER_KEY",
    "SCORE_TRUNCATION_HOOK_KEY",
    "JAIL_HOOK_KEY",
    "ROADBLOCK_HOOK_KEY",
    "SPECIAL_CELL_HOOK_KEY",
    # Screens
    "UPKEEP_SCREEN",
    "TURN_OVER_SCREEN",
    "STANDINGS_SCREEN",
    "YEAR_END_SCREEN",
    "JOB_SHIFT_SCREEN",
    "LOCATION_CLOSED_SCREEN",
    # Outcomes
    "GAME_OVER",
    "PAUSED",
    "QUIT",
    # The runner
    "TurnRunner",
]

# --------------------------------------------------------------------------- #
# Phases: where the runner (re-)enters a turn                                 #
# --------------------------------------------------------------------------- #
#: ``:1010`` — rotate to the next player (standings and year-end check on a wrap).
NEXT_PLAYER = "next_player"
#: ``:1011`` — the active player's turn start is due, beginning with upkeep.
UPKEEP = "upkeep"
#: ``:1011`` after upkeep — the early-win check, ``:1012`` and ``:1013``.
TURN_START = "turn_start"
#: The free turn is open (the map step).
WALKING = "walking"
#: The turn is played; the turn-over screen is next.
TURN_OVER = "turn_over"

#: Every phase, in turn order. ``clock.turn_phase`` holds one; the runner writes
#: :data:`UPKEEP` and :data:`WALKING`.
PHASES = (NEXT_PLAYER, UPKEEP, TURN_START, WALKING, TURN_OVER)

# --------------------------------------------------------------------------- #
# Hook keys: the config's rules, registered in engine.locations.HANDLERS       #
# --------------------------------------------------------------------------- #
#: ``:1011`` the turn-start check: returns truthy when the active player has won the
#: game early (the config narrates it); the game then ends.
EARLY_WIN_HOOK_KEY = "turn.early_win"
#: ``:1012`` returns the active player's movement points for this turn.
MOVEMENT_POINTS_HOOK_KEY = "turn.movement_points"
#: ``:1012`` returns truthy when the active player works a job shift this turn instead
#: of the free turn.
JOB_HOOK_KEY = "turn.job"
#: The job shift itself, run when :data:`JOB_HOOK_KEY` says so.
JOB_SHIFT_HANDLER_KEY = "job.shift"
#: ``:1013`` the score rule at the start of a free turn (applies its own effects).
SCORE_TRUNCATION_HOOK_KEY = "turn.score_truncation"
#: ``:1013`` returns truthy when the active player spends this turn in jail (the
#: config shows the jail screen and counts the sentence down).
JAIL_HOOK_KEY = "turn.jail"
#: ``:2041`` the roadblock, asked after every street step (the config decides whether
#: the police stop the player, and runs the stop). Its return value is ignored; the
#: runner re-reads movement points after it.
ROADBLOCK_HOOK_KEY = "turn.roadblock"
#: ``:2045``/``:2046`` an event cell (``la`` 13/14): asked before every move onto one,
#: with the keyword arguments ``cell`` and ``la``. It returns truthy when the cell is
#: armed for the active player and it ran the cell's flow (the player does not step);
#: falsy, and the move goes on as usual (an unarmed cell is a plain street). The
#: runner re-reads movement points after an armed cell.
SPECIAL_CELL_HOOK_KEY = "turn.special_cell"

# --------------------------------------------------------------------------- #
# Screens the runner yields itself                                             #
# --------------------------------------------------------------------------- #
#: Acknowledge: the upkeep screen. ``params``: ``previous_rank`` (the rank before upkeep).
UPKEEP_SCREEN = "turn.upkeep"
#: Acknowledge: the turn-over summary of the player whose turn just ended.
TURN_OVER_SCREEN = "turn.turn_over"
#: Acknowledge: the round standings. ``params``: ``lines``, ``(key, params)`` pairs.
STANDINGS_SCREEN = "turn.standings"
#: Acknowledge: the year-end result. ``params``: ``lines``, ``(key, params)`` pairs.
YEAR_END_SCREEN = "turn.year_end"
#: Heading: the job shift's screen opens.
JOB_SHIFT_SCREEN = "turn.job_shift"
#: Heading: a door opened onto a location this config has no shell for.
#: ``params``: ``location`` (its key).
LOCATION_CLOSED_SCREEN = "turn.location_closed"

#: :meth:`TurnRunner.run`'s return value when the game ended (year end or early win).
GAME_OVER = "game_over"
#: :meth:`TurnRunner.run`'s return value when it reached its ``until`` phase.
PAUSED = "paused"
#: :meth:`TurnRunner.run`'s return value when the map-move prompt was answered
#: :data:`~engine.interactions.MAP_QUIT`.
QUIT = "quit"


class TurnRunner:
    """Runs turns over ``state``: the order is the engine's, the rules are the config's.

    ``handlers`` is the registry the hooks are looked up in (default: the live
    :data:`engine.locations.HANDLERS`, filled when the config loads). ``rng`` is the
    session RNG, shared by every hook and handler. ``city`` and ``shells`` are the
    loaded config's map and location shells
    (:attr:`~engine.config_loader.LoadedConfig.city`,
    :attr:`~engine.config_loader.LoadedConfig.shells`); the map step needs them.
    ``observe_ai`` opts in to the fight observation frames
    (:func:`engine.interactions.step`).

    :attr:`state` is the last committed state; a driver adopts it whenever it needs
    one (a quit, a save, a render).
    """

    def __init__(
        self,
        state: GameState,
        rng: Any = None,
        *,
        handlers: dict[str, Any] | None = None,
        city: City | None = None,
        shells: Mapping[str, Location] | None = None,
        observe_ai: bool = False,
    ) -> None:
        self.state = state
        self.rng = rng
        if handlers is None:
            from engine.locations import HANDLERS as handlers  # noqa: N811 - local alias
        self._handlers = handlers
        self._city = city
        self._shells: Mapping[str, Location] = shells if shells is not None else {}
        self._observe_ai = observe_ai

    # ------------------------------------------------------------------ #
    # The order                                                           #
    # ------------------------------------------------------------------ #
    def run(
        self, entry: str | None = None, *, until: str | None = None
    ) -> Generator[Any, Any, str]:
        """Run turns from ``entry`` until the game ends or ``until`` is reached.

        ``entry`` is a phase in :data:`PHASES`; ``None`` re-enters the phase recorded in
        ``clock.turn_phase`` (a resumed save). A new game enters at :data:`UPKEEP`.
        ``until`` stops the run just before that phase begins. Returns
        :data:`GAME_OVER`, :data:`PAUSED`, or :data:`QUIT` when the map-move prompt was
        answered with the quit command. A driver that quits may also simply stop
        driving.
        """
        phase = entry if entry is not None else self.state.clock.turn_phase
        for name in (phase, until):
            if name is not None and name not in PHASES:
                raise ValueError(f"unknown turn phase {name!r}; phases: {PHASES}")
        while True:
            if phase == until:
                return PAUSED
            if phase == NEXT_PLAYER:
                if (yield from self._next_player()):
                    return GAME_OVER
                phase = UPKEEP
            elif phase == UPKEEP:
                yield from self._upkeep()
                phase = TURN_START
            elif phase == TURN_START:
                phase = yield from self._turn_start()
                if phase == GAME_OVER:
                    return GAME_OVER
            elif phase == WALKING:
                if (yield from self._walk()) == QUIT:
                    return QUIT
                phase = TURN_OVER
            else:  # TURN_OVER
                yield Acknowledge(TURN_OVER_SCREEN, player=self.state.clock.active_player)
                phase = NEXT_PLAYER

    def _next_player(self) -> Generator[Any, Any, bool]:
        """``:1010``: rotate; on a wrap the standings, then the year-end check."""
        played = self.state
        self._commit(AdvanceTurn(), SetTurnPhase(UPKEEP))
        clock = self.state.clock
        if clock.active_player != 0:
            return False
        # gosub4500 runs BEFORE ja=ja+1/12: the standings show the round just played.
        lines = self._display(run_standings, STANDINGS_HANDLER_KEY, played)
        yield Acknowledge(STANDINGS_SCREEN, {"lines": lines}, player=clock.active_player)
        # ifint(ja)=x9goto40100: the year-end result, on the advanced state.
        if int(clock.year) < clock.end_year:
            return False
        lines = self._display(run_year_end, YEAR_END_HANDLER_KEY, self.state)
        yield Acknowledge(YEAR_END_SCREEN, {"lines": lines}, player=clock.active_player)
        return True

    def _upkeep(self) -> Generator[Any, Any, None]:
        """``:1011`` gosub4000: upkeep, then its screen."""
        idx = self.state.clock.active_player
        previous_rank = self.state.players[idx].rank
        yield from self._hook(UPKEEP_HANDLER_KEY)
        yield Acknowledge(UPKEEP_SCREEN, {"previous_rank": previous_rank}, player=idx)

    def _turn_start(self) -> Generator[Any, Any, str]:
        """``:1011``'s early-win check, ``:1012`` and ``:1013``; returns the next phase."""
        if (yield from self._hook(EARLY_WIN_HOOK_KEY)):
            return GAME_OVER
        movement_points = yield from self._hook(MOVEMENT_POINTS_HOOK_KEY)
        # :1012 ms=tr(tm(sp)) ... ll(sp)=0
        self._commit(SetMovementPoints(movement_points), SetPreviousTile())
        if (yield from self._hook(JOB_HOOK_KEY)):
            yield Heading(JOB_SHIFT_SCREEN, player=self.state.clock.active_player)
            yield from self._hook(JOB_SHIFT_HANDLER_KEY)
            return TURN_OVER
        yield from self._hook(SCORE_TRUNCATION_HOOK_KEY)
        if (yield from self._hook(JAIL_HOOK_KEY)):
            return TURN_OVER
        self._commit(SetTurnPhase(WALKING))
        return WALKING

    # ------------------------------------------------------------------ #
    # The map step                                                        #
    # ------------------------------------------------------------------ #
    def _walk(self) -> Generator[Any, Any, str | None]:
        """The free turn on the map, ``:2000-2065``; returns :data:`QUIT` on a quit."""
        if self._city is None:
            raise ValueError(
                "the free turn needs the config's city map: pass TurnRunner(city=...) "
                "(LoadedConfig.city)"
            )
        city = self._city
        outcome: str | None = None
        while True:
            answer = yield MapMove(outcome=outcome, player=self.state.clock.active_player)
            if answer == MAP_QUIT:
                return QUIT
            delta = DIRECTION_DELTAS.get(answer) if isinstance(answer, str) else None
            if delta is None:
                # Saving is the driver's (it saves the committed state); a save or an
                # unknown answer asks again with nothing moved.
                outcome = None
                continue
            if self._movement_points() <= 0:  # :2005 ifms<=0thenreturn
                return None
            target = self.state.players[self.state.clock.active_player].po + delta
            if target in city.special_cells:  # :2045/2046 an event cell
                # The config says whether it is armed (the source pokes an armed cell
                # off the street code, :2002/:2003) and runs its flow if so.
                armed = yield from self._hook(
                    SPECIAL_CELL_HOOK_KEY, cell=target, la=city.special_cells[target]
                )
                if armed:
                    outcome = "special"
                    if self._movement_points() <= 0:
                        return None
                    continue
            result = try_move(self.state, city, delta)
            self.state = result.state
            move = result.payload
            outcome = move.kind
            if move.kind == "step":  # :2041 the roadblock
                yield from self._hook(ROADBLOCK_HOOK_KEY)
                over = self._movement_points() <= 0
            elif move.kind == "enter":  # :2050-2060 a door
                assert move.la is not None and move.ln is not None
                yield from self._visit(move.la, move.ln)
                # Re-read: the handler may have given points back (the car purchase).
                over = move.turn_over and self._movement_points() <= 0
            else:  # a wall, the edge, an unarmed event cell off the street: nothing moved
                over = False
            if over:
                return None

    def _visit(self, la: int, ln: int) -> Generator[Any, Any, None]:
        """``:2055`` ``gosub3000:ll(sp)=20*la+ln``: the location, then the previous tile."""
        city = self._city
        assert city is not None
        key = city.location_keys.get(la)
        shell = self._shells.get(key) if key is not None else None
        if key is None:
            pass  # a door to no named location: nothing inside
        elif shell is None:
            yield Heading(
                LOCATION_CLOSED_SCREEN, {"location": key}, player=self.state.clock.active_player
            )
        else:
            yield from self._location_menu(shell, ln)
        self._commit(SetPreviousTile(la=la, ln=ln))

    def _location_menu(self, shell: Location, ln: int) -> Generator[Any, Any, None]:
        """The shell's menu; the chosen option runs, anything else leaves."""
        options = available_options(shell, self.state, ln)
        player = self.state.clock.active_player
        answer = yield LocationMenu(
            location=shell.key, options=tuple(o.id for o in options), ln=ln, player=player
        )
        if isinstance(answer, bool) or not isinstance(answer, int):
            return
        if not 0 <= answer < len(options):
            return
        chosen = options[answer]
        if chosen.id == "leave":
            return
        result = yield from step_option(
            shell, chosen.id, self.state, ln=ln, rng=self.rng, observe_ai=self._observe_ai
        )
        self.state = result.state
        yield OptionDone(location=shell.key, option=chosen.id, status=result.status, player=player)

    def _movement_points(self) -> int:
        return self.state.players[self.state.clock.active_player].ms

    # ------------------------------------------------------------------ #
    # Mechanics                                                           #
    # ------------------------------------------------------------------ #
    def _hook(self, key: str, **params: Any) -> Generator[Any, Any, Any]:
        """Run the handler registered under ``key``; commit it; return what it returned.

        ``params`` are passed to the handler as keyword arguments after its ``ctx``.
        Its interactions are yielded to the driver (:func:`engine.interactions.step`).
        If it raises, nothing it buffered commits and :attr:`state` is unchanged.
        """
        factory = self._handlers.get(key)
        if factory is None:
            raise KeyError(
                f"no handler registered under {key!r}; a game config must register every "
                "turn hook the engine turn runner calls (even a no-op one)"
            )
        handler = functools.partial(factory, **params) if params else factory
        result = yield from step(handler, self.state, self.rng, observe_ai=self._observe_ai)
        self.state = result.state
        return result.payload.returned

    def _commit(self, *effects: Any) -> None:
        """Commit the runner's own writes (generic engine effects) as one step."""
        self.state = commit(self.state, list(effects)).state

    def _display(self, runner: Any, key: str, state: GameState) -> list[tuple[str, dict]]:
        """Run a display-only flow over ``state``; return its rows as ``(key, params)``."""
        lines: list[tuple[str, dict]] = []

        def collect(interaction: Any) -> None:
            if not isinstance(interaction, ShowMessage):
                raise AssertionError(f"the display-only {key!r} asked a question: {interaction!r}")
            lines.append((interaction.key, dict(interaction.params)))

        runner(state, input_source=collect, rng=self.rng, handlers=self._handlers)
        return lines

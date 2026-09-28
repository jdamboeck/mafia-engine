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
   the runner writes it) and the job skip (:data:`JOB_HOOK_KEY`, then the shift under
   :data:`JOB_SHIFT_HANDLER_KEY`).
4. ``:1013`` — score truncation (:data:`SCORE_TRUNCATION_HOOK_KEY`) and the jail skip
   (:data:`JAIL_HOOK_KEY`).
5. The free turn (:class:`FreeTurn`), then the turn-over screen, then 1 again.

Every rule is a config handler registered under a fixed key in
:data:`engine.locations.HANDLERS`, the way ``upkeep.turn_start`` is. The runner only
calls them, in order.

**A generator.** :meth:`TurnRunner.run` yields interactions to its driver, like a
handler does, so a transport can hold one runner per session. It runs each hook and
handler with ``yield from`` :func:`engine.interactions.step`, so their prompts,
sub-states and fights reach the driver through the same stream, and it yields its
own screens: :class:`~engine.interactions.Acknowledge` for the upkeep, turn-over,
standings and year-end screens, :class:`~engine.interactions.Heading` where the job
shift opens its screen.

**One commit per step.** Each hook and handler commits on its own (``step`` folds its
buffer when it completes), and the runner's own writes — the rotation, the movement
points, the turn phase — are generic engine effects committed where they happen.
:attr:`TurnRunner.state` is always the last committed state. A hook that raises
commits nothing, leaves the phase as it was, and its exception propagates: the same
policy as a location handler (a bug keeps its traceback).

**The turn phase.** The runner records where it re-enters a turn in
``clock.turn_phase`` (:data:`PHASES`): :data:`UPKEEP` with the rotation, and
:data:`WALKING` when the free turn opens. :meth:`TurnRunner.run` with no ``entry``
re-enters the recorded phase, so a game saved on the map resumes on the map without
re-running upkeep or the turn start.

**The map-step seam.** The free turn — the map walk and the location visits
(``:2000-2065``) — is not the runner's yet: the runner yields :class:`FreeTurn` and
the driver plays the map with the engine's movement and location calls, sending back
the state it ends with. Lifting the map step into the runner replaces that one yield.

``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from engine.effects import AdvanceTurn, SetMovementPoints, SetTurnPhase, commit
from engine.game_end import (
    STANDINGS_HANDLER_KEY,
    YEAR_END_HANDLER_KEY,
    run_standings,
    run_year_end,
)
from engine.interactions import Acknowledge, Heading, ShowMessage, step
from engine.upkeep import UPKEEP_HANDLER_KEY

if TYPE_CHECKING:  # typing only
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
    # Screens
    "UPKEEP_SCREEN",
    "TURN_OVER_SCREEN",
    "STANDINGS_SCREEN",
    "YEAR_END_SCREEN",
    "JOB_SHIFT_SCREEN",
    # Outcomes
    "GAME_OVER",
    "PAUSED",
    # The runner
    "FreeTurn",
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
#: The free turn is open (the map).
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

#: :meth:`TurnRunner.run`'s return value when the game ended (year end or early win).
GAME_OVER = "game_over"
#: :meth:`TurnRunner.run`'s return value when it reached its ``until`` phase.
PAUSED = "paused"


@dataclass(frozen=True)
class FreeTurn:
    """The runner hands the free turn to its driver (the map-step seam).

    The driver plays the map — moves, door entries, location visits — through the
    engine's movement and location calls, and sends back the :class:`GameState` the
    free turn ended with. ``player`` is the active player. This interaction goes away
    when the map step moves into the runner.
    """

    player: int


class TurnRunner:
    """Runs turns over ``state``: the order is the engine's, the rules are the config's.

    ``handlers`` is the registry the hooks are looked up in (default: the live
    :data:`engine.locations.HANDLERS`, filled when the config loads). ``rng`` is the
    session RNG, shared by every hook and handler. ``observe_ai`` opts in to the fight
    observation frames (:func:`engine.interactions.step`).

    :attr:`state` is the last committed state; a driver adopts it whenever it needs
    one (a quit, a save, a render).
    """

    def __init__(
        self,
        state: GameState,
        rng: Any = None,
        *,
        handlers: dict[str, Any] | None = None,
        observe_ai: bool = False,
    ) -> None:
        self.state = state
        self.rng = rng
        if handlers is None:
            from engine.locations import HANDLERS as handlers  # noqa: N811 - local alias
        self._handlers = handlers
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
        :data:`GAME_OVER` or :data:`PAUSED`. A driver that quits simply stops driving.
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
                yield from self._free_turn()
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
        yield Acknowledge(STANDINGS_SCREEN, {"lines": lines})
        # ifint(ja)=x9goto40100: the year-end result, on the advanced state.
        if int(clock.year) < clock.end_year:
            return False
        lines = self._display(run_year_end, YEAR_END_HANDLER_KEY, self.state)
        yield Acknowledge(YEAR_END_SCREEN, {"lines": lines})
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
        self._commit(SetMovementPoints(movement_points))
        if (yield from self._hook(JOB_HOOK_KEY)):
            yield Heading(JOB_SHIFT_SCREEN)
            yield from self._hook(JOB_SHIFT_HANDLER_KEY)
            return TURN_OVER
        yield from self._hook(SCORE_TRUNCATION_HOOK_KEY)
        if (yield from self._hook(JAIL_HOOK_KEY)):
            return TURN_OVER
        self._commit(SetTurnPhase(WALKING))
        return WALKING

    def _free_turn(self) -> Generator[Any, Any, None]:
        """The free turn: handed to the driver until the map step is the runner's."""
        from engine.state import GameState

        ended = yield FreeTurn(player=self.state.clock.active_player)
        if not isinstance(ended, GameState):
            raise TypeError(
                f"a FreeTurn is answered with the GameState the free turn ended with, not {ended!r}"
            )
        self.state = ended

    # ------------------------------------------------------------------ #
    # Mechanics                                                           #
    # ------------------------------------------------------------------ #
    def _hook(self, key: str) -> Generator[Any, Any, Any]:
        """Run the handler registered under ``key``; commit it; return what it returned.

        Its interactions are yielded to the driver (:func:`engine.interactions.step`).
        If it raises, nothing it buffered commits and :attr:`state` is unchanged.
        """
        factory = self._handlers.get(key)
        if factory is None:
            raise KeyError(
                f"no handler registered under {key!r}; a game config must register every "
                "turn hook the engine turn runner calls (even a no-op one)"
            )
        result = yield from step(factory, self.state, self.rng, observe_ai=self._observe_ai)
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

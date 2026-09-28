"""The interaction protocol + in-process synchronous driver — THE SPINE (docs/design/engine-architecture.md).

A **handler** is a factory ``(ctx) -> Generator[Interaction, Response, list[Event]]``.
It ``yield``s a typed **Interaction**; the **driver** turns that into an obtained
**Response** and ``.send()``s it back. The driver has two forms: :func:`step`, a
generator that yields every interaction (a sub-state's and a fight's included) to
whoever drives it, and :func:`run`, a pull loop over ``step`` that asks an input
callback for each answer. This same request/response protocol is,
unchanged, the eventual network message protocol — the WebSocket server (docs/design/engine-architecture.md) is
merely an async transport driving the identical generators. This module holds the
interaction catalog, the response/cancel sentinels, :class:`Ctx`, and the in-process
synchronous driver (:func:`step`/:func:`run`, with the :class:`LoadSubState` sub-state loop). A
yielded :class:`StartCombat` is delegated to :func:`engine.fight_loop._run_combat`,
which holds the fight loop. It imports nothing from ``server``/``clients``/transport.

Two firmly separated categories (docs/design/engine-architecture.md):

- **Interactions** control *execution flow* — they suspend the handler to ask the client
  something. A handler reaches the client *only* by ``yield``ing one of these.
- **Effects** mutate *game state*. A handler never mutates state directly; it calls
  ``ctx.apply(effect)``, which buffers the effect. The driver treats an effect as an
  opaque object; applying it to state is :func:`engine.effects.commit`'s job. The driver
  commits the buffer atomically: on clean return the buffered effects are the committed
  list, on cancel the buffer is discarded (committed effects == ``[]``).

Cancellation: the driver calls ``gen.throw(Cancelled())`` INTO
the handler when the input source supplies the :data:`CANCEL` sentinel at a
``cancellable`` prompt. The handler unwinds through its ``try/finally`` (it does not need
to catch ``Cancelled``); the driver catches ``Cancelled`` as expected control flow and
discards the effect buffer.

Who answers: every interaction carries ``player``, the index of the player who answers
it. ``None`` means the active player, and :func:`step` fills a ``None`` in with the
active player (``state.clock.active_player``) on everything it yields, so what reaches a
driver always names its player. An interaction answered by someone else — the freed
player at ``pol``, a defender's side in a gang war — names that player explicitly, and a
client announces the change of player before the prompt.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, TypeAlias, overload

from engine.actions import EngineResult, HandlerResult

if TYPE_CHECKING:  # typing only: keep engine.state out of this module's import graph
    from engine.state import GameState

__all__ = [
    # Interactions
    "ShowMessage",
    "PromptInt",
    "PromptChoice",
    "Confirm",
    "StartCombat",
    "CombatScreen",
    "OBSERVE_PROMPT",
    "LoadSubState",
    "Acknowledge",
    "Heading",
    "MapMove",
    "MAP_DIRECTIONS",
    "MAP_SAVE",
    "MAP_QUIT",
    "MAP_EXIT",
    "TurnMenu",
    "LocationMenu",
    "OptionDone",
    "Interaction",
    # Response / control
    "Ack",
    "CANCEL",
    "Cancelled",
    # Driver
    "Ctx",
    "step",
    "run",
]


# --------------------------------------------------------------------------- #
# Interaction catalog (docs/design/engine-architecture.md)                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ShowMessage:
    """Display-only interaction. The Response is an :data:`Ack` (no value)."""

    key: str
    params: dict = field(default_factory=dict)
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


@dataclass(frozen=True)
class PromptInt:
    """Ask for an int in ``[min, max]`` inclusive.

    The DRIVER enforces numeric + range validation and re-prompts; the handler
    only ever receives a valid ``int`` (or is cancelled via ``gen.throw`` when
    ``cancellable`` and the input source supplies :data:`CANCEL`).
    """

    key: str
    min: int
    max: int
    cancellable: bool = False
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


@dataclass(frozen=True)
class PromptChoice:
    """Menu / gangster-picker. The Response is the chosen 0-based index (int).

    The driver validates the index is within ``range(len(options))`` and
    re-prompts otherwise. Cancellable as for :class:`PromptInt`.
    """

    key: str
    options: list
    cancellable: bool = False
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


@dataclass(frozen=True)
class Confirm:
    """Yes/no prompt (BASIC sub ``1110``). The Response is a ``bool``."""

    key: str
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


@dataclass(frozen=True)
class StartCombat:
    """Combat entry: run a full fight as a non-cancellable driver sub-protocol.

    A top-level handler yields this to hand control to the driver's inline combat
    loop (:func:`engine.fight_loop._run_combat`). The loop shares the parent's
    :class:`Ctx`, so any effects the fight buffers commit — or are discarded —
    atomically WITH the invoking handler's, exactly as :class:`LoadSubState` does for
    sub-states. The response ``.send()`` back into the handler is the fight's
    :class:`~engine.combat.CombatResult` (``winner`` + per-side ``losses``), so the
    handler applies its own entry-point consequence (debt seizure, reward, …).

    Fields:

    ``sides``
        The two fighter tuples for the fight, already built and placed — normally
        the output of :func:`engine.combat_setup.setup_combat` (``CombatState.sides``).
    ``grid``
        The backdrop's linear 521-cell wall/scenery code array (config data). An
        empty tuple is a legal open arena.
    ``rules``
        The game's :class:`~engine.combat.RulesBundle` — the hit/damage formulas
        and the attribute roles they read. Passed in because the engine owns *when* a
        hit test happens, never the formula or the attribute names it reads.

        There is no ``weapon_stats`` field: each combatant in ``sides``
        arrives carrying its own constructed ``equipment`` mapping, so equipment data
        travels with the roster rather than as a parallel table the engine resolves.
    ``dir_memory``
        Optional per-enemy-fighter direction memory seed (``ri()``), consumed by
        the AI; harmless to omit. Keyed by 0-based fighter index (the source's
        ``ri(i)`` is 1-based; :func:`engine.combat_setup.setup_combat` established the
        0-based keying and the AI follows it).
    ``cpu_sides``
        Which sides the engine plays itself instead of prompting the client
        (``mf-prg.bas:30110``: ``ifks(s)=0thengosub30400``). Defaults to
        :data:`engine.combat_ai.DEFAULT_CPU_SIDES` — side 2, the NPC party the
        combat-launch helper marks with ``ks(2)=0`` (``5010``). Pass an empty
        tuple for a hot-seat fight where both sides are client-driven (the source
        supports this shape at ``27020``, the bandenkrieg launch).

    Combat is only ever yielded from a TOP-LEVEL handler — the driver asserts this
    rather than supporting it inside :func:`_run_substate`.

    ``scenario``
        The whole fight payload in ONE field: a
        :class:`~engine.scenario.Scenario` supplying ``sides``/``grid``/``rules``/
        ``dir_memory``. When given, the four separate fields are IGNORED; when
        ``None``, those four fields describe the fight. The in-game handlers pass a
        ``Scenario``.
    ``drivers``
        Optional explicit ``{side: Driver}`` map. When given it OVERRIDES
        ``cpu_sides`` (the two knobs on one axis never merge); when ``None`` the loop
        derives the map from ``cpu_sides`` — ``AiDriver`` for a CPU side, ``HumanDriver``
        otherwise. A ``policy`` side is expressed by passing a ``drivers`` map.
    """

    sides: Any = ((), ())
    grid: Any = ()
    rules: Any = None
    dir_memory: Any = None
    #: ``None`` means "use the default" (side 2); an explicit ``()`` means "no CPU
    #: side at all" — the two are deliberately distinguishable, so a hot-seat fight
    #: can be requested without the default silently reasserting itself.
    cpu_sides: Any = None
    #: The whole fight payload in one value. Overrides the four separate fields above
    #: when present.
    scenario: Any = None
    #: An explicit ``{side: Driver}`` map. Overrides ``cpu_sides`` when present.
    drivers: Any = None
    #: The player whose handler starts the fight (``None``: the active player). Who
    #: answers each side's screens is the side's driver's (``HumanDriver.player``).
    player: int | None = None


#: The ``CombatScreen.prompt`` of a display-only observation frame.
OBSERVE_PROMPT = "observe"


@dataclass(frozen=True)
class CombatScreen:
    """One activation's combat screen — the client-facing fight interaction.

    Yielded once per activation (and again after an illegal input) while a fight is
    running. It carries everything a client needs to render the board and ask for the
    active fighter's single action, and NOTHING that is not JSON-serializable: this is
    the first interaction payload clients depend on *structurally*, so it doubles as a
    wire message for the eventual network transport. Overloading ``ShowMessage`` or
    ``PromptChoice`` could not carry a 40×13 grid; a dedicated typed interaction keeps
    rendering in the client and the protocol network-ready.

    The **response** the driver expects is a ``(action, argument)`` pair:

    - ``("move", step)`` — step one cell; ``step`` is one of
      :data:`engine.combat.STEPS` (``mf-prg.bas:30130-30133``). An illegal step
      re-prompts without consuming the activation (``30145`` jumps back to ``30125``).
    - ``("shoot", direction)`` — aim and fire; ``direction`` is one of
      :data:`engine.combat.STEPS` (``30206-30209``).
    - ``("pass", None)`` — end the activation without acting (``30135``, SPACE).
    - ``("surrender", None)`` — give up; the OTHER side wins (``30136``).

    Combat prompts are **non-cancellable**: a mandatory fight cannot be
    escaped through a cancel unwind, so the driver maps the client's quit vocabulary
    — :data:`CANCEL`, and EOF, which a client surfaces as ``CANCEL`` — to a
    ``surrender``. Unrecognized responses simply re-prompt.

    ``prompt`` is ``"action"`` for a real activation prompt; the field exists so a
    client that wants a separate aim step (the original reads the direction in a second
    GET at ``30205``) can be served without changing the interaction's type.

    ``prompt == "observe"`` (:data:`OBSERVE_PROMPT`) is a **display-only** frame:
    the board right after a NON-human activation applied, delivered only to an input
    source that opts in (``observes_ai = True`` — see
    :func:`engine.fight_loop._drive_fight`). Its
    ``active_side``/``active_fighter`` name the fighter that just ACTED and ``message``
    carries that activation's shot result (or ``None``). Its response is ignored.

    :meth:`to_json` renders the payload; :data:`SCHEMA_VERSION` versions it from day
    one, since clients bind to this shape structurally.
    """

    #: Payload schema version. Bump on any breaking change to :meth:`to_json`'s shape.
    SCHEMA_VERSION = 1

    sides: Any = ((), ())
    grid: Any = ()
    active_side: int = 1
    active_fighter: int = 1
    losses: Any = (0, 0)
    prompt: str = "action"
    message: Any = None
    #: Who answers this screen: the controller of the acting side
    #: (:attr:`engine.fight_loop.HumanDriver.player`); ``None``: the active player.
    player: int | None = None

    def to_json(self) -> dict:
        """Return the JSON-serializable payload (plain dicts/lists/scalars only).

        Frozen dataclasses and tuples do not survive JSON on their own, so the whole
        snapshot is walked into plain containers here rather than at each client.
        """
        from engine.state import json_safe

        return {
            "version": self.SCHEMA_VERSION,
            "sides": [[json_safe(f) for f in side] for side in self.sides],
            "grid": list(self.grid),
            "active_side": self.active_side,
            "active_fighter": self.active_fighter,
            "losses": list(self.losses),
            "prompt": self.prompt,
            "message": json_safe(self.message) if self.message is not None else None,
            "player": self.player,
            "fighter": self._active_fighter_panel(json_safe),
        }

    def _active_fighter_panel(self, json_safe: Callable[[Any], Any]) -> Any:
        """The active fighter's own stats/weapon panel (``mf-prg.bas:30115-30116``)."""
        side = self.sides[self.active_side - 1] if self.sides else ()
        if not side or self.active_fighter - 1 >= len(side):
            return None
        return json_safe(side[self.active_fighter - 1])


@dataclass(frozen=True)
class LoadSubState:
    """Run a nested sub-state (minigame / spec sheet) as a child generator.

    When a parent handler yields this, the driver looks up the sub-state handler
    registered under ``kind`` in :data:`engine.substates.SUBSTATES`, drives it to
    completion sharing the parent's :class:`Ctx` (its effects/events merge into the
    parent action — one atomic boundary), and ``.send()``s its return value back
    into the parent as this interaction's response. An unknown ``kind`` is a config
    bug and raises :class:`ValueError`.
    """

    kind: Any
    params: dict = field(default_factory=dict)
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


@dataclass(frozen=True)
class Acknowledge:
    """An acknowledgement screen: shown whole, then answered with one key (:data:`Ack`).

    The turn runner (:mod:`engine.turns`) yields one for each screen of a turn that
    only waits to be read: the upkeep screen, the turn-over summary, the round
    standings and the year-end result. ``key`` names the screen (the runner's
    ``*_SCREEN`` constants); ``params`` carries its data, e.g. the standings' rows as
    ``(key, params)`` pairs under ``"lines"``. ``player`` is the player the screen is
    for (``None``: the active player). The response is ignored — a client that offers
    a quit key there simply stops driving the runner.
    """

    key: str
    params: dict = field(default_factory=dict)
    player: int | None = None


@dataclass(frozen=True)
class Heading:
    """A display-only screen title: a new screen begins under ``key``.

    The turn runner yields one where a turn step opens a screen of its own before its
    handler's interactions (the job shift). The response is ignored.
    """

    key: str
    params: dict = field(default_factory=dict)
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


#: The map-move prompt's direction answers, and its command answers: save and quit
#: (offered at the turn menu too) and the map's exit back to the turn menu.
MAP_DIRECTIONS = ("up", "down", "left", "right")
MAP_SAVE = "save"
MAP_QUIT = "quit"
MAP_EXIT = "exit"


@dataclass(frozen=True)
class MapMove:
    """The map-move prompt: the turn runner asks for the next step on the map.

    The runner yields one before every step of the free turn (``mf-prg.bas:2010``).
    The answer is one of ``directions`` (the runner moves) or one of ``commands``:
    :data:`MAP_SAVE` and :data:`MAP_QUIT` are offered here because saving is offered
    only at the turn menu and this prompt. A driver saves the runner's committed
    state itself; the runner, answered :data:`MAP_SAVE`, asks again with nothing
    changed. Answered :data:`MAP_QUIT`, the runner stops (a driver may also simply
    stop driving). Answered :data:`MAP_EXIT`, the player leaves the map for the turn
    menu (``mf-prg.bas:2019 ifx$="_"thensysie:return``). Any other answer asks again.

    ``outcome`` is what the previous answer did on the map, so a client can say so:
    the step's kind (``"step"``, ``"enter"``, ``"wall"``, ``"oob"``, ``"special"``, see
    :class:`engine.movement.MoveResult`), or ``None`` when no move preceded this prompt
    (the free turn just opened, or the answer was a command or invalid). ``player`` is
    the player who moves (``None``: the active player).
    """

    outcome: str | None = None
    directions: tuple[str, ...] = MAP_DIRECTIONS
    commands: tuple[str, ...] = (MAP_SAVE, MAP_QUIT, MAP_EXIT)
    player: int | None = None


@dataclass(frozen=True)
class TurnMenu:
    """The turn menu: the turn runner asks what the active player does next.

    The runner yields one when the free turn opens and again after each action while
    movement points remain (``mf-prg.bas:1015-1045``). ``options`` are the ids of the
    turn-menu shell's options whose guard passes, in shell order, and ``keys`` the key
    that picks each (the same order): the answer is one of ``keys``. Anything else --
    a key no option has, no answer -- is ignored and the same menu is asked again
    (``:1030``). ``commands`` are answers too: :data:`MAP_SAVE` (a driver saves the
    runner's committed state itself; the runner asks again with nothing changed) and
    :data:`MAP_QUIT` (the runner stops). ``player`` is the player choosing (``None``:
    the active player).
    """

    options: tuple[str, ...]
    keys: tuple[str, ...]
    commands: tuple[str, ...] = (MAP_SAVE, MAP_QUIT)
    player: int | None = None


@dataclass(frozen=True)
class LocationMenu:
    """The location menu: a choice over a location shell's available options.

    The turn runner yields one after a door entry. ``location`` is the shell's key,
    ``options`` the ids of the options whose guard passes, in shell order, and ``ln``
    the tile entered (``mf-prg.bas:2050``). The answer is the chosen option's 0-based
    index; anything else — no answer, a non-number, an index out of range — is ignored
    and the same menu is asked again (``:3040 ifw<1orw>awgoto3040``): no answer leaves
    for free, leaving is the shell's own option. With no ``options`` there is nothing
    to choose and any answer ends the visit. ``player`` is the player inside
    (``None``: active).
    """

    location: str
    options: tuple[str, ...]
    ln: int
    player: int | None = None


@dataclass(frozen=True)
class OptionDone:
    """Display-only: a location option chosen at a :class:`LocationMenu` has run.

    ``status`` is its :data:`~engine.actions.EngineStatus`: ``"completed"``, or
    ``"cancelled"`` when the player backed out and nothing committed. A client shows
    the result of the action between actions. The response is ignored.
    """

    location: str
    option: str
    status: str
    player: int | None = None


#: What a handler may yield: the interaction catalog above, as one union. The single
#: definition — :class:`engine.types.HandlerFunc` imports it rather than restating it.
Interaction: TypeAlias = (
    ShowMessage
    | PromptInt
    | PromptChoice
    | Confirm
    | StartCombat
    | CombatScreen
    | LoadSubState
    | Acknowledge
    | Heading
)


# --------------------------------------------------------------------------- #
# Response / control sentinels                                                #
# --------------------------------------------------------------------------- #
class _AckType:
    """Type of the singleton :data:`Ack` acknowledgement sent back for a ShowMessage."""

    _instance: "_AckType | None" = None

    def __new__(cls) -> "_AckType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "Ack"


#: Sentinel the driver ``.send()``s back for a :class:`ShowMessage` (no real value).
Ack = _AckType()


class _CancelType:
    """Type of the singleton :data:`CANCEL` sentinel an input source may supply."""

    _instance: "_CancelType | None" = None

    def __new__(cls) -> "_CancelType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "CANCEL"


#: Sentinel an input source returns at a ``cancellable`` prompt to abort the action.
#: At a non-cancellable prompt it is treated as invalid input and the driver re-prompts.
CANCEL = _CancelType()


class Cancelled(Exception):
    """Thrown INTO a handler (``gen.throw``) to unwind it on cancel.

    Handlers unwind via ``try/finally`` and do not need to catch this. The driver
    catches it as expected control flow and discards the action's effect buffer.
    """


# --------------------------------------------------------------------------- #
# Ctx — the handler's window into the engine                                  #
# --------------------------------------------------------------------------- #
class Ctx:
    """Per-handler-run context passed to the handler factory as ``handler(ctx)``.

    Exposes the *only* surface a handler may touch for state/rng/effects (the rest
    of the Handler API — ``yield`` and named helpers — lives elsewhere). ``apply``
    buffers effects; the driver decides whether that buffer commits or is discarded.
    ``Ctx`` does not interpret effects (any object); :func:`engine.effects.apply` does.
    """

    def __init__(self, state: Any = None, rng: Any = None) -> None:
        self._state = state
        self._rng = rng
        self._buffer: list = []
        self._events: list = []

    @property
    def state(self) -> Any:
        """Read-only handle to the game state passed into the driver."""
        return self._state

    @property
    def rng(self) -> Any:
        """The rng handle passed into the driver."""
        return self._rng

    def apply(self, effect: Any) -> None:
        """Append ``effect`` (any object) to the ordered per-action buffer.

        Does NOT mutate game state — buffering only. The driver commits the buffer
        on clean generator completion and discards it on cancel.
        """
        self._buffer.append(effect)

    def record(self, event: Any) -> None:
        """Append a semantic ``event`` (audit/UI record) to the per-action event buffer.

        Events are pure records — they are NEVER applied to state (that is ``apply``'s
        job for effects). The driver surfaces the buffered events on ``EngineResult.events``
        on clean completion and discards them (like effects) on cancel.
        """
        self._events.append(event)


# --------------------------------------------------------------------------- #
# The driver                                                                  #
# --------------------------------------------------------------------------- #
def step(
    handler: Callable[[Ctx], Generator[Any, Any, Any]],
    state: GameState | None = None,
    rng: Any = None,
    *,
    observe_ai: bool = False,
) -> Generator[Any, Any, EngineResult[Any]]:
    """The generator form of the driver: drive ``handler`` by yielding its interactions.

    ``step`` builds the :class:`Ctx`, calls the factory with it and advances the
    handler. Every interaction that needs the client is YIELDED to whoever drives
    ``step`` — including those of a nested sub-state and every screen of a fight —
    and the value sent back is that attempt's raw response. ``step`` owns validation
    exactly as :func:`run` always has: an invalid answer re-yields the same prompt, a
    ``ShowMessage``/:class:`Acknowledge`/:class:`Heading` is yielded for delivery and
    the handler always gets :data:`Ack`, and :data:`CANCEL` at a ``cancellable``
    prompt unwinds the handler. It returns (``StopIteration.value``) the same
    :class:`~engine.actions.EngineResult` :func:`run` returns.

    Because it is a generator, a caller that itself yields interactions — the engine
    turn runner (:mod:`engine.turns`) — composes a handler into its own stream with
    ``result = yield from step(...)``. :func:`run` is a thin pull loop over it.

    ``observe_ai`` opts in to the display-only ``prompt="observe"`` combat frames after
    each non-human activation (see :func:`engine.fight_loop._drive_fight`); their
    response is ignored. :func:`run` sets it from its input source's ``observes_ai``.

    A handler that RAISES commits nothing: its buffered effects are never folded, and
    the exception propagates out of ``step`` (a bug keeps its traceback).

    Every interaction ``step`` yields names its player: one whose ``player`` is ``None``
    is yielded with the active player's index filled in (``state.clock.active_player``;
    with no state it stays ``None``).
    """
    ctx = Ctx(state=state, rng=rng)
    gen = handler(ctx)
    active = _active_player(state)

    try:
        interaction = next(gen)  # prime the generator to its first yield
        while True:
            if isinstance(interaction, LoadSubState):
                # The nested sub-state runs HERE, sharing this ctx — its ctx.apply/
                # ctx.record append into the parent's buffers, so the whole nesting
                # commits or discards as ONE atomic action. A Cancelled thrown at a
                # child prompt propagates out of _run_substate up to the `except
                # Cancelled` below, unwinding the whole action.
                response = yield from _addressed(_run_substate(interaction, ctx), active)
            elif isinstance(interaction, StartCombat):
                # The combat sub-protocol runs HERE for the same reason: it shares
                # this ctx, so a fight's effects buffer into the parent action and
                # commit (or discard) with it.
                from engine.fight_loop import _run_combat

                response = yield from _addressed(
                    _run_combat(interaction, ctx, observe_ai=observe_ai), active
                )
            else:
                response = yield from _addressed(_resolve(interaction), active)
                if response is _CANCEL_SIGNAL:
                    # A cancellable prompt was cancelled: unwind the handler. The throw
                    # is owned here (not in _resolve) so that a handler which *catches*
                    # Cancelled and continues fails loud instead of silently feeding
                    # its next yielded Interaction back in as a response.
                    gen.throw(Cancelled())
                    # gen.throw only returns if the handler swallowed Cancelled and
                    # yielded again — a contract violation (cancel must unwind).
                    raise RuntimeError(
                        "handler caught Cancelled and continued; cancellation must "
                        "unwind the handler (do not catch Cancelled and keep yielding)"
                    )
            interaction = gen.send(response)
    except Cancelled:
        # Expected control flow: the handler unwound on the cancel throw. Atomic discard:
        # NO effects apply AND NO events surface — the returned state is the ORIGINAL,
        # unchanged state object (atomicity proven at the state level).
        return EngineResult(
            state=state,
            events=[],
            effects=[],
            status="cancelled",
            payload=HandlerResult(returned=None),
        )
    except (StopIteration, _InputExhausted) as stop:
        # Clean completion: commit the buffer atomically against a SINGLE deep copy of the
        # state and surface the buffered semantic events. Import commit lazily HERE so this
        # module never imports engine.effects at module load — engine.effects imports
        # GameState from engine.state, so a top-level import would risk a cycle; the lazy
        # local import keeps engine.effects free of any dependency on this module.
        buffer = list(ctx._buffer)
        if state is None:
            # No state to commit against: the buffered items pass through as the effect
            # record unchanged (they are never applied — there is nothing to apply to).
            final_state = None
            effects = buffer
        else:
            from engine.effects import commit

            commit_result = commit(state, buffer)
            final_state = commit_result.state
            effects = commit_result.effects
        return EngineResult(
            state=final_state,
            events=list(ctx._events),
            effects=effects,
            status="completed",
            payload=HandlerResult(returned=stop.value),
        )


@overload
def run(
    handler: Callable[[Ctx], Generator[Any, Any, Any]],
    input_source: Callable[[Any], Any],
    *,
    state: GameState,
    rng: Any = None,
) -> EngineResult[GameState]: ...


@overload
def run(
    handler: Callable[[Ctx], Generator[Any, Any, Any]],
    input_source: Callable[[Any], Any],
    *,
    state: None = None,
    rng: Any = None,
) -> EngineResult[None]: ...


def run(
    handler: Callable[[Ctx], Generator[Any, Any, Any]],
    input_source: Callable[[Any], Any],
    *,
    state: GameState | None = None,
    rng: Any = None,
) -> EngineResult[GameState | None]:
    """Advance a handler generator to completion, mediating its interactions.

    A thin pull loop over :func:`step`: every interaction ``step`` yields is handed to
    ``input_source`` and its return value is sent back.

    Args:
        handler: A factory ``(ctx) -> generator``. The driver builds the :class:`Ctx`,
            calls the factory with it, and drives the returned generator.
        input_source: A callable ``(interaction) -> response`` the driver pulls from
            whenever an interaction needs client input (``PromptInt``/``PromptChoice``/
            ``Confirm``). It is consulted once per attempt, so an invalid answer that
            triggers a re-prompt consults it again. ``ShowMessage`` is also handed to
            it — for DELIVERY only (the client must be able to render narration); its
            return value there is discarded and :data:`Ack` is sent regardless, so a
            display-only interaction can never become a cancel path. This callable
            shape lets tests both assert on the presented interaction and return a
            context-appropriate response. A truthy ``observes_ai`` attribute on it opts
            in to the fight's observation frames (``step``'s ``observe_ai``).
        state: Opaque game state exposed as ``ctx.state`` (read-only for handlers).
        rng: Opaque rng handle exposed as ``ctx.rng``.

    Returns:
        An :class:`~engine.actions.EngineResult`. On clean completion its ``status`` is
        ``"completed"``, ``effects`` are the committed effects in apply-order, ``events``
        are the semantic events buffered via ``ctx.record``, ``state`` is the post-commit
        state, and ``payload`` is a :class:`~engine.actions.HandlerResult` carrying the
        handler's return value. On cancel its ``status`` is ``"cancelled"`` with empty
        ``events``/``effects``, the ORIGINAL unchanged ``state``, and a
        ``HandlerResult(returned=None)`` payload.

    Raises:
        ValueError: if the handler yields ``LoadSubState`` with a ``kind`` that is not
            registered in :data:`engine.substates.SUBSTATES` (a config bug).

    A yielded ``StartCombat`` runs the combat sub-protocol
    (:func:`engine.fight_loop._run_combat`) and resolves with the fight's
    :class:`~engine.combat.CombatResult`.

    Note:
        Synchronous by construction. The SAME protocol is later driven by an async
        server (docs/design/engine-architecture.md); that transport is deliberately NOT built here.
    """
    steps = step(
        handler,
        state,
        rng,
        observe_ai=bool(getattr(input_source, "observes_ai", False)),
    )
    try:
        interaction = next(steps)
        while True:
            try:
                response = input_source(interaction)
            except StopIteration as exhausted:
                # An input source that raises StopIteration (a scripted answer list
                # run dry) ends the action as a clean completion with what it has
                # buffered — the pull driver's contract before step() existed, kept so
                # scripted callers behave as they always have.
                interaction = steps.throw(_InputExhausted(exhausted.value))
                continue
            interaction = steps.send(response)
    except StopIteration as stop:
        return stop.value


def _active_player(state: Any) -> int | None:
    """The active player's index in ``state``, or ``None`` when it has no turn clock."""
    clock = getattr(state, "clock", None)
    return getattr(clock, "active_player", None)


def _addressed(inner: Generator[Any, Any, Any], active: int | None) -> Generator[Any, Any, Any]:
    """Relay ``inner``'s interactions, filling each unnamed ``player`` with ``active``.

    Answers are sent back to ``inner`` unchanged and its return value is returned. An
    exception thrown in (a driver's :class:`_InputExhausted`) ends the relay where it
    lands; none of the relayed generators catches one, so it reaches :func:`step`
    exactly as through a plain ``yield from``.
    """
    try:
        interaction = next(inner)
        while True:
            if active is not None and getattr(interaction, "player", active) is None:
                interaction = replace(interaction, player=active)
            interaction = inner.send((yield interaction))
    except StopIteration as stop:
        return stop.value


class _InputExhausted(Exception):
    """Thrown into :func:`step` by :func:`run` when its input source raised StopIteration."""

    def __init__(self, value: Any) -> None:
        super().__init__(value)
        self.value = value


#: Private sentinel :func:`_resolve` returns to tell :func:`step` "cancel this action".
#: The driver — not :func:`_resolve` — owns the ``gen.throw(Cancelled())`` so a handler
#: that swallows ``Cancelled`` and continues fails loud rather than corrupting the stream.
_CANCEL_SIGNAL = object()

#: The display-only interactions: delivered to the client, always answered with :data:`Ack`.
_DISPLAY_ONLY = (ShowMessage, Acknowledge, Heading)


def _resolve(interaction: Any) -> Generator[Any, Any, Any]:
    """Obtain the Response to ``.send()`` for one interaction (validating/re-prompting).

    A generator: it yields the interaction once per attempt and receives that attempt's
    raw response. Returns the validated response value, or :data:`_CANCEL_SIGNAL` when
    a ``cancellable`` prompt was cancelled (the caller then unwinds the handler via
    ``gen.throw``). Never throws into the handler generator itself.
    """
    if isinstance(interaction, _DISPLAY_ONLY):
        # Display-only, but DELIVERY and RESPONSE are separate concerns. The
        # message still has to reach the client — a driver that acks without handing
        # it over makes every handler's narration structurally invisible. So the
        # client SEES it, and its response is DISCARDED: nothing a client returns
        # (CANCEL included) can turn narration into a cancel path or feed a
        # fabricated response back into the handler. Ack is sent unconditionally.
        yield interaction
        return Ack

    if isinstance(interaction, StartCombat):
        # StartCombat is a SUB-PROTOCOL, not a single request/response: it is handled
        # by step() before _resolve is ever consulted (like LoadSubState). Reaching here
        # means a new yield path bypassed that dispatch.
        raise AssertionError(
            "StartCombat must be dispatched by the driver's combat loop, not resolved "
            "as a single interaction."
        )

    if isinstance(interaction, PromptInt):
        while True:
            raw = yield interaction
            if raw is CANCEL:
                if interaction.cancellable:
                    return _CANCEL_SIGNAL
                continue  # invalid at a non-cancellable prompt → re-prompt
            value = _coerce_int(raw)
            if value is None:
                continue  # non-numeric → re-prompt
            if interaction.min <= value <= interaction.max:
                return value
            # out of range → re-prompt

    if isinstance(interaction, PromptChoice):
        n = len(interaction.options)
        while True:
            raw = yield interaction
            if raw is CANCEL:
                if interaction.cancellable:
                    return _CANCEL_SIGNAL
                continue
            value = _coerce_int(raw)
            if value is None:
                continue
            if 0 <= value < n:
                return value
            # out of range → re-prompt

    if isinstance(interaction, Confirm):
        raw = yield interaction
        # Confirm is not itself cancellable in this catalog; coerce to a bool.
        return bool(raw)

    raise TypeError(f"Unknown interaction type: {type(interaction).__name__!r}")


def _run_substate(load: "LoadSubState", ctx: "Ctx") -> Generator[Any, Any, Any]:
    """Drive a nested sub-state generator to completion and return its value.

    A generator, composed into :func:`step` with ``yield from``: every interaction of
    the child reaches the client through ``step``'s own stream. Looks up the sub-state
    factory registered under ``load.kind`` in :data:`engine.substates.SUBSTATES`,
    builds the child generator sharing the parent's ``ctx`` (shared-buffer model —
    child ``ctx.apply``/``ctx.record`` append into the parent's buffers), and drives it
    reusing :func:`_resolve` for its interactions. This is deliberately NOT a nested
    :func:`step`: a nested one would commit/discard the child's effects independently
    and break cross-boundary atomicity.

    On the child's clean completion, returns its ``StopIteration.value`` (the value
    :func:`step` then ``.send()``s into the parent). A ``Cancelled`` thrown at a
    cancellable child prompt is raised INTO the child (so its ``try/finally``
    unwinds) and then **propagates out of this function** to :func:`step`'s
    ``except Cancelled`` handler — one discard unwinds the whole nesting.

    Raises:
        ValueError: if ``load.kind`` is not registered (a config bug).
        AssertionError: if the child yields ``StartCombat`` — combat is only ever
            yielded from top-level handlers.
    """
    from engine.substates import SUBSTATES

    factory = SUBSTATES.get(load.kind)
    if factory is None:
        raise ValueError(f"unknown sub-state kind {load.kind!r}; registered: {sorted(SUBSTATES)}")

    child = factory(ctx, load.params)
    interaction = next(child)  # prime the child to its first yield
    while True:
        # Combat is only ever yielded from TOP-LEVEL handlers. The driver asserts that
        # rather than supporting nesting, because a fight inside a sub-state would need
        # cancel semantics ("combat is non-cancellable" vs. "a cancelled sub-state
        # unwinds the whole action") that are not decided. Failing loud here beats
        # silently picking one.
        assert not isinstance(interaction, StartCombat), (
            "StartCombat inside a sub-state is not supported: "
            "yield combat from a top-level handler."
        )
        response = yield from _resolve(interaction)
        if response is _CANCEL_SIGNAL:
            # Cancel INSIDE the sub-state: unwind the child, then let Cancelled
            # propagate up to step()'s handler so the whole action discards atomically.
            child.throw(Cancelled())
            raise RuntimeError(
                "sub-state handler caught Cancelled and continued; cancellation "
                "must unwind the handler (do not catch Cancelled and keep yielding)"
            )
        try:
            interaction = child.send(response)
        except StopIteration as stop:
            return stop.value


def _coerce_int(raw: Any) -> int | None:
    """Return ``raw`` as an int if it is an integer value, else ``None``.

    Accepts genuine ``int`` (but not ``bool``, which is an ``int`` subclass) and
    plain ASCII decimal strings like ``"4"`` / ``"-5"`` / ``"+7"`` (optionally
    surrounded by whitespace). Everything else — floats, non-numeric strings,
    and ``int``-literal niceties a raw player prompt should not honor (``"1_000"``
    underscore separators, non-ASCII digits like ``"３"``) — is rejected so the
    driver re-prompts.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        body = s[1:] if s[:1] in "+-" else s
        if not body.isascii() or not body.isdigit():
            return None
        return int(s)
    return None

"""The interaction protocol: the interaction catalog and the response/control sentinels.

The typed **Interactions** a handler ``yield``s (docs/design/engine-architecture.md),
the read modes and map-move answers they carry, the :data:`Ack` and :data:`CANCEL`
sentinels, and the :class:`Cancelled` and :class:`InputExhausted` control exceptions.
This is the request/response vocabulary the in-process driver
(:mod:`engine.interactions`) and, unchanged, the eventual network transport share. The
module holds data definitions only: it imports nothing from the driver, nor from
``server``/``clients``/transport. :mod:`engine.interactions` re-exports every name in
``__all__``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeAlias

__all__ = [
    # Interactions
    "ShowMessage",
    "PromptInt",
    "PromptChoice",
    "READ_LINE",
    "READ_KEY",
    "Confirm",
    "PromptText",
    "RollFrame",
    "StartCombat",
    "CombatScreen",
    "OBSERVE_PROMPT",
    "BEGINS_MESSAGE",
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
    "InputExhausted",
    # Driver
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


#: How a client reads a prompt's answer (``PromptInt.read`` / ``PromptChoice.read``):
#: a line, ended with Enter -- the default.
READ_LINE = "line"
#: How a client reads a prompt's answer: one key, no Enter.
READ_KEY = "key"
_READ_MODES = (READ_LINE, READ_KEY)


def _check_read(read: str) -> None:
    if read not in _READ_MODES:
        raise ValueError(f"read must be one of {_READ_MODES}, not {read!r}")


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
    #: The value an empty answer stands for, returned without the range check (a
    #: source prompt that keeps its variable's value on an empty answer, where that
    #: value is known); ``None``: an empty answer is asked again. A cancellable
    #: prompt's client reads an empty answer as cancel, so pair it with
    #: ``cancellable=False``.
    blank: int | None = None
    #: How the client reads the answer: :data:`READ_LINE` (a line with Enter) or
    #: :data:`READ_KEY` (one key). Presentation only: the driver validates the
    #: answer the same way under either.
    read: str = READ_LINE
    #: Template params the client fills into the prompt's text (a prompt line that
    #: shows a value, e.g. the cash before the question).
    params: dict = field(default_factory=dict)
    #: Whether a sign set apart from its digits by blanks reads as the signed number
    #: (``"- 3"`` as -3); ``False``: such an answer is asked again. The range check
    #: applies either way.
    spaced_sign: bool = False

    def __post_init__(self) -> None:
        _check_read(self.read)


@dataclass(frozen=True)
class PromptChoice:
    """Menu / gangster-picker. The Response is the chosen 0-based index (int).

    The driver validates the index is within ``range(len(options))`` and
    re-prompts otherwise. Cancellable as for :class:`PromptInt`.

    The protocol stays 0-based whatever the screen shows: a client numbers the
    options for its player and maps the key it reads back to the index (a terminal
    prints option ``i`` as number ``i + 1``), or, when ``answer_keys`` is given, maps
    the key ``answer_keys[i]`` to ``i``.
    """

    key: str
    options: list
    cancellable: bool = False
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None
    #: How the client reads the answer: :data:`READ_LINE` (a line with Enter) or
    #: :data:`READ_KEY` (one key).
    read: str = READ_LINE
    #: The key that answers each option, in option order (lower case; a key may be
    #: more than one character, e.g. a function key's name), or ``None``: the options
    #: are answered by their numbers from 1.
    answer_keys: tuple[str, ...] | None = None
    #: A theme key printed after the options, where the menu asks for its answer;
    #: ``None``: nothing follows the options.
    prompt: str | None = None

    def __post_init__(self) -> None:
        _check_read(self.read)
        keys = self.answer_keys
        if keys is not None and (
            len(keys) != len(self.options)
            or len(set(keys)) != len(keys)
            or any(not k or k != k.lower() for k in keys)
        ):
            raise ValueError(
                f"answer_keys must name one distinct lower-case key per option: {keys!r}"
            )


@dataclass(frozen=True)
class Confirm:
    """Yes/no prompt (BASIC sub ``1110``). The Response is a ``bool``."""

    key: str
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


@dataclass(frozen=True)
class PromptText:
    """Ask for a line of text. The Response is a ``str``.

    Generic: the driver does NO validation — no length rule, no trimming. A config
    handler that needs a rule checks the answer and asks again. Not cancellable: a
    :data:`CANCEL` is invalid input and re-prompts (as at a non-cancellable
    :class:`PromptInt`); any other answer that is not a ``str`` is ``str()``-coerced.
    """

    key: str
    params: dict = field(default_factory=dict)
    #: Who answers this interaction (a player index); ``None``: the active player.
    #: :func:`step` fills a ``None`` in with the active player when it yields.
    player: int | None = None


@dataclass(frozen=True)
class RollFrame:
    """One frame of a roll the player stops. The Response is a ``bool``, "stopped".

    A roll is a sequence of real frames: the handler yields one per draw, with the
    drawn value in ``params``, and the client shows it and answers whether a key
    stopped it. The engine knows nothing of what is rolled. Not cancellable: a
    :data:`CANCEL` (end of input, as a client surfaces EOF) answers ``True``, so the
    roll stops on what is shown; any other answer is coerced with ``bool()`` like
    :class:`Confirm`.
    """

    key: str
    params: dict = field(default_factory=dict)
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
    #: Each side's name, side 1's first: the caller's data (``mf-prg.bas:30015``
    #: ``printbn$(ks(i))``, the gang names), carried onto every :class:`CombatScreen`
    #: for the client to print under the board. Empty: the fight names no side.
    names: Any = ()


#: The ``CombatScreen.prompt`` of a display-only observation frame.
OBSERVE_PROMPT = "observe"
#: The ``CombatScreen.message`` key of a fight's first screen: ``:30025``'s banner.
BEGINS_MESSAGE = "begins"


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

    ``names`` is each side's name as :attr:`StartCombat.names` gave it (``:30015``
    prints them under the board once, and they stay). The first screen of a fight
    carries ``message`` :data:`BEGINS_MESSAGE` (``:30025``'s banner) unless an earlier
    CPU activation left its own. ``cpu_fighter`` is the number of the last CPU fighter
    that acted, ``None`` before any did: ``:30400`` prints its label on every CPU
    activation and nothing erases it, so it is still on the board for the next screen.

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
    names: Any = ()
    cpu_fighter: int | None = None

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
            "names": list(self.names),
            "cpu_fighter": self.cpu_fighter,
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
    | PromptText
    | RollFrame
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


class InputExhausted(RuntimeError):
    """Raised out of :func:`run` when its input source runs dry before the action ends.

    An input source signals "no more answers" by raising ``StopIteration`` (a scripted
    answer list used up). The unfinished action commits nothing: :func:`run` closes
    the action and raises this instead of returning a result. ``interaction`` is the
    one that went unanswered.
    """

    def __init__(self, interaction: Any) -> None:
        super().__init__(f"input source ran dry; nothing answered {interaction!r}")
        self.interaction = interaction

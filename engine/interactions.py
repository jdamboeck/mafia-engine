"""The interaction protocol + in-process synchronous driver — THE SPINE (docs/design/engine-architecture.md).

A **handler** is a factory ``(ctx) -> Generator[Interaction, Response, list[Event]]``.
It ``yield``s a typed **Interaction**; the **driver** (:func:`run`) turns that into an
obtained **Response** and ``.send()``s it back. This same request/response protocol is,
unchanged, the eventual network message protocol — the WebSocket server (docs/design/engine-architecture.md) is
merely an async transport driving the identical generators. This module builds ONLY the
in-process synchronous driver; it imports nothing from ``server``/``clients``/transport.

Two firmly separated categories (docs/design/engine-architecture.md):

- **Interactions** control *execution flow* — they suspend the handler to ask the client
  something. A handler reaches the client *only* by ``yield``ing one of these.
- **Effects** mutate *game state*. A handler never mutates state directly; it calls
  ``ctx.apply(effect)``, which buffers the effect. U4 treats an effect as an opaque
  object (any value) — interpreting/applying effects to state is U5's job. The driver
  commits the buffer atomically: on clean return the buffered effects are the committed
  list, on cancel the buffer is discarded (committed effects == ``[]``).

Cancellation (KTD-2, mechanism *(a)*): the driver calls ``gen.throw(Cancelled())`` INTO
the handler when the input source supplies the :data:`CANCEL` sentinel at a
``cancellable`` prompt. The handler unwinds through its ``try/finally`` (it does not need
to catch ``Cancelled``); the driver catches ``Cancelled`` as expected control flow and
discards the effect buffer.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from engine.actions import EngineResult, HandlerResult

if TYPE_CHECKING:
    # Type-only: keep this module's RUNTIME import graph free of engine.combat (the
    # spine imports it lazily inside _run_combat, see the module docstring). The
    # ``from __future__ import annotations`` above makes every annotation a string, so
    # ``-> CombatResult`` never triggers a runtime import; this block only lets a type
    # checker resolve the name.
    from engine.combat import CombatResult

__all__ = [
    # Interactions
    "ShowMessage",
    "PromptInt",
    "PromptChoice",
    "Confirm",
    "StartCombat",
    "CombatScreen",
    "LoadSubState",
    # Response / control
    "Ack",
    "CANCEL",
    "Cancelled",
    # Per-side combat drivers (U6)
    "Driver",
    "HumanDriver",
    "AiDriver",
    "PolicyDriver",
    "ReplayDriver",
    # Driver
    "Ctx",
    "run",
    "simulate",
]


# --------------------------------------------------------------------------- #
# Interaction catalog (docs/design/engine-architecture.md)                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ShowMessage:
    """Display-only interaction. The Response is an :data:`Ack` (no value)."""

    key: str
    params: dict = field(default_factory=dict)


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


@dataclass(frozen=True)
class PromptChoice:
    """Menu / gangster-picker. The Response is the chosen 0-based index (int).

    The driver validates the index is within ``range(len(options))`` and
    re-prompts otherwise. Cancellable as for :class:`PromptInt`.
    """

    key: str
    options: list
    cancellable: bool = False


@dataclass(frozen=True)
class Confirm:
    """Yes/no prompt (BASIC sub ``1110``). The Response is a ``bool``."""

    key: str


@dataclass(frozen=True)
class StartCombat:
    """Combat entry: run a full fight as a driver sub-protocol (U5, KTD-1/KTD-2).

    A top-level handler yields this to hand control to the driver's inline combat
    loop (:func:`_run_combat`). The loop shares the parent's :class:`Ctx`, so any
    effects the fight buffers commit — or are discarded — atomically WITH the
    invoking handler's, exactly as :class:`LoadSubState` does for sub-states. The
    response ``.send()`` back into the handler is the **winning side** (1 or 2), so
    the handler applies its own entry-point consequence (debt seizure, reward, …).

    Fields:

    ``sides``
        The two fighter tuples for the fight, already built and placed — normally
        the output of :func:`engine.combat.setup_combat` (``CombatState.sides``).
    ``grid``
        The backdrop's linear 521-cell wall/scenery code array (config data). An
        empty tuple is a legal open arena.
    ``rules``
        The game's :class:`~engine.combat.RulesBundle` (U2) — the hit/damage formulas
        and the attribute roles they read. Passed in because the engine owns *when* a
        hit test happens, never the formula or the attribute names it reads.

        There is no ``weapon_stats`` field (amendment A1): each combatant in ``sides``
        arrives carrying its own constructed ``equipment`` mapping, so equipment data
        travels with the roster rather than as a parallel table the engine resolves.
    ``dir_memory``
        Optional per-enemy-fighter direction memory seed (``ri()``), consumed by
        U6's AI; harmless to omit. Keyed by 0-based fighter index (the source's
        ``ri(i)`` is 1-based; :func:`engine.combat.setup_combat` established the
        0-based keying and the AI follows it).
    ``cpu_sides``
        Which sides the engine plays itself instead of prompting the client
        (``mf-prg.bas:30110``: ``ifks(s)=0thengosub30400``). Defaults to
        :data:`engine.combat.DEFAULT_CPU_SIDES` — side 2, the NPC party the
        combat-launch helper marks with ``ks(2)=0`` (``5010``). Pass an empty
        tuple for a hot-seat fight where both sides are client-driven (the source
        supports this shape at ``27020``, the bandenkrieg launch).

    Combat is only ever yielded from a TOP-LEVEL handler this slice (KTD-1) — the
    driver asserts this rather than supporting it inside :func:`_run_substate`.

    ``scenario``
        The whole fight payload in ONE field (U6, amendment A6): a
        :class:`~engine.scenario.Scenario` supplying ``sides``/``grid``/``rules``/
        ``dir_memory``. When given, the legacy four fields are IGNORED; when ``None``,
        today's four-field path is unchanged, so every pre-U6 call site still works.
        This is the widening U5 deferred — the three in-game handlers already build a
        ``Scenario``, so they now pass it straight through.
    ``drivers``
        Optional explicit ``{side: Driver}`` map (U6). When given it OVERRIDES
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
    #: The whole fight payload in one value (amendment A6). Overrides the four legacy
    #: fields above when present.
    scenario: Any = None
    #: An explicit ``{side: Driver}`` map. Overrides ``cpu_sides`` when present.
    drivers: Any = None


@dataclass(frozen=True)
class CombatScreen:
    """One activation's combat screen — the client-facing fight interaction (KTD-2).

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

    Combat prompts are **non-cancellable** (KTD-2): a mandatory fight cannot be
    escaped through a cancel unwind, so the driver maps the client's quit vocabulary
    — :data:`CANCEL`, and EOF, which a client surfaces as ``CANCEL`` — to a
    ``surrender``. Unrecognized responses simply re-prompt.

    ``prompt`` is ``"action"`` today; the field exists so a client that wants a
    separate aim step (the original reads the direction in a second GET at ``30205``)
    can be served without changing the interaction's type.

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
    """Run a nested sub-state (minigame / spec sheet) as a child generator (U1, KTD-1).

    When a parent handler yields this, the driver looks up the sub-state handler
    registered under ``kind`` in :data:`engine.substates.SUBSTATES`, drives it to
    completion sharing the parent's :class:`Ctx` (its effects/events merge into the
    parent action — one atomic boundary), and ``.send()``s its return value back
    into the parent as this interaction's response. An unknown ``kind`` is a config
    bug and raises :class:`ValueError`.
    """

    kind: Any
    params: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Per-side combat drivers (U6) — who answers each side's activations           #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Driver:
    """How ONE side's activations are answered inside :func:`_run_combat` (U6, R10/R11).

    The combat loop's dispatch is a **two-way branch on ``kind``**: suspend-and-ask
    (the human path — the ONLY path that ``yield``s a :class:`CombatScreen`) vs.
    call-and-continue (every other kind — the fight runs headlessly on that side).
    ``yield`` cannot cross a plain function call, so a "callable driver" can never
    suspend; that is exactly why ``human`` carries no callable and the rest do.

    Four kinds, all resolved without moving dispatch out of the generator frame:

    ``human``
        No callable. Its presence tells the loop to yield a :class:`CombatScreen`
        and read the response via the input source.
    ``ai``
        Calls ``fight.ai_decide(view)`` — the split-out chooser, no mutation.
    ``policy``
        Calls ``decide(view)``, a ``Callable[[CombatView], tuple[str, Any]]`` — a
        scripted or heuristic side with no client in the loop.
    ``replay``
        Reserved here so **U7 need not touch this dispatch again** (R15). It is a
        declared kind, not yet functional — a replay driver must NOT execute (the
        recorded result is the thing being reproduced), which is precisely why every
        kind obeys the same "choose, return, let one place apply" contract.

    A base :class:`Driver` is any of these; the concrete subclasses below set ``kind``
    and, where relevant, carry the callable.
    """

    kind: str = "human"

    def decide(self, view: Any) -> tuple[str, Any]:
        """Choose ``(action, argument)`` from a read-only ``CombatView``.

        The default raises — a plain ``human`` :class:`Driver` never decides (the
        loop yields a screen for it instead), and ``replay`` is not yet functional.
        The ``ai``/``policy`` subclasses override this.
        """
        raise NotImplementedError(f"{type(self).__name__}(kind={self.kind!r}) cannot decide")


@dataclass(frozen=True)
class HumanDriver(Driver):
    """A client-driven side: the loop yields a :class:`CombatScreen` and prompts."""

    kind: str = "human"


@dataclass(frozen=True)
class AiDriver(Driver):
    """A CPU side: :meth:`decide` defers to the fight's own ``ai_decide`` chooser."""

    kind: str = "ai"

    def decide(self, view: Any) -> tuple[str, Any]:
        # The view knows the fight it wraps; ai_decide is the fight's split-out
        # chooser, so an AiDriver holds no state of its own.
        return view._fight.ai_decide(view)


@dataclass(frozen=True)
class PolicyDriver(Driver):
    """A side driven by a supplied ``(CombatView) -> (action, argument)`` callable."""

    kind: str = "policy"
    policy: Any = None

    def decide(self, view: Any) -> tuple[str, Any]:
        if self.policy is None:
            raise ValueError("PolicyDriver has no policy callable")
        return self.policy(view)


@dataclass(frozen=True)
class ReplayDriver(Driver):
    """Reserved for U7 (R15). Declared so this dispatch need not change again.

    Not functional this unit: a replay driver reproduces a recorded transcript, and
    the recording/replay machinery is U7's. :meth:`decide` therefore raises with a
    clear message rather than guessing.
    """

    kind: str = "replay"

    def decide(self, view: Any) -> tuple[str, Any]:
        raise NotImplementedError("replay drivers are U7; not functional this unit")


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
    """Thrown INTO a handler (``gen.throw``) to unwind it on cancel (KTD-2, mechanism a).

    Handlers unwind via ``try/finally`` and do not need to catch this. The driver
    catches it as expected control flow and discards the action's effect buffer.
    """


# --------------------------------------------------------------------------- #
# Ctx — the handler's window into the engine (the U4/U5 seam)                 #
# --------------------------------------------------------------------------- #
class Ctx:
    """Per-handler-run context passed to the handler factory as ``handler(ctx)``.

    Exposes the *only* surface a handler may touch for state/rng/effects (the rest
    of the Handler API — ``yield`` and named helpers — lives elsewhere). ``apply``
    buffers effects; the driver decides whether that buffer commits or is discarded.
    U4 does not interpret effects (any object) — that is U5's ``apply(state, effect)``.
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
def run(
    handler: Callable[[Ctx], Generator[Any, Any, Any]],
    input_source: Callable[[Any], Any],
    *,
    state: Any = None,
    rng: Any = None,
) -> EngineResult:
    """Advance a handler generator to completion, mediating its interactions.

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
            context-appropriate response.
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

    A yielded ``StartCombat`` runs the combat sub-protocol (:func:`_run_combat`) and
    resolves with the winning side (U5) — it no longer raises.

    Note:
        Synchronous by construction. The SAME protocol is later driven by an async
        server (docs/design/engine-architecture.md); that transport is deliberately NOT built here.
    """
    ctx = Ctx(state=state, rng=rng)
    gen = handler(ctx)

    try:
        interaction = next(gen)  # prime the generator to its first yield
        while True:
            if isinstance(interaction, LoadSubState):
                # KTD-1: run the nested sub-state HERE (not in _resolve, which has no
                # handle to ctx or the parent generator). The child shares this ctx —
                # its ctx.apply/ctx.record append into the parent's buffers, so the
                # whole nesting commits or discards as ONE atomic action. A Cancelled
                # thrown at a child prompt propagates out of _run_substate up to the
                # `except Cancelled` below, unwinding the whole action.
                response = _run_substate(interaction, input_source, ctx)
                interaction = gen.send(response)
                continue
            if isinstance(interaction, StartCombat):
                # KTD-1: the combat sub-protocol runs HERE for the same reason
                # LoadSubState does — it shares this ctx, so a fight's effects
                # buffer into the parent action and commit (or discard) with it.
                response = _run_combat(interaction, input_source, ctx)
                interaction = gen.send(response)
                continue
            response = _resolve(interaction, input_source)
            if response is _CANCEL_SIGNAL:
                # A cancellable prompt was cancelled: unwind the handler. The throw
                # is owned here (not in _resolve) so that a handler which *catches*
                # Cancelled and continues fails loud instead of silently feeding its
                # next yielded Interaction back in as a response.
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
    except StopIteration as stop:
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


#: Private sentinel :func:`_resolve` returns to tell :func:`run` "cancel this action".
#: The driver — not :func:`_resolve` — owns the ``gen.throw(Cancelled())`` so a handler
#: that swallows ``Cancelled`` and continues fails loud rather than corrupting the stream.
_CANCEL_SIGNAL = object()


def _resolve(
    interaction: Any,
    input_source: Callable[[Any], Any],
) -> Any:
    """Obtain the Response to ``.send()`` for one interaction (validating/re-prompting).

    Returns the validated response value, or :data:`_CANCEL_SIGNAL` when a
    ``cancellable`` prompt was cancelled (the caller, :func:`run`, then unwinds
    the handler via ``gen.throw``). Never throws into the generator itself.
    """
    if isinstance(interaction, ShowMessage):
        # Display-only, but DELIVERY and RESPONSE are separate concerns (#43). The
        # message still has to reach the client — a driver that acks without handing
        # it over makes every handler's narration structurally invisible. So the
        # input source SEES it, and its return value is DISCARDED: nothing a client
        # returns (CANCEL included) can turn narration into a cancel path or feed a
        # fabricated response back into the handler. Ack is sent unconditionally.
        input_source(interaction)
        return Ack

    if isinstance(interaction, StartCombat):
        # StartCombat is a SUB-PROTOCOL, not a single request/response: it is handled
        # by run()/_run_substate before _resolve is ever consulted (like LoadSubState).
        # Reaching here means a new yield path bypassed that dispatch.
        raise AssertionError(
            "StartCombat must be dispatched by the driver's combat loop, not resolved "
            "as a single interaction (KTD-1)."
        )

    if isinstance(interaction, PromptInt):
        while True:
            raw = input_source(interaction)
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
            raw = input_source(interaction)
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
        raw = input_source(interaction)
        # Confirm is not itself cancellable in this catalog; coerce to a bool.
        return bool(raw)

    raise TypeError(f"Unknown interaction type: {type(interaction).__name__!r}")


def _run_substate(
    load: "LoadSubState",
    input_source: Callable[[Any], Any],
    ctx: "Ctx",
) -> Any:
    """Drive a nested sub-state generator to completion and return its value (KTD-1).

    Looks up the sub-state factory registered under ``load.kind`` in
    :data:`engine.substates.SUBSTATES`, builds the child generator sharing the
    parent's ``ctx`` (shared-buffer model — child ``ctx.apply``/``ctx.record`` append
    into the parent's buffers), and drives it with an INLINE loop reusing
    :func:`_resolve` for its interactions. This is deliberately NOT a nested
    :func:`run` call: a nested ``run`` would commit/discard the child's effects
    independently and break cross-boundary atomicity.

    On the child's clean completion, returns its ``StopIteration.value`` (the value
    :func:`run` then ``.send()``s into the parent). A ``Cancelled`` thrown at a
    cancellable child prompt is raised INTO the child (so its ``try/finally``
    unwinds) and then **propagates out of this function** to :func:`run`'s
    ``except Cancelled`` handler — one discard unwinds the whole nesting.

    Raises:
        ValueError: if ``load.kind`` is not registered (a config bug).
        AssertionError: if the child yields ``StartCombat`` — combat is only ever
            yielded from top-level handlers this slice (KTD-1).
    """
    from engine.substates import SUBSTATES

    factory = SUBSTATES.get(load.kind)
    if factory is None:
        raise ValueError(f"unknown sub-state kind {load.kind!r}; registered: {sorted(SUBSTATES)}")

    child = factory(ctx, load.params)
    interaction = next(child)  # prime the child to its first yield
    while True:
        # KTD-1: combat is only ever yielded from TOP-LEVEL handlers this slice. The
        # driver asserts that rather than supporting nesting, because a fight inside a
        # sub-state would need cancel semantics ("combat is non-cancellable" vs. "a
        # cancelled sub-state unwinds the whole action") that this slice has not
        # decided. Failing loud here beats silently picking one.
        assert not isinstance(interaction, StartCombat), (
            "StartCombat inside a sub-state is not supported this slice (KTD-1): "
            "yield combat from a top-level handler."
        )
        response = _resolve(interaction, input_source)
        if response is _CANCEL_SIGNAL:
            # Cancel INSIDE the sub-state: unwind the child, then let Cancelled
            # propagate up to run()'s handler so the whole action discards atomically.
            child.throw(Cancelled())
            raise RuntimeError(
                "sub-state handler caught Cancelled and continued; cancellation "
                "must unwind the handler (do not catch Cancelled and keep yielding)"
            )
        try:
            interaction = child.send(response)
        except StopIteration as stop:
            return stop.value


def _run_combat(
    start: "StartCombat",
    input_source: Callable[[Any], Any],
    ctx: "Ctx",
) -> "CombatResult":
    """Drive a full fight to a winner and return the winning side (KTD-1/KTD-2).

    The combat sub-protocol, structurally a sibling of :func:`_run_substate`: an
    INLINE loop over the parent's ``ctx`` (so the fight's effects buffer into the
    parent action and commit or discard with it), never a nested :func:`run` call
    (which would commit the fight independently and break that atomicity).

    Ports the activation loop ``mf-prg.bas:30100-30155``. Each pass:

    1. Check victory (``30106``) — the fight ends the moment one side has no
       standing fighter, mid-round, without finishing the current side's turn.
    2. If the active side is CPU-controlled (``30110``: ``ifks(s)=0``), run the AI
       decision routine (:meth:`engine.combat.CombatFight.ai_take_turn`, ports
       ``30400-30492``) and advance — **no** :class:`CombatScreen` is yielded and the
       client is never prompted for that side. Otherwise:
    3. Yield a :class:`CombatScreen` for the active fighter and read one action.
    4. Apply exactly ONE action per activation (``30130-30155``): a legal move, a
       shot, a pass, or a surrender. An ILLEGAL move or an unrecognized response
       re-prompts the SAME activation (``30145``/``30139`` jump back to ``30125``)
       rather than consuming it.
    5. Advance the cursor (``30105``), skipping downed fighters (``30109``).

    **Non-cancellable (KTD-2).** :data:`CANCEL` — which is also how a client
    surfaces EOF — is mapped to a surrender, never to a ``Cancelled`` throw. A
    mandatory fight must not be escapable through a cancel unwind, so this loop
    deliberately does not consult the cancel path that :func:`_resolve` owns.

    The invoking handler owns the fight's ENTRY-POINT consequence (KTD-1 — "losing a
    fight carries only the entry point's consequence": debt seizure, job pay, reward,
    etc.) — that is still on the caller. But the fight's OWN persistent side-effect on
    the roster — energy spent, fighters knocked down — is NOT entry-point-specific, it
    is true of every fight regardless of who triggered it, so this function buffers it
    directly (#44): before returning, it diffs side 1's (the acting player's roster,
    ``mf-prg.bas:5010``'s ``ks(1)=sp`` — SpawnFighter's docstring pins this convention)
    per-fighter energy against its pre-fight snapshot and buffers one
    :class:`~engine.effects.EnergyChange` per fighter whose energy changed, in roster
    order (1:1 with ``start.sides[0]``, since :func:`engine.combat.build_player_side`
    never reorders the roster). ``cap`` is set to the fighter's OWN pre/post energy
    ceiling (never a fresh regen-cap computation) so the clamp in
    ``engine.effects._apply``'s ``EnergyChange`` branch is a structural no-op here —
    combat only ever LOWERS energy this slice (no mid-fight healing exists), so the
    post-fight value is by construction the correct final value, not merely a floor.
    Side 2 (the enemy party) is NPC working state, never a roster, so it is not
    persisted here. A no-op fight (no side-1 fighter's energy moved, e.g. a
    zero-activation surrender before anyone was struck) buffers nothing.
    """
    # Lazy imports keep this module's top-level import graph free of engine.state /
    # engine.combat, mirroring the commit() import in run().
    from engine.combat import CombatResult
    from engine.effects import EnergyChange

    fight = _build_fight(start, rng=ctx.rng)
    drivers = _resolve_drivers(start)
    # The depleting resource is the engine's ``vitality`` SLOT (amendment A5) — the
    # driver reads it directly and never spells this game's word for it. Every Fighter
    # carries the slot, so there is nothing to guard: a bundle-less fight (surrendered
    # without a shot) simply sees an unchanged ``vitality`` and buffers no delta.
    pre_vitality = [f.vitality for f in fight.sides[0]]

    # The activation loop is the SHARED one (:func:`_drive_fight`) so `_run_combat` and
    # `simulate` cannot drift — the same loop, whether a client is in it or not.
    winner = _drive_fight(fight, drivers, input_source)

    # #44 — buffer the roster's persistent energy/down consequence BEFORE handing
    # the winner back, so it commits atomically with the invoking handler's own
    # entry-point effects (one shared ctx, one atomic buffer).
    for i, f in enumerate(fight.sides[0]):
        now = f.vitality
        if now == pre_vitality[i]:
            continue
        # Address the gangster this fighter IS, not the slot it happens to sit in
        # (amendment A1). These coincide today because build_player_side maps roster
        # order onto placement order 1:1 — but a fighter without a roster entry must
        # not write to gangster 0 just because it is first.
        if f.roster_id is None:
            continue
        ctx.apply(
            EnergyChange(
                amount=now - pre_vitality[i],
                cap=max(pre_vitality[i], now),
                gangster=f.roster_id,
            )
        )
    # R8/U3: hand back the winner AND the real per-side death tallies (v(1)/v(2)).
    return CombatResult(winner=winner, losses=fight.losses)


def _build_fight(start: "StartCombat", *, rng: Any) -> Any:
    """Construct the :class:`~engine.combat.CombatFight` a ``StartCombat`` describes.

    Prefers ``start.scenario`` (amendment A6 — the whole payload in one field); when
    absent, falls back to the four legacy fields so every pre-U6 call site is
    unchanged. Shared by :func:`_run_combat` and :func:`simulate`.
    """
    from engine.combat import CombatFight
    from engine.state import CombatState

    scenario = start.scenario
    if scenario is not None:
        sides = scenario.sides
        grid = scenario.grid
        rules = scenario.rules
        dir_memory = scenario.dir_memory
    else:
        sides = start.sides
        grid = start.grid
        rules = start.rules
        dir_memory = start.dir_memory
    return CombatFight(
        CombatState(
            sides=sides,
            grid=tuple(grid or ()),
            dir_memory=dict(dir_memory or {}),
        ),
        rng=rng,
        rules=rules,
    )


def _resolve_drivers(start: "StartCombat") -> "dict[int, Driver]":
    """The ``{side: Driver}`` map for a fight — explicit map OVERRIDES ``cpu_sides``.

    An explicit ``start.drivers`` wins outright (two knobs on one axis never merge —
    plan §U6). Otherwise the map is derived from ``cpu_sides``: an :class:`AiDriver`
    for a CPU side, a :class:`HumanDriver` for a client side. ``cpu_sides is None``
    means the default (side 2 is CPU); an explicit ``()`` means a fully hot-seat fight.
    """
    from engine.combat import DEFAULT_CPU_SIDES

    if start.drivers is not None:
        return dict(start.drivers)
    cpu_sides = DEFAULT_CPU_SIDES if start.cpu_sides is None else tuple(start.cpu_sides)
    return {s: (AiDriver() if s in cpu_sides else HumanDriver()) for s in (1, 2)}


def _drive_fight(
    fight: Any,
    drivers: "Mapping[int, Driver]",
    input_source: Callable[[Any], Any],
) -> int:
    """Advance a fight to a winner, one activation at a time — the SHARED loop (U6).

    Ports the activation loop ``mf-prg.bas:30100-30155``, driver-agnostic:

    1. Victory check (``30106``) — the fight ends the moment one side has no standing
       fighter, mid-round, without finishing the current side's turn.
    2. Pick ``drivers[fight.active_side]``. If it is ``human``, yield a
       :class:`CombatScreen` and read one response via ``input_source`` (the ONLY
       suspending path — "headless" means no yield occurs on a non-human side's turn).
       Otherwise the driver ``decide``s ``(action, argument)`` with NO client in the
       loop.
    3. Apply exactly ONE action per activation through the SINGLE apply-block
       (:meth:`~engine.combat.CombatFight.apply_action`), whoever chose it — so a shot
       fires once. An illegal move or unrecognized response from a HUMAN re-prompts the
       same activation (``30145``/``30139``) rather than consuming it.
    4. Advance the cursor (``30105``), skipping downed fighters (``30109``).

    Returns the winning side. Records the fight's result via :meth:`CombatFight.finish`
    / :meth:`surrender` so ``fight.result_flag`` and ``fight.losses`` are final.

    **Non-cancellable (KTD-2).** :data:`CANCEL` / EOF at a human prompt is mapped to a
    surrender, never a ``Cancelled`` throw — a mandatory fight must not be escapable.

    A ``human`` driver on a side reached during a **headless** run (``input_source is
    None``) is a caller error: :func:`simulate` rejects human drivers up front, so this
    loop can assume a human side always has a usable ``input_source``.
    """
    message: Any = None
    while True:
        winner = fight.winner()
        if winner is not None:
            return fight.finish(winner)

        driver = drivers[fight.active_side]

        if driver.kind == "human":
            screen = CombatScreen(
                sides=fight.sides,
                grid=fight.grid,
                active_side=fight.active_side,
                active_fighter=fight.active_fighter,
                losses=fight.losses,
                prompt="action",
                message=message,
            )
            raw = input_source(screen)
            message = None
            action, argument = _parse_combat_response(raw)
        else:
            # ai / policy / replay — DECIDE ONLY, off a read-only view. No yield, no
            # client. The action falls into the SAME apply-block below.
            action, argument = driver.decide(fight.view())

        if action == "surrender":
            return fight.surrender()
        if action == "pass":
            fight.advance_activation()
            continue
        if action == "move":
            committed = fight.apply_action(
                "move", argument, record_dir_memory=driver.kind != "human"
            )
            if not committed:
                # 30145: illegal target. A HUMAN re-prompts the same activation; a
                # non-human driver that returns an illegal step has a broken decide
                # contract and must fail loudly rather than re-decide the same illegal
                # step forever (headless, there is no client to break the loop).
                if driver.kind == "human":
                    message = "illegal_move"
                    continue
                raise ValueError(
                    f"{driver.kind!r} driver on side {fight.active_side} returned an illegal "
                    f"move ({argument!r}); a non-human driver's chooser must pre-validate steps"
                )
            fight.advance_activation()
            continue
        if action == "shoot":
            result = fight.apply_action("shoot", argument)
            message = result
            winner = fight.winner()
            if winner is not None:
                return fight.finish(winner)
            fight.advance_activation()
            continue
        # 30139: an unrecognized key. A HUMAN falls back to the GET wait and re-prompts;
        # a non-human driver that returns an unknown action has a broken decide contract
        # and must fail loudly — re-prompting it headlessly would spin forever.
        if driver.kind == "human":
            message = "unknown_action"
            continue
        raise ValueError(
            f"{driver.kind!r} driver on side {fight.active_side} returned an unrecognized "
            f"action {action!r}; a driver's decide contract must return a known action"
        )


def simulate(
    scenario: Any,
    drivers: "Mapping[int, Driver]",
    *,
    rng: Any = None,
) -> "CombatResult":
    """Run a fight to a result HEADLESSLY, with no client and no ``GameState`` (U6).

    The programmatic sibling of the handler-driven :func:`_run_combat`: a caller hands
    it a :class:`~engine.scenario.Scenario` and an explicit ``{side: Driver}`` map and
    gets back U3's :class:`~engine.combat.CombatResult` (winner + real per-side losses)
    — the SAME value a fight run through a handler yields, so a caller cannot tell which
    path produced it. There is no ``cpu_sides`` sugar here; this is the explicit entry
    point, so the driver map is required and complete.

    It drives the fight through the SAME shared activation loop (:func:`_drive_fight`)
    as the handler path, so the two cannot drift. It builds no :class:`Ctx` and buffers
    no effects — a headless simulation has no roster to persist energy back onto (that
    is :func:`_run_combat`'s #44 concern, tied to the invoking handler's shared ctx).

    ``rng`` seeds the fight's draws; when ``None`` and the scenario carries a ``seed``,
    a fresh seeded :class:`~engine.rng.Rng` is built from it, which is what makes a
    seeded simulation reproducible (same seed -> same transcript -> same result). A
    zero-variance weapon needs no rng at all.

    **Raises loudly on a human side.** A :class:`HumanDriver` cannot run headlessly (it
    would need a client to suspend to), so this raises a clear ``ValueError`` naming the
    side — a distinct failure from a scripted driver returning a bad action, and from an
    exhausted answer list (which the driver's own callable surfaces).
    """
    from engine.combat import CombatResult

    human_sides = [s for s, d in drivers.items() if getattr(d, "kind", None) == "human"]
    if human_sides:
        raise ValueError(
            f"simulate() cannot run a HumanDriver headlessly (sides {sorted(human_sides)}); "
            f"a human side needs a client — run it through a handler/`run` instead"
        )

    if rng is None and getattr(scenario, "seed", None) is not None:
        from engine.rng import Rng

        rng = Rng(seed=scenario.seed)

    fight = _build_fight(StartCombat(scenario=scenario), rng=rng)
    # No input_source: a headless run never reaches the human/yield branch (guarded
    # above), so the loop never consults it.
    winner = _drive_fight(fight, dict(drivers), _no_input_source)
    return CombatResult(winner=winner, losses=fight.losses)


def _no_input_source(interaction: Any) -> Any:
    """The input source a headless :func:`simulate` hands the loop — never called.

    :func:`simulate` rejects human drivers up front, so :func:`_drive_fight`'s only
    suspending branch is unreachable; if it is ever reached, this raises rather than
    silently returning a value that would be misread as a client answer.
    """
    raise AssertionError(
        "headless simulate() reached the client-prompt path — a human driver slipped past "
        "the up-front guard"
    )


def _parse_combat_response(raw: Any) -> tuple[str, Any]:
    """Normalize a client's combat response into ``(action, argument)``.

    Accepts the ``(action, argument)`` pair the protocol specifies, a bare action
    string for the argument-less actions, and maps :data:`CANCEL` (a client's quit /
    EOF vocabulary) to a surrender per KTD-2. Anything else returns an unknown action
    so the loop re-prompts rather than guessing.
    """
    if raw is CANCEL or raw is None:
        return ("surrender", None)
    if isinstance(raw, str):
        return (raw, None)
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        return (str(raw[0]), raw[1])
    if isinstance(raw, (tuple, list)) and len(raw) == 1:
        return (str(raw[0]), None)
    return ("__unknown__", None)


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

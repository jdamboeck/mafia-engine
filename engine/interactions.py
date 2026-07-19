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

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from engine.actions import EngineResult, HandlerResult

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
    # Driver
    "Ctx",
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
    ``weapon_stats``
        Mapping ``weapon id -> (ts, tg)`` from the config's weapon table. Passed in
        because the engine never reads a config's entity tables itself.
    ``dir_memory``
        Optional per-enemy-fighter direction memory seed (``ri()``), consumed by
        U6's AI; harmless to omit.

    Combat is only ever yielded from a TOP-LEVEL handler this slice (KTD-1) — the
    driver asserts this rather than supporting it inside :func:`_run_substate`.
    """

    sides: Any = ((), ())
    grid: Any = ()
    weapon_stats: Any = None
    dir_memory: Any = None


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
            "fighter": self._active_fighter_panel(),
        }

    def _active_fighter_panel(self) -> Any:
        """The active fighter's own stats/weapon panel (``mf-prg.bas:30115-30116``)."""
        from engine.state import json_safe

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
            triggers a re-prompt consults it again. ``ShowMessage`` never consults it
            (the driver auto-acks). This callable shape lets tests both assert on the
            presented interaction and return a context-appropriate response.
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
        # Display-only: auto-ack without consulting the input source.
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
        raise ValueError(
            f"unknown sub-state kind {load.kind!r}; "
            f"registered: {sorted(SUBSTATES)}"
        )

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
) -> int:
    """Drive a full fight to a winner and return the winning side (KTD-1/KTD-2).

    The combat sub-protocol, structurally a sibling of :func:`_run_substate`: an
    INLINE loop over the parent's ``ctx`` (so the fight's effects buffer into the
    parent action and commit or discard with it), never a nested :func:`run` call
    (which would commit the fight independently and break that atomicity).

    Ports the activation loop ``mf-prg.bas:30100-30155``. Each pass:

    1. Check victory (``30106``) — the fight ends the moment one side has no
       standing fighter, mid-round, without finishing the current side's turn.
    2. Yield a :class:`CombatScreen` for the active fighter and read one action.
    3. Apply exactly ONE action per activation (``30130-30155``): a legal move, a
       shot, a pass, or a surrender. An ILLEGAL move or an unrecognized response
       re-prompts the SAME activation (``30145``/``30139`` jump back to ``30125``)
       rather than consuming it.
    4. Advance the cursor (``30105``), skipping downed fighters (``30109``).

    **Non-cancellable (KTD-2).** :data:`CANCEL` — which is also how a client
    surfaces EOF — is mapped to a surrender, never to a ``Cancelled`` throw. A
    mandatory fight must not be escapable through a cancel unwind, so this loop
    deliberately does not consult the cancel path that :func:`_resolve` owns.

    Effects are NOT buffered here: this unit resolves the fight and hands the winner
    back to the invoking handler, which owns the entry-point consequence (KTD-1 —
    "losing a fight carries only the entry point's consequence"). The shared ``ctx``
    is threaded through so a later unit can buffer roster energy/down deltas from
    inside the loop without changing this function's contract.
    """
    # Lazy imports keep this module's top-level import graph free of engine.state /
    # engine.combat, mirroring the commit() import in run().
    from engine.combat import CombatFight
    from engine.state import CombatState

    fight = CombatFight(
        CombatState(
            sides=start.sides,
            grid=tuple(start.grid or ()),
            dir_memory=dict(start.dir_memory or {}),
        ),
        rng=ctx.rng,
        weapon_stats=start.weapon_stats,
    )

    message: Any = None
    while True:
        winner = fight.winner()
        if winner is not None:
            return fight.finish(winner)

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

        if action == "surrender":
            return fight.surrender()
        if action == "pass":
            fight.advance_activation()
            continue
        if action == "move":
            if not fight.try_move(argument):
                # 30145: illegal target -> back to the key read; activation intact.
                message = "illegal_move"
                continue
            fight.advance_activation()
            continue
        if action == "shoot":
            message = fight.shoot(argument)
            winner = fight.winner()
            if winner is not None:
                return fight.finish(winner)
            fight.advance_activation()
            continue
        # 30139: an unrecognized key falls back to the GET wait — re-prompt.
        message = "unknown_action"


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

"""The interaction protocol + in-process synchronous driver — THE SPINE (docs/design/engine-architecture.md).

A **handler** is a factory ``(ctx) -> Generator[Interaction, Response, list[Event]]``.
It ``yield``s a typed **Interaction**; the **driver** turns that into an obtained
**Response** and ``.send()``s it back. The driver has two forms: :func:`step`, a
generator that yields every interaction (a sub-state's and a fight's included) to
whoever drives it, and :func:`run`, a pull loop over ``step`` that asks an input
callback for each answer. This same request/response protocol is,
unchanged, the eventual network message protocol — the WebSocket server (docs/design/engine-architecture.md) is
merely an async transport driving the identical generators. This module holds
:class:`Ctx` and the in-process synchronous driver (:func:`step`/:func:`run`, with the
:class:`LoadSubState` sub-state loop); the interaction catalog and the response/cancel
sentinels live in :mod:`engine.protocol`, and this module re-exports them. A
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
from dataclasses import replace
from typing import TYPE_CHECKING, Any, overload

from engine.actions import EngineResult, HandlerResult
from engine.config_owner import ConfigOwner, check_owner
from engine.protocol import (
    BEGINS_MESSAGE,
    CANCEL,
    MAP_DIRECTIONS,
    MAP_EXIT,
    MAP_QUIT,
    MAP_SAVE,
    OBSERVE_PROMPT,
    READ_KEY,
    READ_LINE,
    Ack,
    Acknowledge,
    Cancelled,
    CombatScreen,
    Confirm,
    Heading,
    InputExhausted,
    Interaction,
    LoadSubState,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptChoice,
    PromptInt,
    PromptText,
    RollFrame,
    ShowMessage,
    StartCombat,
    TurnMenu,
)

if TYPE_CHECKING:  # typing only: keep engine.state out of this module's import graph
    from engine.state import GameState

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
    "Ctx",
    "step",
    "run",
]


# --------------------------------------------------------------------------- #
# Ctx — the handler's window into the engine                                  #
# --------------------------------------------------------------------------- #
class Ctx:
    """Per-handler-run context passed to the handler factory as ``handler(ctx)``.

    Exposes the *only* surface a handler may touch for state/rng/effects (the rest
    of the Handler API — ``yield`` and named helpers — lives elsewhere). ``apply``
    buffers effects; the driver decides whether that buffer commits or is discarded.
    ``Ctx`` does not interpret effects (any object); :func:`engine.effects.apply` does.

    ``owner`` is the config the action runs for (:func:`step`'s ``owner``), engine-side
    only: a handler never reads it.
    """

    def __init__(
        self, state: Any = None, rng: Any = None, *, owner: ConfigOwner | None = None
    ) -> None:
        self._state = state
        self._rng = rng
        self._owner = owner
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
    owner: ConfigOwner | None = None,
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

    ``owner`` is the config the action belongs to (the turn runner passes its
    registries' owner). It is checked when the action starts, before the handler is
    called: once another config has loaded, the sub-states, guard variables,
    consequences and stat names the action would read are that config's, so ``step``
    raises :class:`~engine.config_owner.StaleConfigError` instead. With no owner
    (engine code, a test-only sub-state) nothing is checked.
    """
    check_owner(owner)
    ctx = Ctx(state=state, rng=rng, owner=owner)
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
            ``Confirm``/``PromptText``/``RollFrame``). It is consulted once per attempt, so an invalid answer that
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
        InputExhausted: if ``input_source`` raises ``StopIteration`` (it ran dry)
            before the action ends, mid-fight or inside a sub-state included. The
            unfinished action commits nothing and no result comes back.

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
            except StopIteration:
                # An input source that raises StopIteration (a scripted answer list
                # run dry) leaves the action unfinished. Closing ``step`` raises
                # GeneratorExit at its suspension point, which none of its branches
                # catches, so its buffer is never committed; the dry source is an
                # error, never a result.
                steps.close()
                raise InputExhausted(interaction) from None
            interaction = steps.send(response)
    except StopIteration as stop:
        return stop.value


def _active_player(state: Any) -> int | None:
    """The active player's index in ``state``, or ``None`` when it has no turn clock."""
    clock = getattr(state, "clock", None)
    return getattr(clock, "active_player", None)


def _addressed(inner: Generator[Any, Any, Any], active: int | None) -> Generator[Any, Any, Any]:
    """Relay ``inner``'s interactions, filling each unnamed ``player`` with ``active``.

    Answers are sent back to ``inner`` unchanged and its return value is returned.
    """
    try:
        interaction = next(inner)
        while True:
            if active is not None and getattr(interaction, "player", active) is None:
                interaction = replace(interaction, player=active)
            interaction = inner.send((yield interaction))
    except StopIteration as stop:
        return stop.value


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
            if interaction.blank is not None and isinstance(raw, str) and not raw.strip():
                return interaction.blank  # an empty answer stands for ``blank``
            value = _coerce_int(raw, spaced_sign=interaction.spaced_sign)
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

    if isinstance(interaction, PromptText):
        while True:
            raw = yield interaction
            if raw is CANCEL:
                continue  # not cancellable → re-prompt, as at a non-cancellable PromptInt
            # No validation: the string as given (a config handler applies any rule).
            return raw if isinstance(raw, str) else str(raw)

    if isinstance(interaction, RollFrame):
        raw = yield interaction
        # Not cancellable: CANCEL (end of input) stops the roll on the shown frame.
        if raw is CANCEL:
            return True
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
    :func:`step` then ``.send()``s into the parent) -- also when the child returns
    before its first yield. A ``Cancelled`` thrown at a
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
    try:
        interaction = next(child)  # prime the child to its first yield
    except StopIteration as stop:  # a child that returns before it asks anything
        return stop.value
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


def _coerce_int(raw: Any, *, spaced_sign: bool = False) -> int | None:
    """Return ``raw`` as an int if it is an integer value, else ``None``.

    Accepts genuine ``int`` (but not ``bool``, which is an ``int`` subclass) and
    plain ASCII decimal strings like ``"4"`` / ``"-5"`` / ``"+7"`` (optionally
    surrounded by whitespace); with ``spaced_sign``, blanks between the sign and the
    digits too (``"- 3"`` is -3). Everything else — floats, non-numeric strings,
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
        sign = s[:1] if s[:1] in ("+", "-") else ""
        body = s[len(sign) :]
        if spaced_sign:
            body = body.lstrip(" ")
        if not body.isascii() or not body.isdigit():
            return None
        return int(sign + body)
    return None

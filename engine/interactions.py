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
    """Combat entry (out of scope this slice; the driver raises NotImplementedError)."""

    fighters: Any
    arena: Any


@dataclass(frozen=True)
class LoadSubState:
    """Minigame / nested sub-state (out of scope; the driver raises NotImplementedError)."""

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
        NotImplementedError: if the handler yields ``StartCombat`` or ``LoadSubState``
            (combat / minigames are out of scope for this slice).

    Note:
        Synchronous by construction. The SAME protocol is later driven by an async
        server (docs/design/engine-architecture.md); that transport is deliberately NOT built here.
    """
    ctx = Ctx(state=state, rng=rng)
    gen = handler(ctx)

    try:
        interaction = next(gen)  # prime the generator to its first yield
        while True:
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

    if isinstance(interaction, (StartCombat, LoadSubState)):
        raise NotImplementedError(
            f"{type(interaction).__name__} is out of scope for this slice; "
            "the driver does not run combat / sub-state minigames yet."
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

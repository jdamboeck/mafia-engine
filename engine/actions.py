"""The action/result spine: typed outcomes of running one option (T1).

Running an option (``run_option``) produces exactly one :class:`EngineResult`. It bundles
the four things a caller needs after an action: the resulting :class:`GameState`, the
**semantic events** emitted (audit/UI records — never applied to state), the **primitive
effects** committed (the only things that mutated state), and a :data:`EngineStatus`
naming the outcome. An optional ``payload`` carries handler-return data; ``error`` carries
a future recoverable runtime error.

Events vs. effects is the load-bearing distinction (docs/design/engine-architecture.md
§5.5): effects mutate state and double as replay records; events are pure audit/UI records
that may exist with no matching effect. This module only *carries* both — it never applies
either (application lives in :mod:`engine.effects`).

**Error semantics.** ``status="error"`` and the ``error`` field exist for *future
recoverable runtime errors* only. Programmer, config, and unexpected handler errors RAISE
— they are never funnelled into an :class:`EngineResult`.

Pure data + stdlib only; the ``engine/`` package imports nothing from ``server``,
``clients``, or any transport/render library, and holds no display text.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from engine.state import GameState

if TYPE_CHECKING:  # avoid importing the shell/driver at module load (layering + cycles)
    from engine.locations import Location

#: The mutually-exclusive outcomes of running one option. ``"error"`` is reserved for a
#: future recoverable runtime error (see the module docstring); bugs raise instead.
EngineStatus = Literal[
    "completed",
    "blocked",
    "cancelled",
    "turn_over",
    "needs_input",
    "started_combat",
    "not_implemented",
    "error",
]


@dataclass(frozen=True)
class EngineResult:
    """The outcome of running one option: new state + emitted events/effects + status.

    ``events`` are semantic (audit/UI) records — never applied to state. ``effects`` are
    the primitive, committed mutations (they double as replay records). ``payload`` is
    optional handler-return data; ``error`` is a future recoverable runtime error (bugs
    raise instead of populating it).
    """

    state: GameState
    events: list
    effects: list
    status: EngineStatus
    payload: Any = None
    error: Exception | None = None


@dataclass(frozen=True)
class HandlerResult:
    """A handler's return value, carried out of the driver (``StopIteration.value``)."""

    returned: Any = None


@dataclass(frozen=True)
class DeniedResult:
    """An option denied by its guard before any handler ran.

    ``reason_key`` is a theme-resolvable message key (never player-facing text itself).
    ``guard`` is the failing guard's debug/internal representation — diagnostic data, not
    player-facing UI data.
    """

    location_key: str
    option_id: str
    reason_key: str | None = None
    guard: dict | None = None


def run_option(
    location: "Location",
    option_id: str,
    state: GameState,
    *,
    ln: int | None,
    input_source=None,
    rng=None,
) -> EngineResult:
    """Run one location option end-to-end, producing exactly one :class:`EngineResult`.

    This is the single, location-aware dispatcher over the three option shapes. It owns
    guard evaluation/denial and the *generic location lifecycle events* — the bare
    :func:`engine.interactions.run` driver stays handler-protocol-focused and
    location-agnostic, so lifecycle events are appended HERE, never by ``run``.

    Dispatch:

    1. **Unknown ``option_id``** -> raises :class:`ValueError` (a programmer/config bug,
       not a recoverable runtime outcome).
    2. **Guard fails** -> a ``"blocked"`` result: an :class:`~engine.events.OptionDenied`
       event and a :class:`DeniedResult` payload, ZERO effects, and the UNCHANGED input
       ``state`` object (denial is a normal outcome; the handler is never entered — KTD-8).
    3. **Consequence option** -> the raw dicts are converted (strictly, via
       :func:`engine.consequences.effects_from_dicts`) and committed
       (:func:`engine.effects.commit`); returns ``"completed"`` with the committed effects,
       the new state, and a :class:`~engine.events.LocationActionCompleted` event.
    4. **Handler option** -> delegates to :func:`engine.interactions.run` (which requires an
       ``input_source``), then APPENDS the generic lifecycle event to the driver's result:
       :class:`~engine.events.LocationActionCompleted` on clean completion,
       :class:`~engine.events.LocationActionCancelled` on driver-cancel.

    ``ln`` (the within-location tile index) is threaded into the guard evaluation context
    exactly as :func:`engine.locations.available_options` does (via
    :func:`engine.conditions.build_context`), so ``ln``-sensitive guards such as
    ``tenancy`` resolve against the tile actually entered. Handlers read ``ln`` off state
    (the active player's ``last_location`` seam), so ``ln`` here only feeds the guard.

    Imports of the shell/driver/consequence machinery are LOCAL to keep this module's
    top-level import graph minimal (it defines the result types those modules depend on).
    """
    # Local imports (see docstring): avoids a top-level dependency on the shell/driver.
    from engine.conditions import build_context, evaluate
    from engine.consequences import effects_from_dicts
    from engine.effects import commit
    from engine.events import (
        LocationActionCancelled,
        LocationActionCompleted,
        OptionDenied,
    )
    from engine.interactions import run

    option = next((o for o in location.options if o.id == option_id), None)
    if option is None:
        raise ValueError(
            f"unknown option id {option_id!r} for location {location.key!r}; "
            f"known: {[o.id for o in location.options]}"
        )

    # --- guard (same evaluation path as available_options) ------------------ #
    context = build_context(state, ln)
    if not evaluate(option.guard, context):
        return EngineResult(
            state=state,  # the UNCHANGED input object (denial mutates nothing)
            events=[
                OptionDenied(
                    location_key=location.key,
                    option_id=option_id,
                    reason_key=option.on_denied,
                )
            ],
            effects=[],
            status="blocked",
            payload=DeniedResult(
                location_key=location.key,
                option_id=option_id,
                reason_key=option.on_denied,
                guard=option.guard,
            ),
        )

    # --- consequence option: convert + commit ------------------------------- #
    if option.consequences is not None:
        effects = effects_from_dicts(option.consequences)
        commit_result = commit(state, effects)
        return EngineResult(
            state=commit_result.state,
            events=[
                LocationActionCompleted(
                    location_key=location.key, option_id=option_id
                )
            ],
            effects=commit_result.effects,
            status="completed",
        )

    # --- handler option: delegate to the driver, then append lifecycle ------ #
    if input_source is None:
        raise ValueError(
            f"option {option_id!r} on location {location.key!r} has a handler; "
            "run_option requires an input_source to drive it"
        )
    result = run(option.handler, input_source, state=state, rng=rng)

    if result.status == "cancelled":
        lifecycle = LocationActionCancelled(
            location_key=location.key, option_id=option_id
        )
    else:
        # Clean completion. slw.rent's rejection paths (x<=0, insufficient cash) return
        # with status="completed" and NO machine-readable rejection marker, so there is
        # no unambiguous signal to emit LocationActionRejected — treat as completed
        # (see the T7 report note).
        lifecycle = LocationActionCompleted(
            location_key=location.key, option_id=option_id
        )
    return dataclasses.replace(result, events=[*result.events, lifecycle])

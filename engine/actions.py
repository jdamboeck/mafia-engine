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

from dataclasses import dataclass
from typing import Any, Literal

from engine.state import GameState

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

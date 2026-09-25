"""Typed, serializable **semantic events** — audit/UI records (docs/design/engine-architecture.md §5.5).

An **event** is a pure-data record of *something that happened* in the simulation, emitted
for audit trails and UI. Events are the counterpart of the primitive **effects** in
:mod:`engine.effects`, and the distinction between them is load-bearing:

- **Effects** mutate ``GameState`` and double as the **replay log** (replaying committed
  effects — plus the logged RNG draws — reconstructs a game deterministically).
- **Events** are NEVER applied to state and are **NOT** part of the replay log. They are
  audit/UI records only. An event may exist with **no** matching effect: a blocked move
  emits :class:`MoveBlocked` while mutating nothing at all.

Because events never drive state, they carry no behavior and this module imports no
state-application machinery — no ``GameState``, no ``apply``. Every event is a frozen
dataclass (immutable pure data).

**Machine keys, no display text.** ``reason`` / ``reason_key`` are machine-readable keys
(e.g. ``"turn_over"``, ``"oob"``, ``"wall"``); themes resolve them to player-facing text.
The engine holds zero display strings.

**No raw guard data on events.** :class:`OptionDenied` names *that* an option was denied
and an optional machine ``reason_key`` — never the failing guard's debug representation.
Guard diagnostics live on ``DeniedResult`` in :mod:`engine.actions`, not here.

**No content-specific events.** This is the generic catalog; a config never adds its own
event type (e.g. no ``RoomRented``) — content records itself through this vocabulary.

**Coordinate space.** Cell fields are city-map cells on the 40×25 grid; ``la`` is a
location id; ``ln`` is a within-location tile index 1..9 (CLAUDE.md state gotchas).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MoveStep",
    "EnterLocation",
    "MoveBlocked",
    "OptionDenied",
    "LocationActionCompleted",
    "LocationActionRejected",
    "LocationActionCancelled",
]


# --------------------------------------------------------------------------- #
# Movement events                                                             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MoveStep:
    """A player moved one step across the city map.

    Records the completed ordinary map move (the matching mutation is a
    ``SetPosition`` effect). Cells are 40×25 city-map cells; ``delta`` is the signed
    step applied to ``from_cell`` to reach ``to_cell`` (e.g. +1 east, -40 north).
    """

    player: int  # player index
    from_cell: int  # origin city-map cell (40×25 grid)
    to_cell: int  # destination city-map cell
    delta: int  # signed step from_cell -> to_cell (+1 east, -40 north, ...)


@dataclass(frozen=True)
class EnterLocation:
    """A player stepped onto a location door and entered it.

    Emitted when a move lands on a door cell that opens a location. ``la`` is the
    location id; ``ln`` is the within-location tile index (1..9) — a first-class handler
    input in the original (it changes rent price, which pub serves alcohol, racket
    outcomes; CLAUDE.md state gotchas).
    """

    player: int  # player index
    from_cell: int  # origin city-map cell
    door_cell: int  # the door cell entered (city-map cell)
    delta: int  # signed step from_cell -> door_cell
    la: int  # location id
    ln: int  # within-location tile index 1..9


@dataclass(frozen=True)
class MoveBlocked:
    """A move was rejected; nothing mutated (an event with no matching effect).

    ``target`` is the intended destination cell when one is defined, else ``None`` (e.g.
    an out-of-bounds step has no valid target). ``reason`` is a machine key such as
    ``"turn_over"`` (no movement points left), ``"oob"`` (off the 40×25 grid), or
    ``"wall"`` (impassable) — themes resolve it to text.
    """

    player: int  # player index
    from_cell: int  # cell the player stayed on (no mutation happened)
    target: int | None  # intended destination cell, or None if undefined (e.g. oob)
    delta: int  # signed step that was attempted
    reason: str  # machine key: "turn_over" | "oob" | "wall" | ...


# --------------------------------------------------------------------------- #
# Option / location-action events                                            #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OptionDenied:
    """An option was denied by its guard before any handler ran.

    Carries only *that* the option was denied and an optional machine ``reason_key`` —
    NEVER the failing guard's raw debug data (guard diagnostics live on ``DeniedResult``
    in :mod:`engine.actions`). Themes resolve ``reason_key`` to player-facing text.
    """

    location_key: str  # the location whose option was denied
    option_id: str  # the denied option's id
    reason_key: str | None = None  # theme-resolvable message key, never raw text


@dataclass(frozen=True)
class LocationActionCompleted:
    """A location option's handler ran to completion (clean success)."""

    location_key: str  # the location acted in
    option_id: str  # the option whose handler completed


@dataclass(frozen=True)
class LocationActionRejected:
    """A location option's handler ran but rejected the action mid-flight.

    Distinct from :class:`OptionDenied` (which fires *before* any handler runs, at the
    guard). ``reason_key`` is a theme-resolvable machine key, never player-facing text.
    """

    location_key: str  # the location acted in
    option_id: str  # the rejected option
    reason_key: str | None = None  # theme-resolvable message key, never raw text


@dataclass(frozen=True)
class LocationActionCancelled:
    """A location option's handler was cancelled (e.g. the player backed out)."""

    location_key: str  # the location acted in
    option_id: str  # the cancelled option

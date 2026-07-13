"""Typed, serializable **Effects** + a pure ``apply`` (PLAN.md §5.5; KTD-3, KTD-6).

An **Effect** is the only way a handler mutates game state: a handler never touches
``GameState`` directly — it calls ``ctx.apply(effect)``, which *buffers* the effect
(see ``engine.interactions``). The U4 driver commits the buffer atomically; U5 makes
that commit actually change state by folding :func:`apply` over the buffered effects.

Effects are **pure data**: frozen dataclasses with no behavior. Application logic lives
in the standalone :func:`apply` function, never on the effect. This separation is what
lets an effect double as a **serializable replay event** — a log of committed effects,
each carrying a :data:`SCHEMA_VERSION`, replays a game deterministically, and old logs
stay readable as fields evolve (KTD-6).

:func:`apply` is **pure**: it deep-copies the input state, mutates the copy, and returns
it. The input ``GameState`` is never mutated — this is what makes the driver's
commit-or-discard atomic at the *state* level (a discarded buffer leaves the original
state untouched by construction).

**Player targeting convention.** A player-scoped effect acts on the *active* player
(``state.players[state.clock.active_player]``) by default; passing ``player=<index>``
targets ``state.players[<index>]`` instead. The target index is resolved once in
:func:`apply`.

``engine/`` imports nothing from ``server``/``clients``/transport. This module in
particular does NOT import from ``engine.interactions`` — the driver imports :func:`apply`
lazily to avoid a cycle.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from engine.state import GameState

#: Schema version stamped on every effect (KTD-6). Bump when an effect's fields change
#: in a way that a replay of an OLD log would need to know about; each effect references
#: this module-level constant via its class-level ``SCHEMA_VERSION`` attribute.
SCHEMA_VERSION = 1

#: The gangster stat names a :class:`StatChange` may target (mirrors ``Gangster`` fields).
_STAT_NAMES = ("energie", "kraft", "intelligenz", "brutalitaet")

__all__ = [
    "SCHEMA_VERSION",
    # Live effects
    "MoneyChange",
    "ScoreChange",
    "MsChange",
    "Teleport",
    "StatChange",
    "FlagSet",
    # Declared-but-deferred effects
    "WantedChange",
    "EnergyChange",
    "Jail",
    "SpawnFighter",
    # Application
    "apply",
]


# --------------------------------------------------------------------------- #
# Live effects — pure data (application in ``apply``)                         #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MoneyChange:
    """Add ``amount`` (signed) to the target player's cash ``ka``."""

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None


@dataclass(frozen=True)
class ScoreChange:
    """Add ``amount`` (signed) to the target player's score ``gf``, clamped to [0, 100].

    The clamp is intrinsic to ``gf`` in the original: ``mf-prg.bas:1160`` caps at 100 and
    ``:1161`` floors at 0. NOTE: the score *weighting* (``x * x8``, ``Config.score_mult``)
    is a LATER helper's concern — this raw effect just applies the delta and clamps.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: float
    player: int | None = None


@dataclass(frozen=True)
class MsChange:
    """Add ``amount`` (signed) to the target player's movement points ``ms``.

    ``ms`` is NOT clamped: it may legitimately reach 0 (or below) — a handler drives
    ``ms`` to 0 to force the turn to end (CLAUDE.md state gotchas).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None


@dataclass(frozen=True)
class Teleport:
    """Set the target player's map position ``po`` to the absolute ``cell``.

    ``cell`` is a city-map cell on the 40×25 grid (0..999) — an absolute set, not a delta.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    cell: int
    player: int | None = None


@dataclass(frozen=True)
class StatChange:
    """Add ``amount`` to ``roster[gangster].<stat>`` of the target player.

    ``stat`` is one of ``"energie"|"kraft"|"intelligenz"|"brutalitaet"``; an unknown
    name raises ``ValueError`` in :func:`apply`. No cap is applied here (energy caps and
    the like belong to a later combat unit) — just the raw delta.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    stat: str
    amount: int
    gangster: int = 0
    player: int | None = None


@dataclass(frozen=True)
class FlagSet:
    """Set a flag ``name`` to ``value``.

    Only ``scope="global"`` is implemented this slice: it sets ``state.flags.<name>``,
    validating that ``name`` is an existing ``Flags`` field (``ValueError`` otherwise).
    Any non-``"global"`` scope raises ``NotImplementedError`` — per-player flag bitfields
    are exercised in a later unit.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    name: str
    value: Any
    scope: str = "global"


# --------------------------------------------------------------------------- #
# Declared-but-deferred effects — the type exists & is serializable, but      #
# ``apply`` raises NotImplementedError (exercised in a later unit).           #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WantedChange:
    """Adjust a player's wanted level. Deferred — application in a later unit."""

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None


@dataclass(frozen=True)
class EnergyChange:
    """Adjust a gangster's combat energy. Deferred — application in a later unit."""

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    gangster: int = 0
    player: int | None = None


@dataclass(frozen=True)
class Jail:
    """Jail a player for ``months``. Deferred — application in a later unit."""

    SCHEMA_VERSION = SCHEMA_VERSION
    months: int
    player: int | None = None


@dataclass(frozen=True)
class SpawnFighter:
    """Spawn a combat fighter. Deferred — application in a later unit."""

    SCHEMA_VERSION = SCHEMA_VERSION
    fighter: Any


# --------------------------------------------------------------------------- #
# apply — PURE: deep-copy, mutate the copy, return it                         #
# --------------------------------------------------------------------------- #
def _target_index(state: GameState, player: int | None) -> int:
    """Resolve the target player index: explicit ``player`` or the active player.

    Validates the resolved index is in ``range(len(players))`` so the docstring's
    "out-of-range index surfaces as an error" holds literally — a negative ``player``
    would otherwise silently wrap to a real player via Python indexing, masking a
    handler bug.
    """
    idx = player if player is not None else state.clock.active_player
    if idx < 0 or idx >= len(state.players):
        raise IndexError(
            f"player index {idx} out of range (have {len(state.players)} players)"
        )
    return idx


def apply(state: GameState, effect: Any) -> GameState:
    """Return a NEW :class:`GameState` with ``effect`` applied; never mutate ``state``.

    Purity: the input ``state`` is deep-copied and only the copy is mutated and returned.
    This makes the driver's atomic commit/discard hold at the state level — a discarded
    buffer leaves the caller's state untouched by construction.

    Dispatch is on the concrete effect type. An unknown/unregistered effect type raises
    ``TypeError``. The target player index is resolved once (explicit ``player`` else the
    active player); an out-of-range index surfaces as ``IndexError``.

    Deferred effects (:class:`WantedChange`, :class:`EnergyChange`, :class:`Jail`,
    :class:`SpawnFighter`) raise ``NotImplementedError`` — they are exercised in a later
    unit but exist now so logs stay type-complete and serializable.
    """
    new_state = copy.deepcopy(state)

    if isinstance(effect, MoneyChange):
        p = new_state.players[_target_index(new_state, effect.player)]
        p.ka += effect.amount
        return new_state

    if isinstance(effect, ScoreChange):
        p = new_state.players[_target_index(new_state, effect.player)]
        # Clamp intrinsic to gf: cap 100 (mf-prg.bas:1160), floor 0 (mf-prg.bas:1161).
        p.gf = max(0.0, min(100.0, p.gf + effect.amount))
        return new_state

    if isinstance(effect, MsChange):
        p = new_state.players[_target_index(new_state, effect.player)]
        # ms is NOT clamped — it may reach 0 (or below) to force turn end.
        p.ms += effect.amount
        return new_state

    if isinstance(effect, Teleport):
        p = new_state.players[_target_index(new_state, effect.player)]
        p.po = effect.cell  # absolute city-map cell
        return new_state

    if isinstance(effect, StatChange):
        if effect.stat not in _STAT_NAMES:
            raise ValueError(
                f"unknown gangster stat {effect.stat!r}; expected one of {_STAT_NAMES}"
            )
        p = new_state.players[_target_index(new_state, effect.player)]
        if effect.gangster < 0 or effect.gangster >= len(p.roster):
            raise IndexError(
                f"gangster index {effect.gangster} out of range "
                f"(player has {len(p.roster)} gangsters)"
            )
        g = p.roster[effect.gangster]
        setattr(g, effect.stat, getattr(g, effect.stat) + effect.amount)
        return new_state

    if isinstance(effect, FlagSet):
        if effect.scope != "global":
            raise NotImplementedError(
                f"FlagSet scope {effect.scope!r} is not implemented this slice; only "
                "'global' flags are supported (per-player bitfields come in a later unit)."
            )
        if not hasattr(new_state.flags, effect.name):
            raise ValueError(f"unknown global flag {effect.name!r} on Flags")
        setattr(new_state.flags, effect.name, effect.value)
        return new_state

    if isinstance(effect, (WantedChange, EnergyChange, Jail, SpawnFighter)):
        raise NotImplementedError(
            f"{type(effect).__name__} is declared but its application is exercised in a "
            "later unit."
        )

    raise TypeError(f"Unknown effect type: {type(effect).__name__!r}")

"""Typed, serializable **Effects** + a pure ``apply`` (docs/design/engine-architecture.md; KTD-3, KTD-6).

An **Effect** is the only way a handler mutates game state: a handler never touches
``GameState`` directly — it calls ``ctx.apply(effect)``, which *buffers* the effect
(see ``engine.interactions``). The U4 driver commits the buffer atomically; U5 makes
that commit actually change state by folding :func:`apply` over the buffered effects.

Effects are **pure data**: frozen dataclasses with no behavior. Application logic lives
in the standalone :func:`apply` function, never on the effect. This separation is what
lets an effect double as a **serializable replay event** — a log of committed effects,
each carrying a :data:`SCHEMA_VERSION`, replays a game deterministically, and old logs
stay readable as fields evolve (KTD-6).

:func:`apply` is **pure**: the state graph is frozen (``engine.state``), so it
functionally rebuilds a new state rather than mutating one. The input ``GameState`` is
never mutated — this is what makes the driver's commit-or-discard atomic at the *state*
level (a discarded buffer leaves the original state untouched by construction).

**Player targeting convention.** A player-scoped effect acts on the *active* player
(``state.players[state.clock.active_player]``) by default; passing ``player=<index>``
targets ``state.players[<index>]`` instead. The target index is resolved once in
:func:`apply`.

``engine/`` imports nothing from ``server``/``clients``/transport. This module in
particular does NOT import from ``engine.interactions`` — the driver imports :func:`apply`
lazily to avoid a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from engine.state import GameState, tuple_replace

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
    "SetPosition",
    "SetEntryContext",
    "Teleport",
    "StatChange",
    "StatChangeCapped",
    "AssignWeapon",
    "ScoreAndRank",
    "FlagSet",
    "SetTenancy",
    "RentAccrue",
    # Declared-but-deferred effects
    "WantedChange",
    "EnergyChange",
    "Jail",
    "SpawnFighter",
    # Application
    "apply",
    "commit",
    "CommitResult",
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
class SetPosition:
    """Set the target player's map position ``po`` to the absolute ``cell``.

    The primitive movement effect: ordinary map movement commits its destination
    through this (``po(sp)`` in the original). ``cell`` is a city-map cell on the
    40×25 grid — an absolute set, not a delta. Deliberately identical in shape and
    mutation to :class:`Teleport` (reserved for forced/special relocation): the two
    names let the effects stream record *why* the player moved — do not merge them.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    cell: int
    player: int | None = None


@dataclass(frozen=True)
class SetEntryContext:
    """Record the target player's location-entry context: ``la`` and ``ln``.

    Sets ``player.last_la = la`` (the location id) and ``player.last_location = ln``
    (the within-location tile index 1..9). ``ln`` is a first-class handler input in
    the original (it changes rent price, which pub serves alcohol, racket outcomes —
    CLAUDE.md state gotchas), so entering a location commits both as one effect.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    la: int
    ln: int
    player: int | None = None


@dataclass(frozen=True)
class Teleport:
    """Set the target player's map position ``po`` to the absolute ``cell``.

    ``cell`` is a city-map cell on the 40×25 grid (0..999) — an absolute set, not a delta.
    Reserved for forced/special relocation; the deliberately identical
    :class:`SetPosition` covers ordinary movement (see its docstring — do not merge).
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
class StatChangeCapped:
    """Add ``amount`` to ``roster[gangster].<stat>``, then clamp to ``[floor, cap]``.

    A :class:`StatChange` variant for stat gains that must respect a ceiling. ``cap``
    is a REQUIRED field the handler passes from config (``formula_params.stat_cap`` —
    the 99 stat ceiling is config-owned game data, KTD-10, NOT hardcoded in the engine).
    ``floor`` defaults to 0. ``stat`` is validated like :class:`StatChange` (unknown name
    raises ``ValueError``). Subsumes the original's ``gosub 1365`` repack (KTD-4) — the
    engine stores unpacked stat fields, so applying the effect IS the write-back.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    stat: str
    amount: int
    cap: int
    floor: int = 0
    gangster: int = 0
    player: int | None = None


@dataclass(frozen=True)
class AssignWeapon:
    """Set ``roster[gangster].weapon`` of the target player to ``weapon``.

    The R9 purchase-persist primitive (``mf-prg.bas:13075``): no existing effect mutates
    ``Gangster.weapon`` (``StatChange`` only accepts the four stat names), so a weapon
    buy cannot complete without this. ``weapon`` is a weapon index (0..8).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    weapon: int
    gangster: int = 0
    player: int | None = None


@dataclass(frozen=True)
class ScoreAndRank:
    """Award score and recompute rank in one effect — the port of ``gosub 1160/1165``.

    ``gf = clamp(gf + amount*score_mult, 0, 100)`` then ``nr = int(gf/rank_divisor)+1``,
    computed from the CLAMPED ``gf`` (KTD-5). Fusing the two avoids the ordering hazard a
    separate score-then-rank pair would face (rank must see the post-clamp ``gf``). The
    ``[0, 100]`` clamp is the intrinsic ``gf`` domain, reused from :class:`ScoreChange`
    (KTD-10 exception). ``amount`` is the raw reward ``x``; ``score_mult`` is ``x8``
    (``Config.score_mult``). ``rank_divisor`` (11.1) is a config parameter, NOT hardcoded.
    Targets ``Player.nr`` (per ``:1165``).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: float
    rank_divisor: float
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


@dataclass(frozen=True)
class SetTenancy:
    """Set tenancy of within-location tile ``ln`` to the target player: ``uk(ln)=sp``.

    Ports the tenancy assignment at ``mf-prg.bas:10040`` (the slw rent block). ``ln``
    is the within-location tile index (1..9); the stored value is the resolved TARGET
    player index (explicit ``player`` else the active player, per the module's targeting
    convention). Writes ``state.map.tenancy[ln] = <idx>``.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    ln: int
    player: int | None = None


@dataclass(frozen=True)
class RentAccrue:
    """Add ``months`` to the target player's prepaid rented-months ``um``: ``um(sp)+=x``.

    Ports the rented-months accrual at ``mf-prg.bas:10040`` — the player prepays ``x``
    months of rent. Adds to ``state.players[target].rented_months``.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    months: int
    player: int | None = None


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


def _with_player(state: GameState, idx: int, **field_changes) -> GameState:
    """Return a new ``GameState`` with ``state.players[idx]`` field-updated (KTD-3).

    The single nested-update primitive every player-scoped effect branch funnels
    through: rebuild the ``Player`` via :func:`dataclasses.replace`, swap it into a
    rebuilt read-only ``players`` collection, and rebuild the ``GameState`` around it.
    Written once and tested once so ~15 effect branches never hand-roll the rebuild.

    Engine-internal vocabulary — NOT part of the handler API (CLAUDE.md § 5.2a).
    """
    new_player = replace(state.players[idx], **field_changes)
    return replace(state, players=tuple_replace(state.players, idx, new_player))


def _with_gangster(state: GameState, idx: int, g_idx: int, **field_changes) -> GameState:
    """Return a new ``GameState`` with player ``idx``'s gangster ``g_idx`` updated.

    One level deeper than :func:`_with_player`: rebuild the ``Gangster``, swap it into
    a rebuilt roster, then delegate the player/state rebuild to :func:`_with_player`.
    """
    player = state.players[idx]
    new_gangster = replace(player.roster[g_idx], **field_changes)
    return _with_player(
        state, idx, roster=tuple_replace(player.roster, g_idx, new_gangster)
    )


def _mapping_set(mapping, key, value) -> MappingProxyType:
    """Return a new read-only mapping equal to ``mapping`` with ``key`` set to ``value``.

    Keeps the R2 read-only invariant across effect rebuilds: the result is a
    :class:`~types.MappingProxyType`, never a plain mutable ``dict``.
    """
    updated = dict(mapping)
    updated[key] = value
    return MappingProxyType(updated)


def _apply(state: GameState, effect: Any) -> GameState:
    """Return a NEW ``GameState`` with ``effect`` applied. Pure — never mutates ``state``.

    This holds the dispatch logic shared by :func:`apply` (one effect) and :func:`commit`
    (a fold over many). Dispatch is on the concrete effect type. An unknown/unregistered
    effect type raises ``TypeError``. The target player index is resolved once (explicit
    ``player`` else the active player); an out-of-range index surfaces as ``IndexError``.

    Every branch rebuilds through the :func:`_with_player` / :func:`_with_gangster` /
    :func:`_mapping_set` helpers rather than writing state — the state graph is frozen
    (R1/R3), so the functional rebuild is the only expressible write path.

    Deferred effects (:class:`WantedChange`, :class:`EnergyChange`, :class:`Jail`,
    :class:`SpawnFighter`) raise ``NotImplementedError`` — they are exercised in a later
    unit but exist now so logs stay type-complete and serializable.
    """
    if isinstance(effect, MoneyChange):
        idx = _target_index(state, effect.player)
        return _with_player(state, idx, ka=state.players[idx].ka + effect.amount)

    if isinstance(effect, ScoreChange):
        idx = _target_index(state, effect.player)
        # Clamp intrinsic to gf: cap 100 (mf-prg.bas:1160), floor 0 (mf-prg.bas:1161).
        gf = max(0.0, min(100.0, state.players[idx].gf + effect.amount))
        return _with_player(state, idx, gf=gf)

    if isinstance(effect, MsChange):
        idx = _target_index(state, effect.player)
        # ms is NOT clamped — it may reach 0 (or below) to force turn end.
        return _with_player(state, idx, ms=state.players[idx].ms + effect.amount)

    if isinstance(effect, SetPosition):
        idx = _target_index(state, effect.player)
        return _with_player(state, idx, po=effect.cell)  # absolute cell (po(sp))

    if isinstance(effect, SetEntryContext):
        idx = _target_index(state, effect.player)
        return _with_player(
            state,
            idx,
            last_la=effect.la,  # location id (la)
            last_location=effect.ln,  # within-location tile index 1..9 (ln)
        )

    if isinstance(effect, Teleport):
        idx = _target_index(state, effect.player)
        return _with_player(state, idx, po=effect.cell)  # absolute city-map cell

    if isinstance(effect, StatChange):
        if effect.stat not in _STAT_NAMES:
            raise ValueError(
                f"unknown gangster stat {effect.stat!r}; expected one of {_STAT_NAMES}"
            )
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        if effect.gangster < 0 or effect.gangster >= len(p.roster):
            raise IndexError(
                f"gangster index {effect.gangster} out of range "
                f"(player has {len(p.roster)} gangsters)"
            )
        g = p.roster[effect.gangster]
        raised = getattr(g, effect.stat) + effect.amount
        return _with_gangster(state, idx, effect.gangster, **{effect.stat: raised})

    if isinstance(effect, StatChangeCapped):
        if effect.stat not in _STAT_NAMES:
            raise ValueError(
                f"unknown gangster stat {effect.stat!r}; expected one of {_STAT_NAMES}"
            )
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        if effect.gangster < 0 or effect.gangster >= len(p.roster):
            raise IndexError(
                f"gangster index {effect.gangster} out of range "
                f"(player has {len(p.roster)} gangsters)"
            )
        g = p.roster[effect.gangster]
        raised = getattr(g, effect.stat) + effect.amount
        # cap/floor are config-supplied (KTD-10) — the engine hardcodes no 99.
        capped = max(effect.floor, min(effect.cap, raised))
        return _with_gangster(state, idx, effect.gangster, **{effect.stat: capped})

    if isinstance(effect, AssignWeapon):
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        if effect.gangster < 0 or effect.gangster >= len(p.roster):
            raise IndexError(
                f"gangster index {effect.gangster} out of range "
                f"(player has {len(p.roster)} gangsters)"
            )
        # roster[g].weapon = w (mf-prg.bas:13075)
        return _with_gangster(state, idx, effect.gangster, weapon=effect.weapon)

    if isinstance(effect, ScoreAndRank):
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        # gf += amount*x8, clamped to the intrinsic [0,100] gf domain (mf-prg.bas:1160-1161).
        gf = max(0.0, min(100.0, p.gf + effect.amount * state.config.score_mult))
        # nr recomputed from the CLAMPED gf (mf-prg.bas:1165); divisor is config data.
        return _with_player(state, idx, gf=gf, nr=int(gf / effect.rank_divisor) + 1)

    if isinstance(effect, FlagSet):
        if effect.scope != "global":
            raise NotImplementedError(
                f"FlagSet scope {effect.scope!r} is not implemented this slice; only "
                "'global' flags are supported (per-player bitfields come in a later unit)."
            )
        if not hasattr(state.flags, effect.name):
            raise ValueError(f"unknown global flag {effect.name!r} on Flags")
        return replace(state, flags=replace(state.flags, **{effect.name: effect.value}))

    if isinstance(effect, SetTenancy):
        idx = _target_index(state, effect.player)
        # uk(ln) = sp (mf-prg.bas:10040) — rebuilt as a new read-only mapping.
        new_map = replace(state.map, tenancy=_mapping_set(state.map.tenancy, effect.ln, idx))
        return replace(state, map=new_map)

    if isinstance(effect, RentAccrue):
        idx = _target_index(state, effect.player)
        # um(sp) += x (mf-prg.bas:10040)
        rented = state.players[idx].rented_months + effect.months
        return _with_player(state, idx, rented_months=rented)

    if isinstance(effect, (WantedChange, EnergyChange, Jail, SpawnFighter)):
        raise NotImplementedError(
            f"{type(effect).__name__} is declared but its application is exercised in a "
            "later unit."
        )

    raise TypeError(f"Unknown effect type: {type(effect).__name__!r}")


def apply(state: GameState, effect: Any) -> GameState:
    """Return a NEW :class:`GameState` with ``effect`` applied; never mutate ``state``.

    Purity is structural: the state graph is frozen (R1), so :func:`_apply` can only
    build a new state — there is no in-place write to defend against. This makes the
    driver's atomic commit/discard hold at the state level: a discarded buffer leaves
    the caller's state untouched by construction.

    Errors surface exactly as in :func:`_apply`: ``TypeError`` for an unknown effect,
    ``IndexError`` for an out-of-range target, ``ValueError`` for a bad stat/flag name,
    ``NotImplementedError`` for a deferred effect.
    """
    return _apply(state, effect)


@dataclass(frozen=True)
class CommitResult:
    """The outcome of :func:`commit`: the new state plus the effects committed, in order.

    ``state`` is a newly built state (the caller's input is never mutated). ``effects``
    is the list of committed effects in application order — the replay record for this
    commit.
    """

    state: GameState
    effects: list


def commit(state: GameState, effects: list) -> CommitResult:
    """Fold ``effects`` in order over ``state``, returning the resulting state.

    Purity is structural: the graph is frozen (R1), so each :func:`_apply` step builds a
    new state and the caller's ``state`` is never mutated. Returns a :class:`CommitResult`
    bundling the new state and the committed effects in order. An empty ``effects`` list
    returns the input state unchanged — safe because the graph is frozen (pre-freeze this
    returned a distinct copy, an artifact of the unconditional deepcopy rather than a
    guarantee). Any effect that would raise in :func:`apply`
    (unknown type, out-of-range target, deferred effect, bad name) raises here too, at
    the offending effect.
    """
    new_state = state
    for effect in effects:
        new_state = _apply(new_state, effect)
    return CommitResult(state=new_state, effects=list(effects))

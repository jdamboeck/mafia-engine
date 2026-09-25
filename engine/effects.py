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

from engine.state import Combatant, GameState, Job, tuple_replace

#: Schema version stamped on every effect (KTD-6). Bump when an effect's fields change
#: in a way that a replay of an OLD log would need to know about; each effect references
#: this module-level constant via its class-level ``SCHEMA_VERSION`` attribute.
SCHEMA_VERSION = 1

#: The attribute keys a :class:`StatChange` may target — the ``attrs``-backed stats.
#: ``energie`` is NOT here (U2, amendment A4): it is the ``vitality`` SLOT, changed by
#: :class:`EnergyChange`, not an ``attrs`` key. A ``StatChange(stat="energie")`` would
#: pass a stale validation and then ``KeyError`` on ``attrs["energie"]`` — so the
#: validation set and the write path must agree that ``energie`` is not a StatChange
#: target. (Step 8 will make this list config-declared rather than engine-hardcoded.)
_STAT_NAMES = ("kraft", "intelligenz", "brutalitaet")

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
    "EnergyChange",
    "RankCommit",
    "SpawnFighter",
    "BarrelChange",
    "TipSet",
    "TipClear",
    "RosterAppend",
    "GangsterMarkHired",
    # Declared-but-deferred effects
    "WantedChange",
    "Jail",
    "DebtChange",
    "DebtClear",
    "ShopChange",
    "JobSet",
    "JobClear",
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


@dataclass(frozen=True)
class EnergyChange:
    """Add ``amount`` to ``roster[gangster].energie``, then clamp to ``[0, cap]`` (U3).

    Ports the turn-start energy regen (``mf-prg.bas:4015``: ``en=en+int(kr/10)+1``) and
    its cap (``:4020``: ``x=2+int(kr/4)+int(bt/4):ifen>xthenen=x``). ``cap`` is a REQUIRED
    field the caller computes from the gangster's OWN kraft/brutalitaet before building
    the effect (the formula reads the gangster's stats, not a config constant, so there is
    nothing for the engine to look up here — mirrors :class:`StatChangeCapped`'s
    config-supplied-cap shape, KTD-10). Floors at 0 (energy cannot go negative from a
    regen tick; combat's down-to-0 case is a later unit's separate concern). Was
    declared-but-stubbed since U2 (KTD-7); this is its real application.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    cap: int
    gangster: int = 0
    player: int | None = None


@dataclass(frozen=True)
class RankCommit:
    """Set the target player's committed rank ``rank`` (``ra(sp)``) to ``nr`` (U3).

    Ports the rank-promotion commit (``mf-prg.bas:4030``:
    ``ifra(sp)<>nr(sp)thenra(sp)=nr(sp):gosub4200``) — the SECOND half of the two-step
    rank system: :class:`ScoreAndRank` already recomputes the PENDING next-rank counter
    ``Player.nr`` from ``gf`` on every score award, but ``Player.rank`` (the value guards
    and prices actually read, e.g. ``waf.py``'s ``active.rank >= 5``) only moves when this
    effect commits it. The caller (the upkeep handler) is responsible for checking
    ``rank != nr`` and showing the promotion screen BEFORE applying this — the effect
    itself unconditionally sets ``rank = new_rank`` (an unconditional set is simpler and
    still faithful, since the caller never applies it when they are already equal).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    new_rank: int
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
class Jail:
    """Jail a player for ``months``. Deferred — application in a later unit."""

    SCHEMA_VERSION = SCHEMA_VERSION
    months: int
    player: int | None = None


@dataclass(frozen=True)
class SpawnFighter:
    """Append one fighter to ``state.combat.sides[side - 1]`` (U4, real application).

    ``fighter`` is a fully-built :class:`~engine.state.Fighter` — the caller (combat
    setup, ``engine.combat.setup_combat``) computes its placement/stats before
    building this effect, mirroring :class:`RosterAppend`'s "engine builds the value,
    the effect only appends it" shape. ``side`` is 1 or 2 (matching the source's
    ``kp(1,*)``/``kp(2,*)`` — side 1 is always the acting player, side 2 the enemy
    party, ``mf-prg.bas:5010``: ``ks(1)=sp:ks(2)=0``). Fight setup buffers one
    ``SpawnFighter`` per fighter so the replay log shows the roster being built up
    fighter-by-fighter, matching the source's per-fighter placement loop
    (``mf-prg.bas:30000``'s ``forj=1togz(ks(i))``) rather than one opaque bulk write.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    fighter: Any
    side: int = 1


@dataclass(frozen=True)
class DebtChange:
    """Add ``amount`` (signed) to the target player's debt ``kr(sp)``, and set the
    grace-counter ``months`` (``kz(sp)``) alongside it.

    Declared as groundwork in U2 (KTD-7); real application landed in U11 (kdh
    borrow/repay). Carries both fields in one effect because the source sets them
    together at every kdh call site (borrow :15030 sets ``kr+=x`` and ``kz=6`` in the
    same line; partial repayment :15065 decrements ``kr`` only, leaving ``months``
    untouched — pass ``months=None`` for that case) — see :class:`~engine.state.Debt`
    for the confirmed field semantics.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    months: int | None = None  # None = leave Debt.months unchanged
    player: int | None = None


@dataclass(frozen=True)
class DebtClear:
    """Zero the target player's debt AND its grace counter in one step.

    Declared as groundwork in U2 (KTD-7); real application landed in U11. Ports the
    full-repayment reset (``mf-prg.bas:15075``, ``kz(sp)=0`` once ``kr(sp)`` reaches
    0 — kdh's repay handler applies this ALONGSIDE the final ``DebtChange`` that zeros
    ``kr``) and is also the vocabulary the loan-default penalty (``:4370``,
    ``kr(sp)=0:kz(sp)=0``) will reuse when U12 lands. A dedicated clear (rather than a
    ``DebtChange`` computed to exactly cancel the balance) keeps both loan-shark exit
    paths self-documenting in the replay log.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    player: int | None = None


@dataclass(frozen=True)
class ShopChange:
    """Set the target player's owned shop ``tile`` and/or its ``capital`` delta.

    Declared as groundwork in U2 (KTD-7); real application landed in U11 (kdh
    buy/sell/capital-adjust/income). Targets :class:`~engine.state.Business` —
    ``tile`` sets ``shop_tile`` (``None`` leaves it unchanged; the sentinel 0 means
    "no shop", per the field's own docstring; passing 0 explicitly clears ownership
    on a sale), ``capital_delta`` adds to ``shop_capital`` (``None`` = no change).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    tile: int | None = None
    capital_delta: int | None = None
    player: int | None = None


@dataclass(frozen=True)
class BarrelChange:
    """Add ``amount`` (signed) to the target player's alcohol barrel stock ``ta(sp)``.

    Declared as groundwork in U2 (KTD-7); real application landed in U8 (the pub
    alcohol trade). Targets :class:`~engine.state.Contraband.alcohol_barrels`
    (mf-prg.bas:12035 buy, :12075 sell).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None


@dataclass(frozen=True)
class TipSet:
    """Set the target player's rolled heist tip type ``tp(sp)``.

    Declared as groundwork in U2 (KTD-7); real application landed in U8 (the pub tip
    flow). Targets :class:`~engine.state.Player.tip_target` (mf-prg.bas:12225-12226:
    the tip roll ``tp(sp)=1-5`` dispatching to one of five heist-rumour texts).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    tip_type: int
    player: int | None = None


@dataclass(frozen=True)
class TipClear:
    """Clear the target player's rolled heist tip (``tp(sp)=0``).

    Declared as groundwork in U2 (KTD-7); real application landed in U8. The
    counterpart to :class:`TipSet` — a used or expired tip resets ``tip_target`` to 0
    (no tip held). U8 applies this from both ``pub.tip``'s tip-4 decline/broke paths
    and ``upkeep.py``'s arms-deal slot (the ``tp(sp)=0`` at ``mf-prg.bas:31000``,
    applied FIRST so a stake resolves exactly once — see ``handlers/upkeep.py``).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    player: int | None = None


@dataclass(frozen=True)
class JobSet:
    """Set the target player's accepted job: ``type``/``pending_pay``/``months_left``.

    Declared as groundwork in U2 (KTD-7); real application landed in U10 (the pub
    job-accept flow). Ports ``mf-prg.bas:12335``: ``jo(sp)=x:jl(sp)=p`` (plus the
    per-job-type ``jd(sp)`` duration set earlier at :12308/:12311/:12316/:12322) —
    one effect since the source sets them as a unit when a job is accepted. Targets
    :class:`~engine.state.Job`.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    type: int
    pending_pay: int
    months_left: int
    player: int | None = None


@dataclass(frozen=True)
class JobClear:
    """Clear the target player's job (``jo(sp)=0``).

    Declared as groundwork in U2 (KTD-7); real application landed in U10. Ports the
    job-quit sites (mf-prg.bas:25560 completion, :25510 failed-shift-fight abort,
    :26080 jail commit forces ``jo(sp)=0``) — all zero the job the same way, so one
    effect covers every call site.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    player: int | None = None


@dataclass(frozen=True)
class RosterAppend:
    """Append a new :class:`~engine.state.Gangster` to the target player's roster.

    Declared as groundwork in U2 (KTD-7); real application landed in U9 (the pub
    recruit flow). The ONLY roster-growing effect (:class:`StatChange`/
    :class:`AssignWeapon`/etc. all require an existing index) — recruiting a hire
    adds a new entry, always AFTER the boss at ``roster[0]`` (KTD-6; see
    :class:`~engine.state.Player`'s docstring). ``gangster`` is a fully-built
    :class:`~engine.state.Gangster` (the handler rolls its stats from the candidate
    table before applying this effect) — names are directional data, not
    engine-invented. Ports ``mf-prg.bas:12160-12165``: ``gz(sp)=gz(sp)+1`` (roster
    grows by one; ``len(roster)`` IS ``gz(sp)`` per KTD-6, so nothing separate is
    incremented), ``gn$(sp,gz(sp))=gn$``/``gw(sp,gz(sp))=gw``/``ge$(sp,gz(sp))=
    '05'+ge$`` (name/weapon/stats, energy fixed at 5). The price deduction
    (``ka(sp)=ka(sp)-p``, :12160) is the CALLER's separate :class:`MoneyChange`,
    not part of this effect — mirrors every other settle site in this config
    (e.g. ``pub.drink``'s buy path) keeping one effect per state-shape change.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    gangster: Any
    player: int | None = None


@dataclass(frozen=True)
class GangsterMarkHired:
    """Add ``candidate_id`` to the GLOBAL (not per-player) hired-candidates set.

    New this unit (U9), real application from the start — no groundwork phase
    (unlike most other U9 effects) since ``Flags.hired_gangsters`` did not exist
    before this unit. Ports ``sg(g(i))=1`` (``mf-prg.bas:12165``): once ANY player
    hires candidate ``candidate_id`` (0-based; the source's ``g(i)`` is 1-based),
    every player's future recruit roll skips them (:12110's ``ifsg(g(i))goto12110``
    reroll-on-hired guard). Idempotent by construction: appending an already-present
    id would violate the "no duplicate offers" invariant upstream, but ``apply``
    still de-dupes defensively rather than trusting every caller.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    candidate_id: int


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
        raise IndexError(f"player index {idx} out of range (have {len(state.players)} players)")
    return idx


def _clamp(value: int | float, floor: int | float, cap: int | float) -> int | float:
    """Return ``value`` bounded to ``[floor, cap]`` — the shared clamp shape.

    Every capped-stat branch (:class:`ScoreChange`, :class:`StatChangeCapped`,
    :class:`ScoreAndRank`, :class:`EnergyChange`) computed ``max(floor, min(cap,
    value))`` inline; naming it once does not change any branch's floor/cap
    arguments or rounding — ``int``/``float`` inputs behave exactly as the inline
    expression did.
    """
    return max(floor, min(cap, value))


def _validate_stat(stat: str) -> None:
    """Raise ``ValueError`` if ``stat`` is not one of :data:`_STAT_NAMES`.

    Shared by :class:`StatChange` and :class:`StatChangeCapped`, which both
    validate the same field name against the same set before touching a gangster.
    """
    if stat not in _STAT_NAMES:
        raise ValueError(f"unknown gangster stat {stat!r}; expected one of {_STAT_NAMES}")


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


def _gangster_at(state: GameState, idx: int, g_idx: int) -> Combatant:
    """Return player ``idx``'s gangster ``g_idx``, range-checked.

    Every gangster-targeting effect needs the same guard before reading a stat off
    the roster, so it lives here rather than being restated at each ``_apply`` branch
    — a negative index would otherwise wrap silently and write the WRONG gangster.
    :func:`_with_gangster` calls this too, so the write path is guarded even when a
    caller does not read first.
    """
    player = state.players[idx]
    if g_idx < 0 or g_idx >= len(player.roster):
        raise IndexError(
            f"gangster index {g_idx} out of range (player has {len(player.roster)} gangsters)"
        )
    return player.roster[g_idx]


def _with_gangster(state: GameState, idx: int, g_idx: int, **field_changes) -> GameState:
    """Return a new ``GameState`` with player ``idx``'s gangster ``g_idx`` updated.

    One level deeper than :func:`_with_player`: rebuild the roster member, swap it into
    a rebuilt roster, then delegate the player/state rebuild to :func:`_with_player`.
    The index is range-checked via :func:`_gangster_at` so no caller can skip the
    guard and write to a wrapped-around index. Used for blueprint fields the engine
    DOES name (``weapon``); stat writes go through :func:`_with_gangster_attr`.
    """
    player = state.players[idx]
    new_gangster = replace(_gangster_at(state, idx, g_idx), **field_changes)
    return _with_player(state, idx, roster=tuple_replace(player.roster, g_idx, new_gangster))


def _with_gangster_attr(state: GameState, idx: int, g_idx: int, name: str, value: int) -> GameState:
    """Return a new ``GameState`` with roster member ``g_idx``'s ``attrs[name]`` set.

    The stat-name-free write path (U2, amendment A4): ``name`` is data, never a field
    the engine spells. :meth:`~engine.state.Combatant.with_attr` keeps a config
    ``Gangster``'s named field in sync; a loaded bare ``Combatant`` carries the value
    in ``attrs`` alone.
    """
    player = state.players[idx]
    new_gangster = _gangster_at(state, idx, g_idx).with_attr(name, value)
    return _with_player(state, idx, roster=tuple_replace(player.roster, g_idx, new_gangster))


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

    Deferred effects (:class:`WantedChange`, :class:`Jail`) raise
    ``NotImplementedError`` — they are exercised in a later unit but exist now so
    logs stay type-complete and serializable.
    (:class:`BarrelChange`, :class:`TipSet`, :class:`TipClear` gained real application
    in U8 — the pub alcohol trade and tip flow. :class:`RosterAppend` gained real
    application in U9 — the pub recruit flow. :class:`JobSet`/:class:`JobClear`
    gained real application in U10 — the pub job-accept and shift flows.
    :class:`DebtChange`/:class:`DebtClear`/:class:`ShopChange` gained real
    application in U11 — the kdh loan-shark handlers.)
    """
    if isinstance(effect, MoneyChange):
        idx = _target_index(state, effect.player)
        return _with_player(state, idx, ka=state.players[idx].ka + effect.amount)

    if isinstance(effect, ScoreChange):
        idx = _target_index(state, effect.player)
        # Clamp intrinsic to gf: cap 100 (mf-prg.bas:1160), floor 0 (mf-prg.bas:1161).
        gf = _clamp(state.players[idx].gf + effect.amount, 0.0, 100.0)
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
        _validate_stat(effect.stat)
        idx = _target_index(state, effect.player)
        g = _gangster_at(state, idx, effect.gangster)
        # Read and write the stat through attrs, never a named field: the engine spells
        # no stat name (U2, amendment A4). with_attr keeps a config Gangster's named
        # field in sync; a loaded bare Combatant carries it in attrs only.
        raised = g.attrs[effect.stat] + effect.amount
        return _with_gangster_attr(state, idx, effect.gangster, effect.stat, raised)

    if isinstance(effect, StatChangeCapped):
        _validate_stat(effect.stat)
        idx = _target_index(state, effect.player)
        g = _gangster_at(state, idx, effect.gangster)
        raised = g.attrs[effect.stat] + effect.amount
        # cap/floor are config-supplied (KTD-10) — the engine hardcodes no 99.
        # int(): _clamp is generic over int|float; a stat is always an int here.
        capped = int(_clamp(raised, effect.floor, effect.cap))
        return _with_gangster_attr(state, idx, effect.gangster, effect.stat, capped)

    if isinstance(effect, AssignWeapon):
        idx = _target_index(state, effect.player)
        _gangster_at(state, idx, effect.gangster)  # range-check before the write
        # roster[g].weapon = w (mf-prg.bas:13075)
        return _with_gangster(state, idx, effect.gangster, weapon=effect.weapon)

    if isinstance(effect, ScoreAndRank):
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        # gf += amount*x8, clamped to the intrinsic [0,100] gf domain (mf-prg.bas:1160-1161).
        gf = _clamp(p.gf + effect.amount * state.config.score_mult, 0.0, 100.0)
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

    if isinstance(effect, EnergyChange):
        idx = _target_index(state, effect.player)
        g = _gangster_at(state, idx, effect.gangster)
        # en=en+int(kr/10)+1 (mf-prg.bas:4015), capped at [0, cap] (:4020's ifen>xthenen=x
        # is an upper clamp only in the source; a floor of 0 is the engine's own sane
        # bound — energie has no documented negative-regen path this unit).
        # The depleting resource is the engine's ``vitality`` SLOT (U2, amendment A4) —
        # a named blueprint field, not an attr. The engine spells the role, never this
        # game's "energie".
        raised = int(_clamp(g.vitality + effect.amount, 0, effect.cap))
        return _with_gangster(state, idx, effect.gangster, vitality=raised)

    if isinstance(effect, RankCommit):
        idx = _target_index(state, effect.player)
        # ra(sp) = nr(sp) (mf-prg.bas:4030) — the caller decides WHEN (rank != nr).
        return _with_player(state, idx, rank=effect.new_rank)

    if isinstance(effect, SpawnFighter):
        if effect.side not in (1, 2):
            raise ValueError(f"SpawnFighter.side must be 1 or 2, got {effect.side!r}")
        sides = list(state.combat.sides)
        sides[effect.side - 1] = tuple(sides[effect.side - 1]) + (effect.fighter,)
        new_combat = replace(state.combat, sides=tuple(sides))
        return replace(state, combat=new_combat)

    if isinstance(effect, BarrelChange):
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        # ta(sp) += amount (mf-prg.bas:12035 buy, :12075 sell) — U8 real application.
        new_contraband = replace(
            p.contraband, alcohol_barrels=p.contraband.alcohol_barrels + effect.amount
        )
        return _with_player(state, idx, contraband=new_contraband)

    if isinstance(effect, TipSet):
        idx = _target_index(state, effect.player)
        # tp(sp) = tip_type (mf-prg.bas:12225-12226) — U8 real application.
        return _with_player(state, idx, tip_target=effect.tip_type)

    if isinstance(effect, TipClear):
        idx = _target_index(state, effect.player)
        # tp(sp) = 0 (mf-prg.bas:31000 arms-deal resolve; also the tip4 decline/broke
        # paths in pub.tip) — U8 real application.
        return _with_player(state, idx, tip_target=0)

    if isinstance(effect, RosterAppend):
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        # gz(sp)=gz(sp)+1 : gn$/gw/ge$ stored at the new slot (mf-prg.bas:12160-12165)
        # — U9 real application. len(roster) IS gz(sp) (KTD-6), so appending the tuple
        # IS the increment; nothing separate to bump.
        return _with_player(state, idx, roster=p.roster + (effect.gangster,))

    if isinstance(effect, GangsterMarkHired):
        # sg(g(i))=1 (mf-prg.bas:12165) — GLOBAL, not per-player. De-dupe
        # defensively even though the handler is expected to never mark twice.
        if effect.candidate_id in state.flags.hired_gangsters:
            return state
        new_hired = state.flags.hired_gangsters + (effect.candidate_id,)
        return replace(state, flags=replace(state.flags, hired_gangsters=new_hired))

    if isinstance(effect, JobSet):
        idx = _target_index(state, effect.player)
        # jo(sp)=type : jl(sp)=pending_pay : jd(sp)=months_left (mf-prg.bas:12335, plus
        # the per-type jd(sp) set earlier at :12308/:12311/:12316/:12322) — U10 real
        # application.
        new_job = Job(
            type=effect.type,
            pending_pay=effect.pending_pay,
            months_left=effect.months_left,
        )
        return _with_player(state, idx, jobs=new_job)

    if isinstance(effect, JobClear):
        idx = _target_index(state, effect.player)
        # jo(sp)=0 (mf-prg.bas:25560 completion, :25510 failed shift fight, :26080
        # jail commit) — U10 real application. A full reset (not just type=0) so a
        # cleared job never leaks a stale pending_pay/months_left into a future read.
        return _with_player(state, idx, jobs=Job())

    if isinstance(effect, DebtChange):
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        # kr(sp) += amount (mf-prg.bas:15030 borrow, :15065 repay) — U11 real
        # application. months is None on a partial repay (kz(sp) untouched); borrow
        # and full-repay callers pass an explicit value alongside this effect (full
        # repay's kz=0 reset is DebtClear, applied as a SEPARATE effect by the caller).
        new_debt = replace(p.debt, amount=p.debt.amount + effect.amount)
        if effect.months is not None:
            new_debt = replace(new_debt, months=effect.months)
        return _with_player(state, idx, debt=new_debt)

    if isinstance(effect, DebtClear):
        idx = _target_index(state, effect.player)
        # kr(sp)=0:kz(sp)=0 (mf-prg.bas:15075 full repayment) — U11 real application.
        return _with_player(state, idx, debt=replace(state.players[idx].debt, amount=0, months=0))

    if isinstance(effect, ShopChange):
        idx = _target_index(state, effect.player)
        p = state.players[idx]
        new_business = p.business
        if effect.tile is not None:
            # kg(sp)=ln (buy, mf-prg.bas:15120) or kg(sp)=0 (sell, :15155) — U11.
            new_business = replace(new_business, shop_tile=effect.tile)
        if effect.capital_delta is not None:
            # kk(sp) += capital_delta (fund/income, mf-prg.bas:15220, :4410) — U11.
            new_business = replace(
                new_business, shop_capital=new_business.shop_capital + effect.capital_delta
            )
        return _with_player(state, idx, business=new_business)

    if isinstance(effect, (WantedChange, Jail)):
        raise NotImplementedError(
            f"{type(effect).__name__} is declared but its application is exercised in a later unit."
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
    returns the input state unchanged, since the graph is frozen. Any effect that would
    raise in :func:`apply`
    (unknown type, out-of-range target, deferred effect, bad name) raises here too, at
    the offending effect.
    """
    new_state = state
    for effect in effects:
        new_state = _apply(new_state, effect)
    return CommitResult(state=new_state, effects=list(effects))

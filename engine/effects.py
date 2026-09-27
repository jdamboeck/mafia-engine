"""Typed, serializable **Effects**, each carrying its own ``apply`` (docs/design/engine-architecture.md).

An **Effect** is the only way a handler mutates game state: a handler never touches
``GameState`` directly — it calls ``ctx.apply(effect)``, which *buffers* the effect
(see ``engine.interactions``). The driver commits the buffer atomically via
:func:`commit`, which folds each effect's ``apply`` over the buffered effects.

An effect is a frozen dataclass whose fields are pure data, plus an
``apply(state) -> state`` method. The data is what lets an effect double as a
**serializable replay event** — a log of committed effects, each carrying a
:data:`SCHEMA_VERSION`, replays a game deterministically. ``apply`` is **pure**: the
state graph is frozen (``engine.state``), so it functionally rebuilds a new state; the
input ``GameState`` is never mutated, which is what makes the driver's
commit-or-discard atomic at the *state* level.

**Registration.** Every effect registers under a tag with :func:`register_effect`,
into :data:`EFFECTS`. The tag is what a save writes and what save loading resolves;
an optional ``consequence`` name makes the effect writable as a YAML consequence
(:data:`CONSEQUENCES`). The generic engine effects below register when this module is
imported; a game config registers its own effects when its package is imported, the
way ``@register`` fills the handler registry. Re-registering a tag replaces the old
entry, so a config reload (which re-executes the package) is safe. Registration is
the only list a new effect joins: :func:`commit` dispatches through the effect itself
and names no effect class.

**Player targeting convention.** A player-scoped effect acts on the *active* player
(``state.players[state.clock.active_player]``) by default; passing ``player=<index>``
targets ``state.players[<index>]`` instead. The target index is resolved once in
each effect's ``apply``.

``engine/`` imports nothing from ``server``/``clients``/transport. This module in
particular does NOT import from ``engine.interactions`` — the driver imports
:func:`commit` lazily to avoid a cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from types import MappingProxyType
from typing import Any, TypeVar

from engine.state import Combatant, Fighter, GameState, tuple_replace

#: Schema version stamped on every effect. Bump when an effect's fields change
#: in a way that a replay of an OLD log would need to know about; each effect references
#: this module-level constant via its class-level ``SCHEMA_VERSION`` attribute.
SCHEMA_VERSION = 1

#: The attribute keys a :class:`StatChange` may target — the ``attrs``-backed stats.
#: ``energie`` is NOT here: it is the ``vitality`` SLOT, changed by
#: :class:`EnergyChange`, not an ``attrs`` key. A ``StatChange(stat="energie")`` would
#: pass a stale validation and then ``KeyError`` on ``attrs["energie"]`` — so the
#: validation set and the write path must agree that ``energie`` is not a StatChange
#: target. (This list is engine-hardcoded; it is not yet config-declared.)
_STAT_NAMES = ("kraft", "intelligenz", "brutalitaet")


# --------------------------------------------------------------------------- #
# The effect registry                                                          #
# --------------------------------------------------------------------------- #
#: Every registered effect class, keyed by its tag — the name a save writes in its
#: ``_type`` field. Filled by :func:`register_effect`: the engine's generic effects on
#: this module's import, a config's own effects on the config package's import.
#: Returned on :class:`~engine.config_loader.LoadedConfig` and passed to save loading.
EFFECTS: dict[str, type] = {}

#: The effects a YAML consequence may name, keyed by the consequence ``type`` string
#: (see :mod:`engine.consequences`). Filled by :func:`register_effect`'s
#: ``consequence`` argument.
CONSEQUENCES: dict[str, type] = {}

#: The class attribute :func:`register_effect` stamps with the effect's tag.
_TAG_ATTR = "EFFECT_TAG"

_E = TypeVar("_E", bound=type)


def register_effect(
    tag: str | None = None, *, consequence: str | None = None
) -> Callable[[_E], _E]:
    """Class decorator registering an effect under ``tag`` (default: the class name).

    The class must define ``apply(state) -> state``. ``consequence``, when given, also
    makes the effect writable as a YAML consequence of that ``type``. Registering a
    tag (or consequence name) that is already registered replaces the old entry
    without raising: a config reload re-executes its package, and the reloaded class
    must win.

    A registered dataclass effect compares equal by its tag and field values, not by
    its exact class (:func:`_same_effect`). A config reload re-executes the config's
    effects module and so builds a new class under the same tag; an effect built
    before the reload, or rebuilt from a save, must still equal the same effect built
    after it. The tag, not the class object, is an effect's identity, as it is in a
    save.
    """

    def _decorator(cls: _E) -> _E:
        if not callable(getattr(cls, "apply", None)):
            raise TypeError(f"effect {cls.__name__} defines no apply(state) method")
        name = tag if tag is not None else cls.__name__
        setattr(cls, _TAG_ATTR, name)
        if hasattr(cls, "__dataclass_fields__"):
            # The dataclass __hash__ (over the field values) stays: equal effects have
            # equal fields, so they still hash alike.
            setattr(cls, "__eq__", _same_effect)
        EFFECTS[name] = cls
        if consequence is not None:
            CONSEQUENCES[consequence] = cls
        return cls

    return _decorator


def effect_tag(effect: Any) -> str | None:
    """The tag ``effect``'s class (or ``effect`` itself, given a class) registered under.

    ``None`` if it never registered.
    """
    cls = effect if isinstance(effect, type) else type(effect)
    return getattr(cls, _TAG_ATTR, None)


def _same_effect(self: Any, other: object) -> bool:
    """Effect equality by tag and field values (see :func:`register_effect`)."""
    other_tag = effect_tag(other)
    if other_tag is None or not is_dataclass(other):
        return NotImplemented
    if other_tag != effect_tag(self):
        return False
    mine = [(f.name, getattr(self, f.name)) for f in fields(self)]
    theirs = [(f.name, getattr(other, f.name)) for f in fields(other)]
    return mine == theirs


# --------------------------------------------------------------------------- #
# Live effects — pure data plus their own ``apply``                            #
# --------------------------------------------------------------------------- #
@register_effect(consequence="money_change")
@dataclass(frozen=True)
class MoneyChange:
    """Add ``amount`` (signed) to the target player's cash ``ka``."""

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        return _with_player(state, idx, ka=state.players[idx].ka + self.amount)


@register_effect(consequence="score_change")
@dataclass(frozen=True)
class ScoreChange:
    """Add ``amount`` (signed) to the target player's score ``gf``, then clamp it.

    The bound is caller-supplied, as :class:`StatChangeCapped`'s cap is: ``floor`` and
    ``cap`` are REQUIRED keyword-only fields (no default), because the score's bound is
    the game's, not the engine's, and a port must choose it:

    - ``floor=None, cap=None`` adds the delta with NO bound. A port of a source line that
      changes the score directly, without the game's score routine, wants this.
    - numbers clamp the result to ``[floor, cap]`` (either side may be ``None`` for no
      bound on that side).

    NOTE: any score *weighting* is the caller's concern; this raw effect just applies
    the delta.

    A record written before these fields existed is upgraded on the LOAD paths
    (persistence and the consequence parser) by :func:`legacy_fields`, never by a
    constructor default.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: float
    player: int | None = None
    floor: float | None = field(kw_only=True)
    cap: float | None = field(kw_only=True)

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        gf = state.players[idx].gf + self.amount
        if self.cap is not None:
            gf = min(self.cap, gf)
        if self.floor is not None:
            gf = max(self.floor, gf)
        return _with_player(state, idx, gf=gf)


@register_effect(consequence="ms_change")
@dataclass(frozen=True)
class MsChange:
    """Add ``amount`` (signed) to the target player's movement points ``ms``.

    ``ms`` is NOT clamped: it may legitimately reach 0 (or below) — a handler drives
    ``ms`` to 0 to force the turn to end (CLAUDE.md state gotchas).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # ms is NOT clamped — it may reach 0 (or below) to force turn end.
        return _with_player(state, idx, ms=state.players[idx].ms + self.amount)


@register_effect(consequence="set_position")
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

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        return _with_player(state, idx, po=self.cell)  # absolute cell (po(sp))


@register_effect(consequence="set_entry_context")
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

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        return _with_player(
            state,
            idx,
            last_la=self.la,  # location id (la)
            last_location=self.ln,  # within-location tile index 1..9 (ln)
        )


@register_effect(consequence="teleport")
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

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        return _with_player(state, idx, po=self.cell)  # absolute city-map cell


@register_effect()
@dataclass(frozen=True)
class StatChange:
    """Add ``amount`` to ``roster[gangster].<stat>`` of the target player.

    ``stat`` is one of :data:`_STAT_NAMES` (``"kraft"|"intelligenz"|"brutalitaet"``);
    an unknown name raises ``ValueError`` in :func:`apply`. No cap is applied here — just
    the raw delta (a capped gain is :class:`StatChangeCapped`; energy is
    :class:`EnergyChange`).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    stat: str
    amount: int
    gangster: int = 0
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        _validate_stat(self.stat)
        idx = target_index(state, self.player)
        g = _gangster_at(state, idx, self.gangster)
        # Read and write the stat through attrs, never a named field: the engine spells
        # no stat name. with_attr keeps a config Gangster's named
        # field in sync; a loaded bare Combatant carries it in attrs only.
        raised = g.attrs[self.stat] + self.amount
        return _with_gangster_attr(state, idx, self.gangster, self.stat, raised)


@register_effect(consequence="stat_change_capped")
@dataclass(frozen=True)
class StatChangeCapped:
    """Add ``amount`` to ``roster[gangster].<stat>``, then clamp to ``[floor, cap]``.

    A :class:`StatChange` variant for stat gains that must respect a ceiling. ``cap``
    is a REQUIRED field the handler passes from config (``formula_params.stat_cap`` —
    the 99 stat ceiling is config-owned game data, NOT hardcoded in the engine).
    ``floor`` defaults to 0. ``stat`` is validated like :class:`StatChange` (unknown name
    raises ``ValueError``). Subsumes the original's ``gosub 1365`` repack — the
    engine stores unpacked stat fields, so applying the effect IS the write-back.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    stat: str
    amount: int
    cap: int
    floor: int = 0
    gangster: int = 0
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        _validate_stat(self.stat)
        idx = target_index(state, self.player)
        g = _gangster_at(state, idx, self.gangster)
        raised = g.attrs[self.stat] + self.amount
        # cap/floor are config-supplied — the engine hardcodes no 99.
        # int(): _clamp is generic over int|float; a stat is always an int here.
        capped = int(_clamp(raised, self.floor, self.cap))
        return _with_gangster_attr(state, idx, self.gangster, self.stat, capped)


@register_effect(consequence="assign_weapon")
@dataclass(frozen=True)
class AssignWeapon:
    """Set ``roster[gangster].weapon`` of the target player to ``weapon``.

    The weapon-purchase persist primitive (``mf-prg.bas:13075``): no other effect mutates
    ``Gangster.weapon`` (``StatChange`` only accepts stat names), so a weapon
    buy cannot complete without this. ``weapon`` is a weapon index (0..8).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    weapon: int
    gangster: int = 0
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        _gangster_at(state, idx, self.gangster)  # range-check before the write
        # roster[g].weapon = w (mf-prg.bas:13075)
        return _with_gangster(state, idx, self.gangster, weapon=self.weapon)


@register_effect()
@dataclass(frozen=True)
class FlagSet:
    """Set the global value ``name`` to ``value``.

    Only ``scope="global"`` is implemented: it sets ``state.values[name]``, the global
    value map the config declares. ``name`` must be a key the map already holds (a
    config's setup fills every declared key with its default), else ``ValueError``.
    Any non-``"global"`` scope raises ``NotImplementedError`` — per-player flags are
    not built.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    name: str
    value: Any
    scope: str = "global"

    def apply(self, state: GameState) -> GameState:
        if self.scope != "global":
            raise NotImplementedError(
                f"FlagSet scope {self.scope!r} is not implemented; only "
                "'global' flags are supported (per-player flags are not built)."
            )
        if self.name not in state.values:
            raise ValueError(f"unknown global value {self.name!r}; it is not in the global map")
        return set_global_value(state, self.name, self.value)


@register_effect()
@dataclass(frozen=True)
class EnergyChange:
    """Add ``amount`` to ``roster[gangster]``'s ``vitality`` (energie), clamped to ``[0, cap]``.

    Ports the turn-start energy regen (``mf-prg.bas:4015``: ``en=en+int(kr/10)+1``) and
    its cap (``:4020``: ``x=2+int(kr/4)+int(bt/4):ifen>xthenen=x``). ``cap`` is a REQUIRED
    field the caller computes from the gangster's OWN kraft/brutalitaet before building
    the effect (the formula reads the gangster's stats, not a config constant, so there is
    nothing for the engine to look up here — mirrors :class:`StatChangeCapped`'s
    config-supplied-cap shape). Floors at 0 (energy cannot go negative from a regen
    tick). A fight's energy loss also persists through this effect, buffered by
    :func:`engine.fight_loop._run_combat` with ``cap`` set so the clamp is a no-op.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    cap: int
    gangster: int = 0
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        g = _gangster_at(state, idx, self.gangster)
        # en=en+int(kr/10)+1 (mf-prg.bas:4015), capped at [0, cap] (:4020's ifen>xthenen=x
        # is an upper clamp only in the source; a floor of 0 is the engine's own sane
        # bound — energie has no documented negative-regen path).
        # The depleting resource is the engine's ``vitality`` SLOT —
        # a named blueprint field, not an attr. The engine spells the role, never this
        # game's "energie".
        raised = int(_clamp(g.vitality + self.amount, 0, self.cap))
        return _with_gangster(state, idx, self.gangster, vitality=raised)


@register_effect()
@dataclass(frozen=True)
class SpawnFighter:
    """Append one fighter to ``state.combat.sides[side - 1]``.

    ``fighter`` is a fully-built :class:`~engine.state.Fighter` — the caller (combat
    setup, ``engine.combat_setup.setup_combat``) computes its placement/stats before
    building this effect, mirroring :class:`RosterAppend`'s "engine builds the value,
    the effect only appends it" shape. ``side`` is 1 or 2 (matching the source's
    ``kp(1,*)``/``kp(2,*)`` — side 1 is always the acting player, side 2 the enemy
    party, ``mf-prg.bas:5010``: ``ks(1)=sp:ks(2)=0``). Fight setup buffers one
    ``SpawnFighter`` per fighter so the replay log shows the roster being built up
    fighter-by-fighter, matching the source's per-fighter placement loop
    (``mf-prg.bas:30000``'s ``forj=1togz(ks(i))``) rather than one opaque bulk write.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    fighter: Fighter
    side: int = 1

    def apply(self, state: GameState) -> GameState:
        if self.side not in (1, 2):
            raise ValueError(f"SpawnFighter.side must be 1 or 2, got {self.side!r}")
        sides = list(state.combat.sides)
        sides[self.side - 1] = tuple(sides[self.side - 1]) + (self.fighter,)
        new_combat = replace(state.combat, sides=tuple(sides))
        return replace(state, combat=new_combat)


@register_effect()
@dataclass(frozen=True)
class RosterAppend:
    """Append a new :class:`~engine.state.Gangster` to the target player's roster.

    Applied by the pub recruit flow. The ONLY roster-growing effect (:class:`StatChange`/
    :class:`AssignWeapon`/etc. all require an existing index) — recruiting a hire
    adds a new entry, always AFTER the boss at ``roster[0]`` (see
    :class:`~engine.state.Player`'s docstring). ``gangster`` is a fully-built
    :class:`~engine.state.Gangster` (the handler rolls its stats from the candidate
    table before applying this effect) — names are directional data, not
    engine-invented. Ports ``mf-prg.bas:12160-12165``: ``gz(sp)=gz(sp)+1`` (roster
    grows by one; ``len(roster)`` IS ``gz(sp)``, so nothing separate is
    incremented), ``gn$(sp,gz(sp))=gn$``/``gw(sp,gz(sp))=gw``/``ge$(sp,gz(sp))=
    '05'+ge$`` (name/weapon/stats, energy fixed at 5). The price deduction
    (``ka(sp)=ka(sp)-p``, :12160) is the CALLER's separate :class:`MoneyChange`,
    not part of this effect — mirrors every other settle site in this config
    (e.g. ``pub.drink``'s buy path) keeping one effect per state-shape change.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    gangster: Combatant
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        p = state.players[idx]
        # gz(sp)=gz(sp)+1 : gn$/gw/ge$ stored at the new slot (mf-prg.bas:12160-12165)
        # — len(roster) IS gz(sp), so appending the tuple
        # IS the increment; nothing separate to bump.
        return _with_player(state, idx, roster=p.roster + (self.gangster,))


@register_effect()
@dataclass(frozen=True)
class RosterTruncate:
    """Cut the target player's roster down to its first ``size`` entries.

    Ports the late-rent eviction ``mf-prg.bas:4651`` ``gz(sp)=1``: the gang count
    drops to one, so only gangster 1 — ``roster[0]``, the boss — stays; everyone hired
    after them leaves. ``len(roster)`` IS ``gz(sp)`` (see :class:`RosterAppend`), so
    dropping the tail IS the assignment. A roster already no longer than ``size`` is
    left as it is (``gz(sp)=1`` never adds a gangster the engine could not build).
    The global hired-candidates set is NOT touched: the source leaves ``sg()`` set, so
    a gangster who walked out is never offered for hire again.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    size: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # gz(sp)=1 (mf-prg.bas:4651) — the tail of the roster leaves.
        return _with_player(state, idx, roster=state.players[idx].roster[: self.size])


#: What a field that was ADDED to an effect meant before it existed, for LOADING data
#: written before then (saved effect logs, YAML consequences). Keyed by effect class,
#: then field name. Only :func:`legacy_fields` (called by the load paths,
#: ``engine.persistence`` and ``engine.consequences``) reads this; a constructor never
#: falls back to it, so new code must pass the field.
#:
#: ``ScoreChange.floor``/``cap``: a ``ScoreChange`` written before any bound field
#: existed always clamped to [0, 100] (``mf-prg.bas:1160``/``:1161``). This load shim
#: is the only place the engine still spells that bound; it goes with the table at the
#: save-format version bump.
LEGACY_FIELD_DEFAULTS: dict[type, dict[str, Any]] = {
    ScoreChange: {"floor": 0.0, "cap": 100.0},
}


def legacy_fields(cls: type, raw: Mapping[str, Any]) -> dict[str, Any]:
    """``raw``'s fields for ``cls``, upgraded from a record written under an older shape.

    Two upgrades, both for data written before a field changed:

    - ``ScoreChange``'s ``clamp: bool`` was replaced by the caller-supplied
      ``floor``/``cap`` pair; ``clamp=True`` meant [0, 100] and ``clamp=False`` meant no
      bound.
    - a field in :data:`LEGACY_FIELD_DEFAULTS` that the record omits takes its
      pre-field meaning.

    Returns a new dict; ``raw`` is never mutated.
    """
    given = dict(raw)
    if cls is ScoreChange and "clamp" in given:
        clamped = given.pop("clamp")
        given.setdefault("floor", 0.0 if clamped else None)
        given.setdefault("cap", 100.0 if clamped else None)
    for name, legacy in LEGACY_FIELD_DEFAULTS.get(cls, {}).items():
        given.setdefault(name, legacy)
    return given


# --------------------------------------------------------------------------- #
# Rebuild helpers — PURE: each returns a new state, never mutates its input    #
# --------------------------------------------------------------------------- #
def target_index(state: GameState, player: int | None) -> int:
    """Resolve the target player index: explicit ``player`` or the active player.

    Part of the handler API: a config effect's ``apply`` resolves its target here, so
    it follows the module's targeting convention.

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

    The capped-stat branches (:class:`StatChangeCapped`, :class:`EnergyChange`)
    computed ``max(floor, min(cap, value))`` inline; naming it once does not change
    any branch's floor/cap arguments or rounding — ``int``/``float`` inputs behave
    exactly as the inline expression did.
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
    """Return a new ``GameState`` with ``state.players[idx]`` field-updated.

    The single nested-update primitive every player-scoped effect branch funnels
    through: rebuild the ``Player`` via :func:`dataclasses.replace`, swap it into a
    rebuilt read-only ``players`` collection, and rebuild the ``GameState`` around it.
    Written once and tested once so ~15 effect branches never hand-roll the rebuild.

    Engine-internal vocabulary — NOT part of the handler API (CLAUDE.md, "Handler API").
    """
    new_player = replace(state.players[idx], **field_changes)
    return replace(state, players=tuple_replace(state.players, idx, new_player))


def _gangster_at(state: GameState, idx: int, g_idx: int) -> Combatant:
    """Return player ``idx``'s gangster ``g_idx``, range-checked.

    Every gangster-targeting effect needs the same guard before reading a stat off
    the roster, so it lives here rather than being restated in each effect's ``apply``
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

    The stat-name-free write path: ``name`` is data, never a field
    the engine spells. :meth:`~engine.state.Combatant.with_attr` keeps a config
    ``Gangster``'s named field in sync; a loaded bare ``Combatant`` carries the value
    in ``attrs`` alone.
    """
    player = state.players[idx]
    new_gangster = _gangster_at(state, idx, g_idx).with_attr(name, value)
    return _with_player(state, idx, roster=tuple_replace(player.roster, g_idx, new_gangster))


def _mapping_set(mapping, key, value) -> MappingProxyType:
    """Return a new read-only mapping equal to ``mapping`` with ``key`` set to ``value``.

    Keeps the read-only-collections invariant across effect rebuilds: the result is a
    :class:`~types.MappingProxyType`, never a plain mutable ``dict``.
    """
    updated = dict(mapping)
    updated[key] = value
    return MappingProxyType(updated)


def update_player(state: GameState, *, player: int | None = None, **changes: Any) -> GameState:
    """Return a new state with the target player's fields replaced by ``changes``.

    Part of the handler API: the player-scoped state-update helper a config effect's
    ``apply`` composes (explicit ``player`` or the active one, range-checked), instead
    of rebuilding the players tuple by hand. ``changes`` name ``Player`` fields.
    """
    return _with_player(state, target_index(state, player), **changes)


def player_values(state: GameState, *, player: int | None = None) -> Mapping[str, Any]:
    """The target player's declared value map (explicit ``player`` or the active one).

    Part of the handler API: a config effect's ``apply`` reads its own state here.
    """
    return state.players[target_index(state, player)].values


def set_player_value(
    state: GameState, name: str, value: Any, *, player: int | None = None
) -> GameState:
    """Return a new state with the target player's value ``name`` set to ``value``.

    Part of the handler API: the state-update helper a config effect's ``apply``
    composes, instead of rebuilding the state graph by hand. The engine does not check
    ``name`` against the schema here; save loading refuses an undeclared key.
    """
    idx = target_index(state, player)
    return _with_player(state, idx, values=_mapping_set(state.players[idx].values, name, value))


def set_global_value(state: GameState, name: str, value: Any) -> GameState:
    """Return a new state with the global value ``name`` set to ``value``.

    The global counterpart of :func:`set_player_value`, for game state with no player
    dimension.
    """
    return replace(state, values=_mapping_set(state.values, name, value))


def apply(state: GameState, effect: Any) -> GameState:
    """Return a NEW :class:`GameState` with ``effect`` applied; never mutate ``state``.

    Dispatches through the effect's own ``apply``; an object without one raises
    ``TypeError``. Other errors surface from that ``apply``: ``IndexError`` for an
    out-of-range target, ``ValueError`` for a bad stat or global value name,
    ``NotImplementedError`` for a deferred effect.
    """
    if not callable(getattr(effect, "apply", None)):
        raise TypeError(f"Unknown effect type: {type(effect).__name__!r} has no apply(state)")
    return effect.apply(state)


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

    Each step is the effect's own ``apply`` (via :func:`apply`), so this names no
    effect class. Purity is structural: the graph is frozen, so each step builds a new
    state and the caller's ``state`` is never mutated. An empty ``effects`` list
    returns the input state unchanged. Any effect that would raise in :func:`apply`
    raises here too, at the offending effect.
    """
    new_state = state
    for effect in effects:
        new_state = apply(new_state, effect)
    return CommitResult(state=new_state, effects=list(effects))

"""Modular ``GameState`` dataclasses — the frozen, deeply read-only state graph.

Each subsystem owns its own data (docs/design/engine-architecture.md). Construction
with sensible defaults only — no game logic or formulas (state changes go through
``engine.effects``). Every collection field is coerced to a tuple or
``MappingProxyType`` at construction, so the graph is immutable all the way down.
Source-variable names from the decompiled BASIC (``mf-prg.bas``) are noted in
comments where helpful.

Pure dataclasses + stdlib only; the ``engine/`` package imports nothing from
``server/``, ``clients/``, or any transport/render library, and holds no
display text.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

#: Shared empty read-only mapping — a safe immutable default.
_EMPTY_MAP: Mapping = MappingProxyType({})


def freeze(value):
    """Recursively convert plain containers into read-only ones.

    ``dict`` -> :class:`~types.MappingProxyType`, ``list``/``tuple`` -> ``tuple``,
    applied all the way down. Config data arrives from YAML (and from a JSON save) as
    ordinary mutable containers; every *construction* site pipes it through here so the
    built graph is immutable by construction rather than only at its top level.

    Scalars pass through untouched. Already-frozen containers are rebuilt harmlessly.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def tuple_replace(items, idx: int, value) -> tuple:
    """Return a new tuple with ``items[idx]`` replaced by ``value``.

    The read-only-preserving sequence update every functional rebuild funnels through:
    the result is always a ``tuple``, so a rebuilt collection can never be a mutable
    ``list`` a handler could append to. Structural sharing is implicit — untouched
    elements are the same objects, which is safe because they are themselves frozen.

    Raises ``IndexError`` for an out-of-range ``idx`` (including a negative one). Python
    slicing would otherwise silently GROW the tuple on a bad index — the exact
    silent-corruption shape the frozen graph exists to eliminate, so this fails loudly
    and matches the ``IndexError`` the effect branches already raise.
    """
    items = tuple(items)
    if idx < 0 or idx >= len(items):
        raise IndexError(f"index {idx} out of range (collection has {len(items)} items)")
    return items[:idx] + (value,) + items[idx + 1 :]


def json_safe(value) -> Any:
    """Recursively convert a frozen state graph into plain JSON-safe containers.

    The inverse of :func:`freeze`: read-only mappings unwrap to ``dict`` and tuples
    to ``list``. Needed because neither read-only form survives JSON, and
    ``mappingproxy`` is not even picklable — so ``dataclasses.asdict`` (which
    deepcopies internally) cannot walk a frozen graph at all.

    Lives here rather than in ``engine.persistence`` because it is generic
    graph-walking, not save-format logic: persistence serializes with it, and the
    test purity harness snapshots state values with it.

    Returns ``Any`` deliberately: the return shape is a function of the *input's*
    runtime shape (dataclass/Mapping -> ``dict``, list/tuple -> ``list``, scalar ->
    itself), which no single static type expresses. Callers that know their input is
    a dataclass — :func:`engine.persistence._effect_to_dict` unpacking with ``**`` —
    rely on that. A narrower lie (``dict``) would break the scalar and list branches
    for every other caller.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_safe(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def _coerce_readonly(instance, *field_names) -> None:
    """Force ``instance``'s named collection fields into read-only form.

    Freezing a dataclass stops attribute writes but says nothing about what its
    fields *hold* — a caller passing a plain ``dict``/``list`` would reopen the very
    mutation path the freeze exists to close. Coercing at construction makes deep
    immutability a property every construction site gets for free, rather than one
    each must remember (setup, effect rebuild, persistence load).

    Uses ``object.__setattr__`` because the instance is frozen by the time
    ``__post_init__`` runs — the standard frozen-dataclass normalization idiom.
    """
    for name in field_names:
        current = getattr(instance, name)
        frozen_value = freeze(current)
        if frozen_value is not current:
            object.__setattr__(instance, name, frozen_value)


__all__ = [
    "Combatant",
    "Job",
    "Debt",
    "Business",
    "Contraband",
    "Wanted",
    "Player",
    "MapState",
    "Fighter",
    "CombatState",
    "Clock",
    "Config",
    "Flags",
    "GameState",
    "StateSchema",
    "StateSchemaError",
    "ValueSpec",
    # NOTE: `freeze`, `json_safe`, and `tuple_replace` are deliberately NOT exported.
    # docs/design/config-and-content-contract.md § Handler API admits only "engine
    # helpers for shared BASIC subroutines" (not-enough-money, gangster picker,
    # score update, combat entry) — generic container plumbing is not that category.
    # They stay importable for engine-internal use (effects, movement, persistence)
    # and tests; keeping them out of __all__ keeps the advertised config-facing
    # surface minimal (CLAUDE.md § 5.2a).
]


# eq=False: use the hand-written cross-class __eq__ below, not a dataclass-generated
# one (which would require an identical class and so never equal a Gangster to a
# reloaded Combatant). See __eq__ for why.
@dataclass(frozen=True, eq=False)
class Combatant:
    """A single roster member — the engine's blueprint for anything that can fight.

    The engine addresses this through FOUR slots and knows nothing else about it:

    ``position``/``down``
        Structural. The engine cannot sequence activations, compute a line of fire,
        or find a winner without them. (Carried by :class:`Fighter`, the on-grid
        form; a roster member off the grid has no position.)
    ``attrs``
        The OPAQUE extension point — a ``name -> int`` map the engine passes to a
        config's formulas and never inspects. ``kraft``/``brutalitaet``/``intelligenz``
        are NOT engine slots: they live here, and adding another is a config-only
        change.
    ``identity`` / ``equipment``
        Opaque label (``name``) and opaque equipment id (``weapon``); the game turns
        the id into the fighter's constructed ``equipment`` mapping at fight setup.

    The depleting resource is the ``vitality`` slot — the engine only ever subtracts
    from it and clamps at zero; the game decides what it means.

    **No game-stat fields.** ``kraft``/``brutalitaet``/
    ``intelligenz``/``energie`` are NOT fields here — they are this game's vocabulary,
    and the engine names none of them. A roster member's stats live in ``attrs`` as an
    opaque ``name -> int`` map. The reference title's named-field roster member is
    ``Gangster``, defined in ``data/game_configs/mafia_1920s`` as a subclass that adds
    those fields for construction ergonomics and derives ``attrs`` from them.

    ``name``/``weapon`` stay: ``name`` is the opaque ``identity`` label and ``weapon``
    is the opaque equipment id — both blueprint concerns, neither a stat.

    A **loaded** roster member (rebuilt by ``engine.persistence``) is a bare
    ``Combatant`` carrying ``attrs`` — the engine reconstructs without knowing the
    config's ``Gangster`` type, which keeps the layer rule intact. Config code that
    needs a stat reads it from ``attrs`` so it works for both a freshly-built
    ``Gangster`` and a loaded ``Combatant``.
    """

    name: str = ""
    weapon: int = 0  # weapon index 0..8; 0 = unarmed — the opaque equipment id
    #: The ONE depleting resource, first-class like ``position``/``down``.
    #: The engine subtracts from it and clamps at zero; ``down`` derives from it hitting
    #: zero. This game NAMES it "energie" and maps that onto this slot at construction —
    #: the engine spells only the role, never the game's word.
    vitality: int = 0
    attrs: Mapping[str, int] = field(
        default_factory=lambda: _EMPTY_MAP
    )  # the OPAQUE stat map; the engine's only view

    def __post_init__(self):
        # Coerce a passed-in attrs to read-only. A Gangster subclass builds attrs from
        # its construction kwargs BEFORE calling here.
        object.__setattr__(self, "attrs", MappingProxyType(dict(self.attrs)))

    def __eq__(self, other: object) -> bool:
        # Compare across the Combatant/Gangster boundary by the blueprint fields, not by
        # exact class. A save-then-reload rebuilds a roster member as
        # a bare ``Combatant``; the live one is a config ``Gangster`` with the SAME four
        # fields. The default dataclass ``__eq__`` requires an identical class, so a
        # reconstructed state would never equal the live one — and every purity/replay
        # check (``result.state == commit(state_from_dict(...))``) would fail. Both
        # carry their stats single-sourced (``vitality`` + ``attrs``), so field equality
        # is the honest test of "same roster member".
        if not isinstance(other, Combatant):
            return NotImplemented
        return (
            self.name == other.name
            and self.weapon == other.weapon
            and self.vitality == other.vitality
            and dict(self.attrs) == dict(other.attrs)
        )

    # No __hash__: a custom __eq__ leaves the class unhashable (as it already was — its
    # attrs mappingproxy has no stable hash). Roster members are only ever compared,
    # never used as dict keys or set members, so this costs nothing.

    def with_attr(self, name: str, value: int) -> "Combatant":
        """Return a copy with ``attrs[name]`` set to ``value``.

        The engine's stat-name-free way to write a roster attribute: it names the key
        only as data passed in, never as a field.
        """
        merged = dict(self.attrs)
        merged[name] = value
        return replace(self, attrs=MappingProxyType(merged))


@dataclass(frozen=True)
class Job:
    """A pending job/contract for a player.

    Field semantics confirmed against the source (mf-prg.bas:12308-12335,25550-25560):
    ``type`` is ``jo(sp)`` (the accepted job's type id; 0 = no job), ``pending_pay`` is
    ``jl(sp)`` (the lump sum paid out when the job completes — despite the source
    comment "monthly pay", it is a single payout on completion, not a per-turn wage),
    and ``months_left`` is ``jd(sp)``
    (the remaining-duration counter, decremented once per elapsed month and completing
    the job at 0, :25550).
    """

    type: int = 0
    pending_pay: int = 0
    months_left: int = 0


@dataclass(frozen=True)
class Debt:
    """Per-player debt.

    Renamed from the original ``kr(sp)`` to avoid colliding with the gangster
    stat ``kraft``.

    ``amount`` is ``kr(sp)`` (the outstanding loan-shark balance). ``months`` is
    ``kz(sp)`` — a grace-period counter set to 6 on borrowing (mf-prg.bas:15030) and
    0 on full repayment (:15075).

    The upkeep tick (``kz(sp)=kz(sp)+(kz(sp)>0)``, :4305) **DECREMENTS** the counter
    once per elapsed month while positive, and reaching 0 triggers the debt-collector
    encounter (:4350). This is the C64 ``true=-1`` reading (#47: C64 true is -1) — see
    the COUNTER DIRECTION section of
    ``data/game_configs/mafia_1920s/handlers/upkeep.py`` for the three source lines
    that pin it. The ``>0`` guard makes 0 a fixed point, which is what makes a won
    collectors fight recur every turn.
    """

    amount: int = 0
    months: int = 0


@dataclass(frozen=True)
class Business:
    """Per-player shop/business ownership.

    ``shop_tile`` is a tile, not an ownership flag: the original tracks ownership by
    WHICH ``kdh`` tile the player bought (an ``ln`` value), and shop-income/sale logic
    needs the tile to compute income, so a boolean would be lossy. ``0`` means "no shop"
    (``ln`` is 1-based in the source, so 0 is not a valid owned tile).
    """

    shop_tile: int = 0  # 0 = none; else the owned kdh tile's ln
    shop_capital: int = 0


@dataclass(frozen=True)
class Contraband:
    """Per-player contraband holdings (original per-player bitfield ``ag``)."""

    fake_papers: int = 0
    counterfeit: int = 0
    alcohol_barrels: int = 0  # ta(sp) — alcohol barrel stock (mf-prg.bas:1219,12035,12075)


@dataclass(frozen=True)
class Wanted:
    """Per-player wanted state; also carries the two win flags."""

    jail_months: int = 0
    bribe_months: int = 0
    x5: bool = False  # win flag — cash-transport event
    x6: bool = False  # win flag — mayor-hit event


@dataclass(frozen=True)
class Player:
    """A single player: identity, resources, roster, and owned subsystems.

    ``roster[0]`` is ALWAYS the player's own boss/persona gangster (matching
    ``mf-prg.bas:300``: ``gz(i)=1`` gives the player exactly one gangster at setup,
    named after the player, ``gn$(i,1)=sp$(i)`` — first-array-slot, i.e. index 0 here).
    There is no separate parallel "player stat" representation: the boss's
    kraft/intelligenz/brutalitaet/energie/weapon live entirely on this one
    ``Combatant`` entry, and every roster-length check (``gz(sp)``, e.g. the pub's
    10-gangster cap at :12105) counts the boss too. Later hires are appended after
    it. :func:`data.game_configs.mafia_1920s.setup.new_game` and every roster read
    site (``engine/conditions.py``'s ``gang_size``,
    ``data/game_configs/mafia_1920s/handlers/waf.py``) rely on this — there is no
    separate index shift.
    """

    name: str = ""
    gang_name: str = ""
    ka: int = 0  # cash (rolled 5000-7000 at setup, mf-prg.bas:315)
    gf: float = 0.0  # score/notoriety 0-100 (mf-prg.bas:1209)
    rank: int = 1  # ra(i) — rank 1..10, starts 1 "anfaenger" (mf-prg.bas:220)
    nr: int = 1  # next-rank counter, starts 1 (mf-prg.bas:220)
    po: int = 18  # start map position (mf-prg.bas:220, po(i)=18)
    vehicle: int = 0  # transport type index (tm)
    speed: int = 0
    ms: int = 0  # movement points (mf-prg.bas:1012); ms=0 forces turn end
    roster: tuple[Combatant, ...] = ()  # roster[0] is always the boss (see class docstring)
    jobs: Job = field(default_factory=Job)
    debt: Debt = field(default_factory=Debt)
    business: Business = field(default_factory=Business)
    contraband: Contraband = field(default_factory=Contraband)
    wanted: Wanted = field(default_factory=Wanted)
    tip_target: int = 0
    safe_skill: int = 0
    last_location: int = 0  # ln — within-location tile index 1..9 of the last entry
    last_la: int = 0  # la — location id of the last entry (0 = none)
    rented_months: int = 0  # um(sp) — prepaid rented months accumulator (mf-prg.bas:10040)
    #: This player's game state, declared by the config (:class:`StateSchema`): a
    #: frozen ``name -> value`` map the engine saves and restores without naming a key.
    values: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)

    def __post_init__(self):
        _coerce_readonly(self, "roster", "values")


@dataclass(frozen=True)
class MapState:
    """The city map (40 wide × 25 tall) and its per-tile/special-cell data.

    Distinct coordinate space from the combat grid (40×13) — never conflate.
    """

    grid: tuple[tuple[int, ...], ...] = ()  # 40×25 city map
    tenancy: Mapping[int, int] = field(
        default_factory=lambda: _EMPTY_MAP
    )  # per-tile tenancy by ln (orig uk)
    special_cells: Mapping[int, int] = field(default_factory=lambda: _EMPTY_MAP)  # e.g. 569, 861

    def __post_init__(self):
        _coerce_readonly(self, "grid", "tenancy", "special_cells")


@dataclass(frozen=True)
class Fighter:
    """A single combatant on the combat grid — the per-fighter setup snapshot.

    Mirrors the source's parallel per-side arrays: ``kp(s,f)`` (position),
    ``gw(s,f)`` (weapon), ``ec(f)``/``en`` (energy → the ``vitality`` slot), plus the
    per-side stats, which for a roster gangster are the gangster's own and for an NPC
    enemy are config data (the source's fixed ``bt=30:kr=30`` at ``mf-prg.bas:30245``).
    ``down`` ports ``kp(s,f)<0`` — a dead/downed fighter is marked rather than removed,
    so index-addressed arrays (``dir_memory``, UI panels) stay stable across a fight
    (mirrors ``30106/30109``'s dead-skip checks).

    **No game-stat fields.** Like :class:`Combatant`, the on-grid
    ``Fighter`` names no game stat: the depleting resource is the engine-named
    ``vitality`` SLOT (this game maps ``energie`` onto it at construction), and every
    other stat lives in the opaque ``attrs`` map. The engine reads/writes ``.vitality``
    directly and reads the rest through ``attrs`` by the role a config's rules bundle
    declares — it spells no game word. Stats are single-sourced (``vitality`` in its
    slot, the rest in ``attrs``), so a reloaded ``Fighter`` round-trips to the same
    shape (no double-store).

    A player-side fighter's ``name`` is the roster gangster's name (boss included);
    an enemy-side fighter's ``name`` comes from the ``StartCombat`` spec.
    """

    name: str = ""
    weapon: int = 0
    #: The ONE depleting resource, an engine slot like ``position``/``down``.
    #: The engine subtracts from it and clamps at zero; ``down`` derives from it hitting
    #: zero. This game NAMES it "energie" and maps that onto this slot at construction —
    #: the engine spells only the role, never the game's word.
    vitality: int = 0
    position: int = 0  # linear cell 0..520 on the 40×13 combat grid (CLAUDE.md)
    down: bool = False  # kp(s,f)<0 in the source — energy reached 0
    attrs: Mapping[str, int] = field(
        default_factory=lambda: _EMPTY_MAP
    )  # the OPAQUE stat map — see Combatant's docstring
    #: This fighter's CONSTRUCTED equipment: the stat mapping itself, not a key into
    #: a table the engine would have to hold. The game builds it from
    #: its own entity data before the fight starts, so combat reads no equipment data
    #: from outside the roster and there is no second source to disagree with.
    equipment: Mapping[str, int] = field(default_factory=lambda: _EMPTY_MAP)
    #: Which roster entry this fighter IS, for mapping the outcome back.
    #: A fight consumes a roster and returns consequences — vitality loss, who went
    #: down — that the caller has to apply to the right gangster. ``None`` for a
    #: fighter with no roster entry (every NPC/enemy).
    #:
    #: The engine never interprets it: it carries the value through and hands it back,
    #: exactly as it does ``attrs``. It exists because the alternative is matching on
    #: POSITION, which is only correct while side 1's order happens to equal roster
    #: order — an assumption nothing enforces and a scenario can break outright.
    roster_id: int | None = None

    def __post_init__(self):
        _coerce_readonly(self, "attrs", "equipment")

    # -- blueprint slots ------------------------------------------------------ #
    # The engine addresses a combatant through the four blueprint slots, not through
    # this game's field names. ``position``/``down``/``attrs``/``equipment`` already
    # carry their blueprint names; the alias below keeps engine code from spelling a
    # game-specific one. It is a property rather than a renamed field so the many
    # ``name=``/``weapon=`` construction sites stay valid.
    @property
    def identity(self) -> str:
        """The combatant's opaque label (this game: the gangster/NPC name)."""
        return self.name


@dataclass(frozen=True)
class CombatState:
    """The serializable combat-screen snapshot: populated at setup
    (:func:`engine.combat_setup.setup_combat`); the mid-fight evolution lives in
    :class:`engine.combat.CombatFight`, whose ``snapshot()`` freezes it back into this
    shape.

    ``sides`` holds side 1 (the active player's roster-as-fighters) and side 2 (the
    enemy party spawned from the ``StartCombat`` spec) as two fighter tuples — ports
    the source's parallel ``kp(1,*)``/``kp(2,*)`` arrays as one indexable pair rather
    than a bare 1/2 dict, matching ``Player``'s "no separate parallel array" style.
    ``grid`` is the 40×13 backdrop's wall/scenery codes, **linear** over cells 0..520
    inclusive (521 entries) — the kept 521-cell bound admits a partial 14th row, so a
    strict (row, col) shape would wrongly reject the legal cell 520 (CLAUDE.md).
    ``dir_memory`` is the enemy side's per-fighter direction memory ``ri(f)``
    (``mf-prg.bas:30020``: initialized to -1, "no last move yet"), keyed by enemy
    fighter index — the source only tracks this for the CPU side (``ks(2)=0``).
    ``active_side``/``active_fighter`` are the ``s``/``f`` activation cursors (1/2
    and 1-based fighter index, matching the source so the cursor bookkeeping in
    :mod:`engine.combat` needs no reindexing). ``losses`` mirrors ``v(1)``/``v(2)`` (per-side downed
    count). ``result_flag`` is the original ``s`` post-fight winner flag (0 = fight
    in progress / unset).
    """

    sides: tuple[tuple[Fighter, ...], tuple[Fighter, ...]] = ((), ())
    grid: tuple[int, ...] = ()  # 40×13 combat grid, LINEAR cells 0..520 — different space
    dir_memory: Mapping = field(
        default_factory=lambda: _EMPTY_MAP
    )  # ri() direction memory, enemy fighter index -> int
    active_side: int = 1  # s — 1 or 2
    active_fighter: int = 1  # f — 1-based index into sides[active_side-1]
    losses: tuple[int, int] = (0, 0)  # v(1), v(2) — per-side downed-fighter counts
    result_flag: int = 0  # original s at fight end; 0 = unset/in progress

    def __post_init__(self):
        _coerce_readonly(self, "grid", "dir_memory", "losses")
        object.__setattr__(self, "sides", tuple(tuple(side) for side in self.sides))


@dataclass(frozen=True)
class Clock:
    """Game calendar and player-turn bookkeeping.

    ``year``/``month`` jointly port the original's fractional-year calendar
    ``ja`` (mf-prg.bas:1010, ``ja = ja + 1/12``; the displayed/compared year is
    ``int(ja)``). This engine represents that same quantity as an integer year
    plus a 0-11 month counter rather than a float, so a full round (one lap of
    all players, mf-prg.bas:1010's ``sp=sp+1`` wrap) advances ``month`` by one
    and ``year`` only rolls over every 12 rounds — matching ``int(ja)``
    incrementing only once every 12 additions of ``1/12``.
    """

    year: int = 1925  # int(ja) — current year; starts at 1925 (mf-prg.bas:1000 ja=1925)
    month: int = 0  # the fractional part of ja, in twelfths (0-11); wraps year at 12
    end_year: int = 1978  # x9 — game-end year, validated [1928,1978] (mf-prg.bas:172)
    active_player: int = 0  # sp — active player index
    player_count: int = 1  # sz — player count, validated [1,4] (mf-prg.bas:206)


@dataclass(frozen=True)
class Config:
    """Rules/params (frozen per game at build time conceptually)."""

    score_mult: float = 1.0  # x8 — score-gain weight [0.1,2.0] (mf-prg.bas:176); scales gf += x*x8
    action_costs: Mapping[str, int] = field(default_factory=lambda: _EMPTY_MAP)
    formula_params: Mapping = field(default_factory=lambda: _EMPTY_MAP)

    def __post_init__(self):
        _coerce_readonly(self, "action_costs", "formula_params")


@dataclass(frozen=True)
class Flags:
    """Global flags, distinct from the per-player bitfields above.

    ``hired_gangsters`` ports ``sg(i)`` (mf-prg.bas:12106,12110,12165) — the GLOBAL
    (not per-player) set of the 30 recruit-candidate ids (0-based here; the source's
    ``i`` is 1-based) already hired by ANY player this game. It lives on ``Flags``
    rather than on ``Player`` because the source array has no player dimension: once
    a candidate is hired by one player, every player's recruit roll skips them
    (the pub's ``pub.recruit``). A tuple, not a ``set``/``frozenset``: every
    other read-only COLLECTION field in this module is a tuple or
    ``MappingProxyType`` so ``json_safe``/persistence's generic walkers handle it
    for free; a bare Python ``set`` is not JSON-serializable and would need its own
    special-cased round-trip.
    """

    graphics_mode: int = 0
    loaded: bool = False
    hired_gangsters: tuple[int, ...] = ()

    def __post_init__(self):
        _coerce_readonly(self, "hired_gangsters")


@dataclass(frozen=True)
class GameState:
    """Top-level game state aggregating all subsystems."""

    players: tuple[Player, ...] = ()
    map: MapState = field(default_factory=MapState)
    combat: CombatState = field(default_factory=CombatState)
    clock: Clock = field(default_factory=Clock)
    config: Config = field(default_factory=Config)
    flags: Flags = field(default_factory=Flags)
    #: Game state with no player dimension, declared by the config
    #: (:class:`StateSchema`): the same kind of frozen map as :attr:`Player.values`.
    values: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAP)

    def __post_init__(self):
        _coerce_readonly(self, "players", "values")


# --------------------------------------------------------------------------- #
# Declared value maps — the config's state schema                             #
# --------------------------------------------------------------------------- #
#: The value types a schema may declare, by the name a config writes.
_VALUE_TYPES: dict[str, type] = {"int": int, "float": float, "bool": bool, "str": str}


class StateSchemaError(ValueError):
    """A malformed state schema, or saved values that do not fit the schema."""


@dataclass(frozen=True)
class ValueSpec:
    """One declared value-map key: its ``name``, value ``type`` and ``default``."""

    name: str
    type: type
    default: Any


def _fits(spec_type: type, value: Any) -> bool:
    # Exact type: ``True`` is an int to Python and ``1 == 1.0``, but a bool in an int
    # key or an int in a float key is the drift a save must not carry.
    return type(value) is spec_type


def _parse_specs(section: Any, where: str) -> Mapping[str, ValueSpec]:
    if not isinstance(section, Mapping):
        raise StateSchemaError(f"state.{where} must be a mapping of name -> {{type, default}}")
    specs: dict[str, ValueSpec] = {}
    for name, raw in section.items():
        if not isinstance(raw, Mapping):
            raise StateSchemaError(f"state.{where}.{name} must be a mapping with type and default")
        unknown = set(raw) - {"type", "default"}
        if unknown:
            raise StateSchemaError(f"state.{where}.{name} has unknown field(s) {sorted(unknown)}")
        type_name = raw.get("type")
        if type_name not in _VALUE_TYPES:
            raise StateSchemaError(
                f"state.{where}.{name} declares type {type_name!r}; "
                f"expected one of {sorted(_VALUE_TYPES)}"
            )
        if "default" not in raw:
            raise StateSchemaError(f"state.{where}.{name} declares no default")
        value_type = _VALUE_TYPES[type_name]
        default = raw["default"]
        if value_type is float and type(default) is int:
            default = float(default)  # YAML writes ``0`` for a float's zero
        if not _fits(value_type, default):
            raise StateSchemaError(
                f"state.{where}.{name}: default {default!r} is not a {type_name}"
            )
        specs[name] = ValueSpec(name=name, type=value_type, default=default)
    return MappingProxyType(specs)


def _load_values(specs: Mapping[str, ValueSpec], raw: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise StateSchemaError(f"{where}: saved values must be a mapping")
    unknown = sorted(set(raw) - set(specs))
    if unknown:
        raise StateSchemaError(
            f"{where}: saved key(s) {unknown} are not declared in the config's state schema"
        )
    loaded: dict[str, Any] = {}
    for name, spec in specs.items():
        if name not in raw:
            loaded[name] = spec.default
            continue
        value = raw[name]
        if not _fits(spec.type, value):
            raise StateSchemaError(
                f"{where}: saved key {name!r} holds {value!r}, not a {spec.type.__name__}"
            )
        loaded[name] = value
    return MappingProxyType(loaded)


@dataclass(frozen=True)
class StateSchema:
    """The config's declared value maps: names, types and defaults.

    ``player`` declares the keys of every :attr:`Player.values`; ``global_`` those of
    :attr:`GameState.values`. A config writes it as the ``state`` section of its
    ``config.yaml``::

        state:
          player:
            counter: {type: int, default: 0}
          global:
            round_bonus: {type: float, default: 0.0}

    On load a missing key takes its declared default and an unknown key is refused,
    so a later change can add a key without a save-format bump.
    """

    player: Mapping[str, ValueSpec] = field(default_factory=lambda: _EMPTY_MAP)
    global_: Mapping[str, ValueSpec] = field(default_factory=lambda: _EMPTY_MAP)

    @classmethod
    def from_dict(cls, raw: Any) -> "StateSchema":
        """Parse a config's ``state`` section; ``None`` declares two empty maps."""
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise StateSchemaError("state must be a mapping with 'player' and/or 'global'")
        unknown = set(raw) - {"player", "global"}
        if unknown:
            raise StateSchemaError(
                f"state has unknown section(s) {sorted(unknown)}; expected 'player', 'global'"
            )
        return cls(
            player=_parse_specs(raw.get("player", {}), "player"),
            global_=_parse_specs(raw.get("global", {}), "global"),
        )

    def player_defaults(self) -> Mapping[str, Any]:
        """A fresh player's value map: every declared key at its default."""
        return MappingProxyType({n: s.default for n, s in self.player.items()})

    def global_defaults(self) -> Mapping[str, Any]:
        """A fresh game's global value map: every declared key at its default."""
        return MappingProxyType({n: s.default for n, s in self.global_.items()})

    def load_player_values(self, raw: Any, *, where: str = "player") -> Mapping[str, Any]:
        """Restore one saved player map: default-fill, refuse unknown or mistyped keys."""
        return _load_values(self.player, raw, where)

    def load_global_values(self, raw: Any, *, where: str = "global") -> Mapping[str, Any]:
        """Restore the saved global map: default-fill, refuse unknown or mistyped keys."""
        return _load_values(self.global_, raw, where)

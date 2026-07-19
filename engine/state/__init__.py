"""Modular ``GameState`` dataclasses for the first vertical slice.

Each subsystem owns its own data (docs/design/engine-architecture.md). This is scaffolding only:
happy-path construction with sensible defaults, no game logic or formulas.
Combat and wanted subsystems are empty stubs. Source-variable names from the
decompiled BASIC (``mf-prg.bas``) are noted in comments where helpful.

Pure dataclasses + stdlib only; the ``engine/`` package imports nothing from
``server/``, ``clients/``, or any transport/render library, and holds no
display text.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: Shared empty read-only mapping — a safe immutable default (R2).
_EMPTY_MAP: Mapping = MappingProxyType({})


def freeze(value):
    """Recursively convert plain containers into read-only ones (R2/KTD-2).

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
    ``list`` a handler could append to (R2). Structural sharing is implicit — untouched
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


def json_safe(value):
    """Recursively convert a frozen state graph into plain JSON-safe containers.

    The inverse of :func:`freeze`: read-only mappings unwrap to ``dict`` and tuples
    to ``list``. Needed because neither read-only form survives JSON, and
    ``mappingproxy`` is not even picklable — so ``dataclasses.asdict`` (which
    deepcopies internally) cannot walk a frozen graph at all.

    Lives here rather than in ``engine.persistence`` because it is generic
    graph-walking, not save-format logic: persistence serializes with it, and the
    test purity harness snapshots state values with it.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_safe(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def _coerce_readonly(instance, *field_names) -> None:
    """Force ``instance``'s named collection fields into read-only form (R2).

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
    "Gangster",
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
    # NOTE: `freeze`, `json_safe`, and `tuple_replace` are deliberately NOT exported.
    # docs/design/config-and-content-contract.md § Handler API admits only "engine
    # helpers for shared BASIC subroutines" (not-enough-money, gangster picker,
    # score update, combat entry) — generic container plumbing is not that category.
    # They stay importable for engine-internal use (effects, movement, persistence)
    # and tests; keeping them out of __all__ keeps the advertised config-facing
    # surface minimal (CLAUDE.md § 5.2a).
]


@dataclass(frozen=True)
class Gangster:
    """A single gangster in a player's roster.

    Unpacks the original packed 8-char ``ge$`` stat string into plain ints.
    """

    name: str = ""
    weapon: int = 0  # weapon index 0..8; 0 = unarmed
    energie: int = 5  # starting energy fixed at 5 (mf-prg.bas:313, en=5)
    kraft: int = 0  # strength stat (gangster stat "kraft"); rolled 10-50 at setup
    intelligenz: int = 0  # mf-prg.bas:311 quirk "in = x OR 30"; roll happens at setup
    brutalitaet: int = 0  # brutality (mf-prg.bas:312); later a combat damage bonus


@dataclass(frozen=True)
class Job:
    """A pending job/contract for a player.

    Field semantics confirmed against the source (mf-prg.bas:12308-12335,25550-25560):
    ``type`` is ``jo(sp)`` (the accepted job's type id; 0 = no job), ``pending_pay`` is
    ``jl(sp)`` (the lump sum paid out when the job completes — despite the source
    comment "monthly pay", it is a single payout on completion, not a per-turn wage;
    see the Product Contract's R6/F3 correction), and ``months_left`` is ``jd(sp)``
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
    stat ``kraft`` — a required correctness point for this unit.

    ``amount`` is ``kr(sp)`` (the outstanding loan-shark balance). ``months`` is
    ``kz(sp)`` — a grace-period counter that increments once per elapsed month while
    positive and resets to 6 on borrowing (mf-prg.bas:15030) / 0 on full repayment
    (:15075); reaching 0 again after having been positive triggers the debt-collector
    encounter (:4305,4350). Its exact upkeep tick (the relational-sign-flagged
    ``kz(sp)=kz(sp)+(kz(sp)>0)`` at :4305) is a later unit's (U3's) concern — this
    field only needs to hold the value here.
    """

    amount: int = 0
    months: int = 0


@dataclass(frozen=True)
class Business:
    """Per-player shop/business ownership.

    ``shop_tile`` replaces the earlier ``shop_owner: bool`` (KTD-7 groundwork): the
    original tracks ownership by WHICH ``kdh`` tile the player bought (an ``ln``
    value), not a bare flag — a later unit's shop-income/sale logic needs the tile
    to compute income, so the boolean was a lossy placeholder. ``0`` means "no shop"
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

    ``roster[0]`` is ALWAYS the player's own boss/persona gangster (KTD-6, matching
    ``mf-prg.bas:300``: ``gz(i)=1`` gives the player exactly one gangster at setup,
    named after the player, ``gn$(i,1)=sp$(i)`` — first-array-slot, i.e. index 0 here).
    There is no separate parallel "player stat" representation: the boss's
    kraft/intelligenz/brutalitaet/energie/weapon live entirely on this one
    ``Gangster`` entry, and every roster-length check (``gz(sp)``, e.g. the pub's
    10-gangster cap at :12105) counts the boss too. Later hires are appended after
    it. This is already how :func:`data.game_configs.mafia_1920s.setup.new_game`
    and every roster read site (``engine/conditions.py``'s ``gang_size``,
    ``data/game_configs/mafia_1920s/handlers/waf.py``) are written — there is no
    separate index shift to perform.
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
    roster: tuple[Gangster, ...] = ()  # roster[0] is always the boss (see class docstring)
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

    def __post_init__(self):
        _coerce_readonly(self, "roster")


@dataclass(frozen=True)
class MapState:
    """The city map (40 wide × 25 tall) and its per-tile/special-cell data.

    Distinct coordinate space from the combat grid (40×13) — never conflate.
    """

    grid: tuple[tuple[int, ...], ...] = ()  # 40×25 city map
    tenancy: Mapping[int, int] = _EMPTY_MAP  # per-tile tenancy by ln (orig uk)
    special_cells: Mapping[int, int] = _EMPTY_MAP  # e.g. 569, 861

    def __post_init__(self):
        _coerce_readonly(self, "grid", "tenancy", "special_cells")


@dataclass(frozen=True)
class Fighter:
    """A single combatant on the combat grid — the per-fighter setup snapshot (U4).

    Mirrors the source's parallel per-side arrays: ``kp(s,f)`` (position),
    ``gw(s,f)`` (weapon), ``ec(f)``/``en`` (energy), plus kraft/brutalitaet, which
    for a roster gangster are the gangster's own stats and for an NPC enemy are the
    fixed 30/30 (``mf-prg.bas:30245``). ``down`` ports ``kp(s,f)<0`` — a dead/downed
    fighter is marked rather than removed, so index-addressed arrays (``dir_memory``,
    UI panels) stay stable across a fight (mirrors ``30106/30109``'s dead-skip checks).

    A player-side fighter's ``name`` is the roster gangster's name (boss included,
    KTD-6); an enemy-side fighter's ``name`` comes from the ``StartCombat`` spec.
    """

    name: str = ""
    weapon: int = 0
    energie: int = 0
    kraft: int = 0
    brutalitaet: int = 0
    position: int = 0  # linear cell 0..520 on the 40×13 combat grid (CLAUDE.md)
    down: bool = False  # kp(s,f)<0 in the source — energy reached 0


@dataclass(frozen=True)
class CombatState:
    """The serializable combat-screen snapshot: populated at setup (U4), evolves
    each activation (U5), finalized by outcome effects (U5).

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
    and 1-based fighter index, matching the source so the cursor bookkeeping in U5
    needs no reindexing). ``losses`` mirrors ``v(1)``/``v(2)`` (per-side downed
    count). ``result_flag`` is the original ``s`` post-fight winner flag (0 = fight
    in progress / unset).
    """

    sides: tuple[tuple[Fighter, ...], tuple[Fighter, ...]] = ((), ())
    grid: tuple[int, ...] = ()  # 40×13 combat grid, LINEAR cells 0..520 — different space
    dir_memory: Mapping = _EMPTY_MAP  # ri() direction memory, enemy fighter index -> int
    active_side: int = 1  # s — 1 or 2
    active_fighter: int = 1  # f — 1-based index into sides[active_side-1]
    losses: tuple[int, int] = (0, 0)  # v(1), v(2) — per-side downed-fighter counts
    result_flag: int = 0  # original s at fight end; 0 = unset/in progress

    def __post_init__(self):
        _coerce_readonly(self, "grid", "dir_memory", "losses")
        object.__setattr__(
            self, "sides", tuple(tuple(side) for side in self.sides)
        )


@dataclass(frozen=True)
class Clock:
    """Game calendar and player-turn bookkeeping.

    ``year``/``month`` jointly port the original's fractional-year calendar
    ``ja`` (mf-prg.bas:1010, ``ja = ja + 1/12``; the displayed/compared year is
    ``int(ja)``). This engine represents that same quantity as an integer year
    plus a 0-11 month counter rather than a float, so a full round (one lap of
    all players, mf-prg.bas:1010's ``sp=sp+1`` wrap) advances ``month`` by one
    and ``year`` only rolls over every 12 rounds — matching ``int(ja)``
    incrementing only once every 12 additions of ``1/12`` (KTD-4).
    """

    year: int = 1928  # int(ja) — current year; floor 1928 (mf-prg.bas:170,172)
    month: int = 0  # the fractional part of ja, in twelfths (0-11); wraps year at 12
    end_year: int = 1978  # x9 — game-end year, validated [1928,1978] (mf-prg.bas:172)
    active_player: int = 0  # sp — active player index
    player_count: int = 1  # sz — player count, validated [1,4] (mf-prg.bas:206)


@dataclass(frozen=True)
class Config:
    """Rules/params (frozen per game at build time conceptually)."""

    score_mult: float = 1.0  # x8 — score-gain weight [0.1,2.0] (mf-prg.bas:176); scales gf += x*x8
    action_costs: Mapping[str, int] = _EMPTY_MAP
    formula_params: Mapping = _EMPTY_MAP

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
    (U9's ``pub.recruit``). A tuple, not a ``set``/``frozenset`` (R2/KTD-7): every
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

    def __post_init__(self):
        _coerce_readonly(self, "players")

"""Modular ``GameState`` dataclasses for the first vertical slice.

Each subsystem owns its own data (docs/design/engine-architecture.md). This is scaffolding only:
happy-path construction with sensible defaults, no game logic or formulas.
Combat and wanted subsystems are empty stubs. Source-variable names from the
decompiled BASIC (``mf-prg.bas``) are noted in comments where helpful.

Pure dataclasses + stdlib only; the ``engine/`` package imports nothing from
``server/``, ``clients/``, or any transport/render library, and holds no
display text.
"""

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
    "CombatState",
    "Clock",
    "Config",
    "Flags",
    "GameState",
    "freeze",
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
    """A pending job/contract for a player."""

    type: int = 0
    pending_pay: int = 0
    months_left: int = 0


@dataclass(frozen=True)
class Debt:
    """Per-player debt.

    Renamed from the original ``kr(sp)`` to avoid colliding with the gangster
    stat ``kraft`` — a required correctness point for this unit.
    """

    amount: int = 0
    months: int = 0


@dataclass(frozen=True)
class Business:
    """Per-player shop/business ownership."""

    shop_owner: bool = False
    shop_capital: int = 0


@dataclass(frozen=True)
class Contraband:
    """Per-player contraband holdings (original per-player bitfield ``ag``)."""

    fake_papers: int = 0
    counterfeit: int = 0
    alcohol_barrels: int = 0


@dataclass(frozen=True)
class Wanted:
    """Per-player wanted state; also carries the two win flags."""

    jail_months: int = 0
    bribe_months: int = 0
    x5: bool = False  # win flag — cash-transport event
    x6: bool = False  # win flag — mayor-hit event


@dataclass(frozen=True)
class Player:
    """A single player: identity, resources, roster, and owned subsystems."""

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
    roster: tuple[Gangster, ...] = ()
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
class CombatState:
    """Empty stub — no behavior this unit."""

    enemy_roster: tuple = ()
    grid: tuple[tuple[int, ...], ...] = ()  # 40×13 combat grid — different space
    dir_memory: Mapping = _EMPTY_MAP  # ri() direction memory
    result_flag: int = 0  # original s

    def __post_init__(self):
        _coerce_readonly(self, "enemy_roster", "grid", "dir_memory")


@dataclass(frozen=True)
class Clock:
    """Game calendar and player-turn bookkeeping."""

    year: int = 1928  # ja — current year; floor 1928 (mf-prg.bas:170,172)
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
    """Global flags, distinct from the per-player bitfields above."""

    graphics_mode: int = 0
    loaded: bool = False


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

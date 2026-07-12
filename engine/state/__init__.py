"""Modular ``GameState`` dataclasses for the first vertical slice.

Each subsystem owns its own data (PLAN.md §4). This is scaffolding only:
happy-path construction with sensible defaults, no game logic or formulas.
Combat and wanted subsystems are empty stubs. Source-variable names from the
decompiled BASIC (``mf-prg.bas``) are noted in comments where helpful.

Pure dataclasses + stdlib only; the ``engine/`` package imports nothing from
``server/``, ``clients/``, or any transport/render library, and holds no
display text.
"""

from dataclasses import dataclass, field

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
]


@dataclass
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


@dataclass
class Job:
    """A pending job/contract for a player."""

    type: int = 0
    pending_pay: int = 0
    months_left: int = 0


@dataclass
class Debt:
    """Per-player debt.

    Renamed from the original ``kr(sp)`` to avoid colliding with the gangster
    stat ``kraft`` — a required correctness point for this unit.
    """

    amount: int = 0
    months: int = 0


@dataclass
class Business:
    """Per-player shop/business ownership."""

    shop_owner: bool = False
    shop_capital: int = 0


@dataclass
class Contraband:
    """Per-player contraband holdings (original per-player bitfield ``ag``)."""

    fake_papers: int = 0
    counterfeit: int = 0
    alcohol_barrels: int = 0


@dataclass
class Wanted:
    """Per-player wanted state; also carries the two win flags."""

    jail_months: int = 0
    bribe_months: int = 0
    x5: bool = False  # win flag — cash-transport event
    x6: bool = False  # win flag — mayor-hit event


@dataclass
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
    roster: list[Gangster] = field(default_factory=list)
    jobs: Job = field(default_factory=Job)
    debt: Debt = field(default_factory=Debt)
    business: Business = field(default_factory=Business)
    contraband: Contraband = field(default_factory=Contraband)
    wanted: Wanted = field(default_factory=Wanted)
    tip_target: int = 0
    safe_skill: int = 0
    last_location: int = 0


@dataclass
class MapState:
    """The city map (40 wide × 25 tall) and its per-tile/special-cell data.

    Distinct coordinate space from the combat grid (40×13) — never conflate.
    """

    grid: list[list[int]] = field(default_factory=list)  # 40×25 city map; U1 fills it
    tenancy: dict[int, int] = field(default_factory=dict)  # per-tile tenancy by ln (orig uk)
    special_cells: dict[int, int] = field(default_factory=dict)  # e.g. 569, 861


@dataclass
class CombatState:
    """Empty stub — no behavior this unit."""

    enemy_roster: list = field(default_factory=list)
    grid: list[list[int]] = field(default_factory=list)  # 40×13 combat grid — different space
    dir_memory: dict = field(default_factory=dict)  # ri() direction memory
    result_flag: int = 0  # original s


@dataclass
class Clock:
    """Game calendar and player-turn bookkeeping."""

    year: int = 1928  # ja — current year; floor 1928 (mf-prg.bas:170,172)
    end_year: int = 1978  # x9 — game-end year, validated [1928,1978] (mf-prg.bas:172)
    active_player: int = 0  # sp — active player index
    player_count: int = 1  # sz — player count, validated [1,4] (mf-prg.bas:206)


@dataclass
class Config:
    """Rules/params (frozen per game at build time conceptually)."""

    score_mult: float = 1.0  # x8 — score-gain weight [0.1,2.0] (mf-prg.bas:176); scales gf += x*x8
    action_costs: dict[str, int] = field(default_factory=dict)
    formula_params: dict = field(default_factory=dict)


@dataclass
class Flags:
    """Global flags, distinct from the per-player bitfields above."""

    graphics_mode: int = 0
    loaded: bool = False


@dataclass
class GameState:
    """Top-level game state aggregating all subsystems."""

    players: list[Player] = field(default_factory=list)
    map: MapState = field(default_factory=MapState)
    combat: CombatState = field(default_factory=CombatState)
    clock: Clock = field(default_factory=Clock)
    config: Config = field(default_factory=Config)
    flags: Flags = field(default_factory=Flags)

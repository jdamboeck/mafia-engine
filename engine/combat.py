"""Combat core: the 40×13 grid model and the rules of one fight.

Combat is genre-engine machinery (docs/design/), not config code. This module holds the
grid constants, the two distinct obstruction predicates, :class:`CombatResult`, the
game-supplied :class:`RulesBundle`, and :class:`CombatFight` (the mutable per-fight
working state: activation cursor, moves, shots, the CPU decision, surrender) with its
read-only :class:`CombatView`. It is NOT a generator: the yield/send loop that drives a
fight lives in :mod:`engine.fight_loop`, side placement/setup in
:mod:`engine.combat_setup`, and CPU target selection in :mod:`engine.combat_ai`.

**Grid representation.** The original bounds the combat grid at cell 520 inclusive
(``mf-prg.bas:30145``: ``p<br or p>br+520``), so movement/shots range over 521 cells
(0..520) on a nominally 40-wide, 13-row grid. 521 = 13*40 + 1 — one cell of a partial
14th row. A strict ``(row, col)`` shape with 13 rows would wrongly reject the legal
cell 520, so the grid (and every position) is represented **linearly**: ``CombatState.grid``
is a flat 521-entry tuple indexed 0..520, and a fighter's ``position`` is that same linear
index (``row, col = divmod(position, GRID_COLS)`` recovers 2D coordinates for rendering).

**Two distinct obstruction sets** (do not collapse them — CLAUDE.md):

- **Movement** (``mf-prg.bas:30145``): blocked by ANY occupied-or-scenery cell — the
  target must show code 32 (space) or 96 (a second empty-look glyph) to be walkable.
  Every other code (walls, decorative scenery, another fighter's sprite code 193)
  blocks a step.
- **Shots** (``mf-prg.bas:30225-30226``): blocked ONLY by wall cells (160 or 156) and
  the grid bounds. A shot's projectile freely overflies scenery/other-fighter cells
  that would block a MOVE — it only stops at an actual wall, the edge, or a hit.

**Backdrop walls.** The three combat backdrops (``ks``/``kp``/``km``) are the
same 2003-byte C64 screen-file format as the city map (``research/src/karte``, decoded
by ``tools/decode_city_map.py``): 1000 screen-code bytes stored bottom-up/right-to-left,
so the row-major codes are ``data[:1000][::-1]`` (mirrors
``research/tools/render_c64_assets.py:106-119``'s ``parse_screen_file``). Combat only
plays out over the FIRST 521 of those 1000 decoded cells (the grid's 0..520 bound) —
rows 14..24 of the loaded screen are never addressed by the fight loop. The three
backdrops are pre-decoded once into ``data/game_configs/mafia_1920s/content/combat/*.yaml``
(mirroring the city map's committed-artifact pattern) rather than parsed at runtime, so
the engine never depends on the research checkout at runtime (the sibling of the
"engine imports nothing from server/clients" rule — config data must not require research
either).

``engine/`` imports nothing from ``server``/``clients``/transport, and this module holds
no display text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from engine.state import HOUSE_RULE_SETTINGS, CombatState, Fighter

if TYPE_CHECKING:
    from engine.combat_ai import AiTarget

#: Shared empty role map — a bundle that declares no roles for a capability.
_EMPTY_ROLES: Mapping[str, str] = MappingProxyType({})

__all__ = [
    "GRID_COLS",
    "GRID_ROWS",
    "CELL_COUNT",
    "MAX_CELL",
    "MOVE_EMPTY_CODES",
    "SHOT_WALL_CODES",
    "RANGE_MELEE",
    "RulesBundle",
    "CombatResult",
    "DEFAULT_RANGE",
    "STEP_LEFT",
    "STEP_RIGHT",
    "STEP_UP",
    "STEP_DOWN",
    "STEPS",
    "can_move_onto",
    "blocks_shot",
    "CombatFight",
    "CombatView",
]

# --------------------------------------------------------------------------- #
# Grid constants                                                              #
# --------------------------------------------------------------------------- #
GRID_COLS = 40  # combat grid width (mf-prg.bas:systems-analysis "combat grid 40x13")
GRID_ROWS = 13  # nominal row count; the 521-cell bound admits a partial 14th row
CELL_COUNT = 521  # 0..520 inclusive (mf-prg.bas:30145, 30225 — the source's own bound)
MAX_CELL = CELL_COUNT - 1  # 520 — MUST be legally reachable (CLAUDE.md)

#: Movement target codes that are walkable (mf-prg.bas:30145: peek(p)=32 or 96).
MOVE_EMPTY_CODES = (32, 96)

#: Shot-blocking wall codes (mf-prg.bas:30225-30226: peek(br+p)=160 or 156).
SHOT_WALL_CODES = (160, 156)


# --------------------------------------------------------------------------- #
# Obstruction predicates — two distinct sets (do not collapse)                #
# --------------------------------------------------------------------------- #
def can_move_onto(cell: int, grid: tuple[int, ...], occupied: frozenset[int]) -> bool:
    """Can a fighter step onto ``cell``? Ports ``mf-prg.bas:30145``.

    Blocked by: out of bounds, any non-empty backdrop code (only 32/96 are walkable —
    this also blocks walls AND plain scenery, unlike a shot), or another fighter
    already standing there (``occupied`` — the backdrop alone cannot express this,
    since a fighter's own sprite code 193 is painted onto the grid at runtime in the
    source; this engine keeps fighter occupancy out of the static ``grid`` array and
    checks it as a caller-supplied set instead, so the immutable backdrop config never
    needs to be rewritten mid-fight).
    """
    if cell < 0 or cell > MAX_CELL:
        return False
    if cell in occupied:
        return False
    code = grid[cell] if cell < len(grid) else 32  # unpopulated grid = open ground
    return code in MOVE_EMPTY_CODES


def blocks_shot(cell: int, grid: tuple[int, ...]) -> bool:
    """Would a shot's projectile stop at ``cell``? Ports ``mf-prg.bas:30225-30226``.

    True if ``cell`` is out of bounds or holds a wall code (160/156). Unlike
    :func:`can_move_onto`, plain scenery and occupied cells do NOT block a shot — only
    walls and the grid edge do (the source's projectile step only checks
    ``peek(br+p)=160 or 156``, not the fighter-occupancy check ``30145`` uses).
    """
    if cell < 0 or cell > MAX_CELL:
        return True
    code = grid[cell] if cell < len(grid) else 32
    return code in SHOT_WALL_CODES


# --------------------------------------------------------------------------- #
# Melee reach + the two combat rolls                                          #
# --------------------------------------------------------------------------- #
#: Longest range that still reaches only the ADJACENT cell — the engine's definition
#: of a melee weapon. A shot steps one cell per range point (``mf-prg.bas:30220``),
#: so anything at or below this can never out-reach a neighbour, and a fighter
#: carrying it must close the distance to attack at all.
#:
#: This replaces the source's own melee test, which spelled the same set out as a
#: weapon-id comparison (``30415``: ``orgw(...)<4``) because the original's range
#: table was a hardcoded ladder (``30215``: ``r=2`` widened to 15 by ``ifw>3``).
#: Per-weapon ranges are CONFIG data now, so the taxonomy is derived, not enumerated
#: — verified equivalent across all nine of the reference title's weapons.
RANGE_MELEE = 2

#: The default range for a weapon whose config omits one, matching the source's own
#: base ``r=2`` (``mf-prg.bas:30215``) — an unspecified weapon reaches one cell.
DEFAULT_RANGE = RANGE_MELEE

#: Linear step deltas for the four grid directions (``mf-prg.bas:30130-30133`` and
#: ``30206-30209``): left/right are ±1, up/down are ∓ one row width (±40). These are
#: the *only* legal move/aim deltas — the source reads exactly four keys.
STEP_LEFT = -1
STEP_RIGHT = 1
STEP_UP = -GRID_COLS
STEP_DOWN = GRID_COLS
STEPS: tuple[int, ...] = (STEP_LEFT, STEP_RIGHT, STEP_UP, STEP_DOWN)


# --------------------------------------------------------------------------- #
# The fight's result — winner + per-side losses handed back to the caller     #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CombatResult:
    """What a finished fight hands back to the invoking handler.

    Flat and frozen, mirroring :class:`~engine.effects.CommitResult` and
    :class:`~engine.actions.EngineResult` — the two facts every caller needs and
    nothing more:

    ``winner``
        The side that won: ``1`` (the acting player's roster) or ``2`` (the enemy),
        as :func:`engine.fight_loop._run_combat` computes it.
    ``losses``
        The real per-side death tallies at the moment the fight ended —
        ``(v(1), v(2))`` from ``mf-prg.bas:30100``/``:30310``, read straight off
        :attr:`CombatFight.losses`. A handler narrates these rather than deriving a
        loss count from ``winner``, which is wrong for any multi-fighter side.

    ``last_shooter``
        The fighter who fired the fight's last shot, as it stood when it fired, or
        ``None`` if nobody fired. A shot is any fire order in one of the four
        directions, hit or miss (``mf-prg.bas:30215`` ``w=gw(ks(s),f)`` runs for every
        one), so for a fight won by a kill it is the fighter who landed the killing
        blow. A caller that reads the shooter's equipment after the fight (the source
        leaves its weapon in ``w``) reads it here.

    ``roster_vitality``
        Side 1's ``vitality`` when the fight ended, as ``(roster_id, vitality)`` pairs
        in roster order, for every fighter that came from a roster slot. The fight's
        energy write-back (one :class:`~engine.effects.EnergyChange` per changed
        fighter) is BUFFERED into the invoking action, so a handler that runs a second
        fight in the same action does not see it in ``ctx.state``; it builds that
        fight's player side from these values instead. Empty when nothing reported it
        (a result built by hand).

    ``owner_vitality``
        The same closing values for every fighter a player owns (KTD-14), on either
        side, as ``(owner, ((roster_id, vitality), ...))`` per owner;
        :meth:`vitality_of` reads one owner's pairs. A fight between two players'
        rosters reports both gangs here. Empty when no fighter has an owner.

    Deliberately carries **no** ``state``/``sides``: the contract is winner + losses +
    the last shooter + the closing vitalities; the post-fight board is not part of it.
    """

    winner: int
    losses: tuple[int, int]
    last_shooter: Fighter | None = None
    roster_vitality: tuple[tuple[int, int], ...] = ()
    owner_vitality: tuple[tuple[int, tuple[tuple[int, int], ...]], ...] = ()

    def vitality_of(self, owner: int) -> tuple[tuple[int, int], ...]:
        """``owner``'s closing ``(roster_id, vitality)`` pairs; empty if it owns no fighter."""
        return dict(self.owner_vitality).get(owner, ())


# --------------------------------------------------------------------------- #
# The rules bundle — how a game answers the engine's combat questions          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RulesBundle:
    """The formulas and attribute roles a fight runs on — supplied by the GAME.

    The engine owns *when* a hit test happens and what its result means; it does not
    own the formula. A config passes one of these to :class:`CombatFight` beside its
    ``rng``. There is deliberately no
    global registry: two differently-ruled fights must be able to coexist in one
    process (a genre-engine requirement, not a hypothetical — a scenario harness runs
    several at once).

    **Roles group per capability, not one flat map.** ``hit_roles`` and
    ``damage_roles`` each map a *participant* role name ("attacker") to the attribute
    key that capability reads off that participant. A single flat ``{role: key}`` map
    could not express WHOSE attribute a role reads. The reference title only ever
    reads the attacker's, but the genre contract needs defender-side ``evasion`` or
    ``initiative`` later, and that must not be a breaking change.

    Fields:

    ``hit_roles`` / ``hit_fn``
        The hit test's role map and its formula ``(attacker, equipment, rng) -> bool``.
    ``damage_roles`` / ``damage_fn``
        The damage roll's role map and its formula ``(attacker, equipment, rng) -> int``.
    ``hit_draws`` / ``damage_draws`` (optional)
        What the matching formula draws, for a debug viewer to name each draw in a
        recording without restating the formula: ``(attacker, equipment) ->
        ((label, bound), ...)``, one ``rng.range(bound)`` per entry in draw order. A
        bound of 0 is never drawn. The game's formula draws WITH these bounds, so the
        two cannot disagree. The engine never calls them.
    ``house_rules``
        The game's house-rules map the formulas were built under (a switch id ->
        :data:`~engine.state.FAITHFUL` / :data:`~engine.state.INTENT`), frozen. Plain
        data the engine never reads by id: a recording stores it and a replay compares
        it (:mod:`engine.recording`), so a fight never replays under other choices.
    ``direction_memory_per_side``
        Whose direction memory an AI fighter reads and writes. ``False`` (the default)
        keys it by the fighter's number alone, as the reference title's ``ri(f)`` does
        (``mf-prg.bas:30492``): when both sides are AI-driven, fighter ``f`` of one
        side and fighter ``f`` of the other share one memory. ``True`` gives each
        side's fighters their own. A neutral mechanism setting: the game sets it,
        from whatever rule it likes; the engine never reads a house rule by its id.
    There is deliberately **no** ``vitality`` entry: the depleting
    resource is the engine's :attr:`~engine.state.Fighter.vitality` SLOT, which the
    engine reads and writes directly. A bundle field naming which attrs key held it
    was a second name for one thing — it drove a lookup the slot makes unnecessary.
    There is likewise **no** ``equipment_stats`` entry: equipment stats
    live on the combatant, so a formula reads them off the attacker it was handed. A
    bundle-held lookup would be a second source able to disagree with the roster about
    what a weapon does.
    """

    hit_roles: Mapping[str, str] = field(default_factory=lambda: _EMPTY_ROLES)
    hit_fn: Any = None
    damage_roles: Mapping[str, str] = field(default_factory=lambda: _EMPTY_ROLES)
    damage_fn: Any = None
    hit_draws: Any = None
    damage_draws: Any = None
    house_rules: Mapping[str, str] = field(default_factory=lambda: _EMPTY_ROLES)
    direction_memory_per_side: bool = False

    def __post_init__(self) -> None:
        for rule_id, setting in self.house_rules.items():
            if setting not in HOUSE_RULE_SETTINGS:
                raise ValueError(
                    f"house rule {rule_id!r} is set to {setting!r}; "
                    f"expected one of {list(HOUSE_RULE_SETTINGS)}"
                )
        object.__setattr__(self, "house_rules", MappingProxyType(dict(self.house_rules)))

    def required_keys(self) -> tuple[str, ...]:
        """Every ``attrs`` key this bundle will read off a combatant.

        Used at fight construction to reject a bundle whose roles name a key no
        combatant carries — surfacing the mismatch with both names, rather than as
        a ``KeyError`` several activations deep inside a formula. ``vitality`` is NOT
        here: it is a Fighter SLOT the engine addresses directly, always present, never
        an ``attrs`` key to validate.
        """
        keys = list(self.hit_roles.values())
        keys.extend(self.damage_roles.values())
        return tuple(dict.fromkeys(keys))


# The hit/damage formulas name this game's ``kraft``/``brutalitaet`` and so are POLICY,
# not mechanism: they live in ``data/game_configs/mafia_1920s/combat_rules.py`` as the
# rules bundle's ``hit_fn``/``damage_fn``, reading the attacker's attributes by role. The
# engine calls them through the bundle and never spells a stat name.


# --------------------------------------------------------------------------- #
# CombatView — the read-only argument every non-human driver receives          #
# --------------------------------------------------------------------------- #
class CombatView:
    """A read-only window onto a :class:`CombatFight`, handed to a decision driver.

    Choosing an action is split from applying it (:meth:`CombatFight.ai_decide` never
    executes), so handing a driver the mutable fight is both unnecessary and an
    invitation: a driver could call ``shoot`` directly and bypass the single
    apply-block the loop routes every action through, silently breaking recording.
    A driver receives THIS instead — it exposes exactly what a decision
    reads (``sides``, ``grid``, ``active_side``, ``active_fighter``, ``active``,
    ``hostile_to``, and the per-combatant equipment lookup) and **no mutators**:
    ``shoot`` / ``try_move`` / ``advance_activation`` are absent from its surface,
    so a driver handed a view is structurally unable to move the fight.

    It is a thin, non-copying wrapper: every read delegates to the live fight, so a
    view built once and consulted twice reflects the fight as it stands. Because it
    holds no mutators, that is safe — nothing a driver can call through the view
    changes the fight.

    :func:`engine.combat_ai.ai_target` reads only this surface (``active``/``hostile_to``/
    ``active_side``/``sides``), so it accepts a view unchanged. ``rng`` and
    ``is_melee``/``equipment_range`` are exposed too, because the CPU decision
    (:meth:`CombatFight.ai_decide`) consults them to pick move-vs-shoot — reads,
    never writes.
    """

    __slots__ = ("_fight",)

    def __init__(self, fight: "CombatFight") -> None:
        self._fight = fight

    @property
    def sides(self) -> tuple[tuple[Fighter, ...], tuple[Fighter, ...]]:
        return self._fight.sides

    @property
    def grid(self) -> tuple[int, ...]:
        return self._fight.grid

    @property
    def active_side(self) -> int:
        return self._fight.active_side

    @property
    def active_fighter(self) -> int:
        return self._fight.active_fighter

    @property
    def active(self) -> Fighter:
        return self._fight.active

    @property
    def rng(self) -> Any:
        return self._fight._rng

    def hostile_to(self, side: int) -> tuple[int, ...]:
        return self._fight.hostile_to(side)

    def equipment_stats(self, combatant: Fighter) -> Mapping[str, int]:
        return self._fight.equipment_stats(combatant)

    def equipment_range(self, combatant: Fighter) -> int:
        return self._fight.equipment_range(combatant)

    def is_melee(self, combatant: Fighter) -> bool:
        return self._fight.is_melee(combatant)


# --------------------------------------------------------------------------- #
# CombatFight — the activation loop's working state                           #
# --------------------------------------------------------------------------- #
class CombatFight:
    """Mutable per-fight working state: the blow-by-blow the driver loop advances.

    Combat state is split in two. The **persistent** graph (``GameState.combat``,
    a frozen :class:`~engine.state.CombatState`) holds the setup snapshot and the
    fight's committed consequences. The **mid-fight** evolution — positions moving
    cell by cell, energies ticking down, the activation cursor walking the sides —
    is working memory and lives HERE, in a plain mutable object, never in the frozen
    graph. Only the fight's persistent consequences (roster energy/down deltas, money,
    score, result) ride effects into the shared ``Ctx``, which is what keeps
    ``run_pure``'s replay check honest: replaying the effect stream reproduces the
    post-fight graph without needing to replay every intermediate step.

    This class is deliberately NOT a generator. The driver
    (:func:`engine.fight_loop._run_combat`) owns the yield/send protocol; this
    object owns the *rules*. Keeping them apart is what lets the AI drive the same
    rules with no client in the loop, and what keeps the future async transport a
    pure swap of the driver half.

    **The fight holds no equipment table.** Every combatant arrives
    carrying its own constructed ``equipment`` mapping, built by the game from its
    entity data before the fight starts. There is therefore no handle to resolve, no
    lookup to miss, and — crucially — no second source that can disagree with the
    roster about what a weapon does.
    """

    def __init__(
        self,
        combat: CombatState,
        *,
        rng: Any = None,
        rules: Any = None,
    ) -> None:
        # Fighters are frozen dataclasses; the working copy is a list-of-lists so a
        # step/damage rebuilds one Fighter in place without touching the frozen graph.
        self._sides: list[list[Fighter]] = [list(side) for side in combat.sides]
        self._grid: tuple[int, ...] = tuple(combat.grid)
        self._rng = rng
        self.active_side: int = combat.active_side or 1  # s (1 or 2)
        self.active_fighter: int = combat.active_fighter or 1  # f (1-based)
        self._losses: list[int] = list(combat.losses) or [0, 0]
        self._result_flag: int = combat.result_flag
        self.finished: bool = False
        self.dir_memory: dict = dict(combat.dir_memory)
        self._last_shooter: Fighter | None = None
        # No engine-side default that NAMES an attribute: a bundle-less fight is a
        # fight with no formulas, which is a caller error the moment a shot is fired.
        # (The reference title's bundle lives in its own config, never here.)
        self._rules: RulesBundle = rules if rules is not None else RulesBundle()
        self._check_roles()

    def _check_roles(self) -> None:
        """Reject a bundle naming an attribute key no combatant carries.

        Fails HERE, at construction, naming both the role and the missing key —
        rather than as a bare ``KeyError`` several activations deep inside a
        formula, where the message would name neither the fight nor the bundle.
        """
        # A bundle-less fight declares no formulas, so it reads no attributes and
        # has nothing to validate. It can still be moved through and surrendered —
        # which is exactly what the driver's cancel/EOF tests do — and only a SHOT
        # would fail, at the point the missing formula is actually needed.
        if self._rules.hit_fn is None and self._rules.damage_fn is None:
            return
        required = self._rules.required_keys()
        if not required:
            return
        for side_idx, side in enumerate(self._sides, start=1):
            for f_idx, fighter in enumerate(side):
                for key in required:
                    if key not in fighter.attrs:
                        role = self._role_for(key)
                        raise ValueError(
                            f"combat rules declare role {role!r} -> attribute {key!r}, "
                            f"but fighter {f_idx} on side {side_idx} ({fighter.identity!r}) "
                            f"carries no such attribute (has: "
                            f"{sorted(fighter.attrs)})"
                        )

    def _role_for(self, key: str) -> str:
        """The role name a required attribute key was declared under (for errors)."""
        for capability, roles in (
            ("hit", self._rules.hit_roles),
            ("damage", self._rules.damage_roles),
        ):
            for role, attr in roles.items():
                if attr == key:
                    return f"{capability}.{role}"
        return key

    def _attr(self, fighter: Fighter, roles: Mapping[str, str], role: str) -> int:
        """Resolve one declared role to the value it names on ``fighter``.

        The engine never spells an attribute name itself: it looks up which key the
        config's bundle assigned to this role and reads that key out of the opaque
        ``attrs`` map. :meth:`_check_roles` has already guaranteed the key is present.
        """
        return fighter.attrs[roles[role]]

    # -- read-only views ---------------------------------------------------- #
    @property
    def sides(self) -> tuple[tuple[Fighter, ...], tuple[Fighter, ...]]:
        """The two sides as frozen tuples (a snapshot, safe to hand to a screen)."""
        return (tuple(self._sides[0]), tuple(self._sides[1]))

    @property
    def grid(self) -> tuple[int, ...]:
        return self._grid

    @property
    def losses(self) -> tuple[int, int]:
        """Per-side downed counts — ``v(1)``/``v(2)`` (``mf-prg.bas:30310``)."""
        return (self._losses[0], self._losses[1])

    @property
    def last_shooter(self) -> Fighter | None:
        """The fighter who fired the last shot so far, or ``None`` (see :class:`CombatResult`)."""
        return self._last_shooter

    @property
    def result_flag(self) -> int:
        """The winning side once the fight ends, else 0 (the source's post-fight ``s``)."""
        return self._result_flag

    @property
    def active(self) -> Fighter:
        """The fighter whose activation is in progress."""
        return self._sides[self.active_side - 1][self.active_fighter - 1]

    def occupied(self, *, exclude: Fighter | None = None) -> frozenset[int]:
        """Cells held by a standing fighter (downed ones vacate — ``30310`` pokes 32)."""
        return frozenset(
            f.position for side in self._sides for f in side if not f.down and f is not exclude
        )

    def equipment_stats(self, combatant: Fighter) -> Mapping[str, int]:
        """``combatant``'s own equipment stats, for the game's formulas.

        A read off the roster, not a lookup through a table the fight holds: a
        second table would be a second source for one fact, able to disagree with
        the roster about what a weapon does (damage from one, reach from the other)
        without anything raising. Reading the combatant's own equipment removes the
        second source rather than guarding against the disagreement.
        """
        return combatant.equipment

    def equipment_range(self, combatant: Fighter) -> int:
        """``combatant``'s shot travel range in cells (``mf-prg.bas:30215-30216``).

        Range is entity data the game put on the equipment; the engine holds no
        weapon taxonomy of its own. A combatant whose equipment omits ``range`` gets
        :data:`DEFAULT_RANGE` — the source's own base ``r=2``.
        """
        return combatant.equipment.get("range", DEFAULT_RANGE)

    def is_melee(self, combatant: Fighter) -> bool:
        """True iff ``combatant``'s equipment reaches no further than the next cell.

        The derived replacement for the source's hardcoded ``orgw(...)<4``
        (``mf-prg.bas:30415``) — see :data:`RANGE_MELEE`.
        """
        return self.equipment_range(combatant) <= RANGE_MELEE

    # -- activation cursor (mf-prg.bas:30105-30109) ------------------------- #
    def hostile_to(self, side: int) -> tuple[int, ...]:
        """The sides hostile to ``side`` — the candidate pool for targeting and victory.

        Returns a TUPLE so the shape is N-party from the start, even though the
        reference title has exactly two fixed sides: ``hostile_to(1) == (2,)`` and
        ``hostile_to(2) == (1,)``. Deriving hostility from the acting side (rather than
        hardcoding "the CPU hunts side 1") is what makes a side-1 CPU fighter hunt side
        2 and never its own teammates — a side is never hostile to itself, so
        self-exclusion falls out of the derivation with no identity check.

        **Why two fixed sides / why the CPU "always hunts side 1" in the original.**
        In the C64 game hostility is not a lookup at all: the ``cr`` machine-code
        routine (``$C000``, disassembled in
        ``research/research-data/verification/ml-core-disassembly.yaml``) scans the grid
        for ``char $C1 (=193)`` cells whose **colour-RAM low nibble is 2** — a hardcoded
        constant in the ML. ``mf-prg.bas:30010`` (``pokefr+kp(i,j),2-4*(i=2)``) paints
        side 1 with colour 2 (the relational is false for ``i=1``, so the expression is
        2 under EITHER sign convention — this site is not affected by the relational-sign
        landmine) and side 2 with a different colour. So the ORIGINAL's ``cr`` always
        hunts side 1 because side 1 is the only side ever coloured 2 AND the only side
        ever put under a human at ``5010`` (``ks(1)=sp:ks(2)=0``). "Hostile to side 2"
        thus reduces to "(1,)" — which is exactly what this returns, N-party-shaped, so a
        two-party fight is bit-identical while a future graph can override it.

        Also ports the source's ``1-(s=1)`` side toggle (``mf-prg.bas:30106``, ``30108``,
        ``30250``): under C64 ``true = -1`` that is ``s=1 -> 2`` / ``s=2 -> 1`` (one of
        the structural proofs the relational is -1, not +1 — +1 gives ``1-1=0``, a side
        that does not exist; see
        docs/solutions/architecture-patterns/basic-relational-boolean-is-minus-one-when-porting.md).
        """
        return (2,) if side == 1 else (1,)

    def advance_activation(self) -> None:
        """Move the cursor to the next standing fighter, wrapping side 1 -> 2 -> 1.

        Ports ``mf-prg.bas:30105-30109``: ``f=f+1``; if ``f`` has run past this side's
        last fighter (``30107``) hand over to the other side with ``f=1`` (``30108``);
        skip any fighter already down (``30109``: ``ifkp(s,f)<0goto30105``).

        Guards against an infinite walk when neither side has a standing fighter (only
        reachable from a degenerate empty-roster setup, never from real play, since the
        victory check fires first) by bounding the scan at the total fighter count.
        """
        total = len(self._sides[0]) + len(self._sides[1]) + 2
        for _ in range(total):
            self.active_fighter += 1
            if self.active_fighter > len(self._sides[self.active_side - 1]):
                self.active_side = self.hostile_to(self.active_side)[0]
                self.active_fighter = 1
            side = self._sides[self.active_side - 1]
            if side and not side[self.active_fighter - 1].down:
                return

    def winner(self) -> int | None:
        """The winning side, or ``None`` while both sides still have a standing fighter.

        Ports the per-activation victory check ``mf-prg.bas:30106``
        (``fori=1togz(ks(1-(s=1))):ifkp(1-(s=1),i)<0thennexti:goto30500``): scan the
        OPPOSING side; if every one of its fighters is marked down (``kp<0``, set at
        ``30310``) the fight is over and the *current* side is the winner (``30500``
        names ``bn$(ks(s))``, with ``s`` unchanged by the check).

        Once :meth:`surrender` has ended the fight, the recorded ``result_flag`` is
        returned instead — surrender declares a winner regardless of who is standing.
        """
        if self.finished:
            return self._result_flag or None
        for side in (1, 2):
            other = self.hostile_to(side)[0]
            fighters = self._sides[other - 1]
            if all(f.down for f in fighters):
                return side
        return None

    # -- actions: one per activation (mf-prg.bas:30130-30155) --------------- #
    def _replace_fighter(self, side: int, index0: int, **changes) -> Fighter:
        """Rebuild one frozen :class:`Fighter` in the working roster and return it."""
        current = self._sides[side - 1][index0]
        updated = replace(current, **changes)
        self._sides[side - 1][index0] = updated
        return updated

    def try_move(self, step: int) -> bool:
        """Attempt a one-cell step; return whether it was legal and committed.

        Ports ``mf-prg.bas:30140-30150``: compute the target ``p = kp(s,f) + x``,
        reject it at ``30145`` when the cell is not empty (only codes 32/96 are
        walkable) or falls outside the 0..520 bound, else commit ``kp(s,f)=kp(s,f)+x``
        at ``30150``. A rejected step does NOT consume the activation — the source
        jumps back to the key-read at ``30125``, so the driver re-prompts.

        The caller supplies ``step`` as one of :data:`STEPS`; the row-width deltas
        mean a left/right step can cross a row boundary exactly as the source's
        linear screen addressing does (the original has no per-row clamp either).
        """
        return self._commit_step(step)

    def _commit_step(self, step: int) -> bool:
        """Validate one linear step and commit it if legal; shared by :meth:`try_move`
        and :meth:`apply_action`'s move branch, which layer their own pre/post rules
        (direction memory, for an AI move) around this validate-then-``_replace_fighter``
        core.
        """
        if step not in STEPS:
            return False
        fighter = self.active
        target = fighter.position + step
        if not can_move_onto(target, self._grid, self.occupied(exclude=fighter)):
            return False
        self._replace_fighter(self.active_side, self.active_fighter - 1, position=target)
        return True

    def shoot(self, direction: int) -> dict:
        """Fire in ``direction``; resolve travel, hit check, and damage in one call.

        The whole ``mf-prg.bas:30200-30310`` attack block:

        - ``30215-30216`` — the weapon's travel range, CONFIG data
          (:meth:`equipment_range`).
        - ``30220-30225`` — step the projectile one cell per range point; it stops on
          leaving the grid, on a wall (:func:`blocks_shot` — codes 160/156 only), or
          when the range is exhausted. Scenery and friendly fighters are OVERFLOWN:
          the source's step test checks walls only, and its hit test ``30226``
          additionally requires the OPPOSING side's colour.
        - ``30247`` — the hit test: the rules bundle's ``hit_fn`` on the ATTACKER's
          ``hit_roles`` attribute and its equipment.
        - ``30255`` — the damage roll: the bundle's ``damage_fn`` on the ATTACKER's
          ``damage_roles`` attribute and its equipment.
        - ``:30260``/``:30275`` — subtract from the target's ``vitality``, clamped at 0.
        - ``:30300-30310`` — at 0 energy the target is marked down and the side's
          loss counter increments.

        Returns a small result dict — ``hit``, ``damage``, ``target_side``,
        ``target_index`` (0-based, ``None`` on a miss), and ``downed`` — so the driver
        can narrate the shot without re-deriving what happened.
        """
        miss = {
            "hit": False,
            "damage": 0,
            "target_side": None,
            "target_index": None,
            "downed": False,
        }
        if direction not in STEPS:
            return miss

        attacker = self.active
        # :30215 ``w=gw(ks(s),f)`` — every fire order, hit or miss, leaves its
        # shooter's weapon behind.
        self._last_shooter = attacker
        enemy_side = self.hostile_to(self.active_side)[0]
        equipment = self.equipment_stats(attacker)

        # -- projectile travel (30220-30226) -------------------------------- #
        p = attacker.position
        target_index: int | None = None
        for _ in range(self.equipment_range(attacker)):
            p += direction
            if blocks_shot(p, self._grid):
                return miss
            for i, f in enumerate(self._sides[enemy_side - 1]):
                if not f.down and f.position == p:
                    target_index = i
                    break
            if target_index is not None:
                break
        if target_index is None:
            return miss

        # -- hit check (30245-30247): the GAME's formula, on the GAME's roles - #
        hit_input = self._attr(attacker, self._rules.hit_roles, "attacker")
        if not self._rules.hit_fn(hit_input, equipment, self._rng):
            return miss

        # -- damage (30255) and application (30260/30275, clamp at 0) ------- #
        damage_input = self._attr(attacker, self._rules.damage_roles, "attacker")
        damage = self._rules.damage_fn(damage_input, equipment, self._rng)
        target = self._sides[enemy_side - 1][target_index]
        # The ONE engine invariant on the depleting resource: subtract and clamp at
        # zero. The engine addresses it as the ``vitality`` SLOT — it
        # does not know what the resource means, only that reaching zero terminates a
        # combatant.
        vitality = max(0, target.vitality - damage)
        downed = vitality == 0
        self._replace_fighter(enemy_side, target_index, vitality=vitality, down=downed)
        if downed:
            # v(x)=v(x)+1 (mf-prg.bas:30310) — the STRUCK side takes the loss.
            self._losses[enemy_side - 1] += 1
        return {
            "hit": True,
            "damage": damage,
            "target_side": enemy_side,
            "target_index": target_index,
            "downed": downed,
        }

    # -- the CPU decision layer (mf-prg.bas:30400-30492) -------------------- #
    def view(self) -> "CombatView":
        """A read-only :class:`CombatView` onto this fight, for a decision driver.

        The seam between *choosing* an action and *applying* it: a driver
        (:meth:`ai_decide`, a policy callable) receives one of these — never the
        mutable fight — so it can read the board but cannot bypass the loop's single
        apply-block (which would break recording).
        """
        return CombatView(self)

    def ai_decide(self, view: "CombatView") -> tuple[str, Any]:
        """Choose one CPU action WITHOUT executing it — the pure half of the AI.

        This is :meth:`ai_take_turn` with its one mutation removed: it returns the
        action the CPU would take as an ``(action, argument)`` pair in the engine's
        own vocabulary — ``("shoot", direction)``, ``("move", step)``, or
        ``("pass", None)`` — and touches nothing. The loop's single apply-block (or
        the :meth:`ai_take_turn` wrapper) then executes it, so a shot is fired exactly
        ONCE, at one site, whoever chose it. Wiring a chooser that *also* executed
        behind that apply-block would fire every shot twice — the concrete bug this
        split exists to prevent.

        Ports the decision logic of ``mf-prg.bas:30400-30492`` (see :meth:`ai_take_turn`
        for the line-by-line reading), except that the move branch returns the CHOSEN
        step instead of committing it, and the shoot branch returns the direction
        instead of calling :meth:`shoot`.

        ``view`` is this fight's :class:`CombatView`; it is accepted as an argument
        (rather than read off ``self``) so a driver's decision is expressed purely in
        terms of the read-only surface — the same surface a second game's AI plugs into.

        **Purity.** Called twice on the same board it returns the same choice and
        leaves the fight untouched: it writes no position, no vitality, no ``down``
        flag, and — unlike the executing move path — no direction memory. The
        ``30415`` coin flip DOES draw from the rng when the target is not near-adjacent
        and the fighter is ranged, so "pure" means "does not mutate the fight", not
        "does not advance a stateful rng"; a caller wanting a repeatable probe seeds a
        fresh rng or uses a zero-variance weapon.
        """
        from engine.combat_ai import ai_target

        target = ai_target(view)
        if target is None:
            # Unreachable from real play: 30106's victory check fires before 30110.
            return ("pass", None)

        # 30410: near-adjacent -> attack branch, WITHOUT drawing the 30415 roll.
        near_adjacent = target.abs_dx == 1 or target.abs_dy == 1
        if not near_adjacent:
            # 30415: 50% roll OR a melee weapon forces the close-distance branch.
            melee = view.is_melee(view.active)
            forced_move = view.rng.range(2) == 0 if view.rng is not None else False
            if forced_move or melee:
                return self._ai_choose_step(view, target)

        # :30420-30425: fire only along a shared column or row.
        if target.x == 0:
            direction = target.y  # 30420: x=y, fire vertically
        elif target.y != 0:
            return self._ai_choose_step(view, target)  # 30421: diagonal -> close distance
        else:
            direction = target.x  # 30425: row-aligned, fire horizontally

        if direction == 0:
            # Degenerate: attacker and target share a cell (impossible in play).
            return ("pass", None)
        return ("shoot", direction)

    def _ai_choose_step(self, view: "CombatView", target: AiTarget) -> tuple[str, Any]:
        """The move ROUTINE ``mf-prg.bas:30450-30465`` as a pure chooser.

        Returns the step it WOULD commit (``("move", step)``) instead of committing it,
        or ``("pass", None)`` when the fighter is boxed in (``30465``'s bare return). No
        position write, no direction-memory write — the loop's apply path does both, via
        :meth:`apply_action` with ``record_dir_memory=True`` (which reproduces the source's
        ``30491``/``30492`` commit-and-record on the successful step).

        The four attempts run in the source's exact order, each gated on direction
        memory: horizontal approach (``30450``), vertical approach (``30451``), then the
        perpendicular sidesteps (``30455``-``30462``) that unstick a fighter walking into
        a wall — ``30455`` sends it perpendicular to the approach axis it just failed.
        """
        # 30450 then 30451: the two approach steps, horizontal first.
        for step in (target.x, target.y):
            if step != 0 and self._ai_step_available(view, step):
                return ("move", step)

        # 30455: perpendicular-sidestep gate (see _dir_memory_allows for the ri(f) rule).
        horizontal_available = target.x != 0 and self._dir_memory_allows(target.x)
        if horizontal_available:
            sidesteps: tuple[int, ...] = (STEP_DOWN, STEP_UP)  # 30461, 30462
            if target.y != 0 and self._dir_memory_allows(target.y):
                sidesteps = ()  # 30460: both approach axes tried -> exit
        else:
            sidesteps = (STEP_RIGHT, STEP_LEFT)  # 30456, 30457

        for step in sidesteps:
            if self._ai_step_available(view, step):
                return ("move", step)

        # 30465: boxed in — the activation is spent with no step taken.
        return ("pass", None)

    def _ai_step_available(self, view: "CombatView", step: int) -> bool:
        """Would an AI step commit ``step``? — the ``30490`` gates, but no mutation.

        Direction-memory guard (``30450``-``30462`` + ``30490``) and the walkability
        test (``30490``, :func:`can_move_onto` including fighter occupancy), with NO
        commit and NO ``ri(f)`` write. The pure predicate behind :meth:`ai_decide`.
        """
        if step not in STEPS:
            return False
        if not self._dir_memory_allows(step):
            return False
        fighter = self.active
        target = fighter.position + step
        return can_move_onto(target, self._grid, self.occupied(exclude=fighter))

    def apply_action(self, action: str, argument: Any, *, record_dir_memory: bool = False) -> Any:
        """Execute ONE chosen action against the fight — the single mutation site.

        Whoever chose the action — a human via
        :func:`engine.fight_loop._parse_combat_response`, the AI
        via :meth:`ai_decide`, a policy driver — the loop applies it HERE, so a shot
        mutates exactly once. Returns a small, action-specific value the caller
        narrates/advances on:

        - ``"shoot"`` -> the :meth:`shoot` result dict (``hit``/``damage``/``downed``…).
        - ``"move"``  -> ``True`` iff the step was legal and committed (``False`` re-prompts
          a HUMAN; an AI's step is pre-validated so this is always ``True`` for it).
        - ``"pass"``  -> ``None`` (the activation is spent with no board change).
        - ``"surrender"`` -> the winning (opposing) side (:meth:`surrender`).
        - anything else -> ``"__unknown__"`` so a human re-prompts (``30139``).

        ``record_dir_memory`` (set for AI/policy moves) performs the ``30492``
        ``ri(f)=p`` write on a committed step, so an AI move driven through THIS
        apply-block behaves identically to :meth:`ai_take_turn`'s. A HUMAN
        move never records direction memory (it is keyed by fighter index and read only
        by the AI, so a human write on side 1 would corrupt the AI's side-2
        memory).
        """
        if action == "surrender":
            return self.surrender()
        if action == "pass":
            return None
        if action == "shoot":
            return self.shoot(argument)
        if action == "move":
            committed = self.try_move(argument)
            if committed and record_dir_memory:
                self.dir_memory[self._dir_memory_key()] = argument  # 30492: ri(f)=p
            return committed
        return "__unknown__"

    def ai_take_turn(self) -> dict:
        """Run one CPU activation: pick a target, then attack or move.

        The whole ``mf-prg.bas:30400-30492`` block, in the source's own order:

        - ``30405`` — ``syscr`` locates the nearest side-1 fighter and decodes the
          four ``ua`` bytes into step deltas ``x``/``y`` plus ``abs(dx)``/``abs(dy)``
          (:func:`engine.combat_ai.ai_target`).
        - ``30410`` — ``ifpeek(ua+2)=1orpeek(ua+3)=1goto30420``: if the target is
          **near-adjacent** on either axis, jump STRAIGHT to the attack branch,
          skipping the coin flip entirely. Note this is ``= 1``, not ``<= 1`` — a
          target on the very same cell is impossible, so the distinction never bites.
        - ``30415`` — otherwise ``ifint(rnd(1)*2)=0orgw(ks(s),f)<4goto30450``: a 50%
          roll, OR a melee weapon (ids 0..3), forces the move branch. A ranged fighter
          that wins the flip falls through and tries to shoot.
        - ``30420`` — ``ifx=0thenx=y:goto30215``: column-aligned, so fire vertically.
        - ``30421`` — ``ify<>0goto30450``: reached only with ``x<>0``; if ``y`` is also
          non-zero the target is DIAGONAL, and the original has no diagonal shot, so it
          moves instead.
        - ``30425`` — ``goto30215``: reached with ``x<>0`` and ``y=0`` — row-aligned,
          fire horizontally.

        So the AI fires **only** along a shared row or column, and the 50% roll is
        consulted **only** when the target is not near-adjacent — two easy things to get
        subtly wrong when reading the block out of order.

        Returns a small result dict for the driver to narrate: ``action`` (``"shoot"``,
        ``"move"``, or ``"none"``), ``direction`` (the step/fire delta, ``None`` when
        idle), ``result`` (the :meth:`shoot` outcome, only for ``"shoot"``), and
        ``target`` (the chosen :class:`~engine.combat_ai.AiTarget`, ``None`` when the
        hostile side is already wiped).

        The activation is **always** consumed, including when the fighter is boxed in
        and takes no step (``30465``'s bare ``return``, then ``30110``'s
        ``gosub30400:goto30105``). Advancing the cursor is the driver's job, not this
        method's — mirroring how :meth:`try_move` and :meth:`shoot` leave it alone.

        **A thin wrapper.** This is exactly :meth:`ai_decide` (the pure choice)
        followed by :meth:`apply_action` (the one mutation), so the decision logic
        lives in ONE place and every driver — this wrapper, the loop's dispatcher —
        executes through the SAME apply-block, firing a shot once.
        """
        from engine.combat_ai import ai_target

        # ai_target is recomputed here only to fill the returned dict's ``target``
        # field; ai_decide computes its own.
        target = ai_target(self)
        action, argument = self.ai_decide(self.view())
        if action == "shoot":
            return {
                "action": "shoot",
                "direction": argument,
                "result": self.apply_action("shoot", argument),
                "target": target,
            }
        if action == "move":
            # An AI move records direction memory (30492) on a committed step.
            self.apply_action("move", argument, record_dir_memory=True)
            return {"action": "move", "direction": argument, "result": None, "target": target}
        # ai_decide's ("pass", None) maps to the "none" outcome — no target, a
        # degenerate direction, or a boxed-in fighter (30465). No board change.
        return {"action": "none", "direction": None, "result": None, "target": target}

    def _dir_memory_allows(self, step: int) -> bool:
        """Is ``step`` permitted by this fighter's direction memory ``ri(f)``?

        The rule the four literal guards at ``30456``/``30457``/``30461``/``30462`` pin:
        ``ri(f) <> -p``. A fighter with no recorded step yet carries the ``30020`` seed
        ``-1``, which forbids a rightward step on its very first activation.

        **Direction memory** (``ri(f)``, seeded to -1 at ``30020``, written at ``30492``
        — reproduced by :meth:`apply_action`'s ``record_dir_memory`` on a committed AI
        move) forbids exactly one step per attempt: the exact reverse of the last
        committed step. The approach gates spell it as ``ri(f)<>(1+2*(x=1))`` (``30450``)
        and ``ri(f)<>(40+80*(y=1))`` (``30451``); both reduce to ``ri(f) <> -p``. The
        four sidestep lines state the SAME rule with literal constants — ``30456``
        guards ``p=1`` with ``ri(f)<>-1``, ``30457`` guards ``p=-1`` with ``ri(f)<>1``,
        ``30461`` guards ``p=40`` with ``ri(f)<>-40``, ``30462`` guards ``p=-40`` with
        ``ri(f)<>40``. Those literals are the proof: the rule is "never step the exact
        reverse of your last step", encoded once here rather than re-derived per site.

        (Relational-sign note, per docs/solutions/architecture-patterns/
        basic-relational-boolean-is-minus-one-when-porting.md: the four literal sidestep
        guards pin the rule independently of any sign convention, and the C64
        ``true = -1`` evaluation agrees with them — it makes ``1+2*(x=1)`` equal ``-x``,
        the exact reverse of the last step. A ``true=+1`` reading would yield the
        nonsensical ``3`` for a rightward step — one of the structural proofs (#47) that
        C64 true is -1.)

        The seed value ``ri=-1`` (``30020``) is not neutral: it is a real leftward step,
        so a freshly-spawned enemy will not open the fight by stepping right. That is the
        original's behaviour, faithfully kept.
        """
        return self.dir_memory.get(self._dir_memory_key(), -1) != -step

    def _dir_memory_key(self) -> int:
        """The active fighter's key in :attr:`dir_memory`.

        The fighter's 0-based number, as ``ri(f)`` indexes it (side 2 -- the only CPU
        side the source has -- is seeded under these keys, ``mf-prg.bas:30020``). With
        the bundle's ``direction_memory_per_side`` a side-1 fighter keeps its own
        memory under a negative key (``-1 - index``), unseeded, so it reads the same
        ``-1`` a seeded fighter starts with.
        """
        index = self.active_fighter - 1
        if self._rules.direction_memory_per_side and self.active_side == 1:
            return -1 - index
        return index

    def surrender(self) -> int:
        """The active side gives up; return the winning (opposing) side.

        Ports ``mf-prg.bas:30136`` (``ifx$="q"thensysie:s=1-(s=1):goto30500``): the
        side toggle runs BEFORE the victory screen, so ``30500``'s ``bn$(ks(s))``
        names the side that did NOT surrender. Ends the fight immediately, whatever
        the board looks like.
        """
        self._result_flag = self.hostile_to(self.active_side)[0]
        self.finished = True
        return self._result_flag

    def finish(self, winner: int) -> int:
        """Record ``winner`` as the fight's result and mark the fight over."""
        self._result_flag = winner
        self.finished = True
        return winner

    def snapshot(self) -> CombatState:
        """Freeze the working state back into a :class:`~engine.state.CombatState`.

        The bridge from working memory to the serializable graph shape: what the
        combat screen renders from, and what a fight-result effect would carry.
        """
        return CombatState(
            sides=self.sides,
            grid=self._grid,
            dir_memory=dict(self.dir_memory),
            active_side=self.active_side,
            active_fighter=self.active_fighter,
            losses=self.losses,
            result_flag=self._result_flag,
        )

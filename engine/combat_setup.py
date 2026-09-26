"""Combat fight setup: side placement, fighter-side construction, and the initial CombatState."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from engine.state import CombatState, Fighter

__all__ = [
    "SIDE1_ANCHOR",
    "SIDE2_ANCHOR",
    "STAGGER_OFFSETS",
    "placement_position",
    "placement_positions",
    "build_player_side",
    "build_enemy_side",
    "setup_combat",
]

# --------------------------------------------------------------------------- #
# Placement                                                                   #
# --------------------------------------------------------------------------- #
#: The ten combat spawn-position stagger offsets, DATA 50400 (mf-prg.bas:124,
#: `fori=1to10:readp(i):next` — read into p(1..10), used at 30000).
STAGGER_OFFSETS: tuple[int, ...] = (122, 81, 161, 120, 42, 202, 40, 200, 1, 241)

#: Side anchors from ``mf-prg.bas:30000``: ``kp(i,j) = 129-18*(i=2) + p(j)``.
#: Side 1 (i=1): ``(i=2)`` is false (0) -> anchor 129. Side 2 (i=2): ``(i=2)`` is
#: true, which in C64 BASIC is -1 -> anchor = 129 - 18*(-1) = 147.
#:
#: #47: C64 true is -1, not +1 (true=+1 would give 111). The sibling expressions in
#: this same block decide the sign structurally, and all three fail under true=+1:
#:   :30010 ``pokefr+kp(i,j),2-4*(i=2)`` — a C64 colour code (0..15). true=-1
#:          gives 6 (blue) for side 2 vs 2 (red) for side 1; true=+1 gives -2.
#:   :30015 ``poke211,-20*(i=2)`` — 211/$D3 is the KERNAL cursor COLUMN and
#:          cannot be negative. true=-1 puts side 2's label at column 20 (the
#:          right half of the 40-column screen); true=+1 gives -20.
#:   :30108 ``s=1-(s=1)`` — the side toggle, which must map 1<->2. true=-1 gives
#:          1->2 and 2->1; true=+1 gives 1->0, a nonexistent side.
SIDE1_ANCHOR = 129
SIDE2_ANCHOR = 129 + 18  # 147 — (i=2) is true = -1, so 129 - 18*(-1)


def placement_position(anchor: int, slot: int) -> int:
    """Return the linear cell for the ``slot``-th fighter (1-based) placed at ``anchor``.

    Ports ``kp(i,j) = anchor + p(j)`` (mf-prg.bas:30000) for one fighter. ``slot`` is
    1-based (matching the source's ``forj=1togz(...)``) and indexes
    :data:`STAGGER_OFFSETS` at ``slot - 1``.

    Raises ``ValueError`` if ``slot`` is out of the supported 1..10 range (the source
    only ever reads 10 stagger offsets — a roster/spec larger than 10 fighters per
    side is a config bug the caller should catch, not silently wrap).
    """
    if slot < 1 or slot > len(STAGGER_OFFSETS):
        raise ValueError(
            f"placement slot {slot} out of range (1..{len(STAGGER_OFFSETS)} supported)"
        )
    return anchor + STAGGER_OFFSETS[slot - 1]


def placement_positions(anchor: int, count: int) -> tuple[int, ...]:
    """Return the first ``count`` placement cells for one side, in slot order."""
    return tuple(placement_position(anchor, slot) for slot in range(1, count + 1))


# --------------------------------------------------------------------------- #
# Fighter-side construction                                                   #
# --------------------------------------------------------------------------- #
def build_player_side(roster: Any) -> tuple[Fighter, ...]:
    """Build side 1 from the active player's roster (boss first).

    Each roster :class:`~engine.state.Combatant` becomes one
    :class:`~engine.state.Fighter` carrying its ``vitality`` slot and opaque ``attrs``
    WHOLESALE — the function names no game stat, it copies the two blueprint carriers
    the engine already reads through. ``roster[0]`` is always the boss
    (``Player`` docstring, ``mf-prg.bas:300``) — this function does not reorder it, it
    only maps roster order onto placement-slot order 1:1 (``mf-prg.bas:30000``'s
    ``forj=1togz(ks(i))`` walks the roster in its stored order).
    """
    positions = placement_positions(SIDE1_ANCHOR, len(roster))
    return tuple(
        Fighter(
            name=g.name,
            weapon=g.weapon,
            vitality=g.vitality,
            attrs=dict(g.attrs),
            position=pos,
            down=False,
            equipment=getattr(g, "equipment", None) or {},
            # The roster slot this fighter came from, so the outcome maps back to the
            # right gangster by identity rather than by position.
            roster_id=slot,
        )
        for slot, (g, pos) in enumerate(zip(roster, positions))
    )


def build_enemy_side(
    count: int,
    weapon: int,
    vitality: int,
    *,
    attrs: Mapping[str, int] | None = None,
    name: str = "",
) -> tuple[Fighter, ...]:
    """Build side 2 (the NPC/enemy party) from a ``StartCombat`` spec.

    Ports the combat-launch helper ``mf-prg.bas:5000``: every enemy fighter is armed
    with the SAME ``weapon`` and starts with the SAME ``vitality`` (``fori=1togz(0):
    gw(0,i)=w:ec(i)=e:next`` — one weapon/energy value broadcast across the whole
    enemy roster, not per-fighter). The non-vitality stats come from ``attrs`` supplied
    by the CALLER — the source's fixed ``bt=30:kr=30`` (``mf-prg.bas:30245``) is config
    data, so the engine names neither the stat nor its value. Placed at
    :data:`SIDE2_ANCHOR`.
    """
    positions = placement_positions(SIDE2_ANCHOR, count)
    enemy_attrs = dict(attrs or {})
    return tuple(
        Fighter(
            name=name,
            weapon=weapon,
            vitality=vitality,
            attrs=enemy_attrs,
            position=pos,
            down=False,
        )
        for pos in positions
    )


# --------------------------------------------------------------------------- #
# Fight setup                                                                 #
# --------------------------------------------------------------------------- #
def setup_combat(
    roster: Any,
    *,
    enemy_count: int,
    enemy_weapon: int,
    enemy_vitality: int,
    enemy_attrs: Mapping[str, int] | None = None,
    enemy_name: str = "",
    grid: tuple[int, ...] = (),
    equip: Any = None,
) -> CombatState:
    """Build the initial :class:`~engine.state.CombatState` for a new fight.

    Ports ``mf-prg.bas:30000-30020``: places both sides (:func:`build_player_side`,
    :func:`build_enemy_side`), initializes the enemy side's direction memory
    ``ri(i)=-1`` for each enemy fighter (``mf-prg.bas:30020`` — the source only
    tracks this for the CPU side), and starts the activation cursor at side 1,
    fighter 1 (``mf-prg.bas:30100``: ``s=1:f=0`` then the first ``f=f+1`` at
    activation start lands on fighter 1). Losses start at ``(0, 0)`` (``v(1)=0:v(2)=0``,
    :30100) and ``result_flag`` starts unset (0).

    The enemy party enters with ``enemy_vitality`` (its ``vitality`` slot) and the
    non-vitality stats in ``enemy_attrs`` — both CALLER-supplied config data. The
    engine names no enemy stat and holds no fixed enemy value.

    ``grid`` is the backdrop's linear 521-cell wall/scenery code array (config data,
    pre-decoded — see :mod:`engine.combat`'s docstring); an empty ``grid`` (fidelity-deviation
    fallback) still produces a legally-playable open arena, since
    :func:`engine.combat.can_move_onto`
    treats any cell past the end of a short ``grid`` as open ground (code 32).

    ``equip`` is the GAME's ``weapon id -> stat mapping`` constructor, called once per
    fighter here so every combatant enters the fight already carrying its equipment.
    It is a parameter rather than an engine table because resolving a
    weapon id is entity knowledge the engine does not have — and because building the
    equipment HERE, once, is what stops a second copy existing to disagree later.
    """
    side1 = build_player_side(roster)
    side2 = build_enemy_side(
        enemy_count, enemy_weapon, enemy_vitality, attrs=enemy_attrs, name=enemy_name
    )
    if equip is not None:
        side1 = tuple(replace(f, equipment=equip(f.weapon)) for f in side1)
        side2 = tuple(replace(f, equipment=equip(f.weapon)) for f in side2)
    dir_memory = {i: -1 for i in range(len(side2))}
    return CombatState(
        sides=(side1, side2),
        grid=tuple(grid),
        dir_memory=dir_memory,
        active_side=1,
        active_fighter=1,
        losses=(0, 0),
        result_flag=0,
    )

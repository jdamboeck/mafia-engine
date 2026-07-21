"""This game's combat formulas — the ``mafia_1920s`` rules bundle (U2).

The engine no longer knows *how* a shot hits or how hard it lands: it knows only
that a fight needs a hit test and a damage roll, and asks the bundle it was handed
at construction. Both formulas below are the ports that used to live in
``engine/combat.py``, moved here verbatim (BASIC citations intact) and rewritten to
read their inputs through a **role map** rather than named keyword arguments.

A role is this game's answer to an engine question. The engine asks "what does the
attacker's hit chance depend on?"; this config answers ``{"attacker": "kraft"}``,
and the formula reads ``attacker.attrs["kraft"]``. A different game in the genre
answers ``{"attacker": "aim"}`` and changes nothing in ``engine/``.

This is **config-owned game code** (docs/design/config-and-content-contract.md):
it lives with the config so a new game = copy this directory.
"""

from __future__ import annotations

from typing import Any

from engine.combat import RulesBundle

__all__ = [
    "HIT_ROLES",
    "DAMAGE_ROLES",
    "is_hit",
    "damage_roll",
    "build_rules",
]

# The depleting resource is the engine's ``vitality`` SLOT (amendment A5): the engine
# reads and writes it directly, so this game no longer tells the bundle which key holds
# it. This game's name for the role ("energie") lives only at the CONSTRUCTION boundary
# — ``Gangster(energie=…)`` and the handlers' ``enemy_vitality=`` fold it into the slot.

#: The hit test reads the ATTACKER's kraft (``mf-prg.bas:30246`` loads ``a=ks(s):b=f``
#: — the active side/fighter — before the roll at ``30247``).
HIT_ROLES = {"attacker": "kraft"}

#: The damage roll reads the ATTACKER's brutalitaet (loaded with kraft at ``30246``).
DAMAGE_ROLES = {"attacker": "brutalitaet"}


def is_hit(attacker: Any, equipment: Any, rng: Any) -> bool:
    """Roll the two miss factors; return True iff the shot connects.

    Ports ``mf-prg.bas:30247``: ``ifint(rnd(1)*ts(w))=0orint(rnd(1)*(kr/10+1))=0goto30235``
    — the shot MISSES if EITHER factor rolls 0, so it hits iff NEITHER does.
    ``rng.range(n)`` is exactly the source's ``int(rnd(1)*n)``.

    ``equipment["ts"]`` is the weapon's accuracy (config data,
    ``mf-prg.bas:50100-50115``) and ``attacker`` is the **attacker's** kraft, resolved
    by the engine through :data:`HIT_ROLES`: ``30246`` loads ``a=ks(s):b=f`` — the
    ACTIVE side and fighter, i.e. the attacker — before this roll, and the CPU
    branch ``30245`` substitutes the attacker's fixed ``kr=30``. (The research
    interpretation layer glosses this factor as "dodge by craft"; per KTD-9 the
    decompiled code wins, and the code unambiguously loads the attacker.)

    Both factors are drawn unconditionally, even though BASIC's ``or`` short-circuits
    past the second when the first is already 0. Drawing both keeps the RNG log
    shape stable per shot, which is what makes a seeded replay reproducible — a
    fidelity-neutral deviation (the outcome is identical either way, since a miss
    is a miss) that the behavioral bar explicitly permits.

    ``int(kr/10+1)`` is BASIC's truncation, so the second factor's bound is
    ``kraft // 10 + 1`` — never 0, so ``rng.range`` is always called legally.
    """
    ts = equipment["ts"]
    kraft = attacker
    weapon_factor = rng.range(ts) if ts > 0 else 0
    craft_factor = rng.range(kraft // 10 + 1)
    return weapon_factor != 0 and craft_factor != 0


def damage_roll(attacker: Any, equipment: Any, rng: Any) -> int:
    """Roll one hit's damage.

    Ports ``mf-prg.bas:30255``: ``y=int(rnd(1)*tg(w)+bt/10)+1``. Note where the
    ``int()`` sits — it wraps the WHOLE sum, not just the random term, so the
    fractional part of ``bt/10`` can still carry the sum past an integer boundary.
    ``equipment["tg"]`` is the weapon's damage rating (config data) and ``attacker``
    is the **attacker's** brutalitaet, resolved through :data:`DAMAGE_ROLES` (loaded
    with kraft at ``30246``; fixed 30 for the CPU at ``30245``).

    The trailing ``+1`` makes damage at least 1 on every hit — a connecting shot
    always costs the target energy.
    """
    tg = equipment["tg"]
    brutalitaet = attacker
    draw = rng.range(tg) if tg > 0 else 0
    return int(draw + brutalitaet / 10) + 1


def equipper(weapon_stats: Any) -> Any:
    """Build the ``weapon id -> equipment mapping`` constructor ``setup_combat`` calls.

    This runs ONCE PER FIGHTER at fight setup, not per shot: the result is attached to
    the combatant, and the fight then reads equipment off the roster it was handed
    (amendment A1). The table therefore stops existing the moment setup finishes,
    which is precisely why it cannot later disagree with the roster.

    Raises ``KeyError`` on an unknown weapon id rather than yielding a zeroed default —
    a weapon with ``ts=0`` misses every shot, so a bad id would otherwise present as an
    unwinnable fight instead of an error at its source. Raising HERE, at setup, also
    surfaces it before a single activation runs.
    """

    table = dict(weapon_stats or {})

    def equip(weapon: Any) -> dict:
        stats = table.get(weapon)
        if stats is None:
            raise KeyError(f"unknown weapon id {weapon!r} (known: {sorted(table)})")
        equipment = {"ts": stats[0], "tg": stats[1]}
        if len(stats) > 2:
            equipment["range"] = stats[2]
        return equipment

    return equip


def build_rules() -> RulesBundle:
    """Build this game's :class:`~engine.combat.RulesBundle`.

    Takes no equipment lookup: the formulas read stats off the attacker's own
    ``equipment`` (amendment A1), so the bundle carries only formulas and roles.
    """
    return RulesBundle(
        hit_roles=HIT_ROLES,
        hit_fn=is_hit,
        damage_roles=DAMAGE_ROLES,
        damage_fn=damage_roll,
    )

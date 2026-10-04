"""``Scenario`` — one value that fully describes a fight.

``Scenario`` is the "payload in" half of a fight as a **named, inspectable,
freely-constructible value** — something a test or tool can hold, name, and build
without a ``GameState``.

Two construction paths converge on one shape:

* **Explicit** — build the fighter tuples directly, no ``GameState`` / roster / YAML.
  A scenario can carry **invented entities**: a fighter with a weapon id the
  real game never defined, carrying its own ``equipment`` stat mapping. Because a
  fighter carries its **constructed equipment** (the engine holds no
  weapon table), inventing one needs no new machinery and no parallel stats entry to
  keep in sync. There is no ``(0, 0)`` fallback and no ``KeyError`` to fall into: the
  stats are simply on the fighter.
* **Procedural** — :meth:`Scenario.from_roster` is a **thin wrapper over**
  :func:`engine.combat_setup.setup_combat`. It does not re-implement side placement,
  equipment construction, or direction-memory init — it calls ``setup_combat`` and
  copies the three fields onto the scenario. This is the form the three in-game combat
  handlers share.

**Payload OUT is :class:`~engine.combat.CombatResult`, not this.** ``Scenario`` is
only the payload *in*; a handler passes it whole as ``StartCombat(scenario=...)``, and
:func:`engine.fight_loop.simulate` takes it directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from engine.combat import RulesBundle
from engine.combat_setup import setup_combat
from engine.state import Fighter

__all__ = ["Scenario"]


@dataclass(frozen=True)
class Scenario:
    """A complete description of one fight, constructible without a ``GameState``.

    Fields mirror exactly what :class:`~engine.interactions.StartCombat` and
    :class:`~engine.combat.CombatFight` consume — a scenario IS the fight's input:

    ``sides``
        The two sides as tuples of fully-constructed :class:`~engine.state.Fighter`
        (positions set, equipment on each). ``None`` only for a
        placeholder a caller fills in later; a runnable scenario has both sides.
    ``grid``
        The backdrop's linear wall/scenery code array (empty = open arena).
    ``rules``
        The game's :class:`~engine.combat.RulesBundle` (hit/damage formulas + roles).
        ``None`` is a bundle-less fight — movable and surrenderable, but a shot errors.
    ``dir_memory``
        The CPU side's per-fighter direction memory (``ri(i)``), keyed by fighter index.
    ``seed``
        An optional RNG seed a runner may use to make the fight reproducible. The
        engine does not consume it directly; a caller
        (:func:`engine.fight_loop.simulate`, a debug tool)
        seeds its RNG from it.
    """

    sides: tuple[tuple[Fighter, ...], tuple[Fighter, ...]] | None = None
    grid: tuple[int, ...] = ()
    rules: RulesBundle | None = None
    dir_memory: Mapping[int, int] | None = None
    seed: int | None = None

    @classmethod
    def from_roster(
        cls,
        roster: Any,
        *,
        enemy_count: int,
        enemy_weapon: int,
        enemy_vitality: int,
        enemy_attrs: Mapping[str, int] | None = None,
        enemy_name: str = "",
        grid: tuple[int, ...] = (),
        equip: Any = None,
        rules: RulesBundle | None = None,
        seed: int | None = None,
        owner: int | None = None,
    ) -> "Scenario":
        """Build the procedural scenario the three in-game handlers share.

        A **thin wrapper over** :func:`engine.combat_setup.setup_combat`: it
        forwards every fight-construction argument, then copies the resulting
        ``CombatState``'s ``sides``/``grid``/``dir_memory`` onto the scenario. Side
        placement, per-fighter equipment construction (``equip``), and direction-memory
        init all stay in ``setup_combat`` — this method adds no combat logic of its own.

        The signature matches ``setup_combat``'s shape:
        ``enemy_vitality`` is the enemy's vitality slot, ``enemy_attrs`` its non-vitality
        stats, and ``equip`` the per-fighter equipment constructor. ``rules`` and
        ``seed`` ride alongside — they are not ``setup_combat``'s concern but they are
        the scenario's. ``owner`` is the player whose roster side 1 is.
        """
        state = setup_combat(
            roster,
            enemy_count=enemy_count,
            enemy_weapon=enemy_weapon,
            enemy_vitality=enemy_vitality,
            enemy_attrs=enemy_attrs,
            enemy_name=enemy_name,
            grid=grid,
            equip=equip,
            owner=owner,
        )
        return cls(
            sides=state.sides,
            grid=state.grid,
            rules=rules,
            dir_memory=dict(state.dir_memory),
            seed=seed,
        )

    @classmethod
    def from_encounter(
        cls,
        encounter: Any,
        roster: Any,
        rules: RulesBundle | None,
        *,
        enemy_attrs: Mapping[str, int] | None = None,
        grid: tuple[int, ...] = (),
        equip: Any = None,
        seed: int | None = None,
        owner: int | None = None,
    ) -> "Scenario":
        """Build a scenario from a **parsed encounter declaration**.

        Produces the SAME ``Scenario`` :meth:`from_roster` builds — it simply reads the
        four enemy-setup fields (``count``/``weapon``/``vitality``/``name``) off the
        parsed ``encounter`` instead of taking them as keyword arguments, then delegates
        to :meth:`from_roster`. No new combat logic: same thin wrapper over
        :func:`engine.combat_setup.setup_combat`.

        ``encounter`` is the config's own **already-parsed** encounter value — it exposes
        ``count``/``weapon``/``vitality``/``name`` attributes (see the config's encounter
        loader). The engine deliberately does **not** load the YAML or resolve a key here:
        it knows nothing about this game's config layout, file paths, or the encounter
        format. Loading and validating the declaration is the config's job (its
        ``setup.load_encounter``); ``from_encounter`` takes the parsed result. The
        non-setup pieces — ``enemy_attrs`` (this game's fixed CPU stats), ``grid`` (the
        resolved backdrop), ``equip`` (the per-fighter equipment constructor), ``rules``,
        and ``seed`` — ride alongside, exactly as they do for ``from_roster``: they are
        game data the engine does not synthesize.
        """
        return cls.from_roster(
            roster,
            enemy_count=encounter.count,
            enemy_weapon=encounter.weapon,
            enemy_vitality=encounter.vitality,
            enemy_attrs=enemy_attrs,
            enemy_name=encounter.name,
            grid=grid,
            equip=equip,
            rules=rules,
            seed=seed,
            owner=owner,
        )

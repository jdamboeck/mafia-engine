"""``Scenario`` — one value that fully describes a fight (U5, R6/R7).

Before this unit the "payload in" half of a fight was an ephemeral set of kwargs:
a handler called :func:`engine.combat.setup_combat`, pulled ``sides``/``grid``/
``dir_memory`` off the returned :class:`~engine.state.CombatState`, and passed them
straight into a ``StartCombat`` — nothing a test or tool could hold, name, or build
without a ``GameState``. ``Scenario`` makes that payload a **named, inspectable,
freely-constructible value**.

Two construction paths converge on one shape:

* **Explicit** — build the fighter tuples directly, no ``GameState`` / roster / YAML.
  This is what ``tests/test_driver.py`` already does by hand; a ``Scenario`` gives it
  a name. A scenario can carry **invented entities**: a fighter with a weapon id the
  real game never defined, carrying its own ``equipment`` stat mapping. Because a
  fighter carries its **constructed equipment** (amendment A1 — the engine holds no
  weapon table), inventing one needs no new machinery and no parallel stats entry to
  keep in sync. There is no ``(0, 0)`` fallback and no ``KeyError`` to fall into: the
  stats are simply on the fighter.
* **Procedural** — :meth:`Scenario.from_roster` is a **thin wrapper over the unchanged**
  :func:`engine.combat.setup_combat`. It does not re-implement side placement,
  equipment construction, or direction-memory init — it calls ``setup_combat`` and
  copies the three fields onto the scenario. This is the form the three in-game combat
  handlers share.

**Payload OUT is U3's :class:`~engine.combat.CombatResult`, not this.** ``Scenario`` is
only the payload *in*. And ``StartCombat`` is deliberately **not** widened to accept a
``Scenario`` here (that belongs to U6, which already touches that dataclass) — a handler
unpacks the scenario into the same ``StartCombat`` kwargs it always used.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from engine.combat import RulesBundle, setup_combat
from engine.state import Fighter

__all__ = ["Scenario"]


@dataclass(frozen=True)
class Scenario:
    """A complete description of one fight, constructible without a ``GameState``.

    Fields mirror exactly what :class:`~engine.interactions.StartCombat` and
    :class:`~engine.combat.CombatFight` consume — a scenario IS the fight's input:

    ``sides``
        The two sides as tuples of fully-constructed :class:`~engine.state.Fighter`
        (positions set, equipment on each — amendment A1). ``None`` only for a
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
        engine does not consume it directly; a caller (U6's ``simulate``, a debug tool)
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
    ) -> "Scenario":
        """Build the procedural scenario the three in-game handlers share (U5).

        A **thin wrapper over the unchanged** :func:`engine.combat.setup_combat`: it
        forwards every fight-construction argument, then copies the resulting
        ``CombatState``'s ``sides``/``grid``/``dir_memory`` onto the scenario. Side
        placement, per-fighter equipment construction (``equip``), and direction-memory
        init all stay in ``setup_combat`` — this method adds no combat logic of its own.

        The signature matches ``setup_combat``'s current shape (amendments A5/A1):
        ``enemy_vitality`` is the enemy's vitality slot, ``enemy_attrs`` its non-vitality
        stats, and ``equip`` the per-fighter equipment constructor. ``rules`` and
        ``seed`` ride alongside — they are not ``setup_combat``'s concern but they are
        the scenario's.
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
    ) -> "Scenario":
        """Build a scenario from a **parsed encounter declaration** (U6a).

        Produces the SAME ``Scenario`` :meth:`from_roster` builds — it simply reads the
        four enemy-setup fields (``count``/``weapon``/``vitality``/``name``) off the
        parsed ``encounter`` instead of taking them as keyword arguments, then delegates
        to :meth:`from_roster`. No new combat logic: same thin wrapper over
        :func:`engine.combat.setup_combat`.

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
        )

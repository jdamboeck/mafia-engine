"""``Scenario`` — the fight's "payload in" as a named, freely-constructible value (U5).

Covers the plan's U5 scenarios, reconciled with amendments A1/A5/A6:

* ``from_roster`` is a thin wrapper over ``setup_combat`` — it produces
  ``sides``/``grid``/``dir_memory`` IDENTICAL to a direct ``setup_combat`` call, for
  each in-game fight's real parameters (differential).
* an explicit ``Scenario`` constructs with no ``GameState``, roster, or YAML load.
* an INVENTED weapon (id 250, stats no ``weapons.yaml`` defines) resolves a shot from
  the equipment carried ON the fighter (A1: the engine holds no weapon table, so there
  is no ``(0, 0)`` fallback and no ``KeyError``).
* round-trip: ``Scenario`` -> fight -> ``CombatResult``, with zero ``GameState`` built.
"""

from __future__ import annotations

from pathlib import Path

from engine.combat import CombatFight, CombatResult, setup_combat
from engine.scenario import Scenario
from engine.state import CombatState, Fighter
from data.game_configs.mafia_1920s.combat_rules import build_rules, equipper
from data.game_configs.mafia_1920s.gangster import Gangster
from tests.helpers import StubRng

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"


def _weapon_stats() -> dict:
    """The reference title's ``{weapon_id: (ts, tg, range)}`` table, loaded from YAML."""
    import yaml

    raw = yaml.safe_load((_CONFIG_DIR / "entities" / "weapons.yaml").read_text())
    return {i: (w["ts"], w["tg"], w["range"]) for i, w in enumerate(raw["weapons"])}


# --------------------------------------------------------------------------- #
# from_roster — a thin wrapper over the unchanged setup_combat                 #
# --------------------------------------------------------------------------- #
def test_from_roster_matches_setup_combat_field_for_field():
    """The procedural path reproduces ``setup_combat``'s output exactly (U5)."""
    roster = [Gangster(name="capone", weapon=5, energie=20, kraft=40, brutalitaet=50)]
    kwargs = dict(
        enemy_count=3,
        enemy_weapon=6,
        enemy_vitality=35,
        enemy_attrs={"kraft": 30, "brutalitaet": 30},
        enemy_name="ganove",
        grid=(),
        equip=equipper(_weapon_stats()),
    )
    state = setup_combat(roster, **kwargs)
    scenario = Scenario.from_roster(roster, rules=build_rules(), seed=7, **kwargs)

    assert scenario.sides == state.sides
    assert scenario.grid == state.grid
    assert dict(scenario.dir_memory) == dict(state.dir_memory)
    # The two fields setup_combat does not carry, but the scenario does:
    assert scenario.rules == build_rules()
    assert scenario.seed == 7


def test_from_roster_equips_each_fighter_via_setup_combat_not_a_table():
    """A1: equipment is constructed ONTO each fighter at setup; the scenario holds no
    weapon table. The player and enemy fighters carry their resolved stats."""
    scenario = Scenario.from_roster(
        [Gangster(name="capone", weapon=5, energie=20)],
        enemy_count=1,
        enemy_weapon=0,
        enemy_vitality=20,
        enemy_attrs={"kraft": 30, "brutalitaet": 30},
        equip=equipper(_weapon_stats()),
    )
    assert scenario.sides[0][0].equipment["ts"] == 5  # revolver (id 5)
    assert scenario.sides[1][0].equipment["ts"] == 2  # bare hands (id 0)
    assert not hasattr(scenario, "weapon_stats")  # no parallel table (A1/A6)


# --------------------------------------------------------------------------- #
# Explicit path — no GameState, no roster, no YAML                            #
# --------------------------------------------------------------------------- #
def test_explicit_scenario_needs_no_gamestate_or_config():
    """A caller hand-builds the fight; nothing touches GameState/roster/YAML."""
    scenario = Scenario(
        sides=(
            (Fighter(name="hero", weapon=5, vitality=20, position=100),),
            (Fighter(name="thug", weapon=0, vitality=5, position=101),),
        ),
        grid=(),
        rules=build_rules(),
    )
    assert scenario.sides[0][0].name == "hero"
    assert scenario.sides[1][0].name == "thug"
    assert scenario.rules == build_rules()


def test_a_scenario_defaults_to_an_empty_placeholder():
    """A bare ``Scenario()`` is a legal placeholder — every field optional (U5)."""
    scenario = Scenario()
    assert scenario.sides is None
    assert scenario.grid == ()
    assert scenario.rules is None
    assert scenario.dir_memory is None
    assert scenario.seed is None


# --------------------------------------------------------------------------- #
# Inventing an entity needs no new machinery (A1/A6)                           #
# --------------------------------------------------------------------------- #
def test_a_scenario_with_an_invented_weapon_resolves_a_shot_from_on_fighter_stats():
    """A weapon id (250) no ``weapons.yaml`` defines still fights: its stats ride ON the
    fighter's ``equipment`` (A1), so the shot resolves from those — not a ``(0, 0)``
    fallback (there is none) and not a ``KeyError`` (there is no table to miss).
    """
    # An invented high-accuracy, high-damage, long-range weapon carried on the fighter.
    invented = {"ts": 9, "tg": 12, "range": 40}
    scenario = Scenario(
        sides=(
            (
                Fighter(
                    name="hero",
                    weapon=250,
                    vitality=20,
                    attrs={"kraft": 99, "brutalitaet": 99},
                    position=100,
                    equipment=invented,
                ),
            ),
            (
                Fighter(
                    name="thug",
                    weapon=0,
                    vitality=5,
                    attrs={"kraft": 30, "brutalitaet": 30},
                    position=101,  # adjacent, directly to the right
                    equipment={"ts": 2, "tg": 2, "range": 2},
                ),
            ),
        ),
        rules=build_rules(),
    )
    state = CombatState(sides=scenario.sides, grid=scenario.grid or ())
    # StubRng: two non-zero hit factors + a damage draw -> a guaranteed hit.
    fight = CombatFight(state, rng=StubRng(1, 1, 5), rules=scenario.rules)
    outcome = fight.shoot(+1)  # fire right at the adjacent enemy

    assert outcome["hit"] is True  # resolved from the invented stats, not a (0,0) miss
    assert outcome["damage"] > 0


# --------------------------------------------------------------------------- #
# Round-trip: Scenario -> fight -> CombatResult, zero GameState               #
# --------------------------------------------------------------------------- #
def test_scenario_runs_to_a_combat_result_with_no_gamestate():
    """The whole fight lifecycle from a hand-built ``Scenario``, no ``GameState`` in
    sight: build -> fight -> a per-side ``CombatResult`` (U3)."""
    scenario = Scenario(
        sides=(
            (
                Fighter(
                    name="hero",
                    weapon=5,
                    vitality=20,
                    attrs={"kraft": 99, "brutalitaet": 99},
                    position=100,
                    equipment={"ts": 9, "tg": 12, "range": 40},
                ),
            ),
            (
                Fighter(
                    name="thug",
                    weapon=0,
                    vitality=1,  # one hit ends it
                    attrs={"kraft": 30, "brutalitaet": 30},
                    position=101,
                    equipment={"ts": 2, "tg": 2, "range": 2},
                ),
            ),
        ),
        rules=build_rules(),
    )
    state = CombatState(sides=scenario.sides, grid=scenario.grid or ())
    fight = CombatFight(state, rng=StubRng(1, 1, 5), rules=scenario.rules)
    fight.shoot(+1)  # downs the 1-vitality enemy
    winner = fight.winner()
    result = CombatResult(winner=winner, losses=fight.losses)

    assert result.winner == 1  # side 1 wins; side 2's only fighter is down
    assert result.losses == (0, 1)  # one side-2 death, zero side-1

"""Data-defined encounters (U6a) — differential + load-time validation.

A fight is now declared in config data (``content/encounters/*.yaml``), not assembled
inline in a handler. ``Scenario.from_encounter(spec, roster, rules, …)`` reads the parsed
declaration and produces the SAME ``Scenario`` the handler used to build with
``Scenario.from_roster(…)``. This suite proves that equivalence for every one of the
game's five fights (the plan's "regrouping of existing data, so any divergence is a
transcription error"), and proves the config's load-time validation posture.

Cited behaviour (all values regroup config.yaml / the old handler literals):

- kdh ambush — mf-prg.bas:15312 (setup), :15315 (loss = silent), :15320-15321 (win).
- kdh collectors — mf-prg.bas:4355 (setup; the seizure at :4365-4370 stays in Python).
- job bouncer/croupier/killer — mf-prg.bas:25040-25042 / :25135 / :25210.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.combat_rules import build_rules, enemy_attrs, equipper
from data.game_configs.mafia_1920s.setup import (
    load_combat_backdrop,
    load_encounter,
    weapon_stats_by_id,
)
from data.game_configs.mafia_1920s.gangster import Gangster
from engine.scenario import Scenario

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_ENCOUNTERS = _CONFIG_DIR / "content" / "encounters"


# --------------------------------------------------------------------------- #
# Fixtures mirroring what a handler supplies at the fight's call site          #
# --------------------------------------------------------------------------- #
def _roster():
    """A two-gangster roster — the same shape a handler reads off ``active.roster``."""
    return [
        Gangster(name="alcapone", energie=99, kraft=40, brutalitaet=20),
        Gangster(name="nitti", energie=99, kraft=30, brutalitaet=30),
    ]


def _params():
    """This config's ``formula_params`` (the enemy 30/30 stats live here — A5)."""
    import yaml

    with (_CONFIG_DIR / "config.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["formula_params"]


def _weapon_stats():
    return weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")


def _backdrop(name: str):
    return load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{name}.yaml")


def _load(key: str):
    return load_encounter(_ENCOUNTERS / f"{key}.yaml")


# --------------------------------------------------------------------------- #
# Differential — from_encounter == the inline from_roster it replaces         #
# --------------------------------------------------------------------------- #
# Each row: (encounter key, variant index, backdrop name, and the enemy_count/
# weapon/vitality/name the OLD handler passed inline — the transcription being proven).
_DIFFERENTIAL = [
    # kdh ambush (kdh.py): count 1, weapon 6, vitality 35, schuldner, grid ks.
    ("kdh_ambush", 0, "ks", 1, 6, 35, "schuldner"),
    # kdh collectors (upkeep.py): count 5, weapon 3, vitality 30, eintreiber, grid ks.
    ("kdh_collectors", 0, "ks", 5, 3, 30, "eintreiber"),
    # job bouncer (jobs.py) — its three _BOUNCER_BRAWLERS variants, grid kp.
    ("job_bouncer", 0, "kp", 1, 0, 30, "wurstfinger-fred"),
    ("job_bouncer", 1, "kp", 1, 1, 20, "affenface-alf"),
    ("job_bouncer", 2, "kp", 1, 3, 20, "der schlachter"),
    # job croupier (jobs.py): _CROUPIER_OPPONENT — count 1, weapon 1, vitality 10.
    ("job_croupier", 0, "kp", 1, 1, 10, "spieler"),
    # job killer (jobs.py): _KILLER_VICTIM — count 1, weapon 0, vitality 20.
    ("job_killer", 0, "km", 1, 0, 20, "opfer"),
]


@pytest.mark.parametrize(
    "key, variant, grid_name, count, weapon, vitality, name",
    _DIFFERENTIAL,
    ids=[f"{r[0]}[{r[1]}]" for r in _DIFFERENTIAL],
)
def test_from_encounter_equals_inline_from_roster(
    key, variant, grid_name, count, weapon, vitality, name
):
    """``from_encounter`` produces a Scenario byte-identical to the inline construction
    the handler used before U6a — same roster, same params. setup_combat is fully
    deterministic (no RNG), so equal inputs give an equal frozen Scenario."""
    roster = _roster()
    params = _params()
    grid = _backdrop(grid_name)
    equip = equipper(_weapon_stats())
    attrs = enemy_attrs(params)

    enc = _load(key)
    spec = enc.variants[variant]
    # The spec regroups exactly what the handler used to pass inline.
    assert (spec.count, spec.weapon, spec.vitality, spec.name) == (count, weapon, vitality, name)

    via_encounter = Scenario.from_encounter(
        spec, roster, build_rules(), enemy_attrs=attrs, grid=grid, equip=equip
    )
    via_roster = Scenario.from_roster(
        roster,
        enemy_count=count,
        enemy_weapon=weapon,
        enemy_vitality=vitality,
        enemy_attrs=attrs,
        enemy_name=name,
        grid=grid,
        equip=equip,
        rules=build_rules(),
    )
    assert via_encounter == via_roster


# --------------------------------------------------------------------------- #
# Selection stays in Python; definitions move to data                         #
# --------------------------------------------------------------------------- #
def test_bouncer_encounter_carries_all_three_variants_in_source_order():
    """The bouncer's three troublemakers are ONE encounter with a variants list, in the
    source's :25040-:25042 order — the caller's ctx.rng.range(3) roll indexes it, so a
    given roll selects the same variant it always did."""
    enc = _load("job_bouncer")
    assert len(enc.variants) == 3
    assert [(v.weapon, v.vitality, v.name) for v in enc.variants] == [
        (0, 30, "wurstfinger-fred"),  # :25040
        (1, 20, "affenface-alf"),  # :25041
        (3, 20, "der schlachter"),  # :25042
    ]


# --------------------------------------------------------------------------- #
# kdh ambush consequence — declared fully in data                             #
# --------------------------------------------------------------------------- #
def test_kdh_ambush_win_declares_loot_score_and_message():
    """The ambush WIN consequence is fully declarable: a money roll in [500, 1499], a
    +2 score change, and the loot message (:15320-15321)."""
    enc = _load("kdh_ambush")
    assert enc.on_win is not None
    by_key = {next(iter(step)): step for step in enc.on_win}
    assert by_key["money"]["money"] == {"roll": [500, 1499]}  # :15320
    assert by_key["score"]["score"] == 2.0  # :15321
    assert by_key["message"]["message"] == "locations.kdh.ambush_loot"


def test_kdh_ambush_loss_declares_nothing():
    """The ambush LOSS is a silent return (:15315) — an empty on_loss, so apply_outcome
    applies nothing on a loss."""
    enc = _load("kdh_ambush")
    assert enc.on_loss == ()  # [] in YAML -> empty tuple; nothing to apply


# --------------------------------------------------------------------------- #
# Setup-only encounters omit the consequence (the honest boundary)            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["kdh_collectors", "job_bouncer", "job_croupier", "job_killer"])
def test_setup_only_encounters_carry_no_declared_consequence(key):
    """The collectors' seizure (live ka) and each job's payout are NOT declarable, so
    those encounters carry no on_win/on_loss — a supported shape, not a partial
    migration. Their consequence stays in Python."""
    enc = _load(key)
    assert enc.on_win is None
    assert enc.on_loss is None


# --------------------------------------------------------------------------- #
# No handler assembles a fight inline (the unit's verification bar)           #
# --------------------------------------------------------------------------- #
def test_no_handler_assembles_a_fight_inline():
    """After U6a, no handler contains an opponent literal or an inline
    setup_combat/Scenario.from_roster call — every fight setup is declared in data."""
    handlers = _CONFIG_DIR / "handlers"
    banned = ("_BOUNCER_BRAWLERS", "_CROUPIER_OPPONENT", "_KILLER_VICTIM", "setup_combat(")
    for path in (handlers / "kdh.py", handlers / "upkeep.py", handlers / "jobs.py"):
        src = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in src, f"{path.name} still references {token!r}"
        # from_roster may appear in a comment but never as a call.
        assert ".from_roster(" not in src, f"{path.name} still calls Scenario.from_roster"


# --------------------------------------------------------------------------- #
# Load-time validation — a typo fails at load, not at fight time              #
# --------------------------------------------------------------------------- #
def test_unknown_outcome_key_fails_at_load(tmp_path):
    """An on_win step with a key outside the declarable vocabulary raises AT LOAD."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "key: bad\ngrid: ks\nenemies:\n  count: 1\n  weapon: 6\n  vitality: 10\n  name: x\n"
        "on_win:\n  - teleport: home\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown outcome key"):
        load_encounter(bad, config_dir=_CONFIG_DIR)


def test_missing_grid_fails_at_load(tmp_path):
    """An encounter naming a grid with no backdrop file raises AT LOAD."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "key: bad\ngrid: nonexistent_grid\nenemies:\n  count: 1\n  weapon: 6\n"
        "  vitality: 10\n  name: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="grid"):
        load_encounter(bad, config_dir=_CONFIG_DIR)


def test_unknown_weapon_id_fails_at_load(tmp_path):
    """An enemy naming a weapon id no entity declares raises AT LOAD, not mid-fight."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "key: bad\ngrid: ks\nenemies:\n  count: 1\n  weapon: 999\n  vitality: 10\n  name: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="weapon"):
        load_encounter(bad, config_dir=_CONFIG_DIR)

"""The game's own effects live in the config, and the engine names none of them.

``data/game_configs/mafia_1920s/effects.py`` holds every effect that ports one of this
game's rules or writes this game's state; ``engine/effects.py`` keeps only generic ones
(docs/design/engine-architecture.md, "Engine/config seam"). These tests hold that line:

* no class the config's effects module registers is named anywhere in ``engine/`` —
  the list is read from the module, so a newly moved effect is covered without an edit
  here;
* loading the config registers them, so a save holding them round-trips and replays.
"""

from __future__ import annotations

import re
from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import EFFECTS, commit, effect_tag
from engine.persistence import load_game, replay, save_game

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _ROOT / "data" / "game_configs" / "mafia_1920s"
_ENGINE_DIR = _ROOT / "engine"


def _config_effect_classes() -> dict[str, type]:
    """Every effect the config's own effects module registers, by class name."""
    module = load_game_config(_CONFIG_DIR).module.effects
    return {
        name: obj
        for name, obj in vars(module).items()
        if isinstance(obj, type) and obj.__module__ == module.__name__ and effect_tag(obj)
    }


def test_the_config_registers_its_effects_on_load():
    classes = _config_effect_classes()
    assert classes, "the config's effects module registers no effect"
    for name, cls in classes.items():
        tag = effect_tag(cls)
        assert tag is not None
        assert EFFECTS[tag] is cls, f"{name} is not the registered class for tag {tag!r}"


def test_no_config_effect_is_named_in_the_engine():
    """``engine/`` spells no config effect: not in code, not in a docstring."""
    names = set()
    for name, cls in _config_effect_classes().items():
        names |= {name, effect_tag(cls) or name}
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names))) + r")\b")
    hits = [
        f"{path.relative_to(_ROOT)}:{lineno}: {match.group(0)}"
        for path in sorted(_ENGINE_DIR.rglob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in [pattern.search(line)]
        if match
    ]
    assert hits == [], "engine/ names config effects:\n" + "\n".join(hits)


def test_a_save_holding_config_effects_round_trips(tmp_path: Path):
    """Every moved effect saves under its class-name tag, loads back equal, and replays
    to the same state as committing it live."""
    loaded = load_game_config(_CONFIG_DIR)
    fx = loaded.module.effects
    state = loaded.new_game(
        seed=7, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    log = [
        fx.ScoreAndRank(amount=2.0, rank_divisor=11.1),
        fx.SetTenancy(ln=3),
        fx.RentAccrue(months=2),
        fx.RankCommit(new_rank=2),
        fx.PendingRankReset(),
        fx.Jail(months=4),
        fx.DebtChange(amount=500, months=6),
        fx.DebtChange(amount=-100),
        fx.DebtClear(),
        fx.ShopChange(tile=2, capital_delta=300),
        fx.BarrelChange(amount=5),
        fx.MarkSet(fake_papers=True, counterfeit=True),
        fx.MarkSet(fake_papers=False),
        fx.TipSet(tip_type=3),
        fx.TipClear(),
        fx.JobSet(type=2, pending_pay=900, months_left=3),
        fx.JobClear(),
        fx.GangsterMarkHired(candidate_id=4),
    ]
    assert {type(e).__name__ for e in log} == set(_config_effect_classes()), (
        "the round-trip log must hold one of every config effect"
    )
    path = tmp_path / "game.jsonl"
    save_game(path, state, registries=loaded.registries, effect_log=log, rng_log=[], seed=7)

    # A fresh load re-executes the config: the classes are rebuilt, the tags are not.
    registries = load_game_config(_CONFIG_DIR).registries
    save = load_game(path, registries)

    assert save.effect_log == log
    assert [effect_tag(e) for e in save.effect_log] == [type(e).__name__ for e in log]
    assert replay(save, registries) == commit(state, log).state

"""House rules (U10, R20/R21, KTD-9): the catalogue, the setup step, the frozen map.

The config's quirk catalogue (``content/house_rules.yaml``) is schema-checked when the
config loads. New-game setup offers an optional step, skipped by default, that lists the
entries with a switch -- every one starting at faithful -- and the chosen map lives on
``state.config.house_rules``: frozen, stored in the save, and never written by an effect
(the engine refuses any effect whose result changes ``state.config``). A save with no map
is refused, never default-filled (KTD-5).

The framework tests run a COPY of the config whose catalogue is the fixture below, so
they do not move when the real catalogue does. Its entries are made up for the test and
name no game quirk. Client tests drive ``main()`` only.

The real catalogue (U11, R22) is tested at the end: each switch in both settings, each
faithful-only entry by the test that holds its faithful behaviour, and every entry's
quote held to its cited line by the citation checker.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.effects import EFFECTS, ConfigWriteError, commit, register_effect
from engine.persistence import load_game, save_game
from engine.state import Config, GameState
from engine.types import ConfigValidationError
from tests.helpers import deadline

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

#: A made-up catalogue: two switchable entries and one faithful-only note.
_FIXTURE_CATALOGUE = {
    "house_rules": [
        {
            "id": "alpha",
            "citation": ":311",
            "quote": "in=xor30",
            "faithful": "fixture: the faithful alpha behaviour",
            "intent": "fixture: the intended alpha behaviour",
            "switch": True,
        },
        {
            "id": "beta",
            "citation": ":4355",
            "quote": "ifs=1thenreturn",
            "faithful": "fixture: the faithful beta behaviour",
            "intent": "fixture: the intended beta behaviour",
            "switch": True,
        },
        {
            "id": "gamma",
            "citation": ":25560",
            "quote": "x=3+3*(jo(sp)=2)",
            "faithful": "fixture: the faithful gamma behaviour",
            "no_intent": "fixture: the intent is not clear",
            "switch": False,
        },
    ]
}

#: The fixture entries' one-line setup descriptions (theme strings).
_FIXTURE_STRINGS = {
    "house_rules": {
        "alpha": "alpha: the first fixture switch",
        "beta": "beta: the second fixture switch",
    }
}

_OFFER = "house rules: Enter plays every rule as in the original"


def _write_yaml(path: Path, data) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _config_copy(tmp_path: Path, catalogue: dict) -> Path:
    """A copy of the mafia_1920s config whose catalogue is ``catalogue``."""
    cfg_dir = tmp_path / "mafia_1920s"
    shutil.copytree(_CONFIG_DIR, cfg_dir, ignore=shutil.ignore_patterns("__pycache__"))
    _write_yaml(cfg_dir / "content" / "house_rules.yaml", catalogue)
    _write_yaml(cfg_dir / "themes" / "classic" / "strings" / "house_rules.yaml", _FIXTURE_STRINGS)
    return cfg_dir


@pytest.fixture
def fixture_config(tmp_path, monkeypatch):
    """The fixture-catalogue config, installed as the terminal client's config.

    The copy's package registers its handlers and effects over the real config's on
    load; the real config is loaded again afterwards so later tests see its own.
    """
    import clients.terminal.cli as cli
    import clients.terminal.session as session

    cfg_dir = _config_copy(tmp_path, _FIXTURE_CATALOGUE)
    monkeypatch.setattr(session, "_CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli, "_CONFIG_DIR", cfg_dir)
    yield cfg_dir
    load_game_config(_CONFIG_DIR)


def _main(monkeypatch, argv: list[str], stdin_text: str) -> str:
    """Run the real ``main()`` over scripted stdin; return its stdout."""
    from clients.terminal import main

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(20, "main() did not return (spin?)", exc_type=AssertionError):
        main(argv)
    return out.getvalue()


def _saved_state(path: Path, cfg_dir: Path) -> GameState:
    return load_game(path, load_game_config(cfg_dir).registries).state


_NEW_GAME = ["--seed", "42", "--end-year", "1950", "--score-weight", "1"]


# --------------------------------------------------------------------------- #
# The setup step, driven through main()                                       #
# --------------------------------------------------------------------------- #
def test_accepting_the_defaults_stores_an_all_faithful_map(monkeypatch, tmp_path, fixture_config):
    save = tmp_path / "s.jsonl"
    # title, Enter at the house-rules offer, upkeep ack, save, quit.
    text = _main(monkeypatch, [*_NEW_GAME, "--save", str(save)], "\n\n\np\nq\n")
    assert _OFFER in text
    rules = _saved_state(save, fixture_config).config.house_rules
    assert dict(rules) == {"alpha": "faithful", "beta": "faithful"}


def test_switching_one_entry_stores_exactly_that_change(monkeypatch, tmp_path, fixture_config):
    save = tmp_path / "s.jsonl"
    # title, h (change them), 2 (switch beta), Enter (start), upkeep ack, save, quit.
    text = _main(monkeypatch, [*_NEW_GAME, "--save", str(save)], "\nh\n2\n\n\np\nq\n")
    # The list shows the switchable entries, each with its one-line description,
    # all at faithful first -- never the faithful-only note.
    assert "1. [faithful] alpha: the first fixture switch" in text
    assert "2. [faithful] beta: the second fixture switch" in text
    assert "2. [intent] beta: the second fixture switch" in text
    assert "gamma" not in text
    rules = _saved_state(save, fixture_config).config.house_rules
    assert dict(rules) == {"alpha": "faithful", "beta": "intent"}


def test_a_solo_game_goes_through_the_house_rules_step(monkeypatch, tmp_path, fixture_config):
    save = tmp_path / "s.jsonl"
    argv = [*_NEW_GAME, "--player", "solo:lone gang", "--save", str(save)]
    text = _main(monkeypatch, argv, "\nh\n1\n\n\np\nq\n")
    assert _OFFER in text
    state = _saved_state(save, fixture_config)
    assert state.clock.player_count == 1
    assert dict(state.config.house_rules) == {"alpha": "intent", "beta": "faithful"}


def test_a_bad_answer_in_the_list_asks_again(monkeypatch, tmp_path, fixture_config):
    save = tmp_path / "s.jsonl"
    # 3 and x are no entry: the list is shown again, unchanged.
    text = _main(monkeypatch, [*_NEW_GAME, "--save", str(save)], "\nh\n3\nx\n\n\np\nq\n")
    assert text.count("1. [faithful] alpha") == 3
    rules = _saved_state(save, fixture_config).config.house_rules
    assert dict(rules) == {"alpha": "faithful", "beta": "faithful"}


def test_round_trip_setup_save_load_keeps_the_map(monkeypatch, tmp_path, fixture_config):
    first = tmp_path / "first.jsonl"
    _main(monkeypatch, [*_NEW_GAME, "--save", str(first)], "\nh\n2\n\n\np\nq\n")
    # Load it (no setup, no house-rules step), save again at the turn menu, quit.
    second = tmp_path / "second.jsonl"
    text = _main(monkeypatch, ["--load", str(first), "--save", str(second)], "p\nq\n")
    assert _OFFER not in text
    rules = _saved_state(second, fixture_config).config.house_rules
    assert dict(rules) == {"alpha": "faithful", "beta": "intent"}


def test_the_real_catalogue_offers_its_switches_at_setup(monkeypatch, tmp_path):
    # The real catalogue has switches (U11): the step is shown, Enter keeps every rule
    # faithful, and the save carries the full map.
    save = tmp_path / "s.jsonl"
    text = _main(monkeypatch, [*_NEW_GAME, "--save", str(save)], "\n\n\np\nq\n")
    assert _OFFER in text
    assert dict(_saved_state(save, _CONFIG_DIR).config.house_rules) == {
        "intelligence_or_30": "faithful",
        "shared_direction_memory": "faithful",
    }


# --------------------------------------------------------------------------- #
# A save without the map is refused                                           #
# --------------------------------------------------------------------------- #
def test_a_save_with_no_map_is_refused_with_one_line(capsys, tmp_path):
    from clients.terminal import main

    cfg = load_game_config(_CONFIG_DIR)
    state = cfg.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    path = tmp_path / "no-map.jsonl"
    save_game(path, state, registries=cfg.registries, effect_log=[], rng_log=[], seed=42)
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    del header["snapshot"]["config"]["house_rules"]
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["--load", str(path)])
    err = capsys.readouterr().err
    assert exc.value.code == 1
    assert "Traceback" not in err
    assert err == f"cannot load {path}: the save stores no house rules\n"


@pytest.mark.parametrize(
    ("house_rules", "named"),
    [
        ({"intelligence_or_30": "faithful"}, "shared_direction_memory"),
        (
            {
                "intelligence_or_30": "faithful",
                "shared_direction_memory": "faithful",
                "retired_rule": "intent",
            },
            "retired_rule",
        ),
        ({}, "intelligence_or_30"),
    ],
)
def test_a_save_whose_switches_differ_from_the_catalogue_is_refused_with_one_line(
    capsys, tmp_path, house_rules, named
):
    """A save made under another catalogue (a switch added or retired since) would
    play some rule under a setting nobody chose, so it is refused, naming the rule."""
    from clients.terminal import main

    cfg = load_game_config(_CONFIG_DIR)
    state = cfg.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    path = tmp_path / "other-catalogue.jsonl"
    save_game(path, state, registries=cfg.registries, effect_log=[], rng_log=[], seed=42)
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    header["snapshot"]["config"]["house_rules"] = house_rules
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["--load", str(path)])
    err = capsys.readouterr().err
    assert exc.value.code == 1
    assert "Traceback" not in err
    assert err.startswith(f"cannot load {path}: the save's house rules do not match this game's: ")
    assert named in err
    assert len(err.strip().splitlines()) == 1


# --------------------------------------------------------------------------- #
# The catalogue's schema check                                                #
# --------------------------------------------------------------------------- #
def _entry(**changes) -> dict:
    return {**_FIXTURE_CATALOGUE["house_rules"][0], **changes}


def test_a_switch_without_intent_text_fails_the_schema_check_at_config_load(tmp_path):
    no_intent_text = _entry(no_intent="fixture: unclear")
    del no_intent_text["intent"]
    cfg_dir = _config_copy(tmp_path, {"house_rules": [no_intent_text]})
    with pytest.raises(ConfigValidationError, match="alpha.*switch.*intent"):
        load_game_config(cfg_dir)
    load_game_config(_CONFIG_DIR)


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        (_entry(intent=""), "intent"),
        (_entry(citation="311"), "citation"),
        (_entry(citation=None), "citation"),
        (_entry(quote=""), "quote"),
        ({k: v for k, v in _entry().items() if k != "quote"}, "quote"),
        (_entry(faithful=""), "faithful"),
        (_entry(switch="yes"), "switch"),
        (_entry(id="Not An Id"), "id"),
        (_entry(colour="red"), "unknown"),
        (_entry(no_intent="both given"), "intent"),
        ({k: v for k, v in _entry().items() if k != "intent"}, "intent"),
    ],
)
def test_a_malformed_entry_fails_the_schema_check(tmp_path, entry, match, mafia_module):
    path = tmp_path / "house_rules.yaml"
    _write_yaml(path, {"house_rules": [entry]})
    with pytest.raises(ConfigValidationError, match=match):
        mafia_module.house_rules.load_house_rules(path)


def test_a_duplicate_id_fails_the_schema_check(tmp_path, mafia_module):
    path = tmp_path / "house_rules.yaml"
    _write_yaml(path, {"house_rules": [_entry(), _entry()]})
    with pytest.raises(ConfigValidationError, match="duplicate"):
        mafia_module.house_rules.load_house_rules(path)


def test_the_fixture_catalogue_passes_the_schema_check(tmp_path, mafia_module):
    path = tmp_path / "house_rules.yaml"
    _write_yaml(path, _FIXTURE_CATALOGUE)
    rules = mafia_module.house_rules.load_house_rules(path)
    assert [r.id for r in rules if r.switch] == ["alpha", "beta"]


def test_every_real_switch_has_a_setup_description(mafia_module):
    from engine.strings import Resolver

    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    for rule in mafia_module.house_rules.CATALOGUE:
        if rule.switch:
            assert resolver.resolve(f"house_rules.{rule.id}").strip(), rule.id


# --------------------------------------------------------------------------- #
# new_game and the config's accessor                                          #
# --------------------------------------------------------------------------- #
def _fixture_new_game(tmp_path, **kwargs) -> GameState:
    cfg = load_game_config(_config_copy(tmp_path, _FIXTURE_CATALOGUE))
    try:
        return cfg.module.new_game(
            seed=1, end_year=1930, score_weight=1.0, players=[("a", "b")], **kwargs
        )
    finally:
        load_game_config(_CONFIG_DIR)


def test_new_game_fills_every_switch_at_faithful(tmp_path):
    state = _fixture_new_game(tmp_path)
    assert dict(state.config.house_rules) == {"alpha": "faithful", "beta": "faithful"}


def test_new_game_takes_the_chosen_switches(tmp_path):
    state = _fixture_new_game(tmp_path, house_rules={"alpha": "intent"})
    assert dict(state.config.house_rules) == {"alpha": "intent", "beta": "faithful"}


@pytest.mark.parametrize("choice", [{"gamma": "intent"}, {"delta": "intent"}, {"alpha": "maybe"}])
def test_new_game_refuses_a_choice_off_the_catalogue(tmp_path, choice):
    with pytest.raises(ValueError):
        _fixture_new_game(tmp_path, house_rules=choice)


def test_config_refuses_a_setting_that_is_neither_faithful_nor_intent():
    with pytest.raises(ValueError, match="house rule"):
        Config(house_rules={"alpha": True})  # pyright: ignore[reportArgumentType]  # the bad value is the test


def test_the_map_is_read_only():
    config = Config(house_rules={"alpha": "faithful"})
    with pytest.raises(TypeError):
        config.house_rules["alpha"] = "intent"  # pyright: ignore[reportIndexIssue]  # the write is the test: it must raise


def test_a_handler_reads_a_switch_through_the_state(tmp_path):
    from engine.config_loader import load_game_config as load

    cfg = load(_config_copy(tmp_path, _FIXTURE_CATALOGUE))
    try:
        intent = cfg.module.house_rules.intent
        state = cfg.module.new_game(
            seed=1,
            end_year=1930,
            score_weight=1.0,
            players=[("a", "b")],
            house_rules={"beta": "intent"},
        )
        assert intent(state, "beta") is True
        assert intent(state, "alpha") is False
        # A state built without the map (a test fixture) reads faithful...
        assert intent(GameState(), "beta") is False
        # ...but an id the catalogue has no switch for is a bug, not a default.
        with pytest.raises(KeyError):
            intent(state, "gamma")
        with pytest.raises(KeyError):
            intent(state, "delta")
    finally:
        load_game_config(_CONFIG_DIR)


# --------------------------------------------------------------------------- #
# No effect writes the map                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def rogue_effects():
    """Two effects that write the config, registered for the test only."""

    @register_effect("_RogueHouseRules")
    @dataclass(frozen=True)
    class RogueHouseRules:
        def apply(self, state: GameState) -> GameState:
            return replace(state, config=replace(state.config, house_rules={"alpha": "intent"}))

    @register_effect("_RogueParams")
    @dataclass(frozen=True)
    class RogueParams:
        def apply(self, state: GameState) -> GameState:
            params = {**state.config.formula_params, "score_mult": 2.0}
            return replace(state, config=replace(state.config, formula_params=params))

    yield RogueHouseRules, RogueParams
    EFFECTS.pop("_RogueHouseRules", None)
    EFFECTS.pop("_RogueParams", None)


def test_no_registered_effect_can_write_the_map(rogue_effects):
    rogue_rules, rogue_params = rogue_effects
    state = GameState(config=Config(house_rules={"alpha": "faithful"}))
    with pytest.raises(ConfigWriteError, match="_RogueHouseRules"):
        commit(state, [rogue_rules()])
    with pytest.raises(ConfigWriteError, match="_RogueParams"):
        commit(state, [rogue_params()])


def test_an_effect_that_rebuilds_an_equal_config_is_not_refused():
    # The guard compares the config, not its identity: an effect that rebuilds the
    # state graph wholesale (an equal config, a new object) still applies.
    @register_effect("_RebuildsConfig")
    @dataclass(frozen=True)
    class RebuildsConfig:
        def apply(self, state: GameState) -> GameState:
            config = Config(
                formula_params=dict(state.config.formula_params),
                house_rules=dict(state.config.house_rules),
            )
            return replace(state, config=config, values={"touched": True})

    try:
        state = GameState(config=Config(house_rules={"alpha": "intent"}))
        after = commit(state, [RebuildsConfig()]).state
        assert after.config is not state.config
        assert after.config.house_rules == state.config.house_rules
        assert after.values["touched"] is True
    finally:
        EFFECTS.pop("_RebuildsConfig", None)


# --------------------------------------------------------------------------- #
# The map in the rules bundle, the scenario files and the fight lab (U38)      #
# --------------------------------------------------------------------------- #
_SCENARIOS = sorted((_CONFIG_DIR / "content" / "scenarios").glob("*.yaml"))


def test_an_in_game_fight_is_built_under_the_games_map():
    """The collectors fight (``mf-prg.bas:4350``) takes its rules bundle's map from
    ``state.config.house_rules``, so a recording of it stores the game's choices."""
    from data.game_configs.mafia_1920s import state as game
    from data.game_configs.mafia_1920s.gangster import Gangster
    from data.game_configs.mafia_1920s.state import Business, Debt
    from engine.interactions import ShowMessage, StartCombat
    from engine.locations import HANDLERS
    from engine.state import Clock, Player
    from engine.upkeep import UPKEEP_HANDLER_KEY
    from tests.helpers import StubRng

    load_game_config(_CONFIG_DIR)
    params = yaml.safe_load((_CONFIG_DIR / "config.yaml").read_text(encoding="utf-8"))
    chosen = {"alpha": "intent", "beta": "faithful"}
    player = Player(
        name="alcapone",
        ka=7500,
        values=game.values_of(Debt(amount=3000, months=1), Business()),
        roster=(Gangster(name="alcapone", energie=40, kraft=30, brutalitaet=30),),
    )
    state = GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=params["formula_params"], house_rules=chosen),
    )

    class _Ctx:
        rng = StubRng()

        def __init__(self) -> None:
            self.state = state

        def apply(self, effect):
            pass

    gen = HANDLERS[UPKEEP_HANDLER_KEY](_Ctx())
    interaction = gen.send(None)
    while isinstance(interaction, ShowMessage):
        interaction = gen.send(None)
    assert isinstance(interaction, StartCombat)
    assert interaction.scenario is not None and interaction.scenario.rules is not None
    assert dict(interaction.scenario.rules.house_rules) == chosen


@pytest.mark.parametrize("path", _SCENARIOS, ids=lambda p: p.stem)
def test_each_scenario_file_loads_with_its_all_faithful_map(path, mafia_module):
    from clients.terminal import fightlab

    hr = mafia_module.house_rules
    all_faithful = {rule.id: "faithful" for rule in hr.switchable(hr.CATALOGUE)}
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["house_rules"] == all_faithful
    scenario = fightlab.load_scenario(path)
    assert scenario.rules is not None
    assert dict(scenario.rules.house_rules) == all_faithful


def _scenario_without_map(tmp_path: Path, **changes) -> Path:
    raw = yaml.safe_load((_CONFIG_DIR / "content" / "scenarios" / "kdh_ambush.yaml").read_text())
    del raw["house_rules"]
    raw.update(changes)
    path = tmp_path / "scenario.yaml"
    _write_yaml(path, raw)
    return path


def test_a_scenario_file_with_no_map_is_refused(tmp_path):
    from clients.terminal import fightlab

    with pytest.raises(ValueError) as exc:
        fightlab.load_scenario(_scenario_without_map(tmp_path))
    assert str(exc.value) == "scenario 'scenario.yaml': stores no house-rules map"


def test_a_scenario_file_whose_map_differs_from_the_catalogue_is_refused(tmp_path):
    from clients.terminal import fightlab

    path = _scenario_without_map(tmp_path, house_rules={"alpha": "intent"})
    with pytest.raises(ValueError) as exc:
        fightlab.load_scenario(path)
    assert str(exc.value) == (
        "scenario 'scenario.yaml': the catalogue offers no switch for house rule 'alpha'"
    )


def test_the_fight_lab_refuses_a_scenario_with_no_map_in_one_line(capsys, tmp_path):
    from clients.terminal import fightlab

    path = _scenario_without_map(tmp_path)
    with pytest.raises(SystemExit) as exc:
        fightlab.main(["play", "--scenario", str(path)])
    assert exc.value.code == 1
    assert capsys.readouterr().err == "scenario 'scenario.yaml': stores no house-rules map\n"


def test_the_fight_lab_refuses_a_recording_made_under_another_map_in_one_line(capsys, tmp_path):
    """The fight lab watches under this config's all-faithful map; a recording made
    under other choices is refused in one line naming the entry that differs."""
    from dataclasses import replace as dc_replace

    from clients.terminal import fightlab
    from data.game_configs.mafia_1920s.combat_rules import build_rules
    from engine.fight_loop import AiDriver
    from engine.recording import record_fight, save

    scenario = fightlab.load_scenario(_SCENARIOS[0])
    scenario = dc_replace(scenario, rules=build_rules({"alpha": "intent"}))
    _, recording = record_fight(scenario, {1: AiDriver(), 2: AiDriver()})
    path = tmp_path / "other.json"
    save(recording, path)

    with pytest.raises(SystemExit) as exc:
        fightlab.main(["watch", "--recording", str(path)])
    assert exc.value.code == 1
    assert capsys.readouterr().err == (
        "house rule 'alpha' differs: the recording was made with 'intent', "
        "the supplied rules have it unset\n"
    )


# --------------------------------------------------------------------------- #
# U11: the real catalogue                                                     #
# --------------------------------------------------------------------------- #
def test_the_code_reads_exactly_the_catalogues_switches(mafia_module):
    """Every switch is read by the code that plays it, and the code reads no other."""
    from data.game_configs.mafia_1920s.combat_rules import SHARED_DIRECTION_MEMORY
    from data.game_configs.mafia_1920s.setup import INTELLIGENCE_OR_30

    rules = mafia_module.house_rules
    assert {rule.id for rule in rules.switchable(rules.CATALOGUE)} == {
        INTELLIGENCE_OR_30,
        SHARED_DIRECTION_MEMORY,
    }


#: Each faithful-only entry's faithful behaviour, held by the test named here.
_FAITHFUL_ONLY_TESTS = {
    "collectors_win_keeps_debt": (
        "tests.test_debt_default",
        "test_win_changes_nothing_and_the_fight_recurs_next_turn",
    ),
    "croupier_completion_score_zero": (
        "tests.test_pub_jobs",
        "test_croupier_completion_score_is_zero",
    ),
    "eviction_keeps_the_room": (
        "tests.test_rent_countdown",
        "test_eviction_at_zero_cash_leaves_only_the_boss",
    ),
    "recruit_cap_checked_after_the_offer": (
        "tests.test_pub_recruit",
        "test_ninth_hire_succeeds_tenth_is_offered_then_denied_mid_batch",
    ),
    "direction_memory_seeded_left": (
        "tests.test_combat_ai",
        "test_the_minus_one_seed_blocks_a_rightward_first_step",
    ),
    "passport_rebuy_charged": (
        "tests.test_ble",
        "test_passport_bought_again_while_held_is_charged_again",
    ),
}


def test_every_faithful_only_entry_has_a_test_of_its_faithful_behaviour(mafia_module):
    import importlib

    catalogue = mafia_module.house_rules.CATALOGUE
    assert set(_FAITHFUL_ONLY_TESTS) == {rule.id for rule in catalogue if not rule.switch}
    for rule_id, (module, name) in _FAITHFUL_ONLY_TESTS.items():
        assert callable(getattr(importlib.import_module(module), name, None)), rule_id


def _first_gangster(seed: int, setting: str):
    from data.game_configs.mafia_1920s.setup import new_game

    state = new_game(
        seed=seed,
        end_year=1930,
        score_weight=1.0,
        players=[("a", "b")],
        house_rules={"intelligence_or_30": setting},
    )
    return state.players[0]


def _intelligence_roll(seed: int) -> int:
    """The second setup draw (:311, after :310's kraft) as :350 rolls and prints it."""
    from engine.rng import Rng

    rng = Rng(seed)
    rng.range(9)  # :310 kraft
    return rng.range(9) * 5 + 10  # :350 ``x=int(rnd(1)*9)*5+10``


_SEEDS = range(12)


def test_intelligence_faithful_is_the_roll_or_30():
    # :311 ``in=xor30`` -- the roll OR 30 (bitwise): 30, 31, 62 or 63.
    values = [_first_gangster(seed, "faithful").roster[0].attrs["intelligenz"] for seed in _SEEDS]
    assert values == [_intelligence_roll(seed) | 30 for seed in _SEEDS]
    assert set(values) <= {30, 31, 62, 63}
    assert any(value != _intelligence_roll(seed) for seed, value in zip(_SEEDS, values))


def test_intelligence_intent_is_the_roll_the_setup_screen_prints():
    for seed in _SEEDS:
        intended = _first_gangster(seed, "intent")
        faithful = _first_gangster(seed, "faithful")
        assert intended.roster[0].attrs["intelligenz"] == _intelligence_roll(seed)
        # The same draws: only the stored intelligence differs.
        assert intended.ka == faithful.ka
        for stat in ("kraft", "brutalitaet"):
            assert intended.roster[0].attrs[stat] == faithful.roster[0].attrs[stat]
    assert {_first_gangster(s, "intent").roster[0].attrs["intelligenz"] for s in _SEEDS} - {
        30,
        31,
        62,
        63,
    }, "every intended roll is also a faithful value: the test is vacuous"


def _second_side_decision(setting: str):
    """Both sides AI-driven: side 1's fighter 1 steps right, then side 2's fighter 1
    (its target to the left, on its row) decides its move."""
    from data.game_configs.mafia_1920s.combat_rules import build_rules
    from tests.helpers import build_fight, combat_fighter

    rules = build_rules({"intelligence_or_30": "faithful", "shared_direction_memory": setting})
    fight = build_fight(
        side1=(combat_fighter(name="a", weapon=1, energie=20, position=255),),
        side2=(combat_fighter(name="b", weapon=1, energie=20, position=262),),
        dir_memory={0: -1},  # :30020 ``ri(i)=-1`` for the side-2 fighter
        rules=rules,
        active=(1, 1),
    )
    # :30492 ``ri(f)=p`` -- side 1's AI-driven step is recorded.
    assert fight.apply_action("move", 1, record_dir_memory=True) is True
    fight.advance_activation()
    assert (fight.active_side, fight.active_fighter) == (2, 1)
    return fight.ai_decide(fight.view())


def test_shared_direction_memory_faithful_one_memory_per_fighter_number():
    # Side 1's step right is side 2's fighter 1's memory too: its approach left is the
    # reverse of it (:30450), so it sidesteps right (:30456) instead.
    assert _second_side_decision("faithful") == ("move", 1)


def test_shared_direction_memory_intent_each_side_its_own():
    # Side 2's fighter 1 remembers only its own seed: it approaches left (:30450).
    assert _second_side_decision("intent") == ("move", -1)

"""Content is read once per config load: a running game never reads it from disk again.

The config's entity tables (ranks, vehicles, weapons, gangsters), its combat backdrops
and its ``config.yaml`` are module constants built when the config package imports, as
its encounters are. The package executes again on every ``load_game_config``, so an
edit to a content file reaches a game only through a new load (AE3), a malformed
content file is refused at load, and a game played through ``play()`` reads no content
file once it is running (R5). The path-taking ``load_*`` functions and
``new_game(config_path=...)`` still read the file they are given.
"""

from __future__ import annotations

import builtins
import functools
import io
import shutil
from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.interactions import RollFrame, run
from engine.locations import HANDLERS
from engine.turns import MOVEMENT_POINTS_HOOK_KEY, SETUP_HANDLER_KEY, UPKEEP_SCREEN
from engine.types import ConfigValidationError
from engine.rng import Rng
from tests import test_full_game_e2e as e2e

CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"


def _copy(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "mafia_1920s"
    shutil.copytree(CONFIG_DIR, cfg_dir, ignore=shutil.ignore_patterns("__pycache__"))
    return cfg_dir


def _movement_points(hook, state) -> int:
    return run(hook, lambda _: None, state=state).payload.returned


def _new_game(module):
    return module.new_game(
        seed=1, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )


# --- AE3: an edit reaches a game only through a new load ------------------------------


def test_an_edited_vehicle_table_changes_the_movement_points_only_after_a_reload(tmp_path):
    cfg_dir = _copy(tmp_path)
    loaded = load_game_config(cfg_dir)
    hook = loaded.handlers[MOVEMENT_POINTS_HOOK_KEY]
    state = _new_game(loaded.module)
    assert _movement_points(hook, state) == 25  # on foot, tr(0)

    vehicles_yaml = cfg_dir / "entities" / "vehicles.yaml"
    table = yaml.safe_load(vehicles_yaml.read_text(encoding="utf-8"))
    table["vehicles"][0]["tr"] = 999
    vehicles_yaml.write_text(yaml.safe_dump(table), encoding="utf-8")
    assert _movement_points(hook, state) == 25, "the loaded game kept its vehicle table"

    reloaded = load_game_config(cfg_dir)
    assert _movement_points(reloaded.handlers[MOVEMENT_POINTS_HOOK_KEY], state) == 999


# --- a malformed content file is refused at load ---------------------------------------


@pytest.mark.parametrize(
    "content_file",
    [
        "entities/vehicles.yaml",
        "entities/ranks.yaml",
        "entities/gangsters.yaml",
        "content/combat/kg.yaml",
    ],
)
def test_a_malformed_content_file_fails_at_config_load(tmp_path, content_file):
    cfg_dir = _copy(tmp_path)
    (cfg_dir / content_file).write_text("cells: [1, 2\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_game_config(cfg_dir)


def test_a_vehicle_the_engine_refuses_fails_at_config_load(tmp_path):
    cfg_dir = _copy(tmp_path)
    vehicles_yaml = cfg_dir / "entities" / "vehicles.yaml"
    table = yaml.safe_load(vehicles_yaml.read_text(encoding="utf-8"))
    del table["vehicles"][0]["tr"]
    vehicles_yaml.write_text(yaml.safe_dump(table), encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="tr"):
        load_game_config(cfg_dir)


# --- R5: no content read once a game runs ----------------------------------------------


class ReadSpy:
    """Records every file the process opens while armed; :meth:`content_reads` keeps the
    config's content files (``entities/``, ``content/``, ``config.yaml``)."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir.resolve()
        self.armed = False
        self.paths: list[Path] = []

    def install(self, monkeypatch) -> None:
        spy = self

        def opener(original):
            @functools.wraps(original)
            def spied(file, *args, **kwargs):
                spy.saw(file)
                return original(file, *args, **kwargs)

            return spied

        def path_reader(original):
            @functools.wraps(original)
            def spied(self, *args, **kwargs):
                spy.saw(self)
                return original(self, *args, **kwargs)

            return spied

        monkeypatch.setattr(builtins, "open", opener(builtins.open))
        monkeypatch.setattr(io, "open", opener(io.open))
        for name in ("open", "read_text", "read_bytes"):
            monkeypatch.setattr(Path, name, path_reader(getattr(Path, name)))

    def saw(self, file) -> None:
        if self.armed and isinstance(file, (str, Path)):
            self.paths.append(Path(file))

    def content_reads(self) -> list[Path]:
        reads = []
        for path in self.paths:
            try:
                rel = path.resolve().relative_to(self.config_dir)
            except ValueError:
                continue
            if rel.parts[:1] in (("entities",), ("content",)) or rel == Path("config.yaml"):
                reads.append(rel)
        return reads


class _SpiedEarlyWinPlayer(e2e.EarlyWinPlayer):
    """The early-win run's player, arming the spy when the first screen is drawn."""

    def __init__(self, out, *, budget: int, spy: ReadSpy) -> None:
        super().__init__(out, budget=budget)
        self.spy = spy

    def observe(self, screen, state) -> None:
        self.spy.armed = True
        super().observe(screen, state)


def test_a_game_played_through_play_reads_no_content_file(monkeypatch, tmp_path):
    """The early-win run buys tips at the pub, fights both win flows on the board and
    ends turns into upkeep; from its first screen on, no content file is opened."""
    spy = ReadSpy(CONFIG_DIR)
    spy.install(monkeypatch)
    player = _SpiedEarlyWinPlayer(io.StringIO(), budget=6000, spy=spy)
    e2e._resume(monkeypatch, tmp_path, e2e._early_win_start(42), player, seed=42)

    reads = spy.content_reads()
    assert player.tips_bought, "a purchase"
    assert player.combat_activations > 0, "a fight"
    assert player.screens.count(UPKEEP_SCREEN) >= 2, "a turn end into the next upkeep"
    assert reads == []
    # The armed spy sees a content read.
    (CONFIG_DIR / "entities" / "vehicles.yaml").read_text(encoding="utf-8")
    assert set(spy.content_reads()) == {Path("entities/vehicles.yaml")}


def test_the_setup_handler_and_new_game_read_no_content_file(monkeypatch):
    """What the session runs to start a game (the setup handler, then
    ``new_game(record)``) reads the config's content from its import-time constants."""
    module = load_game_config(CONFIG_DIR).module
    spy = ReadSpy(CONFIG_DIR)
    spy.install(monkeypatch)
    spy.armed = True
    handler = functools.partial(
        HANDLERS[SETUP_HANDLER_KEY],
        end_year=1930,
        score_weight=1.0,
        house_rules={},
        players=[("alcapone", "the outfit")],
    )
    record = run(
        handler, lambda i: True if isinstance(i, RollFrame) else None, state=None, rng=Rng(1)
    ).payload.returned
    module.new_game(record)
    _new_game(module)

    assert spy.content_reads() == []


def test_new_game_given_another_config_path_reads_that_config(monkeypatch, tmp_path):
    cfg_dir = _copy(tmp_path)
    module = load_game_config(CONFIG_DIR).module
    spy = ReadSpy(cfg_dir)
    spy.install(monkeypatch)
    spy.armed = True
    module.new_game(
        seed=1,
        end_year=1930,
        score_weight=1.0,
        players=[("alcapone", "the outfit")],
        config_path=cfg_dir / "config.yaml",
    )

    assert Path("config.yaml") in spy.content_reads()

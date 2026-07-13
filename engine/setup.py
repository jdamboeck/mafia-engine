"""New-game setup routine + config assembly for the ``mafia_1920s`` config (U8).

Ports the BASIC new-game setup (``mf-prg.bas:220,300-315,350``): it rolls each
player's starting gangster stats and cash, seats them at the start position on
foot, and assembles a :class:`~engine.state.GameState`.

Determinism contract
---------------------
**Every** setup random draw flows through an :class:`~engine.rng.Rng` instance
(never the stdlib ``random`` directly). So the same seed reproduces an identical
game and different seeds diverge — which the tests prove end-to-end.

This module also provides the tiny Engine<->Config loaders (``load_config``,
``load_vehicles``, ``load_ranks``) and the ``fnm`` rent-formula helper (whose
params live in ``config.yaml`` and which U7's slw handler reuses).

Layering: ``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.rng import Rng
from engine.state import Clock, Config, Gangster, GameState, Player

__all__ = [
    "new_game",
    "load_config",
    "load_vehicles",
    "load_ranks",
    "fnm",
]

# Default config location, relative to the repo root (engine/../data/...).
_DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "game_configs"
    / "mafia_1920s"
    / "config.yaml"
)

ENGINE_API = 1


# --- config loaders --------------------------------------------------------


def load_config(path: str | Path = _DEFAULT_CONFIG) -> dict:
    """Load and validate a game ``config.yaml``.

    Enforces the Engine<->Config contract (PLAN.md §6a): the config MUST declare
    ``engine_api: 1``. Any other version — or a missing key — raises ``ValueError``.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    api = data.get("engine_api")
    if api is None:
        raise ValueError(
            f"{path}: config declares no 'engine_api'; this engine requires "
            f"engine_api == {ENGINE_API}."
        )
    if api != ENGINE_API:
        raise ValueError(
            f"{path}: unsupported engine_api {api!r}; this engine only accepts "
            f"engine_api == {ENGINE_API}."
        )
    return data


def _load_yaml(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_vehicles(path: str | Path) -> list[dict]:
    """Load the vehicle table (list of ``{name, tank, tr}``), index == vehicle index."""
    return list(_load_yaml(path)["vehicles"])


def load_ranks(path: str | Path) -> list[str]:
    """Load the rank names as a 0-based list (index i == in-game rank i+1)."""
    return list(_load_yaml(path)["ranks"])


# --- fnm rent formula ------------------------------------------------------


def fnm(ln: int, params: dict) -> int:
    """Per-tile rent for tile index ``ln`` (mf-prg.bas:115).

    ``params`` is the ``formula_params.fnm`` block from ``config.yaml``:
    a ``base`` plus a per-``ln`` ``overrides`` map. Returns ``overrides[ln]`` if
    present, else ``base``. This reproduces the original's three-way result,
    including the ln=1 negative-rent quirk (fnm(1) == -50). U7's slw handler
    reuses this helper.
    """
    overrides = params.get("overrides", {}) or {}
    # YAML maps int keys fine, but tolerate str keys defensively.
    if ln in overrides:
        return overrides[ln]
    if str(ln) in overrides:
        return overrides[str(ln)]
    return params["base"]


# --- new-game setup --------------------------------------------------------


def _roll_stat(rng: Rng, roll: dict) -> int:
    """Stat/cash roll: rng.range(choices)*step + base (mf-prg.bas:350/315)."""
    return rng.range(roll["choices"]) * roll["step"] + roll["base"]


def new_game(
    *,
    seed: int,
    end_year: int,
    score_weight: float,
    players: list[tuple[str, str]],
    config_path: str | Path = _DEFAULT_CONFIG,
) -> GameState:
    """Build a fresh :class:`GameState` — the ported BASIC new-game setup.

    Parameters
    ----------
    seed:
        Seed for the single :class:`Rng` driving every setup draw.
    end_year:
        ``x9`` game-end year, validated to the config's range (default 1928-1978).
    score_weight:
        ``x8`` score-gain weight, validated (default 0.1-2.0).
    players:
        One ``(name, gang_name)`` per player; 1..4 players. Each player's single
        starting gangster is named after the player.
    config_path:
        Which ``config.yaml`` to assemble from.

    The setup inputs (year/weight/name/gang/count) are taken as parameters, not
    prompted — interactive prompting is the driver/client's job later.
    """
    cfg = load_config(config_path)
    cfg_dir = Path(config_path).resolve().parent
    setup = cfg["setup"]
    ranges = cfg["input_ranges"]

    # --- validate inputs (setup owns range validation) ---------------------
    yr = ranges["end_year"]
    if not (yr["min"] <= end_year <= yr["max"]):
        raise ValueError(
            f"end_year must be in [{yr['min']}, {yr['max']}], got {end_year}"
        )
    sw = ranges["score_weight"]
    if not (sw["min"] <= score_weight <= sw["max"]):
        raise ValueError(
            f"score_weight must be in [{sw['min']}, {sw['max']}], got {score_weight}"
        )
    pc = ranges["player_count"]
    if not (pc["min"] <= len(players) <= pc["max"]):
        raise ValueError(
            f"player count must be in [{pc['min']}, {pc['max']}], got {len(players)}"
        )

    # --- entity tables -----------------------------------------------------
    vehicles = load_vehicles(cfg_dir / cfg["entities"]["vehicles"])
    start_vehicle = setup["start_vehicle"]
    start_ms = vehicles[start_vehicle]["tr"]  # ms = tr(vehicle); on foot tr(0)=25

    rng = Rng(seed)

    game_players: list[Player] = []
    for name, gang_name in players:
        # Roll the starting gangster's stats — ALL via rng.
        kraft = _roll_stat(rng, setup["stat_roll"])
        raw_intel = _roll_stat(rng, setup["stat_roll"])
        intelligenz = raw_intel | setup["intelligenz_or"]  # OR-30 quirk
        brutalitaet = _roll_stat(rng, setup["stat_roll"])
        cash = _roll_stat(rng, setup["cash_roll"])

        gangster = Gangster(
            name=name,
            weapon=0,
            energie=setup["start_energy"],  # fixed 5, not rolled
            kraft=kraft,
            intelligenz=intelligenz,
            brutalitaet=brutalitaet,
        )
        game_players.append(
            Player(
                name=name,
                gang_name=gang_name,
                ka=cash,
                rank=setup["start_rank"],
                nr=setup["start_nr"],
                po=setup["start_position"],
                vehicle=start_vehicle,
                ms=start_ms,
                roster=[gangster],
            )
        )

    clock = Clock(
        year=ranges["end_year"]["min"],  # game starts at the floor year (1928)
        end_year=end_year,
        active_player=0,
        player_count=len(players),
    )
    config = Config(
        score_mult=score_weight,
        formula_params=cfg["formula_params"],
    )

    return GameState(players=game_players, clock=clock, config=config)

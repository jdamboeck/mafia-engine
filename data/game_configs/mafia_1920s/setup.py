"""New-game setup routine + entity loaders for the ``mafia_1920s`` config (U8).

Ports the BASIC new-game setup (``mf-prg.bas:220,300-315,350``): it rolls each
player's starting gangster stats and cash, seats them at the start position on
foot, and assembles a :class:`~engine.state.GameState`.

This is **config-owned game code** (docs/design/product-and-scope.md and docs/design/config-and-content-contract.md): it lives with the config so a
new game = copy this directory. It imports engine *APIs* (``engine.rng.Rng``,
``engine.state.*``, and the engine's generic ``load_config``) — those are the
engine's public surface; the config depends on the engine, never the reverse.

Determinism contract
---------------------
**Every** setup random draw flows through an :class:`~engine.rng.Rng` instance
(never the stdlib ``random`` directly). So the same seed reproduces an identical
game and different seeds diverge — which the tests prove end-to-end.

This module also provides the ``fnm`` rent-formula helper (whose params live in
``config.yaml``) and the ``load_vehicles``/``load_ranks`` entity loaders. The slw
handler imports ``fnm`` from HERE (its own config), not from the engine.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_config
from engine.effects import ScoreAndRank
from engine.interactions import ShowMessage
from engine.rng import Rng
from engine.state import Clock, Config, Gangster, GameState, Player
from engine.types import validate_rank, validate_vehicle, validate_weapon

__all__ = [
    "new_game",
    "load_vehicles",
    "load_ranks",
    "load_weapons",
    "load_gangster_candidates",
    "load_combat_backdrop",
    "weapon_stats_by_id",
    "fnm",
    "score_and_rank",
    "narrate_combat_outcome",
]

# Default config location: this config's own directory.
_DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"


# --- entity loaders --------------------------------------------------------


def _load_yaml(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_vehicles(path: str | Path) -> list[dict]:
    """Load the vehicle table (list of ``{name, tank, tr}``), index == vehicle index.

    Each entry is validated against the engine's :class:`~engine.types.VehicleInstance`
    contract (:func:`~engine.types.validate_vehicle`) — the engine checks the config's
    entity data adheres to the required types at load time.
    """
    vehicles = list(_load_yaml(path)["vehicles"])
    for i, v in enumerate(vehicles):
        validate_vehicle(v, index=i)
    return vehicles


def load_ranks(path: str | Path) -> list[str]:
    """Load the rank names as a 0-based list (index i == in-game rank i+1).

    Each entry is validated against the engine's :func:`~engine.types.validate_rank`
    contract at load time.
    """
    ranks = list(_load_yaml(path)["ranks"])
    for i, r in enumerate(ranks):
        validate_rank(r, index=i)
    return ranks


def load_weapons(path: str | Path) -> list[dict]:
    """Load the weapon table (list of ``{name, price, ts, tg, ws, req_*}``), index == weapon index.

    Ports the DATA table (``mf-prg.bas:50100-50115``); ``req_int``/``req_kraft``/
    ``req_brut`` are the per-weapon stat minimums derived from the buy-guard lines
    (``13050-13060``). Each entry is validated against the engine's
    :func:`~engine.types.validate_weapon` contract at load time. Returned 0-based
    (``weapons[i]`` == in-game weapon index ``i``, 0..8).
    """
    weapons = list(_load_yaml(path)["weapons"])
    for i, w in enumerate(weapons):
        validate_weapon(w, index=i)
    return weapons


def load_gangster_candidates(path: str | Path) -> list[dict]:
    """Load the 30 recruitable-gangster candidates (U9, ``../research/.../gan-extraction.yaml``).

    Each entry is ``{name, weapon, kraft, intelligenz, brutalitaet, price,
    description, female}``, list index 0-based == the original's 1-based candidate
    file number ``g(i)`` minus one (``pub.recruit`` re-adds the offset when it needs
    the 1-based id for the "already hired" tracking set). No engine-side validator
    exists for this shape (unlike weapons/vehicles/ranks) — the recruit flow is this
    config's own concern end-to-end, so the loader stays a plain YAML read.
    """
    return list(_load_yaml(path)["gangsters"])


def load_combat_backdrop(path: str | Path) -> tuple[int, ...]:
    """Load a combat backdrop's linear wall/scenery code array (U4).

    ``path`` points at one of ``content/combat/{ks,kp,km}.yaml`` — the pre-decoded
    (``tools/decode_combat_backdrops.py``) 521-entry ``cells`` array covering the
    combat grid's kept 0..520 bound (``engine.combat.CELL_COUNT``). Returned as a
    plain tuple of ints, ready to pass straight to ``engine.combat.setup_combat``'s
    ``grid`` parameter — engine combat code owns interpreting the codes (walls vs.
    empty vs. scenery), this loader only supplies the raw array.
    """
    cells = _load_yaml(path)["cells"]
    return tuple(cells)


def weapon_stats_by_id(path: str | Path) -> dict[int, tuple[int, int]]:
    """This config's weapon id -> ``(ts, tg)`` table, for ``StartCombat.weapon_stats``.

    ``path`` points at ``entities/weapons.yaml``. Shared by every combat-starting
    handler (``jobs.py``, ``upkeep.py``, ``kdh.py``) — each needs the same
    ``{id: (ts, tg)}`` shape derived from :func:`load_weapons`, so the derivation
    lives here once rather than as a private per-handler copy.
    """
    weapons = load_weapons(path)
    return {i: (w["ts"], w["tg"]) for i, w in enumerate(weapons)}


# --- fnm rent formula ------------------------------------------------------


def fnm(ln: int, params: dict) -> int:
    """Per-tile rent for tile index ``ln`` (mf-prg.bas:115).

    ``params`` is the ``formula_params.fnm`` block from ``config.yaml``:
    a ``base`` plus a per-``ln`` ``overrides`` map. Returns ``overrides[ln]`` if
    present, else ``base``. This reproduces the original's three-way result,
    including the ln=1 negative-rent quirk (fnm(1) == -50). The slw handler
    reuses this helper.
    """
    overrides = params.get("overrides", {}) or {}
    # YAML maps int keys fine, but tolerate str keys defensively.
    if ln in overrides:
        return overrides[ln]
    if str(ln) in overrides:
        return overrides[str(ln)]
    return params["base"]


# --- score / rank helper ---------------------------------------------------


def score_and_rank(x: float, params: dict) -> ScoreAndRank:
    """Build the :class:`ScoreAndRank` effect for reward ``x`` — ports ``gosub 1160/1165``.

    ``params`` is the config's ``formula_params`` block; the ``rank_divisor`` (11.1) is
    read from it and passed into the effect (KTD-10 — the engine hardcodes no game
    number). ``x`` is the raw reward (1 for range training, 2 for camp, or a buy-score
    delta); the effect weights it by ``Config.score_mult`` (``x8``) at apply time.
    Every score-awarding waf path routes through this helper so rank never drifts from
    the original (KTD-5).
    """
    return ScoreAndRank(amount=x, rank_divisor=params["rank_divisor"])


# --- combat-outcome narration -----------------------------------------------


def narrate_combat_outcome(
    *, winner: int, player_name: str, enemy_name: str, with_losses: bool = True
):
    """Yield the post-fight outcome screen shared by ``jobs.py``, ``kdh.py``, and
    ``upkeep.py`` (KTD-1: narrating the outcome is the invoking handler's job —
    ``_run_combat`` itself yields no final screen).

    ``winner`` is the side returned by ``StartCombat`` (1 == the acting player's
    roster, 2 == the scripted enemy, per ``engine.interactions._run_combat``).

    The original prints this screen at ``:30500-30515`` UNCONDITIONALLY, for every
    fight, win or lose — ``:30106``'s ``goto30500`` is the single exit from the
    combat engine, and ``:5010``'s ``goto30000`` is the single entry every caller
    uses. There is no caller-specific and no win-conditional branch.

    ``with_losses=False`` is therefore NOT a fidelity exception — it is a known
    deviation, see #50. It exists only because ``upkeep.py``'s debt-collectors
    fight is the one in-slice fight with more than one enemy (``gz(0)=5``,
    ``:4355``), and the per-side counts below are a 1v1 shortcut that would print
    a confidently wrong number there.

    ``count``: the original tracks REAL per-side death tallies in ``v(1)``/``v(2)``
    (zeroed at ``:30100``, incremented at ``:30310`` per death). ``0 if winner
    else 1`` reproduces that exactly for a one-fighter-per-side fight, which every
    call site except the collectors currently is. Restoring the collectors' losses
    block needs real tallies from ``_run_combat``, not this shortcut.
    """
    winner_name = player_name if winner == 1 else enemy_name
    yield ShowMessage("combat.winner_banner", {"name": winner_name})
    if not with_losses:
        return
    yield ShowMessage("combat.losses_heading")
    for side in (1, 2):
        count = 0 if side == winner else 1
        name = player_name if side == 1 else enemy_name
        yield ShowMessage("combat.losses_line", {"name": name, "count": count})


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
                roster=(gangster,),
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
        # Passed as plain YAML dicts: Config deep-freezes them on construction (R2/KTD-2).
        formula_params=cfg["formula_params"],
        action_costs=cfg.get("action_costs", {}),
    )

    return GameState(players=tuple(game_players), clock=clock, config=config)

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

from dataclasses import dataclass
from pathlib import Path

import yaml

from engine.config_loader import load_config
from engine.effects import DebtClear, MoneyChange, ScoreAndRank
from engine.interactions import ShowMessage
from engine.rng import Rng
from engine.state import Clock, Config, GameState, Player

try:
    from .gangster import Gangster
except ImportError:  # loaded bare (config dir on sys.path), not as a package
    from gangster import Gangster
from engine.types import validate_rank, validate_vehicle, validate_weapon

__all__ = [
    "new_game",
    "load_vehicles",
    "load_ranks",
    "load_weapons",
    "load_gangster_candidates",
    "load_combat_backdrop",
    "weapon_stats_by_id",
    "load_encounter",
    "EnemySpec",
    "Encounter",
    "apply_outcome",
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


def weapon_stats_by_id(path: str | Path) -> dict[int, tuple[int, int, int]]:
    """This config's weapon id -> ``(ts, tg, range)`` table, for ``StartCombat.weapon_stats``.

    ``path`` points at ``entities/weapons.yaml``. Shared by every combat-starting
    handler (``jobs.py``, ``upkeep.py``, ``kdh.py``) — each needs the same
    ``{id: (ts, tg, range)}`` shape derived from :func:`load_weapons`, so the
    derivation lives here once rather than as a private per-handler copy.

    ``range`` travels this path with ``ts``/``tg`` because it is entity data like
    them: the engine holds no weapon taxonomy of its own, so a weapon's reach — and
    with it whether the AI treats it as melee — has to arrive from the config.
    """
    weapons = load_weapons(path)
    return {i: (w["ts"], w["tg"], w["range"]) for i, w in enumerate(weapons)}


# --- encounter declarations (U6a) ------------------------------------------
#
# An encounter file declares ONE fight as pure data: the enemy party's setup
# (count/weapon/vitality/name), the backdrop, and — optionally — a DECLARABLE
# consequence (``on_win``/``on_loss``). The setup half is a regrouping of data
# that already lived in ``config.yaml`` (kdh_ambush_*/kdh_collectors_*) or as
# Python literals in the handlers (the three job opponents). ``Scenario.from_
# encounter`` reads the parsed :class:`EnemySpec` and produces the same fight the
# handler used to assemble inline.
#
# Validation posture mirrors the guard DSL (engine/conditions.py) and the
# entity loaders above: an unknown outcome key, an unknown/missing grid, or an
# unknown weapon id fails AT LOAD TIME, not at fight time. Loading is where the
# config's shape is proven; a fight should never be the first thing to notice a
# typo.

#: The declarable outcome-consequence vocabulary — the same restrained list the
#: guard DSL is (seven operators, nothing more). Any other key in an ``on_win``/
#: ``on_loss`` step is rejected at load. ``apply_outcome`` (in the handlers'
#: shared helper) is the sole interpreter.
_OUTCOME_KEYS = frozenset({"money", "score", "message", "clear"})

#: The named no-argument effects a ``clear:`` step may name. Only ``debt`` today
#: (mapping to :class:`~engine.effects.DebtClear`); listing them here keeps an
#: unknown ``clear:`` target a LOAD-time failure, not a fight-time one.
_CLEAR_TARGETS = frozenset({"debt"})


@dataclass(frozen=True)
class EnemySpec:
    """One enemy party's fight setup — exactly the four fields ``Scenario.from_
    encounter`` reads (``count``/``weapon``/``vitality``/``name``).

    A single-enemy encounter has one of these; the bouncer's ``variants`` list is
    three of them. The 30/30 CPU ``attrs`` are NOT here — they are this game's
    fixed enemy stats (``enemy_attrs``), supplied by the handler at the call site,
    not part of the per-fight declaration.
    """

    count: int
    weapon: int
    vitality: int
    name: str


@dataclass(frozen=True)
class Encounter:
    """A parsed encounter declaration (U6a).

    ``variants`` always holds at least one :class:`EnemySpec` — a single-enemy
    encounter is a one-variant list, the bouncer is a three-variant list. The
    caller picks a variant (the bouncer with its ``ctx.rng.range(3)`` roll, the
    others with variant 0) and hands it to ``Scenario.from_encounter``. ``grid``
    is the backdrop name (``ks``/``kp``/``km``), already validated to exist.
    ``on_win``/``on_loss`` are the declarable consequence steps (``None`` when the
    file omits them — a supported shape: the handler keeps the consequence in
    Python). Every step's key was validated against :data:`_OUTCOME_KEYS` at load.
    """

    key: str
    grid: str
    variants: tuple[EnemySpec, ...]
    on_win: tuple[dict, ...] | None = None
    on_loss: tuple[dict, ...] | None = None


def _validate_outcome_steps(steps, *, key: str, block: str) -> tuple[dict, ...] | None:
    """Validate one ``on_win``/``on_loss`` block; raise on an unknown key at LOAD."""
    if steps is None:
        return None
    out: list[dict] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError(
                f"encounter {key!r}: {block}[{i}] must be a single-key mapping, got {step!r}"
            )
        (name,) = step
        if name not in _OUTCOME_KEYS:
            raise ValueError(
                f"encounter {key!r}: {block}[{i}] has unknown outcome key {name!r} "
                f"(known: {sorted(_OUTCOME_KEYS)})"
            )
        if name == "clear" and step["clear"] not in _CLEAR_TARGETS:
            raise ValueError(
                f"encounter {key!r}: {block}[{i}] clears unknown target "
                f"{step['clear']!r} (known: {sorted(_CLEAR_TARGETS)})"
            )
        out.append(dict(step))
    return tuple(out)


def load_encounter(path: str | Path, *, config_dir: str | Path | None = None) -> Encounter:
    """Load and VALIDATE one encounter declaration into an :class:`Encounter` (U6a).

    ``config_dir`` locates the sibling ``content/combat/{grid}.yaml`` backdrops and
    ``entities/weapons.yaml`` (defaults to ``path``'s ``…/content/encounters`` parent's
    parent — this config's root). Validation is done HERE, at load, matching the guard
    DSL's posture (engine/conditions.py) and the entity loaders above:

    * every ``on_win``/``on_loss`` step names a key in :data:`_OUTCOME_KEYS`;
    * ``grid`` names a backdrop file that exists under ``content/combat``;
    * every enemy ``weapon`` id exists in ``entities/weapons.yaml``.

    A fight is never the first place a typo surfaces.
    """
    path = Path(path)
    if config_dir is None:
        # …/content/encounters/<file>.yaml  ->  the config root is two parents up.
        config_dir = path.resolve().parents[2]
    config_dir = Path(config_dir)

    raw = _load_yaml(path)
    key = raw.get("key") or path.stem

    grid = raw.get("grid")
    if not grid:
        raise ValueError(f"encounter {key!r}: missing 'grid'")
    grid_path = config_dir / "content" / "combat" / f"{grid}.yaml"
    if not grid_path.exists():
        raise ValueError(
            f"encounter {key!r}: unknown grid {grid!r} (no {grid_path} — "
            f"backdrops live under content/combat/)"
        )

    # Valid weapon ids: the 0-based index range of entities/weapons.yaml.
    weapons = load_weapons(config_dir / "entities" / "weapons.yaml")
    valid_weapons = range(len(weapons))

    if "variants" in raw:
        raw_specs = list(raw["variants"])
    elif "enemies" in raw:
        raw_specs = [raw["enemies"]]
    else:
        raise ValueError(f"encounter {key!r}: needs either 'enemies' or 'variants'")

    specs: list[EnemySpec] = []
    for i, spec in enumerate(raw_specs):
        weapon = spec["weapon"]
        if weapon not in valid_weapons:
            raise ValueError(
                f"encounter {key!r}: variant {i} names unknown weapon id {weapon!r} "
                f"(known: 0..{len(weapons) - 1})"
            )
        specs.append(
            EnemySpec(
                count=spec["count"],
                weapon=weapon,
                vitality=spec["vitality"],
                name=spec["name"],
            )
        )

    return Encounter(
        key=key,
        grid=grid,
        variants=tuple(specs),
        on_win=_validate_outcome_steps(raw.get("on_win"), key=key, block="on_win"),
        on_loss=_validate_outcome_steps(raw.get("on_loss"), key=key, block="on_loss"),
    )


#: The named no-argument effects a ``clear:`` step applies. Kept beside its
#: load-time validator (:data:`_CLEAR_TARGETS`) so the two cannot drift.
_CLEAR_EFFECTS = {"debt": DebtClear}


def apply_outcome(ctx, encounter: Encounter, result):
    """Apply an encounter's DECLARED consequence for a finished fight (U6a).

    A generator (``yield from apply_outcome(ctx, encounter, result)``) that walks the
    ``on_win``/``on_loss`` block selected by ``result.winner`` (side 1 == the acting
    player, side 2 == the enemy — the ``engine.interactions._run_combat`` convention)
    and applies each step of the restrained consequence vocabulary:

    * ``money: int`` — a flat :class:`~engine.effects.MoneyChange`.
    * ``money: {roll: [min, max]}`` — an inclusive ``ctx.rng.hit(min, max)`` roll,
      then :class:`~engine.effects.MoneyChange` of the drawn amount. The rolled amount
      is also exposed to a following ``message`` as ``{"amount": <amount>}``.
    * ``score: float`` — routes through :func:`score_and_rank` (so rank never drifts).
    * ``message: <theme key>`` — a :class:`~engine.interactions.ShowMessage`. Params are
      the last money roll's ``{"amount": …}`` if one preceded it, else empty.
    * ``clear: <named effect>`` — a no-argument named effect (``debt`` ->
      :class:`~engine.effects.DebtClear`), validated at load.

    This is the WHOLE vocabulary — no state references, no conditionals, no arithmetic
    over live values (that boundary is why the collectors' seizure and the jobs' payouts
    stay in Python; those encounters omit ``on_win``/``on_loss``). An encounter with no
    matching block (``None``) applies nothing — the silent-return shape (kdh loss,
    :15315).
    """
    steps = encounter.on_win if result.winner == 1 else encounter.on_loss
    if not steps:
        return

    params = ctx.state.config.formula_params
    last_amount: int | None = None
    for step in steps:
        (name,) = step
        if name == "money":
            spec = step["money"]
            if isinstance(spec, dict):
                lo, hi = spec["roll"]
                amount = ctx.rng.hit(lo, hi)
            else:
                amount = spec
            last_amount = amount
            ctx.apply(MoneyChange(amount))
        elif name == "score":
            ctx.apply(score_and_rank(step["score"], params))
        elif name == "message":
            msg_params = {"amount": last_amount} if last_amount is not None else {}
            yield ShowMessage(step["message"], msg_params)
        elif name == "clear":
            ctx.apply(_CLEAR_EFFECTS[step["clear"]]())


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
    *, winner: int, player_name: str, enemy_name: str, player_losses: int, enemy_losses: int
):
    """Yield the post-fight outcome screen shared by ``jobs.py``, ``kdh.py``, and
    ``upkeep.py`` (KTD-1: narrating the outcome is the invoking handler's job —
    ``_run_combat`` itself yields no final screen).

    ``winner`` is the side returned by ``StartCombat`` (1 == the acting player's
    roster, 2 == the scripted enemy, per ``engine.interactions._run_combat``).

    The original prints this screen at ``:30500-30515`` UNCONDITIONALLY, for every
    fight, win or lose — ``:30106``'s ``goto30500`` is the single exit from the
    combat engine, and ``:5010``'s ``goto30000`` is the single entry every caller
    uses. There is no caller-specific and no win-conditional branch, so the losses
    block always prints (U3 removed the ``with_losses=False`` deviation, #50).

    ``player_losses``/``enemy_losses`` are the REAL per-side death tallies the fight
    computed (``v(1)``/``v(2)``, zeroed at ``:30100``, incremented at ``:30310`` per
    death), handed back on the :class:`~engine.combat.CombatResult`. They replace the
    old ``0 if side == winner else 1`` shortcut, which was only correct for a
    one-fighter-per-side fight and would print a confidently wrong count for the
    multi-enemy debt-collectors fight (``gz(0)=5``, ``:4355``).
    """
    winner_name = player_name if winner == 1 else enemy_name
    yield ShowMessage("combat.winner_banner", {"name": winner_name})
    yield ShowMessage("combat.losses_heading")
    for name, count in ((player_name, player_losses), (enemy_name, enemy_losses)):
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
        raise ValueError(f"end_year must be in [{yr['min']}, {yr['max']}], got {end_year}")
    sw = ranges["score_weight"]
    if not (sw["min"] <= score_weight <= sw["max"]):
        raise ValueError(f"score_weight must be in [{sw['min']}, {sw['max']}], got {score_weight}")
    pc = ranges["player_count"]
    if not (pc["min"] <= len(players) <= pc["max"]):
        raise ValueError(f"player count must be in [{pc['min']}, {pc['max']}], got {len(players)}")

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

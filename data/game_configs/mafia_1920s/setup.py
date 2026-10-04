"""New-game setup routine + entity loaders for the ``mafia_1920s`` config.

Ports the BASIC new-game setup (``mf-prg.bas:220,300-315,350``): it rolls each
player's starting gangster stats and cash, seats them at the start position on
foot, and assembles a :class:`~engine.state.GameState`. The interactive setup is the
handler in ``handlers/new_game.py``, which returns a :class:`SetupRecord`;
:func:`new_game` builds the state from that record, or rolls the stats itself when it
is given the setup values as keywords.

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

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from engine.combat_setup import SIDE2_ANCHOR, build_player_side, placement_positions
from engine.config_loader import load_config
from engine.effects import MoneyChange
from engine.fight_loop import AiDriver, HumanDriver
from engine.interactions import PromptInt, ShowMessage, StartCombat
from engine.rng import Rng
from engine.scenario import Scenario
from engine.state import FAITHFUL, HOUSE_RULE_SETTINGS, INTENT, Clock, Config, GameState, Player

try:
    from .combat_rules import build_rules, enemy_attrs, equipper
    from .effects import DebtClear, ScoreAndRank
    from .gangster import GANGSTER_ATTR_NAMES, Gangster
    from .house_rules import CATALOGUE_FILE as HOUSE_RULES_FILE
    from .house_rules import load_house_rules, switchable
    from .state import SCHEMA
    from .state import gang_name as _gang_name
except ImportError:  # loaded bare (config dir on sys.path), not as a package
    from combat_rules import build_rules, enemy_attrs, equipper
    from effects import DebtClear, ScoreAndRank
    from gangster import GANGSTER_ATTR_NAMES, Gangster
    from house_rules import CATALOGUE_FILE as HOUSE_RULES_FILE
    from house_rules import load_house_rules, switchable
    from state import SCHEMA
    from state import gang_name as _gang_name
from engine.types import ConfigValidationError, validate_rank, validate_vehicle, validate_weapon

__all__ = [
    "new_game",
    "SetupPlayer",
    "SetupRecord",
    "roll_player",
    "parse_setup_number",
    "in_range",
    "check_end_year",
    "check_score_weight",
    "check_players",
    "name_fits",
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
    "run_encounter",
    "run_gang_fight",
    "roster_after",
    "gangster_line",
    "pick_gangster",
    "fnm",
    "score_and_rank",
    "narrate_combat_outcome",
]

# Default config location: this config's own directory.
_CONFIG_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _CONFIG_DIR / "config.yaml"


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
    """Load the weapon table (list of ``{name, price, ts, tg, range, ws, requires}``).

    Ports the DATA table (``mf-prg.bas:50100-50115``). Each entry is validated against
    the engine's :func:`~engine.types.validate_weapon` contract (the fields the engine
    knows) and against this config's own ``requires`` contract: the per-weapon stat
    minimums derived from the buy-guard lines (``13050-13060``), a map from a declared
    gangster stat (:data:`GANGSTER_ATTR_NAMES`) to an int. The engine names no stat, so
    it cannot check that map; the config does, here. Returned 0-based
    (``weapons[i]`` == in-game weapon index ``i``, 0..8).
    """
    weapons = list(_load_yaml(path)["weapons"])
    for i, w in enumerate(weapons):
        validate_weapon(w, index=i)
        _validate_requires(w, index=i)
    return weapons


def _validate_requires(entry: dict, index: int) -> None:
    """Refuse a weapon whose ``requires`` is not a map of declared stat -> int."""
    where = f"weapon[{index}]"
    requires = entry.get("requires")
    if not isinstance(requires, dict):
        raise ConfigValidationError(
            f"{where} field 'requires' must be a mapping of stat -> minimum, "
            f"got {type(requires).__name__}"
        )
    for stat, minimum in requires.items():
        if stat not in GANGSTER_ATTR_NAMES:
            raise ConfigValidationError(
                f"{where} requires undeclared stat {stat!r}; "
                f"the config declares {list(GANGSTER_ATTR_NAMES)}"
            )
        if type(minimum) is not int:
            raise ConfigValidationError(
                f"{where} requirement {stat!r} must be int, got {type(minimum).__name__}"
            )


def load_gangster_candidates(path: str | Path) -> list[dict]:
    """Load the 30 recruitable-gangster candidates (``../research/.../gan-extraction.yaml``).

    Each entry is ``{name, weapon, kraft, intelligenz, brutalitaet, price,
    description, female}``, list index 0-based == the original's 1-based candidate
    file number ``g(i)`` minus one (``pub.recruit`` re-adds the offset when it needs
    the 1-based id for the "already hired" tracking set). No engine-side validator
    exists for this shape (unlike weapons/vehicles/ranks) — the recruit flow is this
    config's own concern end-to-end, so the loader stays a plain YAML read.
    """
    return list(_load_yaml(path)["gangsters"])


def load_combat_backdrop(path: str | Path) -> tuple[int, ...]:
    """Load a combat backdrop's linear wall/scenery code array.

    ``path`` points at one of ``content/combat/{ks,kp,km}.yaml`` — the pre-decoded
    (``tools/decode_combat_backdrops.py``) 521-entry ``cells`` array covering the
    combat grid's kept 0..520 bound (``engine.combat.CELL_COUNT``). Returned as a
    plain tuple of ints, ready to pass straight to ``engine.combat_setup.setup_combat``'s
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


# --- encounter declarations -------------------------------------------------
#
# An encounter file declares ONE fight as pure data: the enemy party's setup
# (count/weapon/vitality/name), the backdrop, and — optionally — a DECLARABLE
# consequence (``on_win``/``on_loss``). ``Scenario.from_encounter`` reads the
# parsed :class:`EnemySpec` and builds the fight from it.
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
#: (mapping to :class:`DebtClear`); listing them here keeps an
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
    """A parsed encounter declaration.

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
    """Load and VALIDATE one encounter declaration into an :class:`Encounter`.

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
    """Apply an encounter's DECLARED consequence for a finished fight.

    A generator (``yield from apply_outcome(ctx, encounter, result)``) that walks the
    ``on_win``/``on_loss`` block selected by ``result.winner`` (side 1 == the acting
    player, side 2 == the enemy — the ``engine.fight_loop._run_combat`` convention)
    and applies each step of the restrained consequence vocabulary:

    * ``money: int`` — a flat :class:`~engine.effects.MoneyChange`.
    * ``money: {roll: [min, max]}`` — an inclusive ``ctx.rng.hit(min, max)`` roll,
      then :class:`~engine.effects.MoneyChange` of the drawn amount. The rolled amount
      is also exposed to a following ``message`` as ``{"amount": <amount>}``.
    * ``score: float`` — routes through :func:`score_and_rank` (so rank never drifts).
    * ``message: <theme key>`` — a :class:`~engine.interactions.ShowMessage`. Params are
      the last money roll's ``{"amount": …}`` if one preceded it, else empty.
    * ``clear: <named effect>`` — a no-argument named effect (``debt`` ->
      :class:`DebtClear`), validated at load.

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


# --- the fight helper --------------------------------------------------------


def run_encounter(
    ctx,
    encounter: Encounter,
    *,
    variant: int = 0,
    count: int | None = None,
    weapon: int | None = None,
    vitality: int | None = None,
    grid: str | None = None,
    roster=None,
    owner: int | None = None,
):
    """Run one declared fight for the active player and narrate its outcome.

    A generator (``result = yield from run_encounter(ctx, encounter)``) returning the
    fight's :class:`~engine.combat.CombatResult`. It is the one place a handler's fight
    is assembled: ``encounter.variants[variant]`` against the active player's roster,
    on this game's rules bundle (``build_rules`` under the game's house rules), with
    the fixed CPU stats (``enemy_attrs``, ``mf-prg.bas:30245``), the encounter's
    backdrop and this config's weapon table. The outcome screen (``:30500-30515``)
    follows every fight, as the source prints it for every caller; it names the sides
    as the source does, by gang: ``bn$(ks(1))``, the player's gang name, and the
    encounter's ``bn$(0)``.

    Runtime overrides, for a fight whose setup depends on the game:

    * ``count``/``weapon``/``vitality`` replace the variant's ``gz(0)``/``w``/``e``
      (the police roll all three from the rank, ``:26000-26010``);
    * ``grid`` replaces the backdrop: the caller's ``kf$`` when the caller sets it;
    * ``roster`` replaces the player side, e.g. the gang a caller has already cut
      before the fight (the entry record of the police capture). It is a per-fight
      VIEW: the side is built from it, and only each fighter's energy is written
      back, to the gangster its slot names (``roster_id``); a weapon or a gang size
      the view changes stays inside the fight;
    * ``owner`` puts another player's gang on side 1: that player owns it (the fight
      writes its energy back to them), moves it (a
      :class:`~engine.fight_loop.HumanDriver` naming them, side 2 the CPU's as
      ``:30110 ifks(s)=0`` plays it) and the outcome screen names their gang,
      ``bn$(ks(1))``. The prison brawl's jailed player (``:27130`` ``ks(1)=us``).
      Without it side 1 is the active player's.
    """
    active = ctx.state.players[ctx.state.clock.active_player]
    spec = encounter.variants[variant]
    overrides = {
        name: value
        for name, value in (("count", count), ("weapon", weapon), ("vitality", vitality))
        if value is not None
    }
    if overrides:
        spec = replace(spec, **overrides)
    params = ctx.state.config.formula_params
    scenario = Scenario.from_encounter(
        spec,
        active.roster if roster is None else roster,
        build_rules(ctx.state.config.house_rules),
        enemy_attrs=enemy_attrs(params),
        grid=load_combat_backdrop(
            _CONFIG_DIR / "content" / "combat" / f"{grid or encounter.grid}.yaml"
        ),
        equip=equipper(weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")),
        # Side 1's gang is its owner's: its energy is written back to them.
        owner=ctx.state.clock.active_player if owner is None else owner,
    )
    if owner is None:
        result = yield StartCombat(scenario=scenario)
        # :30500-30515 print the gangs, bn$(ks(1)): the active player's gang name.
        player_name = _gang_name(active)
    else:
        result = yield StartCombat(
            scenario=scenario, drivers={1: HumanDriver(player=owner), 2: AiDriver()}
        )
        player_name = _gang_name(ctx.state.players[owner])
    # The outcome screen is the caller's to show (_run_combat yields no final screen);
    # the losses come off the CombatResult, which is right for a many-fighter side.
    yield from narrate_combat_outcome(
        winner=result.winner,
        player_name=player_name,
        enemy_name=spec.name,
        player_losses=result.losses[0],
        enemy_losses=result.losses[1],
    )
    return result


def run_gang_fight(ctx, *, defender: int, attacker: int, grid: str):
    """Run a fight between two players' gangs and narrate its outcome.

    A generator (``result = yield from run_gang_fight(ctx, defender=d, attacker=a,
    grid=g)``) returning the fight's :class:`~engine.combat.CombatResult`; the sibling of
    :func:`run_encounter` for a fight with no NPC side, assembled with the same
    discipline (this game's rules bundle under its house rules, its weapon table, the
    named backdrop). The gang war launches it (``mf-prg.bas:27020``
    ``ks(1)=us:ks(2)=sp:kf$="ks":gosub30000``):

    * side 1 is ``defender``'s whole gang, side 2 ``attacker``'s, each placed as
      ``:30000`` places side ``i`` (``kp(i,j)=129-18*(i=2)+p(j)``) with every gangster's
      own weapon and **current** energy (a gangster at 0 is placed and fights);
    * each side is owned by its player (``Fighter.owner``), so the fight writes each
      gang's energy back to its own player (``:30260-30265``);
    * neither side is the CPU's (``:30110 ifks(s)=0`` is false for both): each is a
      :class:`~engine.fight_loop.HumanDriver` naming the player who controls it, which
      every :class:`~engine.interactions.CombatScreen` of that side carries. Side 1, the
      defender, moves first (``:30100 s=1``).

    The outcome screen (``:30500-30515``) names the gangs, ``bn$(ks(i))``.
    """
    players = ctx.state.players
    weapons_path = _CONFIG_DIR / "entities" / "weapons.yaml"
    equip = equipper(weapon_stats_by_id(weapons_path))
    defending = players[defender].roster
    attacking = players[attacker].roster
    side1 = build_player_side(defending, owner=defender)
    # :30000 side 2's anchor: build_player_side places at side 1's, so move each fighter.
    side2 = tuple(
        replace(fighter, position=position)
        for fighter, position in zip(
            build_player_side(attacking, owner=attacker),
            placement_positions(SIDE2_ANCHOR, len(attacking)),
        )
    )
    sides = (
        tuple(replace(f, equipment=equip(f.weapon)) for f in side1),
        tuple(replace(f, equipment=equip(f.weapon)) for f in side2),
    )
    scenario = Scenario(
        sides=sides,
        grid=load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{grid}.yaml"),
        rules=build_rules(ctx.state.config.house_rules),
        # :30020 seeds the direction memory for a CPU side only (ks(2)=0): none here.
        dir_memory={},
    )
    result = yield StartCombat(
        scenario=scenario,
        drivers={1: HumanDriver(player=defender), 2: HumanDriver(player=attacker)},
    )
    yield from narrate_combat_outcome(
        winner=result.winner,
        player_name=_gang_name(players[defender]),
        enemy_name=_gang_name(players[attacker]),
        player_losses=result.losses[0],
        enemy_losses=result.losses[1],
    )
    return result


def roster_after(roster, result):
    """``roster`` with each gangster's energy as the fight ``result`` left it.

    A fight's energy loss is written back as effects buffered into the calling handler
    (:func:`engine.fight_loop._run_combat`), so ``ctx.state`` does not show it until
    the handler ends. A handler that runs a second fight in the same action passes
    this as the second fight's ``roster=`` (the mayor hit, ``mf-prg.bas:24005``
    then ``:24010``: the source keeps the energy in the gang's stats between the two). The
    values come from the result's ``roster_vitality``; a gangster it does not name keeps
    its energy.
    """
    closing = dict(result.roster_vitality)
    return tuple(
        replace(member, vitality=closing[slot]) if slot in closing else member
        for slot, member in enumerate(roster)
    )


# --- the gangster picker ----------------------------------------------------


def gangster_line(member, weapons: list[dict]) -> dict:
    """One gangster as ``:1300-1320`` prints it: the name, the four stats, the weapon.

    ``:1300`` reads the stats ``x$=ge$(a,b)``; ``:1315`` prints the four of them
    (``"e"left$(x$,2)" k"mid$(x$,3,2)" i"mid$(x$,5,2)" b"right$(x$,2)``, two digits
    each); ``:1320`` the weapon's name. The theme template pads the digits.
    """
    return {
        "name": member.name,
        "energie": member.vitality,
        "kraft": member.attrs["kraft"],
        "intelligenz": member.attrs["intelligenz"],
        "brutalitaet": member.attrs["brutalitaet"],
        "weapon": weapons[member.weapon]["name"],
    }


def pick_gangster(ctx, *, cancellable: bool):
    """The gangster picker, ``mf-prg.bas:1130-1155``: which of the gang does the job.

    A generator (``y = yield from pick_gangster(ctx, cancellable=...)``) returning the
    chosen gangster's 0-based roster index, or ``None`` for the source's ``y=0``:

    * ``:1130 ifgz(sp)=0theny=0:return`` — an empty gang returns ``None`` at once,
      with no list and no prompt;
    * ``:1135-1140`` — each gangster, numbered from 1, as ``:1300-1320`` prints it
      (the key the source waits for after each one is the client's pause);
    * ``:1145 input"{down}nummer:";y:ify>gz(sp)thenprint"{up}{up}";:goto1145`` — the
      number, 0 up to the gang's size; a larger one is asked again (the prompt's
      ceiling, which the driver enforces);
    * ``:1150 ify=0thenreturn`` — 0 returns ``None``. The caller decides what that
      means (waf goes back to its weapon list, aut and the training leave);
    * ``:1155 a=sp:b=y:gosub1350`` loads the gangster's stats; the caller reads them
      off the roster at the returned index.

    The caller prints its own question first (``:13035``, ``:13101``, ``:14101``).

    ``cancellable`` says whether the client may cancel the prompt (an empty answer,
    which discards everything the calling handler buffered). A caller that has already
    committed to something the source keeps on a ``y=0`` passes ``False``; then only
    0 leaves, and the caller's buffered effects stand.

    What the port reads differently (the catalogue's header lists it): the prompt reads
    whole numbers from 0 up, so a negative number (the C64 stops with an error at
    ``:1350``'s subscript), a fraction (the C64 truncates it to a gangster) and, on a
    prompt that is not cancellable, an empty answer (the C64 keeps whatever ``y`` last
    held) are asked again.

    The roster is ``ctx.state``'s: no caller changes the gang before it picks.
    """
    roster = ctx.state.players[ctx.state.clock.active_player].roster
    if not roster:  # :1130
        return None
    weapons = load_weapons(_CONFIG_DIR / "entities" / "weapons.yaml")
    for number, member in enumerate(roster, start=1):  # :1135-1140
        yield ShowMessage(
            "turn.picker.gangster", {"index": number, **gangster_line(member, weapons)}
        )
    y = yield PromptInt("turn.picker.prompt", min=0, max=len(roster), cancellable=cancellable)
    if y == 0:  # :1150
        return None
    return y - 1  # :1155


# --- fnm rent formula ------------------------------------------------------


def fnm(ln: int, params: dict) -> int:
    """Per-tile rent for tile index ``ln`` (mf-prg.bas:115).

    ``params`` is the ``formula_params.fnm`` block from ``config.yaml``:
    a ``base`` plus a per-``ln`` ``overrides`` map. Returns ``overrides[ln]`` if
    present, else ``base``. This reproduces the original's three-way result
    (fnm(1) == 150, fnm(3) == fnm(4) == 100, else 50). The slw handler
    reuses this helper.
    """
    # mf-prg.bas:115 `deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)`, with C64 true = -1.
    overrides = params.get("overrides", {}) or {}
    # YAML maps int keys fine, but tolerate str keys defensively.
    if ln in overrides:
        return overrides[ln]
    if str(ln) in overrides:
        return overrides[str(ln)]
    return params["base"]


# --- score / rank helper ---------------------------------------------------


def score_and_rank(x: float, params: dict, *, player: int | None = None) -> ScoreAndRank:
    """Build the :class:`ScoreAndRank` effect for reward ``x`` — ports ``gosub 1160/1165``.

    ``params`` is the config's ``formula_params`` block; the ``rank_divisor`` (11.1) is
    read from it and passed into the effect (the engine hardcodes no game number). ``x`` is the raw reward (1 for range training, 2 for camp, or a buy-score
    delta); the effect weights it by ``formula_params["score_mult"]`` (``x8``) at apply
    time.
    Every score-awarding waf path routes through this helper so rank never drifts from
    the original. ``player`` scores another player than the active one (the gang war's
    loser, ``:27041`` ``sp=b:x=-1:gosub1160``).
    """
    return ScoreAndRank(amount=x, rank_divisor=params["rank_divisor"], player=player)


# --- combat-outcome narration -----------------------------------------------


def narrate_combat_outcome(
    *, winner: int, player_name: str, enemy_name: str, player_losses: int, enemy_losses: int
):
    """Yield the post-fight outcome screen shared by ``jobs.py``, ``kdh.py``, and
    ``upkeep.py`` (narrating the outcome is the invoking handler's job —
    ``_run_combat`` itself yields no final screen).

    ``winner`` is the side returned by ``StartCombat`` (1 == the acting player's
    roster, 2 == the scripted enemy, per ``engine.fight_loop._run_combat``).

    The original prints this screen at ``:30500-30515`` UNCONDITIONALLY, for every
    fight, win or lose — ``:30106``'s ``goto30500`` is the single exit from the
    combat engine, and ``:5010``'s ``goto30000`` is the single entry every caller
    uses. There is no caller-specific and no win-conditional branch, so the losses
    block always prints.

    ``player_losses``/``enemy_losses`` are the REAL per-side death tallies the fight
    computed (``v(1)``/``v(2)``, zeroed at ``:30100``, incremented at ``:30310`` per
    death), handed back on the :class:`~engine.combat.CombatResult`. Deriving them from the
    winner alone (``0 if side == winner else 1``) is only correct for a
    one-fighter-per-side fight and would miscount the multi-enemy debt-collectors
    fight (``gz(0)=5``, ``:4355``).
    """
    winner_name = player_name if winner == 1 else enemy_name
    yield ShowMessage("combat.winner_banner", {"name": winner_name})
    yield ShowMessage("combat.losses_heading")
    for name, count in ((player_name, player_losses), (enemy_name, enemy_losses)):
        yield ShowMessage("combat.losses_line", {"name": name, "count": count})


# --- new-game setup --------------------------------------------------------


def _roll_stat(rng: Rng, roll: Mapping) -> int:
    """Stat/cash roll: rng.range(choices)*step + base (mf-prg.bas:350/315)."""
    # :350 `x=int(rnd(1)*9)*5+10` (stats); :315 `ka(i)=int(rnd(1)*5)*500+5000` (cash).
    return rng.range(roll["choices"]) * roll["step"] + roll["base"]


#: The house rule that picks the first gangster's intelligence (``content/house_rules.yaml``).
INTELLIGENCE_OR_30 = "intelligence_or_30"


def _house_rules_map(cfg_dir: Path, choices: Mapping[str, str]) -> dict[str, str]:
    """Every switch of the catalogue in ``cfg_dir`` at faithful, then ``choices``."""
    rules = switchable(load_house_rules(cfg_dir / HOUSE_RULES_FILE))
    chosen = {rule.id: FAITHFUL for rule in rules}
    for rule_id, setting in choices.items():
        if rule_id not in chosen:
            raise ValueError(f"the catalogue offers no switch for house rule {rule_id!r}")
        if setting not in HOUSE_RULE_SETTINGS:
            raise ValueError(
                f"house rule {rule_id!r} is set to {setting!r}; "
                f"expected one of {list(HOUSE_RULE_SETTINGS)}"
            )
        chosen[rule_id] = setting
    return chosen


def parse_setup_number(text: str, *, integer: bool) -> float | None:
    """Parse one numeric setup answer; ``None`` for non-numeric input (asked again).

    The end year is ``int(val(x$))`` in the source (mf-prg.bas:170) -- truncated to an
    integer; the score weight is ``val(x$)`` (:175), a decimal such as ``0.5``, and so
    is the player count (:205 ``sz=val(x$)``). Only a finite number is a number here.
    """
    try:
        value = float(text.strip())
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if integer else value


def in_range(value: float, bounds: Mapping[str, float]) -> bool:
    """``bounds`` is one ``input_ranges`` entry (``{min, max}``) from config.yaml."""
    return bounds["min"] <= value <= bounds["max"]


def check_end_year(end_year: int, ranges: Mapping) -> None:
    """Refuse an ``x9`` end year outside ``input_ranges.end_year`` (:172)."""
    yr = ranges["end_year"]
    if not in_range(end_year, yr):
        raise ValueError(f"end_year must be in [{yr['min']}, {yr['max']}], got {end_year}")


def check_score_weight(score_weight: float, ranges: Mapping) -> None:
    """Refuse an ``x8`` score weight outside ``input_ranges.score_weight`` (:176)."""
    sw = ranges["score_weight"]
    if not in_range(score_weight, sw):
        raise ValueError(f"score_weight must be in [{sw['min']}, {sw['max']}], got {score_weight}")


def name_fits(name: str, ranges: Mapping) -> bool:
    """Whether a player or gang name is as long as ``input_ranges.name_length`` allows.

    :290-292, the input routine for both names (``:210``/``:215`` ``gosub290``):
    :291 ``ifx$=""orlen(x$)>13`` asks again for an empty name or one over 13 characters.
    """
    return in_range(len(name), ranges["name_length"])


def check_players(players: Sequence[tuple[str, str]], ranges: Mapping) -> None:
    """Refuse a player list of the wrong size (:206) or with a name that does not fit."""
    pc = ranges["player_count"]
    if not in_range(len(players), pc):
        raise ValueError(f"player count must be in [{pc['min']}, {pc['max']}], got {len(players)}")
    nl = ranges["name_length"]
    for name in (text for player in players for text in player):
        if not name_fits(name, ranges):
            raise ValueError(
                f"a player or gang name must be {nl['min']} to {nl['max']} characters, got {name!r}"
            )


@dataclass(frozen=True)
class SetupPlayer:
    """One player as setup made them: the names and the starting gangster's rolls.

    ``intelligenz`` is the :350 roll itself, as the eigenschaften screen showed it;
    :311's ``in=xor30`` is applied when :func:`new_game` builds the state, by the
    game's ``intelligence_or_30`` house rule.
    """

    name: str
    gang_name: str
    kraft: int
    intelligenz: int
    brutalitaet: int
    cash: int


@dataclass(frozen=True)
class SetupRecord:
    """What the new-game setup decided: the setup handler's return value.

    ``house_rules`` is the full map (every switch of the catalogue); ``players`` one
    :class:`SetupPlayer` per player, in turn order. :func:`new_game` builds the first
    state from it; building draws nothing.
    """

    end_year: int
    score_weight: float
    house_rules: Mapping[str, str]
    players: tuple[SetupPlayer, ...]


def roll_player(rng: Rng, setup: Mapping, name: str, gang_name: str) -> SetupPlayer:
    """One player's rolls with no frame stopped late: :310-312 through :350, then :315.

    The keyword :func:`new_game` rolls this way. Draw order: kraft, intelligenz,
    brutalitaet, cash -- the setup handler's rolls stopped on their first frame draw the
    same (KTD-2).
    """
    kraft = _roll_stat(rng, setup["stat_roll"])
    intelligenz = _roll_stat(rng, setup["stat_roll"])
    brutalitaet = _roll_stat(rng, setup["stat_roll"])
    # :315's ``ifpeek(53247)=1thenka(i)=500000`` is a debug switch, not ported.
    cash = _roll_stat(rng, setup["cash_roll"])
    return SetupPlayer(name, gang_name, kraft, intelligenz, brutalitaet, cash)


def new_game(
    record: SetupRecord | None = None,
    *,
    seed: int | None = None,
    end_year: int | None = None,
    score_weight: float | None = None,
    players: Sequence[tuple[str, str]] | None = None,
    house_rules: Mapping[str, str] | None = None,
    config_path: str | Path = _DEFAULT_CONFIG,
) -> GameState:
    """Build a fresh :class:`GameState` — the ported BASIC new-game setup.

    Two paths share one state builder:

    * ``new_game(record)`` builds the state from the setup handler's
      :class:`SetupRecord` (``handlers/new_game.py``, the interactive setup). It draws
      nothing; the keyword arguments other than ``config_path`` must be left out.
    * ``new_game(seed=, end_year=, score_weight=, players=, house_rules=)`` rolls each
      player's stats itself from one ``Rng(seed)`` (:func:`roll_player`), as if every
      roll were stopped on its first frame.

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
    house_rules:
        The setup's house-rules choices, switchable catalogue id -> ``"faithful"`` or
        ``"intent"``. Every switch the catalogue beside ``config_path`` offers starts
        at faithful; these override it. An id the catalogue offers no switch for, or
        another setting, raises ``ValueError``. The full map lands on
        ``state.config.house_rules``.
    config_path:
        Which ``config.yaml`` to assemble from.
    """
    cfg = load_config(config_path)
    cfg_dir = Path(config_path).resolve().parent
    setup = cfg["setup"]
    if record is None:
        if seed is None or end_year is None or score_weight is None or players is None:
            raise TypeError(
                "new_game needs a setup record, or seed, end_year, score_weight and players"
            )
        check_players(players, cfg["input_ranges"])
        rng = Rng(seed)
        record = SetupRecord(
            end_year=end_year,
            score_weight=score_weight,
            house_rules=_house_rules_map(cfg_dir, house_rules or {}),
            players=tuple(roll_player(rng, setup, name, gang) for name, gang in players),
        )
    elif any(v is not None for v in (seed, end_year, score_weight, players, house_rules)):
        raise TypeError("new_game takes a setup record or the setup keywords, not both")
    return _build_state(record, cfg, cfg_dir)


def _build_state(record: SetupRecord, cfg: Mapping, cfg_dir: Path) -> GameState:
    """The first state from a setup record (shared by both :func:`new_game` paths)."""
    setup = cfg["setup"]
    ranges = cfg["input_ranges"]

    # --- validate inputs (setup owns range validation) ---------------------
    check_end_year(record.end_year, ranges)
    check_score_weight(record.score_weight, ranges)
    check_players([(p.name, p.gang_name) for p in record.players], ranges)
    chosen_rules = _house_rules_map(cfg_dir, record.house_rules)
    # House rule intelligence_or_30: faithful stores the roll OR 30, intent the roll.
    intelligence_as_rolled = chosen_rules.get(INTELLIGENCE_OR_30) == INTENT

    # --- entity tables -----------------------------------------------------
    vehicles = load_vehicles(cfg_dir / cfg["entities"]["vehicles"])
    start_vehicle = setup["start_vehicle"]
    start_ms = vehicles[start_vehicle]["tr"]  # ms = tr(vehicle); on foot tr(0)=25

    game_players: list[Player] = []
    for rolled in record.players:
        raw_intel = rolled.intelligenz
        # :311 `in=xor30` — the roll OR 30 (faithful), or the roll :350 printed (intent).
        intelligenz = raw_intel if intelligence_as_rolled else raw_intel | setup["intelligenz_or"]
        gangster = Gangster(
            name=rolled.name,  # :300 gn$(i,1)=sp$(i)
            weapon=0,
            energie=setup["start_energy"],  # fixed 5, not rolled
            kraft=rolled.kraft,
            intelligenz=intelligenz,
            brutalitaet=rolled.brutalitaet,
        )
        game_players.append(
            Player(
                name=rolled.name,
                ka=rolled.cash,
                rank=setup["start_rank"],
                po=setup["start_position"],
                vehicle=start_vehicle,
                ms=start_ms,
                roster=(gangster,),
                # Every declared key at its default, then this player's own values.
                values={
                    **SCHEMA.player_defaults(),
                    "gang_name": rolled.gang_name,
                    "nr": setup["start_nr"],
                },
            )
        )

    clock = Clock(
        year=setup["start_year"],  # ja=1925 (mf-prg.bas:1000), from config data
        month=0,
        end_year=record.end_year,
        active_player=0,
        player_count=len(record.players),
    )
    config = Config(
        # Passed as plain YAML dicts: Config deep-freezes them on construction. The
        # x8 score weight is a setup input, so it joins the static params here.
        formula_params={**cfg["formula_params"], "score_mult": record.score_weight},
        house_rules=chosen_rules,
    )

    return GameState(
        players=tuple(game_players),
        clock=clock,
        config=config,
        values=SCHEMA.global_defaults(),
    )

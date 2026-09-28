"""fightlab — a terminal tool to PLAY, WATCH, and REPLAY fights.

Three things a developer needs and the main client does not give them:

* **play** a self-contained fight described by a scenario file — a COMPLETE two-sided
  fight buildable with no ``GameState`` and no roster (the "invented entities" path,
  :class:`~engine.scenario.Scenario`'s explicit construction). Human-vs-AI, seedable.
* **watch** a recorded fight (loaded via :func:`engine.recording.load`), stepping activation
  by activation — forward, back, or autoplay-to-the-end.
* **--debug** every shot's arithmetic: the two draws with their bounds, the accuracy
  and damage attribute values, and the resulting damage — **read off the recorded
  event, never recomputed**. The tool renders and reads keys; every decision,
  calculation, and state transition already happened in the engine.

**Thinness.** This module imports NO formula-level internal — no
``combat_rules`` ``is_hit``/``damage_roll``, no hand-rolled equivalent. Every number it
prints already exists on a :class:`~engine.interactions.CombatScreen` payload, an
:class:`~engine.recording.ActivationEvent`, or a
:class:`~engine.combat.CombatResult`. The scenario LOADER (below) uses this config's own
``setup``/``combat_rules`` helpers to BUILD a fight — that is fight *construction*, the
same config-side entity resolution :func:`engine.combat_setup.setup_combat` does, not a
second formula path.

**Layering.** ``clients/`` depends on the engine; the engine never depends on
``clients``. The scenario loader lives HERE (client/config-side) rather than in
``engine/`` because reading this game's file format, resolving its weapon ids, and
building its ``Gangster`` roster is config knowledge the engine does not hold.

Invocation (mirrors ``clients/terminal/cli.py``'s argparse)::

    python -m clients.terminal.fightlab play  --scenario PATH [--seed N] [--debug]
    python -m clients.terminal.fightlab watch --recording PATH      [--debug]

No ``--player``, no config-dir, no save flags — the "no game, no save/load UI"
boundary. Step-through vs. autoplay are runtime keypresses within ``watch``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, TextIO

import yaml

from engine.fight_loop import AiDriver, HumanDriver
from engine.recording import load as load_recording
from engine.recording import record_fight, replay
from engine.scenario import Scenario
from engine.state import FAITHFUL
from engine.strings import Resolver

from clients.terminal import TerminalInput
from clients.terminal.palette import Colors, load_palette
from clients.terminal.session import _read_key
from clients.terminal.renderers import (
    render_combat_grid,
    render_fighter_panel,
    render_screen_clear,
)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "data" / "game_configs" / "mafia_1920s"


# --------------------------------------------------------------------------- #
# Scenario file format + loader (client/config-side, engine-layer-clean)      #
# --------------------------------------------------------------------------- #
#
# A scenario file describes a COMPLETE fight — both sides fully constructed — so it is
# buildable with no GameState and no roster. To keep the file path and the in-game path
# from diverging, the ENEMY side is built through the SAME machinery the game uses:
# an ``encounter:`` key names one of this config's encounter declarations
# (content/encounters/*.yaml), and the loader delegates to ``Scenario.from_encounter``.
# One loader, so the two sources cannot diverge.
#
# The scenario adds a PLAYER side the encounter never declares (an encounter declares
# only the enemy): a ``player:`` list of INVENTED fighters. Each is a plain
# ``Gangster(name/weapon/energie/kraft/brutalitaet)`` — the same construction a real
# roster gangster uses — so a scenario carries invented entities without any new
# machinery.
#
# Shape::
#
#   encounter: kdh_ambush          # names content/encounters/<key>.yaml (the enemy side)
#   seed: 42                       # optional default seed (--seed overrides)
#   house_rules: {...}             # the house-rules map it runs under (required; every
#                                  # switch of the catalogue, faithful or intent)
#   player:                        # side 1 — invented fighters, no roster needed
#     - {name: hero, weapon: 5, energie: 20, kraft: 34, brutalitaet: 28}


def _config_helpers():
    """Import this config's OWN build helpers (setup + combat_rules + Gangster).

    fightlab runs as ``python -m clients.terminal.fightlab`` from the repo root, so
    the config is always importable as the package ``data.game_configs.mafia_1920s``.
    """
    from data.game_configs.mafia_1920s import combat_rules, setup
    from data.game_configs.mafia_1920s.gangster import Gangster

    return setup, combat_rules, Gangster


def _all_faithful_rules() -> Any:
    """This config's rules bundle under its all-faithful house-rules map.

    fightlab has no game to take a map from, so ``watch`` replays under this one: a
    recording made under other choices is refused, naming the entry that differs.
    """
    from data.game_configs.mafia_1920s import combat_rules, house_rules

    offered = house_rules.switchable(house_rules.CATALOGUE)
    return combat_rules.build_rules({rule.id: FAITHFUL for rule in offered})


def load_scenario(path: str | Path, *, seed: int | None = None) -> Scenario:
    """Load a scenario file into a complete two-sided :class:`~engine.scenario.Scenario`.

    The enemy side is built through :func:`Scenario.from_encounter` off the file's
    ``encounter:`` key — the SAME path the game uses — so a scenario's enemy side is by
    construction equal to ``Scenario.from_encounter(<key>, …)``. The player side is the
    file's ``player:`` list of invented :class:`Gangster` fighters.

    Weapon ids are resolved at LOAD (both sides go through the config's ``equipper``),
    so an unknown weapon id fails HERE, naming the id — never as a ``KeyError`` several
    activations deep in a fight.

    ``seed`` overrides the file's own ``seed:`` (``--seed`` on the command line).

    The file's ``house_rules:`` map is the one the fight's rules bundle is built under.
    A file with no map, or one whose map does not set exactly the catalogue's switches,
    is refused with a ``ValueError`` naming the problem (R21).
    """
    from data.game_configs.mafia_1920s import house_rules

    setup, combat_rules, Gangster = _config_helpers()

    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    enc_key = raw.get("encounter")
    if not enc_key:
        raise ValueError(
            f"scenario {path.name!r}: missing 'encounter' (the enemy side's declaration)"
        )
    player_specs = raw.get("player")
    if not player_specs:
        raise ValueError(f"scenario {path.name!r}: missing 'player' (side 1's fighters)")
    try:
        chosen = house_rules.check_stored_map(raw.get("house_rules"), house_rules.CATALOGUE)
    except ValueError as exc:
        raise ValueError(f"scenario {path.name!r}: {exc}") from None

    params = yaml.safe_load((_CONFIG_DIR / "config.yaml").read_text(encoding="utf-8"))[
        "formula_params"
    ]
    weapon_stats = setup.weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")
    equip = combat_rules.equipper(weapon_stats)

    # Build the invented player roster. Resolve each weapon id NOW (equip raises a
    # KeyError naming the id) so a bad id is a load-time failure, not a fight-time one.
    roster = []
    for i, spec in enumerate(player_specs):
        weapon = spec.get("weapon", 0)
        try:
            equip(weapon)
        except KeyError as exc:
            raise ValueError(
                f"scenario {path.name!r}: player[{i}] names unknown weapon id {weapon!r} "
                f"({exc}); fix the id before the fight"
            ) from exc
        roster.append(
            Gangster(
                name=spec.get("name", f"fighter{i}"),
                weapon=weapon,
                energie=spec.get("energie", 20),
                kraft=spec.get("kraft", 30),
                brutalitaet=spec.get("brutalitaet", 30),
                intelligenz=spec.get("intelligenz", 30),
            )
        )

    enc = setup.load_encounter(_CONFIG_DIR / "content" / "encounters" / f"{enc_key}.yaml")
    spec = enc.variants[0]
    grid = setup.load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{enc.grid}.yaml")

    file_seed = raw.get("seed")
    effective_seed = seed if seed is not None else file_seed

    return Scenario.from_encounter(
        spec,
        roster,
        combat_rules.build_rules(chosen),
        enemy_attrs=combat_rules.enemy_attrs(params),
        grid=grid,
        equip=equip,
        seed=effective_seed,
    )


# --------------------------------------------------------------------------- #
# Real fighter names (NOT the "side {i}" losses placeholder)                   #
# --------------------------------------------------------------------------- #
def _side_names_from_scenario(scenario: Any) -> dict[int, str]:
    """Real per-side display names, read off the scenario's own fighters.

    ``render_combat_losses`` labels sides ``"side {i}"`` — a placeholder. The debug
    dump's target line needs the REAL name, which lives on the fighter. A side's name
    is its first fighter's name (the boss for side 1, the enemy party's name for
    side 2 — every enemy fighter shares one name).
    """
    names: dict[int, str] = {}
    for i, side in enumerate(scenario.sides or (), start=1):
        names[i] = side[0].name if side else f"side {i}"
    return names


def _fighter_name(snapshot: Any, side: int, index: int) -> str:
    """The name of ``(side, index)`` read off a recorded json_safe snapshot, or ""."""
    if not snapshot:
        return ""
    sides = snapshot.get("sides") or []
    if 1 <= side <= len(sides):
        fighters = sides[side - 1]
        if 0 <= index < len(fighters):
            return fighters[index].get("name", "")
    return ""


# --------------------------------------------------------------------------- #
# --debug: the shot dump — every value read off the event, never recomputed   #
# --------------------------------------------------------------------------- #
def _action_draws(event: Any) -> list:
    """The ACTION's draws — this activation's draws past the DECISION's coin-flip draws.

    ``decision_draw_count`` is how many draws the AI's decision consumed; the ACTION's
    hit/damage rolls are the rest, in order.
    """
    return list(event.draws[event.decision_draw_count :])


def _find_draw(draws: list, bound: int, used: set[int]) -> tuple[int, int] | None:
    """Find the next unconsumed ``range(bound)`` draw; return ``(value, position)``.

    The recorded draws are ``[method, [args], value]`` triples. The bounds come from
    the rules bundle's own draw declarations (:func:`_declared_draws`), never from a
    formula restated here; consuming them in declared order keeps two draws with an
    equal bound apart.
    """
    for i, rec in enumerate(draws):
        if i in used:
            continue
        method, args, value = rec[0], rec[1], rec[2]
        if method == "range" and list(args)[:1] == [bound]:
            used.add(i)
            return value, i
    return None


def _first_made(
    matched: list[tuple[str, int, tuple[int, int] | None]],
) -> tuple[str, Any, tuple[int, int] | None]:
    """The first ``(label, bound, found)`` whose draw was made, or ``("-", "-", None)``."""
    return next((m for m in matched if m[2] is not None), ("-", "-", None))


def _declared_draws(
    declare: Any, attacker: Any, equipment: Any, draws: list, used: set[int]
) -> list[tuple[str, int, tuple[int, int] | None]]:
    """Match a formula's declared draws to the recorded ones: ``(label, bound, found)``.

    ``declare`` is the rules bundle's ``hit_draws``/``damage_draws`` (what the game's
    formula draws, in order, as ``(label, bound)``). ``found`` is ``(value, position)``
    of the recorded draw, or ``None`` when that draw was not made.
    """
    if declare is None or not isinstance(attacker, int):
        return []
    return [
        (label, bound, _find_draw(draws, bound, used))
        for label, bound in declare(attacker, equipment)
    ]


def render_shot_debug(
    event: Any,
    *,
    weapon_names: list[str],
    prev_snapshot: Any,
    out: TextIO,
    rules: Any,
    replayed: bool = False,
) -> None:
    """Print the concrete shot block (draws, attribute values, damage) for one ActivationEvent.

    EVERY number comes from ``event.calc_inputs`` / ``event.draws`` / ``event.result``
    and the recorded snapshots — nothing is recomputed here. Which recorded draw is
    which comes from ``rules`` (the fight's :class:`~engine.combat.RulesBundle`):
    its ``hit_draws``/``damage_draws`` name each draw the game's formula makes. Under
    ``watch``, the block is prefixed ``(replayed)``.
    """
    ci = event.calc_inputs
    result = event.result
    equipment = ci.get("equipment") or {}
    ts = equipment.get("ts", 0)
    tg = equipment.get("tg", 0)
    weapon_id = ci.get("weapon", 0)
    weapon_name = weapon_names[weapon_id] if 0 <= weapon_id < len(weapon_names) else str(weapon_id)
    direction = ci.get("direction")
    actor_name = _fighter_name(event.snapshot, event.side, event.fighter_index)

    hit_attr = ci.get("hit.attacker.attr")
    hit_value = ci.get("hit.attacker.value")
    dmg_attr = ci.get("damage.attacker.attr")
    dmg_value = ci.get("damage.attacker.value")

    action_draws = _action_draws(event)
    used: set[int] = set()
    # The hit test's draws, then the damage roll's, as the rules bundle declares them.
    # Each capability's first draw actually MADE is shown as "the" draw; every declared
    # draw is consumed in order so none is mistaken for another with an equal bound.
    hit_draws = _declared_draws(rules.hit_draws, hit_value, equipment, action_draws, used)
    dmg_draws = _declared_draws(rules.damage_draws, dmg_value, equipment, action_draws, used)
    hit_label, hit_bound, hit_draw = _first_made(hit_draws)
    dmg_label, dmg_bound, dmg_draw = _first_made(dmg_draws)

    prefix = "(replayed) " if replayed else ""
    hdr_dir = f" {_DIR_LABELS.get(direction, direction)}" if direction is not None else ""
    out.write(
        f"{prefix}=== activation {event.index} | side {event.side}, "
        f"fighter {event.fighter_index + 1} ({actor_name}) -> shoot{hdr_dir} ===\n"
    )
    out.write(f"  weapon: {weapon_name} (ts={ts}, tg={tg}, range={equipment.get('range')})\n")
    if rules.hit_draws is None:
        out.write("  hit check:\n")
        out.write("    (the rules bundle declares no hit draws — cannot name them)\n")
    elif hit_draw is None:
        # No declared hit draw was made: the projectile reached no target (left the
        # grid, hit a wall, or found no fighter in line) — the hit check never ran. Say
        # so, rather than printing a misleading "-> None" under a hit-check heading.
        out.write("  hit check:\n")
        out.write("    (shot reached no target — no hit check)\n")
    else:
        out.write("  hit check:\n")
        out.write(f"    draw = rng.range({hit_label}={hit_bound})          -> {hit_draw[0]}\n")
        out.write(f"    accuracy attr ({hit_attr})           -> {hit_value}\n")
        verdict = "HIT" if result.get("hit") else "MISS"
        out.write(f"    weapon != 0, kraft draw >= 10   -> {verdict}\n")

    if result.get("hit"):
        dmg_draw_value = dmg_draw[0] if dmg_draw is not None else None
        damage = result.get("damage")
        out.write("  damage roll:\n")
        out.write(f"    draw = rng.range({dmg_label}={dmg_bound})    -> {dmg_draw_value}\n")
        out.write(f"    damage attr ({dmg_attr})         -> {dmg_value}\n")
        if isinstance(dmg_draw_value, int) and isinstance(dmg_value, int):
            out.write(
                f"    (draw + attr) // 10 + 1         -> "
                f"({dmg_draw_value} + {dmg_value}) // 10 + 1 = {damage}\n"
            )
        else:
            out.write(f"    (draw + attr) // 10 + 1         -> {damage}\n")

        tgt_side = result.get("target_side")
        tgt_index = result.get("target_index")
        tgt_name = _fighter_name(event.snapshot, tgt_side, tgt_index)
        before = _fighter_vitality(prev_snapshot, tgt_side, tgt_index)
        after = _fighter_vitality(event.snapshot, tgt_side, tgt_index)
        out.write(
            f"  target: side {tgt_side}, fighter {(tgt_index or 0) + 1} ({tgt_name})  "
            f"energie {before} -> {after}\n"
        )
    out.write(
        f"  result: {'hit' if result.get('hit') else 'miss'}, "
        f"damage={result.get('damage', 0)}, downed={result.get('downed', False)}\n"
    )
    out.flush()


#: The four combat step deltas -> a readable direction label (presentation only).
_DIR_LABELS = {1: "east", -1: "west", -40: "north", 40: "south"}


def _fighter_vitality(snapshot: Any, side: Any, index: Any) -> Any:
    """The ``vitality`` of ``(side, index)`` read off a json_safe snapshot, or ``?``."""
    if not snapshot or side is None or index is None:
        return "?"
    sides = snapshot.get("sides") or []
    if 1 <= side <= len(sides):
        fighters = sides[side - 1]
        if 0 <= index < len(fighters):
            return fighters[index].get("vitality", "?")
    return "?"


def _print_divergence(report: Any, out: TextIO) -> None:
    """Print a replay divergence (recorded vs. recomputed) side by side."""
    out.write(f"!! REPLAY DIVERGED at activation {report.at_index}\n")
    out.write(f"   recorded  : {report.expected}\n")
    out.write(f"   recomputed: {report.got}\n")
    out.write("   (a formula changed since this recording — autoplay halted)\n")
    out.flush()


# --------------------------------------------------------------------------- #
# play — a self-contained scenario fight (human vs AI), no GameState, no file  #
# --------------------------------------------------------------------------- #
def _weapon_names() -> list[str]:
    setup, _combat_rules, _Gangster = _config_helpers()
    return [w["name"] for w in setup.load_weapons(_CONFIG_DIR / "entities" / "weapons.yaml")]


def play(
    scenario_path: str | Path,
    *,
    seed: int | None = None,
    debug: bool = False,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
) -> Any:
    """Drive a scenario file as a human-vs-AI fight; print a losses block on finish.

    Builds NO ``GameState`` and writes NO file — the fight is the whole artifact. The
    fight is driven through :func:`engine.recording.record_fight` with the terminal
    input source (so a human side renders + prompts exactly like the main client), which
    also makes the transcript available for the debug dump WITHOUT ever touching disk.
    Side 1 is human, side 2 AI (the scenario's default drivers).

    Returns the :class:`~engine.combat.CombatResult`. ``--seed`` makes the outcome
    assertable.
    """
    stdin = stdin if stdin is not None else sys.stdin
    out = out if out is not None else sys.stdout
    assert out is not None, "no output stream: sys.stdout is None"

    scenario = load_scenario(scenario_path, seed=seed)
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    colors = Colors.detect(load_palette(_CONFIG_DIR, "classic"))
    weapon_names = _weapon_names()
    inp = TerminalInput(
        resolver=resolver, colors=colors, stdin=stdin, stdout=out, weapon_names=weapon_names
    )

    drivers = {1: HumanDriver(), 2: AiDriver()}
    from engine.rng import Rng

    rng = Rng(seed=scenario.seed) if scenario.seed is not None else None
    result, recording = record_fight(scenario, drivers, rng=rng, input_source=inp)

    # Outcome: a real losses block, using the REAL side names (not "side {i}").
    render_screen_clear(out)
    names = _side_names_from_scenario(scenario)
    out.write(
        resolver.resolve("combat.winner_banner", {"name": names.get(result.winner, "?")}) + "\n"
    )
    out.write(resolver.resolve("combat.losses_heading") + "\n")
    for i, count in enumerate(result.losses, start=1):
        out.write(
            resolver.resolve(
                "combat.losses_line", {"name": names.get(i, f"side {i}"), "count": count}
            )
            + "\n"
        )

    if debug:
        prev = None
        for event in recording.events:
            if event.kind == "activation" and event.decision.get("action") == "shoot":
                render_shot_debug(
                    event,
                    weapon_names=weapon_names,
                    prev_snapshot=prev,
                    out=out,
                    rules=scenario.rules,
                )
            prev = getattr(event, "snapshot", prev)
    out.flush()
    return result


# --------------------------------------------------------------------------- #
# watch — step through a recording (forward / back / autoplay)                #
# --------------------------------------------------------------------------- #
def _render_activation(
    recording: Any,
    index: int,
    *,
    resolver: Resolver,
    colors: Colors,
    weapon_names: list[str],
    out: TextIO,
) -> None:
    """Render the board AT ``index`` — straight from that event's recorded snapshot.

    Reconstructs the ``CombatScreen.to_json()`` wire shape from the snapshot (a
    json_safe ``CombatState``) so the existing pure renderers draw it. No forward
    replay: seeking to any index reads that index's snapshot directly.
    """
    event = recording.events[index]
    snapshot = event.snapshot or {}
    payload = {
        "grid": snapshot.get("grid") or [],
        "sides": snapshot.get("sides") or [[], []],
        "active_side": event.side,
        "active_fighter": (getattr(event, "fighter_index", 0) or 0) + 1,
        "losses": snapshot.get("losses") or [0, 0],
        "fighter": _panel_fighter(snapshot, event),
        "message": getattr(event, "result", None) or None,
    }
    render_screen_clear(out)
    action = getattr(event, "decision", {}).get("action", event.kind)
    out.write(f"-- activation {index} / {len(recording.events) - 1} | {event.kind}:{action} --\n")
    render_combat_grid(payload, out, colors)
    render_fighter_panel(payload, resolver, weapon_names, out, colors)
    out.flush()


def _panel_fighter(snapshot: Any, event: Any) -> Any:
    """The acting fighter's panel dict, off the snapshot (for render_fighter_panel)."""
    sides = snapshot.get("sides") or []
    si = event.side - 1
    fi = getattr(event, "fighter_index", 0) or 0
    if 0 <= si < len(sides) and 0 <= fi < len(sides[si]):
        return sides[si][fi]
    return None


def watch(
    recording_path: str | Path,
    *,
    debug: bool = False,
    delay: float = 0.0,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    key_reader: Any = None,
    sleeper: Any = None,
) -> None:
    """Step through a recorded fight: render activation 0, then read keys.

    Controls: space/enter advances one event; ``b`` steps back (loads the snapshot at
    ``index-1`` — no forward replay); ``a`` toggles autoplay (advances to the END and
    STOPS — no loop/wrap); any key pauses; ``q``/EOF quits.

    ``delay`` is the pause in seconds between AUTOPLAY frames (0 = as fast as the
    terminal draws, the default). It paces only autoplay; manual stepping is always
    immediate. ``sleeper`` is the pause function (defaults to :func:`time.sleep`),
    injectable so a test can assert the pacing without waiting.

    Under ``--debug`` each shot activation prints the ``(replayed)`` block off the
    recorded event; if :func:`engine.recording.replay` reports a divergence, the
    recorded-vs-recomputed values are printed and autoplay is halted.
    """
    out = out if out is not None else sys.stdout
    assert out is not None, "no output stream: sys.stdout is None"
    key_reader = key_reader if key_reader is not None else _read_key
    if sleeper is None:
        import time

        sleeper = time.sleep

    # Re-attach live rules so a divergence check can re-run the real formulas. They
    # carry the all-faithful map, so a recording made under other choices is refused.
    recording = load_recording(recording_path, rules=_all_faithful_rules())
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    colors = Colors.detect(load_palette(_CONFIG_DIR, "classic"))
    weapon_names = _weapon_names()

    # The divergence verdict (over the whole recording) — checked once; a diverged
    # replay halts autoplay and prints recorded-vs-recomputed at its shots.
    report = replay(recording)

    n = len(recording.events)
    if n == 0:
        out.write("(empty recording)\n")
        return

    def show(index: int) -> None:
        _render_activation(
            recording,
            index,
            resolver=resolver,
            colors=colors,
            weapon_names=weapon_names,
            out=out,
        )
        event = recording.events[index]
        if debug and event.kind == "activation" and event.decision.get("action") == "shoot":
            prev = recording.events[index - 1].snapshot if index > 0 else None
            render_shot_debug(
                event,
                weapon_names=weapon_names,
                prev_snapshot=prev,
                out=out,
                rules=recording.scenario.rules,
                replayed=True,
            )
            if report.diverged and report.at_index == index:
                _print_divergence(report, out)

    index = 0
    show(index)
    autoplay = False
    while True:
        if autoplay:
            if report.diverged:
                # A diverged replay halts autoplay rather than racing to the end.
                autoplay = False
                continue
            if index >= n - 1:
                # Reached the end — STOP (no loop/wrap). Fall back to stepped mode.
                autoplay = False
                continue
            # Pause on the CURRENT frame so it is visible before the next redraw clears
            # the screen; delay 0 keeps the old race-to-the-end behaviour.
            if delay > 0:
                sleeper(delay)
            index += 1
            show(index)
            continue

        key = key_reader()
        if key in ("q", ""):
            return
        if key == "a":
            autoplay = True
            continue
        if key == "b":
            if index > 0:
                index -= 1
                show(index)
            continue
        # space / enter / any other key advances one event (until the end).
        if index < n - 1:
            index += 1
            show(index)


# --------------------------------------------------------------------------- #
# argparse entry point (mirrors clients/terminal/cli.py:main)                 #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="clients.terminal.fightlab",
        description="Play, watch, and replay fights with every variable observable.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_play = sub.add_parser("play", help="play a scenario file as a human-vs-AI fight")
    p_play.add_argument("--scenario", required=True, help="path to a scenario YAML file")
    p_play.add_argument(
        "--seed", type=int, default=None, help="RNG seed (makes the outcome assertable)"
    )
    p_play.add_argument("--debug", action="store_true", help="dump each shot's arithmetic")

    p_watch = sub.add_parser("watch", help="step through a recorded fight")
    p_watch.add_argument("--recording", required=True, help="path to a recording JSON file")
    p_watch.add_argument("--debug", action="store_true", help="dump each shot's arithmetic")
    p_watch.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="seconds between autoplay frames (default 0 = as fast as possible)",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "play":
            play(args.scenario, seed=args.seed, debug=args.debug)
        elif args.command == "watch":
            watch(args.recording, debug=args.debug, delay=args.delay)
    except ValueError as exc:
        # A scenario or recording the lab refuses (a bad weapon id, a missing or
        # different house-rules map): one readable line, no traceback.
        print(exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()

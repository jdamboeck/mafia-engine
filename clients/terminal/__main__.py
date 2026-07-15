"""Runnable entry point: ``python -m clients.terminal`` (U10 follow-up).

Composes the U10 building blocks (:class:`TerminalInput`, the render helpers, and the
movement primitives) into a real, playable single-turn loop over the mafia_1920s config —
a thin harness, NOT new engine behavior. It holds no rules: movement goes through
``engine.movement.try_move`` and location actions through ``engine.actions.run_option``,
and it adopts the returned ``EngineResult.state`` after every action (both are pure).

    walk the map with W/A/S/D  ->  press into a door to ENTER a location
      ->  pick a menu option (the driver drives the handler via TerminalInput)
      ->  return to the map  ->  Q quits, or the turn ends when ms hits 0.

Run:  ``python -m clients.terminal``            (plays the default seed)
      ``python -m clients.terminal --seed 7``   (any int seed)

This is deliberately minimal: one player, one turn, the four wired locations. It exists so
the client can be exercised live; the authoritative end-to-end proof is still the headless
slice test (``tests/test_slice_integration.py``), which drives the same protocol.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from engine.actions import run_option
from engine.config_loader import load_game_config
from engine.locations import available_options, load_location
from engine.movement import DOWN, LEFT, RIGHT, UP, load_city, try_move
from engine.strings import Resolver

from clients.terminal import (
    CLEAR,
    DIM,
    RESET,
    TerminalInput,
    render_result,
)
from clients.terminal.palette import RESET_FG, fg, load_palette
from clients.terminal.renderers import render_status_bar_from_state

_CONFIG_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "game_configs" / "mafia_1920s"
)

#: W/A/S/D -> movement deltas; Q (or empty) -> quit the turn. Case-insensitive.
_MOVE_KEYS = {"w": UP, "s": DOWN, "a": LEFT, "d": RIGHT}

# Layout config loaded once at import time.
_LAYOUT_PATH = Path(__file__).parent / "layout.yaml"
try:
    _LAYOUT = yaml.safe_load(_LAYOUT_PATH.read_text(encoding="utf-8")) or {}
except (OSError, yaml.YAMLError):
    _LAYOUT = {}

_MAP_CFG = _LAYOUT.get("map", {})
_PAL = load_palette(_CONFIG_DIR)


def _load_shell(location_key: str):
    """Load a location shell by its key (``slw``/``pub``/``sph``/``waf``)."""
    path = _CONFIG_DIR / "content" / "locations" / f"{location_key}.yaml"
    return load_location(yaml.safe_load(path.read_text(encoding="utf-8")))


def _door_location_map(city_raw: dict) -> dict[int, str]:
    """Map ``la`` (numeric location id from the door table) -> shell key.

    The decoded city carries ``location`` (the shell key) alongside ``la`` for every door,
    so a single pass builds the id->key lookup the REPL needs when ``try_move`` reports an
    ``enter`` with a numeric ``la``.
    """
    out: dict[int, str] = {}
    for door in city_raw.get("doors", []):
        if "la" in door and "location" in door:
            out[door["la"]] = door["location"]
    return out


def render_map(city, city_raw: dict, state, out) -> None:
    """Draw the 40x25 city with Unicode block characters, colored cells, and
    a box-drawing border, wrapped in a full-width light_blue background band.

    Read-only view built straight off ``City`` + the door table — no rules, no mutation.
    Cell index is row-major (``cell = row*cols + col``), matching ``try_move``'s math.
    """
    cols = city.cols
    rows = len(city.grid)
    po = state.players[state.clock.active_player].po

    # Build door lookup: cell -> (location_key, char, color)
    door_info: dict[int, tuple[str, str, str]] = {}
    door_chars_cfg = _MAP_CFG.get("door_chars", {})
    for door in city_raw.get("doors", []):
        if "cell" in door and door.get("location"):
            loc_key = door["location"]
            char_cfg = door_chars_cfg.get(loc_key, {})
            char = char_cfg.get("char", loc_key[:1].upper())
            color = char_cfg.get("color", "white")
            door_info[door["cell"]] = (loc_key, char, color)

    # Special cell lookup: cell -> (char, color)
    special_cfg = _MAP_CFG.get("special_cells", {})

    player_char = _MAP_CFG.get("player_char", "@")
    player_color = _MAP_CFG.get("player_color", "red")
    street_char = _MAP_CFG.get("street_char", "·")
    street_color = _MAP_CFG.get("street_color", "dark_grey")
    border_color = "dark_grey"

    lines: list[str] = []
    for r in range(rows):
        chars: list[str] = []
        for c in range(cols):
            cell = r * cols + c
            if cell == po:
                chars.append(f"{fg(player_color, _PAL)}{player_char}")
            elif cell in door_info:
                _, dchar, dcolor = door_info[cell]
                chars.append(f"{fg(dcolor, _PAL)}{dchar}")
            elif cell in city.special_cells and cell in special_cfg:
                scfg = special_cfg[cell]
                chars.append(f"{fg(scfg.get('color', 'white'), _PAL)}{scfg['char']}")
            elif city.code(cell) == 156:  # walkable street
                chars.append(f"{fg(street_color, _PAL)}{street_char}")
            else:
                chars.append(" ")
        lines.append("".join(chars) + RESET_FG)

    # Draw box-drawing border
    border_h = "═" * cols
    out.write(f"{fg(border_color, _PAL)}╔{border_h}╗{RESET_FG}\n")
    for line in lines:
        out.write(f"{fg(border_color, _PAL)}║{RESET_FG}{line}{fg(border_color, _PAL)}║{RESET_FG}\n")
    out.write(f"{fg(border_color, _PAL)}╚{border_h}╝{RESET_FG}\n")

    # Legend
    legend_parts = [f"{player_char} you"]
    for loc_key, lchar, lcolor in sorted(
        {v for v in door_info.values()}, key=lambda x: x[0]
    ):
        legend_parts.append(f"{fg(lcolor, _PAL)}{lchar}{RESET} {loc_key}")
    legend_parts.append(f"{fg(street_color, _PAL)}{street_char}{RESET} street")
    out.write("   ".join(legend_parts) + "\n")

    # Status bar at bottom
    render_status_bar_from_state(state, out)


def _run_location(
    location_key: str,
    ln: int,
    state,
    resolver: Resolver,
    inp: TerminalInput,
    out,
):
    """Show a location's available options and run the one the player picks.

    Returns the (possibly new) state. Guard-denied options are excluded by
    ``available_options`` (KTD-8) and never listed. ``leave`` (and an empty choice)
    returns to the map without running anything.
    """
    from clients.terminal import hide_cursor, show_cursor
    from clients.terminal.renderers import (
        render_header,
        render_body,
        render_menu_option,
        render_prompt,
        render_screen_clear,
    )

    shell = _load_shell(location_key)
    options = available_options(shell, state, ln)
    if not options:
        out.write("(nothing to do here)\n")
        return state

    # Entry prompt sets the scene
    try:
        entry_text = resolver.resolve(f"locations.{location_key}.entry_prompt")
    except Exception:
        entry_text = f"-- {location_key} --"

    # --- render location screen ---
    render_screen_clear(out)
    render_header(location_key, out)
    render_body(entry_text, out)
    out.write("\n")
    for i, opt in enumerate(options):
        try:
            label = resolver.resolve(f"locations.{location_key}.menu.{opt.id}")
        except Exception:
            label = opt.id
        render_menu_option(i, label, out)
    render_prompt(out)
    out.flush()

    show_cursor(out)
    raw = sys.stdin.readline().strip()
    hide_cursor(out)

    if not raw.isdigit() or not (0 <= int(raw) < len(options)):
        return state  # invalid / empty -> back to the map, no action run
    chosen = options[int(raw)]
    if chosen.id == "leave":
        return state

    result = run_option(
        shell, chosen.id, state, ln=ln, input_source=inp, rng=None
    )
    render_result(result, out)
    return result.state  # adopt (run_option is pure)


def play(seed: int) -> None:
    """Play one turn of the default config from ``seed`` over real stdin/stdout."""
    from clients.terminal import hide_cursor, show_cursor
    from clients.terminal.ascii_art import title_screen

    out = sys.stdout
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cfg = load_game_config(_CONFIG_DIR)

    city_raw = yaml.safe_load(
        (_CONFIG_DIR / "content" / "map" / "city.yaml").read_text(encoding="utf-8")
    )
    city = load_city(city_raw)
    la_to_key = _door_location_map(city_raw)

    state = cfg.module.new_game(
        seed=seed, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    inp = TerminalInput(resolver=resolver, stdin=sys.stdin, stdout=out)

    # Title screen
    out.write(CLEAR)
    out.write(title_screen())
    out.flush()
    show_cursor(out)
    sys.stdin.readline()
    hide_cursor(out)

    note = "move: W/A/S/D into a door to enter. Q quits."
    while True:
        out.write(CLEAR)
        render_map(city, city_raw, state, out)
        out.write(f"{DIM}{note}{RESET}\n> ")
        out.flush()
        key = sys.stdin.readline().strip().lower()
        if key in ("q", "quit", ""):
            out.write("bye.\n")
            return

        delta = _MOVE_KEYS.get(key)
        if delta is None:
            note = "(use W/A/S/D or Q)"
            continue

        result = try_move(state, city, delta)
        state = result.state
        payload = result.payload
        kind = getattr(payload, "kind", None)
        note = {
            "wall": "(a wall)",
            "oob": "(edge of the city)",
        }.get(kind or "", "move: W/A/S/D into a door to enter. Q quits.")
        if kind == "enter":
            key_for_la = la_to_key.get(payload.la)
            if key_for_la is not None:
                state = _run_location(key_for_la, payload.ln, state, resolver, inp, out)
        if getattr(payload, "turn_over", False):
            from clients.terminal import hide_cursor, show_cursor
            from clients.terminal.renderers import (
                render_header,
                render_body,
                render_screen_clear,
            )
            p = state.players[state.clock.active_player]
            render_screen_clear(out)
            render_header("turn_over", out)
            render_body(
                f"cash: {p.ka}$\n"
                f"position: {p.po}\n"
                f"movement: {p.ms}\n"
                f"rank: {p.rank}\n"
                f"wanted: {p.wanted}",
                out,
            )
            out.write("\n")
            out.flush()
            show_cursor(out)
            sys.stdin.readline()
            hide_cursor(out)
            return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="clients.terminal", description="Play the mafia slice.")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42).")
    args = parser.parse_args(argv)
    play(args.seed)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()

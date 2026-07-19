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
from engine.movement import DOWN, LEFT, RIGHT, UP, advance_turn, load_city, try_move
from engine.rng import Rng
from engine.strings import Resolver

from clients.terminal import (
    CLEAR,
    DIM,
    RESET,
    TerminalInput,
    render_result,
)
from clients.terminal.palette import C64_COLOR_NAMES, RESET_FG, fg, load_palette
from clients.terminal.renderers import render_status_bar_from_state

_CONFIG_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "game_configs" / "mafia_1920s"
)

#: W/A/S/D -> movement deltas; Q (or empty) -> quit the turn. Case-insensitive.
_MOVE_KEYS = {"w": UP, "s": DOWN, "a": LEFT, "d": RIGHT}


def _is_quit(key: str) -> bool:
    """The single quit vocabulary shared by every screen (KTD-2).

    ``"q"`` is an explicit quit; ``""`` is EOF (a TTY read returning no char, or an
    exhausted piped stdin). Both the map loop and the turn-over prompt route their
    keypress through this predicate so quit behaves identically on each.
    """
    return key in ("q", "")


def _read_key() -> str:
    """Read a single keypress without Enter (raw terminal mode).

    Falls back to line-buffered ``readline`` when stdin is not a TTY (piped input,
    CI, redirected files) — raw mode via ``termios``/``tty`` requires a real
    terminal and would otherwise crash with ``termios.error``. The fallback keeps
    the client scriptable (one key per input line).
    """
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)  # raises termios.error on a non-TTY
    except Exception:
        # Not a real terminal (piped input, CI, redirected file, or no termios):
        # fall back to line-buffered reads so the client stays scriptable.
        line = sys.stdin.readline()
        if not line:  # EOF -> treat as quit
            return "q"
        return line.strip()[:1].lower()
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch.lower()

# Layout config loaded once at import time.
_LAYOUT_PATH = Path(__file__).parent / "layout.yaml"
try:
    _LAYOUT = yaml.safe_load(_LAYOUT_PATH.read_text(encoding="utf-8")) or {}
except (OSError, yaml.YAMLError):
    _LAYOUT = {}

_MAP_CFG = _LAYOUT.get("map", {})
_PAL = load_palette(_CONFIG_DIR)

# Screen-code -> Unicode character (shape only; color from C64 color RAM).
# 23 non-door, non-special codes.  Verified all East Asian Width != Wide.
_CODE_TO_CHAR: dict[int, str] = {
    # --- major terrain ---
    160: "\u2588",  # █  full block          — building (432 cells)
    224: "\u2588",  # █  full block          — building secondary (19 cells)
    156: "\u2591",  # ░  light shade         — street textured (359 cells)
     32: " ",       #    space               — open / background (92 cells)
    163: "\u2592",  # ▒  medium shade        — park / vegetation (28 cells)
    # --- water (10 cells) ---
    229: "\u2590",  # ▐  right half block
    244: "\u2590",  # ▐  right half block
    234: "\u258c",  # ▌  left half block
    247: "\u2584",  # ▄  lower half block
    248: "\u2583",  # ▃  lower 3/8 block
    249: "\u2585",  # ▅  upper 3/8 block
    # --- rail tracks (10 cells) ---
    101: "\u2502",  # │  box vertical
    106: "\u258e",  # ▎  left 1/4 block
    118: "\u258e",  # ▎  left 1/4 block
    124: "\u2598",  # ▘  quadrant upper left
    # --- building details (5 cells) ---
    192: "\u2550",  # ═  double horizontal
    237: "\u2514",  # └  corner
    238: "\u2510",  # ┐  corner
    240: "\u2554",  # ╔  double corner
    253: "\u2518",  # ┘  corner
    # --- diagonal transitions (4 cells) ---
    205: "\u2571",  # ╱  diagonal
    206: "\u2572",  # ╲  diagonal
    208: "\u2590",  # ▐  right half block
}


#: Location keys with a real shell file this slice. A door whose ``location`` is NOT
#: in this set exists in the map/door table (per U11's future scope) but has no shell
#: yet — walking into it must deny gracefully instead of crashing (U1 audit finding:
#: the kdh doors at cells 221/753 already exist in city.yaml; see ``_run_location``).
_IMPLEMENTED_LOCATIONS = frozenset({"slw", "pub", "sph", "waf"})


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
    """Draw the 40x25 city with per-cell colors from the C64 color RAM.

    Uses ``city.color(cell)`` for each cell's foreground color, giving the full
    16-color variety of the original: grey streets, red buildings, green parks,
    blue water, brown rail, etc.

    Read-only view built straight off ``City`` + the door table — no rules, no mutation.
    Cell index is row-major (``cell = row*cols + col``), matching ``try_move``'s math.
    """
    from clients.terminal.palette import bg as bg_ansi, RESET_BG

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

    # Special cell lookup
    special_cfg = _MAP_CFG.get("special_cells", {})

    # Config values
    player_char = _MAP_CFG.get("player_char", "@")
    bg_color = _MAP_CFG.get("bg_color", "light_grey")
    border_color = "dark_grey"

    # Light grey background for the entire map
    bg = bg_ansi(bg_color, _PAL)

    lines: list[str] = []
    for r in range(rows):
        chars: list[str] = []
        for c in range(cols):
            cell = r * cols + c
            if cell == po:
                chars.append(f"{fg('red', _PAL)}{player_char}")
            elif cell in door_info:
                _, dchar, dcolor = door_info[cell]
                chars.append(f"{fg(dcolor, _PAL)}{dchar}")
            elif cell in city.special_cells and cell in special_cfg:
                scfg = special_cfg[cell]
                chars.append(f"{fg(scfg.get('color', 'white'), _PAL)}{scfg['char']}")
            else:
                # Use C64 color RAM for foreground color
                c64_color_idx = city.color(cell)
                color_name = C64_COLOR_NAMES[c64_color_idx]
                code = city.code(cell)
                char = _CODE_TO_CHAR.get(code, "\u00b7")
                chars.append(f"{fg(color_name, _PAL)}{char}")
        lines.append("".join(chars) + RESET_FG)

    # Draw box-drawing border with light grey bg
    border_h = "═" * cols
    out.write(f"{bg}{fg(border_color, _PAL)}╔{border_h}╗{RESET_FG}{RESET_BG}\n")
    for line in lines:
        out.write(f"{bg}{fg(border_color, _PAL)}║{RESET_FG}{line}{fg(border_color, _PAL)}║{RESET_FG}{RESET_BG}\n")
    out.write(f"{bg}{fg(border_color, _PAL)}╚{border_h}╝{RESET_FG}{RESET_BG}\n")

    # Legend
    legend_parts = [f"{player_char} you"]
    for loc_key, lchar, lcolor in sorted(
        {v for v in door_info.values()}, key=lambda x: x[0]
    ):
        legend_parts.append(f"{fg(lcolor, _PAL)}{lchar}{RESET} {loc_key}")
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
    rng: Rng,
    stdin=None,
):
    """Show a location's available options and run the one the player picks.

    Returns the (possibly new) state. Guard-denied options are excluded by
    ``available_options`` (KTD-8) and never listed. ``leave`` (and an empty choice)
    returns to the map without running anything.

    ``rng`` is the ONE session RNG constructed in :func:`play` (KTD-8: slice-local
    seeding contract, session-owned until the network transport lands) and threaded
    through every handler call for this location. Passing ``rng=None`` here is the
    root cause of the sph/waf crash this unit fixes — any handler that draws
    (``ctx.rng.range``/``ctx.rng.hit``) needs a real :class:`Rng`, not ``None``.
    """
    import sys as _sys
    if stdin is None:
        stdin = _sys.stdin
    from clients.terminal import hide_cursor, show_cursor
    from clients.terminal.ascii_art import location_art
    from clients.terminal.renderers import (
        render_header,
        render_body,
        render_menu_option,
        render_prompt,
        render_screen_clear,
    )

    if location_key not in _IMPLEMENTED_LOCATIONS:
        # U1 audit finding: kdh's doors (cells 221/753) already exist in city.yaml
        # ahead of U11's shell landing. Deny gracefully rather than let _load_shell's
        # FileNotFoundError crash the whole client — no state change, no move spent
        # beyond what try_move already charged for the door step.
        render_screen_clear(out)
        out.write(f"({location_key} is closed for renovations.)\n\n")
        out.flush()
        return state

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
    # Location ASCII art splash (if available)
    art = location_art(location_key)
    if art is not None:
        for line in art:
            out.write(f"{RESET}\n" if not line.strip() else f"{line}\n")
        out.write("\n  ENTER druecken...\n")
        out.flush()
        show_cursor(out)
        try:
            stdin.readline()
        finally:
            hide_cursor(out)
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
    try:
        raw = stdin.readline().strip()
    finally:
        hide_cursor(out)

    if not raw.isdigit() or not (0 <= int(raw) < len(options)):
        return state  # invalid / empty -> back to the map, no action run
    chosen = options[int(raw)]
    if chosen.id == "leave":
        return state

    result = run_option(
        shell, chosen.id, state, ln=ln, input_source=inp, rng=rng
    )
    render_result(result, out)
    return result.state  # adopt (run_option is pure)


def play(seed: int, players: list[tuple[str, str]] | None = None) -> None:
    """Play the default config from ``seed`` over real stdin/stdout.

    ``players`` is ``[(name, gang_name), ...]``, 1..4 entries (default: a single
    "alcapone" / "the outfit" player, unchanged from before this parameter existed).
    Multiple players hot-seat through ``advance_turn``'s rotation.

    Constructs exactly ONE session :class:`~engine.rng.Rng` from ``seed`` and threads
    it through every ``run_option`` call for the whole session (KTD-8: a slice-local
    seeding contract — ownership may move to the server/driver when the network
    transport lands, per the plan's Open Questions). Previously ``_run_location``
    passed ``rng=None``, which crashed any handler that draws (sph's gamble, waf's
    grenade/training rolls) the moment it was played through this client.
    """
    from clients.terminal import hide_cursor, show_cursor
    from clients.terminal import check_resize, install_sigwinch_handler
    from clients.terminal.ascii_art import title_screen

    out = sys.stdout
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cfg = load_game_config(_CONFIG_DIR)

    city_raw = yaml.safe_load(
        (_CONFIG_DIR / "content" / "map" / "city.yaml").read_text(encoding="utf-8")
    )
    city = load_city(city_raw)
    la_to_key = _door_location_map(city_raw)

    vehicles = cfg.module.load_vehicles(
        _CONFIG_DIR / cfg.config["entities"]["vehicles"]
    )

    state = cfg.module.new_game(
        seed=seed,
        end_year=1930,
        score_weight=1.0,
        players=players or [("alcapone", "the outfit")],
    )
    inp = TerminalInput(resolver=resolver, stdin=sys.stdin, stdout=out)
    rng = Rng(seed)  # the one session RNG (KTD-8) — threaded into every run_option call

    hide_cursor(out)
    try:
        install_sigwinch_handler()
        # Title screen
        out.write(CLEAR)
        out.write(title_screen())
        out.flush()
        show_cursor(out)
        try:
            sys.stdin.readline()
        finally:
            hide_cursor(out)

        note = "move: W/A/S/D into a door to enter. Q quits."
        while True:
            out.write(CLEAR)
            render_map(city, city_raw, state, out)
            out.write(f"{DIM}{note}{RESET}\n")
            out.flush()
            key = _read_key()
            if check_resize():
                note = " resized"
                continue
            if _is_quit(key):
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
                    state = _run_location(
                        key_for_la, payload.ln, state, resolver, inp, out, rng
                    )
            if getattr(payload, "turn_over", False):
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
                out.write(f"\n{DIM}press any key...{RESET}\n")
                out.flush()
                if _is_quit(_read_key()):
                    out.write("bye.\n")
                    return
                # advance_turn is pure — the rotated/replenished state must be adopted.
                state, _game_over = advance_turn(state, vehicles)
    finally:
        show_cursor(out)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="clients.terminal", description="Play the mafia slice.")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42).")
    parser.add_argument(
        "--player",
        dest="players",
        action="append",
        metavar="NAME:GANG",
        help=(
            "A player as 'name:gang'. Repeatable for up to 4 players (hot-seat, "
            "turn order = order given). Default: a single 'alcapone:the outfit'."
        ),
    )
    args = parser.parse_args(argv)
    players = None
    if args.players:
        players = []
        for spec in args.players:
            name, _, gang = spec.partition(":")
            players.append((name, gang or name))
    play(args.seed, players=players)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()

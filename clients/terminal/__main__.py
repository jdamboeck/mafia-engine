"""Runnable entry point: ``python -m clients.terminal`` — the playable terminal game.

Composes the client building blocks (:class:`TerminalInput`, the render helpers, and
the movement primitives) into a full game over the mafia_1920s config. It holds no
rules: movement goes through ``engine.movement.try_move``, location actions through
``engine.actions.run_option``, turn starts through ``engine.upkeep.run_upkeep``, and it
adopts the returned ``EngineResult.state`` after every step (all are pure).

A new game shows the title screen, then asks the two setup questions (end year, score
weight; ``mf-prg.bas:170-176``) unless given on the command line. One to four players
(``--player NAME:GANG``) take hot-seat turns:

    upkeep banner  ->  a job shift (employed player)  OR  a free turn on the map:
      walk with W/A/S/D  ->  press into a door to ENTER one of the five locations
      with a shell (slw/pub/sph/waf/kdh)  ->  pick a menu option (the driver runs the
      handler via TerminalInput)  ->  back on the map; the turn ends when ms hits 0
    ->  turn-over summary  ->  next player. After each round the standings show; when
        the end year is reached the year-end result shows and the game ends.

``p`` on the map saves (to ``--save PATH`` / the loaded file / ``mafia-save.jsonl``);
``--load PATH`` resumes a save; ``--watch-ai`` shows the board after every CPU combat
activation. ``q`` on the map or at a turn-over/standings prompt quits.

``--theme NAME|PATH`` words the session in another theme (a theme of the config, or a
theme directory), merged over ``classic``.

Run:  ``python -m clients.terminal [--seed N] [--player NAME:GANG ...] [--end-year Y]
      [--score-weight W] [--save PATH] [--watch-ai] [--theme NAME|PATH]``
      or  ``--load PATH``.

The headless end-to-end proof is ``tests/test_slice_integration.py``, which drives the
same protocol without a terminal.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import NoReturn

import yaml

from engine.actions import run_option
from engine.config_loader import load_config, load_game_config
from engine.effects import RankCommit
from engine.game_end import run_standings, run_year_end
from engine.interactions import ShowMessage
from engine.interactions import run as run_handler
from engine.locations import HANDLERS, available_options, load_location
from engine.movement import DOWN, LEFT, RIGHT, UP, advance_turn, load_city, try_move
from engine.persistence import SchemaVersionError, load_game, replay, save_game
from engine.rng import Rng
from engine.state import GameState
from engine.strings import Resolver
from engine.upkeep import run_upkeep

from clients.terminal import (
    CLEAR,
    CONFIG_DIR,
    DIM,
    RESET,
    EndOfInput,
    TerminalInput,
    check_resize,
    client_text,
    hide_cursor,
    install_sigwinch_handler,
    render_result,
    show_cursor,
)
from clients.terminal.ascii_art import location_art, title_screen
from clients.terminal.palette import C64_COLOR_NAMES, RESET_FG, fg, load_palette
from clients.terminal.renderers import (
    render_body,
    render_header,
    render_menu_option,
    render_prompt,
    render_screen_clear,
    render_status_bar_from_state,
)

_CONFIG_DIR = CONFIG_DIR

#: W/A/S/D -> movement deltas; Q (or empty) -> quit the turn. Case-insensitive.
_MOVE_KEYS = {"w": UP, "s": DOWN, "a": LEFT, "d": RIGHT}

#: The map screen's save key and the save target when neither ``--save`` nor
#: ``--load`` names one (relative, so it lands in the working directory).
_SAVE_KEY = "p"
_DEFAULT_SAVE = "mafia-save.jsonl"

#: The seed a NEW game uses when none is given. ``--seed`` defaults to ``None`` so a
#: load can tell "not given" from "given" (a load always uses the save's own seed).
_DEFAULT_SEED = 42

#: The theme a session is worded in when ``--theme`` is not given; every other theme
#: is merged over it.
_DEFAULT_THEME = "classic"


class LoadError(Exception):
    """A ``--load`` file could not be resumed; the message is the player-facing line.

    Raised by :func:`_load_session` for ANY failure while reading, replaying or
    re-seeding a save: a structurally corrupt save surfaces as ``ValueError``,
    ``KeyError`` or ``TypeError`` from deep in deserialization, so no narrow list of
    types would catch them all. :func:`main` turns it into one stderr line.
    """


def _load_reason(exc: BaseException, resolver: Resolver) -> str:
    """Plain words for why a save could not be loaded (client diagnostics, not game text)."""

    def reason(name: str, **params) -> str:
        return resolver.resolve(f"client.load.reason.{name}", params)

    if isinstance(exc, FileNotFoundError):
        return reason("not_found")
    if isinstance(exc, IsADirectoryError):
        return reason("is_directory")
    if isinstance(exc, OSError):
        return exc.strerror or str(exc)
    if isinstance(exc, json.JSONDecodeError):
        return reason("not_json", detail=exc.msg)
    if isinstance(exc, UnicodeDecodeError):
        return reason("not_text")
    if isinstance(exc, SchemaVersionError):
        return reason("bad_version", detail=exc)
    if isinstance(exc, KeyError):
        return reason("missing_field", detail=exc)
    return reason("corrupt", error_type=type(exc).__name__, detail=exc)


def _load_session(path: str | Path, resolver: Resolver) -> tuple[int, GameState, Rng]:
    """Resume a save: its seed, its state and the session RNG rebuilt mid-stream.

    The snapshot is authoritative (the effect log is saved empty), and the session RNG
    resumes mid-stream: re-issuing every logged draw leaves it exactly where
    uninterrupted play would be. A draw log that does not match the save's seed makes
    :meth:`Rng.replayed` raise ``ValueError`` -- a load failure like any other.
    Every failure becomes :class:`LoadError` with the theme's ``client.load.error`` line.
    """
    try:
        loaded = load_game(path)
        return loaded.seed, replay(loaded), Rng.replayed(loaded.seed, loaded.rng_log)
    except Exception as exc:
        message = resolver.resolve(
            "client.load.error", {"path": path, "reason": _load_reason(exc, resolver)}
        )
        raise LoadError(message) from exc


def _read_line_visible(stdin, out) -> str:
    """Read one line with the cursor shown, hiding it again afterwards.

    Returns ``readline()``'s raw result: ``""`` at EOF, ``"\\n"`` for a blank line.
    """
    show_cursor(out)
    try:
        return stdin.readline()
    finally:
        hide_cursor(out)


def _is_quit(key: str) -> bool:
    """The single quit vocabulary shared by every screen.

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
    32: " ",  #    space               — open / background (92 cells)
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


def _shell_path(location_key: str) -> Path:
    return _CONFIG_DIR / "content" / "locations" / f"{location_key}.yaml"


def _shell_exists(location_key: str) -> bool:
    """Whether this location has a shell yet.

    The map's door table runs ahead of the shells: city.yaml has doors for all
    twelve menu locations, but only some have a shell under ``content/locations/``,
    so walking into the others would otherwise crash on a missing file. Derived from
    disk rather than a hardcoded list so a new shell needs no edit here to become
    reachable.
    """
    return _shell_path(location_key).is_file()


def _load_shell(location_key: str):
    """Load a location shell by its key (e.g. ``slw``)."""
    return load_location(yaml.safe_load(_shell_path(location_key).read_text(encoding="utf-8")))


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


def render_map(city, city_raw: dict, state, out, resolver: Resolver) -> None:
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
        out.write(
            f"{bg}{fg(border_color, _PAL)}║{RESET_FG}{line}{fg(border_color, _PAL)}║{RESET_FG}{RESET_BG}\n"
        )
    out.write(f"{bg}{fg(border_color, _PAL)}╚{border_h}╝{RESET_FG}{RESET_BG}\n")

    # Legend
    legend_parts = [
        client_text("client.map.legend_you", {"player": player_char}, resolver=resolver)
    ]
    for loc_key, lchar, lcolor in sorted({v for v in door_info.values()}, key=lambda x: x[0]):
        legend_parts.append(f"{fg(lcolor, _PAL)}{lchar}{RESET} {loc_key}")
    out.write("   ".join(legend_parts) + "\n")

    # Status bar at bottom
    render_status_bar_from_state(state, out, resolver)


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
    ``available_options`` and never listed. ``leave`` (and an empty choice)
    returns to the map without running anything.

    ``rng`` is the ONE session RNG constructed in :func:`play` (session-owned; a
    network transport may move that ownership to the server) and threaded
    through every handler call for this location. Any handler that draws
    (``ctx.rng.range``/``ctx.rng.hit``) needs a real :class:`Rng`, not ``None``.
    """
    if stdin is None:
        stdin = sys.stdin

    if not _shell_exists(location_key):
        # No state change, and no move spent beyond try_move's door-step charge.
        render_screen_clear(out)
        out.write(resolver.resolve("client.location.closed", {"location": location_key}) + "\n\n")
        out.flush()
        return state

    shell = _load_shell(location_key)
    options = available_options(shell, state, ln)
    if not options:
        out.write(resolver.resolve("client.location.nothing_to_do") + "\n")
        return state

    # Entry prompt sets the scene
    try:
        entry_text = resolver.resolve(f"locations.{location_key}.entry_prompt")
    except Exception:
        entry_text = resolver.resolve("client.location.entry_fallback", {"location": location_key})

    # --- render location screen ---
    render_screen_clear(out)
    # Location ASCII art splash (if available)
    art = location_art(location_key)
    if art is not None:
        for line in art:
            out.write(f"{RESET}\n" if not line.strip() else f"{line}\n")
        out.write(f"\n  {resolver.resolve('client.location.press_enter')}\n")
        out.flush()
        _read_line_visible(stdin, out)
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

    raw = _read_line_visible(stdin, out).strip()

    if not raw.isdigit() or not (0 <= int(raw) < len(options)):
        return state  # invalid / empty -> back to the map, no action run
    chosen = options[int(raw)]
    if chosen.id == "leave":
        return state

    result = run_option(shell, chosen.id, state, ln=ln, input_source=inp, rng=rng)
    render_result(result, out, resolver)
    return result.state  # adopt (run_option is pure)


def _run_upkeep_screen(state, resolver: Resolver, out, rng: Rng, stdin=None, inp=None):
    """Run the active player's turn-start upkeep and show its banner/promotion.

    Calls :func:`engine.upkeep.run_upkeep` — THE engine-level turn-start entry point —
    so the client never decides for itself whether upkeep runs; it only renders what
    already happened. The turn banner always shows; the rank-promotion "wanted poster"
    screen shows only when the committed effects contain a :class:`RankCommit` (the
    handler's own ``rank != nr`` gate, mirrored here rather than re-derived, so the
    client stays a thin renderer over the driver's decision).

    Blocks for one keypress after the banner/promotion (mirrors the turn-over prompt's
    "press any key..." pattern) so a human has time to read it; EOF is treated as an
    ack, not a quit, since upkeep offers no cancel path — the
    turn must proceed regardless.

    ``inp`` is the session's :class:`TerminalInput`, forwarded to ``run_upkeep`` so the
    debt-default collectors fight (``mf-prg.bas:4350``) can read real combat input.
    Every other upkeep step yields only auto-acked ``ShowMessage`` screens and never
    consults it. This does not reopen a cancel path: combat prompts are
    non-cancellable, so a quit during the fight surrenders — losing it, and
    triggering the seizure — rather than escaping upkeep.
    """
    if stdin is None:
        stdin = sys.stdin

    result = run_upkeep(state, input_source=inp, rng=rng)
    new_state = result.state  # adopt (run_upkeep is pure)
    assert new_state is not None, "run_upkeep was given a state, so it returns one"
    active = new_state.players[new_state.clock.active_player]

    render_screen_clear(out)
    render_header(resolver.resolve("client.header.upkeep"), out)
    render_body(resolver.resolve("upkeep.turn_banner", {"name": active.name}), out)

    promoted = next((e for e in result.effects if isinstance(e, RankCommit)), None)
    if promoted is not None:
        cfg = load_game_config(_CONFIG_DIR)
        ranks = cfg.module.load_ranks(_CONFIG_DIR / cfg.config["entities"]["ranks"])
        out.write("\n")
        render_body(
            resolver.resolve(
                "upkeep.rank_promotion",
                {
                    "gang_name": active.gang_name,
                    "name": active.name,
                    "score": active.gf,
                    "rank_name": ranks[active.rank - 1],
                },
            ),
            out,
        )

    _write_press_any_key(resolver, out)
    _read_line_visible(stdin, out)
    return new_state


def _run_job_shift_screen(state, resolver: Resolver, inp: TerminalInput, out, rng: Rng):
    """Run the active player's job shift (the turn-start job-shift seam) and render it.

    Dispatched by the CALLER (:func:`play`'s turn loop), right after upkeep, in place
    of the free turn -- an employed player never reaches the map/menu this turn
    (``data/game_configs/mafia_1920s/handlers/jobs.py``'s ``job_shift``, registered
    under ``"job.shift"`` in the SAME :data:`engine.locations.HANDLERS` registry a
    location option's handler string resolves against). Drives the generator via
    :func:`engine.interactions.run` directly (there is no location shell/guard layer
    for a shift, unlike :func:`_run_location`), sharing the ONE session RNG and the
    real terminal ``inp`` so the shift's ``StartCombat`` fights render exactly like
    any other fight.
    """
    render_screen_clear(out)
    render_header(resolver.resolve("client.header.job"), out)
    result = run_handler(HANDLERS["job.shift"], inp, state=state, rng=rng)
    return result.state  # adopt (run is pure)


def _run_game_end_screen(runner, header: str, state, resolver: Resolver, out, rng: Rng) -> bool:
    """Run a display-only game-end flow (standings or year-end) and show it as ONE screen.

    ``runner`` is :func:`engine.game_end.run_standings` or
    :func:`engine.game_end.run_year_end`; WHICH state it gets is the caller's
    decision. The runner's input source collects every ``ShowMessage`` the
    config handler yields (one per row -- templates cannot iterate); each is resolved
    through the theme and rendered in order under one header, then the screen waits
    for one key like the turn-over prompt. Any other interaction raises, mirroring the
    engine runners' own display-only contract, so a handler that starts asking
    questions fails loudly instead of being answered here.

    Returns ``False`` when that key is a quit (``q`` or EOF, :func:`_is_quit`), so the
    caller ends the session; ``True`` otherwise.
    """
    messages: list[ShowMessage] = []

    def collect(interaction):
        if not isinstance(interaction, ShowMessage):
            raise AssertionError(f"game-end flow asked a question: {interaction!r}")
        messages.append(interaction)

    runner(state, input_source=collect, rng=rng)

    render_screen_clear(out)
    render_header(header, out)
    render_body("\n".join(resolver.resolve(m.key, m.params) for m in messages), out)
    _write_press_any_key(resolver, out)
    return not _is_quit(_read_key())


def _write_press_any_key(resolver: Resolver, out) -> None:
    """Write the dimmed "press any key" line that ends an acknowledge-only screen."""
    out.write(f"\n{DIM}{resolver.resolve('client.press_any_key')}{RESET}\n")
    out.flush()


def _in_range(value: float, bounds: dict) -> bool:
    """``bounds`` is one ``input_ranges`` entry (``{min, max}``) from config.yaml."""
    return bounds["min"] <= value <= bounds["max"]


def _parse_setup_number(text: str, *, integer: bool) -> float | None:
    """Parse one setup answer; ``None`` for non-numeric input (the caller re-asks).

    The end year is ``int(val(x$))`` in the source (mf-prg.bas:170) -- truncated to an
    integer; the score weight is ``val(x$)`` (:175), a decimal such as ``0.5``.
    """
    try:
        value = float(text.strip())
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if integer else value


def _prompt_setup_value(key: str, bounds: dict, *, integer: bool, resolver, out, stdin):
    """Ask one setup question until the answer is inside ``bounds``.

    Mirrors the source's re-ask loops (mf-prg.bas:170/172 for the end year, :175/:176
    for the score weight): out-of-range or non-numeric input asks again. Real EOF (not a
    blank line) can never be answered, so it raises :class:`EndOfInput` -- ``play()``
    ends the session on it exactly like at a handler prompt.
    """
    while True:
        render_screen_clear(out)
        out.write(f"{resolver.resolve(key)} ")
        out.flush()
        line = _read_line_visible(stdin, out)
        if line == "":
            raise EndOfInput
        value = _parse_setup_number(line, integer=integer)
        if value is not None and _in_range(value, bounds):
            return value


class TerminalSession:
    """One terminal play session: what :func:`play` builds, and one method per phase.

    ``sys.stdin``/``sys.stdout`` are read when the session is built (the session's
    :class:`TerminalInput` keeps that ``stdin``), so a caller replacing them must do so
    first. ``resolver`` is the theme every screen is worded in (default: the config's
    ``classic`` theme).
    """

    def __init__(
        self,
        *,
        seed: int | None,
        players: list[tuple[str, str]] | None,
        end_year: int | None,
        score_weight: float | None,
        load: str | Path | None,
        save: str | Path | None,
        watch_ai: bool,
        resolver: Resolver | None = None,
    ) -> None:
        self.out = sys.stdout
        self.state = None
        self.players = players
        self.end_year = end_year
        self.score_weight = score_weight
        self.loaded = load is not None
        self.resolver = (
            resolver
            if resolver is not None
            else Resolver.from_config(_CONFIG_DIR, theme=_DEFAULT_THEME)
        )
        self.cfg = load_game_config(_CONFIG_DIR)

        self.city_raw = yaml.safe_load(
            (_CONFIG_DIR / "content" / "map" / "city.yaml").read_text(encoding="utf-8")
        )
        self.city = load_city(self.city_raw)
        self.la_to_key = _door_location_map(self.city_raw)

        cfg = self.cfg
        self.vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        self.weapon_names = [
            w["name"]
            for w in cfg.module.load_weapons(_CONFIG_DIR / cfg.config["entities"]["weapons"])
        ]

        self.ranges = cfg.config["input_ranges"]
        self.inp = TerminalInput(
            resolver=self.resolver,
            stdin=sys.stdin,
            stdout=self.out,
            weapon_names=self.weapon_names,
            observe_ai=watch_ai,
        )
        if load is not None:
            # raises LoadError; main() reports it
            self.seed, self.state, self.rng = _load_session(load, self.resolver)
        else:
            self.seed = seed if seed is not None else _DEFAULT_SEED
            # the one session RNG — threaded into every run_option call
            self.rng = Rng(self.seed)
        self.save_path = Path(
            save if save is not None else load if load is not None else _DEFAULT_SAVE
        )
        self.note = self.text("client.map.hint")

    def text(self, key: str, params: dict | None = None) -> str:
        """Resolve a theme key through this session's resolver."""
        return self.resolver.resolve(key, params)

    def run(self) -> tuple:
        """Run the session to its end; return the final ``(state, rng)``."""
        out = self.out
        hide_cursor(out)
        try:
            install_sigwinch_handler()
            if self.loaded:
                self.resume_loaded_game()
            else:
                self.start_new_game()
        except EndOfInput:
            # stdin ran out at a handler prompt. The in-flight handler never
            # returned, so its EngineResult -- and every effect it would have
            # committed -- is never adopted; end the session exactly like a quit.
            out.write(self.text("client.bye") + "\n")
        finally:
            show_cursor(out)
        return self.state, self.rng

    def start_new_game(self) -> None:
        """Title screen, setup prompts, the new game and its first upkeep, then the turns."""
        out, resolver = self.out, self.resolver
        # Title screen
        out.write(CLEAR)
        out.write(title_screen())
        out.flush()
        _read_line_visible(sys.stdin, out)

        # Setup (mf-prg.bas:170-176): ask only for what the caller did not supply.
        end_year, score_weight = self.end_year, self.score_weight
        if end_year is None:
            end_year = _prompt_setup_value(
                "setup.end_year_prompt",
                self.ranges["end_year"],
                integer=True,
                resolver=resolver,
                out=out,
                stdin=sys.stdin,
            )
        if score_weight is None:
            score_weight = _prompt_setup_value(
                "setup.score_weight_prompt",
                self.ranges["score_weight"],
                integer=False,
                resolver=resolver,
                out=out,
                stdin=sys.stdin,
            )
        # new_game validates both against input_ranges and stores the weight as
        # Config.score_mult -- nothing here sets the config directly.
        self.state = self.cfg.module.new_game(
            seed=self.seed,
            end_year=end_year,
            score_weight=score_weight,
            players=self.players or [("alcapone", "the outfit")],
        )

        # The engine owns the coupling — upkeep runs at EVERY turn start,
        # including the very first (before the map loop's first render), so no path
        # through this client can reach a free turn without it. Later turns run it
        # right after advance_turn rotates (in next_turn), at the exact same seam.
        self.state = _run_upkeep_screen(self.state, resolver, out, self.rng, inp=self.inp)
        self.run_turns(resuming_free_turn=False)

    def resume_loaded_game(self) -> None:
        """Enter the turns of a loaded game: no title, no setup, no upkeep."""
        # A loaded game skips all of that: it was set up, and this turn's
        # upkeep already ran, before the save.
        # A save is only ever taken on the map during a free turn -- including the rest
        # of the turn in which a job was just accepted (ms=0, job already set). So the
        # first iteration after a load resumes that free turn; the shift belongs to the
        # NEXT turn start, exactly as in uninterrupted play.
        self.run_turns(resuming_free_turn=True)

    def run_turns(self, *, resuming_free_turn: bool) -> None:
        """The turn loop: a job shift or a map turn, then turn-over, until the session ends."""
        while True:
            # Job-shift seam: an EMPLOYED player never reaches the map/menu this
            # turn -- the shift flow replaces the free turn entirely (mirrors the
            # source's :1012 dispatch). Checked fresh every turn start, right after
            # upkeep (in start_new_game on the first turn, in next_turn on later ones).
            assert self.state is not None, "state is set by setup or load before any turn"
            active = self.state.players[self.state.clock.active_player]
            if active.jobs.type and not resuming_free_turn:
                self.job_shift()
            else:
                resuming_free_turn = False
                if not self.map_turn():
                    return
            if not self.turn_over():
                return
            if not self.next_turn():
                return

    def job_shift(self) -> None:
        """Run the employed active player's job shift in place of the free turn."""
        self.state = _run_job_shift_screen(self.state, self.resolver, self.inp, self.out, self.rng)

    def map_turn(self) -> bool:
        """Play the free turn on the map until ``ms`` runs out; ``False`` on a quit."""
        out = self.out
        while True:
            out.write(CLEAR)
            render_map(self.city, self.city_raw, self.state, out, self.resolver)
            out.write(f"{DIM}{self.note}{RESET}\n")
            out.flush()
            key = _read_key()
            if check_resize():
                self.note = self.text("client.map.resized")
                continue
            if _is_quit(key):
                out.write(self.text("client.bye") + "\n")
                return False

            if key == _SAVE_KEY:
                self.save()
                continue

            delta = _MOVE_KEYS.get(key)
            if delta is None:
                self.note = self.text("client.map.bad_key")
                continue

            assert self.state is not None, "state is set by setup or load before any turn"
            result = try_move(self.state, self.city, delta)
            self.state = result.state
            payload = result.payload
            kind = getattr(payload, "kind", None)
            note_key = {"wall": "client.map.wall", "oob": "client.map.edge"}.get(
                kind or "", "client.map.hint"
            )
            self.note = self.text(note_key)
            if kind == "enter":
                key_for_la = self.la_to_key.get(payload.la)
                if key_for_la is not None:
                    self.state = _run_location(
                        key_for_la, payload.ln, self.state, self.resolver, self.inp, out, self.rng
                    )
            if getattr(payload, "turn_over", False):
                return True

    def save(self) -> None:
        """Save the game from the map (``p``) and set the map note to the outcome."""
        # A map-turn save -- the snapshot is authoritative, so
        # the effect log is empty; the RNG log lets a load resume the
        # stream mid-way. Overwrites without asking.
        # A failed save (missing directory, full disk, no permission)
        # must never end the game: say so and keep playing.
        assert self.state is not None, "state is set by setup or load before any turn"
        try:
            save_game(
                self.save_path, self.state, effect_log=[], rng_log=self.rng.log, seed=self.seed
            )
        except OSError as exc:
            reason = exc.strerror or str(exc)
            self.note = self.resolver.resolve("session.save_failed", {"reason": reason})
        else:
            self.note = self.resolver.resolve("session.saved", {"path": self.save_path})

    def turn_over(self) -> bool:
        """Show the turn-over summary and wait for a key; ``False`` on a quit."""
        out = self.out
        assert self.state is not None, "state is set by setup or load before any turn"
        p = self.state.players[self.state.clock.active_player]
        render_screen_clear(out)
        render_header(self.text("client.header.turn_over"), out)
        render_body(
            self.text(
                "client.turn_over.summary",
                {
                    "cash": p.ka,
                    "position": p.po,
                    "movement": p.ms,
                    "rank": p.rank,
                    "jail_months": p.wanted.jail_months,
                },
            ),
            out,
        )
        _write_press_any_key(self.resolver, out)
        if _is_quit(_read_key()):
            out.write(self.text("client.bye") + "\n")
            return False
        return True

    def next_turn(self) -> bool:
        """Advance to the next turn (standings, ending, upkeep); ``False`` ends the session."""
        assert self.state is not None, "state is set by setup or load before any turn"
        played = self.state  # the round just finished, for the standings
        # advance_turn is pure — the rotated/replenished state must be adopted.
        self.state, game_over = advance_turn(self.state, self.vehicles)
        # :1010 — on a round wrap (back to player 0) gosub4500 shows the standings
        # BEFORE ja=ja+1/12, so they get the pre-advance state: the date shown is
        # the round just played.
        if self.state.clock.active_player == 0:
            if not self.round_end(played):
                return False
        if game_over:
            self.ending()
            return False
        # Upkeep for the NEW active player, right at the turn-start seam
        # advance_turn just opened — before this player's free turn (or job
        # shift) is offered.
        self.state = _run_upkeep_screen(self.state, self.resolver, self.out, self.rng, inp=self.inp)
        return True

    def round_end(self, played) -> bool:
        """Show the standings for ``played``, the round just finished; ``False`` on a quit."""
        if not _run_game_end_screen(
            run_standings,
            self.text("client.header.standings"),
            played,
            self.resolver,
            self.out,
            self.rng,
        ):
            self.out.write(self.text("client.bye") + "\n")
            return False
        return True

    def ending(self) -> None:
        """Show the year-end result; the game ends here."""
        # :40100 — the year-end result (standings again, then winner/tie) on the
        # POST-advance state; the game ends here, so no upkeep and no new turn.
        _run_game_end_screen(
            run_year_end,
            self.text("client.header.game_over"),
            self.state,
            self.resolver,
            self.out,
            self.rng,
        )


def play(
    seed: int | None = None,
    players: list[tuple[str, str]] | None = None,
    *,
    end_year: int | None = None,
    score_weight: float | None = None,
    load: str | Path | None = None,
    save: str | Path | None = None,
    watch_ai: bool = False,
    resolver: Resolver | None = None,
) -> tuple:
    """Play the default config over real stdin/stdout; return the final ``(state, rng)``.

    ``seed`` seeds the ONE session :class:`~engine.rng.Rng` (default 42). ``players`` is
    ``[(name, gang_name), ...]``, 1..4 hot-seat players (default: one "alcapone" /
    "the outfit"). ``end_year`` / ``score_weight`` (x9, x8) left ``None`` are prompted
    for after the title screen (``mf-prg.bas:170-176``); a supplied value skips its prompt.

    ``load`` resumes a save: its state, seed and RNG draw log, straight into the saved
    player's map turn (no title, setup or upkeep); the new-game inputs are ignored.
    ``p`` on the map saves to ``save``, else the loaded file, else ``mafia-save.jsonl``.
    ``watch_ai`` shows the board after every CPU combat activation (off, as in the original).
    ``resolver`` is the theme the session is worded in (default: the ``classic`` theme).

    Returns on EVERY exit -- quit, EOF, or the year-end ending. ``state`` is ``None``
    only if the session ends before setup finished.
    """
    session = TerminalSession(
        seed=seed,
        players=players,
        end_year=end_year,
        score_weight=score_weight,
        load=load,
        save=save,
        watch_ai=watch_ai,
        resolver=resolver,
    )
    return session.run()


def main(argv: list[str] | None = None) -> None:
    # Every line main() prints comes from the theme, so it is loaded first. Without
    # it there are no words to phrase the failure in: the error's own text is shown.
    try:
        resolver = Resolver.from_config(_CONFIG_DIR, theme=_DEFAULT_THEME)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        _die(str(exc))

    def text(key: str, **params) -> str:
        return resolver.resolve(f"client.cli.{key}", params)

    parser = argparse.ArgumentParser(prog="clients.terminal", description=text("description"))
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=text("help_seed", seed=_DEFAULT_SEED),
    )
    parser.add_argument(
        "--player",
        dest="players",
        action="append",
        metavar="NAME:GANG",
        help=text("help_player"),
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help=text("help_end_year"),
    )
    parser.add_argument(
        "--score-weight",
        type=float,
        default=None,
        help=text("help_score_weight"),
    )
    parser.add_argument(
        "--load",
        metavar="PATH",
        default=None,
        help=text("help_load"),
    )
    parser.add_argument(
        "--save",
        metavar="PATH",
        default=None,
        help=text("help_save", save=_DEFAULT_SAVE),
    )
    parser.add_argument(
        "--watch-ai",
        action="store_true",
        help=text("help_watch_ai"),
    )
    parser.add_argument(
        "--theme",
        metavar="NAME|PATH",
        default=_DEFAULT_THEME,
        help=text("help_theme", theme=_DEFAULT_THEME),
    )
    args = parser.parse_args(argv)
    # From here on every line is worded in the chosen theme. An unknown or broken
    # theme is a known failure: one line, not a traceback.
    try:
        resolver = _load_theme(args.theme, resolver)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        _die(text("theme_error", theme=args.theme, error=exc))
    if args.load is not None:
        # A save carries its own seed and setup; a new-game flag beside --load
        # would be silently ignored, so it is refused instead.
        clashing = [
            flag
            for flag, value in (
                ("--seed", args.seed),
                ("--player", args.players),
                ("--end-year", args.end_year),
                ("--score-weight", args.score_weight),
            )
            if value is not None
        ]
        if clashing:
            parser.error(text("load_clash", flags=", ".join(clashing)))
    # Same bounds as the setup prompts: input_ranges in config.yaml, never hardcoded.
    # A broken config dir (missing, malformed YAML, failed validation) is a known
    # failure: one line, not a traceback. Only config.yaml is read here;
    # play() does the one full load_game_config (handlers + setup module).
    try:
        ranges = load_config(_CONFIG_DIR / "config.yaml")["input_ranges"]
    except (OSError, yaml.YAMLError, ValueError, KeyError) as exc:
        _die(text("config_error", config_dir=_CONFIG_DIR, error=exc))
    for flag, value, bounds in (
        ("--end-year", args.end_year, ranges["end_year"]),
        ("--score-weight", args.score_weight, ranges["score_weight"]),
    ):
        if value is not None and not _in_range(value, bounds):
            parser.error(
                text("out_of_range", flag=flag, min=bounds["min"], max=bounds["max"], value=value)
            )
    players = None
    if args.players:
        players = []
        for spec in args.players:
            name, _, gang = spec.partition(":")
            players.append((name, gang or name))
    # Only KNOWN failures are caught here. A LoadError is raised before play()
    # draws anything; KeyboardInterrupt unwinds through play()'s finally (which shows
    # the cursor again) and exits quietly. Anything else is a bug and keeps its
    # traceback.
    try:
        play(
            seed=args.seed,
            players=players,
            end_year=args.end_year,
            score_weight=args.score_weight,
            load=args.load,
            save=args.save,
            watch_ai=args.watch_ai,
            resolver=resolver,
        )
    except LoadError as exc:
        _die(str(exc))
    except KeyboardInterrupt:
        sys.exit(130)  # 128 + SIGINT, the shell convention; no traceback, no message


def _load_theme(value: str, classic: Resolver) -> Resolver:
    """The resolver for ``--theme VALUE``: that theme deep-merged over ``classic``.

    A value containing a path separator, or naming an existing directory, is a theme
    directory (:meth:`Resolver.from_directory`); anything else names a theme of the
    game config (``themes/<name>``). Merging over ``classic`` lets a theme reword a
    few keys without restating the rest. Raises ``OSError``, ``yaml.YAMLError`` or
    ``ValueError`` when the theme cannot be loaded.
    """
    separators = [sep for sep in (os.sep, os.altsep) if sep]
    if any(sep in value for sep in separators) or Path(value).is_dir():
        theme = Resolver.from_directory(value)
    elif value == _DEFAULT_THEME:
        return classic
    else:
        theme = Resolver.from_config(_CONFIG_DIR, theme=value)
    return classic.with_override(theme.tree)


def _die(message: str) -> NoReturn:
    """Print one readable line to stderr and exit non-zero."""
    print(message, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()

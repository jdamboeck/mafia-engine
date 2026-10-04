"""The terminal play session: :class:`TerminalSession`, :func:`play` and their screens.

Composes the client building blocks (:class:`TerminalInput`, the render helpers, and
the movement primitives) into a full game over the mafia_1920s config. It holds no
rules and no turn order: the engine turn runner (:class:`engine.turns.TurnRunner`)
owns the order of every turn -- next player, standings and the year-end check on a
wrap, upkeep, the config's turn-start hooks, a job shift or the free turn and its map
steps and location visits, the turn-over -- and this session renders what the runner
yields (its acknowledgement screens, the map-move prompt, the location menu, its
hooks' and handlers' prompts, narration and fights) and adopts the runner's state at
every interaction.

Each turn opens the turn menu (``mf-prg.bas:1015-1050``): its number keys pick an
option (the overview, walking the map, the next player). On the map-move prompt W/A/S/D
answer a direction; pressing into a door enters the location, whose menu the runner
offers next; ``m`` leaves the map for the turn menu (the source's exit key, ``:2019``).

A new game shows the title screen, then draws the config's setup handler: the end year
and score weight (``mf-prg.bas:170-176``), the optional house-rules step (skipped by
default; not shown when the config's catalogue offers no switch), the player count and
each player's name and gang name (``:205-215``), and each player's eigenschaften screen,
where the player stops each stat's roll with a key (``:300-360``). What the caller
supplied is not asked. The session holds no setup rule: it draws the handler's prompts,
roll frames and screens. One to four players take hot-seat turns.

``p`` at the turn menu or on the map saves (to the ``save`` path / the loaded file /
``mafia-save.jsonl``); a ``load`` resumes a save (:func:`_load_session`) in the phase it
recorded. ``q`` at the turn menu, on the map or at a turn-over/standings prompt quits. The command line lives in
:mod:`clients.terminal.cli`.

The headless end-to-end proof is ``tests/test_slice_integration.py``, which drives the
same protocol without a terminal.
"""

from __future__ import annotations

import functools
import json
import os
import select
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from engine.conditions import build_context, evaluate
from engine.config_loader import load_game_config
from engine.interactions import (
    MAP_EXIT,
    MAP_QUIT,
    MAP_SAVE,
    Acknowledge,
    CombatScreen,
    Heading,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptText,
    RollFrame,
    ShowMessage,
    TurnMenu,
    step,
)
from engine.persistence import (
    MissingHouseRulesError,
    Registries,
    SaveConfigError,
    SchemaVersionError,
    load_game,
    replay,
    save_game,
)
from engine.rng import Rng
from engine.state import GameState
from engine.strings import Resolver
from engine.turns import (
    JOB_SHIFT_SCREEN,
    LOCATION_CLOSED_SCREEN,
    SETUP_HANDLER_KEY,
    STANDINGS_SCREEN,
    TURN_OVER_SCREEN,
    UPKEEP,
    UPKEEP_SCREEN,
    YEAR_END_SCREEN,
    TurnRunner,
)

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
    show_cursor,
)
from clients.terminal.ascii_art import location_art, title_screen
from clients.terminal.palette import C64_COLOR_NAMES, RESET_FG, Colors, Palette, load_palette
from clients.terminal.renderers import (
    render_body,
    render_header,
    render_menu_option,
    render_prompt,
    render_screen_clear,
    render_status_bar_from_state,
)

_CONFIG_DIR = CONFIG_DIR

#: W/A/S/D -> the map-move prompt's directions; Q (or empty) -> quit. Case-insensitive.
_MOVE_KEYS = {"w": "up", "s": "down", "a": "left", "d": "right"}

#: A map-move outcome -> the map note that reports it (any other outcome: the hint).
_OUTCOME_NOTES = {"wall": "client.map.wall", "oob": "client.map.edge"}

#: The map's exit key back to the turn menu (the source's ``_``, ``mf-prg.bas:2019``).
_EXIT_KEY = "m"

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


#: What :meth:`TerminalSession.render` returns when the player quit at that screen.
_QUIT = object()


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
        if exc.older:
            return reason("older_version", found=exc.found, supported=exc.supported)
        return reason("bad_version", detail=exc)
    if isinstance(exc, SaveConfigError):
        return reason(f"other_{exc.field}", found=exc.found, expected=exc.expected)
    if isinstance(exc, MissingHouseRulesError):
        return reason("no_house_rules")
    if isinstance(exc, _HouseRulesMismatch):
        return reason("other_house_rules", detail=exc)
    if isinstance(exc, KeyError):
        return reason("missing_field", detail=exc)
    return reason("corrupt", error_type=type(exc).__name__, detail=exc)


class _HouseRulesMismatch(Exception):
    """A save's house-rules map does not hold exactly the catalogue's switches."""


def _load_session(
    path: str | Path,
    resolver: Resolver,
    registries: Registries,
    check_house_rules: Callable[[dict], object] | None = None,
) -> tuple[int, GameState, Rng]:
    """Resume a save: its seed, its state and the session RNG rebuilt mid-stream.

    The snapshot is authoritative (the effect log is saved empty), and the session RNG
    resumes mid-stream: re-issuing every logged draw leaves it exactly where
    uninterrupted play would be. A draw log that does not match the save's seed makes
    :meth:`Rng.replayed` raise ``ValueError`` -- a load failure like any other.
    Every failure becomes :class:`LoadError` with the theme's ``client.load.error`` line.
    ``registries`` is the loaded config's: effects and value maps load through it.
    ``check_house_rules`` is the config's check of a stored house-rules map (it raises
    ``ValueError`` naming the rule): a save made under another catalogue is refused.
    """
    try:
        loaded = load_game(path, registries)
        if check_house_rules is not None:
            try:
                check_house_rules(dict(loaded.state.config.house_rules))
            except ValueError as exc:
                raise _HouseRulesMismatch(str(exc)) from exc
        return loaded.seed, replay(loaded, registries), Rng.replayed(loaded.seed, loaded.rng_log)
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


def render_map(city, city_raw: dict, state, out, resolver: Resolver, colors: Colors) -> None:
    """Draw the 40x25 city with per-cell colors from the C64 color RAM.

    Uses ``city.color(cell)`` for each cell's foreground color, giving the full
    16-color variety of the original: grey streets, red buildings, green parks,
    blue water, brown rail, etc.

    Read-only view built straight off ``City`` + the door table — no rules, no mutation.
    Cell index is row-major (``cell = row*cols + col``), matching ``try_move``'s math.
    """
    from clients.terminal.palette import RESET_BG

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

    # Special cell lookup: an event cell is drawn only while it is armed for the
    # active player -- its ``armed`` guard (the config's city data) holds against the
    # state, as the source pokes it off the street code (``:2002``/``:2003``). The
    # armed state is derived here each time, never stored; an unarmed cell is drawn as
    # the street it is.
    special_cfg = _MAP_CFG.get("special_cells", {})
    guard_context = build_context(state)
    armed = {
        spec["cell"]
        for spec in city_raw.get("special_cells", [])
        if evaluate(spec.get("armed"), guard_context)
    }

    # Config values
    player_char = _MAP_CFG.get("player_char", "@")
    bg_color = _MAP_CFG.get("bg_color", "light_grey")
    border_color = "dark_grey"

    # Light grey background for the entire map
    bg = colors.bg(bg_color)

    lines: list[str] = []
    for r in range(rows):
        chars: list[str] = []
        for c in range(cols):
            cell = r * cols + c
            if cell == po:
                chars.append(f"{colors.fg('red')}{player_char}")
            elif cell in door_info:
                _, dchar, dcolor = door_info[cell]
                chars.append(f"{colors.fg(dcolor)}{dchar}")
            elif cell in armed and cell in special_cfg:
                scfg = special_cfg[cell]
                chars.append(f"{colors.fg(scfg.get('color', 'white'))}{scfg['char']}")
            else:
                # Use C64 color RAM for foreground color
                c64_color_idx = city.color(cell)
                color_name = C64_COLOR_NAMES[c64_color_idx]
                code = city.code(cell)
                char = _CODE_TO_CHAR.get(code, "\u00b7")
                chars.append(f"{colors.fg(color_name)}{char}")
        lines.append("".join(chars) + RESET_FG)

    # Draw box-drawing border with light grey bg
    border_h = "═" * cols
    out.write(f"{bg}{colors.fg(border_color)}╔{border_h}╗{RESET_FG}{RESET_BG}\n")
    for line in lines:
        out.write(
            f"{bg}{colors.fg(border_color)}║{RESET_FG}{line}{colors.fg(border_color)}║{RESET_FG}{RESET_BG}\n"
        )
    out.write(f"{bg}{colors.fg(border_color)}╚{border_h}╝{RESET_FG}{RESET_BG}\n")

    # Legend
    legend_parts = [
        client_text("client.map.legend_you", {"player": player_char}, resolver=resolver)
    ]
    for loc_key, lchar, lcolor in sorted({v for v in door_info.values()}, key=lambda x: x[0]):
        legend_parts.append(f"{colors.fg(lcolor)}{lchar}{RESET} {loc_key}")
    out.write("   ".join(legend_parts) + "\n")

    # Status bar at bottom
    render_status_bar_from_state(state, out, resolver, colors)


def _render_location_menu(
    menu: LocationMenu, resolver: Resolver, colors: Colors, out, stdin=None
) -> Any:
    """Show a location's menu (the runner's :class:`LocationMenu`); return the pick.

    Returns the chosen 0-based index. A blank line or a key that is not an offered
    option is ignored and the prompt waits again, as the runner does
    (``mf-prg.bas:3040``): only the shell's own leave option leaves. EOF returns
    ``_QUIT`` (the session ends). With no options there is nothing to pick: ``None``.
    """
    if stdin is None:
        stdin = sys.stdin
    location_key = menu.location
    if not menu.options:
        out.write(resolver.resolve("client.location.nothing_to_do") + "\n")
        return None

    # Entry prompt sets the scene
    try:
        entry_text = resolver.resolve(f"locations.{location_key}.entry_prompt")
    except Exception:
        entry_text = resolver.resolve("client.location.entry_fallback", {"location": location_key})

    # --- render location screen ---
    render_screen_clear(out)
    # Location ASCII art splash (if available): the location's picture
    # (:3007 `syslh,lk$(la)+"-pic"`), drawn for the terminal.
    art = location_art(location_key)
    if art is not None:
        for line in art:
            out.write(f"{RESET}\n" if not line.strip() else f"{line}\n")
        out.write(f"\n  {resolver.resolve('client.location.press_enter')}\n")
        out.flush()
        _read_line_visible(stdin, out)
        render_screen_clear(out)
    render_header(location_key, out, colors)
    render_body(entry_text, out, colors)
    out.write("\n")
    for i, option_id in enumerate(menu.options):
        try:
            label = resolver.resolve(f"locations.{location_key}.menu.{option_id}")
        except Exception:
            label = option_id
        render_menu_option(i, label, out, colors)
    while True:
        render_prompt(out)
        out.flush()
        line = _read_line_visible(stdin, out)
        if line == "":  # EOF
            return _QUIT
        raw = line.strip()
        if raw.isdigit() and int(raw) < len(menu.options):
            return int(raw)


def _render_lines_screen(header: str, lines, resolver: Resolver, colors: Colors, out) -> bool:
    """Show a display-only flow (standings or year-end) as ONE screen and wait for a key.

    ``lines`` are the flow's rows as ``(key, params)`` pairs (one per row -- templates
    cannot iterate); each is resolved through the theme and rendered in order under one
    header, then the screen waits for one key like the turn-over prompt.

    Returns ``False`` when that key is a quit (``q`` or EOF, :func:`_is_quit`), so the
    caller ends the session; ``True`` otherwise.
    """
    render_screen_clear(out)
    render_header(header, out, colors)
    render_body("\n".join(resolver.resolve(key, params) for key, params in lines), out, colors)
    _write_press_any_key(resolver, out)
    return not _is_quit(_read_key())


def _write_press_any_key(resolver: Resolver, out) -> None:
    """Write the dimmed "press any key" line that ends an acknowledge-only screen."""
    out.write(f"\n{DIM}{resolver.resolve('client.press_any_key')}{RESET}\n")
    out.flush()


#: The eigenschaften screen's heading (``:300``) and its closing key wait (``:316``).
_EIGENSCHAFTEN_SCREEN = "setup.eigenschaften"

#: The house-rules list prompt: drawn as the list's title, one entry per rule from its
#: ``params["rules"]``, then the prompt.
_HOUSE_RULES_LIST = "setup.house_rules.prompt"

#: The setup prompts that open a fresh screen, and whether asking one AGAIN clears too.
#: The source clears before the end year (``:170``), the score weight (``:175``) and the
#: player count (``:205``), and re-asks each by ``goto`` back to that ``{clr}`` line;
#: ``:210`` clears before each player's name, but ``:291`` re-asks a bad name in place,
#: and the gang name (``:215``) follows on the same screen. The house-rules step is the
#: port's own; its list is redrawn whole after each switch.
_SETUP_CLEARS = {
    "setup.end_year_prompt": True,
    "setup.score_weight_prompt": True,
    "setup.house_rules.offer": True,
    _HOUSE_RULES_LIST: True,
    "setup.player_count_prompt": True,
    "setup.player_name_prompt": False,
}

#: Seconds a setup roll's frame stays on screen while no key stops it. Presentation
#: only: the source draws as fast as its BASIC runs (``:350-355``).
ROLL_FRAME_SECONDS = 0.06

#: Back to the start of the line and clear it: a roll's next frame redraws in place.
_REDRAW_LINE = "\r\033[K"

#: A test seam for the setup rolls' keyboard. When set, it is called once per roll
#: frame in place of the terminal, and its answer says whether a key stopped the roll
#: on that frame -- so a test driving ``play()`` over piped input can stop rolls on
#: later frames. ``None`` (always, outside tests): the terminal decides
#: (:class:`_RollKeyboard`).
_roll_key_pressed: Callable[[], bool] | None = None


class _RollKeyboard:
    """The keyboard while a setup roll runs (``:355 getx$:ifx$=""goto350``).

    On a terminal the first frame of a roll puts it in cbreak mode (keys arrive one by
    one, unechoed; Ctrl-C still interrupts) for the whole roll; each frame then waits
    up to :data:`ROLL_FRAME_SECONDS` for a key. :meth:`release` restores the terminal
    and drops any input still pending, so the key that stopped the roll does not also
    answer the next key wait (as ``:1100``'s ``poke198,0`` empties the key buffer).
    Standard input that is no terminal (piped, a file) stops every roll on its first
    frame and reads nothing: the script's lines are for the prompts.
    """

    def __init__(self) -> None:
        self._raw: tuple[int, Any] | None = None

    def pressed(self) -> bool:
        """Whether a key stopped the roll on the frame just drawn."""
        if _roll_key_pressed is not None:
            return _roll_key_pressed()
        if self._raw is None:
            try:
                import termios
                import tty

                fd = sys.stdin.fileno()
                saved = termios.tcgetattr(fd)  # raises termios.error on a non-TTY
            except Exception:
                return True  # not a terminal: stop here, read nothing
            tty.setcbreak(fd)
            self._raw = (fd, saved)
        fd = self._raw[0]
        ready, _, _ = select.select([fd], [], [], ROLL_FRAME_SECONDS)
        if not ready:
            return False
        if os.read(fd, 1) == b"\x03":
            raise KeyboardInterrupt
        return True

    def release(self) -> None:
        """Restore the terminal after a roll and drop the input still pending."""
        if self._raw is None:
            return
        import termios

        fd, saved = self._raw
        self._raw = None
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


#: The turn runner's own acknowledgement screens; every other one is a handler's.
_RUNNER_SCREENS = frozenset({UPKEEP_SCREEN, TURN_OVER_SCREEN, STANDINGS_SCREEN, YEAR_END_SCREEN})


class TerminalSession:
    """One terminal play session: what :func:`play` builds, and one method per phase.

    ``sys.stdin``/``sys.stdout`` are read when the session is built (the session's
    :class:`TerminalInput` keeps that ``stdin``), so a caller replacing them must do so
    first. ``resolver`` is the theme every screen is worded in and ``palette`` the
    colours it is drawn in (default: the config's ``classic`` theme for both). The
    terminal's colour support is read from the environment once, here, into
    :attr:`colors`: a session never switches colour mode half-way.
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
        palette: Palette | None = None,
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
        self.colors = Colors.detect(
            palette if palette is not None else load_palette(_CONFIG_DIR, _DEFAULT_THEME)
        )
        self.cfg = load_game_config(_CONFIG_DIR)

        # The map's drawing data (door glyphs by location); the rules' city is the config's.
        self.city_raw = yaml.safe_load(
            (_CONFIG_DIR / "content" / "map" / "city.yaml").read_text(encoding="utf-8")
        )
        assert self.cfg.city is not None, "the mafia_1920s config has a city map"
        self.city = self.cfg.city

        cfg = self.cfg
        self.weapon_names = [
            w["name"]
            for w in cfg.module.load_weapons(_CONFIG_DIR / cfg.config["entities"]["weapons"])
        ]

        self.inp = TerminalInput(
            resolver=self.resolver,
            colors=self.colors,
            stdin=sys.stdin,
            stdout=self.out,
            weapon_names=self.weapon_names,
            observe_ai=watch_ai,
        )
        if load is not None:
            # raises LoadError; main() reports it
            rules = self.cfg.module.house_rules
            self.seed, self.state, self.rng = _load_session(
                load,
                self.resolver,
                self.cfg.registries,
                lambda stored: rules.check_stored_map(stored, rules.CATALOGUE),
            )
        else:
            self.seed = seed if seed is not None else _DEFAULT_SEED
            # the one session RNG — threaded into every run_option call
            self.rng = Rng(self.seed)
        self.save_path = Path(
            save if save is not None else load if load is not None else _DEFAULT_SAVE
        )
        self.note = self.text("client.map.hint")
        # The setup screens' own drawing state: the last prompt asked (a re-ask may
        # draw in place), the roll whose frames are on screen, and its keyboard.
        self._last_prompt: str | None = None
        self._rolling: str | None = None
        self._roll_keys = _RollKeyboard()

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
            self._roll_keys.release()
            show_cursor(out)
        return self.state, self.rng

    def start_new_game(self) -> None:
        """Title screen, the setup, the new game and its first upkeep, then the turns.

        The source's order (``:30`` ``gosub100:gosub170:gosub200:goto1000``): the title,
        then the setup -- the config's setup handler (:data:`~engine.turns.SETUP_HANDLER_KEY`)
        asks the end year, the score weight, the house rules, the player count and each
        player's names, and runs each player's eigenschaften screen. What the caller
        supplied is passed in and not asked. Setup draws from its own ``Rng(seed)``, not
        the session RNG, and the game is built from the record it returns.
        """
        out = self.out
        # Title screen; it waits for a key (:154 `getx$:ifx$=""goto154`).
        out.write(CLEAR)
        out.write(title_screen(self.colors))
        out.flush()
        _read_line_visible(sys.stdin, out)

        given = {
            "end_year": self.end_year,
            "score_weight": self.score_weight,
            "players": self.players,
        }
        handler = functools.partial(
            self.cfg.handlers[SETUP_HANDLER_KEY],
            **{name: value for name, value in given.items() if value is not None},
        )
        setup = step(handler, None, Rng(self.seed))
        try:
            interaction = next(setup)
            while True:
                interaction = setup.send(self.render_setup(interaction))
        except StopIteration as done:
            record = done.value.payload.returned
        self.state = self.cfg.module.new_game(record)

        # The engine turn runner owns the order of every turn from here on; a new
        # game enters it at the first player's turn start, upkeep first.
        self.drive_turns(UPKEEP)

    def render_setup(self, interaction):
        """Show one interaction of the setup handler; return its answer."""
        if isinstance(interaction, PromptText):
            return self.setup_prompt(interaction)
        if isinstance(interaction, RollFrame):
            return self.setup_roll(interaction)
        if isinstance(interaction, Heading):
            return self.heading(interaction)
        if isinstance(interaction, Acknowledge):
            return self.acknowledge(interaction)
        if isinstance(interaction, ShowMessage):
            # :313/:316 print"{down}..."; each line under a blank one.
            self.out.write(f"\n{self.text(interaction.key, dict(interaction.params))}\n")
            self.out.flush()
            return None
        raise AssertionError(f"unknown setup interaction {interaction!r}")

    def setup_prompt(self, prompt: PromptText) -> str:
        """Ask one setup question and return the line typed (the handler judges it).

        Real EOF can never be answered, so it raises :class:`EndOfInput`: the session
        ends on it like on a quit.
        """
        out = self.out
        reask_clears = _SETUP_CLEARS.get(prompt.key)
        if reask_clears is not None and (reask_clears or prompt.key != self._last_prompt):
            render_screen_clear(out)
        if prompt.key == _HOUSE_RULES_LIST:
            out.write(self.text("setup.house_rules.title") + "\n")
            for rule in prompt.params["rules"]:
                entry = {
                    "number": rule["number"],
                    "setting": self.text(f"setup.house_rules.setting.{rule['setting']}"),
                    "description": self.text(f"house_rules.{rule['id']}"),
                }
                out.write(self.text("setup.house_rules.entry", entry) + "\n")
        out.write(f"{self.text(prompt.key, dict(prompt.params))} ")
        out.flush()
        self._last_prompt = prompt.key
        line = _read_line_visible(sys.stdin, out)
        if line == "":
            raise EndOfInput
        return line.rstrip("\r\n")

    def setup_roll(self, frame: RollFrame) -> bool:
        """Draw one frame of a setup roll in place; return whether a key stopped it.

        :310-312 print each stat's label on a line of its own; :350 redraws the value
        after it until a key is pressed (:355), and the value then on screen is kept.
        """
        out = self.out
        if frame.key != self._rolling:
            self._rolling = frame.key
            out.write("\n")  # the label's {down}
        out.write(_REDRAW_LINE + self.text(frame.key, dict(frame.params)))
        out.flush()
        stopped = self._roll_keys.pressed()
        if stopped:
            self._roll_keys.release()
            self._rolling = None
            out.write("\n")
            out.flush()
        return stopped

    def resume_loaded_game(self) -> None:
        """Enter the turns of a loaded game: no title, no setup, no upkeep."""
        # The runner re-enters the phase the save recorded: a save is only ever taken
        # at the turn menu or on the map, so the saved player's free turn resumes there,
        # and this turn's upkeep and turn start (which ran before the save) do not run
        # again.
        self.drive_turns()

    def drive_turns(self, entry: str | None = None) -> None:
        """Drive the engine turn runner from ``entry`` until the game ends or a quit.

        The runner (:class:`engine.turns.TurnRunner`) owns the order of the turn; this
        only renders its screens and answers its prompts, adopting the runner's state
        at every interaction. ``entry=None`` re-enters the phase the state recorded.
        """
        assert self.state is not None, "state is set by setup or load before any turn"
        runner = TurnRunner(
            self.state,
            self.rng,
            city=self.cfg.city,
            shells=self.cfg.shells,
            turn_menu=self.cfg.menus.get("turn"),
            observe_ai=self.inp.observes_ai,
        )
        turns = runner.run(entry)
        try:
            interaction = next(turns)
            while True:
                self.state = runner.state
                response = self.render(interaction)
                if response is _QUIT:
                    turns.close()
                    return
                interaction = turns.send(response)
        except StopIteration:
            self.state = runner.state

    def render(self, interaction):
        """Show one interaction of the turn runner; return its answer, or ``_QUIT``."""
        if isinstance(interaction, CombatScreen):
            # The board clears the screen when it is drawn, so the whose-turn line goes
            # under the clear, with the board, not before it.
            return self.inp.answer(interaction, banner=self.whose_turn(interaction))
        if isinstance(interaction, Acknowledge) and interaction.key not in _RUNNER_SCREENS:
            # A handler's own screen clears too (see acknowledge()).
            return self.acknowledge(interaction, banner=self.whose_turn(interaction))
        self.announce_player(interaction)
        if isinstance(interaction, TurnMenu):
            return self.turn_menu(interaction)
        if isinstance(interaction, MapMove):
            return self.map_prompt(interaction)
        if isinstance(interaction, LocationMenu):
            return _render_location_menu(interaction, self.resolver, self.colors, self.out)
        if isinstance(interaction, OptionDone):
            # Between actions, a status bar off the action's committed state; a
            # cancelled action committed nothing and shows nothing.
            if interaction.status != "cancelled":
                render_status_bar_from_state(self.state, self.out, self.resolver, self.colors)
            return None
        if isinstance(interaction, Heading):
            return self.heading(interaction)
        if isinstance(interaction, Acknowledge):
            return self.acknowledge(interaction)
        # A hook's or handler's own interaction: prompts, narration, fights.
        return self.inp(interaction)

    def announce_player(self, interaction) -> None:
        """Name the answering player when it is not the active one (``session.whose_turn``).

        Every interaction names who answers it (``player``; ``None`` is the active
        player). A prompt meant for someone else -- a defender, a freed prisoner -- is
        announced first, so the right player takes the keyboard.
        """
        line = self.whose_turn(interaction)
        if line is None:
            return
        self.out.write(line + "\n")
        self.out.flush()

    def whose_turn(self, interaction) -> str | None:
        """The whose-turn line for ``interaction``, or ``None`` for the active player."""
        player = getattr(interaction, "player", None)
        if player is None or self.state is None or player == self.state.clock.active_player:
            return None
        return self.text("session.whose_turn", {"name": self.state.players[player].name})

    def heading(self, screen: Heading) -> None:
        """Open one of the runner's own screens under its heading."""
        out = self.out
        if screen.key == UPKEEP_SCREEN:
            # Upkeep's messages follow under this heading, each printed once; the
            # Acknowledge that closes the screen only waits for the key.
            render_screen_clear(out)
            render_header(self.text("client.header.upkeep"), out, self.colors)
        elif screen.key == JOB_SHIFT_SCREEN:
            render_screen_clear(out)
            render_header(self.text("client.header.job"), out, self.colors)
        elif screen.key == _EIGENSCHAFTEN_SCREEN:
            # :300 print"{clr}{down}eigenschaften:"
            render_screen_clear(out)
            out.write(f"\n{self.text(screen.key)}\n")
            out.flush()
        elif screen.key == LOCATION_CLOSED_SCREEN:
            # No state change, and no move spent beyond the door step's charge.
            render_screen_clear(out)
            out.write(self.text("client.location.closed", dict(screen.params)) + "\n\n")
            out.flush()
        else:
            raise AssertionError(f"unknown screen heading {screen.key!r}")

    def acknowledge(self, screen: Acknowledge, *, banner: str | None = None):
        """Show one of the runner's acknowledgement screens; ``_QUIT`` on a quit key.

        ``banner`` is the whose-turn line of a handler's own screen, printed under its
        screen clear."""
        if screen.key == UPKEEP_SCREEN:
            # The upkeep screen's body is already printed (see heading()): wait for the
            # key. EOF is an ack, not a quit -- upkeep offers no cancel path.
            _write_press_any_key(self.resolver, self.out)
            _read_line_visible(sys.stdin, self.out)
            return None
        if screen.key == _EIGENSCHAFTEN_SCREEN:
            # :316 goto1100: the key wait under the stats, which stay on screen.
            _write_press_any_key(self.resolver, self.out)
            _read_key()
            return None
        if screen.key == TURN_OVER_SCREEN:
            return None if self.turn_over() else _QUIT
        if screen.key == STANDINGS_SCREEN:
            header = self.text("client.header.standings")
            if not _render_lines_screen(
                header, screen.params["lines"], self.resolver, self.colors, self.out
            ):
                self.out.write(self.text("client.bye") + "\n")
                return _QUIT
            return None
        if screen.key == YEAR_END_SCREEN:
            # :40100 — the year-end result; the game ends after it, whatever the key.
            header = self.text("client.header.game_over")
            _render_lines_screen(
                header, screen.params["lines"], self.resolver, self.colors, self.out
            )
            return None
        # A handler's own screen (the overview): its lines, then a key.
        render_screen_clear(self.out)
        if banner is not None:
            self.out.write(banner + "\n")
        lines = screen.params.get("lines")
        body = (
            "\n".join(self.text(key, params) for key, params in lines)
            if lines is not None
            else self.text(screen.key, dict(screen.params))
        )
        render_body(body, self.out, self.colors)
        _write_press_any_key(self.resolver, self.out)
        _read_key()
        return None

    def turn_menu(self, menu: TurnMenu):
        """Show the turn menu (``mf-prg.bas:1015-1022``); return the key, or ``_QUIT``.

        The head names the player and gang (``:1015``), the cash and the date
        (``:1016-1017``); the options are the runner's, each worded by the theme. A
        pressed key goes to the runner, which ignores one no option has (``:1030``) and
        asks again. The save key saves and redraws; a resize redraws.
        """
        out = self.out
        assert self.state is not None, "state is set by setup or load before any turn"
        player = self.state.players[self.state.clock.active_player]
        clock = self.state.clock
        note = self.text("client.menu.hint")
        while True:
            render_screen_clear(out)
            render_header(
                self.text(
                    "turn.menu.header",
                    {
                        "name": player.name,
                        "gang_name": self.cfg.module.state.gang_name(player),
                    },
                ),
                out,
                self.colors,
            )
            status = self.text(
                "turn.menu.status",
                # :1017 1+int((ja-x)*12): the month, 1-based.
                {"cash": player.ka, "year": clock.year, "month": clock.month + 1},
            )
            options = "\n".join(self.text(f"turn.menu.option.{o}") for o in menu.options)
            render_body(
                f"{status}\n\n{self.text('turn.menu.prompt')}\n\n{options}", out, self.colors
            )
            out.write(f"\n{DIM}{note}{RESET}\n")
            out.flush()
            key = _read_key()
            if check_resize():
                continue
            if _is_quit(key) and MAP_QUIT in menu.commands:
                out.write(self.text("client.bye") + "\n")
                return _QUIT
            if key == _SAVE_KEY and MAP_SAVE in menu.commands:
                self.save()
                note = self.note
                continue
            return key

    def map_prompt(self, prompt: MapMove):
        """Answer the runner's map-move prompt: a direction, or ``_QUIT``.

        The map is drawn with a note saying what the last move did. A resize redraws,
        the save key saves (when the prompt offers saving) and redraws, any other key
        says so and redraws; none of them reaches the runner.
        """
        out = self.out
        # What the last move did; a prompt no move preceded (a fresh free turn) hints.
        self.note = self.text(_OUTCOME_NOTES.get(prompt.outcome or "", "client.map.hint"))
        while True:
            out.write(CLEAR)
            render_map(self.city, self.city_raw, self.state, out, self.resolver, self.colors)
            out.write(f"{DIM}{self.note}{RESET}\n")
            out.flush()
            key = _read_key()
            if check_resize():
                self.note = self.text("client.map.resized")
                continue
            if _is_quit(key):
                out.write(self.text("client.bye") + "\n")
                return _QUIT
            if key == _SAVE_KEY and MAP_SAVE in prompt.commands:
                self.save()
                continue
            if key == _EXIT_KEY and MAP_EXIT in prompt.commands:
                return MAP_EXIT
            direction = _MOVE_KEYS.get(key)
            if direction is None or direction not in prompt.directions:
                self.note = self.text("client.map.bad_key")
                continue
            return direction

    def save(self) -> None:
        """Save the game (``p``, at the turn menu or on the map); the note says how it went."""
        # A map-turn save -- the snapshot is authoritative, so
        # the effect log is empty; the RNG log lets a load resume the
        # stream mid-way. Overwrites without asking.
        # A failed save (missing directory, full disk, no permission)
        # must never end the game: say so and keep playing.
        assert self.state is not None, "state is set by setup or load before any turn"
        try:
            save_game(
                self.save_path,
                self.state,
                registries=self.cfg.registries,
                effect_log=[],
                rng_log=self.rng.log,
                seed=self.seed,
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
        render_header(self.text("client.header.turn_over"), out, self.colors)
        render_body(
            self.text(
                "client.turn_over.summary",
                {
                    "cash": p.ka,
                    "position": p.po,
                    "movement": p.ms,
                    "rank": p.rank,
                    "jail_months": self.cfg.module.state.wanted(p).jail_months,
                },
            ),
            out,
            self.colors,
        )
        _write_press_any_key(self.resolver, out)
        if _is_quit(_read_key()):
            out.write(self.text("client.bye") + "\n")
            return False
        return True


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
    palette: Palette | None = None,
) -> tuple:
    """Play the default config over real stdin/stdout; return the final ``(state, rng)``.

    ``seed`` seeds the ONE session :class:`~engine.rng.Rng` (default 42); the setup
    rolls draw from a separate ``Rng(seed)``. ``players`` is ``[(name, gang_name), ...]``,
    1..4 hot-seat players; left ``None``, the setup asks the player count and every name
    (``mf-prg.bas:205-215``). ``end_year`` / ``score_weight`` (x9, x8) left ``None`` are
    asked after the title screen (``:170-176``). A supplied value skips its prompt; each
    player's eigenschaften screen is always shown.

    ``load`` resumes a save: its state, seed and RNG draw log, straight into the saved
    player's turn at the menu or on the map, where it was saved (no title, setup or
    upkeep); the new-game inputs are ignored. ``p`` at the turn menu or on the map saves
    to ``save``, else the loaded file, else ``mafia-save.jsonl``.
    ``watch_ai`` shows the board after every CPU combat activation (off, as in the original).
    ``resolver`` is the theme the session is worded in and ``palette`` the colours it is
    drawn in (default: the ``classic`` theme's); the terminal's colour support is read
    from ``$COLORTERM``/``$TERM`` once, when the session starts.

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
        palette=palette,
    )
    return session.run()

"""Role-based rendering functions for the terminal client.

Each function styles text by **role** (header, body, menu option, status bar,
etc.).  Strings stay pure text; the renderer decides presentation.  No inline
format codes — the renderer owns all ANSI styling.

All functions write to an injected ``TextIO`` for testability.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, TextIO

import yaml

from clients.terminal.palette import (
    DIM,
    RESET_ALL,
    RESET_BG,
    RESET_FG,
    REVERSE,
    bg,
    fg,
    load_palette,
)
from clients.terminal import CLEAR

# ---------------------------------------------------------------------------
# Layout config (terminal-specific, loaded once at import time)
# ---------------------------------------------------------------------------

_LAYOUT_PATH = Path(__file__).parent / "layout.yaml"


def _load_layout() -> dict:
    try:
        return yaml.safe_load(_LAYOUT_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


_LAYOUT = _load_layout()

# ---------------------------------------------------------------------------
# Terminal width helper
# ---------------------------------------------------------------------------


def _term_width() -> int:
    return shutil.get_terminal_size().columns


# ---------------------------------------------------------------------------
# Palette (loaded from theme config)
# ---------------------------------------------------------------------------

# Lazy-loaded palette dict.  Call _get_palette() to access.
_PAL: dict[str, tuple[int, int, int]] | None = None


def _get_palette(
    config_dir: Path | None = None, theme: str = "classic"
) -> dict[str, tuple[int, int, int]]:
    global _PAL
    if _PAL is None:
        if config_dir is not None:
            _PAL = load_palette(config_dir, theme)
        else:
            # Fallback: load from the known default config path.
            from clients.terminal import _DEFAULT_CONFIG_DIR

            _PAL = load_palette(_DEFAULT_CONFIG_DIR, theme)
    return _PAL


def set_palette(palette: dict[str, tuple[int, int, int]]) -> None:
    """Override the palette (for testing or runtime theme swaps)."""
    global _PAL
    _PAL = palette


# ---------------------------------------------------------------------------
# Role-based render functions
# ---------------------------------------------------------------------------


def render_screen_clear(out: TextIO) -> None:
    """Clear the screen and home the cursor."""
    out.write(CLEAR)


def render_separator(out: TextIO) -> None:
    """Draw a horizontal separator line at terminal width."""
    width = _term_width()
    out.write(f"{DIM}{'─' * width}{RESET_ALL}\n")


def render_header(text: str, out: TextIO) -> None:
    """Full-width double-line box header with centered text.

    ``╔══════════════════ SCHLUPFWINKEL ══════════════════╗``
    """
    width = _term_width()
    # ╔ + space + text + space + ╗ = text_len + 4 for corners+spaces
    # Fill remaining with ═
    inner_width = width - 2  # subtract ╔ and ╗
    pad_total = max(0, inner_width - len(text) - 2)  # -2 for the spaces around text
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    pal = _get_palette()
    out.write(f"{fg('light_grey', pal)}╔{'═' * left_pad}  {text}  {'═' * right_pad}╗{RESET_FG}\n")


def render_subheader(text: str, out: TextIO) -> None:
    """Single-line centered subheader: ``──── text ────``"""
    width = _term_width()
    pad_total = max(0, width - len(text) - 2)  # -2 for the spaces around text
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    pal = _get_palette()
    out.write(
        f"{DIM}{fg('light_grey', pal)}{'─' * left_pad}  {text}  {'─' * right_pad}{RESET_ALL}\n"
    )


def render_body(text: str, out: TextIO) -> None:
    """Normal body text in light grey."""
    pal = _get_palette()
    out.write(f"{fg('light_grey', pal)}{text}{RESET_FG}\n")


def render_colored(text: str, color_name: str, out: TextIO) -> None:
    """Text in a specific Pepto palette color."""
    pal = _get_palette()
    out.write(f"{fg(color_name, pal)}{text}{RESET_FG}\n")


def render_menu_option(index: int, label: str, out: TextIO) -> None:
    """Numbered menu option: blue index, light grey label."""
    pal = _get_palette()
    out.write(
        f"  {fg('light_blue', pal)}{index}){RESET_FG} {fg('light_grey', pal)}{label}{RESET_FG}\n"
    )


def render_prompt(out: TextIO) -> None:
    """Input prompt ``> ``."""
    out.write("> ")
    out.flush()


def render_status_bar(
    player_name: str,
    cash: int,
    pos: int,
    ms: int,
    out: TextIO,
) -> None:
    """Full-width reverse-video status bar with player name.

    Format: `` alcapone │ cash 5400$ │ pos 181 │ ms 19 ``
    """
    width = _term_width()
    bar = f" {player_name} │ cash {cash}$ │ pos {pos} │ ms {ms} "
    padded = bar.center(width)
    pal = _get_palette()
    out.write(f"{REVERSE}{bg('brown', pal)}{fg('light_grey', pal)}{padded}{RESET_ALL}\n")


def render_status_bar_from_state(state: Any, out: TextIO) -> None:
    """Convenience wrapper: extract player info from GameState and render."""
    if state is None or not state.players:
        return
    p = state.players[state.clock.active_player]
    render_status_bar(
        player_name=getattr(p, "name", "player"),
        cash=p.ka,
        pos=p.po,
        ms=p.ms,
        out=out,
    )


def render_map_frame(map_lines: list[str], out: TextIO) -> None:
    """40×25 map wrapped in a full-width light_blue background band."""
    width = _term_width()
    pal = _get_palette()
    bg_code = bg("light_blue", pal)
    reset_bg = RESET_BG
    for line in map_lines:
        padded = line.ljust(width)
        out.write(f"{bg_code}{padded}{reset_bg}\n")


# ---------------------------------------------------------------------------
# Combat (U7) — renders a CombatScreen.to_json() payload
# ---------------------------------------------------------------------------

#: 40×13 combat grid geometry (CLAUDE.md: a DIFFERENT space from the 40x25 city map).
#: Kept local rather than imported from engine.combat: the renderer draws from the
#: JSON payload only (engine/ imports nothing from clients/, and the reverse holds
#: too -- U7 must not import combat LOGIC, only these two int constants that describe
#: the wire shape it already renders positions against).
COMBAT_GRID_COLS = 40
COMBAT_GRID_ROWS = 13

#: Fighter sprite glyphs by side (1/2) and standing/down -- ASCII, unambiguously
#: narrow (no East-Asian-Width "W" glyphs, mirroring the city map's own constraint).
_FIGHTER_CHAR = {1: "1", 2: "2"}
_DOWN_CHAR = "x"
_ACTIVE_WALL_CODES = (160, 156)  # mirrors engine.combat.SHOT_WALL_CODES (U4/U7 note)


def render_combat_grid(payload: dict, out: TextIO) -> None:
    """Render one ``CombatScreen.to_json()`` payload's 40×13 grid.

    Draws walls from ``payload["grid"]`` (a linear 0..520 code array; missing/short
    entries read as open ground, mirroring ``engine.combat.can_move_onto``'s
    treatment of a short grid), a fighter glyph per standing combatant (side 1 vs
    side 2 colored per the palette's capability tiers), an ``x`` for a downed
    fighter, and highlights the active fighter's cell in reverse video. Exactly
    40 columns per row, ASCII-only glyphs (mirrors the city map's width contract,
    ``tests/test_terminal_integration.py::TestMapDisplayWidth``).

    The renderer reads ONLY the JSON payload's plain dicts/lists/ints -- no
    ``engine.combat``/``engine.state`` import -- so the client never depends on
    combat's internal representation, only its wire shape (KTD-2).
    """
    pal = _get_palette()
    grid = payload.get("grid") or []
    sides = payload.get("sides") or [[], []]
    active_side = payload.get("active_side", 1)
    active_fighter = payload.get("active_fighter", 1)

    # cell -> (side, char) for every STANDING fighter; downed fighters still occupy
    # a cell in the payload but render as the down glyph, not a side glyph.
    occupied: dict[int, tuple[int, str, bool]] = {}
    for side_idx, side in enumerate(sides, start=1):
        for f_idx, fighter in enumerate(side, start=1):
            pos = fighter.get("position", 0)
            down = bool(fighter.get("down", False))
            is_active = side_idx == active_side and f_idx == active_fighter
            occupied[pos] = (side_idx, _DOWN_CHAR if down else _FIGHTER_CHAR[side_idx], is_active)

    side_colors = {1: "light_red", 2: "cyan"}

    for row in range(COMBAT_GRID_ROWS):
        chars: list[str] = []
        for col in range(COMBAT_GRID_COLS):
            cell = row * COMBAT_GRID_COLS + col
            if cell in occupied:
                side_idx, ch, is_active = occupied[cell]
                color = fg(side_colors.get(side_idx, "white"), pal)
                prefix = REVERSE if is_active else ""
                chars.append(f"{prefix}{color}{ch}{RESET_ALL}")
            elif cell < len(grid) and grid[cell] in _ACTIVE_WALL_CODES:
                chars.append(f"{fg('dark_grey', pal)}#{RESET_FG}")
            else:
                chars.append(" ")
        out.write("".join(chars) + "\n")


def render_fighter_panel(
    payload: dict,
    resolver: Any,
    weapon_names: list[str],
    out: TextIO,
) -> None:
    """Render the active fighter's stat panel (``mf-prg.bas:30115-30116``): name,
    weapon, energy, kraft, brutalitaet -- resolved through the theme's
    ``combat.panel_*`` keys (zero hardcoded display text, CLAUDE.md).
    """
    fighter = payload.get("fighter")
    pal = _get_palette()
    if fighter is None:
        return
    name = fighter.get("name", "")
    weapon_id = fighter.get("weapon", 0)
    weapon_name = weapon_names[weapon_id] if 0 <= weapon_id < len(weapon_names) else str(weapon_id)
    out.write(f"{fg('light_grey', pal)}{name}{RESET_FG}\n")
    out.write(resolver.resolve("combat.panel_weapon", {"weapon": weapon_name}) + "\n")
    out.write(
        resolver.resolve("combat.panel_energy", {"energie": fighter.get("energie", 0)}) + "\n"
    )
    out.write(resolver.resolve("combat.panel_kraft", {"kraft": fighter.get("kraft", 0)}) + "\n")
    out.write(
        resolver.resolve("combat.panel_brutalitaet", {"brutalitaet": fighter.get("brutalitaet", 0)})
        + "\n"
    )


def render_combat_message(payload: dict, resolver: Any, out: TextIO) -> None:
    """Render the screen's ``message`` field, if any.

    ``message`` is either a bare key string (``"illegal_move"``/``"unknown_action"``)
    set by the driver loop, or the ``CombatFight.shoot`` result dict (``hit``/
    ``damage``/``target_side``/``target_index``/``downed``) -- both shapes are
    JSON-safe per :class:`engine.interactions.CombatScreen`'s contract, so this just
    branches on which one arrived and resolves the matching theme key.
    """
    message = payload.get("message")
    if message is None:
        return
    pal = _get_palette()
    if isinstance(message, str):
        text = resolver.resolve(f"combat.{message}")
    elif isinstance(message, dict):
        if not message.get("hit"):
            text = resolver.resolve("combat.miss")
        elif message.get("downed"):
            target_side = message.get("target_side")
            if target_side == 1:
                text = resolver.resolve(
                    "combat.hit_player_down",
                    {"index": (message.get("target_index") or 0) + 1},
                )
            else:
                text = resolver.resolve("combat.hit_enemy_down")
        else:
            text = resolver.resolve("combat.hit")
    else:
        return
    out.write(f"{fg('yellow', pal)}{text}{RESET_FG}\n")


def render_combat_losses(payload: dict, resolver: Any, out: TextIO) -> None:
    """Render the per-side losses line (``mf-prg.bas:30510-30515``)."""
    losses = payload.get("losses") or [0, 0]
    out.write(resolver.resolve("combat.losses_heading") + "\n")
    for i, count in enumerate(losses, start=1):
        out.write(
            resolver.resolve("combat.losses_line", {"name": f"side {i}", "count": count}) + "\n"
        )

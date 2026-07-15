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


def _get_palette(config_dir: Path | None = None, theme: str = "classic") -> dict[str, tuple[int, int, int]]:
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
    out.write("\033[2J\033[H")


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
    out.write(
        f"{fg('light_grey', pal)}╔{'═' * left_pad}  {text}  {'═' * right_pad}╗"
        f"{RESET_FG}\n"
    )


def render_subheader(text: str, out: TextIO) -> None:
    """Single-line centered subheader: ``──── text ────``"""
    width = _term_width()
    pad_total = max(0, width - len(text) - 2)  # -2 for the spaces around text
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    pal = _get_palette()
    out.write(
        f"{DIM}{fg('light_grey', pal)}{'─' * left_pad}  {text}  {'─' * right_pad}"
        f"{RESET_ALL}\n"
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
        f"  {fg('light_blue', pal)}{index}){RESET_FG} "
        f"{fg('light_grey', pal)}{label}{RESET_FG}\n"
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
    out.write(
        f"{REVERSE}{bg('brown', pal)}{fg('light_grey', pal)}{padded}{RESET_ALL}\n"
    )


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

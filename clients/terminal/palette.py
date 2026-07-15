"""C64 Pepto palette → ANSI escape code generators.

Loads the palette from the game config's ``themes/<theme>/renderer/palette.yaml``
(theme-level data, game-specific, theme-swappable). Provides ``fg(name)`` and
``bg(name)`` functions that return ANSI escape sequences for foreground and
background colors.

Terminal capability detection: 24-bit truecolor, 256-color, or 8-color
fallback based on ``$COLORTERM`` / ``$TERM`` environment variables.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Pepto-accurate C64 palette (hardcoded fallback if YAML is missing)
# ---------------------------------------------------------------------------

_PEPTO_FALLBACK: dict[str, tuple[int, int, int]] = {
    "black":       (0, 0, 0),
    "white":       (255, 255, 255),
    "red":         (158, 52, 38),
    "cyan":        (100, 183, 199),
    "purple":      (138, 70, 172),
    "green":       (104, 169, 65),
    "blue":        (74, 54, 181),
    "yellow":      (208, 221, 94),
    "brown":       (144, 95, 37),
    "light_brown": (100, 87, 8),
    "light_red":   (217, 124, 102),
    "dark_grey":   (82, 82, 82),
    "grey":        (135, 135, 135),
    "light_green": (161, 214, 127),
    "light_blue":  (155, 222, 255),
    "light_grey":  (178, 178, 178),
}

# C64 color RAM index (0-15) → Pepto palette name.
C64_COLOR_NAMES: list[str] = [
    "black",       # 0
    "white",       # 1
    "red",         # 2
    "cyan",        # 3
    "purple",      # 4
    "green",       # 5
    "blue",        # 6
    "yellow",      # 7
    "brown",       # 8
    "light_brown", # 9
    "light_red",   # 10
    "dark_grey",   # 11
    "grey",        # 12
    "light_green", # 13
    "light_blue",  # 14
    "light_grey",  # 15
]


def load_palette(config_dir: Path | None = None, theme: str = "classic") -> dict[str, tuple[int, int, int]]:
    """Load the C64 palette from ``themes/<theme>/renderer/palette.yaml``.

    Falls back to the hardcoded Pepto values if the YAML is missing or
    malformed. If ``config_dir`` is None, uses the hardcoded fallback.
    """
    if config_dir is None:
        return dict(_PEPTO_FALLBACK)
    palette_path = config_dir / "themes" / theme / "renderer" / "palette.yaml"
    try:
        raw = yaml.safe_load(palette_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(_PEPTO_FALLBACK)
        result: dict[str, tuple[int, int, int]] = {}
        for name, rgb in raw.items():
            if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
                result[name] = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        # Merge with fallback so missing keys still work.
        merged = dict(_PEPTO_FALLBACK)
        merged.update(result)
        return merged
    except (OSError, yaml.YAMLError):
        return dict(_PEPTO_FALLBACK)


# ---------------------------------------------------------------------------
# Terminal color capability detection
# ---------------------------------------------------------------------------

class ColorSupport(Enum):
    TRUECOLOR = "24bit"
    COLOR256 = "256"
    COLOR8 = "8"


def term_color_support() -> ColorSupport:
    """Detect terminal color support from ``$COLORTERM`` and ``$TERM``.

    Returns the best supported mode. Defaults to TRUECOLOR if unknown.
    """
    ct = os.environ.get("COLORTERM", "").lower()
    if ct in ("truecolor", "24bit"):
        return ColorSupport.TRUECOLOR
    term = os.environ.get("TERM", "").lower()
    if "256color" in term:
        return ColorSupport.COLOR256
    if term in ("dumb", ""):
        return ColorSupport.COLOR8
    return ColorSupport.TRUECOLOR


# Closest-xterm-256 index for each C64 color name.
_XTERM_256: dict[str, int] = {
    "black": 0, "white": 15, "red": 124, "cyan": 44, "purple": 133,
    "green": 70, "blue": 57, "yellow": 148, "brown": 130, "light_brown": 100,
    "light_red": 209, "dark_grey": 240, "grey": 245, "light_green": 149,
    "light_blue": 153, "light_grey": 250,
}

# Basic 8-color ANSI codes (names map to 0-7).
_BASIC_8: dict[str, int] = {
    "black": 0, "red": 1, "green": 2, "brown": 3,
    "blue": 4, "purple": 5, "cyan": 6, "light_grey": 7,
    "dark_grey": 0, "grey": 7, "white": 7,
    "light_red": 1, "light_green": 2, "light_blue": 4,
    "yellow": 3, "light_brown": 3,
}


# ---------------------------------------------------------------------------
# ANSI escape generators
# ---------------------------------------------------------------------------

def fg(color_name: str, palette: dict[str, tuple[int, int, int]],
       support: ColorSupport | None = None) -> str:
    """Return the ANSI foreground escape sequence for *color_name*."""
    if support is None:
        support = term_color_support()
    r, g, b = palette[color_name]
    if support == ColorSupport.TRUECOLOR:
        return f"\033[38;2;{r};{g};{b}m"
    if support == ColorSupport.COLOR256:
        code = _XTERM_256.get(color_name, 0)
        return f"\033[38;5;{code}m"
    # 8-color fallback
    code = _BASIC_8.get(color_name, 7)
    return f"\033[{30 + code}m"


def bg(color_name: str, palette: dict[str, tuple[int, int, int]],
       support: ColorSupport | None = None) -> str:
    """Return the ANSI background escape sequence for *color_name*."""
    if support is None:
        support = term_color_support()
    r, g, b = palette[color_name]
    if support == ColorSupport.TRUECOLOR:
        return f"\033[48;2;{r};{g};{b}m"
    if support == ColorSupport.COLOR256:
        code = _XTERM_256.get(color_name, 0)
        return f"\033[48;5;{code}m"
    # 8-color fallback
    code = _BASIC_8.get(color_name, 7)
    return f"\033[{40 + code}m"


# ---------------------------------------------------------------------------
# Reset sequences
# ---------------------------------------------------------------------------

RESET_FG = "\033[39m"
RESET_BG = "\033[49m"
RESET_ALL = "\033[0m"
REVERSE = "\033[7m"
NO_REVERSE = "\033[27m"
BOLD_ON = "\033[1m"
BOLD_OFF = "\033[22m"
DIM = "\033[2m"
CURSOR_HIDE = "\033[?25l"
CURSOR_SHOW = "\033[?25h"
CLEAR = "\033[2J\033[H"

# Legacy aliases (keep existing tests working until T12 removes them).
RESET = RESET_ALL

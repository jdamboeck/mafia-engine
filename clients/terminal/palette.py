"""C64 Pepto palette → ANSI escape code generators.

Loads the palette from the game config's ``themes/<theme>/renderer/palette.yaml``
(theme-level data, game-specific, theme-swappable). Provides ``fg(name)`` and
``bg(name)`` functions that return ANSI escape sequences for foreground and
background colors.

Terminal capability detection and 256/8-color fallback is in this module too
(T10). For now, only 24-bit truecolor is implemented.
"""

from __future__ import annotations

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


def load_palette(config_dir: Path, theme: str = "classic") -> dict[str, tuple[int, int, int]]:
    """Load the C64 palette from ``themes/<theme>/renderer/palette.yaml``.

    Falls back to the hardcoded Pepto values if the YAML is missing or
    malformed.
    """
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
# ANSI escape generators (24-bit truecolor)
# ---------------------------------------------------------------------------

def fg(color_name: str, palette: dict[str, tuple[int, int, int]]) -> str:
    """Return the ANSI foreground escape sequence for *color_name*."""
    r, g, b = palette[color_name]
    return f"\033[38;2;{r};{g};{b}m"


def bg(color_name: str, palette: dict[str, tuple[int, int, int]]) -> str:
    """Return the ANSI background escape sequence for *color_name*."""
    r, g, b = palette[color_name]
    return f"\033[48;2;{r};{g};{b}m"


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

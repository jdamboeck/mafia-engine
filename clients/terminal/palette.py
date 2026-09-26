"""C64 Pepto palette → ANSI escape code generators.

Loads the palette from a theme's ``renderer/palette.yaml`` (theme-level data,
game-specific, theme-swappable): :func:`load_palette` reads a config's theme,
:func:`read_palette_overrides` the entries a selected theme sets over classic's.
``fg(name, palette, support)`` and ``bg(...)`` return the ANSI escape sequences.

Terminal capability detection (:func:`term_color_support`): 24-bit truecolor,
256-color, or 8-color fallback based on ``$COLORTERM`` / ``$TERM``. It is read
once per session into a :class:`Colors` -- the palette and support the renderers
take as an argument -- never per escape sequence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

#: Colour name -> ``(r, g, b)``.
Palette = dict[str, tuple[int, int, int]]

# ---------------------------------------------------------------------------
# Pepto-accurate C64 palette (hardcoded fallback if YAML is missing)
# ---------------------------------------------------------------------------

_PEPTO_FALLBACK: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (158, 52, 38),
    "cyan": (100, 183, 199),
    "purple": (138, 70, 172),
    "green": (104, 169, 65),
    "blue": (74, 54, 181),
    "yellow": (208, 221, 94),
    "brown": (144, 95, 37),
    "light_brown": (100, 87, 8),
    "light_red": (217, 124, 102),
    "dark_grey": (82, 82, 82),
    "grey": (135, 135, 135),
    "light_green": (161, 214, 127),
    "light_blue": (155, 222, 255),
    "light_grey": (178, 178, 178),
}

# C64 color RAM index (0-15) → Pepto palette name.
C64_COLOR_NAMES: list[str] = [
    "black",  # 0
    "white",  # 1
    "red",  # 2
    "cyan",  # 3
    "purple",  # 4
    "green",  # 5
    "blue",  # 6
    "yellow",  # 7
    "brown",  # 8
    "light_brown",  # 9
    "light_red",  # 10
    "dark_grey",  # 11
    "grey",  # 12
    "light_green",  # 13
    "light_blue",  # 14
    "light_grey",  # 15
]


def load_palette(
    config_dir: Path | None = None, theme: str = "classic"
) -> dict[str, tuple[int, int, int]]:
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


def read_palette_overrides(theme_dir: str | Path) -> Palette:
    """The colours a theme directory sets in its ``renderer/palette.yaml``.

    The result is merged over classic's palette, the way a theme's strings merge
    over classic's, so a theme restates only the colours it changes. No file means
    no overrides (a theme need not recolour anything). Unlike :func:`load_palette`,
    a file that is there but broken -- not a mapping, or an entry that is not three
    0-255 integers -- raises ``ValueError`` naming the file: a theme the player
    chose must not fail silently. Raises ``OSError``/``yaml.YAMLError`` as read.
    """
    path = Path(theme_dir) / "renderer" / "palette.yaml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping of colour names to [r, g, b]")
    result: Palette = {}
    for name, rgb in raw.items():
        if not (
            isinstance(rgb, list)
            and len(rgb) == 3
            and all(type(c) is int and 0 <= c <= 255 for c in rgb)
        ):
            raise ValueError(f"{path}: {name}: expected [r, g, b] with 0-255 integers")
        result[str(name)] = (rgb[0], rgb[1], rgb[2])
    return result


# ---------------------------------------------------------------------------
# Terminal color capability detection
# ---------------------------------------------------------------------------


class ColorSupport(Enum):
    TRUECOLOR = "24bit"
    COLOR256 = "256"
    COLOR8 = "8"


_FALLBACK_RGB: tuple[int, int, int] = (128, 128, 128)


def term_color_support() -> ColorSupport:
    """Detect terminal color support from ``$COLORTERM`` and ``$TERM``.

    Returns the best supported mode. Defaults to TRUECOLOR if unknown. Reads the
    environment on every call and caches nothing; callers call it once (a session
    builds its :class:`Colors` with :meth:`Colors.detect`) and pass the result on.
    """
    ct = os.environ.get("COLORTERM", "").lower()
    term = os.environ.get("TERM", "").lower()
    if ct in ("truecolor", "24bit"):
        return ColorSupport.TRUECOLOR
    if "256color" in term:
        return ColorSupport.COLOR256
    if term in ("dumb", ""):
        return ColorSupport.COLOR8
    return ColorSupport.TRUECOLOR


# Closest-xterm-256 index for each C64 color name.
_XTERM_256: dict[str, int] = {
    "black": 0,
    "white": 15,
    "red": 124,
    "cyan": 44,
    "purple": 133,
    "green": 70,
    "blue": 57,
    "yellow": 148,
    "brown": 130,
    "light_brown": 100,
    "light_red": 209,
    "dark_grey": 240,
    "grey": 245,
    "light_green": 149,
    "light_blue": 153,
    "light_grey": 250,
}

# Basic 8-color ANSI codes (names map to 0-7).
_BASIC_8: dict[str, int] = {
    "black": 0,
    "red": 1,
    "green": 2,
    "brown": 3,
    "blue": 4,
    "purple": 5,
    "cyan": 6,
    "light_grey": 7,
    "dark_grey": 0,
    "grey": 7,
    "white": 7,
    "light_red": 1,
    "light_green": 2,
    "light_blue": 4,
    "yellow": 3,
    "light_brown": 3,
}


# ---------------------------------------------------------------------------
# ANSI escape generators
# ---------------------------------------------------------------------------


def fg(color_name: str, palette: Palette, support: ColorSupport) -> str:
    """Return the ANSI foreground escape sequence for *color_name*."""
    r, g, b = palette.get(color_name, _PEPTO_FALLBACK.get(color_name, _FALLBACK_RGB))
    if support == ColorSupport.TRUECOLOR:
        return f"\033[38;2;{r};{g};{b}m"
    if support == ColorSupport.COLOR256:
        code = _XTERM_256.get(color_name, 0)
        return f"\033[38;5;{code}m"
    # 8-color fallback
    code = _BASIC_8.get(color_name, 7)
    return f"\033[{30 + code}m"


def bg(color_name: str, palette: Palette, support: ColorSupport) -> str:
    """Return the ANSI background escape sequence for *color_name*."""
    r, g, b = palette.get(color_name, _PEPTO_FALLBACK.get(color_name, _FALLBACK_RGB))
    if support == ColorSupport.TRUECOLOR:
        return f"\033[48;2;{r};{g};{b}m"
    if support == ColorSupport.COLOR256:
        code = _XTERM_256.get(color_name, 0)
        return f"\033[48;5;{code}m"
    # 8-color fallback
    code = _BASIC_8.get(color_name, 7)
    return f"\033[{40 + code}m"


@dataclass(frozen=True)
class Colors:
    """A session's colours: the theme's palette and the terminal's colour support.

    Built once per session (:meth:`detect`) and passed to every renderer, like the
    resolver, so a frame never re-reads the environment and never switches colour
    mode half-way. Tests control the support through ``$COLORTERM``/``$TERM``, read
    when the session starts, or build one directly.
    """

    palette: Palette
    support: ColorSupport

    @classmethod
    def detect(cls, palette: Palette) -> Colors:
        """``palette`` with the colour support the environment reports now."""
        return cls(palette, term_color_support())

    def fg(self, color_name: str) -> str:
        """The foreground escape for ``color_name``."""
        return fg(color_name, self.palette, self.support)

    def bg(self, color_name: str) -> str:
        """The background escape for ``color_name``."""
        return bg(color_name, self.palette, self.support)


# ---------------------------------------------------------------------------
# Reset sequences
# ---------------------------------------------------------------------------

RESET_FG = "\033[39m"
RESET_BG = "\033[49m"
RESET_ALL = "\033[0m"
REVERSE = "\033[7m"
DIM = "\033[2m"

# Short alias for RESET_ALL; the client modules import it under this name.
RESET = RESET_ALL

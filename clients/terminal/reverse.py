"""Reverse video: which theme keys the terminal draws in reverse video, and how.

The C64 prints some texts in reverse video (``{rvon}``): a location's title (``:3025``),
a job's name (``:25015``), the fight banner (``:30025``), the fighters' labels
(``:30115``, ``:30400``). Which keys those are is presentation, so a theme lists them
in its ``renderer/reverse.yaml``; a theme without that file draws every text plain.

An entry is a theme key, or a key pattern in which ``*`` stands for any text
(``locations.*.title``). A template that starts with ``{tab(N)}`` starts its bar at
column ``N``: the padding before it stays plain, as the source moves the cursor
there before ``{rvon}``.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from clients.terminal.palette import RESET_ALL, REVERSE

#: The theme's list of reverse-video keys, inside its directory.
REVERSE_FILE = Path("renderer") / "reverse.yaml"

_LEADING_TAB = re.compile(r"\{tab\((\d+)\)\}")


def read_reverse_keys(theme_dir: str | Path) -> frozenset[str]:
    """The keys (and key patterns) ``theme_dir`` draws in reverse video.

    Empty when the theme has no ``renderer/reverse.yaml``. Raises ``ValueError`` when
    the file is not a list of keys, and ``yaml.YAMLError`` when it is not YAML.
    """
    path = Path(theme_dir) / REVERSE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    raw = yaml.safe_load(text)
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{path}: expected a list of theme keys")
    return frozenset(raw)


def is_reverse(key: str, reverse: frozenset[str]) -> bool:
    """Whether ``key`` is one the theme draws in reverse video."""
    return any(fnmatchcase(key, pattern) for pattern in reverse)


def bar_start(resolver: Any, key: str) -> int:
    """The column a key's reverse bar starts at: ``N`` when its template starts with
    ``{tab(N)}``, else 0. Read off the resolver's ``tree`` (the raw template)."""
    node: Any = getattr(resolver, "tree", None)
    for segment in key.split("."):
        if not isinstance(node, dict):
            return 0
        node = node.get(segment)
    if isinstance(node, str) and (tab := _LEADING_TAB.match(node)):
        return int(tab.group(1))
    return 0


def reverse_text(text: str, start: int = 0, color: str = "") -> str:
    """``text`` in reverse video from column ``start`` of its first row on, every
    later row whole; ``color`` is the foreground code drawn inside the bar."""
    rows = text.split("\n")
    head, rows[0] = rows[0][:start], rows[0][start:]
    return head + "\n".join(f"{REVERSE}{color}{row}{RESET_ALL}" if row else row for row in rows)

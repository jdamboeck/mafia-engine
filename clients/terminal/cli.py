"""The terminal client's command line: ``main()``, its flags, theme loading, the error guard.

Parses the flags, checks them against the config's ``input_ranges``, words and colours
every screen in the chosen theme (``--theme NAME|PATH``, merged over ``classic``) and
hands over to
:func:`clients.terminal.session.play`. Known failures -- a broken theme or config, a
save that cannot be loaded (``--load``), an out-of-range flag -- end in one readable
stderr line, never a traceback.

Run:  ``python -m clients.terminal [--seed N] [--player NAME:GANG ...] [--end-year Y]
      [--score-weight W] [--save PATH] [--watch-ai] [--theme NAME|PATH]``
      or  ``--load PATH``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import NoReturn

import yaml

from engine.config_loader import load_config
from engine.strings import Resolver

from clients.terminal.palette import Palette, load_palette, read_palette_overrides
from clients.terminal.session import (
    _CONFIG_DIR,
    _DEFAULT_SAVE,
    _DEFAULT_SEED,
    _DEFAULT_THEME,
    LoadError,
    _in_range,
    play,
)


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
        resolver, palette = _load_theme(args.theme, resolver)
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
            palette=palette,
        )
    except LoadError as exc:
        _die(str(exc))
    except KeyboardInterrupt:
        sys.exit(130)  # 128 + SIGINT, the shell convention; no traceback, no message


def _load_theme(value: str, classic: Resolver) -> tuple[Resolver, Palette]:
    """The strings and palette for ``--theme VALUE``, each merged over ``classic``'s.

    A path-shaped value -- one containing a path separator, or starting with ``.`` or
    ``~`` -- is a theme directory; any other value names a theme of the game config
    (``themes/<name>``), whatever the current directory holds, so a ``classic/``
    folder in the working directory never shadows the default theme. The theme's
    ``strings/`` are merged over classic's (:meth:`Resolver.from_directory`,
    :meth:`Resolver.with_override`), and its ``renderer/palette.yaml``, if any, over
    classic's palette, so a theme restates only what it changes. Raises ``OSError``,
    ``yaml.YAMLError`` or ``ValueError`` when the theme cannot be loaded.
    """
    classic_palette = load_palette(_CONFIG_DIR, _DEFAULT_THEME)
    if _is_theme_path(value):
        theme_dir = Path(value).expanduser()
    elif value == _DEFAULT_THEME:
        return classic, classic_palette
    else:
        theme_dir = _CONFIG_DIR / "themes" / value
    strings = classic.with_override(Resolver.from_directory(theme_dir).tree)
    return strings, {**classic_palette, **read_palette_overrides(theme_dir)}


def _is_theme_path(value: str) -> bool:
    """Whether ``--theme VALUE`` is a directory path rather than a config theme name."""
    separators = [sep for sep in (os.sep, os.altsep) if sep]
    return any(sep in value for sep in separators) or value.startswith((".", "~"))


def _die(message: str) -> NoReturn:
    """Print one readable line to stderr and exit non-zero."""
    print(message, file=sys.stderr)
    sys.exit(1)

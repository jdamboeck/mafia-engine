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
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, NoReturn

import yaml

from engine.config_loader import load_config, load_game_config
from engine.strings import Resolver

from clients.terminal.palette import Palette, load_palette, read_palette_overrides
from clients.terminal.session import (
    _CONFIG_DIR,
    _DEFAULT_SAVE,
    _DEFAULT_SEED,
    _DEFAULT_THEME,
    LoadError,
    play,
)


def main(argv: list[str] | None = None) -> None:
    # Every line main() prints comes from the theme, so it is loaded first. A broken
    # classic theme still answers --help (in the built-in fallback, help_texts); any
    # other run then ends on the load error's own text, as there are no words to
    # phrase it in.
    classic = load_classic(_CONFIG_DIR)
    help_text = help_texts(classic, "client.cli")
    parser = argparse.ArgumentParser(prog="clients.terminal", description=help_text("description"))
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=help_text("help_seed", seed=_DEFAULT_SEED),
    )
    parser.add_argument(
        "--player",
        dest="players",
        action="append",
        metavar="NAME:GANG",
        help=help_text("help_player"),
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help=help_text("help_end_year"),
    )
    parser.add_argument(
        "--score-weight",
        type=number,
        default=None,
        help=help_text("help_score_weight"),
    )
    parser.add_argument(
        "--load",
        metavar="PATH",
        default=None,
        help=help_text("help_load"),
    )
    parser.add_argument(
        "--save",
        metavar="PATH",
        default=None,
        help=help_text("help_save", save=_DEFAULT_SAVE),
    )
    parser.add_argument(
        "--watch-ai",
        action="store_true",
        help=help_text("help_watch_ai"),
    )
    parser.add_argument(
        "--theme",
        metavar="NAME|PATH",
        default=_DEFAULT_THEME,
        help=help_text("help_theme", theme=_DEFAULT_THEME),
    )
    args = parser.parse_args(argv)
    if not isinstance(classic, Resolver):
        _die(str(classic))
    resolver = classic

    def text(key: str, **params) -> str:
        return resolver.resolve(f"client.cli.{key}", params)

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
        if value is not None and not _in_range(float(value), bounds):
            parser.error(
                text("out_of_range", flag=flag, min=bounds["min"], max=bounds["max"], value=value)
            )
    # The weight's text must also pass the setup's own reading (the C64's parser by the
    # c64_float_score house rule): "1.1_5" is a number to float() but not to setup, and
    # would otherwise be refused only after the title, as a traceback. The rule is the
    # config's (score_weight_accepted); the client holds none of its own.
    if args.score_weight is not None and not load_game_config(
        _CONFIG_DIR
    ).module.score_weight_accepted(args.score_weight, ranges):
        bounds = ranges["score_weight"]
        parser.error(
            text(
                "out_of_range",
                flag="--score-weight",
                min=bounds["min"],
                max=bounds["max"],
                value=args.score_weight,
            )
        )
    players = None
    if args.players:
        players = []
        for spec in args.players:
            name, _, gang = spec.partition(":")
            # The setup asks again for a name outside input_ranges.name_length (:291);
            # here the flag is refused in the same bounds, before the game starts.
            bounds = ranges["name_length"]
            for value in (name, gang or name):
                if not bounds["min"] <= len(value) <= bounds["max"]:
                    parser.error(
                        text(
                            "bad_name",
                            spec=spec,
                            value=value,
                            min=bounds["min"],
                            max=bounds["max"],
                        )
                    )
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


#: The only words the client holds itself: ``--help``'s description when the classic
#: theme -- where every other line lives -- cannot be loaded. The options are then
#: listed without help text. Minimal by design: just enough to say why.
_FALLBACK_DESCRIPTION = "(no help text: theme strings could not be loaded: {error})"


def number(text: str) -> str:
    """``--score-weight``'s type: a number, kept as the text typed.

    The setup parses the text as the setup prompt does (the C64's parser or ``float``,
    by the ``c64_float_score`` house rule), so the flag and the prompt reach the same
    weight for the same text. Text ``float`` cannot read is refused here, as argparse
    refuses a bad ``type``.
    """
    if not math.isfinite(float(text)):  # a ValueError is argparse's "invalid number value"
        raise ValueError(text)
    return text


def _in_range(value: float, bounds: dict) -> bool:
    """``bounds`` is one ``input_ranges`` entry (``{min, max}``) from config.yaml."""
    return bounds["min"] <= value <= bounds["max"]


def load_classic(config_dir: Path) -> Resolver | Exception:
    """The config's ``classic`` strings, or the error that kept them from loading.

    A broken classic theme is returned, not raised, so a command line can still build
    its ``--help`` (:func:`help_texts`) before it gives up on the error.
    """
    try:
        return Resolver.from_config(config_dir, theme=_DEFAULT_THEME)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        return exc


def help_texts(classic: Resolver | Exception, section: str) -> Callable[..., str]:
    """``help_text(key, **params)``: argparse help for ``<section>.<key>`` in ``classic``.

    argparse %-formats help strings, so a literal ``%`` in a theme string is escaped
    before argparse sees it. When ``classic`` is the error that kept the theme from
    loading, the description is the built-in fallback naming it and every option's
    help is empty.
    """

    def help_text(key: str, **params: Any) -> str:
        if isinstance(classic, Resolver):
            text = classic.resolve(f"{section}.{key}", params)
        elif key == "description":
            text = _FALLBACK_DESCRIPTION.format(error=classic)
        else:
            text = ""
        return text.replace("%", "%%")

    return help_text


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

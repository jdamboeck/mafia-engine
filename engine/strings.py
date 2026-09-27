"""The shared, headless string resolver.

The engine emits ``(key, params)`` and never any display text; a **theme** resolves the
key to a parameterized template and the resolver fills the params. This lives in
``engine/`` — NOT in a client — because themes are config-owned and every client
(terminal, eventual server, eventual pygame) needs the identical resolution. It stays
**headless**: it imports nothing from ``clients/``/``server/``/transport.

Themes are files under ``<config_dir>/themes/<theme>/strings/*.yaml``. Each file
contributes a nested key tree (e.g. ``locations.slw.rent_quote``); the resolver
**deep-merges** every file into one tree. A runtime **override** theme deep-merges over
that default (the modding model's runtime-swappable theme axis), so an override may
replace individual leaf keys without restating the rest.

A key is a dotted path into the merged tree; the leaf must be a template string, filled
like ``str.format(**params)`` (matching the verbatim ``{price}``-style strings already in
``themes/classic/strings/``), with numbers styled per *Number style* below. A key that is missing, or that lands on a subtree rather
than a leaf string, raises :class:`MissingKeyError` — a theme gap fails loudly rather than
rendering blank.

Number style
------------
The top-level key ``_format`` is **reserved**: it holds theme settings, not templates,
so resolving any key under it raises :class:`MissingKeyError`. ``_format.numbers``
picks how ``int`` and ``float`` params print (``bool`` and every non-number pass
through untouched)::

    _format:
      numbers: c64      # or: plain

- ``plain`` (the default): exactly ``str.format`` (``22.0``, ``-0.9``; ``{n:g}`` works).
- ``c64``: C64 BASIC's ``str$`` (:func:`engine.c64_numbers.c64_str`) without its
  leading sign-position space for a non-negative (``22``, ``-.9``, ``.5``, ``3000``).
  Any explicit format spec then applies to that *text*, so ``{cash:>6}`` right-aligns
  it; a numeric-only spec (``{n:g}``, ``{n:.2f}``) is a ``ValueError`` under ``c64``.

The ``plain`` default applies only when the style is read and is never written into
:attr:`Resolver.tree`, so an override tree without ``_format`` keeps the base theme's
style. An unknown style (or a non-mapping ``_format``) raises ``ValueError`` on every
construction path.

A template asks for BASIC's ``mid$(str$(n),2)`` with the field spec ``mid$``:
``{n:mid$}`` drops ``str$``'s first character for **any** sign (``-3.5`` prints
``3.5``, ``22.0`` prints ``22``). Anything after ``mid$`` is a spec for the stripped
text (``{n:mid$>3}``). Because the template asks for it explicitly, ``mid$`` applies
under ``plain`` too. (``$`` is not part of Python's format-spec mini-language, so the
name cannot shadow a real spec.)
"""

from __future__ import annotations

import copy
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from engine.c64_numbers import c64_str

__all__ = ["Resolver", "MissingKeyError"]


class MissingKeyError(KeyError):
    """Raised when a key is absent from the theme or does not resolve to a leaf string."""


def _deep_merge(base: dict, override: dict, where: str = "") -> dict:
    """Return a new dict: ``override`` deep-merged over ``base`` (dicts merge key-wise;
    every other value in ``override`` replaces the ``base`` value at that path).

    A non-mapping merged where ``base`` has a mapping (a list over a key tree) is a
    broken theme, not a replacement: it raises ``ValueError`` naming the dotted key.
    """
    if not isinstance(override, dict):
        raise ValueError(f"{where or 'top level'}: expected a mapping, got {_kind(override)}")
    out = copy.deepcopy(base)
    for key, val in override.items():
        path = f"{where}.{key}" if where else str(key)
        if isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val, path)
        else:
            out[key] = copy.deepcopy(val)
    return out


FORMAT_KEY = "_format"
"""Reserved top-level key holding theme settings (``_format.numbers``), not templates."""

NUMBER_STYLES = ("plain", "c64")
"""Accepted values of ``_format.numbers``; the first is the default."""

MID_SPEC = "mid$"
"""Field spec for BASIC's ``mid$(str$(n),2)``: ``str$`` minus its sign position."""


def _number_style(tree: dict) -> str:
    """Read ``_format.numbers`` from ``tree`` (``plain`` when absent), validated."""
    settings = tree.get(FORMAT_KEY, {})
    if not isinstance(settings, dict):
        raise ValueError(f"{FORMAT_KEY}: expected a mapping, got {_kind(settings)}")
    style = settings.get("numbers", NUMBER_STYLES[0])
    if style not in NUMBER_STYLES:
        raise ValueError(
            f"{FORMAT_KEY}.numbers: unknown number style {style!r} (expected one of {', '.join(NUMBER_STYLES)})"
        )
    return style


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _NumberFormatter(string.Formatter):
    """``str.format`` with int/float fields styled per the theme's number style."""

    def __init__(self, style: str) -> None:
        self._style = style

    def format_field(self, value: Any, format_spec: str) -> str:
        if format_spec.startswith(MID_SPEC):
            return format(c64_str(value)[1:], format_spec[len(MID_SPEC) :])
        if self._style == "c64" and _is_number(value):
            return format(c64_str(value).removeprefix(" "), format_spec)
        return format(value, format_spec)


def _kind(value: Any) -> str:
    """A YAML-flavoured name for ``value``'s type, for error messages."""
    return {list: "a list", str: "a string"}.get(type(value), type(value).__name__)


@dataclass(frozen=True)
class Resolver:
    """A resolved theme tree that maps ``(key, params) -> str``.

    Build one with :meth:`from_config` (or :meth:`from_directory`); layer a runtime override with :meth:`with_override`.
    """

    tree: dict

    def __post_init__(self) -> None:
        _number_style(self.tree)  # fail at construction, on every path, not at first resolve

    @classmethod
    def from_config(cls, config_dir: str | Path, *, theme: str = "classic") -> "Resolver":
        """Build a resolver by deep-merging every ``strings/*.yaml`` of ``theme``."""
        return cls.from_directory(Path(config_dir) / "themes" / theme)

    @classmethod
    def from_directory(cls, theme_dir: str | Path) -> "Resolver":
        """Build a resolver from one theme directory: every ``strings/*.yaml`` in it,
        deep-merged. ``from_config`` is this over ``<config_dir>/themes/<theme>``; a
        theme kept outside the config (a mod, a test fixture) is loaded directly."""
        strings_dir = Path(theme_dir) / "strings"
        if not strings_dir.is_dir():
            raise ValueError(f"{strings_dir}: theme strings directory does not exist")
        merged: dict = {}
        # Sort for deterministic merge order (only matters if two files define the same key).
        # A file whose root is not a mapping, or that puts a non-mapping where an
        # earlier file has one, raises ValueError naming the file.
        for path in sorted(strings_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if data is None:  # skip empty files (e.g. .gitkeep-adjacent placeholders)
                continue
            try:
                merged = _deep_merge(merged, data)
            except ValueError as exc:
                raise ValueError(f"{path}: {exc}") from None
        return cls(tree=merged)

    def with_override(self, override: dict) -> "Resolver":
        """Return a new resolver with ``override`` deep-merged over this one's tree.

        Raises ``ValueError`` naming the key where ``override`` puts a non-mapping
        over one of this tree's mappings (or is not a mapping itself).
        """
        return Resolver(tree=_deep_merge(self.tree, override))

    def resolve(self, key: str, params: dict | None = None) -> str:
        """Resolve a dotted ``key`` to its template and fill ``params``.

        Raises :class:`MissingKeyError` if any path segment is absent or the leaf is not a
        string (a key that lands on a subtree is a usage error, not a template).
        """
        if key.split(".", 1)[0] == FORMAT_KEY:
            raise MissingKeyError(f"key {key!r} is reserved for theme settings, not a template")
        node: Any = self.tree
        for segment in key.split("."):
            if not isinstance(node, dict) or segment not in node:
                raise MissingKeyError(f"no theme string for key {key!r}")
            node = node[segment]
        if not isinstance(node, str):
            raise MissingKeyError(f"key {key!r} resolves to a subtree, not a template string")
        return _NumberFormatter(_number_style(self.tree)).format(node, **(params or {}))

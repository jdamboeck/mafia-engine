"""U10 — the shared, headless string resolver (KTD-5).

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
with ``str.format(**params)`` (matching the verbatim ``{price}``-style strings already in
``themes/classic/strings/``). A key that is missing, or that lands on a subtree rather
than a leaf string, raises :class:`MissingKeyError` — a theme gap fails loudly rather than
rendering blank.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["Resolver", "MissingKeyError"]


class MissingKeyError(KeyError):
    """Raised when a key is absent from the theme or does not resolve to a leaf string."""


def _deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict: ``override`` deep-merged over ``base`` (dicts merge key-wise;
    every non-dict value in ``override`` replaces the ``base`` value at that path)."""
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


@dataclass(frozen=True)
class Resolver:
    """A resolved theme tree that maps ``(key, params) -> str``.

    Build one with :meth:`from_config`; layer a runtime override with :meth:`with_override`.
    """

    tree: dict

    @classmethod
    def from_config(cls, config_dir: str | Path, *, theme: str = "classic") -> "Resolver":
        """Build a resolver by deep-merging every ``strings/*.yaml`` of ``theme``."""
        strings_dir = Path(config_dir) / "themes" / theme / "strings"
        if not strings_dir.is_dir():
            raise ValueError(f"{strings_dir}: theme strings directory does not exist")
        merged: dict = {}
        # Sort for deterministic merge order (only matters if two files define the same key).
        for path in sorted(strings_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if data:  # skip empty files (e.g. .gitkeep-adjacent placeholders)
                merged = _deep_merge(merged, data)
        return cls(tree=merged)

    def with_override(self, override: dict) -> "Resolver":
        """Return a new resolver with ``override`` deep-merged over this one's tree."""
        return Resolver(tree=_deep_merge(self.tree, override))

    def resolve(self, key: str, params: dict | None = None) -> str:
        """Resolve a dotted ``key`` to its template and fill ``params``.

        Raises :class:`MissingKeyError` if any path segment is absent or the leaf is not a
        string (a key that lands on a subtree is a usage error, not a template).
        """
        node: Any = self.tree
        for segment in key.split("."):
            if not isinstance(node, dict) or segment not in node:
                raise MissingKeyError(f"no theme string for key {key!r}")
            node = node[segment]
        if not isinstance(node, str):
            raise MissingKeyError(f"key {key!r} resolves to a subtree, not a template string")
        return node.format(**(params or {}))

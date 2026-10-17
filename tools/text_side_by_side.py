#!/usr/bin/env python3
"""The string normalization's review page: today's text beside the C64-text fixture.

Each text unit of the string normalization changes classic-theme strings, content names
or the client's art text, and the user approves the change from an old → new
side-by-side before it lands (R14, KTD-4). This tool writes that side-by-side as
Markdown, so it is generated, never hand-written:

- per source file, each changed key as old → new, ``{placeholders}`` shown as visible
  markers (``⟨rank⟩``) and a row break as ``⏎``;
- a flag on every key whose fold differs from its fixture text's
  (``tests/text_fold.py``): such a key has changed words, not only spelling;
- each key group the client joins on one screen line (``joined_keys.yaml``) rendered
  together, when one of its keys changed (KTD-9).

On a tree whose text matches the fixture it reports no changes.

Run from the engine repo root::

    python tools/text_side_by_side.py [--root TREE] [OUT.md]

``--root`` reads another tree (a scratch copy) and its own fixture; ``OUT.md`` defaults
to stdout.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.text_fold import (  # noqa: E402  (the repo root goes on the path first)
    applying,
    expand,
    fixture_text,
    flat,
    fold,
    joined_groups,
    respellings,
    source_of,
    tree_text,
)

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def show(text: str) -> str:
    """``text`` as one Markdown code span: placeholders as ``⟨name⟩``, a row break as
    ``⏎``, runs of spaces kept (a code span keeps them)."""
    if not text:
        return "*(empty)*"
    shown = _PLACEHOLDER.sub(lambda match: f"⟨{match.group(1)}⟩", text).replace("\n", "⏎")
    fence = "``" if "`" in shown else "`"
    pad = " " if shown.startswith("`") or shown.endswith("`") or not shown.strip() else ""
    return f"{fence}{pad}{shown}{pad}{fence}"


def changes(
    current: Mapping[str, Mapping[str, str]], fixture: Mapping[str, Mapping[str, str]]
) -> dict[str, list[str]]:
    """``{fixture file: [key, ...]}`` of every fixture key whose text changed or is gone."""
    out: dict[str, list[str]] = {}
    for name, keys in fixture.items():
        now = current.get(name, {})
        changed = [key for key, old in keys.items() if now.get(key) != old]
        if changed:
            out[name] = changed
    return out


def side_by_side(root: Path = _ROOT) -> str:
    """The Markdown side-by-side of the tree at ``root`` against its fixture."""
    current, fixture = tree_text(root), fixture_text(root)
    now, then = flat(current), flat(fixture)
    entries = respellings(root)
    changed = changes(current, fixture)

    def folds(key: str) -> bool:
        return key in now and fold(now[key], applying(entries, key)) == fold(then[key])

    def entry(key: str) -> list[str]:
        if key not in now:
            return [f"- `{key}` — **gone from the tree** (was {show(then[key])})"]
        if now[key] == then[key]:
            return [f"- `{key}` — unchanged: {show(now[key])}"]
        lines = [f"- `{key}`", f"  - old: {show(then[key])}", f"  - new: {show(now[key])}"]
        if not folds(key):
            lines.append(
                f"  - **fold differs**: {show(fold(now[key], applying(entries, key)))}"
                f" ≠ {show(fold(then[key]))}"
            )
        return lines

    count = sum(len(keys) for keys in changed.values())
    mismatches = sum(not folds(key) for keys in changed.values() for key in keys)
    added = sorted(set(now) - set(then))
    out = ["# Text side by side", ""]
    if added:
        out += ["## New keys (no C64 text to fold to)", ""]
        out += [f"- `{key}` — {show(now[key])}" for key in added]
        out.append("")
    if not count:
        out += ["No changes: every key's text is its C64-text fixture's.", ""]
        return "\n".join(out)
    out += [f"{count} changed keys, {mismatches} whose fold differs.", ""]
    for name, keys in changed.items():
        out += [f"## {source_of(name).as_posix()}", ""]
        for key in keys:
            out += entry(key)
        out.append("")
    changed_keys = {key for keys in changed.values() for key in keys}
    groups = [
        (group, expand(group["keys"], then))
        for group in joined_groups(root)
        if changed_keys & set(expand(group["keys"], then))
    ]
    if groups:
        out += ["## Joined on screen", ""]
        for group, keys in groups:
            out += [f"### {group['where']}", ""]
            for key in keys:
                out += entry(key)
            out.append("")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", nargs="?", type=Path, help="the Markdown file (default: stdout)")
    parser.add_argument("--root", type=Path, default=_ROOT, help="the tree to read")
    args = parser.parse_args(argv)
    report = side_by_side(args.root)
    if args.out is None:
        sys.stdout.write(report)
    else:
        args.out.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

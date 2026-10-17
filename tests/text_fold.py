"""The fold check's helper: today's text, the C64-text fixture, and the fold between them.

The classic theme, the content names and the client's art text are rewritten from the
C64's spelling (one letter case, ``ae``/``oe``/``ue``/``ss``, 40-column wrap padding)
into today's German. ``tests/fixtures/c64_text/`` freezes the text as it was before that
pass, keyed as it is in the tree; a string still has the source's words when its
:func:`fold` equals its fixture text's fold.

The fold lowercases, writes ä/ö/ü/ß back as ae/oe/ue/ss and collapses each whitespace
run to one space. Four named lists in the fixture undo what that cannot: a spelling
the 1996/2006 reform changed (``reform.yaml``: ``tip`` → ``Tipp``), a word the C64 split
with a hyphen at its line break (``hyphen_joins.yaml``: ``falsch- geld`` →
``Falschgeld``), an author's clear slip, corrected (``author_typos.yaml``:
``poliyei`` → ``Polizei``) and an umlaut restored in a heading spelled with a space
between its letters, where the C64 text's two letters ``A E`` became the one letter
``Ä`` (``letter_spaced.yaml``: ``h a e n d l e r`` → ``H Ä N D L E R``). Each entry
names the keys it applies to and maps the new form back to the source form there, so a
changed word still fails.

The fold is many-to-one: it catches a changed word, not a wrong umlaut or a wrong
capital. The side-by-side review (``tools/text_side_by_side.py``) is the gate for those.

:func:`tree_text` reads the text from a tree (the working tree, or a scratch copy of
it), and :func:`fixture_text` reads the fixture, both as ``{fixture file: {key: text}}``
so ``tests/test_text_fold.py`` and the side-by-side tool compare the same shapes.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: The engine repo root.
ROOT = Path(__file__).resolve().parents[1]

#: The fixture directory, relative to a tree's root.
FIXTURE = Path("tests") / "fixtures" / "c64_text"

_CONFIG = Path("data") / "game_configs" / "mafia_1920s"

#: ``(fixture subdirectory, source directory, key prefix, what is text)``: every YAML
#: file in the source directory is frozen as ``<fixture subdirectory>/<its name>``.
#: A theme key is its own dotted key; the others carry a prefix naming their file, so
#: every fixture key is unique across the fixture.
_YAML_SOURCES = (
    ("strings", _CONFIG / "themes" / "classic" / "strings", "", "all"),
    ("entities", _CONFIG / "entities", "entities", "all"),
    ("encounters", _CONFIG / "content" / "encounters", "encounters", "name"),
    ("scenarios", _CONFIG / "content" / "scenarios", "scenarios", "name"),
)

#: The client's German display literals: the tagline and the location-art lines, read
#: from the module's source (not imported, so a scratch tree is read as it is on disk).
#: The title prompt under the art is the theme's ``client.title.prompt``, indented by
#: the module's ``_TITLE_PROMPT_INDENT``; it is frozen with the art text, as the line
#: the title screen prints.
ASCII_ART = Path("clients") / "terminal" / "ascii_art.py"
_ASCII_ART_FIXTURE = "client/ascii_art.yaml"
_CLIENT_STRINGS = _CONFIG / "themes" / "classic" / "strings" / "client.yaml"

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_WHITESPACE = re.compile(r"\s+")
_LETTER = re.compile(r"[A-Za-zÄÖÜäöüß]")

#: The fixture's named respelling lists, each ``<name>.yaml`` holding a ``<name>:`` list.
LISTS = ("reform", "hyphen_joins", "author_typos", "letter_spaced")


@dataclass(frozen=True)
class Respelling:
    """One named respelling: a reform spelling, a joined wrap hyphen, a fixed typo or a
    letter-spaced umlaut.

    ``source`` is the form the C64 text has, ``new`` the form the new text has, and
    ``keys`` the fixture keys it applies to. Both forms are compared folded.
    """

    source: str
    new: str
    keys: tuple[str, ...]
    #: Which list the entry is on (:data:`LISTS`), for messages.
    kind: str = "reform"

    @property
    def folded_source(self) -> str:
        return fold(self.source)

    @property
    def folded_new(self) -> str:
        return fold(self.new)

    def pattern(self) -> re.Pattern[str]:
        """The folded new form as a whole word (a letter or digit on neither side)."""
        return re.compile(rf"(?<![a-z0-9]){re.escape(self.folded_new)}(?![a-z0-9])")


def fold(text: str, respellings: Iterable[Respelling] = ()) -> str:
    """``text`` folded: lowercased, ä/ö/ü/ß written as ae/oe/ue/ss, each whitespace run
    one space, then each of ``respellings`` mapped from its new form back to its source
    form."""
    folded = _WHITESPACE.sub(" ", text.lower().translate(_UMLAUTS))
    for entry in respellings:
        folded = entry.pattern().sub(entry.folded_source.replace("\\", "\\\\"), folded)
    return folded


def applying(respellings: Iterable[Respelling], key: str) -> list[Respelling]:
    """The entries of ``respellings`` that name ``key``."""
    return [entry for entry in respellings if key in entry.keys]


# --------------------------------------------------------------------------- #
# Reading the text                                                            #
# --------------------------------------------------------------------------- #


def _leaves(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(node, Mapping):
        for name, value in node.items():
            yield from _leaves(value, (*path, str(name)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, (*path, str(index)))
    else:
        yield path, node


def _yaml_text(path: Path, prefix: str, which: str) -> dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # An entity file's one top-level key is its own name (``ranks:``), so its keys
    # are ``entities.ranks.0``; a fight's file has many keys, so the file names it.
    head = () if not prefix else (prefix,) if prefix == "entities" else (prefix, path.stem)
    out: dict[str, str] = {}
    for leaf, value in _leaves(data):
        if not isinstance(value, str) or (which == "name" and leaf[-1] != "name"):
            continue
        out[".".join((*head, *leaf))] = value
    return out


def _literal(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    raise LookupError(f"{ASCII_ART}: no {name} assignment")


def _title_prompt(tree: ast.Module, root: Path) -> str:
    """The line ``title_screen`` prints under the title art: the module's
    ``_TITLE_PROMPT_INDENT``, then the theme's ``client.title.prompt``."""
    client = yaml.safe_load((root / _CLIENT_STRINGS).read_text(encoding="utf-8"))["client"]
    return _literal(tree, "_TITLE_PROMPT_INDENT") + client["title"]["prompt"]


def ascii_art_text(root: Path = ROOT) -> dict[str, str]:
    """The title screen's and the location art's display text in the tree at ``root``:
    ``ascii_art.title_prompt`` (the prompt line), each title line with a letter in it
    (``ascii_art.title.<line>``: the tagline), and each location-art line with a letter
    in it (``ascii_art.location.<location>.<line>``: headings, captions)."""
    tree = ast.parse((root / ASCII_ART).read_text(encoding="utf-8"))
    out = {"ascii_art.title_prompt": _title_prompt(tree, root)}
    for index, line in enumerate(_literal(tree, "_TITLE")):
        if _LETTER.search(line):
            out[f"ascii_art.title.{index}"] = line
    for location, lines in _literal(tree, "_LOCATION_ART").items():
        for index, line in enumerate(lines):
            if _LETTER.search(line):
                out[f"ascii_art.location.{location}.{index}"] = line
    return out


def tree_text(root: Path = ROOT) -> dict[str, dict[str, str]]:
    """The fold-checked text of the tree at ``root``, as ``{fixture file: {key: text}}``."""
    out: dict[str, dict[str, str]] = {}
    for subdir, source, prefix, which in _YAML_SOURCES:
        for path in sorted((root / source).glob("*.yaml")):
            text = _yaml_text(path, prefix, which)
            if text:
                out[f"{subdir}/{path.name}"] = text
    out[_ASCII_ART_FIXTURE] = ascii_art_text(root)
    return out


def source_of(fixture_file: str) -> Path:
    """The tree path (relative to a root) whose text ``fixture_file`` freezes."""
    if fixture_file == _ASCII_ART_FIXTURE:
        return ASCII_ART
    subdir, name = fixture_file.split("/", 1)
    for candidate, source, _prefix, _which in _YAML_SOURCES:
        if candidate == subdir:
            return source / name
    raise LookupError(f"no source for fixture file {fixture_file}")


def fixture_text(root: Path = ROOT) -> dict[str, dict[str, str]]:
    """The C64-text fixture under ``root``, as ``{fixture file: {key: text}}``."""
    base = root / FIXTURE
    out: dict[str, dict[str, str]] = {}
    for path in sorted(base.glob("*/*.yaml")):
        out[path.relative_to(base).as_posix()] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return out


def flat(text: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """``{key: text}`` over every file of ``text`` (fixture keys are unique)."""
    return {key: value for keys in text.values() for key, value in keys.items()}


@functools.cache
def _fixture_keys(root: Path) -> Mapping[str, str]:
    return flat(fixture_text(root))


def fixture_value(key: str, root: Path = ROOT) -> str:
    """The fixture text of one key (the fixture is read once per root)."""
    return _fixture_keys(root)[key]


def respellings(root: Path = ROOT) -> list[Respelling]:
    """The reform list, the hyphen joins and the author typos, as one list of entries."""
    out: list[Respelling] = []
    for kind in LISTS:
        data = yaml.safe_load((root / FIXTURE / f"{kind}.yaml").read_text(encoding="utf-8"))
        for entry in data[kind] or ():
            out.append(Respelling(entry["source"], entry["new"], tuple(entry["keys"]), kind))
    return out


def joined_groups(root: Path = ROOT) -> list[dict[str, Any]]:
    """The key groups the client joins on one screen line (``joined_keys.yaml``)."""
    data = yaml.safe_load((root / FIXTURE / "joined_keys.yaml").read_text(encoding="utf-8"))
    return list(data["joined_keys"])


def expand(patterns: Iterable[str], keys: Iterable[str]) -> list[str]:
    """The keys matching each of ``patterns`` (``fnmatch`` globs), in pattern order."""
    keys = list(keys)
    out: list[str] = []
    for pattern in patterns:
        out.extend(key for key in keys if fnmatch.fnmatchcase(key, pattern) and key not in out)
    return out


# --------------------------------------------------------------------------- #
# The check                                                                   #
# --------------------------------------------------------------------------- #


def fold_failures(
    current: Mapping[str, str], fixture: Mapping[str, str], entries: Iterable[Respelling]
) -> list[str]:
    """One message per fixture key that is gone from ``current`` or whose fold differs."""
    entries = list(entries)
    failures: list[str] = []
    for key, source in fixture.items():
        if key not in current:
            failures.append(f"{key}: in the fixture but gone from the tree")
            continue
        new, old = fold(current[key], applying(entries, key)), fold(source)
        if new != old:
            failures.append(f"{key}: folds to {new!r}, the fixture to {old!r}")
    return failures


def unused_entries(current: Mapping[str, str], entries: Iterable[Respelling]) -> list[str]:
    """One message per entry and key where the entry's new form is not in the key's text."""
    unused: list[str] = []
    for entry in entries:
        for key in entry.keys:
            if key not in current or not entry.pattern().search(fold(current[key])):
                unused.append(f"{entry.kind} {entry.source!r} -> {entry.new!r}: unused at {key}")
    return unused

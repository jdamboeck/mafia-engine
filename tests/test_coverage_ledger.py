"""Coverage ledger checker: every line block of ``mf-prg.bas`` is ported or a named exception.

``docs/coverage-ledger.yaml`` lists every block of the decompiled source (R23, KTD-15),
each ``ported`` or an ``exception`` with a category and a reason. This module holds it
to the source and to the port's citations.

Rules (KTD-15)
--------------
- **Block.** A block starts at the listing's first line or at any jump target, and runs
  to the line before the next start. A jump target is every number after ``goto``,
  ``go to``, ``gosub``, ``then`` (the implicit GOTO of ``then1100``) or ``run``, the
  whole list of an ``on x goto``/``on x gosub`` (``onwgoto12010,12100``) included. The
  listing is crunched, so keywords are glued to their operands (``ifzgoto20``) and
  ``thenprint`` is no target. String literals and ``rem`` remarks are cut out first.
- **Citation in code.** A citation (:func:`tests.test_citations.citations_in_file`,
  quoted or not, and a house-rules catalogue entry's ``citation``) in :data:`CODE_ROOTS`:
  the port's engine, configs, clients and asset tools. A citation cites a block when its
  line falls in the block, or its range overlaps it. Tests and docs do not count: a test
  checks the port and a doc explains it, but only code shows the port does the block.
- **Checks.** A ``ported`` block that no code cites fails, as does an ``exception`` that
  code does cite, and a ledger whose blocks are not the source's (a new jump target
  splits a block: the new block is missing from the ledger). Only that last check and
  the ``player_facing`` flags need ``../research/``; without it, they skip with a reason
  naming the path, and the others still run, as the ledger commits its boundaries and
  flags.
- **Narrow.** A citation spanning many blocks (a whole location's ``:12000-12335``
  header) would keep every block in it looking ported. So a ported block also needs a
  citation that spans at most :data:`NARROW` blocks, unless :data:`BROAD_ONLY` names it
  with its reason; an entry there that is no longer needed fails.
- **Player-facing.** A block whose code (outside strings and ``rem``) holds ``print``,
  ``input`` or ``get`` (not the disk's ``input#1``) or the key wait ``wait198`` reaches
  the player; each block commits it as ``player_facing``, checked against the source
  (with ``../research/`` only, like the boundaries). Game logic citing such a block shows
  the port does its logic, not that the player sees its screen, so a ported
  player-facing block also needs a citation where its text is shown, a theme's
  ``strings/*.yaml`` or ``clients/`` (:func:`shows_text`), or ``screen: deferred`` with a
  ``screen_reason`` naming its issue. A deferral that is no longer needed fails.
- **Exception.** ``category`` is one of :data:`CATEGORIES`; ``reason`` says why the port
  leaves the block out. A ``deferred`` block is game behavior not ported yet, and its
  reason names the follow-up issue (``#146``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
import yaml

from tests import helpers
from tests.helpers import load_source, parse_source
from tests.test_citations import (
    CATALOGUE_NAME,
    Cited,
    catalogue_quotes,
    citations_in_file,
)

_REPO = Path(__file__).resolve().parents[1]
LEDGER = _REPO / "docs" / "coverage-ledger.yaml"

#: The trees whose citations count as the port's: code, config data, clients, tools.
CODE_ROOTS = ("engine", "clients", "data", "tools")
_SUFFIXES = {".py", ".md", ".yaml", ".yml"}

STATUSES = ("ported", "exception")
#: Why a block can be left out of the port.
CATEGORIES: Mapping[str, str] = {
    "hardware": "C64 machine setup: VIC/CIA registers, the RUN/STOP vector",
    "disk": "loading a file from disk",
    "sprite": "sprite registers",
    "graphics": "the location-picture toggle and loads",
    "screen": "drawing or erasing on the C64 screen, with no game state",
    "sound": "SID registers or the sound routine",
    "debug": "developer aids",
    "dead": "nothing runs: a remark or an unreachable line",
    "deferred": "game behavior not ported yet; the reason names its follow-up issue",
}
_ISSUE = re.compile(r"#\d+")
#: The one ``screen`` value: a ported block whose screen the player does not see yet.
SCREEN_DEFERRED = "deferred"
#: Where player-facing text lives: a theme's string files, or a client.
_SHOWN = re.compile(
    r"(?:data/game_configs/[^/]+/themes/(?:[^/]+/)*strings/[^/]+\.ya?ml|clients/.+)$"
)

#: The most blocks a citation may span and still pin a block down.
NARROW = 3
#: Ported blocks that only citations wider than :data:`NARROW` cite, each with why.
BROAD_ONLY: Mapping[int, str] = {
    3010: "the revisit's screen cache (:3010-3012 restores the saved menu screen); "
    "the client draws the menu afresh, under the location menu's :3000-3045",
    12000: "a bare rem the location dispatch lands on, under the pub's :12000-12335",
    30200: "a screen poke (the shooter drawn black while aiming), under :30200-30310",
    30211: "a screen poke (the shooter's colour put back), under :30200-30310",
    30224: "a screen poke (the shot's trail erased), under :30200-30310",
}


# --------------------------------------------------------------------------- #
# Source blocks                                                               #
# --------------------------------------------------------------------------- #
_STRING = re.compile(r'"[^"]*(?:"|$)')
_REM = re.compile(r"(?:^|:|then)\s*rem.*$")
_JUMP = re.compile(r"(?:goto|go\s*to|gosub|then|run)\s*(?P<targets>\d+(?:\s*,\s*\d+)*)")


def jump_targets(statement: str) -> set[int]:
    """The line numbers one source line can jump to."""
    code = _REM.sub("", _STRING.sub('""', statement))
    return {
        int(target)
        for match in _JUMP.finditer(code)
        for target in re.split(r"\s*,\s*", match.group("targets"))
    }


#: A line the player sees or answers: a ``print``, an ``input`` or ``get`` (not the
#: disk's ``input#1``), or the key wait ``wait198`` (another address waits on hardware).
_PLAYER_FACING = re.compile(r"(?:print|input|get)(?!\s*#)|wait\s*198\b")


def player_facing(statement: str) -> bool:
    """Whether one source line prints, prompts or waits for a key, outside strings and rems."""
    return bool(_PLAYER_FACING.search(_REM.sub("", _STRING.sub('""', statement))))


def block_starts(source: Mapping[int, str]) -> set[int]:
    """The first line plus every jump target."""
    starts = {min(source)}
    for statement in source.values():
        starts |= jump_targets(statement)
    return starts


def source_blocks(source: Mapping[int, str]) -> list[tuple[int, int]]:
    """``(first line, last line)`` of every block, in order."""
    numbers = sorted(source)
    starts = sorted(start for start in block_starts(source) if start in source)
    blocks: list[tuple[int, int]] = []
    for index, start in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else numbers[-1] + 1
        blocks.append((start, max(n for n in numbers if start <= n < stop)))
    return blocks


def player_facing_blocks(source: Mapping[int, str]) -> dict[tuple[int, int], bool]:
    """Every block of ``source``, and whether any of its lines is player-facing."""
    return {
        (lo, hi): any(player_facing(text) for n, text in source.items() if lo <= n <= hi)
        for lo, hi in source_blocks(source)
    }


# --------------------------------------------------------------------------- #
# Ledger                                                                      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Block:
    start: int
    end: int
    status: str
    category: str | None = None
    reason: str | None = None
    player_facing: bool | None = None
    screen: str | None = None
    screen_reason: str | None = None

    @property
    def label(self) -> str:
        return f":{self.start}" if self.start == self.end else f":{self.start}-{self.end}"


def parse_ledger(text: str) -> tuple[list[Block], list[str]]:
    """The ledger's blocks and every structural error in it."""
    data = yaml.safe_load(text) or {}
    blocks: list[Block] = []
    errors: list[str] = []
    for entry in data.get("blocks") or []:
        block = Block(
            int(entry["start"]),
            int(entry["end"]),
            str(entry.get("status")),
            entry.get("category"),
            entry.get("reason"),
            entry.get("player_facing"),
            entry.get("screen"),
            entry.get("screen_reason"),
        )
        blocks.append(block)
        if block.status not in STATUSES:
            errors.append(f"{block.label}: status {block.status!r} is not one of {STATUSES}")
        elif block.status == "exception":
            if block.category not in CATEGORIES:
                errors.append(f"{block.label}: exception category {block.category!r} unknown")
            if not block.reason:
                errors.append(f"{block.label}: an exception needs a reason")
            if block.category == "deferred" and not _ISSUE.search(block.reason or ""):
                errors.append(f"{block.label}: a deferred block's reason names its issue")
        elif block.category or block.reason:
            errors.append(f"{block.label}: a ported block carries no category or reason")
        if not isinstance(block.player_facing, bool):
            errors.append(f"{block.label}: player_facing is not true or false")
        if block.screen is not None or block.screen_reason is not None:
            if block.status != "ported":
                errors.append(f"{block.label}: only a ported block defers its screen")
            elif block.screen != SCREEN_DEFERRED:
                errors.append(f"{block.label}: screen {block.screen!r} is not {SCREEN_DEFERRED!r}")
            elif not _ISSUE.search(block.screen_reason or ""):
                errors.append(f"{block.label}: a deferred screen's screen_reason names its issue")
        if block.end < block.start:
            errors.append(f"{block.label}: ends before it starts")
    for before, after in zip(blocks, blocks[1:]):
        if after.start <= before.end:
            errors.append(f"{after.label}: starts inside or before {before.label}")
    if not blocks:
        errors.append("the ledger lists no blocks")
    return blocks, errors


def code_citations(repo: Path = _REPO) -> list[Cited]:
    """Every citation in the port's code (:data:`CODE_ROOTS`)."""
    found: list[Cited] = []
    for root in CODE_ROOTS:
        for path in sorted((repo / root).rglob("*")):
            if path.suffix not in _SUFFIXES or not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(repo).as_posix()
            text = path.read_text(encoding="utf-8")
            found.extend(citations_in_file(rel, text))
            if path.name == CATALOGUE_NAME and path.parent.name == "content":
                found.extend(
                    Cited(q.path, q.lineno, q.citation) for q in catalogue_quotes(rel, text)
                )
    return found


def cites(cited: Cited, block: Block) -> bool:
    citation = cited.citation
    return any(block.start <= n <= block.end for n in citation.numbers) or any(
        low <= block.end and block.start <= high for low, high in citation.ranges
    )


def check_citations(blocks: Sequence[Block], citations: Iterable[Cited]) -> list[str]:
    """A ported block no code cites, and an exception code does cite, each fail."""
    citations = list(citations)
    errors: list[str] = []
    for block in blocks:
        citers = [c for c in citations if cites(c, block)]
        if block.status == "ported" and not citers:
            errors.append(
                f"{block.label}: no citation in code and no exception "
                "(cite it where the port does it, or except it with a reason)"
            )
        elif block.status == "exception" and citers:
            where = ", ".join(f"{c.path}:{c.lineno}" for c in citers[:3])
            errors.append(
                f"{block.label}: an exception ({block.category}) that code cites: {where}"
            )
    return errors


def check_narrow(
    blocks: Sequence[Block], citations: Iterable[Cited], allowed: Mapping[int, str]
) -> list[str]:
    """A ported block only wide citations cite fails, unless ``allowed`` names it."""
    citations = list(citations)
    width = {id(c): sum(1 for b in blocks if cites(c, b)) for c in citations}
    errors: list[str] = []
    ported = {b.start for b in blocks if b.status == "ported"}
    for block in blocks:
        citers = [c for c in citations if cites(c, block)]
        if block.status != "ported" or not citers:
            continue
        narrow = any(width[id(c)] <= NARROW for c in citers)
        if not narrow and block.start not in allowed:
            widest = min(citers, key=lambda c: width[id(c)])
            errors.append(
                f"{block.label}: only citations wider than {NARROW} blocks cite it "
                f"(the narrowest, {widest.path}:{widest.lineno}, spans {width[id(widest)]}); "
                "cite it where the port does it"
            )
        elif narrow and block.start in allowed:
            errors.append(f"{block.label}: allowed as broad-only, but a narrow citation cites it")
    errors += [
        f":{start}: allowed as broad-only, but no ported block"
        for start in sorted(set(allowed) - ported)
    ]
    return errors


def shows_text(cited: Cited) -> bool:
    """Whether a citation sits where the player-facing text lives: a theme string, a client."""
    return bool(_SHOWN.match(cited.path))


def check_player_facing(blocks: Sequence[Block], citations: Iterable[Cited]) -> list[str]:
    """A ported player-facing block needs a citation where its text is shown, or a deferral.

    A deferral that is no longer needed (a theme string or client cites the block, or the
    block is not player-facing) fails, so it cannot outlive the gap it records.
    """
    shown = [c for c in citations if shows_text(c)]
    errors: list[str] = []
    for block in blocks:
        if block.status != "ported":
            continue
        showers = [c for c in shown if cites(c, block)]
        deferred = block.screen == SCREEN_DEFERRED
        if deferred and not block.player_facing:
            errors.append(
                f"{block.label}: its screen is deferred, but it is not player-facing "
                "(drop the deferral)"
            )
        elif deferred and showers:
            where = ", ".join(f"{c.path}:{c.lineno}" for c in showers[:3])
            errors.append(
                f"{block.label}: its screen is deferred, but a theme string or client cites "
                f"it: {where} (drop the deferral)"
            )
        elif block.player_facing and not deferred and not showers:
            errors.append(
                f"{block.label}: player-facing, but only game logic cites it (cite it where "
                "its text is shown: a theme string or clients/; or defer its screen)"
            )
    return errors


def check_player_facing_flags(blocks: Sequence[Block], source: Mapping[int, str]) -> list[str]:
    """Each block's committed ``player_facing`` flag is the source's."""
    facing = player_facing_blocks(source)
    errors: list[str] = []
    for block in blocks:
        actual = facing.get((block.start, block.end))
        if actual is None or actual == block.player_facing:
            continue  # a block the source has not is check_boundaries' to report
        errors.append(
            f"{block.label}: player_facing is false, but the source prints, prompts or "
            "waits for a key"
            if actual
            else f"{block.label}: player_facing is true, but the source shows nothing"
        )
    return errors


def check_boundaries(blocks: Sequence[Block], source: Mapping[int, str]) -> list[str]:
    """The ledger's blocks are exactly the source's."""
    ledger = {(b.start, b.end) for b in blocks}
    actual = set(source_blocks(source))
    errors = [f":{lo}-{hi}: a block missing from the ledger" for lo, hi in sorted(actual - ledger)]
    errors += [
        f":{lo}-{hi}: a ledger block the source has not" for lo, hi in sorted(ledger - actual)
    ]
    return errors


def _ledger() -> list[Block]:
    blocks, errors = parse_ledger(LEDGER.read_text(encoding="utf-8"))
    assert errors == [], "\n".join(errors)
    return blocks


# --------------------------------------------------------------------------- #
# Tests: the parser and the checks, on synthetic sources                      #
# --------------------------------------------------------------------------- #
_SOURCE = parse_source(
    "    1 ifzgoto20\n"
    '   10 print"goto99":gosub100,200\n'
    "   20 onwgoto300,310:rem goto400\n"
    "  100 ifx=1then300\n"
    '  110 ifx=2thenprint"a":return\n'
    "  200 return\n"
    "  300 x=1\n"
    "  310 go to 1:run\n"
)


def test_jump_targets_are_read_from_crunched_lines() -> None:
    assert jump_targets("ifzgoto20") == {20}
    assert jump_targets("onwgoto12010,12100,12200") == {12010, 12100, 12200}
    assert jump_targets("gosub1200,2000,27000") == {1200, 2000, 27000}
    assert jump_targets("ifx=1then1100") == {1100}
    assert jump_targets('ifx=1thenprint"a"') == set()
    assert jump_targets('print"goto99"') == set()
    assert jump_targets("x=1:rem goto400") == set()
    assert jump_targets("go to 50:run 60") == {50, 60}
    assert jump_targets("poke198,0:wait198,1:run") == set()


def test_blocks_start_at_the_first_line_and_every_target() -> None:
    assert source_blocks(_SOURCE) == [
        (1, 10),
        (20, 20),
        (100, 110),
        (200, 200),
        (300, 300),
        (310, 310),
    ]


def _blocks(*statuses: str) -> list[Block]:
    bounds = source_blocks(_SOURCE)
    return [
        Block(lo, hi, s, "screen", "erases a row") if s == "exception" else Block(lo, hi, s)
        for (lo, hi), s in zip(bounds, statuses)
    ]


def _cited(text: str) -> list[Cited]:
    return citations_in_file("engine/m.py", text)


_ALL = "# :1-20 boot\n# :100, :200 and :300-310\n"


def test_a_fully_cited_ledger_passes() -> None:
    assert check_citations(_blocks(*["ported"] * 6), _cited(_ALL)) == []


def test_a_block_with_no_citation_and_no_exception_fails() -> None:
    errors = check_citations(_blocks(*["ported"] * 6), _cited("# :1-20 boot\n# :100, :300-310\n"))
    assert errors == [
        ":200: no citation in code and no exception "
        "(cite it where the port does it, or except it with a reason)"
    ]


def test_an_exception_that_code_cites_fails() -> None:
    blocks = _blocks("ported", "ported", "ported", "exception", "ported", "ported")
    assert check_citations(blocks, _cited("# :1-20\n# :100 and :300-310\n")) == []
    errors = check_citations(blocks, _cited(_ALL))
    assert errors == [":200: an exception (screen) that code cites: engine/m.py:2"]


def test_a_range_cites_every_block_it_overlaps() -> None:
    blocks = _blocks(*["ported"] * 6)
    assert check_citations(blocks, _cited("# :5-310\n")) == []
    assert check_citations(blocks, _cited("# :5-15\n"))[0].startswith(":20:")


def test_a_new_block_missing_from_the_ledger_fails() -> None:
    blocks = _blocks(*["ported"] * 6)
    assert check_boundaries(blocks, _SOURCE) == []
    grown = dict(_SOURCE)
    grown[200] = "goto110"  # a new target splits :100-110
    assert check_boundaries(blocks, grown) == [
        ":100-100: a block missing from the ledger",
        ":110-110: a block missing from the ledger",
        ":100-110: a ledger block the source has not",
    ]


def test_a_block_only_a_wide_citation_cites_fails() -> None:
    blocks = _blocks(*["ported"] * 6)
    wide = "# :1-310\n"
    errors = check_narrow(blocks, _cited(wide + "# :1-20, :200-310\n"), {})
    assert errors == [
        ":100-110: only citations wider than 3 blocks cite it "
        "(the narrowest, engine/m.py:1, spans 6); cite it where the port does it"
    ]
    assert check_narrow(blocks, _cited(wide + "# :1-20, :200-310\n"), {100: "why"}) == []
    assert check_narrow(blocks, _cited(wide + "# :100\n# :1-20, :200-310\n"), {100: "why"}) == [
        ":100-110: allowed as broad-only, but a narrow citation cites it"
    ]
    assert check_narrow(blocks, _cited(_ALL), {999: "why"}) == [
        ":999: allowed as broad-only, but no ported block"
    ]


def test_the_ledger_structure_is_checked() -> None:
    text = (
        "blocks:\n"
        "  - {start: 1, end: 10, status: exception, category: hardware, player_facing: false}\n"
        "  - {start: 5, end: 20, status: exception, category: deferred, reason: later,\n"
        "     player_facing: false}\n"
        "  - {start: 100, end: 110, status: exception, category: sprites, reason: x,\n"
        "     player_facing: false}\n"
        "  - {start: 200, end: 200, status: ported, reason: why, player_facing: false}\n"
        "  - {start: 300, end: 300, status: done, player_facing: false}\n"
    )
    assert parse_ledger(text)[1] == [
        ":1-10: an exception needs a reason",
        ":5-20: a deferred block's reason names its issue",
        ":100-110: exception category 'sprites' unknown",
        ":200: a ported block carries no category or reason",
        ":300: status 'done' is not one of ('ported', 'exception')",
        ":5-20: starts inside or before :1-10",
    ]


def test_player_facing_lines_are_read_from_crunched_code() -> None:
    assert player_facing('print"{clr}eigenschaften:"')
    assert player_facing('ifx=1thenprint"a"')
    assert player_facing('getx$:ifx$=""then100')
    assert player_facing('inputx$:ifx$<"1"then205')
    assert player_facing("poke198,0:wait198,1:poke198,0")
    assert player_facing("wait 198, 1")
    assert not player_facing("wait53265,128")  # a raster wait, no key
    assert not player_facing("wait1980,1")
    assert not player_facing("input#1,x$:print# 4,a:get#2,a$")  # disk I/O
    assert not player_facing('x$="print":y$="get input"')
    assert not player_facing("x=1:rem print the screen")
    assert not player_facing('ifx=1thenrem print"a"')


# :1-10 prints and :100-110 prints after then; :20 is a key wait; :200 prints only
# in a string; :300 reads a disk file; :310 waits on the raster.
_SCREENS = parse_source(
    "    1 ifzgoto20\n"
    '   10 print"goto99":gosub100,200\n'
    "   20 onwgoto300,310:poke198,0:wait198,1\n"
    "  100 ifx=1then300\n"
    '  110 ifx=2thenprint"a":return\n'
    '  200 x$="print":rem input\n'
    "  300 input#1,x$:close1\n"
    "  310 wait53265,128:go to 1:run\n"
)


def _flagged(*, defer: Mapping[int, str] | None = None) -> list[Block]:
    """Every ``_SCREENS`` block ported, flagged from the source, deferrals per ``defer``."""
    defer = defer or {}
    facing = player_facing_blocks(_SCREENS)
    return [
        Block(
            lo,
            hi,
            "ported",
            player_facing=facing[lo, hi],
            screen="deferred" if lo in defer else None,
            screen_reason=defer.get(lo),
        )
        for lo, hi in source_blocks(_SCREENS)
    ]


_THEME = "data/game_configs/g/themes/classic/strings/s.yaml"


def test_player_facing_blocks_are_flagged_from_the_source() -> None:
    assert [b.label for b in _flagged() if b.player_facing] == [":1-10", ":20", ":100-110"]


def test_a_player_facing_block_cited_only_by_game_logic_fails() -> None:
    blocks = _flagged()
    logic = citations_in_file("engine/m.py", _ALL)
    theme = citations_in_file(_THEME, "# :1-20 and :100\n")
    client = citations_in_file("clients/terminal/s.py", "# :1-20 and :100\n")
    errors = check_player_facing(blocks, logic)
    assert errors == [
        f":{label}: player-facing, but only game logic cites it "
        "(cite it where its text is shown: a theme string or clients/; or defer its screen)"
        for label in ("1-10", "20", "100-110")
    ]
    assert check_player_facing(blocks, logic + theme) == []
    assert check_player_facing(blocks, logic + client) == []
    # a theme file outside strings/ (a renderer palette) shows no text
    palette = citations_in_file("data/game_configs/g/themes/classic/renderer/p.yaml", "# :20\n")
    assert [e[:4] for e in check_player_facing(blocks, logic + palette)] == [":1-1", ":20:", ":100"]


def test_a_screen_deferral_covers_a_player_facing_block_until_it_is_shown() -> None:
    logic = citations_in_file("engine/m.py", _ALL)
    theme = citations_in_file(_THEME, "# :1-20\n")
    deferred = _flagged(defer={100: "the screen is not drawn yet, #153"})
    assert check_player_facing(deferred, logic + theme) == []
    shown = citations_in_file(_THEME, "# :1-20 and :110\n")
    assert check_player_facing(deferred, logic + shown) == [
        ":100-110: its screen is deferred, but a theme string or client cites it: "
        f"{_THEME}:1 (drop the deferral)"
    ]
    stray = _flagged(defer={100: "#153", 300: "#153"})
    assert check_player_facing(stray, logic + theme) == [
        ":300: its screen is deferred, but it is not player-facing (drop the deferral)"
    ]


def test_a_screen_deferral_is_checked_in_the_ledger() -> None:
    text = (
        "blocks:\n"
        "  - {start: 1, end: 10, status: ported, player_facing: true,\n"
        "     screen: deferred, screen_reason: later}\n"
        "  - {start: 20, end: 20, status: ported, player_facing: true, screen: deferred}\n"
        "  - {start: 100, end: 110, status: ported, player_facing: true,\n"
        "     screen: hidden, screen_reason: '#153'}\n"
        "  - {start: 200, end: 200, status: exception, category: dead, reason: a rem,\n"
        "     player_facing: false, screen: deferred, screen_reason: '#153'}\n"
        "  - {start: 300, end: 300, status: ported}\n"
        "  - {start: 310, end: 310, status: ported, player_facing: 'yes'}\n"
        "  - {start: 320, end: 320, status: ported, player_facing: true,\n"
        "     screen: deferred, screen_reason: 'not drawn yet, #153'}\n"
    )
    assert parse_ledger(text)[1] == [
        ":1-10: a deferred screen's screen_reason names its issue",
        ":20: a deferred screen's screen_reason names its issue",
        ":100-110: screen 'hidden' is not 'deferred'",
        ":200: only a ported block defers its screen",
        ":300: player_facing is not true or false",
        ":310: player_facing is not true or false",
    ]


def test_a_player_facing_flag_the_source_disagrees_with_fails() -> None:
    blocks = _flagged()
    assert check_player_facing_flags(blocks, _SCREENS) == []
    flipped = [
        replace(b, player_facing=not b.player_facing) if b.start in (20, 200) else b for b in blocks
    ]
    assert check_player_facing_flags(flipped, _SCREENS) == [
        ":20: player_facing is false, but the source prints, prompts or waits for a key",
        ":200: player_facing is true, but the source shows nothing",
    ]


# --------------------------------------------------------------------------- #
# Tests: the real tree                                                        #
# --------------------------------------------------------------------------- #
def test_every_block_is_cited_or_excepted() -> None:
    citations = code_citations()
    assert len(citations) > 1000, "the scan found almost no citations: the parser is broken"
    errors = check_citations(_ledger(), citations)
    assert errors == [], "\n".join(errors)


def test_every_ported_block_has_a_narrow_citation() -> None:
    errors = check_narrow(_ledger(), code_citations(), BROAD_ONLY)
    assert errors == [], "\n".join(errors)


def test_the_ledger_blocks_are_the_sources() -> None:
    source = load_source(helpers.MF_PRG)  # skips, naming the path, without ../research/
    errors = check_boundaries(_ledger(), source)
    assert errors == [], "\n".join(errors)


def test_every_player_facing_block_is_shown_or_deferred() -> None:
    errors = check_player_facing(_ledger(), code_citations())
    assert errors == [], "\n".join(errors)


def test_the_player_facing_flags_are_the_sources() -> None:
    source = load_source(helpers.MF_PRG)  # skips, naming the path, without ../research/
    errors = check_player_facing_flags(_ledger(), source)
    assert errors == [], "\n".join(errors)


def test_without_the_research_tree_only_the_source_checks_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    absent = tmp_path / "research" / "mf-prg.bas"
    monkeypatch.setattr(helpers, "MF_PRG", absent)
    with pytest.raises(pytest.skip.Exception, match=re.escape(str(absent))):
        test_the_ledger_blocks_are_the_sources()
    with pytest.raises(pytest.skip.Exception, match=re.escape(str(absent))):
        test_the_player_facing_flags_are_the_sources()
    test_every_block_is_cited_or_excepted()
    test_every_ported_block_has_a_narrow_citation()
    test_every_player_facing_block_is_shown_or_deferred()

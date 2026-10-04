"""Citation checker: every quoted BASIC fragment matches the ``mf-prg.bas`` line it cites.

Code, tests and docs back a claim about the original game by citing a line of the
decompiled source and quoting the part of it the claim rests on (a ``#`` comment reading
``:30108 `s=1-(s=1)` `` is one). A quote that drifted from its line (a typo, or the
right text under the wrong line number) reads as proof while proving nothing. This
module finds every such quote in the tree and checks it against the source (KTD-9),
and checks that every test a learning doc names by name exists (KTD-10), as does every
entry of a parametrized port test it names (``test_port_matches_basic`` (entry
``slw rent``)), read from :data:`tests.test_ports.PORTS` through the test's own
``parametrize`` mark.

Rules (KTD-9)
-------------
- **Citation.** ``mf-prg.bas:NNNN`` or a bare ``:NNNN``; either may continue as a range
  (``:4000-4090``) or a list (``:2035/2040``, ``mf-prg.bas:12106,12110,12165``). A bare
  ``:NNNN`` counts only where the colon does not follow a word character, a closing
  bracket, a quote or a dot, so ``12:30``, ``a[1:10]`` and ``tests/helpers.py:35`` are
  not citations. A backtick span whose whole content is a citation is a citation.
  Citations joined by ``/``, ``,``, ``+``, ``and``, ``or`` or ``vs.`` form one group
  (``:30015`` / ``:30115``); any line of the group may hold the fragment.
- **Quoted fragment.** The first backtick span after a citation group, when either
  - only whitespace and at most one separator (``—``, ``-``, ``:``, ``,``, ``(``, ``=``,
    ``'s``, or the word ``is`` or ``sets``) stand between them, or
  - the span looks like crunched BASIC (:func:`looks_like_basic`: no whitespace, no
    Python operators or attribute access, variables of one or two letters once the
    keywords are cut out) and the gap is at most :data:`MAX_GAP` characters that
    neither close a bracket or clause (``)``, ``]``, ``;``), start a new sentence, nor
    end in a negation (``:12075`` has no ``gosub1160``).
  A span holding only line numbers (``mf-prg.bas:30106``, ``30108``) continues the group
  instead. In Python code and docstrings only double-backtick spans count (single
  backticks there are Sphinx roles); in ``#`` comments and Markdown a span of any
  backtick length counts. Other spans, often Python, are ignored.
- **Blocks.** A citation stays open across line breaks within its block: consecutive
  ``#`` comment lines, consecutive code/docstring lines, or a Markdown paragraph; a
  blank line ends it. So a quote that wraps onto the next line is paired, but across a
  line break only a BASIC-looking span is (``:30245``, then ``energie=35`` on the next
  line, is not).
- **Heading citation.** A ``#`` comment that opens with a bare line number or range
  and an em dash (``# 13065 — no old weapon``) cites it.
- **Match.** Case and all whitespace are dropped from both sides; the fragment must be
  a substring of one of the cited lines (any line within a range). A fragment that
  elides with ``...`` must have each piece, in order, within one line.
- **Allow-list.** :data:`ALLOWED` names the (file, fragment) pairs that paraphrase the
  line by design, each with its reason. An entry nothing uses any more fails the check.
- **Source.** ``../research/src/decompiled_basic/mf-prg.bas``. When it is absent (CI)
  the real-tree test is skipped with a reason naming the path, never passed.

The same scan yields every citation, quoted or not (:func:`citations_in_file`), which
``tests/test_coverage_ledger.py`` matches against the source's line blocks.

The scanned roots are :data:`ROOTS`. ``tests/test_ports.py`` checks its inventory quotes
itself (:func:`tests.test_ports.test_quote_is_verbatim`); its comments are scanned here.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from tests.helpers import load_source, parse_source

_REPO = Path(__file__).resolve().parents[1]

#: The trees whose code, comments and docs are scanned for citations.
ROOTS = ("engine", "clients", "data", "tests", "docs/solutions")
_SUFFIXES = {".py", ".md", ".yaml", ".yml"}

#: (repo-relative file, fragment as written) -> why the fragment is not verbatim.
ALLOWED: Mapping[tuple[str, str], str] = {}


# --------------------------------------------------------------------------- #
# Source                                                                      #
# --------------------------------------------------------------------------- #
#: ``{line: text}`` as :func:`tests.helpers.load_source` returns it.
Source = Mapping[int, str]


# --------------------------------------------------------------------------- #
# Parser                                                                      #
# --------------------------------------------------------------------------- #
_NUMS = r"\d{1,5}(?:\s?[-–/]\s?\d{1,5}|,\s?\d{1,5})*"
_CITATION = re.compile(r"(?P<prefix>mf-prg\.bas)?:(?P<nums>" + _NUMS + r")(?!\d)")
_WHOLE_CITATION = re.compile(r"\s*(?:mf-prg\.bas)?:" + _NUMS + r"\s*")
# A bare ``:N`` is not a citation after these (a time, a slice, a file:line, a dict).
_MORE_NUMBERS = re.compile(r"\s*" + _NUMS + r"\s*")
_NOT_BEFORE_BARE = re.compile(r"[\w.\])}\"'$\[]")
_GROUP_GAP = re.compile(r"\s*(?:[/,+&]|and|or|vs\.?)?\s*")
_FRAGMENT_GAP = re.compile(r"\s*(?:—|–|-{1,2}|:|,|\(|=|'s|is|sets)?\s*")


@dataclass(frozen=True)
class Citation:
    """A cited group of source lines: explicit numbers and inclusive ranges."""

    numbers: tuple[int, ...]
    ranges: tuple[tuple[int, int], ...]

    def lines(self, source: Source) -> list[int]:
        """The cited lines that exist in ``source``, in order."""
        cited = set(self.numbers)
        for low, high in self.ranges:
            cited.update(n for n in source if low <= n <= high)
        return sorted(n for n in cited if n in source)

    def label(self) -> str:
        parts = [str(n) for n in self.numbers] + [f"{lo}-{hi}" for lo, hi in self.ranges]
        return ",".join(parts)


@dataclass(frozen=True)
class Quote:
    """A fragment quoted directly after a citation, where it was found."""

    path: str
    lineno: int
    citation: Citation
    fragment: str


def _citation_from(nums: str) -> Citation:
    numbers: list[int] = []
    ranges: list[tuple[int, int]] = []
    for part in re.split(r"\s?[/,]\s?", nums):
        bounds = re.split(r"\s?[-–]\s?", part)
        if len(bounds) == 1:
            numbers.append(int(bounds[0]))
        else:
            ranges.append((int(bounds[0]), int(bounds[-1])))
    return Citation(tuple(numbers), tuple(ranges))


def _spans(text: str) -> Iterator[tuple[int, int, int, str]]:
    """Backtick spans as ``(start, end, fence length, content)`` (CommonMark pairing)."""
    runs = [(m.start(), m.end()) for m in re.finditer(r"`+", text)]
    i = 0
    while i < len(runs):
        start, open_end = runs[i]
        fence = open_end - start
        for j in range(i + 1, len(runs)):
            close_start, end = runs[j]
            if end - close_start == fence:
                yield start, end, fence, text[open_end:close_start]
                i = j
                break
        i += 1


def _merge(first: Citation | None, second: Citation) -> Citation:
    if first is None:
        return second
    return Citation(first.numbers + second.numbers, first.ranges + second.ranges)


# A ``#`` comment that opens with a bare line number and a dash (``# 13065 — no old
# weapon``) cites that line: the handlers' step-by-step convention.
_LEAD_CITATION = re.compile(r"\s*(?P<nums>\d{3,5}(?:\s?[-–/]\s?\d{1,5}|,\s?\d{1,5})*)\s+—")
#: The longest gap (in characters, each whitespace run counted as one) a BASIC-looking
#: fragment may stand behind its citation.
MAX_GAP = 60
# A gap that closes a bracket or a clause, or starts a sentence, has left the citation;
# one ending in a negation (``:12075`` has no ``gosub1160``) says the line lacks it.
_GAP_BREAK = re.compile(r"[)\];]|[.!?]\s+[A-Z]|\b(?:no|not|never|without|unlike)\s*$")
_BASIC_TEXT = re.compile(r"[a-z0-9$%#()=<>+\-*/:;,.^\"]+")
_PYTHON_ONLY = re.compile(r"==|!=|[+\-*/]=|\*\*|//|\(\)|\.[a-z]|^[\d.]+$")
_BASIC_MARK = re.compile(r"[=<>:$]|[a-z]\(|(?:goto|gosub|then)\d")
_STRING = re.compile(r'"[^"]*"?')
# C64 BASIC V2 keywords, longest first so ``gosub`` is not read as ``go`` + ``sub``.
_KEYWORDS = re.compile(
    "|".join(
        sorted(
            "end for next data input dim read let goto run if restore gosub return rem stop "
            "on wait load save verify def poke print cont list clr cmd sys open close get new "
            "tab to fn spc then not step and or sgn int abs usr fre pos sqr rnd log exp cos "
            "sin tan atn peek len str val asc chr left right mid go".split(),
            key=len,
            reverse=True,
        )
    )
)


def looks_like_basic(fragment: str) -> bool:
    """Whether a span reads as crunched BASIC rather than Python.

    Crunched BASIC has no whitespace, underscores, capitals, brackets or braces, glues
    keywords to operands, and names variables with one or two letters. A fragment
    qualifies when every letter run, once the keywords are cut out of it, is at most
    two letters long, and it holds an assignment, comparison, statement separator,
    string variable, ``x(``-style subscript or jump (``gosub4500``); string literals
    are set aside first. Python's ``==``, ``!=``, ``+=``, ``**``,
    ``//``, an empty call ``f()`` and attribute access ``a.b`` rule a span out, as do
    ``seed=42``-style keyword arguments and a bare number.
    """
    text = _STRING.sub('""', fragment.strip().replace("...", "").replace("…", ""))
    if not text or _BASIC_TEXT.fullmatch(text) is None or _PYTHON_ONLY.search(text):
        return False
    names = re.findall(r"[a-z]+", text)
    if any(len(piece) > 2 for name in names for piece in _KEYWORDS.split(name)):
        return False
    return _BASIC_MARK.search(text) is not None


def _pairs_across(gap: str, fragment: str) -> bool:
    """Whether ``fragment`` is the quote of a citation ``gap`` characters before it.

    A direct gap (only a separator) pairs any fragment on the same line; across a line
    break the fragment must look like BASIC. A longer gap, up to :data:`MAX_GAP`
    within the sentence and the bracket the citation sits in, pairs only BASIC.
    """
    if _FRAGMENT_GAP.fullmatch(gap):
        return "\n" not in gap or looks_like_basic(fragment)
    return (
        len(re.sub(r"\s+", " ", gap)) <= MAX_GAP
        and _GAP_BREAK.search(gap) is None
        and looks_like_basic(fragment)
    )


# Tokens: ("cite" | "more" | "span", start, end, Citation | str), offsets into the block.
_Token = tuple[str, int, int, object]


def _line_tokens(text: str, offset: int, *, any_fence: bool, lead: bool) -> list[_Token]:
    tokens: list[_Token] = []
    masked = list(text)
    for start, end, fence, content in _spans(text):
        masked[start:end] = " " * (end - start)
        if _WHOLE_CITATION.fullmatch(content):
            match = _CITATION.search(content)
            assert match is not None
            cited = _citation_from(match.group("nums"))
            tokens.append(("cite", offset + start, offset + end, cited))
        elif _MORE_NUMBERS.fullmatch(content):
            cited = _citation_from(content.strip())
            tokens.append(("more", offset + start, offset + end, cited))
        elif any_fence or fence == 2:
            tokens.append(("span", offset + start, offset + end, content))
    plain = "".join(masked)
    for match in _CITATION.finditer(plain):
        if not match.group("prefix"):
            before = plain[match.start() - 1] if match.start() else ""
            if before and _NOT_BEFORE_BARE.match(before):
                continue
        cited = _citation_from(match.group("nums"))
        tokens.append(("cite", offset + match.start(), offset + match.end(), cited))
    heading = _LEAD_CITATION.match(plain) if lead else None
    if heading is not None:
        cited = _citation_from(heading.group("nums"))
        tokens.append(("cite", offset + heading.start("nums"), offset + heading.end("nums"), cited))
    return tokens


def quotes_in_block(
    lines: Sequence[str], *, any_fence: bool, lead: bool = False
) -> list[tuple[int, Citation, str]]:
    """The ``(line index, citation, fragment)`` triples in one block of text.

    See :func:`scan_block`, which also returns the block's citations.
    """
    return scan_block(lines, any_fence=any_fence, lead=lead)[0]


def scan_block(
    lines: Sequence[str], *, any_fence: bool, lead: bool = False
) -> tuple[list[tuple[int, Citation, str]], list[tuple[int, Citation]]]:
    """The quotes and the citations in one block of text.

    The quotes are ``(line index, citation, fragment)`` triples; the citations are
    ``(line index, citation)`` pairs, one per citation token, quoted or not, a bare
    number continuing an open group (``mf-prg.bas:30106``, ``30108``) included.

    A block is one comment, docstring paragraph or Markdown paragraph: consecutive
    non-blank lines, comment markers already blanked. An open citation carries across
    its line breaks, so a quote that wraps onto the next line is still paired. ``lead``
    turns on the leading ``NNNN —`` citation of ``#`` comments.
    """
    text = "\n".join(lines)
    tokens: list[_Token] = []
    offset = 0
    for line in lines:
        tokens.extend(_line_tokens(line, offset, any_fence=any_fence, lead=lead))
        offset += len(line) + 1
    tokens.sort(key=lambda token: token[1])

    found: list[tuple[int, Citation, str]] = []
    cited: list[tuple[int, Citation]] = []
    group: Citation | None = None
    group_end = 0
    for kind, start, end, value in tokens:
        gap = text[group_end:start]
        if kind == "cite":
            assert isinstance(value, Citation)
            cited.append((text.count("\n", 0, start), value))
            joined = group is not None and _GROUP_GAP.fullmatch(gap)
            group = _merge(group if joined else None, value)
            group_end = end
            continue
        if kind == "more":
            # ``mf-prg.bas:30106``, ``30108``: a bare number continues an open group.
            assert isinstance(value, Citation)
            if group is not None and _GROUP_GAP.fullmatch(gap):
                cited.append((text.count("\n", 0, start), value))
                group = _merge(group, value)
                group_end = end
            else:
                group = None
            continue
        assert isinstance(value, str)
        if group is not None and _pairs_across(gap, value):
            found.append((text.count("\n", 0, start), group, value))
        group = None
    return found, cited


def quotes_in_text(text: str, *, any_fence: bool, lead: bool = False) -> list[tuple[Citation, str]]:
    """The ``(citation, fragment)`` pairs in one line (or ``\\n``-joined block) of text.

    ``any_fence`` is true for ``#`` comments and Markdown, where a single-backtick span
    is a quote; in Python code and docstrings only double-backtick spans are.
    """
    return [
        (citation, fragment)
        for _index, citation, fragment in quotes_in_block(
            text.split("\n"), any_fence=any_fence, lead=lead
        )
    ]


def _comment_starts(text: str) -> dict[int, int]:
    """``{line number: column of its '#' comment}`` for a Python file."""
    starts: dict[int, int] = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                starts[token.start[0]] = token.start[1]
    except (tokenize.TokenError, SyntaxError):  # pragma: no cover - the tree parses
        pass
    return starts


def _yaml_comment_start(line: str) -> int | None:
    match = re.search(r"(?:^|\s)#", line)
    return None if match is None else match.end() - 1


def _unmark(comment: str) -> str:
    """A ``#`` comment with its marker (``#``, ``#:``) blanked, columns kept."""
    marker = re.match(r"#+:?", comment)
    assert marker is not None
    return " " * marker.end() + comment[marker.end() :]


@dataclass(frozen=True)
class Cited:
    """A citation found in a file, quoted or not, and where it was found."""

    path: str
    lineno: int
    citation: Citation


def quotes_in_file(path: str, text: str) -> list[Quote]:
    """Every quote in one file; ``path`` is only recorded, never read."""
    return scan_file(path, text)[0]


def citations_in_file(path: str, text: str) -> list[Cited]:
    """Every citation in one file, quoted or not (the coverage ledger's input)."""
    return scan_file(path, text)[1]


def scan_file(path: str, text: str) -> tuple[list[Quote], list[Cited]]:
    """Every quote and every citation in one file; ``path`` is only recorded, never read.

    Each line splits into a code part and a ``#`` comment part (Markdown is all one
    part); consecutive non-blank parts of the same kind form a block.
    """
    suffix = Path(path).suffix
    comments = _comment_starts(text) if suffix == ".py" else {}
    # kind -> (lines of the open block, line number of its first line)
    streams: dict[str, tuple[list[str], int]] = {}
    quotes: list[Quote] = []
    cited: list[Cited] = []

    def close(kind: str) -> None:
        lines, first = streams.pop(kind, ([], 0))
        if not lines:
            return
        any_fence = kind != "code"
        found, citations = scan_block(lines, any_fence=any_fence, lead=kind == "comment")
        for index, citation, fragment in found:
            quotes.append(Quote(path, first + index, citation, fragment))
        for index, citation in citations:
            cited.append(Cited(path, first + index, citation))

    for lineno, line in enumerate(text.splitlines(), start=1):
        if suffix == ".md":
            parts = {"markdown": line}
        else:
            col = comments.get(lineno) if suffix == ".py" else _yaml_comment_start(line)
            parts = (
                {"code": line}
                if col is None
                else {"code": line[:col], "comment": _unmark(line[col:])}
            )
        for kind in ("markdown", "code", "comment"):
            part = parts.get(kind, "")
            if not part.strip():
                close(kind)
                continue
            lines, first = streams.setdefault(kind, ([], lineno))
            lines.append(part)
    for kind in list(streams):
        close(kind)
    quotes.sort(key=lambda quote: quote.lineno)
    cited.sort(key=lambda found: found.lineno)
    return quotes, cited


# --------------------------------------------------------------------------- #
# Matcher                                                                     #
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _contains(line: str, fragment: str) -> bool:
    """Whether ``fragment`` (normalized, ``...`` pieces in order) occurs in ``line``."""
    at = 0
    for piece in re.split(r"\.\.\.|…", _normalize(fragment)):
        if not piece:
            continue
        found = _normalize(line).find(piece, at)
        if found < 0:
            return False
        at = found + len(piece)
    return True


def check_quote(quote: Quote, source: Source) -> str | None:
    """``None`` when the quote holds, else a message naming both line numbers."""
    cited = quote.citation.lines(source)
    if any(_contains(source[n], quote.fragment) for n in cited):
        return None
    where = f"{quote.path}:{quote.lineno}"
    actual = [n for n in sorted(source) if _contains(source[n], quote.fragment)]
    found = (
        "found at " + ", ".join(f"mf-prg.bas:{n}" for n in actual) if actual else "found on no line"
    )
    missing = "" if cited else " (no such line)"
    return (
        f"{where}: `{quote.fragment}` cited as mf-prg.bas:{quote.citation.label()}"
        f"{missing}, but {found}"
    )


@dataclass
class Report:
    quotes: list[Quote]
    errors: list[str]
    allowed_used: set[tuple[str, str]]


def check_quotes(
    quotes: Iterable[Quote], source: Source, allowed: Mapping[tuple[str, str], str]
) -> Report:
    report = Report([], [], set())
    for quote in quotes:
        report.quotes.append(quote)
        error = check_quote(quote, source)
        if error is None:
            continue
        key = (quote.path, quote.fragment)
        if key in allowed:
            report.allowed_used.add(key)
        else:
            report.errors.append(error)
    return report


def tree_files(repo: Path = _REPO) -> Iterator[Path]:
    for root in ROOTS:
        for path in sorted((repo / root).rglob("*")):
            if path.suffix in _SUFFIXES and path.is_file() and "__pycache__" not in path.parts:
                yield path


#: A config's house-rules catalogue: its entries cite as data, not prose.
CATALOGUE_NAME = "house_rules.yaml"
_CATALOGUE_ID = re.compile(r"\s*-\s*id:\s*(?P<id>\S+)\s*$")


def catalogue_quotes(path: str, text: str) -> list[Quote]:
    """Each house-rules catalogue entry's ``quote`` held to its ``citation`` (R22).

    A catalogue (``content/house_rules.yaml``) names the line in an entry's
    ``citation`` field and the verbatim fragment in its ``quote`` field -- data, which
    the prose scan cannot pair -- so this reads them as one :class:`Quote` per entry,
    recorded at the entry's ``id`` line. An entry whose citation does not parse is a
    quote of line 0, which no source has: it fails as a missing line.
    """
    data = yaml.safe_load(text) or {}
    id_lines = {
        match.group("id"): lineno
        for lineno, line in enumerate(text.splitlines(), start=1)
        if (match := _CATALOGUE_ID.fullmatch(line))
    }
    quotes: list[Quote] = []
    for entry in data.get("house_rules") or []:
        match = _CITATION.fullmatch(str(entry.get("citation", "")))
        cited = _citation_from(match.group("nums")) if match else Citation((0,), ())
        lineno = id_lines.get(str(entry.get("id")), 0)
        quotes.append(Quote(path, lineno, cited, str(entry.get("quote", ""))))
    return quotes


def tree_quotes(repo: Path = _REPO) -> list[Quote]:
    quotes: list[Quote] = []
    for path in tree_files(repo):
        if path == Path(__file__).resolve():
            continue  # this module's synthetic misquotes are test data
        rel = path.relative_to(repo).as_posix()
        text = path.read_text(encoding="utf-8")
        quotes.extend(quotes_in_file(rel, text))
        if path.name == CATALOGUE_NAME and path.parent.name == "content":
            quotes.extend(catalogue_quotes(rel, text))
    return quotes


# --------------------------------------------------------------------------- #
# Tests named by docs (KTD-10)                                                #
# --------------------------------------------------------------------------- #
_TEST_REF = re.compile(
    r"(?P<file>(?:tests/)?test_\w+\.py)(?:::(?P<qual>[\w:]+))?(?::(?P<line>\d+)(?:-\d+)?)?"
    r"|(?P<name>test_\w+)"
)


def defined_names(tests_root: Path) -> dict[str, set[str]]:
    """``{file name: names of the functions and classes it defines}`` under ``tests_root``."""
    names: dict[str, set[str]] = {}
    for path in sorted(tests_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names[path.name] = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
    return names


_PARAMETER_SET = type(pytest.param(None))


def entry_names(module: ModuleType) -> dict[str, set[str]]:
    """``{test name: its entry names}`` for each test of ``module`` parametrized over
    objects with a ``name`` (the :class:`tests.test_ports.Port` inventory), read from
    the test's own ``parametrize`` mark."""
    entries: dict[str, set[str]] = {}
    for test_name, function in vars(module).items():
        if not test_name.startswith("test_"):
            continue
        for mark in getattr(function, "pytestmark", []):
            if mark.name != "parametrize":
                continue
            for value in mark.args[1]:
                if isinstance(value, _PARAMETER_SET):
                    value = value.values[0]
                name = getattr(value, "name", None)
                if isinstance(name, str):
                    entries.setdefault(test_name, set()).add(name)
    return entries


# ``test_port_matches_basic`` (entry `slw rent`), (entries `a` and `b`); may wrap.
_ENTRY_REF = re.compile(
    r"`(?P<test>test_\w+)`\s*\(entr(?:y|ies)"
    r"(?P<names>\s+`[^`]+`(?:\s*(?:,|and|,\s*and)\s*`[^`]+`)*)"
)


def missing_entry_refs(doc_path: str, text: str, entries: Mapping[str, set[str]]) -> list[str]:
    """A message for each entry of a parametrized test a doc names that the test lacks."""
    errors: list[str] = []
    for match in _ENTRY_REF.finditer(text):
        test = match.group("test")
        for span in re.finditer(r"`([^`]+)`", match.group("names")):
            lineno = text.count("\n", 0, match.start("names") + span.start()) + 1
            where = f"{doc_path}:{lineno}: `{test}` entry `{span.group(1)}`"
            if test not in entries:
                errors.append(f"{where}: {test} has no entry inventory")
            elif span.group(1) not in entries[test]:
                errors.append(f"{where}: no such entry in {test}'s inventory")
    return errors


def missing_test_refs(
    doc_path: str,
    text: str,
    tests_root: Path,
    *,
    defined: Mapping[str, set[str]] | None = None,
) -> list[str]:
    """A message for each backticked test a doc names that ``tests_root`` lacks.

    ``defined`` is :func:`defined_names` of ``tests_root``; a caller checking many docs
    computes it once and passes it in.
    """
    if defined is None:
        defined = defined_names(tests_root)
    every = set().union(*defined.values()) if defined else set()
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for _start, _end, _fence, content in _spans(line):
            match = _TEST_REF.fullmatch(content.strip())
            if match is None:
                continue
            where = f"{doc_path}:{lineno}: `{content}`"
            if match.group("name"):
                if match.group("name") not in every:
                    errors.append(f"{where}: no test named {match.group('name')} in tests/")
                continue
            file_name = Path(match.group("file")).name
            if file_name not in defined:
                errors.append(f"{where}: no file tests/{file_name}")
                continue
            for part in (match.group("qual") or "").split("::"):
                if part and part not in defined[file_name]:
                    errors.append(f"{where}: tests/{file_name} defines no {part}")
            if match.group("line"):
                count = len((tests_root / file_name).read_text(encoding="utf-8").splitlines())
                if int(match.group("line")) > count:
                    errors.append(f"{where}: tests/{file_name} has only {count} lines")
    return errors


# --------------------------------------------------------------------------- #
# Tests: parser                                                               #
# --------------------------------------------------------------------------- #
def _pairs(text: str, *, any_fence: bool = False) -> list[tuple[str, str]]:
    return [(c.label(), f) for c, f in quotes_in_text(text, any_fence=any_fence)]


def test_explicit_and_bare_citations_are_parsed_alike() -> None:
    assert _pairs("mf-prg.bas:30108 ``s=1-(s=1)``") == [("30108", "s=1-(s=1)")]
    assert _pairs(":30108 ``s=1-(s=1)``") == [("30108", "s=1-(s=1)")]
    assert _pairs("(``mf-prg.bas:30108``: ``s=1-(s=1)``)") == [("30108", "s=1-(s=1)")]


def test_a_span_elsewhere_on_the_line_is_ignored() -> None:
    # The Python span before the citation and the one after a prose word are not quotes.
    text = "``toggle(s)`` ports :30108 ``s=1-(s=1)``; see ``engine.combat`` too"
    assert _pairs(text) == [("30108", "s=1-(s=1)")]
    assert _pairs("the side toggle (:30108) maps ``s`` to ``3 - s``") == []


def test_single_backticks_count_only_in_comments_and_markdown() -> None:
    assert _pairs(":10020 `p=fnm(ln)`") == []  # a docstring: `x` is a Sphinx role
    assert _pairs(":10020 `p=fnm(ln)`", any_fence=True) == [("10020", "p=fnm(ln)")]


def test_separators_between_citation_and_fragment() -> None:
    assert _pairs("mf-prg.bas:12110's `x`", any_fence=True) == [("12110", "x")]
    assert _pairs("`:30010` — `pokefr`", any_fence=True) == [("30010", "pokefr")]
    assert _pairs("(`:25560`, `x=3`)", any_fence=True) == [("25560", "x=3")]
    assert _pairs("``:30255`` is ``y=1``") == [("30255", "y=1")]
    assert _pairs("borrow :15030 sets ``kz=6``") == [("15030", "kz=6")]
    assert _pairs(":2005): if ``ms <= 0``") == []
    assert _pairs(":30255 gives ``y``") == []


def test_ranges_lists_and_groups() -> None:
    assert _pairs(":4000-4090 ``x``") == [("4000-4090", "x")]
    assert _pairs(":2035/2040 ``x``") == [("2035,2040", "x")]
    assert _pairs("mf-prg.bas:12106,12110,12165 ``x``") == [("12106,12110,12165", "x")]
    assert _pairs("`:30015` / `:30115` — `poke`", any_fence=True) == [("30015,30115", "poke")]
    # A span holding only numbers continues an open group rather than being quoted.
    assert _pairs("``mf-prg.bas:30106``, ``30108`` — ``s=1``") == [("30106,30108", "s=1")]
    assert _pairs("``30108`` ``s=1``") == []


def test_things_that_are_not_citations() -> None:
    assert _pairs("at 12:30 ``x``") == []
    assert _pairs("``a[1:10]`` ``b``") == []
    assert _pairs("`tests/helpers.py:35` `x`", any_fence=True) == []


def test_python_comments_are_single_backtick_quotes() -> None:
    code = 'x = f"`a`"  # :30108 `s=1-(s=1)`\n'
    [quote] = quotes_in_file("m.py", code)
    assert (quote.lineno, quote.fragment) == (1, "s=1-(s=1)")
    yaml = "key: 1  # mf-prg.bas:1000 `sp=0:ja=1925`\n"
    assert [q.fragment for q in quotes_in_file("c.yaml", yaml)] == ["sp=0:ja=1925"]


def test_a_quote_wrapped_onto_the_next_line_is_paired() -> None:
    # A citation still open at a line break carries into the rest of its block.
    comment = "# Side toggle (mf-prg.bas:30108,\n#: `s=1-(s=1)` walks both sides).\n"
    assert [(q.lineno, q.fragment) for q in quotes_in_file("m.py", comment)] == [(2, "s=1-(s=1)")]
    doc = '"""Rolls the tip (mf-prg.bas:12225:\n    the roll ``tp(sp)=int(rnd(1)*5)+1``)."""\n'
    assert [(q.lineno, q.fragment) for q in quotes_in_file("m.py", doc)] == [
        (2, "tp(sp)=int(rnd(1)*5)+1")
    ]
    assert _pairs("mf-prg.bas:30108's\n`s=1-(s=1)`", any_fence=True) == [("30108", "s=1-(s=1)")]


def test_a_wrapped_span_must_look_like_basic_and_stay_in_its_block() -> None:
    # Across a line break a Python span is not a quote, even directly after a citation.
    assert _pairs("ambusher (``mf-prg.bas:30245``,\n``energie=35`` from the fixture)") == []
    # A blank line, or code between comments, ends the block.
    assert quotes_in_file("m.py", "# :30108\n#\n# `s=1-(s=1)`\n") == []
    assert quotes_in_file("m.py", "# :30108\nx = 1\n# `s=1-(s=1)`\n") == []
    assert quotes_in_file("d.md", ":30108\n\n`s=1-(s=1)`\n") == []


def test_a_basic_fragment_after_a_short_gap_is_paired() -> None:
    assert _pairs(":13072 upgrade    `gf(sp)=gf(sp)-x8`", any_fence=True) == [
        ("13072", "gf(sp)=gf(sp)-x8")
    ]
    assert _pairs(":12075 — settle, no score effect: ``ka(sp)=ka(sp)+y*x``") == [
        ("12075", "ka(sp)=ka(sp)+y*x")
    ]
    # A comment that opens with a bare line number and a dash cites it.
    comment = "# 13065 — no old weapon: q=0. `gf(sp)=gf(sp)-x8`\n"
    assert [q.fragment for q in quotes_in_file("m.py", comment)] == ["gf(sp)=gf(sp)-x8"]
    assert quotes_in_file("m.py", "x = 13065 - 2  # `gf(sp)=gf(sp)-x8`\n") == []


def test_a_python_span_after_a_gap_is_ignored() -> None:
    for span in ("hostile_to(s)", "ctx.state", "play()", "seed=42", "kr(sp)+=x", "x > old"):
        assert _pairs(f":30108 is ported by ``{span}``") == [], span
    assert _pairs(":30108 is ported by ``s=1-(s=1)``") == [("30108", "s=1-(s=1)")]


def test_a_gap_that_leaves_the_citation_ends_it() -> None:
    assert _pairs(":12075 has no ``gosub1160``") == []  # a negation
    assert _pairs("(:170) -- truncated; the weight is ``val(x$)``") == []  # a bracket
    assert _pairs(":16010. The three games,\npoker ``x=1``") == []  # a new sentence
    assert _pairs(":30108 " + "then a long stretch of prose " * 3 + "``s=1-(s=1)``") == []


def test_looks_like_basic() -> None:
    basic = [
        "s=1-(s=1)",
        "fori=1to10:readp(i):next",
        "gosub4500",
        'print"{down}taste druecken!":poke198,0',
        "input#1,gn$:input#1,gw",
        "s=1-...then30108",
    ]
    python = ["play()", "seed=42", "true=-1", "ctx.state", "a == b", "x > old", "gf", "30108"]
    assert [f for f in basic if not looks_like_basic(f)] == []
    assert [f for f in python if looks_like_basic(f)] == []


# --------------------------------------------------------------------------- #
# Tests: matcher                                                              #
# --------------------------------------------------------------------------- #
_SYNTHETIC = parse_source(
    "  115 deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)\n"
    "  116 deffnr(x)=int(rnd(1)*8)+8\n"
    "30108 s=1-(s=1):ifks(s)=0then30108\n"
    "30115 poke211,-20*(i=2)\n"
)


def _check(text: str, *, path: str = "m.py") -> list[str]:
    return check_quotes(quotes_in_file(path, text), _SYNTHETIC, {}).errors


def test_ae2_a_fragment_under_the_wrong_line_fails_naming_both_lines() -> None:
    [error] = _check(
        '"""Rent is ``deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)`` (mf-prg.bas:116)."""\n'
        '"""mf-prg.bas:116 ``deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)``"""\n'
    )
    assert "m.py:2" in error
    assert "mf-prg.bas:116" in error and "mf-prg.bas:115" in error
    assert "deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)" in error


def test_bare_citation_is_checked_like_an_explicit_one() -> None:
    assert _check("# :30108 `s=1-(s=1)`\n") == []
    assert _check("# :30108 `s=2-(s=1)`\n") != []
    assert _check("# :116 `deffnm`\n") != []


def test_case_and_whitespace_are_normalized() -> None:
    assert _check('"""mf-prg.bas:30108 ``S = 1 - (s=1)``"""\n') == []


def test_a_range_or_list_passes_when_any_cited_line_holds_the_fragment() -> None:
    assert _check("# :30100-30115 `poke211`\n") == []
    assert _check("# :115/30115 `poke211`\n") == []
    assert _check("# `:116` / `:30108` — `ifks(s)=0`\n", path="d.md") == []
    assert _check("# :115-116 `poke211`\n") != []


def test_elided_fragments_match_in_order() -> None:
    assert _check("# :30108 `s=1-...then30108`\n") == []
    assert _check("# :30108 `then30108...s=1`\n") != []


def test_a_citation_of_a_missing_line_fails() -> None:
    [error] = _check("# :117 `deffnr`\n")
    assert "no such line" in error and "mf-prg.bas:116" in error


def test_allow_list_suppresses_only_its_entry() -> None:
    quotes = quotes_in_file("m.py", "# :116 `deffnm`\n# :116 `deffnx`\n")
    report = check_quotes(quotes, _SYNTHETIC, {("m.py", "deffnm"): "paraphrase"})
    assert report.allowed_used == {("m.py", "deffnm")}
    assert len(report.errors) == 1 and "deffnx" in report.errors[0]


_CATALOGUE = """\
house_rules:
  - id: toggle
    citation: ":30108"
    quote: "s=1-(s=1)"
  - id: misquoted
    citation: ":30108"
    quote: "s=2-(s=1)"
  - id: rent
    citation: ":115-116"
    quote: "deffnm"
  - id: nowhere
    citation: "30108"
    quote: "s=1-(s=1)"
"""


def test_a_catalogue_entry_is_held_to_its_cited_line() -> None:
    quotes = catalogue_quotes("c/content/house_rules.yaml", _CATALOGUE)
    assert [(q.lineno, q.citation.label(), q.fragment) for q in quotes] == [
        (2, "30108", "s=1-(s=1)"),
        (5, "30108", "s=2-(s=1)"),
        (8, "115-116", "deffnm"),
        (11, "0", "s=1-(s=1)"),
    ]
    errors = check_quotes(quotes, _SYNTHETIC, {}).errors
    assert len(errors) == 2
    assert errors[0].startswith("c/content/house_rules.yaml:5: `s=2-(s=1)`")
    assert "no such line" in errors[1] and "c/content/house_rules.yaml:11" in errors[1]


def test_the_tree_scan_reads_the_real_catalogue() -> None:
    catalogue = "data/game_configs/mafia_1920s/content/house_rules.yaml"
    held = [q for q in tree_quotes() if q.path == catalogue]
    entries = yaml.safe_load((_REPO / catalogue).read_text(encoding="utf-8"))["house_rules"]
    assert entries, "the real catalogue is empty: the scan is vacuous"
    for entry in entries:
        assert any(
            q.fragment == entry["quote"] and q.citation.label() == entry["citation"].lstrip(":")
            for q in held
        ), entry["id"]


def test_every_citation_is_found_quoted_or_not() -> None:
    text = (
        "# :30108 toggles the side; ``mf-prg.bas:30106``, ``30108``\n"
        "x = 1  # at 12:30, a[1:10]\n"
        "# 13065 — no old weapon\n"
        '"""Ports ``:4000-4090`` and :2035/2040."""\n'
    )
    found = [(c.lineno, c.citation.label()) for c in citations_in_file("m.py", text)]
    assert found == [
        (1, "30108"),
        (1, "30106"),
        (1, "30108"),
        (3, "13065"),
        (4, "4000-4090"),
        (4, "2035,2040"),
    ]


def test_skips_with_a_reason_when_the_source_is_absent(tmp_path: Path) -> None:
    absent = tmp_path / "mf-prg.bas"
    with pytest.raises(pytest.skip.Exception, match=re.escape(str(absent))):
        load_source(absent)


# --------------------------------------------------------------------------- #
# Tests: doc test names (KTD-10)                                              #
# --------------------------------------------------------------------------- #
def test_a_doc_naming_a_missing_test_fails(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text(
        "class TestA:\n    def test_one(self):\n        pass\n\ndef test_two():\n    pass\n"
    )
    doc = (
        "tested by `test_two` and `tests/test_x.py::TestA::test_one` and `tests/test_x.py:3`\n"
        "tested by `test_three`, `tests/test_y.py`, `tests/test_x.py::TestB` and "
        "`tests/test_x.py:99`\n"
    )
    errors = missing_test_refs("d.md", doc, tmp_path)
    assert len(errors) == 4, errors
    assert all(error.startswith("d.md:2:") for error in errors)
    assert "test_three" in errors[0]


def _inventory() -> ModuleType:
    """A stand-in for ``tests/test_ports.py``: a test parametrized over named entries."""
    module = ModuleType("fake_ports")
    entries = [SimpleNamespace(name="slw rent"), SimpleNamespace(name="waf range training")]

    @pytest.mark.parametrize("port", [entries[0], pytest.param(entries[1], id="x")])
    def test_port_matches_basic(port: object) -> None:  # pragma: no cover - never run
        pass

    module.__dict__["test_port_matches_basic"] = test_port_matches_basic
    return module


def test_entry_names_are_read_from_the_parametrize_mark() -> None:
    assert entry_names(_inventory()) == {
        "test_port_matches_basic": {"slw rent", "waf range training"}
    }


def test_a_doc_naming_a_missing_port_entry_fails() -> None:
    entries = entry_names(_inventory())
    doc = (
        "held by `test_port_matches_basic` (entry `slw rent`, which runs `x`) and\n"
        "`test_port_matches_basic` (entries `waf range training` and\n"
        "`waf camp training`); `test_other` (entry `y`).\n"
    )
    errors = missing_entry_refs("d.md", doc, entries)
    assert errors == [
        "d.md:3: `test_port_matches_basic` entry `waf camp training`: no such entry in "
        "test_port_matches_basic's inventory",
        "d.md:3: `test_other` entry `y`: test_other has no entry inventory",
    ]


# --------------------------------------------------------------------------- #
# Tests: the real tree                                                        #
# --------------------------------------------------------------------------- #
def test_every_quoted_fragment_matches_its_cited_line() -> None:
    source = load_source()
    report = check_quotes(tree_quotes(), source, ALLOWED)
    assert report.quotes, "the scan found no quotes at all: the parser is broken"
    assert report.errors == [], "\n".join(report.errors)
    assert set(ALLOWED) - report.allowed_used == set(), "stale allow-list entries"


def test_every_test_a_doc_names_exists() -> None:
    from tests import test_ports

    defined = defined_names(_REPO / "tests")
    entries = entry_names(test_ports)
    assert entries.get("test_port_matches_basic"), "the port inventory was not read"
    errors: list[str] = []
    for path in sorted((_REPO / "docs" / "solutions").rglob("*.md")):
        rel = path.relative_to(_REPO).as_posix()
        text = path.read_text(encoding="utf-8")
        errors.extend(missing_test_refs(rel, text, _REPO / "tests", defined=defined))
        errors.extend(missing_entry_refs(rel, text, entries))
    assert errors == [], "\n".join(errors)

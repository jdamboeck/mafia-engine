"""Citation checker: every quoted BASIC fragment matches the ``mf-prg.bas`` line it cites.

Code, tests and docs back a claim about the original game by citing a line of the
decompiled source and quoting the part of it the claim rests on (a ``#`` comment reading
``:30108 `s=1-(s=1)` `` is one). A quote that drifted from its line (a typo, or the
right text under the wrong line number) reads as proof while proving nothing. This
module finds every such quote in the tree and checks it against the source (KTD-9),
and checks that every test a learning doc names by name exists (KTD-10).

Rules (KTD-9)
-------------
- **Citation.** ``mf-prg.bas:NNNN`` or a bare ``:NNNN``; either may continue as a range
  (``:4000-4090``) or a list (``:2035/2040``, ``mf-prg.bas:12106,12110,12165``). A bare
  ``:NNNN`` counts only where the colon does not follow a word character, a closing
  bracket, a quote or a dot, so ``12:30``, ``a[1:10]`` and ``tests/helpers.py:35`` are
  not citations. A backtick span whose whole content is a citation is a citation.
  Citations joined by ``/``, ``,``, ``+``, ``and``, ``or`` or ``vs.`` form one group
  (``:30015`` / ``:30115``); any line of the group may hold the fragment.
- **Quoted fragment.** The first backtick span *directly* after a citation group: only
  whitespace and at most one separator (``—``, ``-``, ``:``, ``,``, ``(``, ``=``, ``'s``,
  or the word ``is`` or ``sets``) may stand between them. A span holding only line numbers
  (``mf-prg.bas:30106``, ``30108``) continues the group instead. In Python code and
  docstrings only double-backtick spans count (single backticks there are Sphinx roles);
  in ``#`` comments and Markdown a span of any backtick length counts. Spans elsewhere on
  the line, often Python, are ignored.
- **Match.** Case and all whitespace are dropped from both sides; the fragment must be
  a substring of one of the cited lines (any line within a range). A fragment that
  elides with ``...`` must have each piece, in order, within one line.
- **Allow-list.** :data:`ALLOWED` names the (file, fragment) pairs that paraphrase the
  line by design, each with its reason. An entry nothing uses any more fails the check.
- **Source.** ``../research/src/decompiled_basic/mf-prg.bas``. When it is absent (CI)
  the real-tree test is skipped with a reason naming the path, never passed.

The scanned roots are :data:`ROOTS`. ``tests/test_ports.py`` checks its inventory quotes
itself (:func:`tests.test_ports.test_quote_is_verbatim`); its comments are scanned here.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

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


def quotes_in_text(text: str, *, any_fence: bool) -> list[tuple[Citation, str]]:
    """The ``(citation, fragment)`` pairs in one line of text.

    ``any_fence`` is true for ``#`` comments and Markdown, where a single-backtick span
    is a quote; in Python code and docstrings only double-backtick spans are.
    """
    # Tokens in line order: ("cite", start, end, Citation) or ("span", start, end, text).
    tokens: list[tuple[str, int, int, object]] = []
    masked = list(text)
    for start, end, fence, content in _spans(text):
        masked[start:end] = " " * (end - start)
        if _WHOLE_CITATION.fullmatch(content):
            match = _CITATION.search(content)
            assert match is not None
            tokens.append(("cite", start, end, _citation_from(match.group("nums"))))
        elif _MORE_NUMBERS.fullmatch(content):
            tokens.append(("more", start, end, _citation_from(content.strip())))
        elif any_fence or fence == 2:
            tokens.append(("span", start, end, content))
    plain = "".join(masked)
    for match in _CITATION.finditer(plain):
        if not match.group("prefix"):
            before = plain[match.start() - 1] if match.start() else ""
            if before and _NOT_BEFORE_BARE.match(before):
                continue
        tokens.append(("cite", match.start(), match.end(), _citation_from(match.group("nums"))))
    tokens.sort(key=lambda token: token[1])

    found: list[tuple[Citation, str]] = []
    group: Citation | None = None
    group_end = 0
    for kind, start, end, value in tokens:
        gap = text[group_end:start]
        if kind == "cite":
            assert isinstance(value, Citation)
            joined = group is not None and _GROUP_GAP.fullmatch(gap)
            group = _merge(group if joined else None, value)
            group_end = end
            continue
        if kind == "more":
            # ``mf-prg.bas:30106``, ``30108``: a bare number continues an open group.
            assert isinstance(value, Citation)
            if group is not None and _GROUP_GAP.fullmatch(gap):
                group = _merge(group, value)
                group_end = end
            else:
                group = None
            continue
        if group is not None and _FRAGMENT_GAP.fullmatch(gap):
            assert isinstance(value, str)
            found.append((group, value))
        group = None
    return found


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


def quotes_in_file(path: str, text: str) -> list[Quote]:
    """Every quote in one file; ``path`` is only recorded, never read."""
    suffix = Path(path).suffix
    comments = _comment_starts(text) if suffix == ".py" else {}
    quotes: list[Quote] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if suffix == ".md":
            parts = [(line, True)]
        else:
            col = comments.get(lineno) if suffix == ".py" else _yaml_comment_start(line)
            parts = [(line, False)] if col is None else [(line[:col], False), (line[col:], True)]
        for part, any_fence in parts:
            for citation, fragment in quotes_in_text(part, any_fence=any_fence):
                quotes.append(Quote(path, lineno, citation, fragment))
    return quotes


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


def tree_quotes(repo: Path = _REPO) -> list[Quote]:
    quotes: list[Quote] = []
    for path in tree_files(repo):
        if path == Path(__file__).resolve():
            continue  # this module's synthetic misquotes are test data
        rel = path.relative_to(repo).as_posix()
        quotes.extend(quotes_in_file(rel, path.read_text(encoding="utf-8")))
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


def missing_test_refs(doc_path: str, text: str, tests_root: Path) -> list[str]:
    """A message for each backticked test a doc names that ``tests_root`` lacks."""
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
    errors: list[str] = []
    for path in sorted((_REPO / "docs" / "solutions").rglob("*.md")):
        rel = path.relative_to(_REPO).as_posix()
        errors.extend(missing_test_refs(rel, path.read_text(encoding="utf-8"), _REPO / "tests"))
    assert errors == [], "\n".join(errors)

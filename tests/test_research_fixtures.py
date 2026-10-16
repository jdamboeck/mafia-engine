"""The vendored research files, and the guard that keeps CI from skipping them (R24, AE7).

``tests/fixtures/research/`` holds byte-identical copies of the research files the tests
read, at their ``../research/`` paths (:data:`tests.helpers.VENDORED_FILES`), so CI,
which checks out only this repository, runs the port tests from them (KTD-11).
``../research/`` stays the authority: when it is beside this repository, a vendored file
that differs from its research copy fails here. Without it there is nothing to compare
against, and the check passes rather than skips, as a CI run must have no research skip.

:mod:`tests.research_guard` makes any skip for want of the research tree fail the run;
:func:`test_ci_runs_the_research_tests_with_no_research_skip` (AE7) shows it on a
throwaway suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import (
    RESEARCH_MISSING,
    SIBLING_RESEARCH,
    VENDORED_FILES,
    VENDORED_RESEARCH,
)

pytest_plugins = ["pytester"]


def drift(vendored: Path, research: Path, files: tuple[str, ...]) -> list[str]:
    """Each of ``files`` whose vendored copy is not its research copy, byte for byte.

    No research tree at ``research`` is no drift: there is nothing to compare against.
    """
    if not research.is_dir():
        return []
    errors: list[str] = []
    for relative in files:
        source = research / relative
        if not source.is_file():
            errors.append(f"{relative}: vendored, but gone from {research}")
        elif (vendored / relative).read_bytes() != source.read_bytes():
            errors.append(f"{relative}: the vendored copy differs from {source}; recopy it")
    return errors


def test_the_vendored_research_is_the_research() -> None:
    errors = drift(VENDORED_RESEARCH, SIBLING_RESEARCH, VENDORED_FILES)
    assert errors == [], "\n".join(errors)


def test_every_vendored_file_is_listed_and_present() -> None:
    present = {
        path.relative_to(VENDORED_RESEARCH).as_posix()
        for path in VENDORED_RESEARCH.rglob("*")
        if path.is_file()
    }
    assert present == set(VENDORED_FILES)


def _trees(tmp_path: Path, vendored: bytes, research: bytes) -> tuple[Path, Path]:
    for root, data in (("vendored", vendored), ("research", research)):
        path = tmp_path / root / "src" / "x.bas"
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
    return tmp_path / "vendored", tmp_path / "research"


def test_a_changed_byte_is_drift(tmp_path: Path) -> None:
    vendored, research = _trees(tmp_path, b"10 print\n", b"10 print \n")
    assert drift(vendored, research, ("src/x.bas",)) == [
        f"src/x.bas: the vendored copy differs from {research / 'src' / 'x.bas'}; recopy it"
    ]


def test_a_file_gone_from_the_research_is_drift(tmp_path: Path) -> None:
    vendored, research = _trees(tmp_path, b"10 print\n", b"10 print\n")
    (research / "src" / "x.bas").unlink()
    assert drift(vendored, research, ("src/x.bas",)) == [
        f"src/x.bas: vendored, but gone from {research}"
    ]


def test_equal_copies_are_no_drift(tmp_path: Path) -> None:
    vendored, research = _trees(tmp_path, b"10 print\n", b"10 print\n")
    assert drift(vendored, research, ("src/x.bas",)) == []


def test_no_research_tree_is_no_drift_and_no_skip(tmp_path: Path) -> None:
    vendored, _ = _trees(tmp_path, b"10 print\n", b"10 print\n")
    assert drift(vendored, tmp_path / "absent", ("src/x.bas",)) == []


_SUITE = f'''
import pytest

def test_reads_the_research():
    pytest.skip("{RESEARCH_MISSING}: /nowhere/mf-prg.bas is not present")

def test_skips_for_another_reason():
    pytest.skip("not on this platform")

def test_passes():
    pass
'''

#: The inner run imports the guard the outer run already did, so pytest cannot rewrite it.
_QUIET = ("-W", "ignore::pytest.PytestAssertRewriteWarning")


def test_ci_runs_the_research_tests_with_no_research_skip(pytester: pytest.Pytester) -> None:
    """AE7: a run in which a test skips for want of the research tree is red, naming it."""
    pytester.makepyfile(test_suite=_SUITE)
    result = pytester.runpytest_inprocess("-p", "tests.research_guard", *_QUIET)
    result.assert_outcomes(passed=1, skipped=2)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        ["*research skips fail the run*", "test_suite.py::test_reads_the_research: *"]
    )
    assert "test_skips_for_another_reason" not in result.stdout.str()


def test_another_skip_leaves_the_run_green(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_suite=_SUITE.replace(RESEARCH_MISSING, "something else"))
    result = pytester.runpytest_inprocess("-p", "tests.research_guard", *_QUIET)
    result.assert_outcomes(passed=1, skipped=2)
    assert result.ret == pytest.ExitCode.OK
    assert "research skips fail the run" not in result.stdout.str()

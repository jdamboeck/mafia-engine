"""A pytest plugin: a run in which any test skips for want of the research tree is red.

The research-dependent tests read the vendored copy under ``tests/fixtures/research/``
(:func:`tests.helpers.research_file`), so CI runs them without ``../research/``. A test
that still skips with :data:`tests.helpers.RESEARCH_MISSING` means a reader bypasses the
vendored copy or a vendored file is gone, and a skip stays green on the summary line.
:class:`ResearchGuard` collects those skips, lists them, and turns the run's exit status
to "tests failed", so the gap cannot pass as a skip (R24, AE7). Every other skip is left
alone.

``tests/conftest.py`` registers it; ``tests/test_research_fixtures.py`` loads it (as
``-p tests.research_guard``) on a throwaway suite to show that a research skip turns the
run red and another skip does not.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.helpers import RESEARCH_MISSING


def _reason(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    return str(longrepr[2]) if isinstance(longrepr, tuple) else str(longrepr)


class ResearchGuard:
    """Counts the research skips of one run and fails the run when there are any."""

    def __init__(self) -> None:
        self.skips: list[str] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped and RESEARCH_MISSING in _reason(report):
            self.skips.append(f"{report.nodeid}: {_reason(report)}")

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.skips:
            terminalreporter.section("research skips fail the run", sep="=", red=True)
            for line in self.skips:
                terminalreporter.write_line(line)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.skips and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(ResearchGuard(), "research-guard")

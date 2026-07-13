"""U0 bootstrap smoke test.

Proves the package skeleton is import-clean and the CI-lite runner has a real
target before any feature unit lands. This is scaffolding verification, not
game behavior.
"""

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TOP_LEVEL_PACKAGES = [
    "engine",
    "engine.state",
    "clients",
    "clients.terminal",
]


@pytest.mark.parametrize("module", TOP_LEVEL_PACKAGES)
def test_top_level_package_imports(module):
    """Every top-level package imports without error (skeleton is import-clean)."""
    assert importlib.import_module(module) is not None


def test_makefile_has_test_target():
    """The CI-lite runner exists with a real `test` target to run."""
    makefile = REPO_ROOT / "Makefile"
    assert makefile.is_file(), "Makefile missing — no uniform green-tree gate"
    assert "test:" in makefile.read_text(), "Makefile has no `test` target"


def test_pyproject_declares_pytest_testpath():
    """pyproject.toml wires pytest at the `tests/` path (U0 tooling)."""
    pyproject = REPO_ROOT / "pyproject.toml"
    assert pyproject.is_file()
    text = pyproject.read_text()
    assert "[tool.pytest.ini_options]" in text
    assert "tests" in text


def test_make_test_target_is_invokable():
    """`make` can resolve the test target (dry-run, no recursion into pytest)."""
    if shutil.which("make") is None:
        pytest.skip("make not available on this platform")
    result = subprocess.run(
        ["make", "-n", "test"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "pytest" in result.stdout

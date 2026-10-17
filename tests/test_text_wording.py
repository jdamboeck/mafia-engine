"""No file of the game's text calls that text verbatim C64 text.

The classic theme's strings and the content names are the source's words in today's
German spelling: ``tests/test_text_fold.py`` folds each back to the C64 text frozen in
``tests/fixtures/c64_text/``. A comment in those files may still say a quote is
verbatim when it is about the fixture or a backticked source quote (``print"…"``),
which ``tests/test_citations.py`` holds to ``mf-prg.bas``; any other "verbatim" or
"byte for byte" there would claim the value below it is the C64's text, and it is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CONFIG = Path(__file__).resolve().parent.parent / "data" / "game_configs" / "mafia_1920s"

#: The files whose values are the game's display text: the classic theme's strings,
#: the entity names and descriptions, and the fighter names of the encounters and
#: the scenarios.
_TEXT_FILES = sorted(
    [
        *(_CONFIG / "themes" / "classic" / "strings").glob("*.yaml"),
        *(_CONFIG / "entities").glob("*.yaml"),
        *(_CONFIG / "content" / "encounters").glob("*.yaml"),
        *(_CONFIG / "content" / "scenarios").glob("*.yaml"),
    ]
)

_CLAIM = re.compile(r"verbatim|byte[- ]for[- ]byte", re.IGNORECASE)
_BACKTICKED = re.compile(r"`[^`]*`")


def _claims(text: str) -> list[str]:
    """The comment lines of ``text`` that call something verbatim, other than a
    backticked source quote or a mention of the C64-text fixture."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if "#" not in line:
            continue
        comment = _BACKTICKED.sub("", line[line.index("#") :])
        if _CLAIM.search(comment) and "fixture" not in comment.lower():
            found.append(f"{number}: {line.strip()}")
    return found


def test_the_text_files_are_found():
    names = {path.parent.name for path in _TEXT_FILES}
    assert {"strings", "entities", "encounters", "scenarios"} <= names
    assert len(_TEXT_FILES) > 30


@pytest.mark.parametrize("path", _TEXT_FILES, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_no_text_file_calls_its_values_verbatim(path: Path):
    """No comment in a string, entity, encounter or scenario file calls the text
    verbatim or byte for byte (a backticked source quote and the fixture excepted)."""
    assert _claims(path.read_text(encoding="utf-8")) == []


def test_the_claim_check_sees_a_header_claim_and_spares_source_quotes():
    assert _claims("# pub (Kneipe) — classic-theme strings (VERBATIM German).\nx: 1\n")
    assert _claims("# Ported byte-for-byte from the research.\n")
    assert not _claims('x: "a"  # :12101 `print"verbatim"`\n')
    assert not _claims("# the C64-text fixture holds the text verbatim.\n")

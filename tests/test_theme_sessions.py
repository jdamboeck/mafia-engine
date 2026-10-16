"""Two themes of the same game run side by side in one process (R6).

A theme's text lives in each session's own :class:`~engine.strings.Resolver`, not in a
module global: two :class:`~clients.terminal.TerminalSession` objects of the one
``mafia_1920s`` config, one worded in ``classic`` and one in the test fixture theme
(merged over classic, as ``--theme PATH`` merges it), each resolve their own theme's
text, however their resolutions interleave.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
sys.path.insert(0, str(_CONFIG_DIR.parent.parent))

from clients.terminal import TerminalSession  # noqa: E402
from engine.strings import Resolver  # noqa: E402

_TEST_THEME = Path(__file__).resolve().parent / "fixtures" / "themes" / "test"

#: Keys both themes word differently: the kredit-hai's :3025 title, the map hint
#: and the quit line (``tests/fixtures/themes/test/strings``).
_CLASSIC = {
    "locations.kdh.title": "KREDIT-HAI",
    "client.map.hint": "move: W/A/S/D into a door to enter. M menu, P saves, Q quits.",
    "client.bye": "bye.",
}
_FIXTURE = {
    "locations.kdh.title": "GELDVERLEIHER",
    "client.map.hint": "walk on.",
    "client.bye": "ciao.",
}


def _session(resolver: Resolver) -> TerminalSession:
    """A session as :func:`~clients.terminal.play` builds it, worded in ``resolver``."""
    return TerminalSession(
        seed=42,
        players=None,
        end_year=None,
        score_weight=None,
        load=None,
        save=None,
        watch_ai=False,
        resolver=resolver,
    )


@pytest.fixture
def sessions(monkeypatch) -> tuple[TerminalSession, TerminalSession]:
    """``(classic, themed)``: two sessions of the same config, built one after the
    other in this process; the themed one's strings are the fixture merged over
    classic's, as ``--theme PATH`` merges them."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    classic = Resolver.from_config(_CONFIG_DIR, theme="classic")
    themed = classic.with_override(Resolver.from_directory(_TEST_THEME).tree)
    return _session(classic), _session(themed)


def test_the_two_themes_word_the_keys_differently():
    """The keys below are a real difference between the themes, not one the test
    only assumes: classic and the fixture each word them as listed."""
    classic = Resolver.from_config(_CONFIG_DIR, theme="classic")
    fixture = Resolver.from_directory(_TEST_THEME)
    assert {key: classic.resolve(key) for key in _CLASSIC} == _CLASSIC
    assert {key: fixture.resolve(key) for key in _FIXTURE} == _FIXTURE


def test_two_interleaved_sessions_each_resolve_their_own_theme(sessions):
    classic, themed = sessions
    for key in _CLASSIC:  # alternate: classic, themed, classic, themed, ...
        assert classic.text(key) == _CLASSIC[key]
        assert themed.text(key) == _FIXTURE[key]
    # The map hint each session took at construction is its own theme's too.
    assert (classic.note, themed.note) == (_CLASSIC["client.map.hint"], "walk on.")


def test_resolving_in_one_session_leaves_the_other_unchanged(sessions):
    classic, themed = sessions
    title = "locations.kdh.title"
    first = classic.text(title)
    for key in _FIXTURE:
        themed.text(key)
    assert classic.text(title) == first == "KREDIT-HAI"
    # And the other way round: classic resolving leaves the themed session's text.
    themed_first = themed.text(title)
    for key in _CLASSIC:
        classic.text(key)
    assert themed.text(title) == themed_first == "GELDVERLEIHER"
    # A key the fixture leaves alone still comes from classic in the themed session.
    assert themed.text("client.location.press_enter") == classic.text("client.location.press_enter")

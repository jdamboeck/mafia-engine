"""Tests for ScreenContext, cursor helpers, and SIGWINCH — T3 verification."""

from __future__ import annotations

import io
import os
import signal
import sys
from pathlib import Path

import pytest

from clients.terminal.palette import ColorSupport, Colors, load_palette
from clients.terminal import (
    CURSOR_HIDE,
    CURSOR_SHOW,
    ScreenContext,
    check_resize,
    hide_cursor,
    install_sigwinch_handler,
    play,
    show_cursor,
)
from tests.helpers import NEW_GAME_ACKS, deadline


def _buf() -> io.StringIO:
    return io.StringIO()


#: The built-in Pepto palette (no config) in truecolor: deterministic ANSI codes.
_COLORS = Colors(load_palette(), ColorSupport.TRUECOLOR)


class TestCursorHelpers:
    def test_hide_cursor_writes_escape(self) -> None:
        buf = _buf()
        hide_cursor(buf)
        assert CURSOR_HIDE in buf.getvalue()

    def test_show_cursor_writes_escape(self) -> None:
        buf = _buf()
        show_cursor(buf)
        assert CURSOR_SHOW in buf.getvalue()


class TestScreenContext:
    def test_switch_applies_colors(self) -> None:
        contexts = {
            "jail": {"bg": "blue", "fg": "light_grey"},
        }
        buf = _buf()
        ctx = ScreenContext(contexts, buf, _COLORS)
        ctx.switch("jail")
        val = buf.getvalue()
        # Should contain ANSI bg and fg codes.
        assert "\033[48;2;" in val
        assert "\033[38;2;" in val

    def test_switch_unknown_context_is_noop(self) -> None:
        buf = _buf()
        ctx = ScreenContext({}, buf, _COLORS)
        ctx.switch("nonexistent")
        assert buf.getvalue() == ""

    def test_reset_clears_colors(self) -> None:
        contexts = {"test": {"bg": "red", "fg": "white"}}
        buf = _buf()
        ctx = ScreenContext(contexts, buf, _COLORS)
        ctx.switch("test")
        buf.truncate(0)
        buf.seek(0)
        ctx.reset()
        val = buf.getvalue()
        assert "\033[39m" in val  # reset fg
        assert "\033[49m" in val  # reset bg

    def test_name_property(self) -> None:
        buf = _buf()
        ctx = ScreenContext({"a": {"bg": "black"}}, buf, _COLORS)
        assert ctx.name is None
        ctx.switch("a")
        assert ctx.name == "a"

    def test_from_config_loads_yaml(self, tmp_path: Path) -> None:
        rdr = tmp_path / "themes" / "classic" / "renderer"
        rdr.mkdir(parents=True)
        (rdr / "contexts.yaml").write_text(
            "overworld: { bg: light_blue, fg: black }\n", encoding="utf-8"
        )
        buf = _buf()
        ctx = ScreenContext.from_config(tmp_path, buf, _COLORS, theme="classic")
        ctx.switch("overworld")
        # The file's colours are what switching emits: light_blue bg, black fg.
        assert buf.getvalue() == "\033[48;2;155;222;255m\033[38;2;0;0;0m"
        assert ctx.name == "overworld"

    def test_from_config_fallback_on_missing(self) -> None:
        buf = _buf()
        ctx = ScreenContext.from_config(Path("/nonexistent"), buf, _COLORS)
        # No contexts loaded: switching to any name is a no-op.
        ctx.switch("overworld")
        assert buf.getvalue() == ""
        assert ctx.name is None


_HAS_SIGWINCH = hasattr(signal, "SIGWINCH")


@pytest.fixture
def _sigwinch_restored():
    """Put back whatever SIGWINCH handler the process had before the test."""
    previous = signal.getsignal(signal.SIGWINCH)
    yield
    signal.signal(signal.SIGWINCH, previous)


@pytest.mark.skipif(not _HAS_SIGWINCH, reason="no SIGWINCH on this platform")
@pytest.mark.usefixtures("_sigwinch_restored")
class TestSigwinch:
    def test_no_resize_since_last_check_is_false(self) -> None:
        install_sigwinch_handler()
        check_resize()  # drain anything an earlier test left
        assert check_resize() is False

    def test_a_real_sigwinch_is_reported_once(self) -> None:
        install_sigwinch_handler()
        check_resize()  # drain
        os.kill(os.getpid(), signal.SIGWINCH)
        assert check_resize() is True
        # Should clear after check.
        assert check_resize() is False

    def test_install_handler(self) -> None:
        install_sigwinch_handler()
        assert signal.getsignal(signal.SIGWINCH) is not signal.SIG_DFL


class _ResizingStdin(io.StringIO):
    """Standard input that answers ``lines``; on read number ``resize_at`` (1-based)
    the terminal is resized -- a real SIGWINCH to this process -- before the line
    is handed back, the way a window resize lands while the client waits for a key."""

    def __init__(self, lines: list[str], resize_at: int | None) -> None:
        super().__init__("".join(f"{line}\n" for line in lines))
        self._reads = 0
        self._resize_at = resize_at

    def readline(self, *args) -> str:
        self._reads += 1
        if self._reads == self._resize_at:
            os.kill(os.getpid(), signal.SIGWINCH)
        return super().readline(*args)


@pytest.mark.skipif(not _HAS_SIGWINCH, reason="no SIGWINCH on this platform")
@pytest.mark.usefixtures("_sigwinch_restored")
class TestResizeOnTheMap:
    """``play()`` installs the handler; a resize while the map waits for a key
    redraws the map with the resized note and drops that key (it was typed into
    a screen the player could no longer see)."""

    # Title ack, the house-rules offer, upkeep ack, the turn menu's walk, then the map's
    # first key: one step down (the start cell has walls left, right and above).
    _MOVE = [*NEW_GAME_ACKS, "2", "s"]
    _MAP_READ = 5
    _RESIZED_NOTE = " resized"  # client.map.resized in the classic theme

    def _play(self, monkeypatch, lines: list[str], resize_at: int | None = None):
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", _ResizingStdin(lines, resize_at))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20.0, "play() did not return (EOF spin?)", exc_type=AssertionError):
            state, _rng = play(seed=42, end_year=1930, score_weight=1)
        return state, out.getvalue()

    def test_resize_redraws_the_map_and_drops_the_key(self, monkeypatch) -> None:
        start, _ = self._play(monkeypatch, [*NEW_GAME_ACKS, "2"])  # quits at the first map prompt
        moved, plain_text = self._play(monkeypatch, self._MOVE)
        check_resize()  # drain: the resize below must be the only one
        resized, text = self._play(monkeypatch, self._MOVE, resize_at=self._MAP_READ)
        assert start is not None and moved is not None and resized is not None

        # Control: without the resize the key moves the player off the start cell.
        assert moved.players[0].po != start.players[0].po
        assert self._RESIZED_NOTE not in plain_text
        # With it, the map is redrawn with the resized note and the key is dropped:
        # the game ends exactly where it started.
        assert self._RESIZED_NOTE in text, "the map was not redrawn with the resized note"
        assert resized == start

"""Tests for ScreenContext, cursor helpers, and SIGWINCH — T3 verification."""

from __future__ import annotations

import io
from pathlib import Path

from clients.terminal import (
    CURSOR_HIDE,
    CURSOR_SHOW,
    ScreenContext,
    check_resize,
    hide_cursor,
    install_sigwinch_handler,
    show_cursor,
)


def _buf() -> io.StringIO:
    return io.StringIO()


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
        ctx = ScreenContext(contexts, buf)
        ctx.switch("jail")
        val = buf.getvalue()
        # Should contain ANSI bg and fg codes.
        assert "\033[48;2;" in val
        assert "\033[38;2;" in val

    def test_switch_unknown_context_is_noop(self) -> None:
        buf = _buf()
        ctx = ScreenContext({}, buf)
        ctx.switch("nonexistent")
        assert buf.getvalue() == ""

    def test_reset_clears_colors(self) -> None:
        contexts = {"test": {"bg": "red", "fg": "white"}}
        buf = _buf()
        ctx = ScreenContext(contexts, buf)
        ctx.switch("test")
        buf.truncate(0)
        buf.seek(0)
        ctx.reset()
        val = buf.getvalue()
        assert "\033[39m" in val  # reset fg
        assert "\033[49m" in val  # reset bg

    def test_name_property(self) -> None:
        buf = _buf()
        ctx = ScreenContext({"a": {"bg": "black"}}, buf)
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
        ctx = ScreenContext.from_config(tmp_path, buf, theme="classic")
        assert "overworld" in ctx._contexts

    def test_from_config_fallback_on_missing(self) -> None:
        buf = _buf()
        ctx = ScreenContext.from_config(Path("/nonexistent"), buf)
        assert ctx._contexts == {}


class TestSigwinch:
    def test_check_resize_default_false(self) -> None:
        import clients.terminal as term
        term._resize_pending = False
        assert check_resize() is False

    def test_check_resize_returns_true_when_set(self) -> None:
        import clients.terminal as term
        term._resize_pending = True
        assert check_resize() is True
        # Should clear after check.
        assert check_resize() is False

    def test_install_handler(self) -> None:
        import signal
        # Should not raise.
        install_sigwinch_handler()
        # Verify handler is installed (if SIGWINCH exists on this platform).
        if hasattr(signal, "SIGWINCH"):
            assert signal.getsignal(signal.SIGWINCH) is not signal.SIG_DFL

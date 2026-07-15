"""Tests for clients/terminal/renderers.py — T2 verification."""

from __future__ import annotations

import io

from clients.terminal.palette import _PEPTO_FALLBACK
from clients.terminal.renderers import (
    render_body,
    render_colored,
    render_header,
    render_map_frame,
    render_menu_option,
    render_screen_clear,
    render_separator,
    render_status_bar,
    render_subheader,
    set_palette,
)


def _out() -> io.StringIO:
    return io.StringIO()


def _setup() -> None:
    """Set the palette to Pepto fallback for deterministic ANSI codes."""
    set_palette(dict(_PEPTO_FALLBACK))


class TestRenderScreenClear:
    def test_writes_escape_sequence(self) -> None:
        _setup()
        buf = _out()
        render_screen_clear(buf)
        assert buf.getvalue() == "\033[2J\033[H"


class TestRenderSeparator:
    def test_writes_dim_line(self) -> None:
        _setup()
        buf = _out()
        render_separator(buf)
        val = buf.getvalue()
        assert "─" in val
        assert val.endswith("\n")


class TestRenderHeader:
    def test_contains_box_drawing(self) -> None:
        _setup()
        buf = _out()
        render_header("TEST", buf)
        val = buf.getvalue()
        assert "╔" in val
        assert "╗" in val
        assert "TEST" in val

    def test_contains_ansi_codes(self) -> None:
        _setup()
        buf = _out()
        render_header("TEST", buf)
        val = buf.getvalue()
        assert "\033[" in val  # has ANSI escapes
        assert val.endswith("\n")


class TestRenderSubheader:
    def test_contains_dashes(self) -> None:
        _setup()
        buf = _out()
        render_subheader("section", buf)
        val = buf.getvalue()
        assert "─" in val
        assert "section" in val


class TestRenderBody:
    def test_writes_light_grey_text(self) -> None:
        _setup()
        buf = _out()
        render_body("hello", buf)
        val = buf.getvalue()
        assert "hello" in val
        assert val.endswith("\n")


class TestRenderColored:
    def test_writes_specific_color(self) -> None:
        _setup()
        buf = _out()
        render_colored("warning", "red", buf)
        val = buf.getvalue()
        assert "warning" in val
        assert "\033[38;2;" in val  # truecolor fg


class TestRenderMenuOption:
    def test_writes_index_and_label(self) -> None:
        _setup()
        buf = _out()
        render_menu_option(1, "mieten", buf)
        val = buf.getvalue()
        assert "1)" in val
        assert "mieten" in val


class TestRenderStatusBar:
    def test_contains_player_info(self) -> None:
        _setup()
        buf = _out()
        render_status_bar("alcapone", 5400, 181, 19, buf)
        val = buf.getvalue()
        assert "alcapone" in val
        assert "5400$" in val
        assert "181" in val
        assert "19" in val

    def test_has_reverse_video(self) -> None:
        _setup()
        buf = _out()
        render_status_bar("test", 0, 0, 0, buf)
        val = buf.getvalue()
        assert "\033[7m" in val  # reverse video


class TestRenderMapFrame:
    def test_wraps_in_blue_bg(self) -> None:
        _setup()
        buf = _out()
        render_map_frame(["....@"], buf)
        val = buf.getvalue()
        assert "\033[48;2;" in val  # truecolor bg
        assert "....@" in val

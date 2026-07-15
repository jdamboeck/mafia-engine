"""T12 — integration smoke tests for the terminal renderer.

Verifies the full rendering pipeline: palette → renderers → ASCII art → no exceptions.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.terminal.ascii_art import location_art, title_art, title_screen
from clients.terminal.palette import (
    ColorSupport,
    _BASIC_8,
    _XTERM_256,
    bg,
    fg,
    load_palette,
    term_color_support,
)
from clients.terminal.renderers import (
    render_body,
    render_colored,
    render_header,
    render_map_frame,
    render_menu_option,
    render_prompt,
    render_screen_clear,
    render_separator,
    render_status_bar,
    render_subheader,
)

_CONFIG_DIR = (
    Path(__file__).resolve().parents[1]
    / "data" / "game_configs" / "mafia_1920s"
)


class TestSmokeRenderPipeline:
    """Load palette → render every function → no exceptions."""

    def setup_method(self):
        self.pal = load_palette(_CONFIG_DIR)
        self.buf = io.StringIO()

    def test_render_screen_clear(self):
        render_screen_clear(self.buf)
        assert "\033[2J\033[H" in self.buf.getvalue()

    def test_render_separator(self):
        render_separator(self.buf)
        out = self.buf.getvalue()
        assert "\033[2m" in out
        assert "──" in out

    def test_render_header(self):
        render_header("SCHLUPFWINKEL", self.buf)
        out = self.buf.getvalue()
        assert "SCHLUPFWINKEL" in out
        assert "╔" in out
        assert "╗" in out

    def test_render_subheader(self):
        render_subheader("Raum 1", self.buf)
        out = self.buf.getvalue()
        assert "Raum 1" in out
        assert "──" in out

    def test_render_body(self):
        render_body("Willkommen im Schlupfwinkel.", self.buf)
        out = self.buf.getvalue()
        assert "Willkommen" in out

    def test_render_colored(self):
        render_colored("Achtung!", "red", self.buf)
        out = self.buf.getvalue()
        assert "Achtung!" in out
        assert "\033[" in out  # has ANSI code

    def test_render_menu_option(self):
        render_menu_option(0, "Miete verlangen", self.buf)
        out = self.buf.getvalue()
        assert "0" in out
        assert "Miete verlangen" in out

    def test_render_prompt(self):
        render_prompt(self.buf)
        out = self.buf.getvalue()
        assert ">" in out

    def test_render_status_bar(self):
        render_status_bar("alcapone", 5400, 181, 19, self.buf)
        out = self.buf.getvalue()
        assert "alcapone" in out
        assert "5400" in out
        assert "181" in out
        assert "19" in out

    def test_render_map_frame(self):
        lines = ["ABCDE", "FGHIJ"]
        render_map_frame(lines, self.buf)
        out = self.buf.getvalue()
        assert "ABCDE" in out
        assert "FGHIJ" in out
        assert "\033[48;2;" in out  # blue background


class TestSmokeAsciiArt:
    """Verify title screen and location art return non-empty multi-line strings."""

    def test_title_art_lines(self):
        lines = title_art()
        assert len(lines) > 5
        combined = "\n".join(lines)
        assert "_____" in combined or "M" in combined

    def test_title_screen_has_prompt(self):
        screen = title_screen()
        assert "Druecke ENTER" in screen
        assert len(screen.split("\n")) > 5

    def test_location_art_slw(self):
        art = location_art("slw")
        assert art is not None
        assert len(art) > 5

    def test_location_art_pub(self):
        art = location_art("pub")
        assert art is not None
        assert len(art) > 5

    def test_location_art_sph(self):
        art = location_art("sph")
        assert art is not None
        assert len(art) > 5

    def test_location_art_waf(self):
        art = location_art("waf")
        assert art is not None
        assert len(art) > 5

    def test_location_art_unknown(self):
        assert location_art("nonexistent") is None


class TestSmokeColorSupport:
    """Verify color support detection works."""

    def test_term_color_support_returns_enum(self):
        val = term_color_support()
        assert isinstance(val, ColorSupport)

    def test_xterm_256_table_complete(self):
        pal = load_palette()
        for name in pal:
            assert name in _XTERM_256, f"missing xterm-256 mapping for {name}"

    def test_basic_8_table_complete(self):
        pal = load_palette()
        for name in pal:
            assert name in _BASIC_8, f"missing basic-8 mapping for {name}"

    def test_fg_256_fallback(self):
        pal = load_palette()
        out = fg("red", pal, support=ColorSupport.COLOR256)
        assert "\033[38;5;" in out

    def test_fg_8_fallback(self):
        pal = load_palette()
        out = fg("red", pal, support=ColorSupport.COLOR8)
        assert "\033[31m" in out

    def test_bg_256_fallback(self):
        pal = load_palette()
        out = bg("blue", pal, support=ColorSupport.COLOR256)
        assert "\033[48;5;" in out

    def test_bg_8_fallback(self):
        pal = load_palette()
        out = bg("blue", pal, support=ColorSupport.COLOR8)
        assert "\033[44m" in out

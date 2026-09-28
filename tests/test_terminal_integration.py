"""T12 — integration smoke tests for the terminal renderer.

Verifies the full rendering pipeline: palette → renderers → ASCII art → no exceptions.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.terminal import CURSOR_SHOW
from clients.terminal.ascii_art import location_art, title_art, title_screen
from clients.terminal.palette import (
    ColorSupport,
    Colors,
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
from engine.strings import Resolver


_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"


class TestSmokeRenderPipeline:
    """Load palette → render every function → no exceptions."""

    def setup_method(self):
        self.pal = load_palette(_CONFIG_DIR)
        self.colors = Colors(self.pal, ColorSupport.TRUECOLOR)
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
        render_header("SCHLUPFWINKEL", self.buf, self.colors)
        out = self.buf.getvalue()
        assert "SCHLUPFWINKEL" in out
        assert "╔" in out
        assert "╗" in out

    def test_render_subheader(self):
        render_subheader("Raum 1", self.buf, self.colors)
        out = self.buf.getvalue()
        assert "Raum 1" in out
        assert "──" in out

    def test_render_body(self):
        render_body("Willkommen im Schlupfwinkel.", self.buf, self.colors)
        out = self.buf.getvalue()
        assert "Willkommen" in out

    def test_render_colored(self):
        render_colored("Achtung!", "red", self.buf, self.colors)
        out = self.buf.getvalue()
        assert "Achtung!" in out
        assert "\033[" in out  # has ANSI code

    def test_render_menu_option(self):
        render_menu_option(0, "Miete verlangen", self.buf, self.colors)
        out = self.buf.getvalue()
        assert "0" in out
        assert "Miete verlangen" in out

    def test_render_prompt(self):
        render_prompt(self.buf)
        out = self.buf.getvalue()
        assert ">" in out

    def test_render_status_bar(self):
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        render_status_bar("alcapone", 5400, 181, 19, self.buf, resolver, self.colors)
        out = self.buf.getvalue()
        assert "alcapone" in out
        assert "5400" in out
        assert "181" in out
        assert "19" in out

    def test_render_map_frame(self):
        lines = ["ABCDE", "FGHIJ"]
        render_map_frame(lines, self.buf, self.colors)
        out = self.buf.getvalue()
        assert "ABCDE" in out
        assert "FGHIJ" in out
        assert "\033[48;2;" in out  # blue background


class TestSmokeAsciiArt:
    """Verify title screen and location art return non-empty multi-line strings."""

    def test_title_art_lines(self):
        lines = title_art(Colors(load_palette(), ColorSupport.TRUECOLOR))
        assert len(lines) > 5
        combined = "\n".join(lines)
        assert "_____" in combined or "M" in combined

    def test_title_screen_has_prompt(self):
        screen = title_screen(Colors(load_palette(), ColorSupport.TRUECOLOR))
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

    def test_location_art_ble(self):
        art = location_art("ble")
        assert art is not None
        assert len(art) > 5

    def test_location_art_pol(self):
        art = location_art("pol")
        assert art is not None
        assert len(art) > 5

    def test_location_art_aut(self):
        art = location_art("aut")
        assert art is not None
        assert len(art) > 5

    def test_location_art_sub(self):
        art = location_art("sub")
        assert art is not None
        assert len(art) > 5

    def test_location_art_unknown(self):
        assert location_art("nonexistent") is None

    def test_entering_a_location_shows_its_art_and_waits_for_a_key(self, monkeypatch):
        """Walking into sph shows its art splash, which eats one key before the menu:
        the menu's "0" (play) and the game's "0" (poker) reach the wager prompt only
        when a splash ack comes first."""
        from engine.movement import load_city
        from tests.test_client_loop import (
            find_door_cell,
            load_city_raw,
            new_state,
            run_play,
            walk_keys_to_cell,
        )

        city_raw = load_city_raw()
        walk = walk_keys_to_cell(
            new_state(42), load_city(city_raw), find_door_cell(city_raw, "sph")
        )
        art = location_art("sph")
        assert art is not None
        art_line = next(line for line in art if line.strip())

        acked = run_play(monkeypatch, seed=42, stdin_keys=walk + ["", "0", "0"])
        unacked = run_play(monkeypatch, seed=42, stdin_keys=walk + ["0", "0"])

        assert art_line in acked and "ENTER druecken..." in acked
        assert acked.index(art_line) < acked.index("ENTER druecken...")
        assert "dein einsatz" in acked.lower(), "the acked script never reached the wager"
        assert "dein einsatz" not in unacked.lower(), "the splash did not wait for a key"


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


class TestMapDisplayWidth:
    """Every map line must have exactly 42 visible columns (no wide-char protrusion)."""

    #: Any CSI escape: colors, and the screen clear / cursor controls before the map.
    _ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")

    def _render_map(self, monkeypatch) -> str:
        """The first map screen of a real ``play()``, from its top border on."""
        from tests.test_client_loop import run_play

        output = run_play(monkeypatch, seed=42, stdin_keys=["q"])
        lines = output.split("\n")
        top = "╔" + "═" * 40 + "╗"
        start = next(i for i, line in enumerate(lines) if self._ANSI.sub("", line) == top)
        return "\n".join(lines[start:])

    def test_all_lines_exact_width(self, monkeypatch):
        output = self._render_map(monkeypatch)
        ansi_re = self._ANSI
        lines = output.split("\n")
        # Lines 0 (top border) through 26 (bottom border) = 27 lines
        # Lines 27+ are legend/status — skip those
        for i, line in enumerate(lines[:27]):
            clean = ansi_re.sub("", line)
            assert len(clean) == 42, f"Line {i}: visible width {len(clean)} != 42  ({clean!r})"

    def test_no_wide_chars_in_map(self, monkeypatch):
        import unicodedata

        output = self._render_map(monkeypatch)
        ansi_re = self._ANSI
        lines = output.split("\n")
        for i, line in enumerate(lines[:27]):
            clean = ansi_re.sub("", line)
            for ch in clean:
                if unicodedata.east_asian_width(ch) == "W":
                    assert False, f"Line {i}: wide char {ch!r} U+{ord(ch):04X}"

    def test_every_cell_has_a_glyph_of_its_own(self, monkeypatch):
        """Every screen code on the city map has a glyph: none falls back to the
        placeholder dot drawn for a code the client does not know."""
        lines = self._render_map(monkeypatch).split("\n")
        rows = [self._ANSI.sub("", line)[1:-1] for line in lines[1:26]]
        assert all(len(row) == 40 for row in rows)
        unknown = [(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row) if ch == "·"]
        assert unknown == [], f"cells drawn with the fallback glyph: {unknown}"


class TestQuitVocabulary:
    """On the map, ``q`` and the end of input quit; any other key does not (KTD-2).
    The turn-over screen shares the vocabulary (:class:`TestTurnOverQuit`)."""

    @staticmethod
    def _first_step():
        """A key that steps from the start and the cell it steps to."""
        from engine.movement import load_city, try_move
        from tests.test_client_loop import MOVE_KEYS, load_city_raw, new_state

        city = load_city(load_city_raw())
        for key, delta in MOVE_KEYS.items():
            result = try_move(new_state(42), city, delta)
            if getattr(result.payload, "kind", None) == "step":
                return key, result.state.players[0].po
        raise AssertionError("no stepping direction available from the start")

    def test_q_is_quit(self, monkeypatch):
        from tests.test_client_loop import new_state, run_play_returning

        step, _cell = self._first_step()
        output, (state, _rng) = run_play_returning(monkeypatch, seed=42, stdin_keys=["q", step])
        assert output.endswith("bye.\n" + CURSOR_SHOW)
        assert state.players[0].po == new_state(42).players[0].po, "a key after q was played"

    def test_eof_is_quit(self, monkeypatch):
        from tests.test_client_loop import new_state, run_play_returning

        output, (state, _rng) = run_play_returning(monkeypatch, seed=42, stdin_keys=[])
        assert output.endswith("bye.\n" + CURSOR_SHOW)
        assert state.players[0].po == new_state(42).players[0].po
        assert "(use W/A/S/D, M, P or Q)" not in output

    @pytest.mark.parametrize("key", ["x", "e", "1"])
    def test_other_keys_are_not_quit(self, monkeypatch, key):
        from tests.test_client_loop import run_play_returning

        step, cell = self._first_step()
        output, (state, _rng) = run_play_returning(
            monkeypatch, seed=42, stdin_keys=[key, step, "q"]
        )
        assert "(use W/A/S/D, M, P or Q)" in output, f"{key!r} was not refused as a bad key"
        assert output.count("bye.") == 1
        assert state.players[0].po == cell, f"the session ended at {key!r}"


class TestTurnOverQuit:
    """At the turn-over prompt, q/EOF exits cleanly; any other key advances the turn.

    ``play()`` only reaches turn-over after the active player's movement points are
    walked to 0, so these tests drive the real loop over a scripted (piped) stdin.
    The walk is asked of the engine (at each step, a movement key that steps on the
    current map), then the turn-over key under test. Whether the turn advanced is
    read off what ``play()`` shows and returns: the clock, and the round's standings
    screen that only an advance opens.
    """

    def _walk_to_turn_over(self):
        """The movement keys that walk the start player to turn-over (stepping moves
        only, so no move is spent entering a location mid-walk)."""
        from engine.movement import load_city, try_move
        from tests.test_client_loop import MOVE_KEYS, load_city_raw, new_state

        city = load_city(load_city_raw())
        state = new_state(42)
        keys = []
        for _ in range(200):
            for key, delta in MOVE_KEYS.items():
                result = try_move(state, city, delta)
                if getattr(result.payload, "kind", None) == "step":
                    break
            else:
                raise AssertionError("no stepping direction available from this state")
            state = result.state
            keys.append(key)
            if getattr(result.payload, "turn_over", False):
                return keys
        raise AssertionError("did not reach turn_over within 200 steps")

    def _run(self, monkeypatch, turn_over_keys):
        """Drive play() to the turn-over screen, answer it; return (stdout, state)."""
        from tests.test_client_loop import run_play_returning

        keys = self._walk_to_turn_over() + turn_over_keys
        output, (state, _rng) = run_play_returning(monkeypatch, seed=42, stdin_keys=keys)
        assert "turn_over" in output, "the walk never reached the turn-over screen"
        return output, state

    def test_q_at_turn_over_exits_without_advancing(self, monkeypatch):
        output, state = self._run(monkeypatch, ["q"])
        assert (state.clock.year, state.clock.month) == (1925, 0)
        assert "spielstand" not in output
        assert output.endswith("bye.\n" + CURSOR_SHOW)

    def test_eof_at_turn_over_exits_without_advancing(self, monkeypatch):
        # No turn-over key supplied: stdin exhausts, which quits like q.
        output, state = self._run(monkeypatch, [])
        assert (state.clock.year, state.clock.month) == (1925, 0)
        assert "spielstand" not in output
        assert output.endswith("bye.\n" + CURSOR_SHOW)

    def test_other_key_at_turn_over_advances(self, monkeypatch):
        # A non-quit key advances exactly one turn: a single player wraps the round,
        # so the standings of the month just played show, and their read hits EOF.
        output, state = self._run(monkeypatch, ["x"])
        assert (state.clock.year, state.clock.month) == (1925, 1)
        assert output.index("turn_over") < output.index("spielstand 1925-1\n")

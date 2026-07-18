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
from tests.helpers import with_player

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

    def test_location_art_wired_in_run_location(self):
        """_run_location imports and calls location_art on entry."""
        import inspect

        from clients.terminal.__main__ import _run_location

        src = inspect.getsource(_run_location)
        assert "location_art" in src
        assert "stdin.readline()" in src


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

    def _render_map(self) -> str:
        import yaml as _yaml

        from clients.terminal.__main__ import _CONFIG_DIR, render_map
        from engine.config_loader import load_game_config
        from engine.movement import load_city

        city_raw = _yaml.safe_load(
            (_CONFIG_DIR / "content" / "map" / "city.yaml").read_text(encoding="utf-8")
        )
        city = load_city(city_raw)
        cfg = load_game_config(_CONFIG_DIR)
        state = cfg.module.new_game(
            seed=42, end_year=1930, score_weight=1.0, players=[("a", "b")]
        )
        state = with_player(state, 0, po=141)
        buf = io.StringIO()
        render_map(city, city_raw, state, buf)
        return buf.getvalue()

    def test_all_lines_exact_width(self):
        import re

        output = self._render_map()
        ansi_re = re.compile(r"\033\[[0-9;]*m|\033\[[?][0-9;]*[hl]")
        lines = output.split("\n")
        # Lines 0 (top border) through 26 (bottom border) = 27 lines
        # Lines 27+ are legend/status — skip those
        for i, line in enumerate(lines[:27]):
            clean = ansi_re.sub("", line)
            assert len(clean) == 42, (
                f"Line {i}: visible width {len(clean)} != 42  ({clean!r})"
            )

    def test_no_wide_chars_in_map(self):
        import re
        import unicodedata

        output = self._render_map()
        ansi_re = re.compile(r"\033\[[0-9;]*m|\033\[[?][0-9;]*[hl]")
        lines = output.split("\n")
        for i, line in enumerate(lines[:27]):
            clean = ansi_re.sub("", line)
            for ch in clean:
                if unicodedata.east_asian_width(ch) == "W":
                    assert False, (
                        f"Line {i}: wide char {ch!r} U+{ord(ch):04X}"
                    )


class TestScreenCodeMapping:
    """The _CODE_TO_CHAR table covers all non-door, non-special screen codes."""

    def test_table_has_23_entries(self):
        from clients.terminal.__main__ import _CODE_TO_CHAR

        assert len(_CODE_TO_CHAR) == 23

    def test_all_table_values_narrow(self):
        import unicodedata

        from clients.terminal.__main__ import _CODE_TO_CHAR

        for code, char in _CODE_TO_CHAR.items():
            eaw = unicodedata.east_asian_width(char)
            assert eaw != "W", (
                f"Code {code}: char {char!r} U+{ord(char):04X} is wide (EAW={eaw})"
            )


class TestQuitVocabulary:
    """The shared quit predicate is the one vocabulary both screens use (KTD-2)."""

    def test_q_is_quit(self):
        from clients.terminal.__main__ import _is_quit

        assert _is_quit("q") is True

    def test_eof_is_quit(self):
        from clients.terminal.__main__ import _is_quit

        assert _is_quit("") is True

    def test_other_keys_are_not_quit(self):
        from clients.terminal.__main__ import _is_quit

        for key in (" ", "x", "w", "a", "s", "d"):
            assert _is_quit(key) is False, key


class TestTurnOverQuit:
    """At the turn-over prompt, q/EOF exits cleanly; any other key advances the turn.

    ``play()`` is monolithic and only reaches turn-over after the active player's
    movement points are walked to 0, so these tests drive the real loop over a
    scripted (piped) stdin. The walk itself is layout-independent: at each turn we
    feed a movement key that always steps on the current map, then the turn-over
    key under test. ``advance_turn`` is monkeypatched to a counter so the tests
    observe the advance-vs-return decision without running a second real turn.
    """

    def _first_stepping_key(self, state, city):
        """Return a movement key that produces a step from ``state`` (not wall/oob).

        Keeps the walk resilient to map geometry — we don't hardcode a direction
        sequence, we ask the engine which direction steps right now.
        """
        from clients.terminal.__main__ import _MOVE_KEYS
        from engine.movement import try_move

        for key, delta in _MOVE_KEYS.items():
            payload = try_move(state, city, delta).payload
            if getattr(payload, "kind", None) == "step":
                return key
        raise AssertionError("no stepping direction available from this state")

    def _walk_to_turn_over(self):
        """Build the movement-key sequence that walks the start player to turn-over.

        Returns the list of movement keys (one per step); the caller appends the
        turn-over response key under test. Uses only stepping moves so no move is
        consumed entering a location mid-walk.
        """
        import clients.terminal.__main__ as tmain
        import yaml
        from engine.config_loader import load_game_config
        from engine.movement import load_city, try_move

        cfg = load_game_config(tmain._CONFIG_DIR)
        city_raw = yaml.safe_load(
            (tmain._CONFIG_DIR / "content" / "map" / "city.yaml").read_text(
                encoding="utf-8"
            )
        )
        city = load_city(city_raw)
        state = cfg.module.new_game(
            seed=42, end_year=1930, score_weight=1.0,
            players=[("alcapone", "the outfit")],
        )
        keys = []
        for _ in range(200):
            key = self._first_stepping_key(state, city)
            from clients.terminal.__main__ import _MOVE_KEYS

            result = try_move(state, city, _MOVE_KEYS[key])
            state = result.state
            keys.append(key)
            if getattr(result.payload, "turn_over", False):
                return keys
        raise AssertionError("did not reach turn_over within 200 steps")

    def _script(self, keys):
        """Piped-stdin body: a leading blank line dismisses the title screen
        (``play()`` consumes one line there before the map loop), then one key
        per line."""
        return io.StringIO("\n".join([""] + keys) + "\n")

    def _run(self, monkeypatch, turn_over_key):
        """Drive play() through one turn-over, return the advance_turn call count."""
        import clients.terminal.__main__ as tmain

        keys = self._walk_to_turn_over() + [turn_over_key]
        calls = []
        monkeypatch.setattr(
            tmain, "advance_turn", lambda st, *a, **k: (calls.append(a), (st, False))[1]
        )
        monkeypatch.setattr(sys, "stdin", self._script(keys))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        tmain.play(seed=42)
        return len(calls)

    def test_q_at_turn_over_exits_without_advancing(self, monkeypatch):
        assert self._run(monkeypatch, "q") == 0

    def test_eof_at_turn_over_exits_without_advancing(self, monkeypatch):
        # No turn-over key supplied: stdin exhausts, _read_key() -> "" / "q" (quit).
        import clients.terminal.__main__ as tmain

        keys = self._walk_to_turn_over()
        calls = []
        monkeypatch.setattr(
            tmain, "advance_turn", lambda st, *a, **k: (calls.append(a), (st, False))[1]
        )
        monkeypatch.setattr(sys, "stdin", self._script(keys))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        tmain.play(seed=42)
        assert calls == []

    def test_other_key_at_turn_over_advances(self, monkeypatch):
        # A non-quit key advances exactly one turn (the next turn's map read then
        # hits EOF and quits).
        assert self._run(monkeypatch, "x") == 1

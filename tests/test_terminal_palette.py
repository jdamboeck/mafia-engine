"""Tests for clients/terminal/palette.py — T1 verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from clients.terminal.palette import (
    _PEPTO_FALLBACK,
    RESET_ALL,
    RESET_BG,
    RESET_FG,
    ColorSupport,
    bg,
    fg,
    load_palette,
    term_color_support,
)


class TestLoadPalette:
    """palette.yaml loading with fallback."""

    def test_loads_from_config_dir(self, tmp_path: Path) -> None:
        rdr = tmp_path / "themes" / "classic" / "renderer"
        rdr.mkdir(parents=True)
        (rdr / "palette.yaml").write_text("black: [0, 0, 0]\nred: [255, 0, 0]\n", encoding="utf-8")
        pal = load_palette(tmp_path, theme="classic")
        assert pal["black"] == (0, 0, 0)
        assert pal["red"] == (255, 0, 0)
        # Fallback keys still present.
        assert pal["light_blue"] == (155, 222, 255)

    def test_fallback_when_file_missing(self, tmp_path: Path) -> None:
        pal = load_palette(tmp_path, theme="classic")
        assert pal == _PEPTO_FALLBACK

    def test_fallback_on_malformed_yaml(self, tmp_path: Path) -> None:
        rdr = tmp_path / "themes" / "classic" / "renderer"
        rdr.mkdir(parents=True)
        (rdr / "palette.yaml").write_text("{{bad yaml", encoding="utf-8")
        pal = load_palette(tmp_path, theme="classic")
        assert pal == _PEPTO_FALLBACK

    def test_merges_with_fallback(self, tmp_path: Path) -> None:
        rdr = tmp_path / "themes" / "classic" / "renderer"
        rdr.mkdir(parents=True)
        (rdr / "palette.yaml").write_text("red: [100, 0, 0]\n", encoding="utf-8")
        pal = load_palette(tmp_path, theme="classic")
        # Overridden.
        assert pal["red"] == (100, 0, 0)
        # Fallback intact.
        assert pal["blue"] == (74, 54, 181)


TRUE = ColorSupport.TRUECOLOR


@pytest.mark.usefixtures("hostile_color_env")
class TestAnsiGenerators:
    """fg() and bg() produce correct ANSI escape sequences.

    Support is passed explicitly and the class runs under ``hostile_color_env``
    (a host that detects 256-color), so these hold on any terminal or CI runner.
    """

    def test_256color_variant(self) -> None:
        pal = {"red": (1, 2, 3)}
        assert fg("red", pal, ColorSupport.COLOR256) == "\033[38;5;124m"
        assert bg("red", pal, ColorSupport.COLOR256) == "\033[48;5;124m"

    def test_fg_truecolor(self) -> None:
        pal = {"test": (42, 100, 200)}
        assert fg("test", pal, TRUE) == "\033[38;2;42;100;200m"

    def test_bg_truecolor(self) -> None:
        pal = {"test": (42, 100, 200)}
        assert bg("test", pal, TRUE) == "\033[48;2;42;100;200m"

    def test_fg_black(self) -> None:
        pal = {"black": (0, 0, 0)}
        assert fg("black", pal, TRUE) == "\033[38;2;0;0;0m"

    def test_bg_white(self) -> None:
        pal = {"white": (255, 255, 255)}
        assert bg("white", pal, TRUE) == "\033[48;2;255;255;255m"


class TestColorSupportDetection:
    """term_color_support() picks the mode from the env, read fresh on every call."""

    @pytest.mark.parametrize(
        ("colorterm", "term", "expected"),
        [
            ("truecolor", "xterm-256color", ColorSupport.TRUECOLOR),
            ("24bit", "xterm-256color", ColorSupport.TRUECOLOR),
            (None, "xterm-256color", ColorSupport.COLOR256),
            (None, "dumb", ColorSupport.COLOR8),
            (None, None, ColorSupport.COLOR8),
            (None, "xterm", ColorSupport.TRUECOLOR),  # unknown -> fallback
        ],
    )
    def test_detects(
        self,
        monkeypatch: pytest.MonkeyPatch,
        colorterm: str | None,
        term: str | None,
        expected: ColorSupport,
    ) -> None:
        for var, val in (("COLORTERM", colorterm), ("TERM", term)):
            if val is None:
                monkeypatch.delenv(var, raising=False)
            else:
                monkeypatch.setenv(var, val)
        assert term_color_support() is expected

    def test_detection_follows_an_env_change_in_the_same_process(
        self, hostile_color_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing is cached: a later env change is seen by the next call."""
        assert term_color_support() is ColorSupport.COLOR256
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert term_color_support() is ColorSupport.TRUECOLOR

    def test_default_support_follows_detection(self, hostile_color_env: None) -> None:
        """Without an explicit ``support``, fg() uses the detected (256) mode."""
        assert fg("red", {"red": (1, 2, 3)}) == "\033[38;5;124m"


class TestResetConstants:
    """Reset sequences are correct ANSI codes."""

    def test_reset_all(self) -> None:
        assert RESET_ALL == "\033[0m"

    def test_reset_fg(self) -> None:
        assert RESET_FG == "\033[39m"

    def test_reset_bg(self) -> None:
        assert RESET_BG == "\033[49m"

    def test_legacy_reset_alias(self) -> None:
        from clients.terminal.palette import RESET

        assert RESET == RESET_ALL

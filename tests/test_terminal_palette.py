"""Tests for clients/terminal/palette.py — T1 verification."""

from __future__ import annotations

from pathlib import Path

from clients.terminal.palette import (
    _PEPTO_FALLBACK,
    RESET_ALL,
    RESET_BG,
    RESET_FG,
    bg,
    fg,
    load_palette,
)


class TestLoadPalette:
    """palette.yaml loading with fallback."""

    def test_loads_from_config_dir(self, tmp_path: Path) -> None:
        rdr = tmp_path / "themes" / "classic" / "renderer"
        rdr.mkdir(parents=True)
        (rdr / "palette.yaml").write_text(
            "black: [0, 0, 0]\nred: [255, 0, 0]\n", encoding="utf-8"
        )
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
        (rdr / "palette.yaml").write_text(
            "red: [100, 0, 0]\n", encoding="utf-8"
        )
        pal = load_palette(tmp_path, theme="classic")
        # Overridden.
        assert pal["red"] == (100, 0, 0)
        # Fallback intact.
        assert pal["blue"] == (74, 54, 181)


class TestAnsiGenerators:
    """fg() and bg() produce correct ANSI escape sequences."""

    def test_fg_truecolor(self) -> None:
        pal = {"test": (42, 100, 200)}
        assert fg("test", pal) == "\033[38;2;42;100;200m"

    def test_bg_truecolor(self) -> None:
        pal = {"test": (42, 100, 200)}
        assert bg("test", pal) == "\033[48;2;42;100;200m"

    def test_fg_black(self) -> None:
        pal = {"black": (0, 0, 0)}
        assert fg("black", pal) == "\033[38;2;0;0;0m"

    def test_bg_white(self) -> None:
        pal = {"white": (255, 255, 255)}
        assert bg("white", pal) == "\033[48;2;255;255;255m"


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

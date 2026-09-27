"""Tests for clients/terminal/palette.py — T1 verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from clients.terminal.palette import (
    RESET_ALL,
    RESET_BG,
    RESET_FG,
    ColorSupport,
    Colors,
    bg,
    fg,
    load_palette,
    read_palette_overrides,
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
        assert pal == load_palette()  # the built-in Pepto palette
        assert pal["light_blue"] == (155, 222, 255)

    def test_fallback_on_malformed_yaml(self, tmp_path: Path) -> None:
        rdr = tmp_path / "themes" / "classic" / "renderer"
        rdr.mkdir(parents=True)
        (rdr / "palette.yaml").write_text("{{bad yaml", encoding="utf-8")
        pal = load_palette(tmp_path, theme="classic")
        assert pal == load_palette()  # the built-in Pepto palette
        assert pal["light_blue"] == (155, 222, 255)

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
    """term_color_support() picks the mode from the env, read fresh on every call;
    a session reads it once, into its Colors."""

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

    def test_colors_detect_takes_the_support_the_env_reports(
        self, hostile_color_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Colors.detect`` reads the env once; the Colors it built keeps that mode."""
        colors = Colors.detect({"red": (1, 2, 3)})
        assert colors.support is ColorSupport.COLOR256
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert colors.fg("red") == "\033[38;5;124m"
        assert Colors.detect({"red": (1, 2, 3)}).fg("red") == "\033[38;2;1;2;3m"


class TestReadPaletteOverrides:
    """A selected theme's ``renderer/palette.yaml``: absent is fine, broken is an error."""

    def _write(self, theme_dir: Path, text: str) -> None:
        (theme_dir / "renderer").mkdir(parents=True)
        (theme_dir / "renderer" / "palette.yaml").write_text(text, encoding="utf-8")

    def test_no_file_means_no_overrides(self, tmp_path: Path) -> None:
        assert read_palette_overrides(tmp_path) == {}

    def test_reads_only_the_colours_the_theme_sets(self, tmp_path: Path) -> None:
        self._write(tmp_path, "red: [1, 2, 3]\n")
        assert read_palette_overrides(tmp_path) == {"red": (1, 2, 3)}

    @pytest.mark.parametrize(
        "text", ["- [1, 2, 3]\n", "red: [1, 2]\n", "red: [1, 2, 300]\n", "red: blue\n"]
    )
    def test_a_broken_file_raises_naming_it(self, tmp_path: Path, text: str) -> None:
        self._write(tmp_path, text)
        with pytest.raises(ValueError, match="palette.yaml"):
            read_palette_overrides(tmp_path)


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

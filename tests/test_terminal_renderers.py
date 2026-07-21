"""Tests for clients/terminal/renderers.py — T2 verification."""

from __future__ import annotations

import io

from clients.terminal.palette import _PEPTO_FALLBACK
from clients.terminal.renderers import (
    render_body,
    render_colored,
    render_combat_grid,
    render_combat_losses,
    render_combat_message,
    render_fighter_panel,
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


# --------------------------------------------------------------------------- #
# U7 — combat rendering (grid, fighter panel, message, losses)                #
# --------------------------------------------------------------------------- #


def _payload(**overrides):
    base = {
        "version": 1,
        "sides": [
            [
                {
                    "name": "hero",
                    "weapon": 5,
                    "vitality": 20,
                    "position": 100,
                    "down": False,
                    "attrs": {"kraft": 30, "brutalitaet": 30},
                }
            ],
            [
                {
                    "name": "thug",
                    "weapon": 0,
                    "vitality": 5,
                    "position": 141,
                    "down": False,
                    "attrs": {"kraft": 10, "brutalitaet": 10},
                }
            ],
        ],
        "grid": [],
        "active_side": 1,
        "active_fighter": 1,
        "losses": [0, 0],
        "prompt": "action",
        "message": None,
        "fighter": {
            "name": "hero",
            "weapon": 5,
            # A5: vitality is a top-level SLOT; the panel renders it first, labelled by
            # the theme. The non-vitality stats live in the opaque attrs map.
            "vitality": 20,
            "position": 100,
            "down": False,
            "attrs": {"kraft": 30, "brutalitaet": 30, "intelligenz": 17},
        },
    }
    base.update(overrides)
    return base


class _FakeResolver:
    """A minimal stand-in for engine.strings.Resolver: dotted key -> template."""

    _TREE = {
        "combat": {
            "unknown_action": "?",
            "illegal_move": "(das geht nicht)",
            "miss": "verfehlt!",
            "hit": "treffer!",
            "hit_player_down": "treffer! spieler {index} ist am ende!",
            "hit_enemy_down": "der gegner ist tot!",
            "winner_banner": "sieger: {name}!!!",
            "losses_heading": "verluste der spieler:",
            "losses_line": "{name}: {count}",
            "panel_weapon": "waffe: {weapon}",
            # A5: vitality is a slot rendered first, labelled by the theme; the rest are
            # theme-declared attrs (mirrors the classic theme).
            "panel_vitality": "energie: {value}",
            "panel_attrs": ["kraft", "brutalitaet"],
            "panel_attr_kraft": "kraft: {value}",
            "panel_attr_brutalitaet": "brutalitaet: {value}",
            "action_prompt": "deine aktion:",
            "key_legend": "[wasd] bewegen  [f]+[wasd] schiessen  [p] aussetzen",
        }
    }

    @property
    def tree(self):
        return self._TREE

    def resolve(self, key, params=None):
        node = self._TREE
        for seg in key.split("."):
            node = node[seg]
        return node.format(**(params or {}))


class TestRenderCombatGrid:
    def test_exactly_40_columns_13_rows(self) -> None:
        _setup()
        buf = _out()
        render_combat_grid(_payload(), buf)
        lines = buf.getvalue().rstrip("\n").split("\n")
        assert len(lines) == 13
        import re

        ansi_re = re.compile(r"\033\[[0-9;]*m")
        for line in lines:
            assert len(ansi_re.sub("", line)) == 40

    def test_no_wide_characters(self) -> None:
        _setup()
        buf = _out()
        render_combat_grid(_payload(), buf)
        import re
        import unicodedata

        ansi_re = re.compile(r"\033\[[0-9;]*m")
        clean = ansi_re.sub("", buf.getvalue())
        for ch in clean:
            if ch == "\n":
                continue
            assert unicodedata.east_asian_width(ch) != "W"

    def test_active_fighter_is_highlighted(self) -> None:
        _setup()
        buf = _out()
        render_combat_grid(_payload(), buf)
        assert "\033[7m" in buf.getvalue()  # reverse video on the active fighter

    def test_downed_fighter_renders_distinct_glyph(self) -> None:
        _setup()
        buf = _out()
        payload = _payload()
        payload["sides"][1][0]["down"] = True
        render_combat_grid(payload, buf)
        row, col = divmod(141, 40)
        lines = buf.getvalue().rstrip("\n").split("\n")
        import re

        ansi_re = re.compile(r"\033\[[0-9;]*m")
        clean_row = ansi_re.sub("", lines[row])
        assert clean_row[col] == "x"

    def test_missing_grid_reads_as_open_ground(self) -> None:
        """An empty/short grid list must not raise -- every cell past the end reads
        as open floor (mirrors engine.combat.can_move_onto's short-grid handling)."""
        _setup()
        buf = _out()
        render_combat_grid(_payload(grid=[]), buf)  # must not raise
        assert buf.getvalue()


class TestRenderFighterPanel:
    def test_shows_stats_and_weapon_name(self) -> None:
        _setup()
        buf = _out()
        render_fighter_panel(
            _payload(),
            _FakeResolver(),
            ["haende", "messer", "knueppel", "schlagkette", "wurfsterne", "revolver"],
            buf,
        )
        val = buf.getvalue()
        assert "hero" in val
        assert "revolver" in val  # weapon id 5 resolved to its name
        assert "20" in val  # energie
        assert "30" in val  # kraft / brutalitaet

    def test_unknown_weapon_id_falls_back_to_the_raw_id(self) -> None:
        _setup()
        buf = _out()
        render_fighter_panel(_payload(), _FakeResolver(), [], buf)
        assert "5" in buf.getvalue()

    def test_no_fighter_writes_nothing(self) -> None:
        _setup()
        buf = _out()
        render_fighter_panel(_payload(fighter=None), _FakeResolver(), [], buf)
        assert buf.getvalue() == ""


class TestRenderCombatMessage:
    def test_string_message_resolves_the_matching_key(self) -> None:
        _setup()
        buf = _out()
        render_combat_message(_payload(message="illegal_move"), _FakeResolver(), buf)
        assert "geht nicht" in buf.getvalue()

    def test_miss_dict_resolves_to_miss_key(self) -> None:
        _setup()
        buf = _out()
        msg = {
            "hit": False,
            "damage": 0,
            "target_side": None,
            "target_index": None,
            "downed": False,
        }
        render_combat_message(_payload(message=msg), _FakeResolver(), buf)
        assert "verfehlt" in buf.getvalue()

    def test_hit_and_downed_player_side_resolves_player_down(self) -> None:
        _setup()
        buf = _out()
        msg = {"hit": True, "damage": 20, "target_side": 1, "target_index": 0, "downed": True}
        render_combat_message(_payload(message=msg), _FakeResolver(), buf)
        assert "spieler 1 ist am ende" in buf.getvalue()

    def test_hit_and_downed_enemy_side_resolves_enemy_down(self) -> None:
        _setup()
        buf = _out()
        msg = {"hit": True, "damage": 20, "target_side": 2, "target_index": 0, "downed": True}
        render_combat_message(_payload(message=msg), _FakeResolver(), buf)
        assert "gegner ist tot" in buf.getvalue()

    def test_hit_not_downed_resolves_plain_hit(self) -> None:
        _setup()
        buf = _out()
        msg = {"hit": True, "damage": 2, "target_side": 2, "target_index": 0, "downed": False}
        render_combat_message(_payload(message=msg), _FakeResolver(), buf)
        assert "treffer!" in buf.getvalue()

    def test_no_message_writes_nothing(self) -> None:
        _setup()
        buf = _out()
        render_combat_message(_payload(message=None), _FakeResolver(), buf)
        assert buf.getvalue() == ""


class TestRenderCombatLosses:
    def test_shows_heading_and_both_sides(self) -> None:
        _setup()
        buf = _out()
        render_combat_losses(_payload(losses=[2, 1]), _FakeResolver(), buf)
        val = buf.getvalue()
        assert "verluste" in val
        assert "2" in val and "1" in val


class TestFighterPanelIsAttributeAgnostic:
    """U2: the panel renders from the opaque ``attrs`` map + theme-declared names.

    The renderer used to read ``energie``/``kraft``/``brutalitaet`` straight off the
    payload with ``.get(name, 0)``. That is the silent-failure shape U2 had to close:
    after the attributes moved into ``attrs``, those reads would have rendered
    **zeros, not errors** — and no grep over ``engine/`` could have caught it, since
    the defect lives in ``clients/``.
    """

    #: The panel exactly as it rendered BEFORE the wire change, captured from the
    #: real classic theme. Byte-for-byte — the change must be invisible to a player.
    EXPECTED = "\x1b[38;2;178;178;178mhero\x1b[39m\nwaffe: revolver\nenergie: 20\nkraft: 30\nbrutalitaet: 44\n"

    def _weapons(self):
        return ["haende", "messer", "knueppel", "schlagkette", "wurfsterne", "revolver"]

    def test_panel_is_byte_identical_to_the_pre_u2_rendering(self) -> None:
        """The captured baseline, reproduced through the REAL theme and a real Fighter."""
        from engine.state import Fighter, json_safe
        from engine.strings import Resolver

        _setup()
        buf = _out()
        fighter = Fighter(
            name="hero",
            weapon=5,
            vitality=20,
            attrs={"kraft": 30, "brutalitaet": 44},
            position=10,
        )
        resolver = Resolver.from_config("data/game_configs/mafia_1920s")
        render_fighter_panel({"fighter": json_safe(fighter)}, resolver, self._weapons(), buf)
        assert buf.getvalue() == self.EXPECTED

    def test_a_game_with_different_attributes_renders_its_own(self) -> None:
        """An invented game's attributes render with no renderer change (the claim).

        The theme names ``aim``/``grit``; the payload carries them in ``attrs``. If
        the renderer still hardcoded this game's German names, this panel would come
        out empty.
        """

        class _OtherThemeResolver:
            _TREE = {
                "combat": {
                    "panel_weapon": "gun: {weapon}",
                    "panel_attrs": ["aim", "grit"],
                    "panel_attr_aim": "aim: {value}",
                    "panel_attr_grit": "grit: {value}",
                }
            }

            @property
            def tree(self):
                return self._TREE

            def resolve(self, key, params=None):
                node = self._TREE
                for seg in key.split("."):
                    node = node[seg]
                return node.format(**(params or {}))

        _setup()
        buf = _out()
        payload = {"fighter": {"name": "ranger", "weapon": 0, "attrs": {"aim": 7, "grit": 3}}}
        render_fighter_panel(payload, _OtherThemeResolver(), ["fist"], buf)
        val = buf.getvalue()
        assert "aim: 7" in val
        assert "grit: 3" in val

    def test_an_attribute_the_theme_does_not_list_is_not_shown(self) -> None:
        """The panel is a curated view, not a dump of every attribute carried."""
        _setup()
        buf = _out()
        render_fighter_panel(_payload(), _FakeResolver(), self._weapons(), buf)
        # intelligenz rides in attrs (it survives round-trips) but the original's
        # panel never showed it, so the theme does not list it.
        assert "intelligenz" not in buf.getvalue()

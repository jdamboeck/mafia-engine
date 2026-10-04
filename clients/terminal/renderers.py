"""Role-based rendering functions for the terminal client.

Each function styles text by **role** (header, body, menu option, status bar,
etc.).  Strings stay pure text; the renderer decides presentation.  No inline
format codes — the renderer owns all ANSI styling.

All functions write to an injected ``TextIO`` for testability. Colours come from
the session's :class:`~clients.terminal.palette.Colors` (the theme's palette and the
terminal's colour support, resolved once per session), passed in like the resolver.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, TextIO

import yaml

from engine.strings import Resolver

from clients.terminal.palette import (
    DIM,
    RESET_ALL,
    RESET_BG,
    RESET_FG,
    REVERSE,
    Colors,
)
from clients.terminal import CLEAR, client_text

# ---------------------------------------------------------------------------
# Layout config (terminal-specific, loaded once at import time)
# ---------------------------------------------------------------------------

_LAYOUT_PATH = Path(__file__).parent / "layout.yaml"


def _load_layout() -> dict:
    try:
        return yaml.safe_load(_LAYOUT_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


_LAYOUT = _load_layout()

# ---------------------------------------------------------------------------
# Terminal width helper
# ---------------------------------------------------------------------------


def _term_width() -> int:
    return shutil.get_terminal_size().columns


# ---------------------------------------------------------------------------
# Role-based render functions
# ---------------------------------------------------------------------------


def render_screen_clear(out: TextIO) -> None:
    """Clear the screen and home the cursor."""
    out.write(CLEAR)


def render_separator(out: TextIO) -> None:
    """Draw a horizontal separator line at terminal width."""
    width = _term_width()
    out.write(f"{DIM}{'─' * width}{RESET_ALL}\n")


def render_header(text: str, out: TextIO, colors: Colors) -> None:
    """Full-width double-line box header with centered text.

    ``╔══════════════════ SCHLUPFWINKEL ══════════════════╗``
    """
    width = _term_width()
    # ╔ + space + text + space + ╗ = text_len + 4 for corners+spaces
    # Fill remaining with ═
    inner_width = width - 2  # subtract ╔ and ╗
    pad_total = max(0, inner_width - len(text) - 2)  # -2 for the spaces around text
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    out.write(f"{colors.fg('light_grey')}╔{'═' * left_pad}  {text}  {'═' * right_pad}╗{RESET_FG}\n")


def render_subheader(text: str, out: TextIO, colors: Colors) -> None:
    """Single-line centered subheader: ``──── text ────``"""
    width = _term_width()
    pad_total = max(0, width - len(text) - 2)  # -2 for the spaces around text
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    out.write(
        f"{DIM}{colors.fg('light_grey')}{'─' * left_pad}  {text}  {'─' * right_pad}{RESET_ALL}\n"
    )


def render_body(text: str, out: TextIO, colors: Colors) -> None:
    """Normal body text in light grey."""
    out.write(f"{colors.fg('light_grey')}{text}{RESET_FG}\n")


def render_colored(text: str, color_name: str, out: TextIO, colors: Colors) -> None:
    """Text in a specific Pepto palette color."""
    out.write(f"{colors.fg(color_name)}{text}{RESET_FG}\n")


def render_menu_option(index: int, label: str, out: TextIO, colors: Colors) -> None:
    """Numbered menu option: blue index, light grey label."""
    out.write(
        f"  {colors.fg('light_blue')}{index}){RESET_FG} {colors.fg('light_grey')}{label}{RESET_FG}\n"
    )


def render_prompt(out: TextIO) -> None:
    """Input prompt ``> ``."""
    out.write("> ")
    out.flush()


def render_status_bar(
    player_name: str,
    cash: int,
    pos: int,
    ms: int,
    out: TextIO,
    resolver: Resolver,
    colors: Colors,
) -> None:
    """Full-width reverse-video status bar with player name.

    The bar's text is the theme's ``client.status_bar`` template, e.g.
    `` alcapone │ cash 5400$ │ pos 181 │ ms 19 ``.
    """
    width = _term_width()
    bar = client_text(
        "client.status_bar",
        {"name": player_name, "cash": cash, "pos": pos, "ms": ms},
        resolver=resolver,
    )
    padded = bar.center(width)
    out.write(f"{REVERSE}{colors.bg('brown')}{colors.fg('light_grey')}{padded}{RESET_ALL}\n")


def render_status_bar_from_state(
    state: Any, out: TextIO, resolver: Resolver, colors: Colors
) -> None:
    """Convenience wrapper: extract player info from GameState and render."""
    if state is None or not state.players:
        return
    p = state.players[state.clock.active_player]
    if hasattr(p, "name"):
        name = p.name
    else:
        name = client_text("client.unnamed_player", resolver=resolver)
    render_status_bar(
        player_name=name,
        cash=p.ka,
        pos=p.po,
        ms=p.ms,
        out=out,
        resolver=resolver,
        colors=colors,
    )


def render_map_frame(map_lines: list[str], out: TextIO, colors: Colors) -> None:
    """40×25 map wrapped in a full-width light_blue background band."""
    width = _term_width()
    bg_code = colors.bg("light_blue")
    reset_bg = RESET_BG
    for line in map_lines:
        padded = line.ljust(width)
        out.write(f"{bg_code}{padded}{reset_bg}\n")


# ---------------------------------------------------------------------------
# Combat — renders a CombatScreen.to_json() payload
# ---------------------------------------------------------------------------

#: 40×13 combat grid geometry (CLAUDE.md: a DIFFERENT space from the 40x25 city map).
#: Kept local rather than imported from engine.combat: the renderer draws from the
#: JSON payload only (engine/ imports nothing from clients/, and the reverse holds
#: too -- the client must not import combat LOGIC, only these two int constants that
#: describe the wire shape it already renders positions against).
COMBAT_GRID_COLS = 40
COMBAT_GRID_ROWS = 13

#: Fighter sprite glyphs by side (1/2) and standing/down -- ASCII, unambiguously
#: narrow (no East-Asian-Width "W" glyphs, mirroring the city map's own constraint).
_FIGHTER_CHAR = {1: "1", 2: "2"}
_DOWN_CHAR = "x"
_ACTIVE_WALL_CODES = (160, 156)  # mirrors engine.combat.SHOT_WALL_CODES


def render_combat_grid(payload: dict, out: TextIO, colors: Colors) -> None:
    """Render one ``CombatScreen.to_json()`` payload's 40×13 grid.

    Draws walls from ``payload["grid"]`` (a linear 0..520 code array; missing/short
    entries read as open ground, mirroring ``engine.combat.can_move_onto``'s
    treatment of a short grid), a fighter glyph per standing combatant (side 1 vs
    side 2 colored per the palette's capability tiers), an ``x`` for a downed
    fighter, and highlights the active fighter's cell in reverse video. Exactly
    40 columns per row, ASCII-only glyphs (mirrors the city map's width contract,
    ``tests/test_terminal_integration.py::TestMapDisplayWidth``).

    The renderer reads ONLY the JSON payload's plain dicts/lists/ints -- no
    ``engine.combat``/``engine.state`` import -- so the client never depends on
    combat's internal representation, only its wire shape.
    """
    grid = payload.get("grid") or []
    sides = payload.get("sides") or [[], []]
    active_side = payload.get("active_side", 1)
    active_fighter = payload.get("active_fighter", 1)

    # cell -> (side, char) for every STANDING fighter; downed fighters still occupy
    # a cell in the payload but render as the down glyph, not a side glyph.
    occupied: dict[int, tuple[int, str, bool]] = {}
    for side_idx, side in enumerate(sides, start=1):
        for f_idx, fighter in enumerate(side, start=1):
            pos = fighter.get("position", 0)
            down = bool(fighter.get("down", False))
            is_active = side_idx == active_side and f_idx == active_fighter
            occupied[pos] = (side_idx, _DOWN_CHAR if down else _FIGHTER_CHAR[side_idx], is_active)

    side_colors = {1: "light_red", 2: "cyan"}

    for row in range(COMBAT_GRID_ROWS):
        chars: list[str] = []
        for col in range(COMBAT_GRID_COLS):
            cell = row * COMBAT_GRID_COLS + col
            if cell in occupied:
                side_idx, ch, is_active = occupied[cell]
                color = colors.fg(side_colors.get(side_idx, "white"))
                prefix = REVERSE if is_active else ""
                chars.append(f"{prefix}{color}{ch}{RESET_ALL}")
            elif cell < len(grid) and grid[cell] in _ACTIVE_WALL_CODES:
                chars.append(f"{colors.fg('dark_grey')}#{RESET_FG}")
            else:
                chars.append(" ")
        out.write("".join(chars) + "\n")


def render_fighter_panel(
    payload: dict,
    resolver: Any,
    weapon_names: list[str],
    out: TextIO,
    colors: Colors,
) -> None:
    """Render the active fighter's stat panel (``mf-prg.bas:30115-30116``): name,
    weapon, then one line per attribute -- all resolved through the theme's
    ``combat.panel_*`` keys (zero hardcoded display text, CLAUDE.md).

    **This renderer knows no game's attribute names.** It reads the
    wire payload's ``vitality`` SLOT and its opaque ``attrs`` map, and asks the theme
    what to show. The depleting resource (the ``vitality`` slot) renders first, via
    ``combat.panel_vitality`` -- the theme supplies this game's word for it (here
    "energie") as the label, exactly as the engine names the slot and the game names
    the word at every boundary. The remaining attributes come from
    ``combat.panel_attrs`` -- a list of ``attrs`` keys, each rendered through
    ``combat.panel_attr_<key>``, so the theme owns both the selection and the label. A
    game with ``aim``/``grit`` instead of ``kraft``/``brutalitaet`` needs no change here.

    Attributes the theme does not list are not displayed -- the panel is a curated
    view, not a dump. An unknown/unresolvable key is skipped rather than raising, so
    a theme that lists an attribute this fighter lacks degrades to a shorter panel
    instead of taking down the screen mid-fight. A theme with no ``panel_vitality``
    key simply omits that line.

    A theme that declares ``combat.panel_stats`` prints the stat block as one template
    instead, filled with the ``vitality`` slot and every ``attrs`` key, and the weapon
    after it: the classic theme's ``e05 k10 i07 b03`` line, the source's zero-padded
    ``ge$`` fields (``:1315``, built at ``:1365-1385``). Then ``panel_vitality`` and
    ``panel_attrs`` are not used.
    """
    fighter = payload.get("fighter")
    if fighter is None:
        return
    name = fighter.get("name", "")
    weapon_id = fighter.get("weapon", 0)
    weapon_name = weapon_names[weapon_id] if 0 <= weapon_id < len(weapon_names) else str(weapon_id)
    out.write(f"{colors.fg('light_grey')}{name}{RESET_FG}\n")
    attrs = fighter.get("attrs") or {}
    if _theme_has(resolver, "panel_stats"):
        # One theme template for the whole stat block, then the weapon: the order of
        # the source's gangster block (:1300-1320).
        stats = {**attrs, "vitality": fighter.get("vitality", 0)}
        try:
            out.write(resolver.resolve("combat.panel_stats", stats) + "\n")
        except KeyError:
            # A fighter that lacks a stat the template names (a CPU enemy carries no
            # intelligenz) shows no stat line, as the source's CPU side has no ge$.
            pass
        out.write(resolver.resolve("combat.panel_weapon", {"weapon": weapon_name}) + "\n")
        return
    out.write(resolver.resolve("combat.panel_weapon", {"weapon": weapon_name}) + "\n")

    # The depleting resource is the engine's ``vitality`` slot; the theme labels it in
    # this game's word. Rendered first, ahead of the opaque attrs.
    if "vitality" in fighter and _theme_has(resolver, "panel_vitality"):
        out.write(resolver.resolve("combat.panel_vitality", {"value": fighter["vitality"]}) + "\n")

    for key in _panel_attr_keys(resolver):
        if key not in attrs:
            continue
        out.write(resolver.resolve(f"combat.panel_attr_{key}", {"value": attrs[key]}) + "\n")


def _panel_attr_keys(resolver: Any) -> list:
    """The attribute keys the theme wants on the panel, in display order.

    Returns an empty list when the theme declares none -- a theme that has not opted
    in renders name + weapon only, rather than guessing at this game's stat names.

    Read off the resolver's ``tree`` rather than through ``resolve()``, because this
    key holds a LIST, and ``resolve()`` is a template-formatting call that rejects any
    non-string leaf by contract. A comma-separated string is also accepted, so a theme
    format that cannot express a list still works.
    """
    tree = getattr(resolver, "tree", None)
    declared = None
    if isinstance(tree, dict):
        declared = (tree.get("combat") or {}).get("panel_attrs")
    if declared is None:
        return []
    if isinstance(declared, str):
        return [k.strip() for k in declared.split(",") if k.strip()]
    return list(declared)


def _theme_has(resolver: Any, key: str) -> bool:
    """Whether the theme declares ``combat.<key>``.

    Read off the resolver's ``tree`` for the same reason as :func:`_panel_attr_keys`:
    it lets the panel omit an optional line (the vitality row) for a theme that has not
    opted into it, rather than asking ``resolve()`` for a key it would reject as missing.
    """
    tree = getattr(resolver, "tree", None)
    if isinstance(tree, dict):
        return key in (tree.get("combat") or {})
    return False


def render_combat_message(payload: dict, resolver: Any, out: TextIO, colors: Colors) -> None:
    """Render the screen's ``message`` field, if any.

    ``message`` is either a bare key string (``"illegal_move"``/``"unknown_action"``)
    set by the driver loop, or the ``CombatFight.shoot`` result dict (``hit``/
    ``damage``/``target_side``/``target_index``/``downed``) -- both shapes are
    JSON-safe per :class:`engine.interactions.CombatScreen`'s contract, so this just
    branches on which one arrived and resolves the matching theme key.
    """
    message = payload.get("message")
    if message is None:
        return
    if isinstance(message, str):
        text = resolver.resolve(f"combat.{message}")
    elif isinstance(message, dict):
        if not message.get("hit"):
            text = resolver.resolve("combat.miss")
        elif message.get("downed"):
            target_side = message.get("target_side")
            if target_side == 1:
                text = resolver.resolve(
                    "combat.hit_player_down",
                    {"index": (message.get("target_index") or 0) + 1},
                )
            else:
                text = resolver.resolve("combat.hit_enemy_down")
        else:
            # :30350 ``print"{rvon}treffer! energie -"mid$(str$(y),2)"!"``: the damage.
            text = resolver.resolve("combat.hit", {"damage": message.get("damage", 0)})
    else:
        return
    out.write(f"{colors.fg('yellow')}{text}{RESET_FG}\n")


def render_combat_losses(payload: dict, resolver: Any, out: TextIO) -> None:
    """Render the per-side losses line (``mf-prg.bas:30510-30515``)."""
    losses = payload.get("losses") or [0, 0]
    out.write(resolver.resolve("combat.losses_heading") + "\n")
    for i, count in enumerate(losses, start=1):
        out.write(
            resolver.resolve(
                "combat.losses_line",
                {
                    "name": client_text("client.side_name", {"index": i}, resolver=resolver),
                    "count": count,
                },
            )
            + "\n"
        )

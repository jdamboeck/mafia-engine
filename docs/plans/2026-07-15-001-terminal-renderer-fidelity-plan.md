# Terminal Renderer — Full Fidelity Pass (Refined)

**Date:** 2026-07-15 (refined)
**Scope:** Optimize `clients/terminal/` to match the original 1986 C64 Mafia game's visual
presentation as closely as terminal constraints allow. Covers color, layout, text formatting,
map rendering, per-context screen theming, ASCII art location entries, and a title screen.

**Depends on:** U10 (terminal client) must be un-deferred and its core
(`TerminalInput`, `render_result`, `render_map`, `map_repl`) functional.

**Design constraints:**
- No new dependencies. Raw ANSI escape codes + Unicode block/box-drawing only.
- Renderer is a thin client with zero game logic.
- Shared `Resolver` stays ANSI-unaware. All formatting is client-side.
- YAML strings stay pure text. Renderer applies formatting by **role** (header, body,
  menu option, status), not by inline markup.

---

## 1. Design decisions (confirmed)

| Decision | Choice |
|---|---|
| Background fill | Map content: 40×25 fixed. Additional UI (menus, status): full terminal width. |
| Player marker | Static colored `@` (no blink). |
| Location entry | Detailed Unicode art (20–25 lines each) for 4 wired locations + title = 5 pieces. |
| Format codes | **None in strings.** Renderer-side styling by role. Strings stay pure text. |
| Map background | Light grey (Pepto `#b2b2b2`) — matches original C64 `$D021=15` light grey bg. |
| Map buildings | Light red solid blocks (Pepto `#d97c66`) — matches original color RAM index 10, 43% of cells. |
| Map streets | Grey dots (Pepto `#878787`) — matches original color RAM index 12, 36% of cells. |
| Map vegetation | Green (Pepto `#69a941`) — matches original color RAM index 5, 7% of cells. |
| Status bar | Fixed bottom row (reverse-video), like original row 14. |
| Menu options | Colored indices (blue), themed German labels (from theme strings). |
| Screen clear | Clear on every major transition. |
| Menu language | Themed labels (German, matching original). |
| Map cells | Unicode block characters for doors/special; solid blocks for walls. |
| Color palette | Pepto palette (most accurate C64 palette). |
| Header style | Double-line box: `╔══ SCHLUPFWINKEL ══╗`, full-width, centered name. |
| Special cells | Unicode symbols (colored). |
| Turn-over | Full brown background screen. |
| ASCII art style | Detailed Unicode art (20–25 lines each). |
| Title screen | ASCII art "MAFIA" in large letters. |

---

## 2. Architecture

### 2a. Renderer-side styling by role (no inline format codes)

The renderer has **role-based formatting functions** that apply ANSI styling based on
what is being rendered. Strings stay pure text; the renderer decides presentation.

```python
# In clients/terminal/renderers.py

def render_header(text: str, out: TextIO) -> None:
    """Full-width double-line box header."""
    width = shutil.get_terminal_size().columns
    pad = max(0, width - len(text) - 6)  # ╔══ x ══╗ = 6 overhead
    left = pad // 2
    right = pad - left
    out.write(f"{BOLD_ON}{BG_BLACK}{FG_LIGHT_GREY}"
              f"╔{'═' * left}  {text}  {'═' * right}╗"
              f"{RESET_ALL}\n")

def render_body(text: str, out: TextIO) -> None:
    """Normal body text in light grey."""
    out.write(f"{FG_LIGHT_GREY}{text}{RESET_FG}\n")

def render_menu_option(index: int, label: str, out: TextIO) -> None:
    """Numbered option: blue index, light grey label."""
    out.write(f"  {FG_LIGHT_BLUE}{index}){RESET_FG} {FG_LIGHT_GREY}{label}{RESET_FG}\n")

def render_status_bar(state, out: TextIO) -> None:
    """Full-width reverse-video bar at bottom."""
    p = state.players[state.clock.active_player]
    bar = f" {p.name} │ cash {p.ka}$ │ pos {p.po} │ ms {p.ms} "
    width = shutil.get_terminal_size().columns
    padded = bar.center(width)
    out.write(f"{REVERSE}{BG_BROWN}{padded}{RESET_ALL}\n")

def render_map_frame(map_lines: list[str], out: TextIO) -> None:
    """40×25 map in full-width light_blue background band."""
    width = shutil.get_terminal_size().columns
    bg = f"\033[48;2;155;222;255m"  # light_blue Pepto RGB
    reset = "\033[49m"
    for line in map_lines:
        padded = line.ljust(width)
        out.write(f"{bg}{padded}{reset}\n")
```

This approach means:
- **Strings stay pure text** — portable to pygame, web, any renderer
- **Renderer owns all presentation** — different renderers style the same text differently
- **No format code parser needed** — simpler code, fewer bugs
- **Easy to adjust** — change one function to restyle all headers globally

**Inline color handling (edge cases):** Most messages are single-color, but a few need
mixed colors within one string (e.g., player name in red on the main menu). Strategy:
handlers yield **multiple `ShowMessage` interactions** — one per color region — rather than
embedding markup. The renderer applies color based on the string key pattern or the
handler's explicit structure. If a message truly needs inline mixed colors, the handler
yields a structured sequence:

```python
# Handler yields separate messages for each color region:
yield ShowMessage(key="menu.player_name", params={"name": name})  # renderer: red
yield ShowMessage(key="menu.separator")                            # renderer: dimmed
yield ShowMessage(key="menu.body", params={...})                   # renderer: light grey
```

The renderer maps key prefixes to colors:
```python
_COLOR_BY_PREFIX = {
    "menu.player_name": fg_ansi("red"),
    "menu.body": fg_ansi("light_grey"),
    "system.warning": fg_ansi("yellow"),
    # ... default: light_grey
}
```

### 2b. Color palette (Pepto)

File: `clients/terminal/palette.py`

```python
# Pepto-accurate C64 palette → 24-bit RGB
PEPTO = {
    "black":       (0, 0, 0),
    "white":       (255, 255, 255),
    "red":         (158, 52, 38),
    "cyan":        (100, 183, 199),
    "purple":      (138, 70, 172),
    "green":       (104, 169, 65),
    "blue":        (74, 54, 181),
    "yellow":      (208, 221, 94),
    "brown":       (144, 95, 37),
    "light_brown": (100, 87, 8),
    "light_red":   (217, 124, 102),
    "dark_grey":   (82, 82, 82),
    "grey":        (135, 135, 135),
    "light_green": (161, 214, 127),
    "light_blue":  (155, 222, 255),
    "light_grey":  (178, 178, 178),
}
```

ANSI generation:

```python
def fg_ansi(color_name: str) -> str:
    r, g, b = PEPTO[color_name]
    return f"\033[38;2;{r};{g};{b}m"

def bg_ansi(color_name: str) -> str:
    r, g, b = PEPTO[color_name]
    return f"\033[48;2;{r};{g};{b}m"
```

**Fallback** (T10): detect `$COLORTERM` for truecolor; if absent, map each Pepto color
to nearest 256-color index; if 256 unsupported, map to basic 8-color ANSI.

### 2c. Screen context system

File: `clients/terminal/__init__.py` (new `ScreenContext` class)

Tracks the current color scheme (bg, fg) and applies it on transitions. Context
switches happen at known points in `__main__.py`:

| Transition | From → To | BASIC Line |
|---|---|---|
| Game start → map | default → overworld | 2000 |
| Enter location | overworld → location_entry | 3005 |
| Leave location | location_entry → overworld | — |
| Turn start | overworld → turn_start | 4005 |
| Jail event | → jail | 1500 |
| Police encounter | → police | 6000 |
| Score display | → score | 4500 |

The context applies:
1. Background fill (full-width colored spaces or ANSI bg on all output)
2. Foreground text color
3. A brief "screen flash" effect (clear + set colors)

### 2d. File structure after implementation

Renderer config is **split** between the game theme (palette, contexts — theme-level data)
and the terminal client (layout — terminal-specific rendering choices).

```
clients/terminal/                   # terminal client package (all renderer code here)
├── __init__.py      # (modified) ScreenContext, cursor helpers, expanded constants
├── __main__.py      # (modified) context switching, ASCII art calls, SIGWINCH, new rendering
├── palette.py       # (new) Pepto palette + ANSI generators + 256/8-color fallback
├── renderers.py     # (new) role-based render functions (header, body, menu, status, map)
├── ascii_art.py     # (new) title art + 4 location entry art (hand-crafted string constants)
└── layout.yaml      # (new) terminal-specific: map cell chars, box-drawing, status bar format

data/game_configs/mafia_1920s/themes/classic/
├── strings/         # (existing, unchanged — pure text)
└── renderer/        # (new) theme-level renderer config — palette, contexts
    ├── palette.yaml    # C64 color values (Pepto) — game-specific, theme-swappable
    └── contexts.yaml   # per-screen-context color schemes — game-specific, theme-swappable
```

---

## 3. Tasks

### T1: Renderer config + palette module

**Goal:** Create `themes/classic/renderer/` with `palette.yaml` and `contexts.yaml`.
Create `clients/terminal/layout.yaml` with terminal-specific rendering config.
Implement `clients/terminal/palette.py` that loads the palette and provides
`fg(name)`, `bg(name)` ANSI generators.

**Files:**
- `data/game_configs/mafia_1920s/themes/classic/renderer/palette.yaml` (new)
- `data/game_configs/mafia_1920s/themes/classic/renderer/contexts.yaml` (new)
- `clients/terminal/layout.yaml` (new) — terminal-specific rendering config
- `clients/terminal/palette.py` (new)

**Note on config loading:** `palette.py` loads `palette.yaml` and `contexts.yaml` from
the game config's `themes/classic/renderer/` directory. This is theme-level data —
different themes can override the palette and context colors. `layout.yaml` is loaded
from `clients/terminal/` — it contains terminal-specific rendering choices (box-drawing
chars, map cell symbols) that would not apply to other client types.

**Config contents:**

`palette.yaml` — Pepto C64 palette as RGB triples. Used by `palette.py` at load time.

`contexts.yaml` — Per-screen color scheme (bg, fg) for each game context:
```yaml
overworld:   { bg: light_blue, fg: black }
turn_start:  { bg: brown, fg: light_grey }
location_entry: { bg: black, fg: light_grey }
jail:        { bg: blue, fg: light_grey }
police:      { bg: light_blue, fg: black }
score:       { bg: red, fg: light_grey }
jobs:        { bg: light_grey, fg: black }
credit:      { bg: dark_grey, fg: light_grey }
bank_robbery: { bg: brown, fg: light_grey }
```

`layout.yaml` — **Terminal-specific** (lives in `clients/terminal/`, not in the theme):
```yaml
map:
  cols: 40
  rows: 25
  border: true
  player_char: "@"
  player_color: red
  street_char: "·"
  street_color: dark_grey
  door_chars:
    slw: { char: "♠", color: white }
    pub: { char: "♦", color: light_green }
    sph: { char: "♣", color: yellow }
    waf: { char: "★", color: light_red }
  special_cells:
    569: { char: "⚓", color: blue }    # transport
    861: { char: "♛", color: red }      # mayor

status_bar:
  style: reverse
  bg: brown
  fg: light_grey
  width: full

header:
  style: double_line_box
  fg: light_grey
  bg: black
```

**Verification:** Unit tests for palette loading, `fg()`/`bg()` returning correct ANSI
strings, unknown color name raises `KeyError`.

---

### T2: Role-based renderer functions

**Goal:** Implement `renderers.py` with functions that style text by role. No inline
format codes — the renderer decides how each type of content looks.

**Functions:**
- `render_header(text, out)` — full-width `╔══ text ══╗` double-line box, black bg,
  light grey text. Name centered within box. Width = terminal width.
- `render_subheader(text, out)` — single-line separator: `──── text ────`
- `render_body(text, out)` — light grey text, one line
- `render_colored(text, color_name, out)` — text in a specific Pepto color
- `render_menu_option(index, label, out)` — blue index `1)`, light grey label
- `render_status_bar(state, out)` — full-width reverse-video brown bg bar at bottom
- `render_separator(out)` — horizontal line using `─` at terminal width
- `render_screen_clear(out)` — `\033[2J\033[H`
- `apply_context(ctx, out)` — set background/foreground for current screen context
- `resolve_color(key: str) -> str` — map a string key to a Pepto color name using
  `_COLOR_BY_PREFIX` lookup (for inline-color edge cases where handlers yield multiple
  ShowMessage interactions with different keys)

**Files:**
- `clients/terminal/renderers.py` (new)
- `tests/test_terminal_renderers.py` (new)

**Verification:** Unit tests for each function asserting ANSI output content. Test that
`render_header` centers the name correctly at various terminal widths.

---

### T3: Screen context system + cursor management + SIGWINCH

**Goal:** Implement `ScreenContext` class that tracks the active color scheme and
applies it to stdout. Also handle cursor visibility and terminal resize.

**ScreenContext API:**
```python
class ScreenContext:
    def __init__(self, config_dir, out, palette_module):
        self._contexts = load_contexts(config_dir)
        self._current = None

    def switch(self, context_name: str) -> None:
        """Switch to a named context, applying its colors."""
        ...

    def apply(self) -> None:
        """Re-apply the current context's colors to the output stream."""
        ...
```

**Cursor visibility:**
- On game start: `\033[?25l` (hide cursor)
- At input prompts: `\033[?25h` (show cursor)
- On exit / Ctrl-C / exception: `\033[?25h` (restore cursor) via `try/finally`
- Integrated into `TerminalInput.__call__()` — cursor shown before `input()`, hidden after

**SIGWINCH handling:**
- Register `signal.SIGWINCH` handler in `play()`
- Handler sets a flag; main loop checks flag and re-renders current screen
- Re-render triggers full screen clear + redraw with current context
- Ensures full-width elements (headers, status bar, blue band) stay correct on resize
- **Must also work in `map_repl()`** — the map loop in `__init__.py` has its own input
  loop; SIGWINCH needs to trigger a map redraw there too. The flag-based approach works
  for both loops since the flag is checked after each key read.

**Files:**
- `clients/terminal/__init__.py` (modified) — add `ScreenContext`, cursor helpers, update
  `__all__` with new exports (`ScreenContext`, `CURSOR_HIDE`, `CURSOR_SHOW`)
- `clients/terminal/__main__.py` (modified) — context switches, SIGWINCH handler

**Context switching points in `__main__.py`:**
```
play() start → switch("overworld")
enter location → switch("location_entry")
leave location → switch("overworld")
turn start screen → switch("turn_start")
turn end → switch("turn_start") for brown end screen
```

**Verification:** Test that `switch("jail")` writes the correct bg/fg ANSI sequences
to a mock stdout.

---

### T4: Map rendering overhaul

**Goal:** Replace raw ASCII `render_map()` with a styled version matching the original
C64 game's visual: light grey background, red building blocks, grey street dots.

**Source fidelity (from karte file analysis):**
- Background (`$D021`): light grey (`#b2b2b2`)
- Streets (code 156, color 12): grey dots (`#878787`) — 35.9% of cells
- Buildings (code 160, color 10): light red solid blocks (`#d97c66`) — 43.2% of cells
- Other walls: various textures in dark grey/brown — ~5% of cells
- Vegetation (color 5): green (`#69a941`) — 7% of cells
- Location markers: inverse-video letters baked into map data

**Map cell rendering (terminal adaptation):**
| Element | Character | Color | Notes |
|---|---|---|---|
| Player | `@` | red (Pepto) | Static, no blink |
| Streets (156) | `·` (middle dot) | grey `#878787` | Walkable paths |
| Buildings (160) | `█` (full block) | light_red `#d97c66` | Dominant visual mass |
| Other walls | `·` or ` ` | dark_grey | Texture variation |
| Vegetation | `♣` or `·` | green `#69a941` | Parks/areas |
| Schlupfwinkel door | `♠` | white | |
| Pub door | `♦` | light_green | |
| Spielhölle door | `♣` | yellow | |
| Waffen door | `★` | light_red | |
| Transport (569) | `⚓` | blue | Dynamic marker |
| Mayor (861) | `♛` | red | Dynamic marker |
| Map border | `╔═╗║╚═╝` | dark_grey | Box-drawing |

**Map rendering:**
1. Fill entire 40×25 area with light grey background
2. Draw `╔` + `═×40` + `╗` as top border (dark_grey)
3. Each row: `║` + 40 colored cells + `║`
4. Draw `╚` + `═×40` + `╝` as bottom border (dark_grey)
5. No full-width bg band — the map IS the background

**Cell rendering logic:**
- Code 156 (walkable): `·` in grey on light grey bg
- Code 160 (building): `█` in light_red on light grey bg
- Door cells: Unicode symbol in door-specific color on light grey bg
- Special cells (569/861): Unicode symbol in event color on light grey bg
- Other codes: `·` in dark_grey on light grey bg (texture variation)

**File:** `clients/terminal/__main__.py` (modified `render_map()`)

**Verification:** Snapshot test comparing rendered output at known terminal width.
Visual manual check against original C64 screenshot.

---

### T5: Status bar redesign

**Goal:** Replace dimmed `[cash X$ | pos Y | ms Z]` with a full-width reverse-video
bar fixed at the bottom of the screen.

**Implementation:**
1. After rendering the main content (map, menu), calculate remaining rows
2. Fill rows between content and bottom with blank lines
3. Render status bar as the last line:
   - Full terminal width
   - Brown background (Pepto brown RGB)
   - Light grey text
   - Reverse-video style
   - Format: `alcapone │ cash 5400$ │ pos 181 │ ms 19` (player name included)

**Note:** A true fixed-bottom-row requires cursor positioning (`\033[{row};1H`).
The simpler approach: render the status bar as the last line of output after all
content. The screen clear before each view ensures it appears at the bottom.

**Files:**
- `clients/terminal/renderers.py` (modified) — `render_status_bar()` update
- `clients/terminal/__init__.py` (modified) — `render_result()` update

**Verification:** Test that `render_status_bar()` outputs a full-width line with
brown bg ANSI codes.

---

### T6: Location menu redesign

**Goal:** Restyle `_run_location()` with double-line box headers, colored menu
options, and full context switching.

**Layout:**
```
[clear screen]
[set location_entry context: black bg, light grey text]
╔═══════════════════════════════════════════╗
║              SCHLUPFWINKEL                ║    ← render_header()
╚═══════════════════════════════════════════╝
                                            ← blank line
'gut. pro monat kostet das 500$ miete.'    ← render_body()
                                            ← blank line
  1) mieten                                 ← render_menu_option(blue index, grey label)
  2) austreten
                                            ← blank line
> _                                         ← prompt
[...padding to bottom...]
[alcapone │ cash 5400$ │ pos 181 │ ms 19]  ← render_status_bar() fixed bottom
```

**Files:**
- `clients/terminal/__main__.py` (modified) — `_run_location()` rewrite
- `clients/terminal/renderers.py` (new) — extracted menu rendering helpers

**Verification:** Snapshot test of rendered menu output. Visual manual check.

---

### T7: Turn-over screen

**Goal:** When `ms` hits 0, show a full brown-background turn-end screen matching
the original's turn-start style (BASIC line 4005: brown/brown colors).

**Layout:**
```
[clear screen]
[set turn_start context: brown bg, light grey text]
[fill screen with brown background]
[center vertically + horizontally:]
╔════════════════════════════════╗
║      RUNDE VORBEI              ║
║   Keine Bewegungspunkte mehr.  ║
╚════════════════════════════════╝
[wait for keypress]
```

**Files:**
- `clients/terminal/__main__.py` (modified) — turn-end rendering
- `clients/terminal/renderers.py` (new) — `render_turn_end()`

**Verification:** Visual check. Test that turn-end writes clear + brown context +
correct text to stdout.

---

### T8: Title screen

**Goal:** Show an ASCII art title screen when the game starts, before the main loop.

**Design:** "MAFIA" in large ASCII art letters (block style, 5-7 lines tall) on a
black background. Below: subtitle "— 1926 —" and "Drücke ENTER zum Starten."
Centered, white text on black.

**Layout:**
```
[clear screen]
[black background, full width]
[centered vertically:]

    ██╗  ██╗ █████╗ ██████╗ ██████╗ ██╗   ██╗
    ██║  ██║██╔══██╗██╔══██╗██╔══██╗╚██╗ ██╔╝
    ███████║███████║██████╔╝██████╔╝ ╚████╔╝
    ██╔══██║██╔══██║██╔═══╝ ██╔═══╝   ╚██╔╝
    ██║  ██║██║  ██║██║     ██║        ██║
    ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝        ╚═╝
                    — 1926 —

        Drücke ENTER zum Starten.
```

**Files:**
- `clients/terminal/ascii_art.py` (new) — title art constant + render function
  (hand-crafted string constant, lives in the terminal client package)
  - `TITLE_ART: list[str]` — the 6-line block letter art as a list of strings
  - `render_title(out: TextIO) -> None` — centers art at terminal width, applies
    white-on-black Pepto colors, adds subtitle and "press ENTER" prompt
- `clients/terminal/__main__.py` (modified) — show title before `play()` loop

**Verification:** Visual check. Test that title screen writes the art to stdout.

---

### T9: Location entry ASCII art

**Goal:** Detailed Unicode art (20–25 lines each) for 4 wired locations + title
(covered in T8) = 4 location art pieces.

**Locations and themes:**
| Location | German | Art concept |
|---|---|---|
| Schlupfwinkel | Hideout | Door in a dark alley, shadowy figure |
| Pub | Bar | Bar counter with bottles, stools |
| Spielhölle | Casino | Roulette wheel, playing cards |
| Waffen | Weapons | Gun rack, ammunition |

Each piece uses:
- Unicode block characters (█, ▄, ▀, ╭, ╮, ╰, ╯)
- Box-drawing for architectural elements
- Colored with Pepto palette (fg only, black bg)
- 20-25 lines tall, centered in terminal width

**Files:**
- `clients/terminal/ascii_art.py` (modified) — 4 location art constants
  (hand-crafted string constants, lives in the terminal client package)
  - `LOCATION_ART: dict[str, list[str]]` — keyed by location id (slw, pub, sph, waf)
  - `render_location_art(location_key: str, out: TextIO) -> None` — centers the
    20-25 line art at terminal width, applies location-specific Pepto colors
- `clients/terminal/__main__.py` (modified) — show art on location entry

**Art rendering flow:**
```
[clear screen]
[set location_entry context]
[render ASCII art, centered, colored]
[press ENTER to continue]
[clear screen]
[render location menu]
```

**Verification:** Visual check of all 4 pieces. Test that art renders to stdout.

---

### T10: 256-color + 8-color fallback

**Goal:** Detect terminal capabilities and degrade gracefully.

**Detection:**
1. Check `$COLORTERM` for `truecolor` or `24bit` → use 24-bit RGB
2. Check `$TERM` for `256color` → use 256-color palette (nearest neighbor mapping)
3. Otherwise → use basic 8-color ANSI (`\033[31m`–`\033[37m`)

**Fallback palette mapping:**
- Pepto RGB → nearest 256-color index (precomputed lookup table)
- 256-color → basic 8-color (map ranges)

**Files:**
- `clients/terminal/palette.py` (modified) — fallback mapping + detection

**Verification:** Unit tests for fallback path. Manual test with `$COLORTERM` unset.

---

### T11: Theme string pure-text update

**Goal:** Ensure all theme strings are pure text (no existing format codes) and
cover the text the renderer needs. Verify the current strings work with the
renderer-by-role approach. If any strings are missing (e.g., turn-end message,
title screen subtitle), add them.

**Files:**
- `data/game_configs/mafia_1920s/themes/classic/strings/` — verify + add if needed
- Potentially add `ui.yaml` for system-wide UI strings:
  ```yaml
  ui:
    turn_end_title: "RUNDE VORBEI"
    turn_end_body: "Keine Bewegungspunkte mehr."
    press_enter: "Drücke ENTER zum Starten."
    turn_start_title: "NEUE RUNDE"
    location_loading: "Betrete {location}..."
  ```

**Verification:** Resolver resolves all keys. No test breakage.

---

### T12: Test suite update + integration

**Goal:** Comprehensive test coverage for the new renderer.

**New test files:**
- `tests/test_terminal_palette.py` — palette loading, fg/bg generation, fallback
- `tests/test_terminal_renderers.py` — all role-based render functions
- `tests/test_terminal_context.py` — context switching
- `tests/test_terminal_ascii_art.py` — art rendering

**Updated test files:**
- `tests/test_terminal_client.py` — update assertions for new ANSI output

**Snapshot tests:**
- `tests/snapshots/map_rendered.txt` — expected map output
- `tests/snapshots/title_screen.txt` — expected title art
- `tests/snapshots/location_menu.txt` — expected menu layout
- `tests/snapshots/status_bar.txt` — expected status bar

**Verification:** `make check` passes. All tests green.

---

## 4. Execution order

```
T1 (palette + config) ─────────────────────────┐
T2 (renderer functions) ────────────────────────┤
                                                ├─→ T6 (location menus)
T3 (context system) ────────────────────────────┤
                                                ├─→ T7 (turn-over screen)
T4 (map overhaul) ──────────────────────────────┤
                                                └─→ T8 (title screen)
T5 (status bar) ────────────────────────────────┘
                                                │
T9 (ASCII art) ─────────────── parallel with T4–T8 ────┐
T10 (256-color fallback) ────── parallel with T4–T8 ───┤
T11 (theme strings) ─────────── parallel with T4–T8 ───┤
                                                        │
T12 (tests + integration) ──────────────────────────────┘
```

**Dependency graph:**
- T1 → T3, T5, T2 (palette needed by context, status bar, and renderers)
- T2 → T4, T6, T7, T8 (renderer functions used by all screen renderers)
- T3 → T4, T6, T7, T8 (context system + cursor + SIGWINCH used by all screens)
- T4, T5, T6, T7, T8, T9, T10, T11 → T12 (all feed into final integration)
- T9, T10, T11 can run in parallel with T4–T8
- T12 is last

---

## 5. Constraints preserved

- **No new dependencies.** Only stdlib (`sys`, `os`, `shutil`, `yaml`) + raw ANSI
  escapes + Unicode characters.
- **No game logic in client.** Renderer reads `EngineResult`; never computes outcomes.
- **Resolver stays headless.** `engine/strings.py` does not know about ANSI, format
  codes, or rendering roles.
- **Strings stay pure text.** Portable to pygame, web, any future renderer.
- **Testable via stdout capture.** All rendering through `TextIO` injection.
- **Theme-swappable.** `themes/classic/renderer/` configs can be overridden by a
  different theme. Different theme = different palette, different context colors.
- **Graceful degradation.** 24-bit → 256-color → 8-color fallback chain.

---

## 6. Resolved questions

| # | Question | Resolution |
|---|---|---|
| 1 | ASCII art authoring | **Hand-crafted** as string constants in `clients/terminal/ascii_art.py`. All renderer files live under `clients/terminal/`, not in `data/`. |
| 2 | Cursor visibility | **Hide during play.** `\033[?25l` on game start, `\033[?25h` on exit and in `try/finally` for Ctrl-C safety. Show cursor only at input prompts. |
| 3 | Terminal resize | **Handle SIGWINCH.** Re-render the current screen on resize signal. Adds signal handling complexity but keeps full-width elements correct. |
| 4 | Player name in status bar | **Show player name.** Status bar format: ` alcapone │ cash 5400$ │ pos 181 │ ms 19 `. Future-proof for multi-player. |

---

## 7. Review findings (2026-07-15)

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R1 | **Inline color gap.** Renderer-by-role doesn't handle mixed-color text within a single string (e.g., player name in red on main menu). | Medium | Handlers yield **multiple `ShowMessage` interactions** — one per color region. Renderer maps key prefixes to colors via `_COLOR_BY_PREFIX` lookup. Added `render_colored()` and `resolve_color()` to T2. |
| R2 | **SIGWINCH in `map_repl()`.** The map loop in `__init__.py` has its own input loop; SIGWINCH must trigger map redraw there too. | Low | Flag-based approach works for both loops. Documented in T3. |
| R3 | **ASCII art rendering needs `out` parameter and centering.** Art constants are string lists; `render_art()` must center at terminal width. | Low | Added `render_title(out)` and `render_location_art(key, out)` with explicit centering logic to T8/T9. |
| R4 | **Missing `__all__` update.** New exports (ScreenContext, cursor helpers) need to be in `__all__`. | Low | Added to T3 files section. |
| R5 | **Palette config loading is client-side.** `palette.py` loads YAML from `data/` directly — not through the engine. This is correct (each client type loads its own renderer config) but should be documented. | Low | Added note to T1 files section. |

**Overall assessment:** The plan is consistent, feasible, and respects all architecture
constraints. The inline color gap (R1) was the only substantive issue — the multi-message
strategy resolves it without breaking the "no format codes in strings" principle. All other
findings are minor documentation/cleanup items already addressed above.

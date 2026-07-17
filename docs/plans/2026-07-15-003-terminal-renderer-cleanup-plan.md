# Terminal Renderer — Code Quality & Architecture Cleanup

**Date:** 2026-07-15
**Scope:** Fix all issues found in the high-level review of the terminal renderer layer.
**Depends on:** All prior opencode commits (T1–T12 + map fixes + location art wiring).

---

## Issues Found

| # | Category | Severity | Summary |
|---|----------|----------|---------|
| 1 | Usability | 🔴 | Cursor hide/show has no try/finally — SIGINT leaves cursor invisible |
| 2 | Architecture | 🔴 | ANSI constants defined in 3 places (palette, init, renderers) |
| 3 | Correctness | 🔴 | `fg()`/`bg()` crash KeyError on unknown color name |
| 4 | Dead code | 🔴 | SIGWINCH handler wired but never activated in `play()` |
| 5 | Dead code | 🔴 | `map_repl` exported+tested but `play()` doesn't use it |
| 6 | Performance | 🟡 | `ScreenContext.apply()` re-reads palette YAML on every call |
| 7 | Correctness | 🟡 | `ScreenContext.apply()` ignores theme — hardcoded `_DEFAULT_CONFIG_DIR` |
| 8 | Testability | 🟡 | `_run_location` bypasses `TerminalInput` for raw stdin reads |
| 9 | Structure | 🟡 | `render_map` lives in `__main__.py` instead of `renderers.py` |
| 10 | Consistency | 🟡 | Unused layout.yaml config keys (status_bar, header, map chars) |
| 11 | Testability | 🟡 | `renderers.py` module-level mutable `_PAL` global |
| 12 | Test gaps | 🟡 | Confirm/cancel tokens mostly untested |
| 13 | Test gaps | 🟡 | `inspect.getsource` test is brittle |
| 14 | Dead code | 🟡 | `NO_REVERSE`, `BOLD_ON`, `BOLD_OFF` unused constants |
| 15 | Performance | 🟡 | `term_color_support()` re-reads env vars on every `fg()`/`bg()` call |

---

## Tasks

### T1: Cursor try/finally (🔴 usability bug)

**Problem:** Every `show_cursor` → `readline` → `hide_cursor` pair in `__main__.py` has no exception safety. SIGINT or EOFError leaves the cursor invisible after process exit.

**4 affected sites:**
| Site | File:Line | Context |
|------|-----------|---------|
| A | `__main__.py:240-242` | Location art splash |
| B | `__main__.py:256-258` | Menu option input |
| C | `__main__.py:297-299` | Title screen |
| D | `__main__.py:349-351` | Turn-over screen |

**Fix for each:**
```python
show_cursor(out)
try:
    raw = sys.stdin.readline().strip()
finally:
    hide_cursor(out)
```

**Also:** Add a top-level try/finally in `play()` that always calls `show_cursor(out)` on exit.

**Test:** Verify cursor is shown in finally block (mock readline to raise EOFError, assert show_cursor was called).

---

### T2: Consolidate ANSI constants (🔴 architecture)

**Problem:** 5 constants duplicated between `palette.py` and `__init__.py`. `renderers.py` hardcodes `\033[2J\033[H` inline. `__init__.py:150` hardcodes `\033[39m\033[49m` inline.

**Plan:** `palette.py` owns color-related ANSI constants. `__init__.py` owns cursor/screen control.

| Step | Action |
|------|--------|
| 1 | Delete from `palette.py`: `CLEAR`, `CURSOR_HIDE`, `CURSOR_SHOW` — cursor/screen, not color. |
| 2 | `__init__.py`: delete 5 redefined constants. Import `RESET`, `DIM` from `palette.py`. Define `CLEAR`, `CURSOR_HIDE`, `CURSOR_SHOW` locally. |
| 3 | `renderers.py:87`: replace inline `"\033[2J\033[H"` with `CLEAR` from `clients.terminal`. |
| 4 | `__init__.py:150`: replace inline `"\033[39m\033[49m"` with `RESET_FG + RESET_BG` from `palette.py`. |

**Final ownership:**
- `palette.py` → `RESET_ALL`, `RESET_FG`, `RESET_BG`, `REVERSE`, `DIM`, `RESET` (alias)
- `__init__.py` → `CLEAR`, `CURSOR_HIDE`, `CURSOR_SHOW`, `hide_cursor`, `show_cursor`

---

### T3: Fix fg()/bg() KeyError + cache term_color_support (🔴 + 🟡)

**Fix `fg()`/`bg()`:** Use `.get()` with fallback:
```python
rgb = palette.get(color_name, _PEPTO_FALLBACK.get(color_name, (128, 128, 128)))
```

**Cache `term_color_support()`:**
```python
_COLOR_SUPPORT: ColorSupport | None = None
def term_color_support() -> ColorSupport:
    global _COLOR_SUPPORT
    if _COLOR_SUPPORT is None:
        _COLOR_SUPPORT = _detect_color_support()
    return _COLOR_SUPPORT
```

---

### T4: Wire up SIGWINCH (🔴 dead code → active)

**Fix in `play()`:**
1. Call `install_sigwinch_handler()` at start.
2. In the main map loop, after each key read, call `check_resize()`. If True, re-render.

---

### T5: Cache palette in ScreenContext (🟡 performance + correctness)

**Fix:**
1. Accept `palette` dict in constructor (or load once from `config_dir`).
2. Store `self._palette` at construction time.
3. `apply()` uses `self._palette`.

---

### T6: Delete dead code (🟡)

**From `palette.py`:** Delete `NO_REVERSE`, `BOLD_ON`, `BOLD_OFF` — zero usage.

**From `layout.yaml`:** Delete `map.street_char/color`, `wall_char/color`, `other_wall_char/color`, `veg_char/color`, `status_bar` section, `header` section — no code reads these.

---

### T7: Fill test gaps (🟡)

- Confirm negative + multi-token tests
- Cancel token tests
- Replace `inspect.getsource` test with behavioral test
- `render_status_bar_from_state` test

---

## Execution Order

```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → make check
```

## Files Modified

| File | Tasks |
|------|-------|
| `clients/terminal/__init__.py` | T1, T2 |
| `clients/terminal/__main__.py` | T1, T2, T4 |
| `clients/terminal/palette.py` | T2, T3, T6 |
| `clients/terminal/renderers.py` | T2 |
| `clients/terminal/layout.yaml` | T6 |
| `tests/test_terminal_client.py` | T7 |
| `tests/test_terminal_integration.py` | T1, T7 |
| `tests/test_terminal_context.py` | T4 |
| `tests/test_terminal_renderers.py` | T7 |

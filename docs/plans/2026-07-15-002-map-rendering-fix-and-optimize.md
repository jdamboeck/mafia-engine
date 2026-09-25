# Map Rendering Fix & Optimize Plan

**Date:** 2026-07-15
**Scope:** Fix the 1-tile border dent + full screen-code-to-Unicode mapping for visual fidelity.
**Depends on:** Current terminal renderer (T1–T12 all landed).

---

## Analysis Summary

### Karte Decode: ✅ Correct
The binary decode (`tools/decode_city_map.py`) is byte-identical to the research tool
(`render_c64_assets.py:113-119`). Reversal, row-major layout, color RAM masking — all
match. No changes needed.

### The Dent: ⚓ is East Asian Width = Wide
Cell 569 (cash transport special) renders as ⚓ (U+2693). Python's `unicodedata.east_asian_width()`
returns `'W'` — **always 2 terminal columns**. This makes row 15 visually 43 columns
while the border is 42, creating a 1-tile protrusion ("dent").

**Affected lines with potentially wide/ambiguous chars:**
| Line | Cell | Char | EAW | Actual cols |
|------|------|------|-----|-------------|
| 1 | 23 (waf door) | ★ | A | 1 or 2* |
| 4 | 82 (sgl door) | ♠ | A | 1 or 2* |
| 5 | 163,281,333 (sph doors) | ♣ | A | 1 or 2* |
| 5 | 201 (sgl door) | ♠ | A | 1 or 2* |
| 10 | 101 (waf door) | ★ | A | 1 or 2* |
| 11 | 66 (sgl door) | ♠ | A | 1 or 2* |
| 15 | **569 (transport)** | **⚓** | **W** | **always 2** |
| 17 | 269 (sgl door) | ♠ | A | 1 or 2* |
| 19 | 63 (sph door) | ♣ | A | 1 or 2* |
| 20 | 387 (waf door) | ★ | A | 1 or 2* |
| 21 | 122 (sgl door) | ♠ | A | 1 or 2* |
| 22 | 861 (mayor) | ♛ | N | 1 (safe) |

*A = Ambiguous: renders as 1 col in Western terminals, 2 cols in CJK locales.

### Screen Code Mapping: 33 of 35 codes collapsed to `·`
Current logic (lines 138–145 of `__main__.py`):
- Code 156 → `·` (street) — 359 cells
- Code 160 → `█` (building) — 432 cells
- **Everything else** → `·` — 109 cells of lost detail

The C64 rendered 35 distinct screen codes via the custom `mf-zeichen` charset. The
terminal currently shows only 2 visual types. Key losses:
- Code 32 (92 cells): empty space rendered as `·` instead of blank
- Code 163 (28 cells): park/vegetation cross-hatch rendered as `·`
- Code 224 (19 cells): building block rendered as `·` (looks like street)
- Water/rail/building-detail codes (25 cells): all collapsed

---

## Tasks

### T1: Fix ⚓ wide-char dent (layout.yaml)

**Goal:** Replace ⚓ with a guaranteed narrow character.

**File:** `clients/terminal/layout.yaml` line 34

**Change:**
```yaml
# Before
569: { char: "\u2693", color: blue }   # ⚓ transport

# After
569: { char: "\u25c6", color: blue }   # ◆ transport (narrow)
```

**Also fix ambiguous-width door chars** to prevent CJK-locale breakage:
```yaml
# Before                          # After
slw: { char: "\u2660", ... }  →  slw: { char: "\u25c0", ... }  # ◀ (narrow)
pub: { char: "\u2666", ... }  →  pub: { char: "\u25b6", ... }  # ▶ (narrow)
sph: { char: "\u2663", ... }  →  sph: { char: "\u2630", ... }  # ☰ (narrow)
waf: { char: "\u2605", ... }  →  waf: { char: "\u2691", ... }  # ⚑ (narrow)
```

All replacements are East Asian Width = `N` (narrow) or `Na` (narrow, ASCII-range).

**Test:** Re-render map, verify all lines have exactly 42 visible columns in any locale.

---

### T2: Full screen-code → Unicode character mapping (__main__.py)

**Goal:** Replace the 3-branch if/elif/else with a lookup table mapping all 23 non-door,
non-special screen codes to distinct Unicode characters.

**File:** `clients/terminal/__main__.py` lines 134–146

**Mapping table** (all chars verified East Asian Width ≠ W):
```python
# Screen code → (char, description)
# Color comes from C64 color RAM already — this is shape only.
_CODE_TO_CHAR: dict[int, str] = {
    # === MAJOR TERRAIN ===
    160: "\u2588",  # █  full block       — building (432 cells)
    224: "\u2588",  # █  full block       — building secondary (19 cells)
    156: "\u2591",  # ░  light shade      — street textured (359 cells)
     32: " ",       #    space            — open/background (92 cells)
    163: "\u2592",  # ▒  medium shade     — park/vegetation (28 cells)

    # === WATER (10 cells) ===
    229: "\u2590",  # ▐  right half block
    244: "\u2590",  # ▐  right half block
    234: "\u258c",  # ▌  left half block
    247: "\u2584",  # ▄  lower half block
    248: "\u2583",  # ▃  lower 3/8 block
    249: "\u2585",  # ▅  upper 3/8 block

    # === RAIL TRACKS (10 cells) ===
    101: "\u2502",  # │  box vertical
    106: "\u258e",  # ▎  left 1/4 block
    118: "\u258e",  # ▎  left 1/4 block
    124: "\u2598",  # ▘  quadrant upper left

    # === BUILDING DETAILS (5 cells) ===
    192: "\u2550",  # ═  double horizontal
    237: "\u2514",  # └  corner
    238: "\u2510",  # ┐  corner
    240: "\u2554",  # ╔  double corner
    253: "\u2518",  # ┘  corner

    # === DIAGONAL TRANSITIONS (4 cells) ===
    205: "\u2571",  # ╱  diagonal
    206: "\u2572",  # ╲  diagonal
    208: "\u2590",  # ▐  right half block
}
```

**Implementation:**
1. Add `_CODE_TO_CHAR` as a module-level constant in `__main__.py`.
2. Replace lines 138–145 with: `char = _CODE_TO_CHAR.get(code, "\u00b7")`
3. The fallback `·` stays for any unknown code.

**Test:** Assert the table has exactly 23 entries. Assert all values are ≤ 1 display
column (using `unicodedata.east_asian_width` check in test).

---

### T3: Update layout.yaml metadata

**Goal:** Update `layout.yaml` comments and `street_char`/`wall_char` to match the new
mapping (currently says `·` for streets and `█` for walls; after T2, streets are `░`).

**File:** `clients/terminal/layout.yaml`

**Changes:**
- `street_char`: `"\u00b7"` → `"\u2591"` (░ light shade)
- `other_wall_char`: `"\u00b7"` → `"\u2591"` (░ fallback)
- `veg_char`: `"\u2663"` (♣) → `"\u2592"` (▒ medium shade)
- Update comments to reflect actual render mapping

---

### T4: Add display-width guard test

**Goal:** Prevent future wide-char regressions. A test that re-renders the map and
asserts every content line has exactly `cols + 2` visible columns (40 + 2 border = 42).

**File:** `tests/test_terminal_integration.py`

**Test:**
```python
def test_map_lines_all_same_visible_width():
    """Every map line must have exactly 42 visible columns (no wide chars)."""
    import unicodedata, re
    # render map with test state
    # strip ANSI
    # assert len(visible) == 42 for every content line
```

---

### T5: Verify — full test suite green

**Goal:** Run `make check` (pytest + lint). Fix any regressions.

**Expected:** 406+ tests green (406 existing + 1 new from T4 + updated integration tests).

---

## Execution Order

```
T1 (fix ⚓ + door chars) → T2 (screen code mapping) → T3 (layout.yaml update) → T4 (guard test) → T5 (green tree)
```

## Files Modified

| File | Tasks |
|------|-------|
| `clients/terminal/layout.yaml` | T1, T3 |
| `clients/terminal/__main__.py` | T2 |
| `tests/test_terminal_integration.py` | T4 |

## Verification

1. `make check` — all tests green
2. Visual: render map in terminal, confirm no line protrudes past border
3. Visual: 35 distinct screen codes now visible (not just 2)
4. Visual: parks (▒ green), water (block chars), rail (│) all distinguishable from streets (░) and buildings (█)

---
title: "Next Slice — Brainstorm Basis (pointer)"
type: brainstorm-input
date: 2026-09-27
topic: next-slice
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
status: input-for-brainstorm
---

# Next Slice — Brainstorm Basis (pointer)

> **This is a brainstorm INPUT, not a plan.** It exists so that the newest file in `docs/plans/` is a brainstorm-basis again after the C64 numbers and recruit cap plan (`2026-09-27-001-fix-c64-numbers-and-recruit-cap-plan.md`) landed. The main content is unchanged: see **`2026-09-25-002-next-slice-brainstorm-basis.md`**, then the additions in **`2026-09-26-002-next-slice-brainstorm-basis.md`** and **`2026-09-26-004-next-slice-brainstorm-basis.md`**.

## Resolved by the C64 numbers and recruit cap plan (drop from the earlier lists)

- **#98:** numbers print the way the C64 prints them. `engine/c64_numbers.py` is checked against a VICE capture (`tests/fixtures/c64_str/`), a theme picks the style with `_format.numbers`, and classic uses `c64`. The rank screen's `mid$(str$(gf(sp)),2)` (`:4215`) drops a negative score's minus sign.
- **#105:** the recruit loop checks the 10-gangster cap after the offer, confirm and cash check, as `:12145` does.

## Added by the C64 numbers and recruit cap plan

- **`:1013` rounding differs from the C64.** The VICE capture shows that `int(25.4*100)/100` is `25.39` on the C64 but `25.4` as a double. The port's per-turn score truncation therefore rounds some scores differently from the original. Fixing this would mean emulating 40-bit arithmetic, not changing how numbers print. The same capture shows that fractional score sums leave different leftovers on the C64 than in Python, in both directions (see the `c64_str` docstring).
- **The C64 `PRINT` spacing is not ported.** `PRINT` adds a sign space before a number and a cursor-right after it. The templates keep their own hand-written spacing, so a negative number in a template that has a literal space before it prints ` -500` where the C64 prints `-500`.
- **The combat panel's values** (`combat.panel_*`) print as plain numbers. The source's panel (`:1315`) prints zero-padded `ge$` fields built at `:1365-1385`, and the port's label/value panel is a different layout.

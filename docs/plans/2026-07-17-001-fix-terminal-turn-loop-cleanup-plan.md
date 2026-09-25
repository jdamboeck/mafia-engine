---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
type: fix
date: 2026-07-17
title: Terminal Turn-Loop Cleanup — Two Review Findings
---

# Terminal Turn-Loop Cleanup — Two Review Findings

## Summary

Fix two verified code-review findings in `clients/terminal/__main__.py` (the runnable
terminal client entry point, `python -m clients.terminal`), both introduced when the
`[opencode]` renderer-fidelity pass turned the original single-turn loop into an unbounded
multi-turn loop:

1. **Unreachable dead code** after the `while True:` turn loop — a `show_cursor` +
   `sys.stdin.readline()` + `finally` block that can never run.
2. **No way to quit from the turn-over screen** — the only game exit is `q` on the map;
   at turn-over the keypress is read and discarded, so the player is forced into the next
   turn indefinitely.

Both live entirely inside one function (`play()`); neither touches the engine, the driver,
or any config. A related non-TTY crash in `_read_key` was already fixed (commit `61254cd`)
and is **out of scope here**.

## Problem Frame

`play()` (`clients/terminal/__main__.py`) runs the interactive loop:

- `while True:` (line ~351) renders the map, reads a key via `_read_key()`, moves, and —
  on `turn_over` — renders a turn-over screen, waits for a keypress, then calls
  `advance_turn(...)` and loops back for another turn.
- The loop's **only** exits are two `return` statements on the map key (`q` / empty →
  `return` at line ~360). Nothing after `while True:` is reachable.

This produces two defects:

- **Dead code (lines ~402-406):** a `show_cursor(out)` + `try: sys.stdin.readline() finally:
  hide_cursor(out)` block sits *after* the infinite loop, inside the outer `try`. It is
  unreachable. The outer `finally: show_cursor(out)` (line ~408) already restores the
  cursor on every exit, so the block is also redundant.
- **No turn-over quit (lines ~381-401):** the `if payload.turn_over:` branch renders the
  turn-over summary, calls `_read_key()` (line ~400) to "press any key", **discards the
  result**, and unconditionally calls `advance_turn(...)`. A player who presses `q` at
  turn-over does not quit — they advance into the next turn. There is no exit from the
  game except returning to the map and pressing `q` there.

This is a focused follow-up: the existing cleanup plan
(`docs/plans/2026-07-15-003-terminal-renderer-cleanup-plan.md`) covers 15 other renderer
issues but **not** these two.

## Requirements

- **R1** — Remove the unreachable, redundant block after the `while True:` loop in
  `play()` without changing cursor-restoration behavior (the outer `finally` must still
  run on every exit path).
- **R2** — At the turn-over screen, honor `q` (and EOF) as a game-quit: the loop returns
  cleanly instead of advancing to the next turn. Any other key advances the turn as today.
- **R3** — The client stays scriptable via piped stdin (the non-TTY fallback in
  `_read_key` must keep working end-to-end after both changes).

## Key Technical Decisions

**KTD-1 — Fix in place; no restructuring of the loop.** Both defects are localized to the
`turn_over` branch and the post-loop tail of `play()`. Delete the dead block (R1) and add a
quit check on the turn-over keypress (R2). Do not refactor `play()` into `map_repl` or
otherwise reshape the loop — that is a separate concern owned by the other cleanup plan.

**KTD-2 — Reuse the existing quit vocabulary.** The map loop already treats `q` / empty
(EOF) as quit (`_read_key()` returns `"q"` on EOF via the non-TTY fallback). The turn-over
check mirrors that exact vocabulary so quit behaves identically on both screens — no new
key semantics.

## Implementation Units

### U1. Remove unreachable post-loop block in `play()`

**Goal.** Delete the dead, redundant `show_cursor` + `readline` + `finally` block that sits
after the `while True:` loop in `play()`, leaving cursor restoration to the existing outer
`finally`.
**Requirements.** R1.
**Dependencies.** none.
**Files.** `clients/terminal/__main__.py`.
**Approach.** Remove the block after the loop (currently ~lines 402-406:
`show_cursor(out)` + `try: sys.stdin.readline() finally: hide_cursor(out)`). The enclosing
`try/finally` whose `finally: show_cursor(out)` (line ~408) already guarantees the cursor
is restored on every exit, so no replacement is needed. Confirm the outer `try/finally`
structure remains syntactically intact after the deletion.
**Patterns to follow.** The outer `try: … finally: show_cursor(out)` wrapper already in
`play()` is the single cursor-restoration owner — mirror the intent (one owner, on every
exit), don't reintroduce a second.
**Test scenarios.**
- `Test expectation: none — pure dead-code deletion, no behavioral change.` The deleted
  block is provably unreachable (the only loop exits are `return`s before it), so no test
  can exercise it. Coverage that the *reachable* behavior is unchanged comes from U2's and
  the existing suite's smoke runs (cursor still restored on quit).
**Verification.** `make check` stays green; a piped `q` run still exits cleanly with the
cursor shown (no `\033[?25l` left dangling in the output tail).

### U2. Honor quit at the turn-over screen

**Goal.** Make `q` / EOF at the turn-over "press any key" prompt exit the game cleanly
instead of advancing into the next turn.
**Requirements.** R2, R3.
**Dependencies.** none. (Independent of U1; may land in either order or together.)
**Files.** `clients/terminal/__main__.py`, `tests/test_terminal_integration.py`.
**Approach.** In the `if payload.turn_over:` branch of `play()` (~lines 381-401), capture
the return value of the `_read_key()` call that currently discards it (line ~400). If it is
a quit key (`q` or empty/EOF — the same set the map loop treats as quit), `return` from
`play()` instead of falling through to `advance_turn(...)`. Any other key advances the turn
exactly as today. Reuse the map loop's quit condition so the two screens share one
vocabulary (KTD-2); factor the quit test to a small local predicate if that reads cleaner,
but a shared inline `in ("q", "")` check is acceptable.

**Testability seam (resolve during implementation).** `play()` reads `sys.stdin` /
`sys.stdout` directly and only reaches turn-over after `ms` is walked to 0, and
`tests/test_terminal_integration.py` currently has **no harness that drives `play()` or the
turn loop** (it unit-tests individual render functions). Do not assume a full-`play()` walk
test exists. Land the quit-vs-advance decision on a **testable seam** instead — the
lightest option is a tiny module-level predicate (e.g. `_is_quit(key) -> bool`) that both
the map loop and the turn-over branch call, tested directly against `"q"` / `""` / other;
pair it with a turn-over-branch test that monkeypatches `sys.stdin` (a `StringIO`) and
asserts the advance-vs-return decision without a full map walk. Prefer the predicate seam
over string-source assertions like the existing `inspect.getsource` check (brittle).
**Technical design (directional, not literal).**
```
key = _read_key()            # was: _read_key() with result discarded
if key in ("q", ""):         # same quit vocabulary as the map loop
    return                   # clean exit; outer finally restores the cursor
advance_turn(state, vehicles)
```
**Patterns to follow.** The map loop's own quit branch (`if key in ("q", ""): … return`)
in the same function — match its key set and its clean-return shape.
**Test scenarios.** (Against the testability seam above — a `_is_quit` predicate and/or a
monkeypatched-`sys.stdin` turn-over-branch test — not a full `play()` walk.)
- Quit vocabulary: the shared quit predicate returns True for `"q"` and `""` (EOF) and
  False for any other key (e.g. `" "`, `"x"`) — proving the turn-over screen and the map
  loop share one quit set (KTD-2).
- Turn-over quit: with the turn-over branch exercised under a monkeypatched `sys.stdin`
  supplying `q`, assert the decision is to exit (return, `advance_turn` NOT called) — no
  second turn begins.
- Turn-over continue: same seam supplying a non-quit key, assert the decision is to advance
  (`advance_turn` called, next turn begins).
- Edge — EOF at turn-over: `sys.stdin` exhausted so `_read_key()` returns `""`; assert exit,
  not loop (guards R3 — EOF must terminate, not spin).
**Execution note.** Prefer writing the quit-at-turn-over test first (it currently cannot
pass — the key is discarded) so the fix is proven against a red test; the piped-stdin
integration harness in `tests/test_terminal_integration.py` is the right home.
**Verification.** New turn-over quit/continue/EOF tests pass; `make check` green; a manual
piped run that reaches turn-over and sends `q` exits without starting a new turn.

## Verification Contract

- **Test command:** `make check` (pytest + lint) from the repo root — must stay green.
- **Gates that prove the plan:**
  - U1: the reachable behavior is unchanged and the cursor is restored on quit (no dangling
    hide-cursor escape); `make check` green.
  - U2: turn-over honors `q`/EOF as a clean exit and any other key advances the turn, proven
    by the three scripted-stdin scenarios above.
  - R3: an end-to-end piped-stdin run (the method used to verify the client throughout this
    work) still walks, enters a location, reaches turn-over, and quits — no `termios` crash,
    no infinite loop.
- **Behavioral scope:** presentation/entry-point only. No engine, driver, effect, or config
  behavior changes; the headless layering test (`tests/test_slice_integration.py`) must
  remain green and unaffected.

## Definition of Done

- R1: the unreachable post-loop block is gone; `play()`'s outer `try/finally` still restores
  the cursor on every exit.
- R2: `q`/EOF at the turn-over screen exits the game; any other key advances the turn.
- R3: the client remains scriptable via piped stdin end-to-end.
- `make check` is green (pytest + lint); the new turn-over tests are included.
- No changes outside `clients/terminal/__main__.py` and `tests/test_terminal_integration.py`.

## Scope Boundaries

**In scope:** the two findings above, in `play()`.

**Out of scope (non-goals):**
- The non-TTY `_read_key` crash — **already fixed** in commit `61254cd`.
- The 15 issues in `docs/plans/2026-07-15-003-terminal-renderer-cleanup-plan.md` (ANSI
  constant consolidation, `map_repl` dead-code, palette caching, `ScreenContext` theme bug,
  test gaps, etc.) — that plan owns them; several are already implemented and would need a
  drift check before execution, but none are this plan's concern.
- Reshaping `play()` to use the exported `map_repl`, or any broader loop refactor.

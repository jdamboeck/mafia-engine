---
title: "Driving the terminal play() loop in tests: the title screen eats one stdin line"
date: 2026-07-17
category: developer-experience
module: clients/terminal
problem_type: developer_experience
component: testing_framework
severity: medium
applies_when:
  - "Writing a test that drives clients/terminal/__main__.py play() over piped/scripted stdin"
  - "A scripted-stdin terminal test 'reaches the wrong screen' or ends one key too early/late"
  - "Deciding how to make a monolithic stdin/stdout REPL testable without refactoring the loop"
  - "Adding a quit/branch decision inside play() that must be asserted from a test"
tags:
  - terminal-client
  - piped-stdin
  - test-harness
  - play-loop
  - monkeypatch
  - eof
---

# Driving the terminal play() loop in tests: the title screen eats one stdin line

## Context

`clients/terminal/__main__.py::play()` is a monolithic stdin/stdout REPL: title
screen → `while True:` map loop → (on door entry) location sub-loops → turn-over
prompt → `advance_turn`. It reads `sys.stdin` directly and only reaches later
screens after real state is walked (e.g. movement points to 0). The client is
deliberately scriptable via **piped stdin** (one key per line — the `_read_key`
non-TTY fallback), so tests drive it by monkeypatching `sys.stdin` with a
`StringIO`.

Two things about this loop silently break a naive scripted-input test, and both
cost real debugging time when writing `TestTurnOverQuit` for the turn-loop
cleanup (issues #26/#27; landed on `feat/vertical-slice` as commit `03dbe12`,
unmerged as of this writing — the SHA may be rewritten on merge).

## Guidance

**1. The title screen consumes one stdin line before the map loop.** `play()`
prints the title and calls `sys.stdin.readline()` (`clients/terminal/__main__.py:355`)
to wait for "press a key", *before* the `while True:` map loop
(`clients/terminal/__main__.py:360`). A scripted stdin whose first line is the
first movement key is therefore off by one — the title read eats it, every
subsequent key shifts, and the walk lands on the wrong screen. **Prepend one
blank (title-dismiss) line to every scripted stdin body:**

```python
def _script(keys):
    # leading "" dismisses the title screen (play() reads one line there
    # before the map loop), then one key per line.
    return io.StringIO("\n".join([""] + keys) + "\n")
```

**2. To reach a deep screen, don't hardcode a key sequence — walk the engine.**
Turn-over only fires once `ms` hits 0 (25 deterministic steps at seed 42, but
that count is map-geometry-dependent and must not be a literal in the test).
Build the walk by asking the engine which direction *steps* from the current
state each turn, so the test survives map edits:

```python
# pick a direction that produces a step (not wall/oob/enter) right now
for key, delta in _MOVE_KEYS.items():
    if getattr(try_move(state, city, delta).payload, "kind", None) == "step":
        return key
```

Use only `kind == "step"` moves so no move is spent *entering* a location
mid-walk (which would consume stdin in a sub-loop and desync the script again).

**3. Make the in-loop decision observable via a module-level seam.** To assert
"quit exits vs. advances the turn" without a full refactor, the decision must
route through something patchable. Two moves made it testable: a module-level
`_is_quit(key)` predicate (unit-testable directly against `"q"`/`""`/other), and
**hoisting `advance_turn` from a `play()`-local import to a module-level import**
so a test can `monkeypatch.setattr(tmain, "advance_turn", spy)`. A name imported
*inside* the function is not a module attribute and cannot be patched from the
outside.

**4. EOF is a quit on both stdin paths.** `_read_key()` returns `"q"` on
piped-stdin EOF (`clients/terminal/__main__.py:79-80`), and a real-TTY read
returns `""`. A shared quit predicate must accept both (`key in ("q", "")`) so an
exhausted script terminates the loop instead of spinning — assert this with a
script that stops *before* the screen's key so stdin runs out there.

## Why This Matters

The title-screen swallow is invisible: the test doesn't error, it just drives the
loop to a different screen and asserts against the wrong state — a green test that
proves nothing, or a confusing failure "one key off". Anchoring the walk to the
engine (not a literal step count) is what keeps these tests from breaking every
time the city map changes. And the patchability seams (`_is_quit` +
module-level `advance_turn`) are the difference between "assert a branch decision
in three lines" and "refactor the whole REPL to test it."

## When to Apply

- Any new test that drives `play()` (or a similar direct-`sys.stdin` REPL) via `StringIO`.
- When a scripted-stdin test reaches an unexpected screen or ends one key early/late — check for a pre-loop `readline()` that eats the first line.
- Before hardcoding a movement sequence to reach a deep screen — walk the engine instead.
- When you need to assert a decision made *inside* the loop — add a module-level predicate and ensure the collaborator it calls is a patchable module-level name.

## Examples

Reaching turn-over and asserting quit-vs-advance (shape from `tests/test_terminal_integration.py::TestTurnOverQuit`):

```python
keys = self._walk_to_turn_over() + ["q"]        # walk, then the key under test
calls = []
monkeypatch.setattr(tmain, "advance_turn", lambda *a, **k: calls.append(a))
monkeypatch.setattr(sys, "stdin", self._script(keys))   # NOTE: _script prepends ""
monkeypatch.setattr(sys, "stdout", io.StringIO())
tmain.play(seed=42)
assert calls == []            # q at turn-over exited; no next turn began
```

EOF variant: supply the walk with **no** turn-over key; stdin exhausts at the
prompt, `_read_key()` returns `"q"`, and the loop exits (guards against an
infinite loop on EOF).

## Related
- `docs/plans/2026-07-17-001-fix-terminal-turn-loop-cleanup-plan.md` — the plan whose tests surfaced this.
- Issues #26 (U1 dead-block removal) and #27 (U2 turn-over quit) — landed as `03dbe12`, unmerged as of this writing.

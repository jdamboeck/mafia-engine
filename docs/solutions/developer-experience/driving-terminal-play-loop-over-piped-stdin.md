---
title: "Driving the terminal play() loop in tests: the pre-map acks eat stdin lines"
date: 2026-07-17
updated: 2026-09-28
category: developer-experience
module: clients/terminal
problem_type: developer_experience
component: testing_framework
severity: medium
applies_when:
  - "Writing a test that drives clients.terminal play() over piped/scripted stdin"
  - "A scripted-stdin terminal test 'reaches the wrong screen' or ends one key too early/late"
  - "Asserting a decision made inside play() (quit vs. advance, save, skip upkeep) from a test"
  - "Tempted to monkeypatch a clients.terminal name or private function to observe the loop"
tags:
  - terminal-client
  - piped-stdin
  - test-harness
  - play-loop
  - monkeypatch
  - eof
---

# Driving the terminal play() loop in tests: the pre-map acks eat stdin lines

## Context

`clients/terminal/session.py::play()` (a thin wrapper over `TerminalSession`) is a stdin/stdout REPL: title
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

**1. Lines are read before the map loop.** A new game's `play()` reads one line
for the title screen's "press a key", one for the house-rules offer (Enter keeps
every rule faithful; shown whenever the config's catalogue has a switch), one for
the first turn-start upkeep screen's ack and one for the turn menu's choice (`2`
walks), *before* the map-move prompt (`TerminalSession.map_prompt`) reads its first
key. The setup prompts between the title and the house-rules offer read one line
each too, unless `end_year`/`score_weight` are passed to `play()`, and every later
turn change adds one more upkeep ack and one more turn-menu key. A scripted stdin
whose first line is the first movement key is therefore off — the acks eat it,
every later key shifts, and the walk lands on the wrong screen. **Prepend the blank
ack lines to every scripted stdin body** (`tests/helpers.py::NEW_GAME_ACKS` holds
the three acks, and `tests/helpers.py::make_walk_script` prepends them and the walk):

```python
NEW_GAME_ACKS = ["", "", ""]  # title, house-rules offer, first upkeep


def make_walk_script(keys):
    # the new game's acks, the turn menu's walk, then one key per line.
    return io.StringIO("\n".join([*NEW_GAME_ACKS, MENU_WALK_KEY] + keys) + "\n")
```

**2. To reach a deep screen, don't hardcode a key sequence — walk the engine.**
Turn-over only fires once `ms` runs out (the original's move loop ends at
`:2005` `ifms<=0thenreturn`; tested by `test_ms_zero_ends_turn`). At seed 42 that
takes 25 deterministic steps, but the count is map-geometry-dependent and must not
be a literal in the test.
Build the walk by asking the engine which direction *steps* from the current
state each turn, so the test survives map edits:

```python
# the test's own key table (the map's hint names W/A/S/D), not the client's;
# pick a direction that produces a step (not wall/oob/enter) right now
MOVE_KEYS = {"w": UP, "s": DOWN, "a": LEFT, "d": RIGHT}
for key, delta in MOVE_KEYS.items():
    if getattr(try_move(state, city, delta).payload, "kind", None) == "step":
        return key
```

Use only `kind == "step"` moves so no move is spent *entering* a location
mid-walk (which would consume stdin in a sub-loop and desync the script again).

**3. Observe the in-loop decision through what `play()` shows, returns and
writes — never by patching the client.** Tests drive `play()`/`main()` (imported
from the package `clients.terminal`) through standard input and assert on
standard output, the returned `(state, rng)` and the files written. They do not
monkeypatch a `clients.terminal` name or call a private function: that pins
where a function lives and what it calls, so a refactor that keeps the behavior
breaks the test (the engine quality gates plan, R11). A call count becomes what
the call makes visible (KTD-4):

- "`advance_turn` was (not) called" becomes the returned clock and the screen
  only an advance opens (the round's standings);
- "`run_upkeep` was not called" becomes "no upkeep banner in the output, and the
  returned state equals the saved one";
- a `save_game` spy becomes reading the save file after each `p`
  (`engine.persistence.load_game`);
- a `play` spy behind `main()` becomes an end-to-end `main([...])` run whose
  output and save file are asserted.

A fault that must happen *inside* the loop enters through standard input too
(KTD-3): a `StringIO` subclass whose `readline` raises `KeyboardInterrupt` or
`RuntimeError` at the read under test, or does something the outside world
would do there (send the process a real `SIGWINCH` before answering, for a
terminal resize). The input stream comes from outside the program, so the
client needs no seam for it.

**4. EOF is a quit on both stdin paths.** `_read_key()` returns `"q"` on
piped-stdin EOF (`clients/terminal/session.py::_read_key`), and a real-TTY read
returns `""`. A shared quit predicate must accept both (`key in ("q", "")`) so an
exhausted script terminates the loop instead of spinning — assert this with a
script that stops *before* the screen's key so stdin runs out there.

## Why This Matters

The title-screen swallow is invisible: the test doesn't error, it just drives the
loop to a different screen and asserts against the wrong state — a green test that
proves nothing, or a confusing failure "one key off". Anchoring the walk to the
engine (not a literal step count) is what keeps these tests from breaking every
time the city map changes. And asserting on screens, the returned state and save
files keeps the tests about behavior: the client was split into modules (session,
command line, entry point) with no test edits, which a test patching
`advance_turn` in one module could not have survived.

## When to Apply

- Any new test that drives `play()` (or a similar direct-`sys.stdin` REPL) via `StringIO`.
- When a scripted-stdin test reaches an unexpected screen or ends one key early/late — check for a pre-loop `readline()` that eats the first line.
- Before hardcoding a movement sequence to reach a deep screen — walk the engine instead.
- When you need to assert a decision made *inside* the loop — find what the decision prints, returns or writes and assert that; if it is not observable at all, question whether it is behavior.

## Examples

Reaching turn-over and asserting quit-vs-advance (shape from `tests/test_terminal_integration.py::TestTurnOverQuit`):

```python
keys = self._walk_to_turn_over() + ["q"]        # walk, then the key under test
out = io.StringIO()
monkeypatch.setattr(sys, "stdin", make_walk_script(keys))   # prepends the acks and the walk
monkeypatch.setattr(sys, "stdout", out)
state, _rng = play(seed=42, end_year=1930, score_weight=1.0)
output = out.getvalue()
assert "turn_over" in output, "the walk never reached the turn-over screen"
assert (state.clock.year, state.clock.month) == (1925, 0)   # no next turn began
assert "spielstand" not in output                           # no round standings
```

The advancing key is the contrast: `["x"]` instead of `["q"]` returns a clock
of 1925-1 and prints the standings after the turn-over screen
(`test_other_key_at_turn_over_advances`).

EOF variant: supply the walk with **no** turn-over key; stdin exhausts at the
prompt, `_read_key()` returns `"q"`, and the loop exits (guards against an
infinite loop on EOF).

## Related
- `docs/plans/2026-07-17-001-fix-terminal-turn-loop-cleanup-plan.md` — the plan whose tests surfaced this.
- Issues #26 (U1 dead-block removal) and #27 (U2 turn-over quit) — landed as `03dbe12`, unmerged as of this writing.

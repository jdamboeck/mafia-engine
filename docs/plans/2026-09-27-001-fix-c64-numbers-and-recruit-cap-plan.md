---
title: C64 Number Printing and Recruit Cap Order - Plan
type: fix
date: 2026-09-27
topic: c64-numbers-and-recruit-cap
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# C64 Number Printing and Recruit Cap Order - Plan

## Goal Capsule

- **Objective:** Close the two open issues. #98: every number a theme shows prints the way the C64 prints it. #105: the pub recruit loop checks the 10-gangster cap where `:12145` checks it.
- **Product authority:** this contract for scope. `mf-prg.bas` (via `mafia-oracle`, `conclude`) for every game value or order a test asserts. The C64 BASIC V2 ROM, run in VICE (`x64sc`), for how numbers print. `docs/AGENTS.md` for process rules.
- **Execution profile:** `docs/AGENTS.md`. One orchestrator and one subagent per unit, run serially. At most two subagents run in parallel, and only for review work. The orchestrator owns commits and the authoritative `make check`. Units run in U-ID order, except that U4 (no dependencies) may run before U1–U3.
- **Stop conditions:**
  - VICE is unavailable, or its `STR$` output disagrees with the rules table. Record it and do not guess.
  - A template whose source line cannot be found.
  - A red tree the unit cannot fix within its scope.
  - Any push. Wait for the user.

---

## Product Contract

### Summary

Numbers in theme text render through a number style that the theme picks. The classic theme picks `c64`. That style prints `22` for `22.0`, `.5` for `0.5`, nine significant digits, and exponent form outside the C64's fixed-point range.

Templates whose source line prints `mid$(str$(n),2)` use a sign-stripped form. That reproduces the rank screen's lost minus sign. The recruit loop moves its cap check after the offer, the confirm and the cash check, as the source does.

### Problem Frame

**#98.** The resolver fills templates with Python's `str.format`. Integral floats print `22.0`, fractions print `0.5`, and the standings row uses `{score:g}`, which gives six significant digits.

The rank-promotion screen (`:4215`) prints `mid$(str$(gf(sp)),2)`. For a positive score that strips `str$`'s leading space. For a negative score it strips the minus sign.

**#105.** The source checks the cap at `:12145`, after the offer, the confirm and the cash check. The port checks it at the top of the next loop pass and ends the batch silently. When the gang fills mid-batch, the screens and the RNG draws differ from the source.

### Findings Confirmed During Planning

- **The minus quirk holds in the source.** `str$(-3.5)` is `"-3.5"`, and `mid$(…,2)` is `"3.5"`, so the screen shows `3.5 p.`. The issue's example (`gf=-0.9` shows `0.9 p.`) is off by one character: the C64 prints `.9 p.`, because `str$` gives `-.9` with no leading zero.
- **The quirk is reachable.** A `gosub 1160` in the turn (`:1165`) sets `nr(sp)` to something other than `ra(sp)`. Later in the same turn, the weapon buy at `:13073` (`gf=gf+x8*2*(gf>0)`, with no clamp) takes `gf` below 0. For example, `gf=12.5`, `x8=2`, and four side-grades give -3.5.
  - The next `:1011` → `:4030` then shows the rank screen before `:1013` rounds the score or any `gosub 1160` clamps it.
  - The port matches this. `ScoreChange(clamp=False)` does not touch `nr` (see `engine/effects.py`), and upkeep compares `rank` with `nr` at its start.
- **What `:12145` does.** It `goto`s `12005`, which dispatches back to `12100`. The rank and housing guards (`:12100`–`:12104`) pass again because nothing in the batch changed them. `:12105` then prints `maximal 10 gangster!` and goes to `1100` (press-a-key, then return).
  - A "no" at `:12136` goes to `12175 nexti`, and the batch continues with the next candidate.
  - Not enough cash at `:12140` gives `gosub1125:goto12175`, which also continues.

### Requirements

**Number printing (#98)**

- R1. A theme selects a number style. `plain` keeps today's `str.format` output. `c64` renders int and float parameters as C64 `str$` digits.
- R2. Under `c64`, a non-negative number prints without `str$`'s leading space and a negative number prints with `-`. The spacing around a number stays whatever the template holds.
- R3. A template can ask for the `mid$(str$(n),2)` form, which drops `str$`'s first character (the sign position) for any sign.
- R4. The classic theme selects `c64`. Every template whose source line uses `mid$(str$(…),2)` asks for the sign-stripped form, including the rank screen (`:4215`). The standings row no longer uses `:g`.
- R5. Width and alignment specs in templates (`{name:<16}`, `{cash:>6}`) still apply, to the styled text.

**Recruit cap (#105)**

- R6. Once the roster reaches 10 mid-batch, the next candidate is still drawn, introduced, offered and confirmed. A "yes" with enough cash shows `recruit_gang_full` and ends the handler. A "no", or not enough cash, continues the batch as for any other candidate.
- R7. The cap check before the batch (`:12105`) stays as it is.

### Acceptance Examples

- AE1. Classic theme, rank screen, `gf = -3.5` → the score line reads `3.5 p.`. With `gf = 22.0` → `22 p.`. With `gf = 0.5` → `.5 p.`.
- AE2. Classic theme, standings row, score `22.0` → `22`. Score `-0.9` → `-.9`.
- AE3. Roster at 9, a batch of 2, and the player says yes to both with enough cash. The first hire fills the roster. The second candidate is drawn, introduced and offered, then `maximal 10 gangster!` shows, and there are no more effects.
- AE4. Same as AE3, but the player says no to the second candidate. There is no gang-full message, and the batch goes on to a third candidate if one was rolled.

### Scope Boundaries

- The spacing around numbers stays as the templates have it. The C64 `PRINT` also writes a sign space before and a cursor-right after each number; matching that would mean rewriting every template, so it is out of scope.
- Next-slice work is not in this plan: `:4050`/`:4055`–`:4056`, the jail skip, and the 7 unbuilt locations.
- C64 40-bit float arithmetic is out of scope. Only formatting is ported; the arithmetic stays in Python doubles.

---

## Planning Contract

### Key Technical Decisions

- KTD-1. **The number style is theme data, and the resolver applies it.** Formatting belongs to presentation, and the resolver is the one place every client resolves text. The style is a reserved key in the theme's merged string tree, so the deep-merge a runtime override theme already gets can switch it. It needs no new loading path. Default `plain` keeps a theme without the key unchanged.
- KTD-2. **The C64 formatter is a pure function returning the full `str$` text, sign position included.** Both the plain `c64` form (R2) and the stripped form (R3) derive from it. The stripped form is then literally `mid$(…,2)`, which makes the quirk fall out of the formula rather than being special-cased.
- KTD-3. **Only int and float parameters are styled.** Strings, names and bools pass through. A `bool` is excluded even though it is an `int` subclass.
- KTD-4. **Explicit format specs stay available.** The styled string then goes through the spec, so `{cash:>6}` aligns the C64 text. The stripped form is a named spec on the field. Its exact name is chosen at implementation.
- KTD-5. **The recruit fix moves the cap check. It does not duplicate the guards.** The source re-enters at `:12005` and reruns rank and housing, but inside one handler run those cannot change: effects are buffered, and the loop touches neither. The port shows the gang-full message directly, and a comment names the re-entry.

### C64 `str$` Rules to Implement

The formatter follows C64 BASIC V2's float-to-text rules. The research corpus does not document them, so U1 captures `PRINT STR$(x)` for every row in VICE, using the research repo's `tools/vice_runtime_verify.py` stack, and the captured text is the test oracle. The table below is the expected result, not the authority.

| Input | `str$` | Rule |
|---|---|---|
| `22` / `22.0` | ` 22` | sign position, no trailing `.0` |
| `-0.9` | `-.9` | no leading zero before the point |
| `0.5` | ` .5` | same |
| `100.25` | ` 100.25` | trailing zeros dropped |
| `1/3` | ` .333333333` | 9 significant digits, rounded |
| `2/3` | ` .666666667` | same |
| `0.01` | ` .01` | smallest fixed-point magnitude |
| `0.001` | ` 1E-03` | below 0.01 → exponent form |
| `123456789` | ` 123456789` | largest 9-digit integer stays fixed |
| `1e9` | ` 1E+09` | 1e9 and above → exponent form |
| `0` | ` 0` | zero |

### Sequencing

U1 (formatter) → U2 (resolver style) → U3 (classic theme and template audit) → U4 (recruit cap) → U5 (close-out). U4 is independent of U1–U3 and may run before them.

### Risks

- **Double versus 40-bit float.** The rank screen (`:4215`, run from `:1011`) and the standings (`:4510`, from `:1010`'s `gosub 4500`) show `gf` before `:1013` rounds it. `gf` builds up in steps of `x*x8`, where `x8` is any value from 0.1 to 2, so a double can carry a leftover such as `0.1+0.2-0.3 = 5.55e-17` that prints in exponent form, while the C64's 40-bit result differs. Cash is an integer and is not affected. U1 measures one such sequence in VICE, and the outcome decides between treating near-zero values as 0 and accepting the difference as documented.
- **Tests that pin today's output.** Existing tests that assert `22.0`, `0.5`, or the silent batch end will change. Each change is legitimate only when the new value is the source's value.

---

## Implementation Units

### U1. C64 `str$` formatter

**Goal:** A pure function that turns an int or float into the C64 `str$` text.

**Requirements:** R1, R2, R3 (the basis for both)

**Dependencies:** none

**Files:**
- `engine/c64_numbers.py` (new; exact module placement at implementation)
- `tests/test_c64_numbers.py` (new)

**Approach:**
- Build the formatter from the rules table: sign position, 9 significant digits, no leading zero, trailing zeros dropped, and exponent form below 0.01 or at 1e9 and above.
- Exponent form has a two-digit exponent with a sign, and a mantissa that follows the same digit rules.
- The engine layering rule holds: no imports from `clients/`.

**Execution note:** Capture the oracle first. Run `PRINT STR$(x)` in VICE for every table row and edge case below, and commit the captured strings as the test's expected values, noting in the test module that they come from VICE. Then implement test-first against them.

**Test scenarios:**
- Happy path: every row of the rules table, in both signs where the sign matters.
- Edge cases:
  - `-0.0` prints ` 0`.
  - A 10-digit integer switches to exponent form.
  - `0.0099` goes to exponent form.
  - `999999999.6` rounds into exponent form.
- Error path: a non-number argument raises `TypeError`, not a garbled string.
- Accumulation: run one fractional-weight sequence in VICE (e.g. `x8=0.1`, a score of +3 then -3 steps as `:1160` applies them) and print the result. If the C64 lands on 0 where the double leaves a leftover, the formatter treats near-zero values as 0. Otherwise record the difference, with the worked example, as accepted in the formatter's docstring. A test pins whichever outcome VICE shows.

**Verification:** The table passes. Each case fails when its rule is broken; break-to-prove, with `PYTHONDONTWRITEBYTECODE=1`.

### U2. Theme-selected number style in the resolver

**Goal:** `Resolver.resolve` renders numeric parameters through the theme's number style.

**Requirements:** R1, R2, R3, R5

**Dependencies:** U1

**Files:**
- `engine/strings.py`
- `tests/test_strings.py`

**Approach:**
- Replace the bare `str.format` with a formatter that styles int and float fields per KTD-1 to KTD-4.
- The style comes from the reserved tree key. The `plain` default applies only when the style is read; it is never written into `Resolver.tree`. Otherwise a `--theme` override without the key (`clients/terminal/cli.py` merges the override's whole tree over classic) would switch classic back to `plain`.
- An unknown style raises `ValueError` naming it, on every construction path: `from_directory`, `with_override`, and direct `Resolver(tree=...)`.
- The reserved key must not collide with template lookup: resolving it as a template key is an error.

**Patterns to follow:** the existing `ValueError`-naming-the-key style in `_deep_merge`, and `Resolver.with_override`.

**Test scenarios:**
- Happy path:
  - Under `c64`, `{n}` with `22.0` gives `22`, with `-0.9` gives `-.9`, and with `3000` gives `3000`.
  - Under `plain`, the same three give `22.0`, `-0.9` and `3000`.
- Happy path: the stripped spec on `-3.5` gives `3.5`, and on `22.0` gives `22`.
- Edge cases:
  - `{cash:>6}` under `c64` right-aligns `3000` to width 6.
  - A `bool` or `str` parameter passes through unchanged.
- Integration: a runtime override that sets the style to `plain` over a `c64` base switches it (`with_override`).
- Integration: classic overridden by a theme tree without the style key (e.g. `tests/fixtures/themes/test`) still renders `c64`.
- Error path: an unknown style name raises `ValueError` naming it.

**Verification:** New tests pass and each fails with its branch broken. The whole existing suite is green under `plain`, before the classic theme opts in.

### U3. Classic theme opts in; template audit

**Goal:** The classic theme prints numbers as the C64 does, including the rank screen's lost minus.

**Requirements:** R4, AE1, AE2

**Dependencies:** U2

**Files:**
- `data/game_configs/mafia_1920s/themes/classic/strings/` (a style key; `upkeep.yaml`, `game_end.yaml`, and any template the audit touches)
- `tests/` (existing client and theme tests whose expected text changes; a rank-screen test driving `play()`)

**Approach:**
- Set the classic style to `c64`.
- Audit every classic template with a numeric parameter against its source line. Each `mid$(str$(…),2)` site uses the stripped spec. That includes `:4215` rank score, `:4501` standings year and month (`game_end.standings_header`), and `:13015` weapon numbers, if they are templated.
- Replace `{score:g}` with the plain field.
- Client-owned templates in `client.yaml` (status bar, summary) have no source line. They take the theme style unchanged.
- Update each existing test whose expected number text changes, and confirm the new value against the source.

**Test scenarios:**
- Covers AE1. The rank screen shown by `play()` for a player with `rank != nr`. Route: save a two-player state with `engine.persistence.save_game` where player 1 has the given `gf` and `nr != rank`, load it with `play(load=...)`, and end player 0's turn so player 1's upkeep shows the rank screen. `TerminalSession.next_turn()` is not used.
  - `gf=-3.5` shows `3.5 p.`
  - `gf=22.0` shows `22 p.`
  - `gf=0.5` shows `.5 p.`
- Covers AE2. The standings row (`:4510`, shown at every year end and at game end) shows `22` and `-.9`.
- Integration: a price template (`recruit_offer`) shows `3000$`, not `3000.0$`, when the price is a float.

**Verification:**
- The citation checker and the quote checks are green on every edited YAML comment.
- The audit list (template key → source line → form) is in the commit message.
- The rank-screen test fails when the stripped spec is swapped back to the plain field.

### U4. Recruit cap checked at `:12145`

**Goal:** The pub recruit loop checks the cap after the offer, the confirm and the cash check.

**Requirements:** R6, R7, AE3, AE4

**Dependencies:** none

**Files:**
- `data/game_configs/mafia_1920s/handlers/pub.py`
- `tests/test_pub_recruit.py`
- `tests/test_ports.py` (a port test for the `:12136`–`:12145` order, if the port inventory tracks it)

**Approach:**
- Remove the top-of-pass cap check.
- After the afford check succeeds, check the running roster size. At 10, show `recruit_gang_full` and return the effects gathered so far.
- Keep the docstring and the module notes in step: `:12145` → `:12005` → `:12105`.
- Rewrite `test_ninth_hire_succeeds_tenth_is_denied_mid_batch` to the source order.

**Execution note:** Read `:12100`–`:12175` through `mafia-oracle` `conclude` before editing. It must confirm the re-entry path and that a "no" continues the batch.

**Test scenarios:**
- Covers AE3:
  - Roster 9, batch of 2, yes/yes, cash enough. Three RNG draws: pool, candidate 1, candidate 2.
  - Two intro and offer screens, then `recruit_gang_full`.
  - Effects are only the first hire's three.
- Covers AE4: roster 9, batch of 3, yes/no/yes, cash enough.
  - Candidate 2 is declined with no message.
  - Candidate 3 is offered, then `recruit_gang_full` shows.
- Edge case: roster 9, batch of 2, yes, then yes with too little cash. The cant-afford message shows and there is no gang-full message.
- R7: the `:12105` entry check still denies with no draws when the roster is already 10.

**Verification:** Each new test fails when the check is moved back to the top of the pass. The oracle's conclusion is quoted in the test module's docstring.

### U5. Close-out

**Goal:** Close #98 and #105 and leave the tree pointing at the next slice.

**Requirements:** all

**Dependencies:** U1–U4

**Files:**
- `CLAUDE.md` (ledger line)
- `docs/plans/2026-09-27-002-next-slice-brainstorm-basis.md` (new; carries forward `docs/plans/2026-09-26-004-next-slice-brainstorm-basis.md` without #98 and #105)

**Approach:**
- Add this plan to the landed ledger.
- Write the next-slice basis doc, so the newest `docs/plans/` file is a basis doc again.
- Close the issues when their commits land green.

**Test expectation:** none, since this unit only changes docs. The citation checker covers any quoted BASIC.

**Verification:** `make check` is green on 3.11 and 3.14. `gh issue list` shows neither #98 nor #105.

---

## Verification Contract

| Gate | Check | When |
|---|---|---|
| Green tree | `make check` (pytest, ruff, pyright) via `uv run --python 3.14 --with-editable '.[dev]' --isolated make check` | Before every dispatch and commit |
| Both Pythons | The same with `--python 3.11` | U2, U3, U5 |
| Break-to-prove | Each new or changed test fails with its feature broken; `PYTHONDONTWRITEBYTECODE=1`; restore by editing, never `git checkout` | U1–U4 |
| Fidelity | Every game value or order a test asserts is gated through `mafia-oracle` (`conclude`) | U3, U4 |
| ROM oracle | Every `str$` expectation comes from a VICE capture | U1 |
| Client tests | Drive `play()`/`main()` only; no `monkeypatch` of `clients.terminal` names | U3 |

## Definition of Done

- Every AE passes as a test that was seen red.
- The classic theme shows no `.0` float and no `0.` leading zero anywhere a template prints a number.
- The rank screen reproduces the lost minus. The sign-stripped form is used only where the source uses `mid$(str$(…),2)`.
- The recruit loop matches `:12108`–`:12175` in screen and draw order.
- #98 and #105 are closed; the ledger and the next-slice basis doc are in place.
- There is no dead code from abandoned approaches in the diff.

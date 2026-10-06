---
title: "Next Slice — Brainstorm Basis"
type: brainstorm-input
date: 2026-10-06
topic: next-slice
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
status: input-for-brainstorm
---

# Next Slice — Brainstorm Basis

> **This is a brainstorm INPUT, not a plan.** It is the newest file in `docs/plans/` so
> that the Open Issues Fidelity plan (`2026-10-05-001-fix-open-issues-fidelity-plan.md`,
> U1–U11, closed #146, #147, #155, #156, #159, #160 and #162–#172) reads as done. Nothing
> here is decided; each item needs a brainstorm and a plan before anyone builds it. It
> replaces the previous basis (`2026-10-04-003`), whose open items are carried forward.

## Where the game stands

The whole game plays in the terminal client for 1–4 hot-seat players, set up through the
original's screens. Since the last basis:
- The score sums in ported C64 float arithmetic (FADD, FMULT, the number parser and the rank
  divide, each held to VICE captures), switchable to exact decimal (`c64_float_score`).
- Negative numbers the C64 accepts at the pub's counts and the chief's months follow the source
  (`c64_input_negatives`, which replaced `chief_bribe_negative_months`).
- The location screen shows its title, numbers its options from 1 and picks on one key.
- Every location exit waits for a key exactly where its BASIC reaches `:1100`, and every fight's
  outcome screen waits once; the client adds no wait of its own.
- The coverage ledger requires a narrow citation where each player-facing block is shown, and no
  player-facing block is deferred.

## Most visible open gap

- **In-handler choice menus are still 0-based.** `PromptChoice` menus render `0) 1 - poker`:
  the casino's game menu (`:16010-16016`) prints the source's own numbers, but typing `1` picks
  black jack. The source reads 1..n. Same fix shape as #159's location menu; every `PromptChoice`
  is affected, and scripted picks shift. The wager prompt also prints "dein einsatz" twice.

## User request carried forward: watch the end-to-end runs in the terminal client

From `2026-10-04-001`: an entry point (`--demo`, a `tools/` script, or a fight-lab-style `watch`
subcommand) that drives `play()` with the same screen-reading players and start states as
`tests/test_full_game_e2e.py`, with a per-key delay, one shared source of truth for test and demo,
and no weakening of the tests.

## Open issues

- **#157**, **#158** — review reports (PR #148; the whole repository).

## Found during the Open Issues Fidelity plan

**Input and parsing**
- The motel's months (`:10030 ifx=0orx<0 thennm=1:return`): a negative answer returns quietly
  on the C64; the port asks again.
- C64 `INPUT` reads `- 3` (sign, space, digits) as -3; the port asks again. Pinned as a departure
  in `tests/test_c64_input.py` (`_READ_DIFFERENTLY`), with no note in `content/house_rules.yaml`.
- `--score-weight`'s CLI range check reads the text with `float` only; a value within about 1e-10
  of a bound can pass the CLI and raise at the setup pre-fill.
- `c64_int_divide`'s negative quotients and `c64_val`'s exponent, overflow and underflow edges are
  model-only (no capture).

**Score**
- The weapon shop's `:13065`/`:13072`/`:13073` score changes add in doubles (`ScoreChange`, not
  `:1160`); the next `:1160` store rounds the result back to a 5-byte value.

**Screens and text**
- `:4620` with a negative fine (cash below zero) drops the minus the C64 prints at column 39 of
  the blank row (pinned in `test_a_negative_seizure_drops_only_the_minus_at_column_39`).
- `:12022` prints the uncapped barrel roll before `:12025`'s cap; the port shows the capped stock.
- `:12070`'s "'besorg noch mehr (gier)!'" has no theme string.
- The tip-4 text shows twice (`tip_waffenschmuggel`, `arms_deal_offer`); the source prints it once.
- The pub job and loan-shark strings add `'…'` quote marks the source lines lack.
- `jobs`' "welchen trick (1-3)" is a prompt the port invented; the source prints none.
- `{rvon}` reverse video is not rendered for the job title, the fight banner or the CPU label;
  `:30030`'s 2-second delay after the banner is not modelled.
- The human fighter's own label at `:30115` is not rendered.
- `locations.waf.grenades_in` joins `:13090-13091` onto one line; the source breaks it.
- The fight lab passes no side names, so it shows no names row.
- A quit at the location menu prints no "bye." line; the map quit does.

**Key waits**
- The `:1100` wait is addressed to the active player even where the defender acts (prison brawl,
  gang duel).
- The job shift's `TURN_OVER` screen follows the shift's own waits; it has no source counterpart.

**Tests that prove less than their names**
- `TestWholeSessionDeterminism._script` claims to walk into the pub and buy, but at seed 11 with
  two players it enters the casino; it only checks determinism.
- `test_waf_train_camp_same_seed_twice` asserts no training outcome, only that two runs agree.
- The won-fight wait paths of the bouncer, croupier and killer shifts, and the mayor and
  cash-transport flows' fight waits, have no dedicated tests.

## Recorded departures (in `content/house_rules.yaml`'s header and the handler docstrings)

- The turn-start upkeep messages share one screen and one key (`handlers/upkeep.py`); the source
  gives each its own screen and `:1100` wait.
- The location art splash and its Enter before the `:3025` menu (a port addition).
- Fractional and empty answers and `val()` prefix parsing at prompts (the casino's fractional game
  choice and stake included); the score weight takes only the C64 parser's precision.
- After a lost fight, capture receives `p=0` where the source's `p` is stale.
- Theme stand-ins for picture-only or sound-only screens: the safe dials' slip/click lines, the
  opened safe (`trs2`) and the victory screen (`sieg-pic`).
- The stale `kf$` backdrop at `:15312`, the always-drawn second miss factor at `:30247`, and the
  debug switches at `:1208` and `:315` (`peek(53247)`).

## Carried forward from earlier basis docs

- Network and server transport, still behind the proven in-process driver.
- The genre-engine goal is untested with a second game config.
- `WeaponInstance.price/ts/tg/ws` are read only by the game config.
- Refactor-only: `play()`'s keyword parameters, the size of `engine/interactions.py`, the comment
  volume.
- Declined or unproven: the `game_end`/`upkeep` runner merge; a loaded save ignoring
  `pending_action`; `Rng.replayed`'s load time; `q`/EOF at the standings screen.
- No test compares the pub's tip texts word for word with the research corpus.
- The untracked local `recordings/kdh_ambush.json` predates house rules and no longer loads.

---
title: "Next Slice — Brainstorm Basis"
type: brainstorm-input
date: 2026-10-04
topic: next-slice
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
status: input-for-brainstorm
---

# Next Slice — Brainstorm Basis

> **This is a brainstorm INPUT, not a plan.** It is the newest file in `docs/plans/` so
> that the Multiplayer Setup plan (`2026-10-04-002-feat-multiplayer-setup-plan.md`,
> U1–U6, closed #149–#154) reads as done. Nothing here is decided; each item needs a
> brainstorm and a plan before anyone builds it. It replaces the previous basis
> (`2026-10-04-001`), whose open items are carried forward below.

## Where the game stands

The whole game plays in the terminal client. A plain start sets up a 1–4 player
hot-seat game through the original's screens: the end year, the score weight, the house
rules, the player count, each player's name and gang name, and the eigenschaften screen,
where each player stops their own stat rolls. Setup is a config handler
(`handlers/new_game.py`, `engine.turns.SETUP_HANDLER_KEY`) over the interaction protocol,
with two generic interactions, `PromptText` and `RollFrame`; the client only draws it.
The coverage ledger now also holds every block's screens to account: a block that prints,
prompts or waits for a key needs a citation where the player sees it (a theme string or
`clients/`), or a deferral naming an issue.

## Leftovers from the Multiplayer Setup plan

- **#159** — the location screen (`:3025-3040`) shows no disk title and numbers the
  options from 0, where the source numbers them from 1 and reads 1..aw. The ledger defers
  `:3015-3035` to it. Every scripted location pick in the tests shifts by one.
- **#160** — the `:1100` key wait after a location result is a generic client rule since
  #153 (an option that completes with a message unread waits for a key). Audit each
  handler's exits against the BASIC and make the divergent ones explicit.
- **#155** — the player-facing ledger rule accepts wide theme-header citations, so about
  100 blocks pass on a range comment. Narrow them as `check_narrow` does for code.
- Setup shows no whose-turn line on the eigenschaften screens of `--player` games (the
  source shows none either); more than four `--player` flags are refused only after the
  title screen.
- Theme stand-in added: the opened-safe picture (`:20150`, `trs2`) is a line of text,
  as the victory picture is.

## User request: watch the end-to-end runs in the terminal client

Carried forward from `2026-10-04-001`. The user wants to see the two full-game runs
(`tests/test_full_game_e2e.py`) play out in the real terminal client. Shape to settle:
an entry point (a `--demo` flag, a `tools/` script, or a fight-lab-style `watch`
subcommand) that drives `play()` with the same screen-reading players and start states,
a per-key delay, one shared source of truth for the test and the demo, and no weakening
of the tests.

## Open amendments (GitHub issues, label `plan:full-game`)

- **#146** — known divergences in landed handlers: input holes (`PromptInt(min=0)` where
  C64 `INPUT` lets a negative through), the `tenancy` guard variable, the upkeep screens'
  one-key-for-all (the source waits after each), the weapon shop's spec-sheet labels
  (`:13515-13520`), and `:4620`'s `{left}`.
- **#147** — `:1160`'s score accumulation drifts a cent in doubles on 3 of 11 captured
  series.

## Other open issues

- **#156** — exhausted combat input silently returns an unfinished recording.
- **#157**, **#158** — review reports (PR #148; the whole repository).

## Recorded departures (in `content/house_rules.yaml`'s header and the handler docstrings)

- After a lost fight, capture receives `p=0` where the source's `p` is stale.
- Fractional and empty answers to whole-number prompts re-ask instead of following C64
  `INPUT`; the setup's number prompts parse with `float`, so `val("2a")`'s 2 is asked
  again.
- Theme stand-ins where the source shows only a picture or plays only sounds: the safe
  dials' slip/click lines, the opened safe (`trs2`) and the victory screen (`sieg-pic`).
- The stale `kf$` backdrop at `:15312`, the always-drawn second miss factor at `:30247`,
  and the debug switches at `:1208` and `:315` (`peek(53247)`).

## Carried forward from earlier basis docs

- Network and server transport, still behind the proven in-process driver.
- The genre-engine goal is untested with a second game config.
- `WeaponInstance.price/ts/tg/ws` are read only by the game config.
- Refactor-only: `play()`'s keyword parameters, the size of `engine/interactions.py`, the
  comment volume.
- Declined or unproven: the `game_end`/`upkeep` runner merge; a loaded save ignoring
  `pending_action`; `Rng.replayed`'s load time; `q`/EOF at the standings screen.
- No test compares the pub's tip texts word for word with the research corpus.
- The untracked local `recordings/kdh_ambush.json` predates house rules and no longer loads.

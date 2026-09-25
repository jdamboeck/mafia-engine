---
title: "Next Slice — Brainstorm Basis"
type: brainstorm-input
date: 2026-09-25
topic: next-slice
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
status: input-for-brainstorm
---

# Next Slice — Brainstorm Basis

> **This is a brainstorm INPUT, not a plan.** It gathers what the vertical-slice completion plan (`2026-09-25-001-feat-vertical-slice-completion-plan.md`) deliberately deferred, plus what its execution and code review left behind. Feed it into `ce-brainstorm` to scope the next slice. Nothing here is decided.

## 0. Where things stand

- The first vertical slice is complete: setup (end year, score weight), five locations, combat, per-round standings, the year-end ending, save and resume, CI on Python 3.11 and 3.14. See the README's "What works today".
- All units U1-U12 of the completion plan landed on `feat/vertical-slice`; issues #45, #49, #51 are closed.
- The original has two endings. Only the **year-end** ending exists. The **early win** (rank 10 plus both win flags, `mf-prg.bas:1011`) is not built.

## 1. The deferred core: the crime and jail chain

These were cut from the slice as one piece, because each depends on the next.

- **Crime → wanted → arrest → jail.** `WantedChange` and `Jail` in `engine/effects.py` still raise `NotImplementedError`; the upkeep jail gate is a no-op. The original's police capture is `mf-prg.bas:26020-26080` (bribe / flee / surrender, trial, lawyer, sentence).
- **The two map-triggered win flows.** Cash transport (`la=13`, cell 569, `mf-prg.bas:23000-23030`) and mayor hit (`la=14`, cell 861, `mf-prg.bas:24000-24020`). Both are started by a pub tip (`:12225`, tip types 3 and 5) and both lose into police capture. They set the win flags `x5`/`x6`.
- **The early win.** `ra(sp)=10 and x5%(sp)>0 and x6%(sp)>0` at turn start (`mf-prg.bas:1011`), with the `sieg-pic` ending.
- **Per-player flag scopes.** `FlagSet` with a non-global scope still raises `NotImplementedError`.

## 2. Remaining content

- **Seven more locations.** The original has 12 menu locations; the slice built `kdh`, `pub`, `slw`, `sph`, `waf`.

## 3. Engine-boundary work (carried from the combat plan)

- Game-stat names still in engine code (`engine/effects.py` `_STAT_NAMES`; `WeaponInstance.req_*`), and game-vocabulary effects and state entities in `engine/`. The seam should be config-registered **typed** effects, decided as a `docs/design/` amendment.
- Player-vs-player fight initiation from the map.

## 4. Left over from this slice (low priority, recorded so they are not lost)

- `play()` in `clients/terminal/__main__.py` has grown to seven keyword parameters ("new game" vs "resume a save" are two groups). A restructure was declined for test blast radius.
- `engine/game_end.py` and `engine/upkeep.py` share a runner shape; merging was declined because upkeep's input contract has a combat exception.
- The map screen's hint line and the turn-over/save notes are partly hardcoded English in the client, not theme strings.
- A loaded save ignores `pending_action` (unreachable today: the client only saves on the map).
- `Rng.replayed` re-issues every logged draw on load; load time grows with the number of draws. Measured: 3.4 KB for a full idle two-player game; a combat-heavy game's size is unmeasured.
- `q` or EOF at the per-round standings screen ends the session (by design, via the shared quit vocabulary).
- `engine/interactions.py` is over 1,100 lines; a decomposition candidate.

## 5. Suggested brainstorm agenda

1. Scope the jail chain: which parts of police capture, trial and jail make the slice, and is the early win in it?
2. Which win flow first, or both?
3. How many of the seven remaining locations, and which?
4. Does the engine-boundary work (§3) go before or after more content?

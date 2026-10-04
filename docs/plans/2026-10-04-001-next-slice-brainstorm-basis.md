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
> that the Full Game plan (`2026-09-27-003-feat-full-game-plan.md`, U1–U38, closed
> #108–#145) reads as done. Nothing here is decided; each item needs a brainstorm and a
> plan before anyone builds it. The older basis docs (`2026-09-25-002`, `2026-09-26-002`,
> `2026-09-26-004`, `2026-09-27-002`) are consumed: what they listed was either built by
> the Full Game plan or is carried forward below.

## Where the game stands

Every feature of the original is playable in the terminal client: all 12 locations, the
police chain, both map win flows, the early win, the gang war and the prison brawl. Every
line block of `mf-prg.bas` is accounted for in `docs/coverage-ledger.yaml` (243 ported,
5 named exceptions), held in sync with the code by `tests/test_coverage_ledger.py`, and
two seeded end-to-end runs (`tests/test_full_game_e2e.py`) reach the early win and serve
a sentence through `play()`.

## Open amendments (GitHub issues, label `plan:full-game`)

- **#146** — known divergences in landed handlers:
  1. Input holes: handlers clamp inputs with `PromptInt(min=0)` where C64 `INPUT` lets a
     negative through (`:12027`/`:12060` barrel counts, `:10030` months, the kdh
     amounts). KTD-11 asks for a VICE check and an intent switch each. (`ble`, `pol` and
     the lawyer prompt already port their negatives.)
  2. The `tenancy` guard variable reads 0 for both a vacant room and player 0's room; no
     shell uses it any more. Redefine or remove it.
  3. The source waits for a key after every upkeep screen (`:4200`, `:4309`, `:4420`,
     `:4620`, `:31010` → `:1100`); the port shows them on one screen with one key.
  4. The weapon shop's spec sheet prints bucket numbers where the source prints the
     `ts$`/`tg$` labels (`:13515-13520`).
  5. `:4620`'s `{left}` moves the number onto the next line; the port joins it.
- **#147** — `:1160`'s `gf=gf+x*x8` accumulates in doubles; 3 of 11 captured series
  drift a cent from the C64 (pinned by `tests/test_c64_float.py`). Emulating FADD/FMULT
  would close it; the behavioral fidelity bar may accept the cent.

## Recorded departures (in `content/house_rules.yaml`'s header and the handler docstrings)

- After a lost fight, capture receives `p=0`; the source's stale `p` is whatever cell the
  fight last touched (at most a 520 $ difference on the chief-bribe auto-pay). Adding a
  "last p" to `CombatResult` was declined in U35.
- Fractional and empty answers to whole-number prompts re-ask instead of following C64
  `INPUT` (ble's stake, the lawyer fee, the pickers, aut's letter key).
- Theme stand-ins where the source shows only a picture or plays only sounds: the safe
  dials' slip/click lines (U37) and the victory screen (U23, `sieg-pic`).
- The source's stale `kf$` backdrop at `:15312`, the always-drawn second miss factor at
  `:30247`, and the hidden debug line at `:1208`.

## Carried forward from earlier basis docs and the plan's Scope Boundaries

- Network and server transport, still behind the proven in-process driver.
- The genre-engine goal is untested with a second game config.
- `WeaponInstance.price/ts/tg/ws` are read only by the game config (the engine reads
  `range`); under the "abstract contracts only for what the engine reads" rule they could
  leave `engine/types`.
- Refactor-only: `play()`'s keyword parameters, the size of `engine/interactions.py`, the
  comment volume.
- Declined or unproven: the `game_end`/`upkeep` runner merge; a loaded save ignoring
  `pending_action` (unreachable); `Rng.replayed`'s load time (unmeasured); `q`/EOF at the
  standings screen ending the session (by design).

## Small findings from the last units

- No test compares the pub's tip texts word for word with the research corpus (U29 found
  `:12241-12242`'s doubled "wagen" dropped, now restored).
- The untracked local `recordings/kdh_ambush.json` predates house rules and no longer
  loads; `clients/terminal/FIGHTLAB.md` gives the command to record a fresh one.

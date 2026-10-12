---
title: "Next Slice — Brainstorm Basis"
type: brainstorm-input
date: 2026-10-12
topic: next-slice
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
status: input-for-brainstorm
---

# Next Slice — Brainstorm Basis

> **This is a brainstorm INPUT, not a plan.** It is the newest file in `docs/plans/` so
> that the Remaining Fidelity Gaps plan (`2026-10-12-001-fix-remaining-fidelity-gaps-plan.md`,
> U1–U9, closed #179–#187) reads as done. Nothing here is decided; each item needs a
> brainstorm and a plan before anyone builds it. It replaces the previous basis
> (`2026-10-06-001`), whose open items went into that plan or onto the board as #174–#178.

## Where the game stands

The whole game plays in the terminal client for 1–4 hot-seat players. Since the last basis:
- Choice menus print the source's own numbers and read as their source line reads: one key
  for a `getx$` menu (the shop, the police, the car dealer, freeing a prisoner, the gang war's
  opponent, the croupier's tricks), a line for an `input` menu (the casino), F1/F3/F5 at the
  safe and s/t at the weapon shop's venue. The engine's answers stay 0-based.
- Texts show what the source prints: no doubled lines, no quote marks the source lacks, the
  uncapped barrel roll, the grenade news broken as `:13090-13091` break it.
- The rent seizure's minus at column 39, reverse video for the source's `{rvon}` texts through
  the theme's `renderer/reverse.yaml`, and the human fighter's label (`:30115`).
- The prison brawl's key waits go to the jailed player.
- Under `c64_input_negatives`: a spaced sign (`- 3`) at every line-read number prompt, and the
  motel's and casino's negatives return quietly.
- The weapon shop's score lines (`:13065`, `:13072`, `:13073`) follow `c64_float_score`.
- Too many `--player` flags are refused; a location-menu quit says "bye.".
- The departures list in `content/house_rules.yaml`'s header records the turn-over screen,
  the unmodelled `:30030` banner pause, setup's re-ask of a weight like `2a` and the location
  splash's Enter.

## Open issues

- **#174** — engine robustness: `run()`'s mid-fight input exhaustion, the intent arm's decimal
  edge, a per-line ledger gate, 10+ location options.
- **#175** — test credibility: tests that prove less than their names, and untested paths.
- **#176** — watch the end-to-end runs in the real terminal client (the demo request).
- **#177** — refactors and housekeeping (`setup.py` and `interactions.py` size, `play()`'s
  parameters, `WeaponInstance` fields, the stale recording).
- **#178** — roadmap: network/server transport and a second game config.
- **#157**, **#158** — review reports (PR #148; the whole repository).

## Found during the Remaining Fidelity Gaps plan

**Menus and keys (U1)**
- The weapon shop's venue and the safe still print option labels the source does not:
  `:13105` prints only `(s)chiesstand oder (t)rainingscamp ?`, and `:20115` reads F1/F3/F5 with
  no menu text of its own.
- Modified function keys (`ESC [1;2P` and the like) are not mapped; only the plain F1/F3/F5
  sequences are.
- A burst of keys typed at once keeps the rest for later reads, where a C64 `getx$` loop
  would see them one by one from the keyboard buffer; not compared against the source.

**Screens and text**
- Texts the source prints with several `PRINT`s (the pub job and recruit, kdh, the job shifts,
  tip 4's trailing blank row) are joined with one space; the source breaks the rows. (U3)
- More `{rvon}` texts are not in the reverse list: the `:25000` shift header, `:30235`
  `verfehlt!`, `:30350` `treffer!`; the `:30115` fighter panel is drawn plain; the human label
  is not kept on observe frames, whose payload lacks the last human fighter. (U4)
- A `--theme` without its own `renderer/reverse.yaml` draws plain instead of merging over the
  classic list, so a strings-only mod theme loses reverse video. (U4)

**Input**
- `val("- 3")` at the `x$` prompts (kdh `:15015`/`:15051`/`:15208`, the lawyer `:26060`, the
  thank-you `:21252`) is assumed to read -3 like `INPUT`; not captured in VICE. Blanks between
  digits (`1 2` read as 12) are not ported. (U6)

## Recorded departures (in `content/house_rules.yaml`'s header and the handler docstrings)

- The turn-over screen after every turn, the `:30030` banner pause, setup's re-ask of a weight
  like `2a` and the location splash's Enter (added by U9).
- The turn-start upkeep messages share one screen and one key (`handlers/upkeep.py`).
- Fractional and empty answers at the prompts listed in the header.
- After a lost fight, capture receives `p=0` where the source's `p` is stale.
- Theme stand-ins for picture-only or sound-only screens.
- The stale `kf$` backdrop at `:15312`, the always-drawn second miss factor at `:30247`, and the
  debug switches at `:1208` and `:315`.

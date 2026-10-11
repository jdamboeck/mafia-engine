---
title: "Remaining Fidelity Gaps - Plan"
type: fix
date: 2026-10-12
topic: remaining-fidelity-gaps
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
---

# Remaining Fidelity Gaps - Plan

## Goal Capsule

- **Objective:** close the remaining player-visible fidelity gaps found by the 2026-10-11 audit of every brainstorm-basis doc, so each one either matches the 1986 original or stands as a recorded departure.
- **Product authority:** `../research/` for game behaviour; the BASIC (`mf-prg.bas`) wins over secondary research files, and VICE is the authority for C64 ROM behaviour. `docs/design/` governs architecture.
- **Out of this slice:** the GitHub issues #174–#178 (engine robustness, test credibility, the e2e demo, refactors, the roadmap) and the review reports #157 and #158.
- **Open blockers:** none.

---

## Product Contract

### Summary

This slice makes the last known player-visible divergences faithful: choice menus, duplicated and invented texts, negative-number input, screen details, who answers a key wait, and the weapon shop's score arithmetic. Where the port keeps its own behaviour on purpose (the turn-over screen, the fight banner's pause), the house-rules catalogue records it as a departure.

### Problem Frame

The full game plays and the large fidelity work has landed, but an audit of all eight brainstorm-basis docs found a tail of small divergences that a player still meets. The most visible one changes outcomes: in-location choice menus are numbered from 0, so typing `1` at the casino picks black jack instead of poker.

The rest are smaller but each is a place where the screen or the input differs from the original: texts shown twice or invented, quote marks the source lacks, a missing minus, a missing label, reverse video drawn as plain text, a key wait answered by the wrong player. Two departures exist only as code comments, so an audit cannot tell them from gaps.

### Key Decisions

- **The turn-over screen stays after every turn**, job shifts included, recorded as a hot-seat departure. The source goes straight to the next player after a job shift (`:1012 gosub25000:goto1010`); the port's turn-over screen is the keyboard handover between hot-seat players.
- **Reverse video is faithful; the banner pause is not.** The job title (`:25015`, `:25100`, `:25200`), the fight banner (`:30025`) and the CPU label (`:30400`) render in reverse video as `{rvon}` prints them and as the location title already does. The `:30030` banner loop (about two seconds) is not modelled and is recorded as a departure.
- **Negative answers follow `c64_input_negatives`.** Under faithful, C64 `INPUT`'s reading of a sign separated from its digits (`- 3` as -3) applies at every number prompt, and a negative motel month count returns quietly (`:10030`). Under intent both are asked again.
- **Choice menus read as their source lines do.** Each in-location choice menu is numbered from 1 in the source's form and reads one key where the source uses `getx$`, a line with Enter where it uses `input`.

### Requirements

**Menus and prompts**

- R1. Every in-location choice menu shows its options numbered from 1 as its source line prints them, and a number picks the option it labels.
- R2. Each choice menu reads its answer the way its source line does: one key for `getx$`, a line with Enter for `input`.
- R3. The croupier's trick menu shows only the three tricks (`:25110-25111`) and reads one key (`:25115`); the port's own "welchen trick (1-3)" line goes.
- R4. More than four `--player` flags are refused before the game starts, with the CLI's existing kind of error.

**Texts**

- R5. The casino's stake prompt shows "dein einsatz" once (`:16020`).
- R6. The pub's fourth tip shows its text once.
- R7. The pub's barrel offer shows the uncapped roll the source prints at `:12022`, before `:12025` caps it.
- R8. The pub job and loan-shark strings carry no quote marks the source lines lack.
- R9. The grenade news breaks across lines as `:13090-13091` print it.
- R10. A quit at the location menu ends with the same "bye." line as a quit on the map.

**Screens**

- R11. A negative rent seizure shows its minus where `:4620`'s `{left}` places it, at column 39 of the blank row (the existing VICE capture holds the layout).
- R12. The job title, the fight banner and the CPU fighter's label render in reverse video.
- R13. The human fighter's own label (`:30115`) is shown as the source shows it.
- R14. A key wait in the prison brawl and the gang duel is addressed to the player whose screen it closes, not always the active player.

**Input and score**

- R15. Under faithful `c64_input_negatives`, a number typed with a sign separated from its digits reads as C64 `INPUT` reads it, at every number prompt; a prompt that refuses negatives still refuses it.
- R16. Under faithful `c64_input_negatives`, a negative motel month count returns quietly as `:10030` does; under intent it is asked again.
- R17. The weapon shop's score changes (`:13065`, `:13072`, `:13073`) follow `c64_float_score`: C64 float under faithful, exact decimal under intent.

**Recorded departures**

- R18. The house-rules catalogue's departures list records the turn-over screen after every turn, the unmodelled `:30030` banner pause, setup's re-ask of a weight like `2a` (`val` prefix parsing), and the location art splash's Enter.

### Acceptance Examples

- AE1. **Covers R1, R2.** Given the casino's game menu, when the player answers `1`, then poker is played; when the source line reads `input`, the answer needs Enter, and when it reads `getx$`, one key picks.
- AE2. **Covers R15.** Given faithful `c64_input_negatives`, when the player types `- 3` at the pub's buy count, then the trade runs as for -3; at a prompt whose BASIC refuses negatives, `- 3` is refused like `-3`.
- AE3. **Covers R16.** Given faithful `c64_input_negatives`, when the player rents the motel for -2 months, then nothing is rented and no prompt repeats; under intent the prompt asks again.
- AE4. **Covers R14.** Given a prison brawl where the defender acts last, when the brawl's outcome waits for a key, then the whose-turn line names the defender.
- AE5. **Covers R17.** Given the weapon shop's training score change under faithful, when its sum is compared with the C64's, then they match as the `:1160` sums do.

### Success Criteria

- Every in-scope item of the 2026-10-11 audit is either faithful or a catalogue-recorded departure.
- An audit of the departures list finds each port difference this slice keeps, and no gap it fixed.
- Seeded scripted runs, including the two end-to-end runs, still pass after their scripts follow the new menu numbering.

### Scope Boundaries

- Engine robustness follow-ups (`run()`'s mid-fight exhaustion, the intent decimal edge, a per-line ledger gate, 10+ location options): #174.
- Test-credibility gaps: #175.
- The end-to-end demo request: #176.
- Refactors and housekeeping: #177.
- Network transport and a second game config: #178.
- The review reports #157 and #158.
- The `:30030` banner pause (recorded, not modelled).

### Dependencies / Assumptions

- The source line behind each choice menu (`getx$` or `input`) is read per menu during planning; the casino's game menu is `:16015 input`, the croupier's tricks `:25115 getx$`.
- Assumption: the `:4620` VICE capture (`tests/fixtures/c64_rent_seizure/`) already holds the negative-fine layout (row 4 ends in `-` at column 39), so R11 needs no new capture.
- Assumption: C64 `INPUT`'s reading of `- 3` is confirmed by the committed capture in `tests/fixtures/c64_input/` (`[- 3]:-3`).

### Sources / Research

- The 2026-10-11 audit of all brainstorm-basis docs (`docs/plans/*-brainstorm-basis.md`); forward pointer `docs/plans/2026-10-06-001-next-slice-brainstorm-basis.md`.
- `docs/plans/2026-10-05-001-fix-open-issues-fidelity-plan.md`: the `c64_input_negatives` and `c64_float_score` rules this slice extends, and the location menu's one-key pick.
- `clients/terminal/renderers.py` `render_location_title`: the existing reverse-video rendering.

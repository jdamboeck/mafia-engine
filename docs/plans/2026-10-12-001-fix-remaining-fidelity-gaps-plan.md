---
title: "Remaining Fidelity Gaps - Plan"
type: fix
date: 2026-10-12
topic: remaining-fidelity-gaps
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
branch: fix/remaining-fidelity-gaps
---

# Remaining Fidelity Gaps - Plan

## Goal Capsule

- **Objective:** close the remaining player-visible fidelity gaps found by the 2026-10-11 audit of every brainstorm-basis doc, so each one either matches the 1986 original or stands as a recorded departure.
- **Product authority:** `../research/` for game behaviour; the BASIC (`mf-prg.bas`) wins over secondary research files, and VICE is the authority for C64 ROM behaviour. `docs/design/` governs architecture.
- **Out of this slice:** the GitHub issues #174–#178 (engine robustness, test credibility, the e2e demo, refactors, the roadmap) and the review reports #157 and #158.
- **Execution profile:** `docs/AGENTS.md`: one orchestrator, one subagent per unit, serial in the order of the Unit Index. The orchestrator owns commits, the authoritative `make check` (through uv on 3.14 and 3.11) and the board: one GitHub issue per unit with `dep:` labels, created before U1. Work lands on `fix/remaining-fidelity-gaps`, off `main`.
- **Stop conditions:** a plan claim does not match the BASIC or VICE when re-read (the source wins; fix and report); a red tree a unit cannot fix within its scope.
- **Open blockers:** none.
- **Product Contract preservation:** changed: R2 and R8 widened by planning research. R2 now covers the number menus read by `getx$` (croupier, car dealer, freeing a prisoner, the gang war's opponent) and the two menus answered by letter keys (the safe's F1/F3/F5, the weapon shop's s/t). R8 now covers the pub recruit and job-shift strings and the doubled `''` apostrophes. Both stay inside the confirmed intent ("each menu reads as its source line does"; "no quote marks the source lacks"). R14's duel case is pinned in KTD-6.

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
- R2. Each choice menu reads its answer the way its source line does: one key for `getx$`, a line with Enter for `input`. This covers the number menus a source line reads with `getx$` (the croupier's trick, the car dealer's model, freeing a prisoner, the gang war's opponent) and the menus answered by the key the source names (the safe's F1/F3/F5, the weapon shop's s/t).
- R3. The croupier's trick menu shows only the three tricks (`:25110-25111`) and reads one key (`:25115`); the port's own "welchen trick (1-3)" line goes.
- R4. More than four `--player` flags are refused before the game starts, with the CLI's existing kind of error.

**Texts**

- R5. The casino's stake prompt shows "dein einsatz" once (`:16020`).
- R6. The pub's fourth tip shows its text once.
- R7. The pub's barrel offer shows the uncapped roll the source prints at `:12022`, before `:12025` caps it.
- R8. The pub job, pub recruit, job-shift and loan-shark strings carry no quote marks the source lines lack, and no string prints a doubled apostrophe (`''`) where the source has one.
- R9. The grenade news breaks across lines as `:13090-13091` print it.
- R10. A quit at the location menu ends with the same "bye." line as a quit on the map.

**Screens**

- R11. A negative rent seizure shows its minus where `:4620`'s `{left}` places it, at column 39 of the blank row (the existing VICE capture holds the layout).
- R12. The job title, the fight banner and the CPU fighter's label render in reverse video.
- R13. The human fighter's own label (`:30115`) is shown as the source shows it.
- R14. The prison brawl's key waits are addressed to the defending player; the gang duel's wait stays with the active player (KTD-6).

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

---

## Planning Contract

### Key Technical Decisions

- KTD-1. **Menus keep the engine's 0-based protocol; the client numbers and reads.** `PromptChoice` and `PromptInt` answers stay what the driver validates today, so engine-level tests do not shift. The client prints each `PromptChoice` option's theme label as given, with no prefix of its own (the labels already carry the source's numbers, e.g. "1 - poker"), and maps key `str(k)` to index `k-1`, as U6's location menu (`clients/terminal/session.py` `_render_location_menu`) already does. Options with answer keys are picked by those keys. In a cancellable, line-read menu the client relays `0` as cancel, as `:16015 ifx=0thenreturn` returns.
- KTD-2. **A prompt declares how it is read.** `PromptChoice` and `PromptInt` gain a generic read-mode field (one key or a line) and `PromptChoice` optional per-option answer keys (e.g. `f1`, `s`). The engine stays free of game vocabulary; each handler sets the mode its source line implies (`getx$` → one key, `input` → line). The terminal client reads one key through a reader on its own stdin (today `_read_key` reads `sys.stdin`), with the piped fallback of one key per line. The reader decodes the terminal's F1/F3/F5 escape sequences (`ESC O P`/`R`/... and `ESC [ 11~`/`13~`/`15~`, plus the Linux console's `ESC [ [ A`/`C`/`E`) into `f1`/`f3`/`f5`, and the piped fallback compares the whole stripped line against multi-character answer keys rather than its first character. A one-key read tells Enter from EOF, so a RETURN-leaves menu (car dealer, gang war) leaves on Enter and EOF ends the session.
- KTD-3. **Text fixes are theme edits, held to the source.** Quote marks, duplicated texts, line breaks and the doubled apostrophe are corrected in the classic theme strings; each changed string's citation quotes its source line (`tests/test_citations.py`). Strings whose double-quoted YAML writes `''` for one apostrophe become single-apostrophe strings.
- KTD-4. **Reverse video is a theme-owned list.** The classic theme names the keys its source prints in `{rvon}` (job titles, the fight banner, the CPU and human fighter labels, the location title); the client draws those in reverse video with the existing `REVERSE` code. The list lives in `themes/classic/renderer/reverse.yaml`, loaded as `renderer/contexts.yaml` is; a theme that omits it draws plain text. A label that starts with `{tab(N)}` (the CPU label) starts its reverse bar after the padding.
- KTD-5. **`c64_input_negatives` widens to two more behaviours.** Under faithful, number prompts accept a sign separated from its digits (`- 3`), set per prompt by a config helper that reads the switch (the engine gains only a generic "accept a spaced sign" option on `PromptInt`); and the motel's months prompt allows negatives and returns quietly on one (`:10030`). Under intent both stay as today. The catalogue entry and its theme line are widened to name them.
- KTD-6. **Key-wait addressing in the brawl and duel.** The prison brawl's outcome wait is addressed to the defending player, the only human side. In the gang duel both sides are human and the source names no addressee; the wait stays with the active (attacking) player, and only the wait is addressed, not the four outcome lines, so the whose-turn line prints once.
- KTD-7. **The weapon shop's score changes get a config effect.** A config effect applies `:13065`/`:13072`/`:13073`'s `gf=gf±x8` through `c64_add` under faithful and exact decimal under intent, mirroring `ScoreAndRank`'s branch; subtraction is FADD of the negated operand, pinned by a new VICE capture of FSUB cases.

### Risks

| Risk | Mitigation |
|---|---|
| One-key reads change how scripted client tests feed menus. | Piped input keeps one key per line, so line counts stay; only the numbers in about 16 scripts shift (measured: the casino menu in 13 tests, the police menu in one e2e run, the croupier in 2). |
| Removing quote marks breaks tests that pin today's text. | Update the pinned strings to the source text; never weaken an assertion to a substring. |
| The `{tab(39)}` seizure template pads positive fines with trailing spaces. | Render the row with the template, compare rows after stripping trailing spaces, as the capture test does. |
| Widening `c64_input_negatives` changes the catalogue text old saves store. | Old saves already fail on the catalogue change since #173; the switch id and setting values do not change, so saves from #173 on still load. |

---

## Implementation Units

### Unit Index

| U-ID | Title | Files touched | Depends on |
|---|---|---|---|
| U1 | Prompt read modes and 1-based choice menus | `engine/interactions.py`, `clients/terminal/__init__.py`, `clients/terminal/session.py`, `handlers/sph.py`, `sgl.py`, `police.py`, `ban.py`, `waf.py`, client tests | none |
| U2 | One-key number menus and the croupier | `handlers/jobs.py`, `aut.py`, `pol.py`, `gang_war.py`, `strings/jobs.yaml` | U1 |
| U3 | Text fixes | theme strings (`sph`, `pub`, `kdh`, `jobs`, `waf`), `handlers/pub.py`, citation-pinning tests | U2 |
| U4 | Screen details: seizure minus, reverse video, fighter label | `strings/upkeep.yaml`, `strings/combat.yaml`, `strings/jobs.yaml`, `clients/terminal/renderers.py`, `clients/terminal/session.py`, `renderer/reverse.yaml` | U3 |
| U5 | Key-wait addressing in the brawl and duel | `setup.py` (`narrate_combat_outcome`), `handlers/gang_war.py`, `tests/test_gang_war.py` | U4 |
| U6 | Negative input under `c64_input_negatives` | `engine/interactions.py`, `handlers/slw.py`, config prompt helpers, `content/house_rules.yaml` | U5 |
| U7 | The weapon shop's score in C64 float | `effects.py`, `handlers/waf.py`, `tests/fixtures/c64_float/` | U6 |
| U8 | CLI and quit: player count, bye | `clients/terminal/cli.py`, `clients/terminal/session.py` | U7 |
| U9 | Departures and close-out | `content/house_rules.yaml`, `CLAUDE.md`, a new brainstorm basis | U8 |

Paths under `handlers/`, `strings/`, `content/` and `effects.py` are relative to `data/game_configs/mafia_1920s/` (theme strings under `themes/classic/strings/`).

### U1. Prompt read modes and 1-based choice menus

**Goal:** every `PromptChoice` shows its options numbered from 1 and reads its answer as its source line does.

**Requirements:** R1, R2

**Dependencies:** none

**Files:** `engine/interactions.py`, `clients/terminal/__init__.py`, `clients/terminal/session.py` (`_read_key`, `_render_location_menu`), `data/game_configs/mafia_1920s/handlers/sph.py` (`:16015 input`), `sgl.py` (`:17555 getx$`), `police.py` (`:26025 getx$`), `ban.py` (`:20115` F1/F3/F5), `waf.py` (`:13106` s/t), `themes/classic/strings/sph.yaml` ("was soll's sein:"), `tests/test_driver.py`, `tests/test_terminal_client.py`, `tests/test_client_loop.py`, `tests/test_slice_integration.py`, `tests/test_terminal_integration.py`, `tests/test_full_game_e2e.py`, and the handler tests of the five sites.

**Approach:** KTD-1, KTD-2. Add the read mode and answer keys; the client renders `PromptChoice` 1-based (or by answer key) and reads one key or a line per the mode. Each of the five sites sets its mode and keys from its BASIC line; the casino menu gains its `:16015` prompt text and keeps its "0 returns" exit. Shift the scripts that answer these menus.

**Execution note:** test-first through `play()`: AE1 fails on today's client before the change.

**Patterns to follow:** U6's `_render_location_menu` one-key pick; `TerminalInput.answer`.

**Test scenarios:**
- Covers AE1. Casino: `1` plays poker, the answer needs Enter (`:16015 input`), `0` leaves.
- The shop menu (`:17555`) and the police menu (`:26025`) pick on one key `1`..`3`; other keys are ignored.
- The safe's dials answer to F1/F3/F5: the raw escape sequences decode to `f1`/`f3`/`f5`, and a piped `f1` line picks F1.
- The weapon shop's venue answers to s/t.
- The casino's menu prints "1 - poker" with no extra prefix.
- Piped scripts keep one key per line; EOF at a one-key menu ends the session (under a deadline: a reader that loops on EOF fails the test).
- The engine's driver still validates 0-based answers (engine tests unchanged).

**Verification:** client tests drive `play()`/`main()` only and pass; a manual pty run picks poker with `1`.

### U2. One-key number menus and the croupier

**Goal:** the number menus their source reads with `getx$` take one key, and the croupier's menu is the source's.

**Requirements:** R2, R3

**Dependencies:** U1

**Files:** `data/game_configs/mafia_1920s/handlers/jobs.py` (`:25110-25115`), `aut.py` (`:14020`), `pol.py` (`:21120`), `gang_war.py` (`:27015`), `themes/classic/strings/jobs.yaml`, their tests, client scripts that answer them.

**Approach:** KTD-2. Each `PromptInt` whose source line is `getx$` sets the one-key mode; the croupier stays a one-key `PromptInt(1..3)`, so its tests keep sending 1..3; the croupier shows the three tricks as `:25110-25111` print them and reads `:25115`'s key, with the invented "welchen trick (1-3)" line removed (R3). The existing quiet-leave keys (RETURN at the car dealer and gang war) keep their behaviour.

**Test scenarios:**
- The croupier screen shows the three trick lines and no extra prompt; `2` picks "gezinkte karten" on one key.
- The car dealer, freeing a prisoner and the gang-war opponent pick on one key.
- RETURN still leaves where the source leaves.

**Verification:** handler and client tests pass.

### U3. Text fixes

**Goal:** the texts show exactly what the source prints.

**Requirements:** R5, R6, R7, R8, R9

**Dependencies:** U2

**Files:** `themes/classic/strings/sph.yaml`, `pub.yaml`, `kdh.yaml`, `jobs.yaml`, `waf.yaml`, `data/game_configs/mafia_1920s/handlers/pub.py` (tip 4, the barrel offer), `tests/test_sph.py`, `tests/test_pub_*.py`, `tests/test_kdh_shell.py`, `tests/test_movement.py`, `tests/test_bhf.py`, `tests/test_strings.py`.

**Approach:** KTD-3. The stake prompt shows "dein einsatz" once (`:16020 print"{down}du hast"ka(sp)"$. dein einsatz:":inputp`): `PromptInt` gains a generic `params` field, the wager prompt carries the whole line with the cash, and the separate cash message goes; the fourth tip prints once (`:12245-12246`); the barrel offer shows the uncapped roll (`:12022`) with its line break; quote marks the source lacks go from the pub job, recruit, job-shift and loan-shark strings; `''` becomes one apostrophe; the grenade news breaks as `:13090-13091`.

**Test scenarios:**
- Each changed string equals the source line's text (quoted in its citation).
- The stake line appears once on the casino screen.
- Tip 4 appears once.
- With a full tank the offer still shows the rolled barrel count.
- No theme string contains a doubled apostrophe.

**Verification:** strings and citation tests pass with the source text.

### U4. Screen details: seizure minus, reverse video, fighter label

**Goal:** the seizure's minus, the reverse-video labels and the human fighter's label show as on the C64.

**Requirements:** R11, R12, R13

**Dependencies:** U3

**Files:** `themes/classic/strings/upkeep.yaml`, `combat.yaml`, `jobs.yaml`, `clients/terminal/renderers.py`, `clients/terminal/session.py`, the theme's reverse-video list, `tests/test_rent_countdown.py`, `tests/test_terminal_renderers.py`, `tests/test_terminal_client.py`.

**Approach:** KTD-4. The seizure template places a negative fine's minus at column 39 of the blank row (`tests/fixtures/c64_rent_seizure/` holds `P=-50`). The client draws the theme-listed keys in reverse video. The human fighter's label (`:30115 print"{rvon}spieler   {left}{left}{left}"f`, column 0 for side 1, 20 for side 2) is drawn on the human's turns, as the CPU label is on the CPU's.

**Test scenarios:**
- The seizure rows for `P=-50` match the capture (trailing spaces stripped); positive fines still match.
- The job title, banner and both fighter labels carry the reverse code; a theme without the list draws them plain.
- The human label shows the active fighter's number at the source's column.

**Verification:** renderer and client tests pass.

### U5. Key-wait addressing in the brawl and duel

**Goal:** the brawl's outcome wait names the defender.

**Requirements:** R14

**Dependencies:** U4

**Files:** `data/game_configs/mafia_1920s/setup.py` (`narrate_combat_outcome`), `handlers/gang_war.py`, `tests/test_gang_war.py`.

**Approach:** KTD-6. The outcome wait takes an addressee; the brawl passes the defending player, the duel the active player. The lost brawl's trailing wait (`:27150 ...goto1100`, in `_prison_brawl`) is addressed to the defender too. Only the waits are addressed.

**Test scenarios:**
- Covers AE4. A prison brawl's outcome wait carries the defender; the whose-turn line names the defender once.
- A gang duel's wait carries the active player; no extra whose-turn line appears.
- On a lost brawl, the trailing wait also carries the defender.
- Wait counts per path are unchanged.

**Verification:** gang-war tests pass.

### U6. Negative input under `c64_input_negatives`

**Goal:** the spaced sign and the motel's negative months follow the switch.

**Requirements:** R15, R16

**Dependencies:** U5

**Files:** `engine/interactions.py` (`PromptInt`, `_coerce_int`), the config's prompt helpers (`handlers/pub.py` `_count_prompt` pattern), `handlers/slw.py`, the config's other number prompts, `content/house_rules.yaml`, `themes/classic/strings/house_rules.yaml`, `tests/test_c64_input.py`, `tests/test_slw.py`, `tests/test_driver.py`.

**Approach:** KTD-5. A generic `PromptInt` option accepts a sign separated from its digits; the config sets it at every number prompt under faithful. The motel's months prompt under faithful allows negatives and returns quietly on one (`:10030`). Remove `- 3` from `test_c64_input.py`'s `_READ_DIFFERENTLY` and drive it through the port. Widen the catalogue entry and its theme line.

**Test scenarios:**
- Covers AE2. Faithful: `- 3` at the pub's buy count trades as -3; at a prompt refusing negatives it is refused like `-3`; intent asks `- 3` again.
- Covers AE3. Faithful: motel months -2 returns quietly, nothing rented; intent asks again.
- The capture's `[- 3]:-3` row drives the port.

**Verification:** input, motel and catalogue tests pass.

### U7. The weapon shop's score in C64 float

**Goal:** the weapon shop's score changes match the C64.

**Requirements:** R17

**Dependencies:** U6

**Files:** `data/game_configs/mafia_1920s/effects.py`, `handlers/waf.py`, `tests/fixtures/c64_float/` (a new FSUB capture), `tests/test_c64_float.py`, `tests/test_waf_buy.py`.

**Approach:** KTD-7. A config effect replaces `ScoreChange` at `:13065`/`:13072`/`:13073`, following `c64_float_score`. Capture subtraction cases in VICE.

**Execution note:** VICE capture first; the effect is proven against it.

**Test scenarios:**
- Covers AE5. Faithful: the shop's training score sums match the capture byte for byte; intent equals the exact decimal.
- The lines are unclamped, as the source's are: `:13065`/`:13072` add `x8` only while `gf<100` (`gf-x8*1*(gf<100)`, true being -1) and `:13073` subtracts `2*x8` only while `gf>0`, so a step may pass 100 or 0; the C64 sum matches the capture on both sides of each guard.

**Verification:** float and weapon-shop tests pass.

### U8. CLI and quit: player count, bye

**Goal:** a fifth `--player` is refused before the game starts, and a quit at the location menu says "bye.".

**Requirements:** R4, R10

**Dependencies:** U7

**Files:** `clients/terminal/cli.py`, `clients/terminal/session.py`, `themes/classic/strings/client.yaml`, `tests/test_terminal_client.py`, `tests/test_terminal_integration.py`.

**Approach:** The CLI compares the flag count with `input_ranges.player_count` and refuses with `parser.error`; the location menu's quit writes `client.bye` as the map's quit does.

**Test scenarios:**
- Five `--player` flags exit 2 with no title drawn and no traceback.
- `q` at the location menu prints "bye." once.

**Verification:** client tests pass.

### U9. Departures and close-out

**Goal:** every kept difference is recorded, and the plan state is unambiguous.

**Requirements:** R18; all

**Dependencies:** U8

**Files:** `data/game_configs/mafia_1920s/content/house_rules.yaml` (departures header), `CLAUDE.md`, a new `docs/plans/<date>-NNN-next-slice-brainstorm-basis.md`.

**Approach:** Widen the departures header's intro to cover screen departures and add the turn-over screen, the `:30030` pause, setup's `2a` re-ask and the location splash's Enter, each with its source line. Add this plan to CLAUDE.md's landed ledger and drop a brainstorm basis pointing at #174–#178 and anything the units found.

**Test expectation:** none beyond the citation checker holding the new quotes.

**Verification:** `make check` green on 3.11 and 3.14.

---

## Verification Contract

| Gate | Check | When |
|---|---|---|
| Green tree | `make check` through `uv run --python 3.14 --with-editable '.[dev]' --isolated make check`, and again with `--python 3.11` | Before every dispatch and every commit |
| Fidelity | Every changed line re-read with `mafia-oracle` `conclude`; `tests/test_citations.py` green | Every unit |
| C64 ROM | VICE captures committed under `tests/fixtures/`; CI never runs VICE | U7 |
| Break-to-prove | Each new test fails with its feature broken (scratch-copy restore, `PYTHONDONTWRITEBYTECODE=1`, timeouts count as red) | Every feature unit |
| Client surface | Client tests drive `play()`/`main()` only | U1, U2, U4, U8 |
| Board | Every unit has its own issue before dispatch | Before each dispatch |

---

## Definition of Done

- U1-U9 each landed as a green commit on `fix/remaining-fidelity-gaps`, and each unit's issue is closed.
- R1-R18 hold, and AE1-AE5 each have a named test.
- Every in-scope item of the 2026-10-11 audit is faithful or a recorded departure.
- No dead or experimental code from abandoned approaches remains.
- CLAUDE.md lists this plan as landed, and a brainstorm basis is the newest file in `docs/plans/`.
- A code review ran over the branch, its fixable findings are fixed, and a PR is open.

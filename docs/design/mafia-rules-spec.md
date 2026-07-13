# Mafia Rules Spec

## Research/BASIC references

The rules in this document are derived from `../research/` and must be checked against cited BASIC line blocks before implementation changes.

Key references:

- `../research/src/decompiled_basic/mf-prg.bas` — authoritative BASIC implementation.
- `../research/research-data/pass-2/systems-analysis.yaml` — combat, economy, wanted, and location systems.
- `../research/research-data/pass-2/game-logic.yaml` — RNG tables, combat AI, score and rank formulas.
- `../research/research-data/pass-2/location-handlers.yaml` — per-option behavior and guards.
- `../research/research-data/pass-2/data-structures.yaml` — map/combat grids, variables, door placement.
- `../research/research-data/pass-1/location-extraction.yaml` — menu trees.
- `../research/docs/systems/*.md` — human-readable summaries.

Do not invent behavior when these sources disagree or are unclear; read the BASIC block.

## Map/movement facts

- City map: 40×25 grid, 1000 cells, row-major `index = row * 40 + col`.
- Walkable street cell code: `156` (`peek(p)=156`, BASIC 2035).
- Door tiles use distinct code `160`; the `lc` routine (BASIC 2050) maps a door address to `(la, ln)`.
- Do not conflate street cells and door cells: movement happens on `156`; location entry resolves adjacent `160` doors.
- Movement deltas: `-1`, `+1`, `-40`, `+40`.
- Leaving a location costs `ms -= 5`.
- Special city cells:
  - `569` — cash-transport robbery event flow (`la=13`);
  - `861` — mayor hit event flow (`la=14`);
  - `911` — jail cell position after arrest.
- City map coordinates and combat coordinates are different spaces. The city grid is 40×25; combat grid is 40×13.
- Original map data comes from reversed binary `../research/src/karte`: 1000 screen codes, 1000 color bytes, and 3 tail bytes, streamed reversed as `screen[999-k]=file[k]`.
- The reusable decode exists at `../research/tools/render_c64_assets.py::parse_screen_file`; use it to emit committed runtime `city.yaml` data. Runtime should not parse the binary.
- Door-to-`(la, ln)` placement is decoded in `data-structures.yaml` around lines 789–890.

## Turn system and movement points

- Players take complete turns in order; `sp` cycles from `0` to `sz-1`.
- Movement points `ms` replenish each turn from vehicle speed: `ms = speed(vehicle)` (BASIC 1012).
- Actions cost `ms`; the turn ends when `ms <= 0`.
- `ms` is also a control-flow signal. Some handlers set `ms = 0` to force turn end, such as taking a job or lawyer acquittal.
- Default action costs are configurable: map step `1`, enter/exit location `5`, enter/exit combat `10`. Verify against BASIC before finalizing.

## Locations

Twelve menu-driven locations plus two map-triggered events:

| `la` | Key | Location |
|---:|---|---|
| 1 | `slw` | Schlupfwinkel / motel |
| 2 | `pub` | Pub / bar |
| 3 | `waf` | Waffenladen / gun shop |
| 4 | `aut` | Automobil / car dealer |
| 5 | `kdh` | Kredit-Hai / loan shark |
| 6 | `sph` | Spielhölle / casino |
| 7 | `sgl` | Laden / racket |
| 8 | `sub` | Subway |
| 9 | `bhf` | Bahnhof / station |
| 10 | `ban` | Bank |
| 11 | `pol` | Polizei |
| 12 | `ble` | Blüten-Eddie / forger |
| 13 | — | Cash transport, map cell 569, no menu |
| 14 | — | Mayor hit, map cell 861, no menu |

Each menu location resolves an option to leave, a handler, or a sub-menu. The only real submenus are casino game choice and racket follow-up; model both as in-handler `PromptChoice` interactions rather than a generic nested menu tree.

## `ln` semantics

`ln` is the within-location tile index, values 1–9. It is a first-class handler input, not an option index.

Known `ln` effects include:

- rent price through `fnm(ln)`;
- which pub serves alcohol (`ln in {4, 5}`);
- racket outcomes (`ln in {1, 4, 5}`);
- reachable motel negative-rent behavior for `ln=1`.

Handlers must receive and preserve the resolved `ln`.

## Combat formulas

Combat is a core engine system, not a minigame.

- Grid: 40×13, flat indices `0..520`; row is `int(pos / 40)`.
- Gangster energy initializes to `5`.
- Energy restoration each turn: `en = en + int(kr / 10) + 1`.
- Energy cap: `en = 2 + int(kr / 4) + int(bt / 4)`.
- Weapons: 9 total from BASIC `DATA 50100–50115`, with price, accuracy `ts`, damage `tg`, and sound `ws`, ranging from hands (`$0`, `ts=2`, `tg=2`) to hand grenades (`$10000`, `ts=7`, `tg=18`).
- Range: base `2`; `15` if weapon index > 3; `20` if weapon index is 6 or 7.
- Hit misses if either `int(rnd * ts) = 0` or `int(rnd * (kr / 10 + 1)) = 0`.
- Damage on hit: `y = int(rnd * tg + bt / 10) + 1`; damage is always at least 1 on hit.
- AI from BASIC 30400–30492:
  - step toward nearest enemy;
  - melee weapons, weapon index < 4, or a 50% roll force approach behavior;
  - use per-fighter direction memory `ri()` to prevent oscillation;
  - check collision and bounds before movement.
- Combat ends when one side is dead or surrender occurs.
- The result is exposed to the calling handler as a flag equivalent to BASIC `s`, represented by `CombatResult(outcome="won" | "lost" | "surrendered", survivors, loot)`.

## Economy formulas

- Bank daytime hold-up requires a tip target.
- Bank night break-in requires `in >= 40`, `kr >= 15`, and `bt >= 20`, plus safe-cracking minigame.
- Bank payout: `p = int(rnd * 3000) + 4000 - 500 * direct`; add `3000` bonus for postzug/cash-transport.
- Casino gambling supports poker, blackjack, and roulette.
- Gambling win condition: `int(rnd * (1 + x)) = 0`, where `x` is game index 1–3.
- Gambling gross payout: `p = int(p * (0.5 + x))`; stake is already deducted. All three games are negative expected value.
- Rewards include jobs, extortion, pickpocket loot tables, debt collection, jail-break bribe, and croupier cheat; formulas are documented in `game-logic.yaml`.
- Training at shooting range and training camp raises gangster stats by `fnr = int(rnd * 8) + 8`, i.e. 8–15, capped at 99, and adds to score.
- Loans allow borrowing 0–5000 dollars when no existing debt exists; debt must be repaid within 6 months.
- Loan-shop ownership/capitalization is a separate business system.

## Score, rank, and win rules

- Score variable `gf` ranges 0–100.
- Score updates at about 25 sites via `gf += x * x8`, where `x` is a hand-tuned event delta and `x8` is the setup score-gain weighting from `punktewertigkeit` (BASIC 175).
- There is no `×8` score factor. `x8` is the score weight; `x9` is the end year.
- Rank `ra` ranges 1–10 and is computed as `nr = int(gf / 11.1) + 1`; promotion occurs when the computed rank changes.
- Win flags:
  - `x5%` — cash-transport success;
  - `x6%` — mayor hit.
- Early win: rank 10 plus both win flags.
- Year-end win: when `int(ja) = x9`, highest `gf` wins.

## Wanted, police, and jail rules

- Street stop: 1-in-5 chance when `ra > 3` and `ms / 20` is an integer, leading to police combat.
- When caught, the player may bribe, escape, or surrender.
- Bribe cost: `500 + 500 * ra`; a 1-in-5 chance still results in jail.
- Escape succeeds when `int(rnd * tr / 11) = 0`.
- Surrender jail time: `gs = int(ra / 2 + .5)` months.
- Jail position: map cell `911`.
- Lawyer option exists if `ra >= 5`; paying 0–10000 dollars reduces months.
- Jail-break option is available at the police station.

## Known quirks to preserve

- Motel rent formula: `fnm(ln) = 50 - 50 * (ln = 3 or 4) - 100 * (ln = 1)`.
- `fnm(1) = -50` is a reachable negative-rent quirk because `slw` has a real `ln=1` tile. Port this verbatim.
- `ms = 0` from handlers is a deliberate forced-turn-end signal.
- Door tiles (`160`) and street tiles (`156`) are distinct and must not be merged.
- `ln` changes handler behavior and must not be treated as display-only or as a menu option index.

# ble — Blueten-Eddie (la=12)

## 1. Header

- **Lines:** `mf-prg.bas:22000-22120` (dispatcher `:22005 onwgoto22010,22100`; passports `:22010-22020`; counterfeit `:22100-22120`).
- **Entry:** door cell 371 (`la=12, ln=1`, content/map/city.yaml:176) → `:2055 gosub3000` → SEQ `src/ble` (3 options, `aw=3`) → `:3110 onla-10goto21000,22000,...` → `:22005`.
- **State on entry:** `la=12`, `ln=1`, `w` = chosen option. No rank / re-entry check.
- **Exit:** handlers return into `:2055 ll(sp)=20*la+ln` → `:2060 ms=ms-5`. Leave: `:3045 ms=ms-5` extra.

## 2. Research coverage verdict

**Mostly covered, with gaps.** Menu + response strings are in location-extraction.yaml / location-dialogue.yaml (ble). Gaps/contradictions:

- location-handlers.yaml `"22010"` "Buy a passport per gangster (1000 $ each) — grants fake-papers flag … to evade the wanted system" — correct price; the *effect* (roadblock only, `:6018`) is not described anywhere except wanted-system.md's one line. There is no "wanted level" in the BASIC at all.
- location-handlers.yaml `"22100"` "Invest 0-5000 $ real cash for a larger sum in counterfeit bills" — correct in spirit; the payout formula `:22110` and the downside (roadblock `:6016` capture 2/3, priority over the passport) are undocumented.
- location-dialogue.yaml ble opt 2 `delegates_to: 23000` — spurious (`:22120 return`).
- location-dialogue.yaml ble opt 1 omits `:22011` "fuer einen pass".
- Decay of both marks (`:4055-4056`, 1/8 per turn each) is not documented in research docs/systems (see roadblock dossier).

## 3. Menu options and guards

| # | Label (SEQ) | Shell guard | Handler |
|---|---|---|---|
| 1 | 'YEAH, KANNST DU MIR EINEN NEUEN PASS MACHEN? …' | none | `:22010` |
| 2 | 'DRUCKST DU NOCH BLUETEN? …' | none | `:22100` |
| 3 | 'DANKE, EDDIE, VIELLEICHT BEIM NAECHSTEN MAL. BYE!' | none | leave (`:3045`) |

No DSL guards needed.

## 4. Behaviour walk

### Option 1 — passports (`:22010-22020`)
1. `:22010` `x=gz(sp)` (roster size incl. boss, ≥1); `p=1000*x`. Print "gut, mein sohn. ".
2. `:22011` `x=1` → "fuer einen pass"; else `:22012` "fuer `x` paesse".
3. `:22013` "macht das `p` $. " + `:1110` "ok (j/n)?" (only j/n accepted, `:1115`). "n" → return (no pause).
4. `:22014` `ka(sp)<p` → `:1125` "du hast zu wenig kies!" + pause → return.
5. `:22015` "hier, noch druckfrisch, he, he!"; `:22020` `ka-=p`; `ag(sp)=ag(sp) or 1` (passport bit, value 1); `x=1:gosub1160` → `+1*x8` score; `:1100` pause; return.
6. Re-buying while already holding the bit is allowed and charged again (bit is idempotent).
7. No RNG. Price counts every gangster, but the mark is one per player (`ag(sp)`), not per gangster.

### Option 2 — counterfeit money (`:22100-22120`)
1. `:22100` "nng...hoechst ungern! wieviel dollar"; `:22105` numeric `INPUT "willst du anlegen (0-5000)";q`.
2. `q<=0` → return (no pause).
3. `:22106` `q>5000 or q>ka(sp)` → cursor up, re-prompt (loop until valid or ≤0).
4. `:22110` RNG `p=int(rnd(1)*q/2)+q+100` → `p ∈ [q+100, q+100+ceil(q/2)-1]`, uniform over `int(rnd*q/2)` (last bucket partial when `q` odd). Print "ich gebe dir `p` $ blueten dafuer." + blank line.
5. `:22115` `:1110` "ok (j/n)?"; "n" → return (the draw is consumed, nothing changes).
6. `:22120` `ka(sp)=ka(sp)-q+p` (net gain `p-q` = 100 … 100+q/2); `ag(sp)=ag(sp) or 2` (counterfeit bit, value 2); `x=1:gosub1160` → `+1*x8`; `return` (no pause).
7. Fractional `q` (e.g. 0.5) passes `q>0` → `ka` becomes fractional (`+100.5`). See Q2.
8. Downside lives elsewhere: roadblock `:6016` checks bit 2 **before** alcohol and passport → caught (2/3 of roadblocks, the 1/3 free pass `:6015` is rolled first). Decays 1/8 per upkeep (`:4056`).

### Leave
`:3045 ms=ms-5` + `:2060 ms=ms-5`.

## 5. State table

| BASIC | Meaning | Port name |
|---|---|---|
| `ka(sp)` | cash | `Player.ka` / `MoneyChange` |
| `gz(sp)` | gang size | `len(Player.roster)` |
| `ag(sp) and 1` | passport ("papiere", DATA `:50600`) | `Contraband.fake_papers: int` (exists, unused) — NEW effect (set/clear) |
| `ag(sp) and 2` | counterfeit ("falschgeld") | `Contraband.counterfeit: int` (exists, unused) — NEW effect |
| `gf`/`nr` via `:1160` | score | `ScoreAndRank(1, rank_divisor)` |
| `q`, `p`, `x` | locals | handler locals |

`ag` is a 2-bit field; `ag$(0..1)` = "papiere","falschgeld" (`:126`, `:50600`) are shown in the overview `:1221`. Recommend modelling as two bools (the port's `int` fields work as 0/1).

## 6. Dependencies

- Roadblock body (`roadblock-and-marks.md`) is the only consumer of both bits (`:6016`, `:6018`); mayor hit sets bit 1 (`:24020`); gang war transfers bit 1 (`:27035`). Without the roadblock, ble has no downside and no upside.
- Upkeep `:4055-4056` decay (upkeep.py explicitly skips them today, upkeep.py:50-51).
- `FlagSet` is global-only (engine/effects.py:265) — a per-player contraband effect is NEW (e.g. `ContrabandSet(fake_papers=…, counterfeit=…)`).
- Overview screen (`:1220-1222`) — no port equivalent yet (turn menu option 1).

## 7. Open questions

1. Passport price uses `gz(sp)` including the boss (`:22010`) — confirm port charges `1000*len(roster)`.
2. Fractional/odd investment `q` (`:22105` has no integer check) — port as integer-only input or allow floats? Recommend integer input (behavioural bar), record deviation.
3. Empty INPUT at `:22105` keeps the old `q` on C64 (value from any earlier use of `q`, e.g. another location) — VICE check; recommend treating empty as 0 (return).
4. Should `ag` be two booleans or keep the bitfield int? (Port has two `int` fields already.)

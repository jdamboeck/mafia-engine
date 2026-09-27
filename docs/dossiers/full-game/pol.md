# pol — Polizei-Praesidium (la=11)

## 1. Header

- **Lines:** `mf-prg.bas:21000-21255` (dispatcher `:21005`, bribe chief `:21010-21030`, free inmate `:21100-21255`); option 1 jumps into the capture chain at `:26045` (see `police-capture-and-jail.md`).
- **Entry:** map door cell 910 (`la=11, ln=1`, content/map/city.yaml:175) → `:2050` `syslc` → `:2055 gosub3000` → SEQ `src/pol` menu (4 options, `aw=4`) → `:3040` read `w` → `:3110 onla-10goto21000,...` → `:21005 onwgoto26045,21010,21100`.
- **State on entry:** `la=11`, `ln=1`, `w` = chosen option; `ms` already reduced by nothing yet — `:2060 ms=ms-5` runs **after** the handler returns (port charges it at entry, engine/movement.py:276). `x` still holds the last map delta (`:2015-2018`: ±1/±40) — matters for the empty-INPUT case in Q3.
- **Exit:** every option returns into `:2055 ll(sp)=20*la+ln` → `:2060 ms=ms-5`. Leave (`w=aw=4`) takes `:3045 ms=ms-5:return` (an **extra** −5 on top of `:2060`).

## 2. Research coverage verdict

**Gaps + contradictions.** The menu text is complete (location-extraction.yaml pol; location-dialogue.yaml pol). The behaviour summaries are partly wrong:

| Research claim | BASIC | Verdict |
|---|---|---|
| "Bribe the guards (3000-5500 $)" (location-handlers.yaml `"21100"`) | `:21130 p=500*int(rnd(1)*5)+3000` → 3000..5000 | **wrong** (max 5000) |
| "break out one of your jailed gangsters" (location-handlers.yaml `"21100"`) | `:21100` lists **players** `i=1..sz` with `gs(i)<>0` | **wrong** — jailed *players*, plus the phantom |
| jail_break "success: 1/3 chance if ra(sp)>4" (systems-analysis.yaml:184) | `:21105` the 1/3 & `ra>4` only **adds the phantom inmate**; the break itself never fails once paid (`:21140-21200`) | **wrong** |
| jail_break "cost: pay y dollars, ka(x) reduced, ka(sp) increased" (systems-analysis.yaml:185) | that is the freed player's voluntary thank-you (`:21252-21255`), not the cost | **mislabelled** |
| guard "someone must be jailed (gs)" (location-handlers.yaml) | no guard; `:21110` prints "es ist niemand inhaftiert." inside the handler | **not a guard** |
| 21010 "buy inactivity/protection" (location-handlers.yaml) | effect is `pl(sp)=pl(sp)+x+1` only; what `pl` *does* is `:26021` — not documented anywhere in research | **gap** |
| "Surrender … fine or jail time based on rank" (location-handlers.yaml `"26045"`) | no fine exists; jail months `int(ra/2+.5)`, lawyer optional | **wrong** (no fine) |
| pol opt 3 `delegates_to: 22000`; ble opt 2 `delegates_to: 23000` (location-dialogue.yaml) | `:21255 return`, `:22120 return` — no fall-through | **spurious** |
| edge-cases.yaml:21 "911 jail; exit via pol bestechung" | `:21030 po(sp)=911` — the chief bribe *moves the briber* to 911 ("hinterausgang"); it frees nobody | **misleading** |
| location-dialogue pol responses | missing `:21015`, `:21110`, `:21105` "irgendjemanden", `:21205`, `:26070` | **gap** |

## 3. Menu options and guards

SEQ `src/pol`: title "POLIZEI-PRAESIDIUM", prompt, `aw=4`.

| # | Label (SEQ) | Shell guard | Handler line |
|---|---|---|---|
| 1 | 'ICH BIN EIN GESUCHTER GANGSTER! …STELLEN.' | none | `:26045` (capture chain, sentence) |
| 2 | POLIZEICHEF BESTECHEN | none | `:21010` |
| 3 | VERSUCHEN, INHAFTIERTEN GANGSTER ZU BEFREIEN | none | `:21100` |
| 4 | LIEBER GAR NICHTS TUN | none | leave (`:3045 ms=ms-5`) |

No rank gate, no wanted gate, no `ll(sp)` re-entry check (unlike ban `:20004` / sgl `:17007`). All refusals happen inside handlers → no DSL guard needed; nothing requires NOT.

## 4. Behaviour walk

### Option 1 — surrender (`:21005` → `:26045`)
1. Jumps straight to `:26045` — no bribe/flee menu, no wanted check (a clean player may surrender).
2. Full sentence/lawyer flow as in `police-capture-and-jail.md` §4.C (steps C1-C8): `tp(sp)=0`, `+2` score, `gs=int(ra/2+.5)`, lawyer if `ra>=5`, then either acquittal (`:26070`, `ms=0`) or `:26080` (`ms=0`, `jo=0`, `−10` score, `po=911`).
3. Return path: `:26070`/`:26080 goto1100` → press-key → `return` closes `gosub3000` → `:2055` → `:2060 ms=ms-5` (ms goes to −5) → `:2065 return` → `:1045` ms≤0 → `:1050 goto1010` next player.

### Option 2 — bribe the chief (`:21010-21030`)
1. `:21010` print "aha! fuer einen monat untaetigkeit" (location-dialogue pol opt2 `:21010`).
2. `:21011` numeric `INPUT "nehme ich 1000 $. wieviele monate:";x` — no range check. `p=1000*x`.
   - `x=0` → `return` (no message, no pause).
3. `:21015` `ka(sp)<p` → "sie sind leider nich fluessig!" (`:21015`) → `:1100` pause → return. No state change.
4. `:21020` `ka(sp)-=p`; `pl(sp)+=x+1` (the `+1` pre-pays the `:4050` decrement that runs at this player's next upkeep); `x=2:gosub1160` → score `+2*x8`, clamp, `nr` recompute.
5. `:21025-21030` "danke sehr! nehmen sie den hinter-" / "ausgang!"; `po(sp)=911` (teleport to the street cell right of the station, the same cell a convict is put on); `:1100` pause; return.
6. No RNG. `ms` unaffected here (only `:2060`).
7. **Validation holes (faithful behaviour):** negative `x` → `p<0` passes `ka<p`, cash *increases* by `1000*|x|`, `pl` decreases (`pl+=x+1`); fractional `x` (e.g. 1.5) accepted → `p=1500`, `pl+=2.5`. See Q2/Q3.
8. **What `pl` buys** (only reader besides display `:1225` and aging `:4050`): `:26021` — on capture, 50 % chance to skip the bribe/flee/surrender menu and jump into the bribe-payment line `:26037` with a **stale `p`** (see `police-capture-and-jail.md` §4.B0 and Q1 there). It is *not* immunity.

### Option 3 — free a jailed inmate (`:21100-21255`)
1. `:21100` build list `g(1..)`: every player `i=1..sz` with `gs(i)<>0` (the acting player is never jailed while acting, so effectively "other jailed players").
2. `:21105` RNG `int(rnd(1)*3)=0` (p=1/3) **and** `ra(sp)>4` → append phantom entry `g(x)=5`, `sp$(5)="irgendjemanden"`. (BASIC `and` is not short-circuit: the draw happens every time.)
3. `:21110` list empty → "es ist niemand inhaftiert." → pause → return.
4. `:21115-21116` "wen willst du befreien:" then numbered list `i sp$(g(i))`.
5. `:21120` `GET` single key; re-read while empty or `val(x$)>count`. `:21125 g=val(x$)`; `g=0` (key "0" **or any non-digit**) → return (no pause).
6. `:21130` RNG `p=500*int(rnd(1)*5)+3000` → {3000,3500,4000,4500,5000} each 1/5. Print "du brauchst p $, um die waerter zu bestechen." + `:1110` "ok (j/n)?".
   - "n" → return.
   - `ka(sp)<p` → `:1125` "du hast zu wenig kies!" + pause → return.
7. `:21140` `ka(sp)-=p`. `:21200` "du konntest den gefangenen befreien!" + pause. **No failure roll exists.**
8. Phantom chosen (`g(g)=5`):
   - `:21205` `gz(sp)=10` → "er bedankt sich und verschwindet..." + pause → return (money spent, nothing gained).
   - else `:21210` append gangster: `gz+=1`, name "knasti", weapon `gw=0` (haende), `ge$="05100540"` → energie 5, kraft 10, intelligenz 5, brutalitaet 40. `:21215 return`. **No score**, no `sg()` mark.
9. Real player `x=g(g)` chosen:
   - `:21250-21252` screen addressed to the freed player: "`sp$(x)`! du bist von `sp$(sp)` aus dem knast befreit worden. wieviel zahlst du ihm zum dank (0 - `ka(x)`):" — `INPUT x$` typed by **player x** (hot-seat).
   - `y=val(x$)`; `y>ka(x)` → redraw whole screen (`goto21250`); `:21253` `y<0` → re-prompt. Fractional `y` accepted. Non-numeric → 0.
   - `:21255` `ka(x)-=y`, `ka(sp)+=y`, `gs(x)=0`, `x=2:gosub1160` → rescuer `+2*x8`. `return` (no pause). The freed player's `po` stays 911; `tp`, `ag`, `pl` untouched.

### Leave (`w=4`)
`:3045 ms=ms-5:return`, then `:2060 ms=ms-5` → leave costs 10 total vs. 5 for any action.

## 5. State table

| BASIC | Meaning | Port name |
|---|---|---|
| `ka(sp)`, `ka(x)` | cash | `Player.ka` / `MoneyChange(player=)` |
| `gf`, `nr` via `gosub1160` | score, pending rank | `Player.gf`,`Player.nr` / `ScoreAndRank(amount, rank_divisor)` |
| `pl(sp)` | chief-bribe months | `Wanted.bribe_months` (exists, unused) — NEW effect needed |
| `gs(i)` | jail months | `Wanted.jail_months` (exists) — `Jail` effect is a stub (engine/effects.py:365) |
| `po(sp)` | map cell | `Player.po` / `Teleport(911)` |
| `gz(sp)`, `gn$`, `gw`, `ge$` | roster | `len(Player.roster)` / `RosterAppend(Gangster(...))` |
| `sz` | player count | `Clock.player_count` |
| `sp$(i)` | player name | `Player.name` |
| `sp$(5)="irgendjemanden"`, `g()`, `g`, `p`, `y` | locals | handler locals; phantom name → theme string |
| `ra(sp)` | rank | `Player.rank` |
| `ms` | movement | `Player.ms` (only via `:2060`/`:3045`) |

## 6. Dependencies

- Capture/sentence chain (`police-capture-and-jail.md`) for option 1 — needs `Jail` effect built, `Teleport`, `JobClear` (exists), `TipClear` (exists).
- Turn loop must skip jailed players (`:1013`) for option 3 to have targets.
- Hot-seat prompt addressed to a non-active player (`:21252`) — the interaction protocol has no "player" field on prompts today; the terminal client is hot-seat so it works, the future network transport needs a target.
- `RosterAppend` exists; cap check `gz=10` mirrors pub (`formula_params` recruit cap).
- Engine currently charges no `:3045` leave cost (content/locations/*.yaml leave = `ms_change 0`); pol/ble leave should decide consistently (see Q5).

## 7. Open questions

1. **`pl` semantics** — the only effect is `:26021` → `:26037` with stale `p` (see capture dossier Q1). Faithful port reproduces an arbitrary price; decide: port stale-`p` literally per entry path, or model intent.
2. **Negative / fractional month input at `:21011`** gives money and lowers `pl`. Port faithfully (exploit) or validate `x>=1` integer? Evidence: no range check `:21011-21015`.
3. **Empty RETURN on numeric INPUT** (`:21011` `x`): on C64 BASIC V2 an empty INPUT leaves `x` unchanged — here the last map delta (±1/±40), so `x=-40` would pay out 40000 $. Needs a VICE check before deciding; recommend treating empty as 0.
4. Phantom inmate: no score on success, and freeing at `gz=10` wastes the money — confirm faithful port (it is what `:21205` does).
5. Leave cost: `:3045` charges −5 on leave; existing port leaves charge 0. Verify and decide globally (affects all 12 locations, not only pol).
6. Free-inmate list excludes nobody by relationship — a player may free a rival (who then pays a voluntary tip). Faithful; just note it for UX.

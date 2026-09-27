# Police capture, trial and jail

## 1. Header

- **Lines:** police fight `mf-prg.bas:26000-26015`; capture menu `:26020-26043`; sentence/trial `:26045-26080`; jail turn `:1013` + `:1500-1515`; helpers `:1100` (press key), `:1110/1115` (j/n), `:1125` (no money), `:1160-1165` (score+rank).
- **Three entries:**

| Entry | Jumped from | State set by caller |
|---|---|---|
| `:26000` police **fight** first | sgl `:17009` (re-entry, "poliyei!"), sgl `:17020` (extortion, owner calls police), ban `:20142` (safe-crack fail) — all `goto26000` with `kf$` set ("ks"/"ks"/"kb") | `kf$` only |
| `:26020` **caught** menu | roadblock `:6020/:6030/:6036`; aut `:14125`; sub `:18042`; bhf `:19030`; ban `:20015`; cash transport `:23025`; mayor `:24005/:24010` (all `ifs=2goto26020` or direct `goto`); fall-through from `:26015` when the police fight is lost | nothing (note: `p` is **stale**, see B0) |
| `:26045` **surrender/sentence** | pol option 1 `:21005`; bribe declined `:26036`; bribe unaffordable `:26037`; bribe paid but 1/5 `:26038`; failed flight falls through `:26042-26043` | nothing |

- All are reached by `goto` from inside a `gosub` (map `:2041 gosub6000`, `:2045/2046 gosub23000/24000`, `:2055 gosub3000`). Every exit is `goto1100` (press key + `return`), which closes that outer gosub → back at `:2060 ms=ms-5` (map loop).

## 2. Research coverage verdict

**Covered in outline, several contradictions, key gap on `pl`.**

| Research | BASIC | Verdict |
|---|---|---|
| escape "Success if int(rnd*tr(sp)/11)=0" (systems-analysis.yaml:167; game-logic.yaml:110 "escape …==0") | `:26040` `=0` → **caught** (`x=-5`, `:26042`); non-zero → escaped `:26041` | **inverted** |
| game-logic.yaml:109 "26038: bribe success int(rnd(1)*5)==0 branch" | `=0` → `goto26045` (jail despite paying) | **inverted label** (systems-analysis.yaml:164 has it right) |
| escape uses `tr(sp)` "vehicle range" (implied) | `tr()` is indexed by **vehicle** everywhere else (`:1012 tr(tm(sp))`), here by **player number** `sp` | **undocumented source bug** |
| surrender "gf change x=2 (score penalty)" (systems-analysis.yaml:172) | `:26045 x=2:gosub1160` is **+2**; the penalty is `:26080 x=-10` | **wrong sign/placement** |
| lawyer "code: Lines 21010-21050", "Pay 0-10000$ (random)" (systems-analysis.yaml:177-181) | lawyer is `:26055-26072`; amount is **player-chosen**; 10000 cap is only in the prompt text, not enforced (`:26061`) | **wrong** |
| wanted-system.md / systems-analysis.yaml: no mention of `:26021` | `ifpl(sp)andint(rnd(1)*2)<>0goto26037` | **gap** (whole effect of chief bribe) |
| no mention of acquittal skipping `:26080` | `:26070 … ms=0:goto1100` bypasses `jo=0`, `−10`, `po=911` | **gap** |
| edge-cases.yaml:120 "Jail months 1,1,2,2,3,3,4,4,5,5" | `int(ra/2+.5)` for ra=1..10 → 1,1,2,2,3,3,4,4,5,5 | **correct** |
| game-flow.yaml:141 `1013 -> 1500` jail serve | correct; order vs upkeep/job not stated | **gap** (ordering) |

Nothing in research describes what capture does to money/gang/weapons/rank (answer: almost nothing — §4.E).

## 3. Menu (capture menu `:26022-26030`, not a location)

| Key | Label | Line | Guard |
|---|---|---|---|
| 1 | polizisten bestechen | `:26035` | none |
| 2 | fluchtversuch | `:26040` | none |
| 3 | ergeben | `:26045` | none |

Input `:26025` `GET` loop until "1".."3". Skipped entirely by `:26021` (below). No DSL guards.

## 4. Behaviour walk

### A. Police fight `:26000-26015`
A1. `bn$(0)="die polizei"`; RNG `gz(0)=5+int(rnd(1)*ra(sp)/2)` → ra 1-2: 5; ra 3-4: 5-6; ra 5-6: 5-7; ra 7-8: 5-8; ra 9-10: 5-9. For odd `ra` the top value has the reduced weight `0.5/(ra/2)` (e.g. ra 3: P(6)=1/3).
A2. `w=5-2*(ra(sp)>5)` → revolver (5) at ra≤5, gewehr (7) at ra≥6 (C64 true=−1).
A3. RNG `e=20+2*(ra(sp)-1)-int(rnd(1)*21)` → one roll for the whole squad: ra 1 → 0..20; ra 10 → 18..38. **e can be 0** (fighter dies to any hit).
A4. `gosub5000` → all `gz(0)` police get `gw(0,i)=w`, `ec(i)=e`; `ks(1)=sp, ks(2)=0` (AI); `goto30000` on grid `kf$` set by caller.
A5. `:26015` `s=1` (player won) → `x=2:gosub1160` (+2) → `return` (closes the outer gosub; turn continues at `:2060`).
A6. `s=2` → falls into `:26020`.

### B. Caught `:26020-26043`
B0. `:26020` print "du bist von der polizei gefasst worden." (always).
`:26021` `if pl(sp) and int(rnd(1)*2)<>0 goto26037` — the draw is made every time (no short-circuit). With `pl(sp)>0`: p=1/2 → skip the menu and go to the **payment line** `:26037` using whatever `p` last held (no price is printed):
  - after a roadblock (`:6020/6030/6036`) or sub (`:18042`): last `p` is the map step `:2030 p=br+po(sp)+x` = 52224 + cell → ≈52224..53223 $ → nearly always `ka<p` → "zu wenig kies" → sentence.
  - after a lost fight (every other entry): last `p` is the combat cell of the last fighter killed (`:30220-30310`, 0..520), or a direction/0 from AI movement (`:30450-30492`) or a player move `br+kp+x` if the player quit with `q` (`:30136`, `:30140`).
  - `pl(sp)=0` or the 1/2 fails → normal menu.
B1. **Bribe (1)** `:26035` `p=500+500*ra(sp)` (1000..5500). Print "sie verlangen `p` $." + `:1110` j/n.
  - "n" → `x=2:gosub1160` (+2) → `:26045`.
  - `:26037` `ka<p` → `:1125` "du hast zu wenig kies!" + pause → `:26045`.
  - `:26038` `ka-=p`; RNG `int(rnd(1)*5)=0` (1/5) → `:26045` (money lost). Else (4/5) `:26039` "die bullen lassen dich gehen!" → pause → return. **No score, `tp` kept, `ms` untouched.**
B2. **Flee (2)** `:26040` RNG `int(rnd(1)*tr(sp)/11)=0` → caught. `tr` is the vehicle-range table indexed by the **player seat** (`sp`=1..4 → tr(1..4)=35,40,40,60, DATA `:50300-50305`), so P(caught)=11/35=31.4 % (seat 1), 11/40=27.5 % (seats 2,3), 11/60=18.3 % (seat 4); independent of the player's actual vehicle.
  - caught: `x=-5:gosub1160` (−5) → `:26042-26043` "die polizisten schnappen dich / du musst dich ergeben!" + pause → falls into `:26045`.
  - escaped: `:26041` "du bist gerade noch einmal entkommen!" `x=2:gosub1160` (+2) → pause → return (`tp` kept).
B3. **Surrender (3)** → `:26045`.

### C. Sentence `:26045-26080`
C1. `:26045` `tp(sp)=0` (any heist tip is lost); `x=2:gosub1160` (+2); `gs(sp)=int(ra(sp)/2+.5)` = ceil(ra/2): 1,1,2,2,3,3,4,4,5,5. **Overwrites** any previous `gs`.
C2. `:26050` "deine gerichtsverhandlung..."; `ra(sp)<5` → C6 (no lawyer offered).
C3. `:26055` "willst du einen anwalt (j/n) ?" + `:1115` (j/n key); "n" → C6.
C4. `:26060` `INPUT "wieviel legst du an (0-10000$)";x$` → `x=val(x$)`; `x=0` (incl. non-numeric) → C6. `:26061` `x>ka or x<0` → re-prompt. **No upper cap of 10000**; fractions accepted.
C5. `:26062` `ka-=x`; RNG `y=int(rnd(1)*(x/1000+1))+1`. With `M=x/1000+1`: `y-1` uniform over `0..floor(M)-1` with prob `1/M` each, plus value `floor(M)` with prob `frac(M)/M`. Any `x>0` gives `y≥1` (1 $ buys a 1-month cut). `:26065` `gs-=y`, floor 0.
  - `gs=0` → `:26070` "du wirst freigesprochen!" `ms=0` → pause → return. **Skips `:26080`:** no `−10`, `jo` kept, `po` unchanged.
  - else `:26071-26072` "dein anwalt boxt eine strafe von nur `gs` monat(en) heraus." → C7.
C6. `:26075` "du wanderst fuer `gs` monat(e) hinter gitter!"
C7. `:26080` `ms=0` (turn ends); `jo(sp)=0` (job lost); `x=-10:gosub1160` (−10); `po(sp)=911`; pause; return.
C8. Return lands at `:2060 ms=ms-5` → `ms<0` → `:2065 return` → `:1045` → `:1050 goto1010` (next player).

Net score per path (units of `x8`, each step clamped to [0,100] in sequence): surrender −8 (acquitted +2); bribe declined −6 (acq. +4); bribe unaffordable/1-in-5 −8; bribe ok 0; flight caught −13 (acq. −3); flight ok +2; police fight won +2.

### D. Serving the sentence — turn start `:1010-1013`, `:1500-1515`
D1. Order each turn: `:1010` rotate (+standings/year-end) → `:1011 gosub4000` **upkeep runs in full for a jailed player** (energy regen, rank commit incl. demotion, rent, pl aging, ag decay, arms deal; only the loan-shark block `:4040` is skipped while `gs>0`, freezing its `kz` countdown) → early-win check → `:1012` `ms=tr(tm)`, `nr=ra`, `ll=0`, job shift if `jo` (never set for a convict) → `:1013` score truncation, then `ifgs(sp)thengosub1500:goto1010`.
D2. `:1500` `gs(sp)-=1`; `:1510-1515` "du sitzt im knast. `gs+1` monat(e) hast du noch vor dir..." (shows the pre-decrement value) → pause → back to `:1010` (turn skipped: no menu, no map).
D3. A sentence of N skips exactly N turns; the player resumes at `po=911`.
D4. Early release: pol option 3 by another player (`:21255 gs(x)=0`); sentence lengthened by gang-war prison brawl (`:27145`, +1..2).

### E. What capture does NOT touch
Cash (except bribe/lawyer), gang size, weapons, gangster stats (fight damage already persisted by combat), `ag` marks (so a counterfeit mark survives jail and can re-trigger), `pl`, `kr` debt, `ta` alcohol (only the roadblock `:6036` confiscates it), `tm` vehicle, `x5%/x6%`. Rank changes only indirectly: `gf` drops → `nr` recomputed at `:1165` → committed (possibly **down**) at next `:4030` with the promotion screen `:4200`.

## 5. State table

| BASIC | Meaning | Port name |
|---|---|---|
| `gs(sp)` | jail months | `Wanted.jail_months` (exists) / `Jail(months)` stub (engine/effects.py:365) → build as SET, not add |
| `pl(sp)` | chief-bribe months | `Wanted.bribe_months` (exists, unused) |
| `tp(sp)` | heist tip | `Player.tip_target` / `TipClear` (exists) |
| `jo(sp)` | job | `Player.jobs.type` / `JobClear` (exists, docstring already cites `:26080`) |
| `po(sp)` | map cell | `Player.po` / `Teleport(911)` |
| `ms` | movement | `Player.ms` / `MsChange(-ms)` (pub.py:515 pattern) |
| `ka(sp)` | cash | `MoneyChange` |
| `gf`,`nr` | score, pending rank | `ScoreAndRank` |
| `ra(sp)` | rank | `Player.rank` |
| `tr(sp)` (!) | vehicle range indexed by seat | `vehicles[seat].tr` with seat = active_player+1 (0-based port) |
| `ta(sp)` | alcohol | `Contraband.alcohol_barrels` / `BarrelChange` |
| `bn$(0)`,`gz(0)`,`w`,`e`,`kf$` | police encounter | NEW encounter YAML `police.yaml` (count/weapon/vitality rolled per rank → handler-computed, not static) |
| `p` (stale) | last price/cell | NEW decision — see Q1 |
| `x`,`y`,`x$` | locals | locals |
| `WantedChange` | — | **no BASIC counterpart**: there is no wanted level variable in mf-prg.bas |

## 6. Dependencies

- Combat: `StartCombat` + `setup_combat` exist; police count/energy are rolled per capture (handler builds the encounter). Grids needed: `ks` (exists), `kb` (NEW, ban) — whoever builds sgl/ban.
- Turn loop (clients/terminal/session.py `run_turns`/`next_turn`): must add the `:1013` jail skip **after** upkeep and the job-dispatch check; today no jail skip exists.
- Upkeep: `:4040` debt check must add `gs=0` condition (upkeep.py debt slot).
- Effects to build: `Jail` (set months), bribe-months set/decrement, `Teleport` (exists).
- Hot interaction: capture menu is a `PromptChoice` inside whatever handler triggered it → capture must be a reusable sub-flow (`yield from capture(ctx, …)`) callable from location handlers, map events and the roadblock.
- `ll(sp)` re-entry trap (sgl `:17007`, ban `:20004`) interacts with captures but is owned by those locations.

## 7. Open questions

1. **Stale `p` at `:26021→:26037`.** Faithful = roadblock/sub entries effectively jail, fight entries pay 0..520 $ (80 % free). Options: (a) port literally per entry path (needs the "last p" value from each caller); (b) treat `pl` as "pay the normal bribe `500+500*ra`" silently; (c) free pass. Evidence: `:26021`, `:2030`, `:30220-30310`. Needs a design decision; a VICE run can confirm the roadblock value.
2. **`tr(sp)` seat-indexed flight odds** (`:26040`): port the bug (seat 1 worst) or use `tr(tm(sp))`? Fidelity bar says port it; record as known source quirk.
3. Lawyer amount >10000 accepted (`:26061`) — keep?
4. Acquittal keeps `jo` and position and skips −10 (`:26070`) — faithful, confirm.
5. `:26045` overwrites `gs` — irrelevant in practice (a convict can't act) but `Jail` should be a set.
6. Should the capture sub-flow be an engine helper (shared by ≥10 call sites) or config code? Handler API allows "named engine helpers"; it is game logic → config-level shared module recommended.
7. Demotion on capture happens at next upkeep via `RankCommit`; the existing RankCommit docstring speaks of "promotion" — confirm it handles decreases (it sets unconditionally, so yes).

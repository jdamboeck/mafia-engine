# Dossier: aut — Automobil-Haendler (car dealer)

All citations are `mf-prg.bas:<line>` (`../research/src/decompiled_basic/mf-prg.bas`).
C64 relational terms evaluate `true = -1`.

## 1. Header

| | |
|---|---|
| key | `aut` |
| la | 4 (`:50005` DATA order; dispatch `:3105` `onlagoto…,14000,…`) |
| title | "AUTOMOBIL-HAENDLER" (SEQ `src/aut`, `location-extraction.yaml` aut) |
| line range | 14000–14131 |
| menu | SEQ file `aut`, `aw=3` options (`:3025-3030`); option 3 = leave |
| entry path | map step onto a door cell → `:2050` `la=peek(ua+1):ln=peek(ua+2)` → `:2055 gosub3000` → picture (`:3000-3007`, `aut-pic`, graphics-mode `lm` gated) → menu read (`:3015-3035`) → `:3040` key `w` (1..aw) → `:3045` `w=aw` ⇒ `ms=ms-5:return` (leave) → `:3105` → `:14005 onwgoto14006,14100`. After return: `:2055 ll(sp)=20*la+ln`, `:2060 ms=ms-5`. |
| tiles (`content/map/city.yaml`) | ln1 cell 147, ln2 cell 278, ln3 cell 368, ln4 cell 605 |
| what ln changes | ln=2: showroom offers a 4th model (auburn, 6000 $) (`:14010`). ln=4: the steal never hits the "too crowded" refusal (`:14100`). ln 1/3: no effect. |

## 2. Research coverage verdict

**Gaps + contradictions.** Every line is reachable-explained in aggregate, but:

Gaps
- `location-dialogue.yaml` aut option 2 omits `:14100` "es sind zuviele leute hier!".
- No research file states: the 4th model exists only at ln=2 (`:14010`); the afford check ignores the trade-in (`:14035`); declining the trade-in aborts the purchase (`:14047`); buying adjusts `ms` immediately but stealing does not (`:14050` vs `:14118`); barrels are not re-capped on a smaller car.

Contradictions (BASIC wins)
- `location-handlers.yaml` "14100" guard "succeeds if int(rnd*(in/40+kr/30))=0" — **inverted**. `:14110 ifint(rnd(1)*(in/40+kr/30))=0goto14120` → `=0` means **caught** (14120), non-zero means success (14115).
- `location-handlers.yaml` "14006" summary "improves your getaway" — no getaway link. The only escape roll, `:26040 int(rnd(1)*tr(sp)/11)`, indexes `tr` by **player number** `sp`, not `tm(sp)`. The vehicle only sets movement points (`:1012 ms=tr(tm(sp))`) and barrel capacity (`:12025 tk(tm(sp))`).
- `systems-analysis.yaml` rank_restrictions "aut line 12200" — 12200 is the pub tip; aut has no rank check anywhere in 14000–14131.

## 3. Menu

| # | option (SEQ label) | handler line | guard (engine DSL) |
|---|---|---|---|
| 1 | WAGEN KAUFEN | 14006 | none |
| 2 | AUTO 'ORGANISIEREN' | 14100 | none. The crowd refusal is an RNG gate, so it stays in the handler. |
| 3 | WIEDER GEHEN | — (`:3045`) | none; costs `ms-5` on top of the entry cost |

## 4. Behavior walk

### Option 1 — buy a car (`:14006-14052`)
1. `:14006-14008` Intro text (3 lines, dialogue `:14006/7/8`), then wait for a key (`gosub1100`). Clear the screen.
2. `:14010` `x=2`; `if ln=2 then x=3`. Models offered are 1..x+1 (3 models, 4 at ln=2).
3. `:14011-14015` For each model i=0..x, draw the sprite and print `chr$(49+i)` (the digit), `tm$(i+1)` (name) and the price `3000+1000*i` $. Models: 1 talbot 90 3000, 2 chevy roadster 4000, 3 buick century 5000, 4 auburn mod.120 6000 (`:50300-50305`). Sprites are presentation only.
4. `:14020` Single key `x$` (GET loop).
5. `:14025` `y=val(x$)`. `y=0` → hide the sprites and **return** (quiet exit). Any non-digit key has `val=0`, so it also exits.
6. `:14030` `(y-1)>x` → ignore the key and loop back to step 4. Valid y = 1..x+1.
7. `:14035` Clear; `p=3000+1000*(y-1)`. **If `ka(sp)<p`** → `gosub1125` ("du hast zu wenig kies!" + wait for a key) → `goto14006` (restart the whole showroom at step 1). The trade-in value is NOT counted toward affordability.
8. `:14040` If `tm(sp)=0` (on foot) → `q=0`; skip to step 10.
9. `:14045` `q=1000+1000*tm(sp)`; if `tm(sp)=5` (stolen citroen) → `q=1000`. Trade-in values: talbot 2000, chevy 3000, buick 4000, auburn 5000, citroen 1000. `:14046-14047` prints "man bietet dir `q` $ fuer deine alte schaukel. ok (j/n)?" (`gosub1110`: only j/n accepted). **`n` → `goto14006`** (back to step 1; you cannot buy without trading in).
10. `:14050` `ka(sp)=ka(sp)-p+q`; `ms=ms+tr(y)-tr(tm_old)` (the movement-point delta applies **this turn**); `tm(sp)=y`.
11. `:14051-14052` "der verkaeufer reicht dir schluessel und papiere." + key → return.
- No score change and no RNG anywhere in option 1.
- Re-buying the model you already own is allowed (net cost 1000 $).

### Option 2 — steal a car (`:14100-14131`)
1. `:14100` `if ln<>4 and int(rnd(1)*3)<>0` → print "es sind zuviele leute hier!" + key → return. P(refused) = 2/3 at ln≠4 and 0 at ln=4. C64 BASIC has no short-circuit, so the rnd draw happens even at ln=4; draw order is not a fidelity target.
2. `:14101` "wer soll den wagen aufbrechen:" → `gosub1130` gangster picker. The picker lists each gangster (`gosub1300`: name, `e k i b` stats, weapon), waits for a key after each one, then `input "nummer:";y`. `y>gz(sp)` → re-prompt. `y=0` → return. Otherwise `gosub1350` loads the chosen gangster's `en,kr,in,bt` into scalars. If `gz(sp)=0` the picker returns `y=0` immediately. **`y=0` → return.**
3. `:14110` Caught test: `int(rnd(1)*(in/40+kr/30))=0` → caught (step 5). With `k=in/40+kr/30`, P(caught)=min(1, 1/k). Example: in=40, kr=30 ⇒ k=2 ⇒ 50 %. k<1 ⇒ always caught.
4. Success `:14115-14118`: "yeah! die karre ist offen! du machst dich damit aus dem staub." If `tm(sp)≠0`, it also prints "und musst leider deinen alten wagen stehen lassen." (`:14117`). Then `tm(sp)=5` (citroen t.a., tr 35, tank 100). **No `ms` adjustment** (unlike the buy). No score. The old car is lost with no compensation. Key → return.
5. Caught `:14120` "leider erwischt dich der besitzer der karre!" + key.
6. `:14125` Combat: `bn$(0)="wagenbesitzer"`, `gz(0)=1`, `e=30`, `w=5` (revolver), `kf$="ks"` → `gosub5000` (`:5000` `gw(0,i)=w:ec(i)=e`; `:5010 ks(1)=sp:ks(2)=0:goto30000`).
   - `s=2` (player lost or surrendered with `q`, `:30136`) → **`goto26020`** (direct capture, no police fight). The location sets no other variable first. `kf$` is still `"ks"`.
   - `s=1` → `:14130-14131` "nach dem mord an dem besitzer musst du verschwinden und den wagen stehen lassen!" + key → return. No car, no money, no score.

### Option 3 — leave: `:3045` `ms=ms-5`, return to map.

## 5. State

| BASIC | meaning | port name |
|---|---|---|
| `ka(sp)` | cash | `Player.ka` (`MoneyChange`) |
| `tm(sp)` | vehicle index 0..5 | `Player.vehicle` — **NEW effect needed** (no effect sets `vehicle`; e.g. `VehicleSet(index)`) |
| `tr(i)`, `tk(i)`, `tm$(i)` | vehicle range / tank / name | `entities/vehicles.yaml` `tr`/`tank`/`name` |
| `ms` | movement points | `Player.ms` (`MsChange(tr(y)-tr(old))`) |
| `ln` | tile | `Player.last_location` |
| `gz(sp)` | roster size incl. boss | `len(Player.roster)` |
| `in`, `kr` (scalars via `:1350`) | chosen gangster's intelligenz/kraft | `roster[y-1].attrs["intelligenz"/"kraft"]` |
| `p`, `q`, `x`, `y` | locals | handler locals; price base/step and trade-in formula → NEW `formula_params` (`aut_price_base=3000`, `aut_price_step=1000`, `aut_tradein_base=1000`, `aut_tradein_stolen=1000`, `aut_crowd_roll=3`, `aut_steal_in_div=40`, `aut_steal_kr_div=30`, `aut_extra_model_tile=2`, `aut_no_crowd_tile=4`) |
| `bn$(0),gz(0),e,w,kf$` | owner fight | NEW encounter `aut_owner.yaml` (count 1, weapon 5, vitality 30, name wagenbesitzer, grid ks) |
| `s` | combat winner | `CombatResult.winner` |
| `ta(sp)` | barrels (NOT touched) | `Contraband.alcohol_barrels` — see Q3 |

## 6. Dependencies

- **Capture chain** `:26020-26080` (other dossier): needed for the lost owner fight.
- **Gangster picker** `:1130-1155`: the port's `waf` has an equivalent (`_pick_gangster_and_arm`); reuse its shape.
- **Vehicle-set effect**: NEW.
- Presentation: showroom sprites (`:14013-14015`), `aut-pic`.
- Leave cost `:3045` (`ms-=5`): the existing `kdh.yaml` comment says leave leaves `ms` unchanged. Cross-cutting (Q4).

## 7. Open questions

1. **Afford check ignores trade-in** (`:14035` tests `ka<p` before `q` is known). Faithful port = require full price in cash. Recommend faithful.
2. **Declining the trade-in aborts the purchase** (`:14047 ifx$="n"goto14006`). The option to decline exists but does nothing useful. Keep it faithful, as a loop back to the showroom (an in-handler loop, like waf's trade-in decline).
3. **Barrel overflow**: switching to a smaller-tank car (buy or steal) never clamps `ta(sp)` to the new `tk`. Only `:12025` (pub buy) and the roadblock (`:6035`) look at barrels. Faithful = no clamp.
4. **Leave cost**: `:3045` charges `ms-5` on leave (on top of the 5 at `:2060`). The existing port shells treat leave as free. Planner: cross-cutting fix or deliberate divergence?
5. **Steal gives no `ms` delta this turn** (`:14118`), unlike the buy (`:14050`). Faithful asymmetry; record it in a test.
6. **Picker input edge**: a negative/non-integer `nummer` in `:1145` (BASIC error or truncated index). Port should clamp to 0..gz.

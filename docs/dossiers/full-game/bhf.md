# Dossier: bhf — Bahnhof (railway station)

All citations are `mf-prg.bas:<line>`. C64 relational terms evaluate `true = -1`.

## 1. Header

| | |
|---|---|
| key | `bhf` |
| la | 9 (`:3105`) |
| title | "RAILWAY-STATION (BAHNHOF)", prompt "DU BIST IM BAHNHOF VON CHICKAGO." (SEQ `src/bhf`) |
| line range | 19000–19050, plus the shared bodies it jumps into: `:18035-18052` (pickpocket), `:20050-20060` (heist payout), `:3000` (pub menu) |
| menu | SEQ `bhf`, `aw=4`; option 4 = leave (`:3045` `ms-5`) |
| entry path | `:2050` → `:2055 gosub3000`. **No picture**: `:3000 ifla=9or(…)goto3010` skips the picture load for la=9 (no `bhf-pic` exists). → w → `:3105` → `:19005 onwgoto19010,19050,19015` |
| tiles | ln1 only, cell 68 (`city.yaml`) |
| what ln changes | nothing in bhf itself. Option 1 **forces `ln=5`** for the pub it opens. |
| pub tip link | Pub tip `tp(sp)=1` "im postzug soll demnaechst eine grosse ladung diamanten transportiert werden!" (`:12225-12231`, P=1/5 per bought tip) is the only key to option 3. |

## 2. Research coverage verdict

**Gaps + contradictions.**

Gaps
- `location-dialogue.yaml` bhf misses `:19015` ("kein postzug zu sehen...") and `:19016` ("du hast zu wenig gangster!").
- Undocumented: the gang-size refusal **destroys the tip** (`:19016 tp(sp)=0`); bhf option 1 forces pub `ln=5`, which unlocks the pub's alcohol **buy** branch (`:12010 ifln=4orln=5`); option 1's jump is a `goto` into `:3000`, so no extra entry cost or `ll(sp)` for la=9.

Contradictions
- `location-handlers.yaml` "19050" "Pickpocket at the station (subway theft logic, **weapon w=1**)". `w` is the **menu-option** variable. It is forced to 1 so that `:18045`'s `-(w=2)` term (the train bonus) does not fire, since bhf option 2 has `w=2` natively.
- `game-logic.yaml` theft_loot says la=9 is "the subway". la=9 is the Bahnhof, where `-(la<>9)=0`, so the bhf loot is the **worst** table (see sub dossier).
- **Engine port**: `data/game_configs/mafia_1920s/handlers/pub.py` docstring + `_ALCOHOL_TILE=4` say the `ln=5` branch is "unreachable and deliberately not ported". Once bhf is built, it becomes reachable via `:19010`. `pub.drink` must then honour `ln in {4,5}` (and the drink test/doc comments must change).

## 3. Menu

| # | option | line | guard |
|---|---|---|---|
| 1 | BAHNHOFSKNEIPE AUFSUCHEN | 19010 | none |
| 2 | TASCHENDIEBSTAHL | 19050 → 18035 | none |
| 3 | POSTZUG UEBERFALLEN | 19015 | handler-internal: `tip_target = 1` then `gang_size >= 3`. Cannot be a pure shell guard: the second check's failure has a **side effect** (`tp(sp)=0`), and the first has its own message. |
| 4 | NICHTS WIE WEG | leave | none |

## 4. Behavior walk

### Option 1 — station pub (`:19010`)
1. `ln=5:la=2:goto3000`. This is a **goto**, so it runs inside the same `gosub3000` frame opened at `:2055`.
2. `:3000` runs for la=2: the pub picture may load (lm-gated). `:3010 ifll<>la` → the menu cache `ll` (scalar, ≠ `ll(sp)`) is 9, so the pub SEQ is re-read. The pub menu is shown and `w` is read.
3. Pub leave (`w=aw`) → `:3045 ms=ms-5`, return. Otherwise `:3105` → `:12000` pub handlers run with **la=2, ln=5**:
   - drink `:12010`: ln=5 → **buy** branch `:12020` (stock 100..299, price 5..9, capped by tank).
   - recruit `:12107`: `orln=3` does not fire → normal.
   - tip `:12200`, job `:12300`: no ln dependence.
4. On return to `:2055`: `ll(sp)=20*2+5=45` (the pub, not the Bahnhof), then `:2060 ms-=5`. There is no second entry charge.

### Option 2 — pickpocket (`:19050`)
`w=1:goto18035`. The body is identical to sub (`sub.md` §4), with **la=9, w=1**:
1. Thief picker; `y=0` → return.
2. `gf += 1*x8`.
3. P=1/15 safecracker manual → `s9(sp)=5`.
4. Caught P=min(1,10/in) → **`goto26020`** (direct capture).
5. Loot idx `r∈{0..3}`: handtasche 0 / fotoapparat +50 / perlenkette 0 / armbanduhr +100 (EV 37.5 $). No ticket.

### Option 3 — rob the mail train (`:19015-19040` → `:20050-20060`)
1. `:19015` `tp(sp)<>1` → "kein postzug zu sehen..." + key → return. The tip is kept (it is not 1 anyway).
2. `:19016` `gz(sp)<3` (fewer than boss + 2) → "du hast zu wenig gangster!", **`tp(sp)=0`** (tip lost) + key → return.
3. `:19020` Load picture `pzug-pic`. `:19025-19027` "du stuermst in den panzerwaggon … drei nette herren aufmerksam wirst..." + key.
4. `:19030` Combat: `bn$(0)="wachen"`, `gz(0)=3`, `w=7` (maschinenpistole), `e=30`, `kf$="kpzug"` → `gosub5000`.
   - `s=2` → **`goto26020`** (direct capture). Set first: `kf$="kpzug"`; `tp(sp)` is **still 1**. The capture chain clears it only on the surrender/court path (`:26045 tp(sp)=0`); bribe success `:26039` and escape `:26041` exit with `tp(sp)=1` intact, so the train can be retried. `p` is combat-clobbered (Q3).
   - `s=1` → `:19040 goto20050`.
5. `:20050` `p=int(rnd(1)*3000)+4000-500*(la=10andln=1)`. At la=9 the term is 0, so 4000..6999. `x=tp(sp)` (=1).
6. `:20051` `(x=1 and la=9)` true → **`tp(sp)=0`, `p+=3000`**, giving **7000..9999**.
7. `:20055-20060` "du hast es geschafft! deine beute betraegt `p` $!" `ka += p`, **`x=4:gosub1160` → gf += 4*x8**. Key → return.

### Option 4 — leave: `:3045` `ms-5`.

No `ms=0` set in bhf.

## 5. State

| BASIC | meaning | port |
|---|---|---|
| `tp(sp)` | pub tip id (1 = Postzug) | `Player.tip_target` (`TipClear`) |
| `gz(sp)` | roster size incl. boss | `len(roster)` |
| `la`, `ln` | forced to 2/5 for option 1 | NEW: handler must re-dispatch into the pub with `SetEntryContext(la=2, ln=5)` and **no** extra `MsChange` |
| `ll(sp)` | prev-entry code | NEW (see sgl). After option 1 it must end up as 45, not 181. |
| `ka`, `gf` | cash, score | `MoneyChange`, `ScoreAndRank(4)` |
| `s9(sp)`, `in` | via the sub body | see `sub.md` |
| `w` | menu option, forced to 1 | handler param |
| fight | wachen | NEW encounter `bhf_guards` (3 × weapon 7, vit 30, name wachen, grid kpzug). NEW backdrop `kpzug` (`../research/src/kpzug`). |
| payout | `:20050-20051` | shared helper with ban (NEW `formula_params`: `heist_pay_min=4000`, `heist_pay_max=6999`, `heist_tip_bonus=3000`, `heist_score=4`) |

## 6. Dependencies

- **Pub tips** (`tip_target`, built). Tip 1 is the key. This location is its only consumer.
- **Pub handlers with ln=5** → `pub.drink` must accept tile 5 (a change to existing code).
- **Location re-dispatch** (handler → another location's menu, keeping the same visit): NEW engine capability, or model it as a `LoadSubState` that runs the pub menu. There is no precedent in the port.
- **Capture chain** `:26020-26080` (other dossier), including its `tp(sp)=0` at `:26045`.
- **Shared heist payout `:20050`** with ban and the la=13 cash-transport flow (other dossier, `:23030`).
- The sub pickpocket body.
- Presentation: `pzug-pic`.

## 7. Open questions

1. **Station pub re-dispatch**: how does the port run the pub menu from inside bhf? Options: (a) a `LoadSubState("location_menu", la=2, ln=5)`; (b) the handler returns a "redirect" result that the session turns into a pub visit without charging `ENTER_COST`. The source charges nothing extra, and the pub's own leave costs `ms-5`.
2. **Tip loss on the gang-size check** (`:19016`) is harsh but faithful. Recommend faithful.
3. **Stale/clobbered `p` into `:26020`** (auto-bribe path `:26021→26037` reads `p` without setting it). Here `p` is whatever combat left. Flag to the capture-chain owner.
4. **Retry after bribe/escape**: `tp(sp)` survives capture unless the player surrenders or is jailed. Faithful.
5. `ll(sp)` after the station pub = 45 (the pub tile code 20*2+5). There is no real pub tile with ln=5, so this can never trigger a trap. Harmless, but the port's prev-entry write must use the *current* la/ln at exit.

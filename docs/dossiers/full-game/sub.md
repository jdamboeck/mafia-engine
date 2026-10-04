# Dossier: sub — Subway-Station (U-Bahn)

All citations are `mf-prg.bas:<line>`. C64 relational terms evaluate `true = -1` (`-(w=2)` = **+1** when w=2).

## 1. Header

| | |
|---|---|
| key | `sub` |
| la | 8 (`:3105`) |
| title | "SUBWAY-STATION (U-BAHN)" (SEQ `src/sub`) |
| line range | 18000–18052 |
| menu | SEQ `sub`, `aw=3`; option 3 = leave (`:3045` `ms-5`) |
| entry path | `:2050` → `:2055 gosub3000` (picture `sub-pic`, lm-gated) → w → `:3105` → `:18010 onwgoto18035,18015` |
| tiles | ln1 cell 149, ln2 167, ln3 511 (`city.yaml`) |
| what ln changes | **nothing** (no `ln` read in 18000–18052) |
| shared | `:18035-18052` is also bhf option 2's body (`:19050 w=1:goto18035`, with la=9). The `la<>9` term is what distinguishes the two. |

## 2. Research coverage verdict

**Gaps + contradictions.** `location-dialogue.yaml` covers every print. The logic claims are partly wrong:

- `game-logic.yaml` `theft_loot.bias`: "shift … when using **weapon** 2 and when **in the subway (la=9)**". Both are wrong. `w` at `:18045` is the **menu option** (2 = "in der Bahn"), not a weapon. `la=9` is the **Bahnhof**. `-(la<>9)` adds +1 when *not* at the Bahnhof, i.e. in the subway.
- `game-logic.yaml` lists `18052` (safecracker manual) as a loot-table outcome. It is a separate pre-roll at `:18040` (`int(rnd(1)*15)=10`, P=1/15) that happens **before** the catch test.
- `location-handlers.yaml` "18035" summary: "…or getting caught (combat)". There is **no combat**. Caught → `:18042 goto26020` (direct capture).
- `location-handlers.yaml` "18015" "(same loot table, **weapon-biased** odds)". The bias comes from the menu option, not a weapon.
- `systems-analysis.yaml:211` "s9(sp) = safecracker manual (sub 18051)". It is `:18052`.
- Not recorded anywhere: `gf += 1*x8` at `:18039` happens *before* the outcome, so it is paid even when caught; and the ticket is lost if the thief picker is cancelled.

## 3. Menu

| # | option | line | guard |
|---|---|---|---|
| 1 | LEUTEN AUF DEM BAHNSTEIG DIE TASCHEN ERLEICHTERN | 18035 | none |
| 2 | IN DER BAHN DIE TASCHENINHALTE PRUEFEN | 18015 | none (ticket afford is in-handler) |
| 3 | ZURUECK ANS TAGESLICHT | leave | none |

## 4. Behavior walk

### Option 2 — train (`:18015-18030`, then continues into option 1's body)
1. `:18015-18020` "ein u-bahn-ticket kostet dich 50 $. ok (j/n)?" (`gosub1110`: only j/n). `n` → return.
2. `:18025` `ka(sp)<50` → `goto1125` ("du hast zu wenig kies!" + key) → return.
3. `:18030` `ka(sp) -= 50`. The ticket is **non-refundable**, even if step 4 is cancelled.
4. Continue at `:18035` with `w=2`.

### Option 1 — platform / shared body (`:18035-18052`)
1. `:18035` Clear; "welchen spieler setzt du als dieb ein:" → `gosub1130` picker (lists gangsters, `input "nummer:"`; `y=0` or `gz=0` → y=0). **`y=0` → return.** The picker loads the thief's `in` scalar via `:1155`.
2. `:18039` `x=1:gosub1160` → **`gf += 1*x8`** (always paid). "du stiehlst..." then a delay (`fort=1to500`).
3. `:18040` RNG `int(rnd(1)*15)=10` (P=1/15) → `:18052` "...eine anleitung -der safeknacker- ??!", **`s9(sp)=5`** (set, not add) → key → return. No catch test.
4. `:18041` Catch test: `int(rnd(1)*(in/10))` non-zero → loot (step 6). Zero → caught. P(caught | no manual) = min(1, 10/in) (in=10 ⇒ 100 %, in=50 ⇒ 20 %, in=99 ⇒ ~10.1 %).
5. Caught `:18042` "...nichts! denn du wirst erwischt!" + key → **`goto26020`** (direct capture; no fight). Variables set first: none by sub. `kf$` is **stale** from the last fight anywhere. `26020+` does not read `kf$`, only `:26000` does, and 26020 skips it. `p` is also stale; `:26037` may read it (see Q3).
6. Loot `:18045` `on int(rnd(1)*4)-(w=2)-(la<>9) goto 18047,18048,18049,18050,18051`. The index is `r + [w=2] + [la≠9]` with r∈{0..3} uniform. Index 0 falls through to `:18046`.

| idx | line | item | $ |
|---|---|---|---|
| 0 | 18046 | handtasche – nichts wertvolles | 0 |
| 1 | 18047 | fotoapparat | +50 |
| 2 | 18048 | falsche perlenkette | 0 |
| 3 | 18049 | armbanduhr | +100 |
| 4 | 18050 | brieftasche | +500 |
| 5 | 18051 | diamant | +800 |

   - sub option 1 (w=1, la=8): idx 1–4, each 1/4 → EV 162.5 $.
   - sub option 2 (w=2, la=8): idx 2–5, each 1/4 → EV 350 $ (−50 ticket).
   - bhf option 2 (w forced to 1, la=9): idx 0–3 → EV 37.5 $.
   Each loot line prints its text, adds `ka`, then key → return.

### Option 3 — leave: `:3045` `ms-5`.

No `ms=0` here. The turn ends only through the capture chain (`:26070/:26080`).

## 5. State

| BASIC | meaning | port |
|---|---|---|
| `ka(sp)` | cash | `MoneyChange` |
| `gf`/`nr` via `:1160` | score | `ScoreAndRank` (x=1) |
| `in` (thief) | chosen gangster's intelligenz | `roster[y-1].attrs["intelligenz"]` |
| `s9(sp)` | safecracker-manual bonus (tries) | `Player.safe_skill` — **NEW effect** (e.g. `SafeSkillSet(value)`) |
| `w` | menu option (1/2) | handler param (sub opt1 → 1, opt2 → 2, bhf → 1) |
| `la` | location id | `Player.last_la` (8 vs 9 drives `-(la<>9)`) |
| constants | ticket 50, manual 1/15 (`=10`), in divisor 10, loot table | NEW `formula_params` / a loot table in content (`theft_loot: [[text,0],[..,50],…]`) |

## 6. Dependencies

- **Capture chain** `:26020-26080` (other dossier).
- **Gangster picker** (shared with aut/ban).
- **bhf** reuses this body with `w=1`. Implement it as one shared helper parameterised by `(w, la)`.
- **Safe-crack** (`ban` option 2) consumes `s9`.

## 7. Open questions

1. **Score paid before the outcome** (`:18039`), including when caught. Faithful (recommend yes).
2. **Ticket lost on picker cancel** (`:18030` before `:18035`). Faithful. In waf terms this is a non-cancellable spend, so the port must not use `cancellable=True` on the picker after the ticket is paid.
3. **Stale `p` into `:26020`**: the capture chain's auto-bribe path `:26021 ifpl(sp)andint(rnd(1)*2)<>0goto26037` jumps past `:26035` (where `p` is computed) and uses whatever `p` last held. From sub, `p` is never set, so it is left over from an earlier action. The owner of the capture chain must decide. Flag it to them; the likely answer is to compute `p=500+500*ra` on that path too, as a documented divergence.
4. The manual *sets* `s9=5` (`:18052`); a second manual resets it to 5 rather than stacking. Faithful.

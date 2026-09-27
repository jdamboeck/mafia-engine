# Dossier: sgl — Einfacher Laden (protection racket)

All citations are `mf-prg.bas:<line>`. C64 relational terms evaluate `true = -1` (`-300*(ln=2)` = **+300** at ln=2).

## 1. Header

| | |
|---|---|
| key | `sgl` |
| la | 7 (`:3105`) |
| title | "EINFACHER LADEN (WIE GESCHAFFEN ZUM SCHUTZGELD EINTREIBEN!)" (SEQ `src/sgl`) |
| line range | 17000–17592 |
| menu | SEQ `sgl`, `aw=5`; option 5 = leave (`:3045` `ms-5`) |
| entry path | `:2050` → `:2055 gosub3000` (picture `sgl-pic`, lm-gated) → `:3040` w → `:3105` → `:17005` |
| tiles | ln1 cell 49, ln2 145, ln3 340, ln4 443, ln5 538, ln6 772, ln7 790, ln8 874, ln9 905 (`city.yaml`) |
| what ln changes | **Everything**: which option works, the payout bonus, whether demolish/kill triggers a fight. See the tile table in §4. |

## 2. Research coverage verdict

**Gaps + contradictions.**

Gaps
- `location-dialogue.yaml` sgl misses `:17006` (milchgesicht), `:17008-17009` (police waiting) and `:17016` (schindest keinen eindruck).
- No research file records the **revisit trap** semantics precisely. `ll(sp)` is the *previous* location entry this turn. It is reset at `:1012` and written at `:2055` **after** the visit returns.
- The **dead `ll(sp)=0`** at `:17516` is not recorded.
- The **combat clobbers `w`** after the Jack fight (`:30215`) is not recorded.
- Also unrecorded: the rank-2 gate covers options 1–4, and the boss (`b=1`), not a chosen gangster, is tested at `:17015`/`:17300`.

Contradictions
- `location-extraction.yaml` sgl `extortion_entry_flow.score_penalty: "x=2"` — it is a **gain**: `:17500 x=2:gosub1160` → `gf += 2*x8`.
- `location-extraction.yaml` `w2_begging: "ll(sp)=0 (clears last-location tracker)"` — no effect. `:2055 ll(sp)=20*la+ln` overwrites it when `gosub3000` returns. This is the only exit path, since sgl is entered only via `:2055`.
- `docs/systems/location-systems.md:38` "submenu 17510 after extortion" / `menu-flow-index.md:74` "sgl option 4 → 17510 sub-menu". `:17510` is the per-option **response** dispatch shared by all four options. The player submenu is `:17550-17560`.
- `location-handlers.yaml` "17015" gloss says "brutalitaet >= 30 to impress" without naming *whose*. It is the boss's (`:17015 a=sp:b=1:gosub1350`).

## 3. Menu

Every option is always shown. The source refuses **after** the choice, in this order: rank → trap → option logic.

| # | option | line | guard (DSL) |
|---|---|---|---|
| 1 | 'DEN ZASTER HER…' (threat) | 17015 | handler-internal: `rank > 1` (`:17005`); trap (`:17007`); boss `brutalitaet >= 30` and `ln in [1,7,9]` |
| 2 | 'BITTE… KRANKE MA' (sob story) | 17100 | rank, trap; `ln in [1,4,5]` |
| 3 | 'WIR SCHUETZEN DICH…' (protection) | 17200 | rank, trap; `ln in [2,3,8]` else fight |
| 4 | 'POLIZEI! … FALSCHGELD' (fake police) | 17300 | rank, trap; `and: [{ln in [1,3,6]}, {boss intelligenz >= 30}]` (depth 2 OK) |
| 5 | HEUTE MAL NICHT KASSIEREN | leave | none |

- The rank gate is expressible as a shell guard (`rank > 1`, `on_denied: milchgesicht`). The source shows the option and refuses after selection, so keep it in-handler (pub precedent).
- The trap needs a NEW var (previous-entry code). As a guard it would be `prev_entry != <20*la+ln>`, and that literal differs per tile, so the DSL cannot express it with one literal. Keep it in-handler.
- No NOT is needed.

## 4. Behavior walk

### Common prologue (every option 1–4), `:17005-17009`
1. `:17005` `ra(sp)>1` required. Otherwise print "'verschwinde, du milchgesicht!'" + key → return (no other effect).
2. `:17007` **Revisit trap**: if `ll(sp)=20*la+ln`, i.e. the player's previous location entry this turn was this very sgl tile (whatever they did there, including just leaving): `:17008-17009` "vor dem laden erwartet dich die poliyei!" (sic) + key → `kf$="ks"` → **`goto26000`** (police fight). `ll(sp)` is reset to 0 each turn at `:1012`.
3. `:17010 onwgoto17015,17100,17200,17300`.

### Option 1 — threat (`:17015-17020`)
1. `:17015` Clear; load the **boss** stats (`a=sp:b=1:gosub1350`).
2. `:17016` `bt<30` → "du schindest keinen eindruck..." + blank line → step 4.
3. `:17017` `ln in {1,7,9}` → **17500** (success).
4. `:17020` "der kerl ruft die polizei!!!" + key → `kf$="ks"` → **`goto26000`** (police fight, `:26000-26015`; `s=1` ⇒ `gf+=2*x8`, return; `s=2` ⇒ capture `:26020`).

### Option 2 — sob story (`:17100-17105`)
1. `ln in {1,4,5}` → **17500**.
2. Else `:17105` "'sorge doch selbst fuer deine alte!'" + key → return. No penalty.

### Option 3 — protection (`:17200-17225`)
1. `ln in {2,3,8}` → **17500**.
2. Else `:17205-17206` "'das wird aber narben-jack interessieren!'" + key.
3. `:17210` Combat: `bn$(0)="jack's gang"`, `gz(0)=3-2*(gz(sp)>5)` (= **5** if the roster has more than 5 members incl. the boss, else **3**), `w=7` (maschinenpistole), `e=30`, `kf$="ksgl"` → `gosub5000`.
4. `:17215` `s=2` → **return** (no capture, no money, no score; only combat losses persist).
5. `s=1` → `:17220-17221` "jack verzieht sich erstmal aus diesem gebiet..." + key → **17500**.
   - **Quirk:** combat set `w=gw(ks(s),f)` at every shot (`:30215`), so `w` now holds the weapon of the gangster who fired the last (killing) shot, not 3. That value feeds `:17505` (`+600*(w=2)`) and `:17510` (`onwgoto`; w∉1..4 falls through to `:17511`). See Q1.

### Option 4 — fake police (`:17300-17306`)
1. `:17300` Load the boss stats. `(ln in {1,3,6}) and in>=30` → **17500**.
2. Else `:17305-17306` "'ach, die alte bullen-masche! damit neppt ihr keinen mehr!'" + key → return.

### Shared success `:17500-17592`
1. `:17500` `x=2:gosub1160` → **`gf += 2*x8`**, clamp [0,100], `nr` recomputed. This happens *before* the payout roll.
2. RNG `int(rnd(1)*3)=0` (P=1/3) → **small payout** `:17530`: `p=int(rnd(1)*100)+100` (100..199). Message "'ich habe leider nur `p` $!" (the same for every option) → step 4.
3. Else (P=2/3) `:17505`: `p=int(rnd(1)*200)+800-300*(ln=2)-200*(ln=7)-200*(ln=9)+600*(w=2)`. That is 800..999, **+300 at ln2, +200 at ln7, +200 at ln9, +600 if w=2**. `:17510 onwgoto` response:
   - w=1 `:17511-17512` "i..i..ich z..zahle ja schon! hi..hier sind `p` $!"
   - w=2 `:17515` "deine arme ma (schnief)! gib ihr die `p` $ hier!" then `:17516 ll(sp)=0` (**dead**, see §2)
   - w=3 `:17520` "'ich habe `p` dollar, reicht das?'"
   - w=4 `:17525-17526` "verdammt! wer hat mir nur blueten fuer `p` $ angedreht...?"
   - w outside 1..4 (only after a Jack fight) → falls through to `:17511` (w=1 text).
4. `:17550` **`ka(sp) += p`** (credited before the submenu, whatever is chosen next). Submenu text "was machst du: 1 angebotenes geld nehmen / 2 laden demolieren / 3 besitzer fertigmachen" (`:17550-17551`).
5. `:17555` Single key, only '1'..'3' accepted. `:17560` dispatch:
   - **1** `:17565` → return.
   - **2 demolish** `:17570`: if `ln in {2,6,7,8}` → `:17571-17572` "'saubande! das werdet ihr buessen!' (der kerl winkt ein paar schlaeger heran!)" + key → `:17573` combat `bn$(0)="schlaeger"`, `gz(0)=5`, `w=3` (schlagkette), `e=20`, `kf$="ksgl"`. `s=2` → return (the extortion `p` is kept). `s=1` → step 6. Other ln → step 6 directly.
   - **3 kill owner** `:17580`: if `ln in {1,4}` → `:17585-17586` "er verspricht dir einen schoenen grabstein und laedt seine `wa$(7)`!" (maschinenpistole) + key → `:17587` combat `bn$(0)="ladenbesitzer"`, `gz(0)=1`, `w=7`, `e=30`, `kf$="ksgl"`. `:17588` `s=2` → return. `s=1` → step 7. Other ln → step 7 directly.
6. Demolish settle `:17575-17578`: `p=int(rnd(1)*100)+300` (300..399). "du hast kleinholz aus dem laden gemacht. in der kasse waren `p` $!" `ka += p`, `x=1:gosub1160` (**gf += 1*x8**), key → return.
7. Kill settle `:17590-17592`: `p=int(rnd(1)*100)+200` (200..299). "der aufmuepfige kerl ist hin. `p` $ hatte er der tasche!" `x=1:gosub1160`, then `goto17578`: `ka += p`, `x=1:gosub1160` **again**. Total **gf += 2*x8**. Key → return.

### Tile table (derived from `:17017/:17100/:17200/:17300/:17505/:17570/:17580`)

| ln | opt1 (boss bt≥30) | opt2 | opt3 | opt4 (boss in≥30) | payout bonus | demolish fight | kill fight |
|---|---|---|---|---|---|---|---|
| 1 | ok | ok | Jack | ok | – | no | yes |
| 2 | police | refuse | ok | refuse | +300 | yes | no |
| 3 | police | refuse | ok | ok | – | no | no |
| 4 | police | ok | Jack | refuse | – | no | yes |
| 5 | police | ok | Jack | refuse | – | no | no |
| 6 | police | refuse | Jack | ok | – | yes | no |
| 7 | ok | refuse | Jack | refuse | +200 | yes | no |
| 8 | police | refuse | ok | refuse | – | yes | no |
| 9 | ok | refuse | Jack | refuse | +200 | no | no |

Option 2 always adds +600 (w=2). "police" = `:17020 → 26000`. Option 1 with boss bt<30 → police at every tile.

### Exits to police
| site | entry | state set first |
|---|---|---|
| `:17009` trap | `goto26000` | `kf$="ks"` |
| `:17020` opt1 fail | `goto26000` | `kf$="ks"` |
| none else | — | Lost fights (`:17215/:17573/:17588`) just `return`. There is no capture. |

`:26000` builds the police fight itself (`bn$(0)`, `gz(0)=5+int(rnd*ra/2)`, `w`, `e`). Only `kf$` (backdrop) comes from sgl.

### Turn-end
None set here. `ms=0` only via `:26070/:26080` if the police chain ends in court.

## 5. State

| BASIC | meaning | port |
|---|---|---|
| `ra(sp)` | rank | `Player.rank` |
| `ll(sp)` | previous location entry this turn, `20*la+ln`; 0 at turn start | **NEW** (e.g. `Player.prev_entry: int`, reset by the turn loop at `:1012`, set on location *exit* at `:2055`). `last_la`/`last_location` do not work: `SetEntryContext` overwrites them before the handler runs. |
| `ln`, `la` | tile, location | `Player.last_location`, `Player.last_la` |
| `bt`, `in` (boss) | stats of roster[0] | `roster[0].attrs["brutalitaet"/"intelligenz"]` |
| `gz(sp)` | roster size | `len(roster)` |
| `gf`, `nr` via `:1160` | score/rank | `ScoreAndRank` via `score_and_rank(x, params)` |
| `ka(sp)` | cash | `MoneyChange` |
| `w` | option index, then clobbered by combat | handler local + see Q1 |
| `p` | payout | local; NEW `formula_params` (`sgl_small_roll=3`, `sgl_small_min=100/max=199`, `sgl_pay_min=800/max=999`, `sgl_ln_bonus={2:300,7:200,9:200}`, `sgl_sob_bonus=600`, `sgl_demolish_min=300/max=399`, `sgl_kill_min=200/max=299`, score 2/1/1+1, stat thresholds 30/30, rank floor 1) |
| fights | Jack / schlaeger / ladenbesitzer | NEW encounters: `sgl_jack` (variants: 3 or 5 × weapon 7, vit 30, grid ksgl), `sgl_thugs` (5 × weapon 3, vit 20, ksgl), `sgl_owner` (1 × weapon 7, vit 30, ksgl). NEW backdrop `ksgl` (source `../research/src/ksgl`). |

## 6. Dependencies

- **Police fight + capture** `:26000-26080` (other dossier). Entered with `kf$="ks"`.
- **Prev-entry tracking** (`ll(sp)`): NEW turn-loop/movement state. It must be written on location exit and reset at turn start. The same var serves the ban trap (`:20004`).
- **Combat backdrop `ksgl`**: new decode (tools/decode_combat_backdrops.py covers ks/kp/km only).
- The "last shooter weapon" output from combat, if Q1 is decided faithful.

## 7. Open questions

1. **Post-Jack-fight `w` clobber** (`:30215` sets `w` per shot; `:17505`/`:17510` read it). Faithful: response text and the +600 bonus depend on the killing gangster's weapon (knueppel ⇒ +600 and the sob-story text; weapon 0/5–8 ⇒ the option-1 text). Intended: w=3. The port's `CombatResult` has only `winner` and `losses`, so the faithful path needs a `last_weapon` field. Planner decides.
2. **Dead `ll(sp)=0`** at `:17516`. Faithful port = no-op. Confirm the port does NOT clear prev-entry there.
3. **Trap scope**: the trap fires on the immediately previous entry only (a single slot, not a set). Visiting sgl A → sgl B → sgl A is safe. Confirm faithful.
4. **Money before choice**: `p` is credited at `:17550` even if the demolish/kill fight is then lost. Confirm faithful (recommend yes).
5. **Score before payout**: `+2*x8` at `:17500` is granted even on the 1/3 small payout. Faithful.
6. The rank gate (`:17005`) placement: in-handler (source order) vs shell guard. It must stay before the trap in either case.

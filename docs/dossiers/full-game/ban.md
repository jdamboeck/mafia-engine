# Dossier: ban — Bank/Postamt

All citations are `mf-prg.bas:<line>`. C64 relational terms evaluate `true = -1`: `3-(ln=1)` = **4** at ln=1, and `-500*(la=10andln=1)` = **+500** at ln=1.

## 1. Header

| | |
|---|---|
| key | `ban` |
| la | 10 (`:3105`) |
| title | "BANK/POSTAMT", prompt "'GUTEN TAG, MEIN HERR! WAS IST IHR BEGEHR?'" (SEQ `src/ban`) |
| line range | 20000–20150 (+ shared payout `:20050-20060`, also entered from bhf `:19040` and cash transport `:23030`) |
| menu | SEQ `ban`, `aw=3`; option 3 = leave (`:3045` `ms-5`) |
| entry path | `:2050` → `:2055 gosub3000` (picture `ban-pic`, lm-gated) → w → `:3105` → `:20003` |
| tiles | ln1 cell 95, ln2 437, ln3 442, ln4 657, ln5 865 (`city.yaml`) |
| what ln changes | ln=1: 4 guards instead of 3 (`:20012`), **+500** payout (`:20050`), **−3** safe tries (`:20111`). ln=2: the **only** tile where pub tip 2 ("die bank an der hauptstrasse…", `:12235`) pays +3000 (`:20051`). ln 3–5: no effect. |

## 2. Research coverage verdict

**Gaps + contradictions.**

Gaps
- `location-dialogue.yaml` ban misses `:20003` ("werde erst 'kleiner fisch'!"), `:20009` ("du brauchst einen begleiter!") and the trap text reused from `:17008-17009`.
- Undocumented:
  - The rank gate covers **both** options (the handlers catalog lists it under option 1 only).
  - The revisit trap `:20004`.
  - Option 2's stat check is on the **boss**.
  - The minigame's "slip" also suppresses the click on a correct digit.
  - The final successful press costs no try.
  - Safe success nets `+3*x8` (−1 then +4).
  - The bank tip works only at ln=2.

Contradictions
- `location-handlers.yaml` "20009" summary "~1/3 chance the guards spot you (combat)". It is **2/3**: `:20010 ifint(rnd(1)*3)=0goto20050` skips the fight with P=1/3, and the fight follows with P=2/3. (`systems-analysis.yaml:79` has it right.)
- `location-handlers.yaml` "20100" guard "**chosen cracker** needs intelligenz >= 40". `:20100 a=sp:b=1:gosub1350` tests the **boss** (in≥40, kr≥15, bt≥20) *before* the cracker is picked (`:20104`). The cracker's own `in` only drives the tries and the slip rate.
- `systems-analysis.yaml:84`, `docs/systems/economy-system.md:13`, `pass-4/edge-cases.yaml:74`: "la=10 ln=1 = bank direct" / "base 4000-7000". The term is `-500*(true)` = **+500 at ln=1** (a bank tile, not a mode). The base is 4000..6999 and ln=1 gives 4500..7499.
- `systems-analysis.yaml:188` "werde erst rang 3" — it prints `ra$(3)`, i.e. "kleiner fisch".
- `pass-4/edge-cases.yaml:149` "ln option index". `ln` is the tile index, not a menu option.

## 3. Menu

Every option is always shown; refusals happen after the choice, in the order rank → trap → option.

| # | option | line | guard (DSL) |
|---|---|---|---|
| 1 | 'QUATSCH NICH' UND LANG DEN ZASTER 'RUEBER…' (hold-up) | 20009 | in-handler: `rank >= 3` (`:20003`), trap (`:20004`), `gang_size != 1` (`:20009`; the source tests `=1` exactly, so gz=0 would pass, see Q5) |
| 2 | UNAUFFAELLIG NACH ALARMANLAGEN UMSEHEN UND NACHTS EINBRECHEN (safe) | 20100 | in-handler: rank, trap, then boss `and: [intelligenz>=40, kraft>=15, brutalitaet>=20]` (depth 1; needs boss-attr vars in the DSL if shell-guarded) |
| 3 | WIEDER GEHEN | leave | none |

## 4. Behavior walk

### Prologue (options 1–2), `:20003-20005`
1. `:20003` `ra(sp)<3` → "werde erst '`ra$(3)`'!" + key → return.
2. `:20004` **Revisit trap** `ll(sp)=20*la+ln` (the previous location entry this turn was this bank tile) → `goto17008`: "vor dem laden erwartet dich die poliyei!" (shop wording reused) + key → `kf$="ks"` → **`goto26000`** (police fight).
3. `:20005 onwgoto20009,20100`.

### Option 1 — daytime hold-up (`:20009-20015`, `:20050-20060`)
1. `:20009` `gz(sp)=1` (boss alone) → "du brauchst einen begleiter!" + key → return.
2. `:20010` RNG `int(rnd(1)*3)=0` (P=1/3) → straight to payout (step 5).
3. Else (P=2/3) `:20011-20012` "leider hast du die drei wachmaenner am eingang uebersehen..." + key. The text says "drei" even at ln=1 where there are 4. `gz(0)=3-(ln=1)` → **4 at ln=1, else 3**.
4. `:20015` Combat: `bn$(0)="wachmaenner"`, `e=30`, `w=6` (gewehr), `kf$="kb"` → `gosub5000`.
   - `s=2` → **`goto26020`** (direct capture). Set first: `kf$="kb"`; `tp(sp)` unchanged; `p` combat-clobbered.
   - `s=1` → falls through to `:20050`.
5. **Payout `:20050`**: `p=int(rnd(1)*3000)+4000-500*(la=10andln=1)` → 4000..6999, **4500..7499 at ln=1**. `x=tp(sp)`.
6. `:20051` If `(x=1 and la=9) or (x=2 and la=10 and ln=2) or (x=3 and la=13)` → `tp(sp)=0`, **`p+=3000`**. At the bank only `tp=2` at **ln=2** qualifies. A tip-2 holder robbing any other bank tile keeps the tip.
7. `:20055-20060` "du hast es geschafft! deine beute betraegt `p` $!" `ka += p`, **`x=4:gosub1160` → gf += 4*x8**. Key → return.

### Option 2 — night safe-crack (`:20100-20150`)
1. `:20100` Load the **boss** stats (`b=1`). Require `in>=40 and kr>=15 and bt>=20`. Otherwise `:20101` "du musst noch trainieren!" + key → return.
2. `:20102-20104` "wer soll den kasten knacken:" → `gosub1130` picker. **`y=0` → return.** The picker loads the cracker's `in` (and `kr,bt,en`).
3. `:20105-20107` "'hhm. mal sehen. ich versuch's mit dem stethoskop...' (drehen der coderaeder mit f1, f3 und f5!)" + key.
4. `:20110` Load screen `trs1`; volume on. For i=0..2: dials `rd(i)=1+i` (start 1,2,3); code `cd(i)=int(rnd(1)*10)` (3 draws, 0..9 each).
5. `:20111` **Tries** `y=20+int(in/10)+3*(ln=1)+s9(sp)` (in = the cracker's, ln=1 ⇒ **−3**). Then `s9(sp)=max(0, s9(sp)-1)`. The manual bonus decays 5,4,3,2,1 over successive attempts and is consumed at attempt start, win or lose.
6. `:20115` Read a key. Only F1/F3/F5 (PETSCII 133/134/135) are accepted; anything else is ignored. `x=key-133` (dial 0/1/2).
7. `:20116` `rd(x)=(rd(x)+1) mod 10`. `:20120` Print the new digit at column 16+3x, row 7.
8. `:20125` RNG `int(rnd(1)*(in/8))=0` (P(slip)=min(1, 8/in)) **or** `rd(x)<>cd(x)` → fail sound (`sysso,7`) → step 10.
9. `:20130` Otherwise: click sound (`sysso,8`). If all three `rd(i)=cd(i)` → silence (`gosub1190`) → **success** (step 12). If not all match, fall through to step 10.
10. `:20135` `y=y-1`. If `y>0` → back to step 6.
11. **Tries exhausted** `:20140-20142`: silence, alarm sound (`sysso,6`), "'teufel...! da ist was schiefgegangen! es kommt jemand!'" + key → `kf$="kb"` → **`goto26000`** (police fight, not direct capture).
12. **Success** `:20150`: load screen `trs2`, wait for a key, **`x=-1:gosub1160` → gf −= 1*x8**, then `goto20050` → payout (steps 5–7 of option 1): +500 at ln=1, +3000 with tip 2 at ln=2, **gf += 4*x8**. Net score **+3*x8**.

Minigame notes, all consequences of `:20115-20135`:
- The only feedback is the per-press sound. A correct digit can still sound "wrong" (slip), so the player must cycle that dial another full turn.
- The completing press costs no try; every other press costs 1.
- Initial dial values can already equal the code, but completion is only checked on a click.
- A cracker with in<8 always slips and can never win.
- The minimum presses are Σ((cd(i)-rd(i)) mod 10), up to 27, against 20+int(in/10)(−3)(+s9) tries.

### Option 3 — leave: `:3045` `ms-5`.

No `ms=0` here. The police chain may set it (`:26070/:26080`).

### Exits to police
| site | entry | set first |
|---|---|---|
| `:20004` trap | `goto17008` → `goto26000` | `kf$="ks"` (at `:17009`) |
| `:20015` lost hold-up fight | `goto26020` | `kf$="kb"`; `p` clobbered by combat |
| `:20142` safe tries exhausted | `goto26000` | `kf$="kb"`; `p` stale (never set in option 2 before this) |

## 5. State

| BASIC | meaning | port |
|---|---|---|
| `ra(sp)` | rank | `Player.rank` |
| `ll(sp)` | previous entry code this turn | **NEW** (shared with sgl) |
| `ln`, `la` | tile / location | `Player.last_location`, `Player.last_la` |
| `gz(sp)` | roster size | `len(roster)` |
| boss `in,kr,bt` | roster[0] stats | `roster[0].attrs[...]` |
| cracker `in` | roster[y-1] | `roster[y-1].attrs["intelligenz"]` |
| `s9(sp)` | manual bonus | `Player.safe_skill` — NEW effect (decrement, floor 0) |
| `tp(sp)` | tip | `Player.tip_target` (`TipClear`) |
| `rd(0..2)`, `cd(0..2)`, `y` | dials, code, tries | NEW sub-state-local (minigame) |
| `ka`, `gf` | cash, score | `MoneyChange`; `ScoreAndRank(4)`, `ScoreAndRank(-1)` |
| fight | wachmaenner | NEW encounter `ban_guards` with variants (3 or 4 × weapon 6, vit 30, grid **kb**); NEW backdrop `kb` |
| screens `trs1`/`trs2`, sounds 6/7/8 | presentation | theme / client |
| constants | rank 3, stat 40/15/20, tries 20, in/10, ln1 −3, slip in/8, dial start 1+i, payout 4000..6999, ln1 +500, tip +3000, score 4, −1 | NEW `formula_params` |

## 6. Dependencies

- **Police fight + capture** `:26000-26080` (other dossier). Entered with `kf$="ks"`/`"kb"`.
- **`ll(sp)` prev-entry tracking** (NEW, shared with sgl).
- **Pub tip 2** (built: `TipSet`). This location is its only consumer, and only at ln=2.
- **`s9` producer**: the sub/bhf pickpocket manual.
- **Interactive minigame**: a NEW sub-state with a key-driven loop (F1/F3/F5 → dial choice) and per-press feedback (click/fail). The existing interactions (`PromptChoice` per press) may suffice.
- The shared `:20050` payout with bhf and the la=13 flow.

## 7. Open questions

1. **Safe minigame UX**: the source's only feedback is a sound per press. Port it as a `ShowMessage`/event per press ("click"/"fail")? The slip deliberately lies, so the port must not reveal the true match state.
2. **Score dip on safe success** (`:20150 x=-1` before `:20060 x=4`). The dip is only visible if `gf` sits at 0 or 100, because clamping between the two awards can change the net. Port as two `ScoreAndRank` effects in order, to keep the clamp behaviour.
3. The text **"drei wachmaenner"** at ln=1 while 4 spawn. Keep the text verbatim (faithful).
4. **Stale `p`** reaching `:26020` via the auto-bribe path `:26021→26037` (see sub/bhf). Flag to the capture-chain owner.
5. **`gz(sp)=0`** passes the companion check (`:20009` tests `=1`). Can gz reach 0? `:4651` sets gz=1, never 0, and the boss is roster[0]. Probably unreachable; guard `gang_size > 1` is equivalent under that invariant. Confirm.
6. **Trap at the bank reuses the shop text** "vor dem laden" (`:17008`). Faithful: reuse the same theme key.

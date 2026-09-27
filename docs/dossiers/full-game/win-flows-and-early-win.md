# Map win flows (cash transport, mayor hit) and the early win

## 1. Header

- **Lines:** arming overlay `mf-prg.bas:2002-2003`; triggers `:2035`, `:2045` (cell 569 → `la=13`), `:2046` (cell 861 → `la=14`); cash transport `:23000-23030` → loot `:20050-20060`; mayor hit `:24000-24020`; early win `:1011` → `:40000` (rem) → `:40100-40166`; tips from pub `:12200-12252`.
- **Entry paths:**
  - Cash transport: map step onto cell 569 while `tp(sp)=3` → `:2045 la=13:ln=1:gosub23000:goto2060`.
  - Mayor: map step onto cell 861 while `tp(sp)=5` → `:2046 la=14:ln=1:gosub24000:goto2060`.
  - Early win: turn start, `:1011` after `gosub4000` (upkeep) if `ra(sp)=10 and x5%(sp)>0 and x6%(sp)>0`.
- **Not entered via** `:3000`/SEQ: no menu, `ll(sp)` not set, `:3045` not involved; `po(sp)` is **not** moved onto the event cell.

## 2. Research coverage verdict

**Covered in outline; gating mechanism and several consequences missing; one misleading summary.**

- Special cells, overlay pokes, flag setting: data-structures.yaml:53-60, systems-analysis.yaml:144-147, game-flow.yaml:81-83 — correct.
- **Gap:** no research file states that cells 569/861 are plain road (`karte` code 156; verified `src/karte` byte `999-cell`, reversed layout per data-structures.yaml want/karte note) and therefore the flows fire **only** while the overlay is poked (`tp=3`/`tp=5`), because `:2035` routes only non-156 targets to `:2045/2046`. data-structures.yaml:891-895 calls them "dynamic overlays", which is right, but edge-cases.yaml:19-20 and systems-analysis.yaml:201-202 read as if the cells always trigger.
- **Gap:** cash-transport payout — systems-analysis.yaml:87 "payout via 20050 with bonus" without the numbers (7000-9999 $, +8 score).
- **Gap:** mayor hit awards **no score** (no `gosub1160` in `:24000-24020`); research lists only "7000$, new papers, x6".
- **Misleading:** game-flow.md:18,44 "Early win (rank 10 + flags)" — the early win runs the same ranking as year end (`:40000` is a `rem` that falls into `:40100`); the triggering player is not declared winner unless they have the top score. game-flow.yaml:83 states the fall-through correctly.
- **Gap:** win flags are never cleared (only writes: `:23030`, `:24020`; `dim :103`).
- Port: engine/movement.py treats `special_cells` (content/map/city.yaml:177-179) as static and checks street (156) first (movement.py:250), so the "special" branch (movement.py:304) is unreachable on the real map.

## 3. Options

No menus. Prompts only via `:1100` press-key; combats are unconditional.

## 4. Behaviour walk

### A. Arming (tips) and the overlay
A1. Pub tip `:12200` needs `ra>3`; `:12210` RNG `int(rnd(1)*3)<>0` (2/3) → "leider habe ich nichts"; price `:12215 p=1000+int(rnd(1)*3)*500` (1000/1500/2000); `:12225 tp(sp)=int(rnd(1)*5)+1`. P(tp=3) = P(tp=5) = 1/15 per paid-for attempt (1/5 per purchase). Buying a new tip overwrites the old one.
A2. `:2002-2003` on every map (re)draw (`:2000`, i.e. after each step/location via `:2060 goto2000`): `tp=3` → poke screen code 135 colour 6 at cell 569; `tp=5` → code 130 colour 2 at cell 861. Only one tip can be held → at most one event cell armed.
A3. Stepping toward an armed cell: `:2035 peek<>156` → `:2045/2046` position test → flow. Unarmed, the cell is road: normal step (`:2040`), roadblock gate applies.

### B. Cash transport `:23000-23030`
B1. `:23001` load "gtp-pic" (unconditional, ignores the graphics mode `lm`).
B2. `:23005` header "geldtransport-ueberfall".
B3. `:23010` `gz(sp)<3` → "du hast zuwenig gangster!", `tp(sp)=0` (tip lost), pause, return.
B4. `:23015-23020` "leider wird der geldtransport von einer polizeieskorte begleitet!" + pause.
B5. `:23025` fight: `bn$(0)="eskorte"`, `gz(0)=10`, `e=50`, `w=7` (gewehr), grid `kgtp`; `gosub5000` (player side = whole roster, AI side 2). `s=2` → `goto26020` (capture chain; `tp` cleared only if sentenced, kept on bribe/flight escape).
B6. `s=1`: `:23030` `x=4:gosub1160` (+4), `x5%(sp)=1`, `goto20050`.
B7. `:20050` RNG `p=int(rnd(1)*3000)+4000` (`-500*(la=10andln=1)`=0 because `la=13`); `:20051` `x=tp(sp)=3 and la=13` → `tp(sp)=0`, `p+=3000` → **7000..9999 $** uniform.
B8. `:20055-20060` "du hast es geschafft! deine beute betraegt `p` $!"; `ka+=p`; `x=4:gosub1160` (+4 → **+8 total**, clamped per call); pause; return → `:2060 ms-=5`.

### C. Mayor hit `:24000-24020`
C1. No picture, no header, no gang-size check.
C2. `:24005` fight 1: `bn$(0)="leibwaechter"`, `gz(0)=5`, `e=20`, `w=7` (gewehr), grid `ks`. `s=2` → `:26020`.
C3. `:24010` fight 2: `bn$(0)="buergermeister"`, `gz(0)=1`, `e=30`, `w=1` (messer), grid `ks`. Player energy damage from fight 1 carries over (persisted in `ge$`); gangsters at 0 energy are still placed (`:30000` places all `gz`). `s=2` → `:26020`.
C4. `:24015-24017` "mord!!! fuer deine ruchlose tat bekommst du auch noch 7000 $ und einen neuen pass!"
C5. `:24020` `ka+=7000`; `ag(sp)=ag or 1` (passport); `tp(sp)=0`; `x6%(sp)=1`; pause; return → `:2060`. **No score change.**

### D. Clearing / repetition
D1. `tp` cleared by: success (`:20051`, `:24020`), too-small gang (`:23010`), any sentence (`:26045`). Not by bribe/flight escape → cell stays armed, can retry the same turn if `ms>0`.
D2. `x5%/x6%` never cleared: survive jail, gang-war defeat, eviction. A later tip 3 still allows another transport for cash/score.

### E. Early win `:1011` → `:40000` → `:40100-40166`
E1. Checked once per turn start of each player, after upkeep (`:1011 gosub4000` — so a promotion to rank 10 committed at `:4030` counts the same turn), before the job shift (`:1012`) and before the jail skip (`:1013`, so a jailed player can win).
E2. `ra(sp)=10` means `nr=10` was committed, i.e. `gf>=99.9` at the last `:1160` (`nr=int(gf/11.1)+1`).
E3. Year-end check `:1010` runs first on a round wrap: if the end year is reached, `:40100` runs and the early win is never checked.
E4. `syslh,"sieg-pic"` (victory bitmap), `goto40000` (`rem ende`) → falls into `:40100`:
  - `gosub4500` standings (date = current `ja`, no advance),
  - winner(s) = highest `gf` among **all** players, ties shared (`:40105-40106`) — exactly the port's `top_scorers`,
  - sole: "`sp$` hat gewonnen! du warst von allen der brutalste, gemeinste und schlaueste!" (`:40115-40117`); tie: `:40150-40160`.
  - `:40165-40166` two key waits, `run` (restart; no save).
E5. Consequence: the triggering player can be listed in a tie or even lose to a player with strictly higher `gf` (e.g. 100 vs 99.95). `gf` is compared before this player's `:1013` truncation.

## 5. State table

| BASIC | Meaning | Port |
|---|---|---|
| `tp(sp)` | tip 1-5 | `Player.tip_target` / `TipSet`, `TipClear` |
| `x5%(sp)`, `x6%(sp)` | win flags | `Wanted.x5`, `Wanted.x6` (bool, exist) — NEW effect to set (FlagSet is global-only) |
| `po(sp)+x` | target cell | `try_move` target |
| `la`, `ln` | 13/14, 1 | `special_cells` `la` (city.yaml:178-179) |
| `gz(sp)` | gang size | `len(roster)` |
| `ka` | cash | `MoneyChange` |
| `ag(sp) or 1` | passport | `Contraband.fake_papers` |
| `gf/nr` | score | `ScoreAndRank(4)` ×2 (cash transport) |
| `ra(sp)` | rank | `Player.rank` |
| `bn$(0)`,`gz(0)`,`e`,`w`,`kf$` | encounters | NEW encounter YAMLs: `cash_transport_escort` (10, w7, e50, `kgtp`), `mayor_guards` (5, w7, e20, `ks`), `mayor` (1, w1, e30, `ks`) |
| grid `kgtp` | combat backdrop | NEW `content/combat/kgtp.yaml` (source `src/kgtp`) |
| `lm`, `sieg-pic`, `gtp-pic` | graphics | presentation/theme |

## 6. Dependencies

- movement: dynamic overlay — a special cell is live only when `tip_target` matches (3 → 569, 5 → 861); must be tested **before** the street check (or the grid code treated as non-street for that player). Map rendering must draw the armed icon (theme).
- A config handler for each flow launched by the client when `try_move` reports `kind="special"`; `po` must not move; charge `MsChange(-5)` after (`:2060`).
- Capture chain for `s=2`.
- Bank loot formula `:20050-20060` is shared with ban/bhf (other agent) — factor as a shared helper.
- Combat: multi-fight handler (two `StartCombat` in one handler) — supported by the protocol; energy persistence between fights happens through committed `EnergyChange` — verify the second fight sees fight-1 damage (effects buffered in one ctx; `setup_combat` reads `ctx.state` which may be pre-commit).
- Early win: session `next_turn` needs a hook after upkeep: if rank==10 and x5 and x6 → run year-end flow (reuse `game_end.year_end` with a `victory` intro key) and end.

## 7. Open questions

1. Early-win presentation: show sieg-pic then the normal ranking (faithful: may crown someone else) — confirm we keep that, not "trigger player wins".
2. Does the second mayor fight see the first fight's energy loss in the port? (effects buffer vs. state read inside one handler) — needs a test.
3. Arming overlay while jailed / on another player's turn: overlay is per active player only (`:2002` uses `tp(sp)`); nothing to decide but the renderer must use the active player's tip.
4. `run` restart at `:40166` vs port session end — existing year-end already ends the session; keep.
5. Cash transport can be repeated after `x5` is set (for 7000-9999 $ and +8 score) — faithful; confirm.

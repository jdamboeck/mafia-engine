# Gang war (Bandenkrieg)

## 1. Header

- **Lines:** turn menu `mf-prg.bas:1015-1050` (option 3 at `:1021`, dispatch `:1035 onxgosub1200,2000,27000`); gang war `:27000-27045`; prison-brawl branch `:27100-27150`; combat `:30000-30520`; helpers `:1100`, `:1110/1115`, `:1125`, `:1160`, `:1350/1365` (unpack/pack `ge$`).
- **Entry:** turn menu option 3, any time the menu is shown (start of a free turn and again after every menu action while `ms>0`, `:1045`). State: `sp` active, `ms>0`.
- **Exit:** every path ends in `return` (directly or via `:1100`) → `:1045 ifms>0goto1015` (menu again) else `:1050 goto1010` (next player).

## 2. Research coverage verdict

**Largely uncovered.** Research only names the entry and the solo refusal:
- game-flow.yaml:35,60 / game-flow.md:17,28 / combat-system.md:56 — entry `gosub27000`, "choose opponent; then gosub 30000 (vs player) or plunder/jail logic" — no formulas.
- edge-cases.yaml:173-174 — solo refusal `:27000` (correct).
- **Gaps:** date gate `:27001`, opponent list, the jailed-opponent branch `:27100-27150`, plunder formula `:27025`, vehicle/passport/alcohol transfer `:27030-27041`, score changes, `ms-=10`, who controls which side, energy persistence. None of these are documented.
- **Contradictions:** none found (nothing to contradict).
- Port: no turn menu exists (clients/terminal/session.py `run_turns` → `map_turn` directly); no gang-war code.

## 3. Menu and guards

Turn menu (`:1020-1022`): `1 uebersicht` (`:1200`), `2 durch die stadt gehen` (`:2000`), `3 bandenkrieg` (`:27000`), `4 naechster spieler` (`:1031 goto1010`), F1 = graphics-load mode toggle (`:1032`). Input `:1030` `GET`, only 1-4/F1.

Option 3 carries no shell guard in the original — the refusals print inside the handler. If a shell guard is wanted it is expressible without NOT: `player_count > 1 and (year > 1925 or month >= 4)` = depth 2 — fits, but hiding the option would drop the two refusal messages; faithful is handler-internal. Recommend: no guard.

Opponent picker (`:27010-27017`): keys `1..sz` except `sp`; `0`/non-digit = back.

## 4. Behaviour walk

### A. Refusals
A1. `:27000` `sz=1` → "bei solo-spiel nicht moeglich!" + pause → return.
A2. `:27001` `ja<1925+4/12` → "erst ab 4/1925 moeglich!" + pause → return. `ja` starts 1925.0 and gains 1/12 per round (`:1010`), so the first allowed round is `ja=1925+4/12`, which the header displays as month **5** (`:1017 1+int((ja-x)*12)`) — the message says 4/1925. Float accumulation of `1/12` could push it one round later (Q3). In port terms: allowed iff `year>1925 or month>=4` (0-based `Clock.month`).

### B. Opponent choice
B1. `:27005-27006` "bandenkrieg!" + border flash (presentation).
B2. `:27007-27013` "mit wem willst du dich anlegen:"; per `i<>sp`: `i`, gang name `bn$(i)`, cash `ka(i)` $, and a bar of `gz(i)` reverse glyphs (`left$("{rvon}AAAAAAAAAA",1+gz(i))`) = gang size. Jailed players are listed like others.
B3. `:27015-27017` `GET` → `us=val(x$)`; `us=0` → return (no pause); `us<1 or us>sz or us=sp` → wait for another key.
B4. `:27018` `gs(us)<>0` → section D (no choice offered).

### C. Duel `:27020-27045`
C1. `ks(1)=us` (defender = side 1), `ks(2)=sp` (attacker = side 2), `kf$="ks"` (street grid), `gosub30000` directly (not `:5000`): both sides are full player rosters with their own weapons and **current** energies (0-energy gangsters still placed and fight). Neither side is AI (`:30110` AI only when `ks(s)=0`) → hot-seat; each player moves his own side. `:30100 s=1` → **the defender acts first**. `q` = the side to move concedes (`:30136`).
C2. Energy damage to **both** rosters persists (`:30260-30265` write `ge$(ks(x),i)`).
C3. `a=ks(s)` winner, `b=ks(1-(s=1))` loser — either player may win.
C4. `:27025` RNG `p=int(rnd(1)*ka(b)/6)+int(ka(b)/4)` → `int(ka/4) .. int(ka/4)+ceil(ka/6)-1`, ≈ 25 %-41.7 % of the loser's cash.
C5. `:27026-27028` to the winner: "`sp$(a)`, du findest beim pluendern deines gegners `p` $. ausserdem erbeutest du den alkoholvorrat."
C6. `:27028` `tm(b)=0` (loser on foot) → skip. Else `:27030-27031` "willst du auch noch den `tm$(tm(b))` mitnehmen (j/n) ?" `:1115` answered by the **winner**: "j" → `tm(a)=tm(b)`, `tm(b)=0` (loser on foot; winner's previous vehicle is discarded). `ms` is **not** recomputed (compare aut `:14050`).
C7. `:27035` `ka(a)+=p`, `ka(b)-=p`; `ag(a)=ag(a) or (ag(b) and 1)` (winner gains loser's passport); `ag(b)=ag(b) and 254` (loser loses passport). Counterfeit bit untouched.
C8. `:27040` `x=tk(tm(a))-ta(a)` (free tank capacity of the winner's current vehicle, `tk` = vehicles.yaml `tank`); `x>ta(b)` → `x=ta(b)`. `:27041` `ta(a)+=x`, `ta(b)-=x`. If the winner is over capacity (`x<0`, e.g. after taking a smaller vehicle) barrels flow **to the loser**.
C9. `:27041` `x=3:gosub1160` applied to **`sp` (the attacker) regardless of outcome**, then `sp=b: x=-1:gosub1160: sp=y` (loser −1). Net: attacker wins → attacker +3, defender −1; defender wins → attacker +3−1 = **+2**, defender (winner) **0**.
C10. `:27045` `ms-=10` → pause → return → menu if `ms>0`.
C11. No jail, no deaths, no roster removal, no direct rank change (ranks follow `nr` at each player's next `:4030`).

### D. Opponent in jail `:27100-27150`
D1. `:27100-27110` "`sp$(us)` sitzt im knast. ein mitgefangener will ihn fuer dich 'aufmischen'. er verlangt 3000 $." + `:1110` j/n; "n" → return.
D2. `:27115` `ka<3000` → `:1125` → return. `:27120` `ka-=3000`; "verteidige dich, `sp$(us)`!" + pause.
D3. `:27125` `bn$(0)="mr.bonebreaker"`, `gz(0)=1`, `gw(0,1)=3` (schlagkette), `ec(1)=50`, `ks(2)=0` (AI).
D4. `:27130` save `z1=gw(us,1)`, `z2=gz(us)`; set `gw(us,1)=0` (haende), `gz(us)=1` (boss alone); `ks(1)=us`, `kf$="kg"` (prison grid); `gosub30000`. Side 1 is controlled by the **jailed player `us`** (hot-seat).
D5. `:27135` restore `gw(us,1)`, `gz(us)`. Boss energy damage persists.
D6. `s=1` (jailed boss wins) → `:27140` RNG `x=int(rnd(1)*2)+1` (1 or 2, ½ each): "`sp$(us)`! deine strafe wird wegen dieser schlaegerei um `x` monat(e) verlaengert!" `gs(us)+=x`.
D7. `s=2` (bonebreaker wins, incl. `us` conceding) → `:27146` `a=sp:b=1:gosub1350:en=0:gosub1365` → sets the **attacker's own** gangster 1 energy to 0 (source bug; `us`'s boss is already at 0 if killed).
D8. `:27150` `ms-=10`; `x=2:gosub1160` (attacker +2) in both outcomes; pause; return.

## 5. State table

| BASIC | Meaning | Port |
|---|---|---|
| `sz`, `sp` | players, active | `Clock.player_count`, `Clock.active_player` (0-based) |
| `ja` | date | `Clock.year` + `Clock.month` |
| `us` | chosen opponent | local |
| `bn$(i)`, `sp$(i)` | gang/player name | `Player.gang_name`, `Player.name` |
| `ka(i)` | cash | `MoneyChange(player=i)` |
| `gz(i)` | gang size | `len(roster)` |
| `gs(us)` | jail months | `Wanted.jail_months` — NEW add-months effect |
| `ks(1..2)`, `kf$`, `s` | combat sides/grid/winner | `StartCombat(sides, grid, cpu_sides=())`, `CombatResult.winner` |
| `ge$` energy | vitality | `Combatant.vitality` / `EnergyChange(player=, gangster=)` |
| `gw(us,1)` temp 0, `gz(us)` temp 1 | brawl setup | build side 1 from `roster[0]` with weapon 0 (no state write needed) |
| `tm(i)`, `tm$`, `tk` | vehicle, name, tank | `Player.vehicle`, vehicles.yaml `name`/`tank` — NEW vehicle-set effect |
| `ta(i)` | alcohol | `BarrelChange(player=i)` |
| `ag(i) and 1` | passport | `Contraband.fake_papers` |
| `gf`,`nr` via `:1160` with `sp` swapped | score | `ScoreAndRank(player=i)` |
| `ms` | movement | `MsChange(-10)` |
| `mr.bonebreaker` | NPC | NEW encounter (1, w3, e50, grid `kg`) |
| grid `kg` | prison backdrop | NEW `content/combat/kg.yaml` (source `src/kg`) |

## 6. Dependencies

- **Turn menu** (NEW in the client/session): options 1-4, re-shown while `ms>0` (`:1045`); option 4 ends the turn voluntarily; map exit key `_` (`:2019`) returns to the menu. Overview (option 1, `:1200-1245`) also unported.
- **Combat engine:** hot-seat is supported (`StartCombat.cpu_sides=()`, engine/interactions.py:141-147), but `fight_loop._run_combat` persists energy only for side 1 and targets the **active** player (engine/fight_loop.py:196-212, `EnergyChange` without `player`). Gang war needs both sides persisted to their owners (side 1 = defender, side 2 = attacker); the brawl needs side 1 persisted to `us`. Needs a per-side owner field (e.g. `roster_owner`) — engine change.
- Player-side placement for side 2 as a *roster* side (`:30000 kp(i,j)=129-18*(i=2)+p(j)`) — `setup_combat` currently builds side 2 as an NPC party only.
- Prompts to a non-active player (winner's vehicle j/n; `us` driving side 1) — interaction protocol has no addressee; fine for the hot-seat terminal, needed for network.
- Clock: gate on `Clock.month`.

## 7. Open questions

1. **Score bug `:27041`** — attacker always gets +3, the loser −1 (so a losing attacker nets +2, a winning defender 0). Port literally (fidelity bar) or fix to winner +3? Evidence: `x=3:gosub1160` precedes the `sp=b` swap.
2. **Brawl bug `:27146`** — zeroes the *attacker's* boss energy when the bonebreaker wins. Port literally or target `us`?
3. **Date gate** — `ja<1925+4/12` with accumulated `1/12` floats: first allowed displayed month 5 or 6? VICE check (`ja` after 4 additions vs `1925+4/12`). Message text says "4/1925" either way.
4. Alcohol transfer can go negative for the winner (`:27040` no floor) — port literally?
5. Vehicle swap leaves `ms` untouched for the attacker (`:27031`) — confirm.
6. Hot-seat control of side 1 by the defender (and by the jailed player in the brawl) — the client must hand the keyboard over; confirm UX (announce whose side is moving).
7. `ka(b)` negative → `p` negative (winner pays) — can `ka` be negative anywhere? (pol negative-month exploit could not; `kdh` debt does not touch `ka` sign) — probably unreachable; decide whether to clamp.

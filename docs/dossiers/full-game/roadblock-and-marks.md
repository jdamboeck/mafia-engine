# Roadblock (Strassensperre) and the contraband marks

## 1. Header

- **Lines:** gate `mf-prg.bas:2041`; body `:6000-6036`; upkeep aging/decay `:4050`, `:4055`, `:4056`; overview display `:1220-1225`.
- **Entry:** only from a **street step** in the map loop: `:2035` target is road (`peek=156`) → `:2040` `po+=x`, `ms-=1` → `:2041 if ms/20=int(ms/20) and int(rnd(1)*5)=0 and ra(sp)>3 then gosub6000:goto2060`. Never on location entry, never on the event cells, never in the turn menu.
- **State on entry:** `ms` already decremented (0, 20, 40 or 60 — any multiple of 20 incl. 0); `p=br+po(sp)` (stale, matters for capture `:26021`).
- **Exit:** clean → `:6025 goto1100` → return → `:2041 goto2060` → **`ms-=5`**. Caught → `goto26020` (capture chain; returns into the same `goto2060`).

## 2. Research coverage verdict

**Gate covered; body partly; decay undocumented.**

- Gate (systems-analysis.yaml:154, wanted-system.md "Street block (2041)") — correct.
- Body `:6015-6036` order and the 1/3 free pass are **not** documented in research (wanted-system.md: "6000 → 26020 if caught" only). ble dossier facts (passport protects, counterfeit/alcohol betray) are not written down anywhere in research docs.
- Flow "gosub 6000 -> goto 2060 (if not caught) or 26020 (if caught)" (systems-analysis.yaml:156) — incomplete: both paths end at `goto2060` (the caught path via the gosub return), and the clean path costs 5 ms.
- `:4050` (`pl` aging) and `:4055-4056` (mark decay) — only listed as "not ported" in upkeep.py:50-51; no research description found.
- data-structures.yaml:906 and location-extraction.yaml:552 notes "ln = option index from SEQ" — wrong, `ln` is the within-location tile (`:2050 ln=peek(ua+2)`); irrelevant here but misleading for every location.
- Port: engine/movement.py:439 `police_interrupt_would_fire` implements the gate but is **never called** (grep); the body is not built.

## 3. Options

Not a menu. The roadblock asks nothing; outcomes are automatic.

## 4. Behaviour walk

### Gate `:2041`
G1. Evaluated after every successful street step (`:2040`), with the *new* `ms`.
G2. Condition: `ms mod 20 = 0` **and** RNG `int(rnd(1)*5)=0` (1/5) **and** `ra(sp)>3` (ranks 4-10). BASIC evaluates all three terms (draw always consumed); order irrelevant for behavioural fidelity.
G3. Reachable `ms` values: 60/40/20/0 depending on start `tr(tm)` (25,35,40,40,60,35) and 5-ms location/roadblock costs. Firing at `ms=0` happens on the last step of the turn.

### Body `:6000-6036`
B1. `:6000-6010` "strassensperre!!!" / "ausweiskontrolle! der polizist verlangt deine papiere!" then a delay loop (`:6015 fort=1to2000`).
B2. RNG `:6015 int(rnd(1)*3)=0` (1/3) → B6 (nothing found) — **before** any mark is checked.
B3. `:6016` `(ag(sp) and 2)<>0` (counterfeit) → `:6030` "dein blueten-schwindel ist aufgeflogen!" + pause → `goto26020` (caught). Takes priority over the passport.
B4. `:6017` `ta(sp)<>0` (any alcohol barrels) → `:6035-6036` "der kerl hat deine alkoholfaesser entdeckt! es ist aus..." `ta(sp)=0` (all barrels confiscated) + pause → `goto26020`. Also beats the passport.
B5. `:6018` `(ag(sp) and 1)<>0` (passport) → B6.
   else `:6020` "er hat einen steckbrief von dir!" + pause → `goto26020`.
B6. `:6025` "er hat nichts zu beanstanden." → pause → return.
B7. Capture probabilities per roadblock: clean player with passport and no contraband 0; anything else 2/3. Per step at a qualifying `ms`: ×1/5.
B8. The marks are **not** cleared by being caught (only `ta` is zeroed at `:6036`); a counterfeit mark keeps betraying until it decays.

### Upkeep `:4050-4056` (every turn start, incl. jailed turns — `:1011` runs before `:1013`)
U1. `:4050` `pl(sp)=pl(sp)+(pl(sp)>0)` → decrement by 1 while positive (C64 true = −1).
U2. `:4055` RNG `int(rnd(1)*8)=0` (1/8) → `ag=ag and 254` (lose passport).
U3. `:4056` RNG `int(rnd(1)*8)=0` (1/8) → `ag=ag and 253` (lose counterfeit mark). Two independent draws, both consumed every turn even when the bits are 0. Expected lifetime of each mark: 8 turns (geometric). No message is printed for either.
U4. Upkeep order: after rent (`:4045-4046`), before the arms deal (`:4060`).

### Who sets / clears the marks
| Mark | Set | Cleared |
|---|---|---|
| passport (bit 1) | ble `:22020`; mayor hit `:24020`; gang-war winner inherits loser's `:27035` | decay `:4055`; gang-war loser `:27035` |
| counterfeit (bit 2) | ble `:22120` | decay `:4056` only |
| alcohol `ta` | pub buy `:12035`; gang-war plunder `:27041` | roadblock `:6036`; pub sell `:12075`; gang-war `:27041` |
| `pl` | pol `:21020` | aging `:4050`; negative pol input (see pol Q2) |

## 5. State table

| BASIC | Meaning | Port |
|---|---|---|
| `ms` | movement | `Player.ms` |
| `ra(sp)` | rank | `Player.rank` |
| `ag(sp)` bit 1 / bit 2 | passport / counterfeit | `Contraband.fake_papers` / `Contraband.counterfeit` (exist, int, unused) |
| `ta(sp)` | alcohol barrels | `Contraband.alcohol_barrels` / `BarrelChange(-ta)` |
| `pl(sp)` | chief-bribe months | `Wanted.bribe_months` |
| `po`, `p` | position / stale screen addr | `Player.po`; stale `p` see capture Q1 |

## 6. Dependencies

- engine/movement.py `try_move` must call the gate after a `kind="step"` and hand over to a config roadblock flow (engine must not know the body: it is game content). Needs a hook "after street step" analogous to the upkeep runner — the roadblock asks no questions but may start capture (which prompts) → it must run as a handler through the interaction driver, not inside the pure `try_move`.
- Roadblock costs `MsChange(-5)` on every exit path (`:2060`).
- Capture chain (`police-capture-and-jail.md`).
- Upkeep: add `:4050`, `:4055`, `:4056` slots between rent and arms deal (upkeep.py currently documents them as skipped).
- NEW effects: contraband bit set/clear; bribe-months change.

## 7. Open questions

1. Hook placement: engine-level "post-step event" seam vs. config handler invoked by the client after `try_move` returns `kind="step"`? The gate reads rank + RNG, which is game rule → config-owned; the seam is engine.
2. Gate draw order: port's `police_interrupt_would_fire` skips the RNG draw at rank ≤3; BASIC always draws. Allowed by the behavioural fidelity bar; record.
3. Should the decay print anything? BASIC is silent (`:4055-4056`). Port silently.
4. `ms` values at which the gate can fire depend on the 5-ms costs of locations/roadblocks, so porting the exact `ms` accounting (incl. `:3045` leave −5, which the port currently omits) changes roadblock frequency — decide the leave-cost question first.

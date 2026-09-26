---
title: "Porting BASIC relational terms: a true relational contributes -1, not +1"
date: 2026-07-15
updated: 2026-09-26
category: architecture-patterns
module: data/game_configs/mafia_1920s
problem_type: porting_convention
component: handler
severity: high
applies_when:
  - "Porting any mf-prg.bas formula that multiplies a coefficient by a relational, e.g. `gf = gf - x8*(gf<100)`"
  - "Deciding the SIGN of a term gated by a `(a=b)` / `(a<b)` / `(a>b)` comparison"
  - "Writing or reviewing a waf/sph/racket handler whose score/stat/price math has a `(...)` boolean factor"
  - "Tempted to use the research interpretation layer's `true = +1` reading for a ported formula"
tags:
  - porting
  - basic
  - boolean-as-integer
  - fidelity
  - waf
  - score
  - fnm
related_components:
  - handler
  - effects
---

# Porting BASIC relational terms: a true relational contributes -1, not +1

> **REVERSED 2026-07-19 by the #47 fidelity audit.** This doc previously pinned
> `true = +1`. That was wrong, and it had propagated inverted signs into shipped
> handlers *and* into the tests guarding them. The corrected rule is below; the
> post-mortem of the bad pin is kept at the end so the reasoning error is not
> repeated.

## Context

The original *Mafia* BASIC uses the idiom `<expr> ± k*(<relational>)` — a comparison
multiplies a coefficient. In Commodore BASIC a true relational evaluates to **`-1`**
(all bits set) and false to `0`. This engine ports that arithmetic **as written**.

This is easy to get wrong and silently inverts signs (a score meant to go *up* goes
*down*). It surfaces in the `waf` weapon-shop buy score (`mf-prg.bas:13065,13072,13073`),
the `ln`-modified training gains (`13110-13130`), the `fnm` rent function (`115`), the
combat side anchors/colours (`30000-30015`), and the job-completion score (`25560`).

## Guidance

**Rule: when porting a `mf-prg.bas` formula, read every relational factor `(a<b)` /
`(a=b)` / `(a>b)` as `-1` when true and `0` when false.** Evaluate the expression
exactly as C64 BASIC would.

**Why this is settled.** Three sites in the source are *structurally* decidable: under
`true = +1` the program would be malformed, not merely differently balanced. Three more
are only *coherent* under `true = -1`:

1. **`:30015` / `:30115` — `poke211,-20*(i=2)`.** `$D3`/211 is the KERNAL cursor-*column*
   zero page location and cannot hold a negative value. `true=-1` → column **20** (side 2
   labelled on the right half of the 40-column screen, mirroring side 1 at column 0).
   `true=+1` → **-20**, impossible.
2. **`:30010` — `pokefr+kp(i,j),2-4*(i=2)`.** A C64 colour code is 0-15. `true=-1` → **6**
   (blue) for side 2 vs. 2 (red) for side 1. `true=+1` → **-2**, not a colour.
3. **`:30108` — `s=1-(s=1)`, the side toggle.** Must map 1↔2. `true=-1`: `1-(-1)=2`, and
   `1-0=1`. ✓  `true=+1`: `1-1=0` — a side that does not exist.
4. **`:30240` — `fori=1to1-2*(w=4)`.** Supporting, not decisive. `true=-1` → 3 flash
   frames for `w=4` (the throwing stars, `wurfsterne`) against 1 for other weapons.
   `true=+1` gives a bound of `-1`, but a C64 `FOR` loop tests at `NEXT` and runs its body
   once anyway, so nothing breaks; this site only fits `-1` better.
5. **`:13073` vs. `:13065`/`:13072` — the weapon buy score.** Weapon indices ascend in
   power and price (`DATA 50100-50115`, read as index 0-8 at `:121`: 0 `haende` 0$ up to 8 `handgranaten` 10000$), so at
   `:13072` `x > gw` is an **upgrade**. `gf` is notoriety, positive for successes
   (`x=2` for won fights/heists) and negative for failures (`x=-2`, `-5`, `-10`). Only
   `true=-1` makes arming/upgrading *raise* notoriety and downgrading *lower* it.
6. **`:115` + `:10035` — `fnm` and the affordability check.** `fnm(1)` under `true=+1` is
   `-50`, i.e. a room that *pays the tenant* — and it makes `:10035`'s `ka < x*p` check
   permanently false (dead code). Under `true=-1`, `fnm(1) = 150` (a premium unit) and the
   check is live. An unreachable branch is the same tell that pinned `kz`'s direction at
   `:4305`.

**Consequences for the `waf` buy score (`13065`,`13072`,`13073`), to encode directly:**

- `gf = gf - x8*(gf<100)` → score **up** by `x8` (first weapon / upgrade), only while
  `gf < 100`.
- `gf = gf + x8*2*(gf>0)` → score **down** by `2*x8` (a downgrade), only while `gf > 0`.
- When the `(gf<100)` / `(gf>0)` guard is false, the term is `0` — **no** score change.

For the `ln`-modified training gains (`13126-13127`): `in = in+3-2*(ln=1)` is `+5` at
`ln=1` (not `+1`), and `bt = bt+2-3*(ln=2)` is `+5` at `ln=2` (not `-1`). Note the
`true=+1` reading made the `bt` gain *negative* — training that damages the stat.

For `fnm` (`:115`): `fnm(1) = 150`, `fnm(3) = fnm(4) = 100`, else `50`.

For the job-completion score (`:25560`, `x=3+3*(jo(sp)=2)`): every job scores `3`, and the
croupier (`jo=2`) scores **`0`** — not `6`. The croupier is the job that already paid an
immediate per-shift bonus (`:25125`), so it earns no completion award.

## The research interpretation layer

`research-data/`'s prose used to gloss these sites with the `true=+1` value (`fnm`
`result_range: "-50 | 0 | 50"`, `25560` "3 or 6", `30015` "x = 0 or -20", `13072`
labelled a downgrade). The research repo corrected 16 such descriptions and now states
the rule for its agents (research commit `478ecc3`, 2026-09-26). The principle stands:
the **raw `mf-prg.bas` line wins over any interpretation**, because a gloss is a derived
reading, not observed behaviour.

## Post-mortem: why the original pin was wrong

The superseded version of this doc pinned `true = +1` on a single argument: that
`fnm(1) == -50` is an *observed result* which only `+1` reproduces. It is not observed —
`research-data/pass-2/game-logic.yaml` derives that `result_range` by applying `true=+1`
to the very same line. The argument was **circular**: the convention was justified by a
number the convention itself had produced. It then claimed to be "settled, not a judgment
call," which discouraged the per-site cross-checks that would have caught it.

Two lessons:

- **A derived value is not evidence for the rule that derived it.** Pin conventions on
  *structural* constraints (a column that cannot be negative, a colour code that must be
  0-15, a branch that must be reachable) — those cannot be argued into agreement.
- **Beware a convention that makes code dead.** Both wrong pins found in this project
  (`fnm`/`:10035` and `kz`/`:4305`) announced themselves by rendering a branch
  unreachable. Treat "this check can never fire — that is faithful" as a red flag.

## Verification

Structural re-confirmation, any of which fails under `true=+1`:
`oracle.py conclude 30015 "poke211 is a column and cannot be negative"`,
`oracle.py conclude 30010 "2-4*(i=2) must be a colour code 0-15"`,
`oracle.py conclude 30108 "s=1-(s=1) must toggle between sides 1 and 2"`.
The `waf` buy-score, training, `fnm`, combat-anchor and job-completion tests encode the
corrected signs and are the regression guard.

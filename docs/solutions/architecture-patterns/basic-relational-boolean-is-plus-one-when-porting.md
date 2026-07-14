---
title: "Porting BASIC relational terms: a true relational contributes +1, not -1"
date: 2026-07-15
category: architecture-patterns
module: data/game_configs/mafia_1920s
problem_type: porting_convention
component: handler
severity: high
applies_when:
  - "Porting any mf-prg.bas formula that multiplies a coefficient by a relational, e.g. `gf = gf - x8*(gf<100)`"
  - "Deciding the SIGN of a term gated by a `(a=b)` / `(a<b)` / `(a>b)` comparison"
  - "Writing or reviewing a waf/sph/racket handler whose score/stat/price math has a `(...)` boolean factor"
  - "Tempted to use the textbook C64 `true = -1` reading for a ported formula"
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

# Porting BASIC relational terms: a true relational contributes +1, not -1

## Context

The original *Mafia* BASIC uses the idiom `<expr> ± k*(<relational>)` — a comparison
multiplies a coefficient. In genuine Commodore BASIC a true relational evaluates to `-1`
(all bits set), so `50*(ln=3)` would be `-50` when `ln=3`. **This engine does not port raw
C64 numeric semantics — it ports the research's documented *interpretation* of each
formula (behavioral fidelity, CLAUDE.md).** In that interpretation model a true relational
contributes **`+1`**.

This is easy to get wrong and silently inverts signs (a score meant to go *down* goes
*up*). It surfaces in the `waf` weapon-shop buy score (`mf-prg.bas:13065,13072,13073`), the
grenade-stock gate (`13011`), and the `ln`-modified training gains (`13110-13130`), plus
any future score/price formula with a `(...)` factor.

## Guidance

**Rule: when porting a `mf-prg.bas` formula, read every relational factor `(a<b)` /
`(a=b)` / `(a>b)` as `1` when true and `0` when false — never `-1`.** Port the *prose*
result, not a raw C64 evaluation.

**Why this is settled and not a judgment call.** It is pinned by an already-shipped,
research-verified formula: `fnm`. The source is
`deffnm(ln) = 50 - 50*(ln=3 or ln=4) - 100*(ln=1)` (`mf-prg.bas:115`), and the game's
observed result is `fnm(1) == -50` (the negative-rent quirk, encoded in
`config.yaml formula_params.fnm`). That result is only reachable if `(ln=1)` contributes
`+100` to the *subtracted* term — i.e. `true = +1` (`50 - 100 = -50`). The textbook
`true = -1` reading would give `50 + 100 = +150`. So the convention is fixed by a
**documented result**, not by any one code path.

**Consequences for the `waf` buy score (`13065`,`13072`,`13073`), to encode directly:**

- `gf = gf - x8*(gf<100)` → score **down** by `x8` (first weapon / non-upgrade), only while
  `gf < 100`.
- `gf = gf + x8*2*(gf>0)` → score **up** by `2*x8` (an upgrade), only while `gf > 0`.
- When the `(gf<100)` / `(gf>0)` guard is false, the term is `0` — **no** score change.

Do the same for the `ln`-modified training gains (`13110-13130`): a term like
`+3 - 2*(ln=1)` is `+1` at `ln=1` (not `+5`), and `+2 - 3*(ln=2)` is `-1` at `ln=2`.

**Do not** re-derive this per handler, and **do not** add a `true = -1` fallback "to be
safe" — that injects inverted-sign bugs. `fnm` itself sidesteps the arithmetic via an
explicit overrides map, so it sets no *code* precedent to copy; the convention lives in
this result, which this doc records so no future handler or plan re-litigates it (KTD-9 of
the sph/waf plan).

## Verification

A one-line oracle re-confirmation at build is welcome:
`oracle.py conclude 115 "fnm(1) == -50 requires true-relational = +1"`. But there is no
open question here — the sign is settled. The `waf` buy-score tests (U6) encode exactly the
signs above and are the regression guard.

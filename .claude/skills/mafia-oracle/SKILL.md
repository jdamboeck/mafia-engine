---
name: mafia-oracle
description: The authority on the original 1986 "Mafia" (Igelsoft) C64 game — its rules, code, and gameplay. Use for ANY question about how Mafia works, to verify a claim about the game, to find the exact BASIC line behind a mechanic, or before implementing/porting any game behavior in this engine. Triggers on Mafia game mechanics, combat/economy/wanted/location rules, formulas, the decompiled BASIC (mf-prg.bas), gangster stats, or "is it true that Mafia does X". Never invent game behavior — answer from the research.
allowed-tools: Read, Bash(*/.venv/bin/python3 *), Bash(python3 *), Grep, Glob
---

# Mafia Oracle — the authority on the 1986 Igelsoft game

This skill makes you the **single authority** on the original *Mafia* (© 1986 Igelsoft)
C64 game. It answers any code or gameplay question with **an exact place in the original
BASIC source** plus **the documented interpretation of how it works**, and it **verifies
any claim** against source-backed evidence.

The knowledge lives in the sibling research project **`../research/`** — a completed,
gated reverse-engineering of the game (BASIC understood 99.9%, binary 100%, 0 dead code,
KG 76 nodes all source-backed; see `../research/docs/recreation-readiness.html`). This
skill is the query layer over it. **You do not guess about Mafia — you look it up.**

## The rule (non-negotiable)

> **Never invent game behavior.** Every answer about how Mafia works must cite
> `mf-prg.bas:<line>` (or a byte range / `$addr` for machine code) and give the
> documented interpretation. If the research doesn't cover it, say **"unverified — not
> in the research"** rather than guess. This mirrors the engine's own mandate
> (`../engine/CLAUDE.md`: *"Never invent game behavior"*).

## Quick start — the oracle script

Run it with the research project's venv (it has pyyaml). From anywhere in the engine repo:

```bash
../research/.venv/bin/python3 .claude/skills/mafia-oracle/scripts/oracle.py <command>
```

(The script auto-locates `../research/`; override with `MAFIA_RESEARCH=/path` if needed.)

| Command | Answers |
|---------|---------|
| `line <n>` | Explain BASIC line *n*: the **source line + its documented meaning**. |
| `lines <a> <b>` | Explain a labeled block (e.g. `lines 30100 30160` = a combat block). |
| `quote <n>` | The raw BASIC source for line *n*, verbatim (no interpretation). |
| `search <text>` | Find lines / KG nodes matching a keyword (e.g. `search jail`). |
| `node <id-or-name>` | A knowledge-graph node with its **source-backed evidence**. |
| `verify <claim>` | Surface the cited evidence that confirms/refutes a claim. |
| `conclude <lines> <claim>` | **Gate a rule/cap/number behind its verbatim source** before you state it. |
| `status` | Coverage + final-state metrics (what's known, how it's verified). |

Example — the combat damage formula:

```
$ oracle.py line 30255
SOURCE  : 30255 y=int(rnd(1)*tg(w)+bt/10)+1:ifks(x)=0goto30275
MEANING : combat damage: roll y = int(rnd(1)*tg(w) + bt/10) + 1 damage; …
```

## How to answer a question (the protocol)

**The iron rule: quote source before you conclude.** A `search` result is a snippet of
the *interpretation*, not the code. Never state a rule, cap, number, or outcome from a
snippet — read the actual line(s) with `quote`/`line` first, or gate the claim through
`conclude`. (This is the exact failure to avoid: reading "5 apartment slots" in a summary
and guessing a cap of 5, when the code at `12105` caps at 10 and `12103`'s `for i=1 to 5`
is just a "do you own *any* apartment?" check.)

1. **Locate it.** Use `search <keyword>` to find candidate lines and KG nodes. Treat the
   snippets as *pointers to read*, never as the answer.
2. **Read the actual source.** `quote <n>` (verbatim) or `line <n>` (source + meaning).
   For a whole mechanic, also read the KG node (`node <id>`). You must have the raw code
   in front of you before drawing any conclusion.
3. **Before stating any rule/cap/number, run `conclude <lines> "<claim>"`.** It prints the
   verbatim source and forces a citation-first answer where the number must appear *in the
   quoted code* (a literal, a comparison, a loop bound). If it isn't there, you haven't
   proven it — quote more lines or say UNVERIFIED. If two lines look like they conflict
   (e.g. `for i=1 to 5` next to `=10`), state what **each** does; never average or guess.
4. **Cite, always.** Quote the exact `mf-prg.bas:<line>` (or byte range) in your answer,
   then explain the interpretation. A Mafia answer without a source citation is incomplete.
5. **State confidence & provenance.** Lines are marked `extracted` (byte-level fact) vs
   `interpreted` (meaning on top) with a confidence. Pass that through — don't upgrade an
   interpretation to a certainty.
6. **If it's not covered, say so.** "The research doesn't document this" is a valid answer.
   Do not fill the gap with plausible-sounding invention.

## How to verify a claim

Run `verify "<claim>"`. It returns the source-backed KG evidence relevant to the claim,
then a **verdict rule**:

- **CONFIRMED** — the claim matches the cited BASIC line / byte evidence.
- **WRONG** — the claim contradicts the cited evidence (say what the source actually says).
- **UNVERIFIED** — nothing in the research addresses it (don't assert it either way).

Never rule CONFIRMED from memory or plausibility — only from a citation the script surfaces.

## What the research covers (so you know what's answerable)

- **All game logic** — 870 BASIC lines documented; combat, economy, wanted, 12 locations,
  win/lose, the ~108 variables. Sections: 100–155 title, 170–350 setup, 1000–1100 main
  loop, 10000–26080 location handlers, 27000–30500 combat, 40000–40166 win/lose.
- **Formulas** — damage, energy, payout, win-condition (each verified against its line).
- **Machine code** — `mf-ml` is 8/8 routines disassembled (combat-AI targeting `cr`,
  map-cursor `i1`/`ie`, block-copy `rc`, loaders, sound). Byte-level, with `$addr` refs.
- **Content** — 30 gangsters (stats), weapon/vehicle/rank DATA tables, all screens & pics.

For deeper structure — the file map, common question→file routing, and the extracted-vs-
interpreted provenance model — see [reference.md](reference.md).

## When to use this skill

- Any question about how Mafia works (a mechanic, a formula, an outcome, an edge case).
- Before **porting or implementing** any game behavior in the engine — confirm the
  original's rule first (the engine ports the research; it never improvises).
- To **fact-check** a statement about the game ("does jail cost X?", "is damage capped?").
- To find the **exact BASIC line** a Python handler should be a port of.

## Anti-patterns

- ❌ Answering a Mafia mechanic from memory or "how such games usually work."
- ❌ Citing the research's own YAML as the source of truth for a *behavior* — the ground
  truth is `mf-prg.bas:<line>` / the bytes; the YAML interprets those.
- ❌ Upgrading an `interpreted`/`confidence: medium` note to a stated fact.
- ❌ Guessing to fill a gap instead of saying "not in the research."

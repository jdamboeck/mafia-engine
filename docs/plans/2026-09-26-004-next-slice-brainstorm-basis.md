---
title: "Next Slice — Brainstorm Basis (pointer)"
type: brainstorm-input
date: 2026-09-26
topic: next-slice
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
status: input-for-brainstorm
---

# Next Slice — Brainstorm Basis (pointer)

> **This is a brainstorm INPUT, not a plan.** It exists so the newest file in `docs/plans/` is a brainstorm-basis after the engine quality gates plan (`2026-09-26-003-refactor-engine-quality-gates-plan.md`) landed. The main content is unchanged: see **`2026-09-25-002-next-slice-brainstorm-basis.md`**, then the additions in **`2026-09-26-002-next-slice-brainstorm-basis.md`**.

## Resolved by the quality gates plan (drop from the earlier lists)

- The `clients/terminal/__main__.py` split: now `session.py` + `cli.py`, and no test monkeypatches client internals.
- Theme selection: `--theme NAME|PATH` exists and loads after argument parsing.

## Still open from the earlier lists

- The **classic** theme is still loaded before argparse (the help text comes from it), so a broken classic theme breaks `--help`; a literal `%` in a help string would break argparse; a strings YAML whose top level is a list gives a traceback.
- `map_repl()` in `clients/terminal/__init__.py` has no production caller.
- The fight lab keeps its own English text.

## Added by the quality gates plan

- **#98: numbers print Python-style, not C64 `PRINT`/`str$` style.** `22.0` where the source prints `22`; the rank screen's `mid$(str$(gf(sp)),2)` (`:4215`) strips a negative score's minus sign, reachable now that the weapon buy moves `gf` unclamped.
- **Ports not yet made:** `:4050` `pl(sp)=pl(sp)+(pl(sp)>0)` (bribe-protection aging) and `:4055-4056`; jail (`:1013`'s `ifgs(sp)thengosub1500`, `:1500-1515`) has no session skip yet. (`:1013`'s truncation and the `:4045-4652` rent countdown were ported in the review follow-up, #99/#101.)
- **#105: recruit cap order.** `:12145` checks the 10-gangster cap after the offer; the engine checks it before the next one, so display and draw order differ when the gang fills mid-batch.
- **Recordings made before `2621ccc` no longer replay** past the first shot whose draw changed (the `:30247`/`:30255` fixes). The replay detector reports this correctly; re-record any fixture that matters.
- **Process:** `docs/AGENTS.md` now requires a fresh-context check of doc game claims and orchestrator verification of subagent reports; `make check` needs pyright, so the local gate runs through uv on an externally managed Python.

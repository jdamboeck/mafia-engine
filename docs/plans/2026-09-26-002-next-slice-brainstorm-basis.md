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

> **This is a brainstorm INPUT, not a plan.** It exists so the newest file in `docs/plans/` is a brainstorm-basis after the slice polish plan (`2026-09-26-001-refactor-slice-polish-plan.md`) landed. The content is unchanged: see **`2026-09-25-002-next-slice-brainstorm-basis.md`**.

## Added by the polish pass

- **`clients/terminal/__main__.py` is 1,040 lines.** `TerminalSession` sits next to the module-level screen helpers and the CLI. Splitting it into `session.py` and `cli.py` means moving the tests' monkeypatch targets (`advance_turn`, `run_upkeep`, `render_map`, `save_game`, `_run_upkeep_screen`, `TerminalInput`).
- **Comment volume is unchanged (46% → 45%).** The polish removed the plan IDs but mostly replaced them with the decision in plain words. Shortening docstrings further is a separate job.
- **Theme-load failures before argparse.** `main()` builds the theme before parsing arguments, so a broken theme also breaks `--help`. A theme help string containing a literal `%` would break argparse. A strings YAML whose top level is a list gives a traceback instead of one line.
- **`map_repl()`** in `clients/terminal/__init__.py` has no production caller.
- **The fight lab** keeps its own English text; only the play client is themed.

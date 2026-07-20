# Agent working conventions

This file orients a fresh agent session working on the Mafia engine. Read it
first. It captures **how** the work runs; **what** to build lives in the active
plan (see `CLAUDE.md` § Current state — currently
`docs/plans/2026-07-20-003-refactor-combat-engine-foundation-plan.md`) and the
authoritative design in `docs/design/`.

## Environment setup (get to a green tree first)

```bash
pip install -e '.[dev]'   # pytest (+ ruff for lint); pyyaml is a runtime dep
make check                # → pytest + soft lint; must be green before any work
```

`make check` runs `pytest` plus lint (`ruff check` **and** `ruff format --check`).
The lint is soft only on ruff's *absence* — a machine without dev extras skips it
and stays green. When ruff is installed both checks are **hard**, so formatting
drift fails the gate rather than accumulating silently. The research knowledge base is
a sibling checkout at `../research/` — handlers port from the BASIC line blocks
it cites; confirm it is present before starting a handler unit.

## Execution model — one orchestrator, one subagent at a time

A single **orchestrator** agent holds the architecture in context and owns the
whole run. For each implementation unit it dispatches **exactly one** subagent,
integrates the result, then dispatches the next. Never more than one subagent in
flight.

- The orchestrator keeps the cross-cutting contracts in its own context (the
  interaction protocol, the effect buffer / cancellation semantics, the
  handler-API boundary, the event schema — KTD-2/3/6/7/8 in the plan) and does
  **not** delegate those decisions.
- A subagent gets a **bounded packet** (the target unit's plan section + the
  KTDs it cites + the named research references) — never "read the whole plan."
- Subagents implement and run their **own** unit tests as a self-check. They do
  **not** `git add`, commit, or run the full suite. The orchestrator owns all
  commits and the authoritative `make check`.

## Picking up the next unit

The next unit is the **earliest unit in the serial order whose dependency
issues are all closed**.

Serial order (from the plan):

```
U0 → U3 → U2 → U1 → U4 → U5 → U6 → U8 → U7 → U9 → U11 → U12 → U10
```

To see the board: `gh issue list` — each open issue is an unfinished unit, with
`dep:U<N>` labels showing what must close first. (If this repo has no GitHub
remote, the same board lives in `docs/PROGRESS.md`.)

## The green-tree rule

`make check` (→ `pytest` + soft lint) is the gate. **Never dispatch a subagent
on a red tree, and never commit on a red tree.** After a unit's subagent
returns: review the diff against the unit's `Files:` and scope → run
`make check` → fix on green → commit → close the unit's issue. Only then pick
the next unit.

## Commit convention

- Conventional commits: `feat(scope): …`, `fix(scope): …`, `docs(scope): …`,
  `test(scope): …`, `chore(scope): …`. Derive the message from the unit's Goal.
- One logical unit per commit. The orchestrator (not subagents) commits.
- Incremental unit commits use clean messages with no attribution footer; a
  final PR-level commit carries the attribution footer.

## Branch / worktree convention

- Default: work on the feature branch **`feat/vertical-slice`** off `main`. The
  orchestrator commits each unit here.
- Units run **serially** (one subagent at a time), so a single feature branch is
  the baseline. Per-unit git worktrees are the escalation only if units are ever
  parallelized — not needed for the current serial workflow.
- `main` stays clean; the slice merges back when the Definition of Done holds.

## Sources of truth (do not confuse them)

- `docs/design/` — engine design (architecture, phasing). Genre engine, not a clone.
- `../research/` — authoritative game *knowledge* (the reverse-engineered 1986
  game). **Never invent game behavior**; port from the cited BASIC line blocks.
- Before porting any formula, gate the claim through the `mafia-oracle` skill.

## Test discipline

- Feature-bearing units are proof-first where practical: write/observe the
  failing test before implementing. Scaffolding-only units (U0, U3 stubs, U10
  IO) use their stated `Test expectation` annotations instead.
- Behavioral-fidelity bar: assert against research-documented formulas and value
  ranges, **not** a byte-for-byte C64 RNG trace.

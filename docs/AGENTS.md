# Agent working conventions

This file orients a fresh agent session working on the Mafia engine. Read it
first. It captures **how** the work runs; **what** to build lives in the active
plan (derive it from the tree per `CLAUDE.md` § Current state, point 1 — never
hardcode a plan path) and the authoritative design in `docs/design/`.

## Environment setup (get to a green tree first)

```bash
pip install -e '.[dev]'   # pytest (+ ruff for lint); pyyaml is a runtime dep
make check                # → pytest + soft lint; must be green before any work
```

`make check` runs `pytest` plus lint (`ruff check` **and** `ruff format --check`);
both are hard when ruff is installed and skipped only if it is absent. The research
knowledge base is a sibling checkout at `../research/` — handlers port from the
BASIC line blocks it cites; confirm it is present before starting a handler unit.

## Execution model — one orchestrator, one subagent at a time

A single **orchestrator** agent holds the architecture in context and owns the
whole run. For each implementation unit it dispatches **exactly one** subagent,
integrates the result, then dispatches the next. Never more than one subagent in
flight.

- The orchestrator keeps the cross-cutting contracts in its own context (the
  interaction protocol, the effect buffer / cancellation semantics, the
  handler-API boundary, the event schema — the active plan tags these as KTDs)
  and does **not** delegate those decisions.
- A subagent gets a **bounded packet** (the target unit's plan section + the
  KTDs it cites + the named research references) — never "read the whole plan."
- Subagents implement and run their **own** unit tests as a self-check. They do
  **not** `git add`, commit, or run the full suite. The orchestrator owns all
  commits and the authoritative `make check`.

## Picking up the next unit

The next unit is the **earliest unit in the active plan's serial order whose
dependency issues are all closed**. The serial order and unit list are the
*active plan's* — read them from it, not from here (a baked-in order goes stale
the moment a plan closes).

To see the board: `gh issue list` — each open issue is an unfinished unit, with
`dep:U<N>` labels showing what must close first. (If this repo has no GitHub
remote, the same board lives in `docs/PROGRESS.md`.)

## Closing out a plan (do this when the final unit lands)

When a plan's last unit lands green, the plan is done — and **stale plan pointers
are how a finished plan keeps looking active to the next session** (it has already
misled two). So completing a plan is not just closing the last issue:

1. **`CLAUDE.md` must carry no plan-specific pointer.** It derives the active plan
   from the tree (its "Current state" point 1); it must never name *this* plan as
   active. Move the finished plan into its append-only *"Landed, do not re-open"*
   ledger (point 2) and confirm nothing else references it as current.
2. **Drop a `*-brainstorm-basis` doc** in `docs/plans/` for what comes next. That
   doc — not `CLAUDE.md` — is the forward pointer; its presence is the signal that
   "the previous plan is done, the next isn't planned yet."
3. **Delete consumed scratch companions** (`*-NEXT-STEPS`, stale `*-amendments`)
   once their units are done, so the plans dir stays unambiguous.

The rule in one line: **no doc may hold a fact that a plan completion falsifies —
state it as a derivation, or move it to the closed-work ledger.**

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

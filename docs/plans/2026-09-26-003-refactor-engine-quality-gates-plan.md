---
title: Engine Quality Gates - Plan
type: refactor
date: 2026-09-26
topic: engine-quality-gates
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
branch: chore/polish
---

# Engine Quality Gates - Plan

## Goal Capsule

- **Objective:** Make four kinds of silent drift fail loudly in the engine: wrong types, ports that disagree with their BASIC line, unverified claims in learning docs, and tests that dictate the client's structure.
- **Product authority:** This contract for scope; `mf-prg.bas` (via `mafia-oracle`) for every game value a test or doc asserts; `docs/AGENTS.md` for process rules.
- **Open blockers:** None. Lands on `chore/polish` in the same PR as the slice polish plan.

---

## Product Contract

### Summary

Every package is type-checked in the gate.
Every ported BASIC formula is tested against its own quoted line, evaluated the C64 way.
Learning docs quote the source for their claims, their load-bearing claims are tests, and a fresh-context check runs before a doc lands.
Client tests verify only what a player or caller can observe, and the client splits into a session module and a CLI module.

### Problem Frame

Four problems surfaced while finishing the slice.

The code is heavily annotated but nothing checks the annotations: pyright 1.1.412 reports 10 errors in `engine/`, 16 in `clients/`, 0 in `data/` and 163 in `tests/`, while `make check` runs only pytest and ruff.

Nothing ties an engine port to the BASIC line it ports. The #47 audit found inverted signs by hand, and the research repo carried 16 wrong readings of the same kind for two months.

Learning docs read as settled but were wrong in two ways: misreading the source ("7 = handgranaten"; it is 8) and reasoning wrongly from a correct quote (":30240's loop never runs" under true=+1; a C64 `FOR` loop still runs once).

Tests shape the client: 17 monkeypatches of module-level names in two test files pin `TerminalSession` inside the 1,040-line `clients/terminal/__main__.py`, and five client functions keep an optional `resolver=None` mainly for test fakes.

### Key Decisions

- **Tests verify; they do not shape.** Tests may use seams the design has for its own reasons (standard input and output, the input source, the RNG, the resolver, public entry points). They must not reach into internals such as module globals, private functions or call counts. This is the rule that decides item 4 and applies to new tests from now on.
- **Fix all type errors, including tests.** A clean slate and a gate with no exceptions, accepting test-fixture churn over a baseline file that tolerates known-wrong types.
- **Evidence is the quoted line, evaluated with C64 semantics.** A test evaluates the verbatim BASIC expression with true = -1 and bitwise `AND`/`OR` and compares it with the port. Real-C64 (VICE) fixtures are out of scope.
- **Citation checks run where the source is.** The check that quoted fragments match `mf-prg.bas` runs locally when `../research/` exists and is skipped in CI, which cannot see that repo.
- **Docs are checked three ways:** quoted citations (mechanical), load-bearing claims as tests (mechanical), and a fresh-context verifier before a doc lands (process). A one-line rule adds that factual claims from subagent reports are verified before they land in code or docs.
- **One PR.** This work lands on `chore/polish` with the slice polish plan.

### Requirements

**Type checking**

- R1. `make check` and CI run pyright over `engine/`, `clients/`, `data/` and `tests/`, and fail on any error.
- R2. All existing pyright errors are fixed, not suppressed. An inline ignore is allowed only with a one-line reason where the checker is demonstrably wrong.
- R3. The pyright version used by the gate is pinned, like ruff, so local runs and CI agree.

**Ports tied to their BASIC lines**

- R4. A test-side evaluator computes BASIC arithmetic the C64 way: `+ - * /`, `int()`, comparisons that yield -1 or 0, bitwise `and`/`or`, and variables.
- R5. Every engine function that ports a BASIC formula has a test that holds the verbatim expression with its `mf-prg.bas:NNNN` citation, evaluates it for the port's inputs, and compares the results with the port.
- R6. A local-only test checks that every quoted BASIC fragment cited next to `mf-prg.bas:NNNN` in engine code, tests and docs matches that line of the source. It is skipped, with a visible reason, when `../research/` is absent.

**Verified docs**

- R7. A claim about game behavior in `docs/solutions/` cites `mf-prg.bas:NNNN` with the verbatim fragment it rests on; R6 checks those fragments.
- R8. Each existing learning doc's load-bearing game claims become tests in the R5 suite, and the doc links to them.
- R9. `docs/AGENTS.md` requires a fresh-context verification of every game claim before a new or changed learning doc is committed.
- R10. `docs/AGENTS.md` requires the orchestrator to verify factual claims from a subagent's report against the tree or the source before they land in code or docs.

**Client tests and structure**

- R11. No test monkeypatches a module-level name or private function of `clients/terminal/`; tests drive `play()`, `main()` or the session's public methods through standard input and output, and assert on printed output, returned state and files written.
- R12. Call-count assertions are replaced by observable equivalents (for example, the number of turn-over screens and the final clock instead of `advance_turn` calls).
- R13. Client functions that render text take a resolver as a required argument; tests pass the real classic theme or a complete theme.
- R14. After R11-R13, `clients/terminal/__main__.py` becomes a thin entry point, with the session in its own module and the command-line handling in another, and no test changes.

### Acceptance Examples

- AE1. **Covers R5.** Given `x=3+3*(jo(sp)=2)` from `:25560`, the evaluator yields 0 for `jo=2` and 3 for `jo=1,3,4`, and the test fails if `_completion_score` returns 6 for the croupier.
- AE2. **Covers R6.** Given a comment quoting `deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)` next to `mf-prg.bas:116`, the citation check fails because the line is 115.
- AE3. **Covers R11, R12.** The test that a loaded game skips upkeep asserts that no upkeep banner is printed and the returned state equals the saved state, without replacing `run_upkeep`.
- AE4. **Covers R1, R2.** After the work, `make check` fails if a function annotated to take a `GameState` is called with an optional value that may be `None`.

### Scope Boundaries

**Deferred**

- Real-C64 (VICE) fixtures for ported formulas.
- Any change in the research repo; those items are parked in `../research/todos/004-005`.

**Not planned**

- Gameplay changes and next-slice work.
- Typing strictness beyond pyright's default ("basic") mode.

### Dependencies / Assumptions

- The client test rewrite (R11-R13) happens before the type fixes in those same test files, so they are fixed once.
- The evaluator (R4) exists before load-bearing doc claims become tests (R8).
- Assumed: every observable client behavior the current spies check can be asserted through output, returned state or files. If one cannot, it is surfaced during planning rather than solved with a new injection seam.

### Outstanding Questions

**Deferred to Planning**

- Which engine functions count as "ports of a BASIC formula" for R5; planning inventories them from `mf-prg.bas:` citations.
- How the citation check (R6) recognizes a quoted fragment in prose and comments.
- Whether pyright runs through `python -m` from the dev extra or a pinned binary.

### Sources / Research

- Measured this session: pyright 1.1.412 error counts by package; 17 client monkeypatches across `tests/test_client_loop.py` and `tests/test_terminal_client.py`; five optional `resolver` parameters in `clients/terminal/`.
- The relational rule and its structural proofs: `docs/solutions/architecture-patterns/basic-relational-boolean-is-minus-one-when-porting.md`.
- Vacuous-test history and the break-to-prove discipline: `docs/solutions/developer-experience/tests-that-cannot-fail.md`.
- Research-side counterparts: `../research/todos/004-pending-p3-validate-pass-rewrites-completion-timestamp.md`, `../research/todos/005-pending-p2-interpreted-meanings-have-no-independent-check.md`.

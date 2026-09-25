---
title: "Tests that cannot fail: three vacuous-assertion shapes and how to catch them"
date: 2026-07-20
category: developer-experience
module: tests
problem_type: developer_experience
component: testing_framework
severity: high
applies_when:
  - "Writing an assertion that is a disjunction where one clause could be satisfied by ambient output (a status bar, a map legend, a boilerplate header)"
  - "Writing an assertion whose truth is guaranteed by the callee's return type or contract"
  - "Building a scripted RNG queue, a scripted stdin script, or any exhaustible test fixture"
  - "Auditing an existing suite for coverage that only looks real"
  - "A test's name promises a code path (a win, a denial message, entering a location) the body may not actually reach"
symptoms:
  - "A test passes both before and after deleting or inverting the feature it claims to cover"
  - "A disjunctive assertion where one clause matches boilerplate present on nearly every screen"
  - "An assertion the function signature already guarantees, such as an isinstance check on a helper that always returns that type"
  - "A scripted RNG or input fixture with zero queued values, or a script that ends before the branch under test is entered"
  - "A test asserting 'X survives unchanged' where X was never touched because the operation never ran"
tags:
  - testing
  - vacuous-assertions
  - test-validity
  - mutation-testing
  - regression-safety
  - rng
  - test-review
related_components:
  - combat
  - client
  - rng
---

# Tests that cannot fail

## Context

A five-group cleanup pass over this repo's test suite went looking for dead
weight and found something worse: six tests that were **structurally incapable
of failing**, regardless of whether the code they claimed to cover was correct.
All six passed. None were guarding anything.

Four of the six were found only by deliberately breaking the feature and
watching the test not react. Reading the assertion was not enough — in every
case it read plausibly, which is exactly how all six survived review the first
time.

An earlier, seventh instance is already documented from the other direction:
`freezing-a-mutable-dataclass-graph.md` §3 describes the purity harness going
blind when its snapshot became identity-based, reducing one assertion to
`state == state` and another to comparing `commit`'s output with itself — "It
can never fail, for any handler." That was a harness-level instance of the same
shape, found before this pass.

## Guidance

### The three shapes

**1. Trivially-true disjunct.** `assert A or B`, where `B` is satisfied by
*ambient* output the code under test does not own — a status bar, a legend, a
header rendered on every screen.

```python
# Before — the "$" disjunct is satisfied by the status bar's `cash {N}$`
# on nearly every screen, whether or not the gamble ever resolved:
assert "won" in output.lower() or "lost" in output.lower() or "$" in output

# After (tests/test_client_loop.py:205-223) — resolved outcome text plus
# the exact cash values:
assert "gewonnen" in low, "sph's win narration never reached the client"
assert "verloren" not in low, "seed 42 wins; a loss string means the seed drifted"
assert "cash 5500$" in low
assert "cash 5550$" in low
```

The same shape appeared in the unimplemented-door test, which asserted
`"closed for renovations" in output or "sgl" in output.lower()`. The map legend
renders the location key `sgl` on every map screen. Replacing the entire denial
message with unrelated text kept the test green. Fixed at
`tests/test_client_loop.py:709` to assert the denial text alone.

**The tell:** one arm of the `or` references something the handler under test
does not produce.

**2. Structurally guaranteed assertion.** A type check or invariant the callee's
own contract already promises, so no code path can violate it.

```python
# Before — cannot fail; run_play always returns StringIO().getvalue():
output = run_play(monkeypatch, seed=42, stdin_keys=keys)
assert isinstance(output, str)

# After (tests/test_client_loop.py:655-666) — asserts what the EOF path
# actually determines:
assert "bye." in low, "the client did not exit cleanly on EOF"
assert "gewonnen" not in low and "verloren" not in low, (
    "EOF must abandon the gamble, not resolve it"
)
```

This one was worse than inert — it **masked real coverage drift**. The walk
script never reaches the location in the test's own name; the run ends on the
map screen, not mid-handler at the wager prompt. The dead assertion hid that for
as long as it existed. Tracked as #51 rather than fixed by relabeling.

**3. Fixture that no-ops before the assertion matters.** The scripted input or
RNG runs dry, or the setup never reaches the branch, so the asserted-on code
never executes — and the assertion happens to hold on the unreached state too.

```python
# Before — zero queued values. The first rng draw inside the fight raises
# StopIteration and unwinds the combat generator before any fight logic runs:
result = run_upkeep(st, rng=_StubRng())

# Before — a bare string is a directionless shot. _parse_combat_response
# (engine/interactions.py:733-749) maps any str to (raw, None), so the shot
# never connects:
src = _scripted(*([3] + ["shoot"] * 120))

# After — the shot aims, so the win branch actually executes:
src = _scripted(*([3] + [("shoot", STEP_RIGHT)] * 120))
```

For the zero-value RNG pair, the run emits only `upkeep.turn_banner` and
`upkeep.debt_collectors_intro` — no `combat.winner_banner` at all. The
assertions ("cash, debt, and the counter survive untouched") pass because
nothing ran. A test named `test_win_changes_nothing_and_the_fight_recurs_next_turn`
never reaches a win. Issue #49.

### The detection technique

**Break the feature, confirm the test fails.** Four of the six were found this
way and by no other means.

1. Pick the production line the test's name or docstring claims to cover.
2. Delete it, invert it, or otherwise make it obviously wrong.
3. Run the test.
4. If it still passes, the test was never exercising that code — it is vacuous,
   not passing.
5. Revert the break and fix the *test*, not the production code you broke to
   probe it.

**Safety note — never `git checkout` to restore.** A bare
`git checkout -- <file>` silently discards *all* uncommitted changes in that
file, including work unrelated to your probe. During this pass, a subagent's
uncommitted refactor was destroyed exactly this way. Commit first, then probe;
restore with `git stash pop` or a scratch copy.

### The strict-fixture corollary

`tests/helpers.py`'s shared `StubRng` raises `AssertionError` on exhaustion
rather than letting `StopIteration` propagate silently. That strictness is what
exposed the zero-value RNG pair: `tests/test_debt_default.py` keeps a local,
permissive `_StubRng` (docstring at `tests/test_debt_default.py:56-84`)
precisely because swapping in the strict shared stub turns both tests red.

The permissive copy was *preserving* the vacuous pass, not fixing it. When a
stricter fixture turns a test red, the fix is to make the test drive the code
path — not to restore the permissive fixture that was hiding the gap. **A
fixture that never fails is exactly as suspect as a test that never fails.**

## Why This Matters

A vacuous test is worse than no test: it occupies the slot a real test would
fill and reports green while doing it.

- The directionless-shot test left the win branch of a job-shift fight
  unguarded; a flipped comparison there could ship silently.
- The zero-RNG pair left the debt-collector fight's win outcome entirely
  unobserved by the two tests whose names promise otherwise.
- The gamble test meant deleting **both** the win and loss narration shipped
  with the full suite green — verified, not hypothesized.
- The denial test meant the only message a player sees when walking into an
  unbuilt location could be replaced with anything.

The consequence is not theoretical. Issue #50 — the debt-collectors fight
omitting its losses block — is a real defect that the #49 vacuous pair was
nominally protecting against. The tests never let a fight resolve, so the
missing block was never observed.

The unifying risk: **every one of these tests would have kept passing if the
feature it named had been deleted outright.** That is the operational
definition worth carrying forward — not "does the assertion look reasonable"
but "does deleting the feature turn this red."

## When to Apply

- **Any test with an `or` in the assertion.** Ask what satisfies the disjunct
  that isn't the branch under test.
- **Any test whose only assertion is a type check or an invariant the callee
  already guarantees.** That is a smoke test for "did it crash" — fine to have,
  but label it as that, not as coverage of the behavior in the test's name.
- **Any exhaustible fixture.** Check the script has *enough* values to reach the
  branch, and prefer a stub that raises loudly on exhaustion over one that lets
  `StopIteration` or a default swallow the gap.
- **Reviewing a PR that adds or changes a test.** Ask "what code path does this
  reach," not "does the assertion read plausibly."
- **Before landing a fix for a bug a test claims to already cover.** If the
  covering test doesn't go red when you stash the fix, it wasn't covering it.

This applies with most force around generator-driven handlers and scripted RNG.
Every handler test is a scripted conversation with a generator, and a script
that runs dry, aims nowhere, or never reaches the prompt it claims to answer is
the most common way these tests go vacuous.

## Examples

```bash
# 1. Commit first — never probe on top of uncommitted work.
git stash push -u

# 2. Break the feature the test claims to cover:
#    delete both ShowMessage calls for sph's win/loss narration.

# 3. Run just that test.
pytest tests/test_client_loop.py -k test_gamble_completes_and_pays_out -q
# PASSED  <- vacuous: the "$" disjunct never needed the narration at all

# 4. Restore.
git stash pop
```

| Shape | Tell |
|---|---|
| Trivially-true disjunct | One arm of `A or B` is satisfied by output the code under test doesn't own |
| Structurally guaranteed assertion | The assertion restates a guarantee already in the callee's contract |
| Fixture no-ops before the assertion matters | The scripted input/RNG runs dry, or aims nowhere, before the branch executes |

**Issue tracking:** #47 (inverted relational-sign convention — six tests
asserted the bug, one that paid training makes a gangster *less* brutal),
#48 (turn-over screen content had no coverage beyond a dataclass-repr guard),
#49 (the zero-value `_StubRng` pair), #50 (the defect #49's tests were
nominally guarding), #51 (the EOF test's name/coverage mismatch).

## See also

- `docs/solutions/architecture-patterns/freezing-a-mutable-dataclass-graph.md`
  §3 — a prior, harness-level instance of shape 2: the purity harness's
  identity-based snapshot reduced its assertions to `state == state`, which "can
  never fail, for any handler." The fix there (snapshot by value, not identity)
  is a specific case of "inject a real difference and confirm the check reacts."
- `docs/solutions/architecture-patterns/basic-relational-boolean-is-plus-one-when-porting.md`
  — the #47 sign-convention reversal. Six tests had encoded the inverted
  convention and therefore could not have caught it. That doc's caution ("a
  derived value is not evidence for the rule that derived it") is the same
  caution as trusting a passing test as evidence the code is correct.
- `docs/solutions/workflow-issues/a-missing-artifact-is-not-a-missing-review.md`
  — the same meta-lesson from the other direction: don't under-trust a signal
  without reading what produced it, just as this doc says don't over-trust one.

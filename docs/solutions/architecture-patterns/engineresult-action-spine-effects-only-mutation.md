---
title: "EngineResult action spine: effects-only mutation, audit-only events, adopt-result.state"
date: 2026-07-14
category: architecture-patterns
module: engine
problem_type: architecture_pattern
component: service_object
severity: high
applies_when:
  - "Adding or modifying any engine action (try_move, run, run_option) or anything that composes them"
  - "Writing or reviewing handlers that mutate state or record RNG draws"
  - "Implementing replay, save/load, or audit/UI consumers of engine output"
  - "Tempted to merge SetPosition/Teleport, emit LocationActionRejected, or use EngineResult.error for bugs"
  - "Deciding whether a new mutation may bypass Effects + commit()"
tags:
  - engine-result
  - effects
  - semantic-events
  - commit
  - replay
  - handler-atomicity
  - purity
  - compose-contract
related_components:
  - testing_framework
---

# EngineResult action spine: effects-only mutation, audit-only events, adopt-result.state

## Context

The State/Event Foundation refactor (plan `docs/plans/current-action-plan.md`, tasks
T1–T9, landed on `feat/vertical-slice` as of 2026-07-14, unmerged to `main`) replaced two
divergent action-result shapes with one spine. Before it, `try_move` mutated state **in
place** while the interaction driver was pure and returned a `DriverResult` — two
opposite contracts side by side, and a caller that forgot to adopt the driver's returned
state silently dropped effects. The refactor unified both on a single result type and
made every mutation flow through one committed effects stream. This doc captures the
pattern and — just as important — its deliberate quirks, so a future contributor does not
"clean them up" into bugs.

## Guidance

**1. Every engine action returns a frozen `EngineResult` — and callers must adopt its
state.** `EngineResult(state, events, effects, status, payload, error)` is defined at
`engine/actions.py:49`; `status` is the `EngineStatus` literal. The compose contract:

```python
result = try_move(state, city, RIGHT)
state = result.state          # ALWAYS adopt — the input object is never mutated
if result.payload.turn_over:  # payload carries the action-specific MoveResult
    ...
```

Never keep using the pre-call `state` after a mutating action — the move/rent/purchase
lives only in `result.state`.

**2. Effects are the only mutations; `commit()` is the only applier.** Effect dataclasses
live in `engine/effects.py`; `commit(state, effects)` (`engine/effects.py:393`)
folds every effect through the private `_apply` and returns `CommitResult(state, effects)`
with the committed effects in order — the replay record. Single-effect `apply()` stays pure
the same way. No engine code assigns to game state outside `_apply`.

*Updated 2026-07-18 (immutable-state-graph refactor):* `commit()` no longer deep-copies.
The state graph is now frozen (`@dataclass(frozen=True)` throughout `engine/state`, with
read-only `tuple`/`MappingProxyType` collections), so purity is **structural** — each
effect functionally rebuilds a new state and there is no shared-mutable object left to
defend against. A direct write now raises at the offending line rather than silently
corrupting the next save.

**3. Semantic events are audit/UI records — never applied, never replayed.** The catalog
in `engine/events.py` (e.g. `MoveBlocked` at `engine/events.py:84`, `OptionDenied` at
`engine/events.py:104`) imports no state machinery. **Replay vocabulary = effects +
logged RNG draws**; events may exist with zero effects (a blocked move mutates nothing
but still emits `MoveBlocked`). The future save/load unit (U12, issue #12) builds on
this split — do not let events leak into the replay log.

**4. Handlers buffer; the driver commits or discards atomically.** Handlers call
`ctx.apply(effect)` (`engine/interactions.py:191`) and `ctx.record(event)`
(`engine/interactions.py:199`) — never mutate `ctx.state` directly. On clean completion
the driver commits the effect buffer and surfaces the events; on cancel it discards
**both** buffers and returns the original state object with `status="cancelled"`.

```python
def rent(ctx):                          # a handler
    ...
    ctx.apply(MoneyChange(-price))      # buffered — applied only on clean completion
    ctx.state.players[0].ka -= price    # FORBIDDEN — run_pure will fail the test
```

**5. `run_option()` is the single location-option dispatcher**
(`engine/actions.py:88`): unknown option id raises `ValueError`; guard denial returns
`status="blocked"` with an `OptionDenied` event, a `DeniedResult` payload
(`engine/actions.py:74` — guard debug data lives here, never on the event), zero effects,
and the unchanged input state; consequence options convert strictly via
`effects_from_dicts` (`engine/consequences.py:114`, delegating per-item to
`effect_from_dict` at `engine/consequences.py:69`; `ValueError` on unknown type /
missing / extra fields) and commit; handler options delegate to `engine.interactions.run()` with the generic
lifecycle events appended by `run_option`, never by bare `run()`.

**6. Purity is enforced structurally, not by convention.** Route handler tests through
`run_pure` (`tests/helpers.py:35`): it snapshots the input, runs the handler,
independently replays `result.effects` onto the snapshot via `commit()`, and asserts the
input was never mutated **and** the returned state is fully explained by the effects. A
handler that mutates directly fails the suite (proven by a rigged-handler test in
`tests/test_driver.py`).

## Why This Matters

- **Replay integrity:** the committed effects stream doubles as the save/replay log
  (`initial seed + setup + ordered effects = game`). One hidden in-place mutation makes
  replay diverge from live play.
- **Atomic cancel:** a player backing out of a multi-prompt interaction must leave zero
  trace; buffer-then-commit makes that hold by construction.
- **Protocol surface:** the interaction protocol is the future network/agent protocol —
  frozen pure-data results with machine-key events are what a remote driver consumes.
- **The old contract bites silently:** code written against the pre-refactor in-place
  `try_move` compiles and runs but drops every move unless it adopts `result.state`.

## When to Apply

- Adding any engine action, effect, or event, or composing existing ones.
- Writing or reviewing a handler (bug bar: any direct state write).
- Building replay/save-load (U12) or any audit/UI consumer of events.
- Reviewing "cleanup" PRs that touch the deliberate quirks below.

## Examples

Deliberate quirks that look like mistakes — **do not "fix" these** (each has a docstring
saying so at the cited line):

| Looks wrong | Actually deliberate |
|---|---|
| `SetPosition` (`engine/effects.py:110`) and `Teleport` (`engine/effects.py:142`) are byte-identical | Two names so the effects stream records *why* the player moved (ordinary movement vs forced relocation). Do not merge. |
| `LocationActionRejected` (`engine/events.py:126`) is defined and tested but emitted nowhere | Handler rejection paths (e.g. slw.rent insufficient cash) return `status="completed"` with no machine-readable marker — the do-not-guess rule; the wiring point is documented in `run_option`'s else branch. |
| `advance_turn` (`engine/movement.py`) is pure and returns `(state, game_over)` | **Changed 2026-07-18** (immutable-state-graph refactor). It was previously the one in-place mutator outside the effect funnel — a known asymmetry — and the frozen graph forced it onto the functional path. Callers MUST adopt the returned state; the terminal turn loop was discarding it. |
| Bugs raise instead of returning `EngineResult(error=...)` | `status="error"`/`error` are reserved for *future recoverable runtime errors*; config/programmer bugs must raise (`engine/actions.py` module docstring). |
| Clean completion with an empty effect buffer returns the state unchanged | **Changed 2026-07-18.** Pre-freeze `commit()` always deep-copied, so an empty commit returned a distinct object; that distinctness was an artifact of the copy, not a guarantee. With a frozen graph the caller cannot mutate what it gets back, so an empty commit returns the input itself. Assert on VALUES, not identity. Driver-cancel still returns the original object by contract. |

## Related

- `docs/plans/current-action-plan.md` — the plan this landed from (T1–T9, issues #17–#25, closed).
- `docs/design/engine-architecture.md` § events-vs-effects — predates this naming split
  ("effects are committed as events"); read that as the *effects* stream. A wording
  alignment pass is owed post-plan.
- Issue #12 (U12 save/load, deferred) — builds directly on the replay-vocabulary rule here.

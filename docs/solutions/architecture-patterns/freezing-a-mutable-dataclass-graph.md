---
title: "Freezing a mutable dataclass graph: the false floor, mappingproxy, and the harness that goes blind"
date: 2026-07-18
category: architecture-patterns
module: engine
problem_type: architecture_pattern
component: state
severity: high
applies_when:
  - "Making a dataclass graph immutable, or adding a dataclass to engine/state"
  - "Serializing state that holds read-only collections (save/load, replay, snapshots)"
  - "Changing or 'simplifying' tests/helpers.py::run_pure or any purity/snapshot harness"
---

# Freezing a mutable dataclass graph

`@dataclass(frozen=True)` across `engine/state` made the replay guarantee
language-enforced instead of docstring-enforced: a handler writing
`ctx.state.players[sp].ka += 100` now raises at that line instead of silently
producing a different result on load. Three things about that migration were not
obvious, and each one can silently undo the guarantee.

## 1. `frozen=True` leaves a false floor

Freezing stops attribute *writes*. It says nothing about what a field *holds*. A
frozen dataclass with a `dict` field still hands out a fully mutable dict:

```python
@dataclass(frozen=True)
class Config:
    formula_params: dict            # frozen field, MUTABLE value

cfg.formula_params = {}             # raises, as intended
cfg.formula_params["fnm"] = {}      # succeeds — the false floor
```

Closing it requires the fields to *hold* read-only types — `MappingProxyType` for
mappings, `tuple` for sequences — and, critically, **coercing at construction**
rather than trusting every call site to pass the right type. `engine/state` does
this with a `__post_init__` calling `_coerce_readonly`, which uses
`object.__setattr__` (legal during construction, on an already-frozen instance).
Without the coercion, immutability depends on every construction site remembering
— setup, effect rebuild, persistence load — and one forgetful caller reopens the
hole with no test failure.

Note `freeze()` is recursive: a shallow wrap leaves `formula_params["fnm"][1] = 0`
writable one level down.

## 2. `MappingProxyType` breaks `deepcopy`, `pickle`, and `dataclasses.asdict`

`mappingproxy` is unpicklable, and `copy.deepcopy` falls back to pickle for
unknown types. `dataclasses.asdict()` deep-copies internally, so **it cannot walk
a graph containing a mappingproxy** — which is why the engine has a hand-rolled
walker, `engine.state.json_safe()`, the public inverse of `freeze()`. Save unwraps
read-only containers to plain dict/list; `engine.persistence.state_from_dict()`
rebuilds the read-only forms. Old saves still load: the JSON shape is identical
either way.

The same constraint bites anywhere else deepcopy was used as a cheap snapshot —
which leads directly to the third gotcha.

## 3. The purity harness can go blind, and stay green

`tests/helpers.py::run_pure` snapshots state before running a handler, then
asserts (a) the input is unchanged and (b) the returned state is fully explained
by the committed effects. It used `copy.deepcopy` for the snapshot. When freezing
broke deepcopy, the tempting fix looked airtight:

```python
snapshot = state    # "the graph is frozen, so nothing can change it"
```

This is wrong twice, and **the entire suite stays green while the harness checks
nothing**:

- Assertion (a) becomes `state == state`.
- Assertion (b) becomes vacuous too — the driver computes `result.state` as
  `commit(state, buffer)`, so replaying `commit(state, result.effects)` compares
  `commit`'s output with itself. It can never fail, for any handler.

And the premise is false: `object.__setattr__` bypasses frozen-ness, which is
exactly the escape this harness is the compensating control for. A handler doing
`object.__setattr__(ctx.state.players[0], "ka", 999999)` passed `run_pure` with
zero assertions firing, across ~30 call sites.

The fix is a snapshot by **value**, not identity — `json_safe(state)` — with the
replay baseline rebuilt from those values so it is genuinely independent of the
object the driver used.

## 4. The same harness went blind a *second* time — types, not values

The value-snapshot fix above was itself incomplete, and the second failure is the
more instructive one. `json_safe` exists to **erase** types (proxy to `dict`,
tuple to `list`) so the graph can become JSON — so a comparison built on its
output cannot see a type change. Three escapes still passed silently:

```python
object.__setattr__(ctx.state.map, "tenancy", {1: 0})   # proxy -> plain dict
object.__setattr__(player, "speed", False)             # int 0 -> bool  (0 == False)
object.__setattr__(player, "ka", 5000.0)               # int -> float   (5000 == 5000.0)
```

The first is the serious one: it swaps a read-only mapping for a mutable one,
**reopening the exact false floor from §1** — and both sides flatten to the same
JSON, so the harness saw nothing. The other two are Python's own coercions hiding
type drift, which is precisely the corruption that surfaces only on load.

Closed by asserting a **type fingerprint** (`_shape()`) alongside the value
comparison: walk the graph recording `type(v).__name__` at every node, snapshot it
before the run, compare after.

**The generalizable lesson, in its strongest form:** when freezing removes the
*need* for a defensive copy, check whether anything relied on that copy for
**independence** rather than for safety — structural immutability does not supply
a separate observation. And when a snapshot is built on a *lossy* projection
(anything that normalizes, serializes, or flattens), it can only ever detect what
that projection preserves; assert on the discarded dimension too, or the harness
is blind to exactly the changes the projection was designed to erase.

Any harness whose assertions can degrade into tautologies needs a **negative
self-test** — one that fails if the harness stops detecting what it claims to
detect. This one needed three, and each was written only after the corresponding
hole was found the hard way:

- `test_run_pure_catches_a_mutation_that_bypasses_frozen` (§3)
- `test_run_pure_catches_a_readonly_collection_downgrade` (§4)
- `test_run_pure_catches_result_state_unexplained_by_effects` (assertion (b);
  its docstring records what it does *not* pin — it proves (b) can fire, but not
  that its baseline is independent)

That the same harness failed twice, in two different ways, both times staying
green, is the point: a verification mechanism gets no credit for passing.

## See also

- [[engineresult-action-spine-effects-only-mutation]] — the effects-only-mutation
  contract this refactor made structural.
- `docs/plans/2026-07-18-001-refactor-immutable-state-graph-plan.md` — R1/R2 (deep
  immutability), R6 (the boundary proof in `tests/test_handler_boundary.py`).

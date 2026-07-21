---
title: "A dataclass split across a layer boundary needs cross-class __eq__ and eq=False"
date: 2026-07-21
category: architecture-patterns
module: engine
problem_type: architecture_pattern
component: state
severity: high
applies_when:
  - "Splitting a state dataclass into an engine blueprint + a config-side subclass"
  - "The engine reconstructs a base instance while live code builds a subclass instance"
  - "Any purity/replay/snapshot check compares result.state to a state_from_dict rebuild"
  - "Moving named fields off an engine dataclass into an opaque attrs map"
---

# The problem

U2/A4 moved the roster member's game stats out of the engine: the engine's
`Combatant` became a blueprint (`name`, `weapon`, `vitality`, `attrs`), and the
config defined `Gangster(Combatant)` adding named stats. The engine's persistence
layer can't import the config type (layer rule), so it **reconstructs a bare
`Combatant`** on load, while live code builds a `Gangster`.

That split creates two non-obvious traps. Both surfaced as ~150 simultaneous
purity-check failures with **identical-looking reprs on both sides of the `==`** —
the most confusing possible symptom.

## Trap 1 — double serialization

If the subclass keeps the stats as **dataclass fields** *and* derives `attrs`
from them, `json_safe` dumps each stat twice (named key + inside `attrs`). The
reconstructed bare `Combatant` has them once (in `attrs`). Live shape != loaded
shape, so every `result.state == commit(state_from_dict(snapshot), effects)`
fails.

**Fix:** single-source the stats. Make the subclass's named stats **properties**
over one storage (`vitality` slot + `attrs`), not dataclass fields — so they are
never serialized and cannot drift. `json_safe(Gangster) == json_safe(Combatant)`
for the same logical member.

## Trap 2 — type-based equality (the killer)

Even with symmetric serialization, `Gangster(...) == Combatant(...)` is `False`
for **identical field values**, because a dataclass-generated `__eq__` begins with
`other.__class__ is self.__class__`. Purity checks compare with `==` (not
`json_safe`), so a live `Gangster` state never equals a reloaded `Combatant`
state. The repr shows two identical objects that refuse to be equal.

**Fix, two parts — both required:**

1. Hand-write `__eq__` on the **base** class to compare by blueprint fields and
   accept any subclass (`isinstance(other, Combatant)`, not `is`).
2. Declare **both** classes `@dataclass(frozen=True, eq=False)` so the decorator
   does not regenerate a per-class `__eq__` that clobbers the inherited one.

Dropping either half silently re-breaks all the purity/replay checks.

## How to recognize it

- Hundreds of `assert a == b` failures where `-vv` shows byte-identical reprs.
- The failures cluster on any harness that does
  `result.state == commit(state_from_dict(snapshot), effects)`
  (see `tests/helpers.py::run_pure`) — i.e. purity, replay, snapshot round-trip.
- `astuple()` on the object raises `cannot pickle 'mappingproxy' object` — a tell
  that the generated dataclass equality path is walking the frozen graph.

## Why not a registry

The alternative — the config registers its type so the engine reconstructs a real
`Gangster` — keeps `a == b` trivially true but re-opens the layer concern (the
engine reaching a config type, even indirectly) and is more machinery. With only a
handful of named reads outside the engine, single-sourcing + cross-class `__eq__`
is the smaller, layer-clean answer. See `docs/plans/2026-07-20-003-amendments.md`
§A4.

Related: [[freezing-a-mutable-dataclass-graph]] (the mappingproxy / purity-harness
context this builds on), [[two-sources-for-one-fact-remove-dont-reconcile]] (the
single-source principle applied from the same unit — the reason a stat is stored
once rather than as both a named field and an attrs entry).

---
title: "Two sources for one fact: remove the second source, don't reconcile them"
date: 2026-07-21
category: architecture-patterns
module: engine/combat
problem_type: architecture_pattern
component: combat
severity: high
applies_when:
  - "A value is looked up from a table AND also held on the object it describes"
  - "A rules/config bundle carries a lookup over data an entity already owns"
  - "Passing a stats/lookup table alongside the entities it applies to"
  - "A convention ('always build the bundle over the same table') is the only thing keeping two copies in sync"
tags:
  - combat
  - architecture
  - data-model
  - single-source-of-truth
---

# Two sources for one fact: remove the second source, don't reconcile them

## Context

Combat setup (amendment A1) had a fight that took a `weapon_stats` table
`{weapon_id: (ts, tg, range)}` **and** a `RulesBundle` carrying an
`equipment_stats(handle)` callable over its own copy of that table. A combatant
held an opaque weapon *id*; the engine resolved the id through a table to get
stats.

That is two sources for one fact — a weapon's stats — and they could disagree
silently. Reproduced on the working tree before the fix:

```python
fight = build_fight(..., weapon_stats={7: (99, 99, 20)},
                    rules=rules_for(WEAPON_STATS))   # bundle over the STOCK table
fight.weapon_range(7)      # -> 20            from the fight's own table
fight.equipment_stats(7)   # -> {ts: 6, tg: 15}   from the BUNDLE's table
```

The fight fired with stock accuracy/damage while its reach came from the custom
table. Nothing raised — it just used the wrong numbers. The test helper avoided
it by threading `rules_for(table)` at the call site, but that is a **convention**,
not an enforced invariant: one forgetful call site away from recurring.

This is the same defect class as a stale copied lookup and as a vacuous test
(see [[tests-that-cannot-fail]]): **a wrong value that produces a plausible
result instead of an error.** The failure has no symptom until someone notices
the numbers are off.

## Guidance

**When one fact has two sources, delete a source — do not add reconciliation.**

Options that *keep* both lookups only manage the symptom:

- "Validate that the two tables agree at construction" — a guard that can be
  forgotten, and that still admits the window between construction and the check.
- "Prefer one table over the other" — makes the divergence quiet instead of
  loud; the wrong-number outcome is unchanged.

The fix removes the second source. Each combatant now carries its **constructed
equipment mapping** (`{ts, tg, range}`), built once by the game before the fight.
The engine reads equipment **off the combatant it was handed** — there is no
table and no id to resolve:

```python
# engine/combat.py — reads the entity, not a table (commit b6e5bea)
def equipment_stats(self, combatant: Fighter) -> Mapping[str, int]:
    return combatant.equipment

def equipment_range(self, combatant: Fighter) -> int:
    return combatant.equipment.get("range", DEFAULT_RANGE)
```

`CombatFight` holds no `_weapon_stats`; `RulesBundle` has no `equipment_stats`
callable; `StartCombat` has no `weapon_stats` field. The table exists only inside
the game's one-shot `equipper(...)` at setup, then ceases to exist — which is
*why* nothing downstream can disagree with the roster.

## Why This Matters

The two-table divergence is now **unrepresentable**, not merely guarded. There is
only one place a weapon's stats can come from, so "the two disagree" is not a
state the type system can express. A guard you can forget is worth less than a
shape that cannot be wrong.

Consequences that fell out for free once the second source was gone:

- The "unknown handle" failure mode ceased to exist — no handle, no lookup, so
  "raise on an unknown id" is satisfied by construction rather than by a guard.
- Inventing an entity got simpler: a scenario builds a combatant holding whatever
  equipment it likes, with no parallel stats entry to keep in sync.
- The engine stopped knowing that equipment is keyed by an integer id at all —
  its attribute-agnosticism improved.

## When to Apply

Reach for "remove the source" whenever you catch yourself about to write a
consistency check between two representations of the same fact, or a comment that
says "these must be kept in sync." Ask: can one of them be *derived from* or
*carried by* the other, so the second never exists independently? If yes, that is
almost always cheaper and safer than the check.

Not every duplicated-looking value is this. A cache with an explicit invalidation
contract, or two representations that legitimately diverge (a display string vs.
its source datum), are not "two sources for one fact." The test is whether the two
are *supposed* to always be equal — if they are, one of them should not exist.

## Examples

**Before** — id handle + external table, reconciled by convention:

```python
class RulesBundle:
    equipment_stats: Callable   # lookup over a table passed separately
fight = CombatFight(..., weapon_stats=table, rules=rules_for(table))
#                        ^^^^^^^^^^^^^^^^^^^        ^^^^^^^^^^^^^^^^^^
#                        two copies; "must match" is a convention, not a type
```

**After** — the entity carries its own data; no table, no reconciliation:

```python
# game builds the equipment onto each combatant, once, at setup:
side = tuple(replace(f, equipment=equip(f.weapon)) for f in raw_side)
fight = CombatFight(..., rules=build_rules())   # bundle carries formulas only
# there is no table for anything to disagree with
```

See `docs/plans/2026-07-20-003-amendments.md` §A1 for the full decision record.
Related: [[tests-that-cannot-fail]] (same "plausible wrong value" defect class in
test design), [[subclass-across-a-layer-boundary-needs-cross-class-eq]] (a
different single-source fix from the same unit — stats stored once, not as both a
named field and an attrs entry).

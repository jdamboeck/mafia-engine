---
title: "Map decode: the cell index is authoritative, not the research screen code"
date: 2026-07-20
category: architecture-patterns
module: engine/movement
problem_type: architecture_pattern
component: map_decode
severity: medium
applies_when:
  - "Decoding the 40x25 city map or its door table"
  - "Cross-checking a decoded cell against research-data's karte_code column"
  - "Adding or moving a location door"
tags:
  - map
  - decode
  - fidelity
  - research-data
related_components:
  - movement
  - city
---

# Map decode: the cell index is authoritative

## Context

The city map is decoded once into `city.yaml`. Two candidate sources exist for
verifying a decoded tile: the **linear cell index** derived from the original's
own `lc` door-lookup routine, and the **`karte_code` column** in
`../research/research-data/`.

They do not agree, and only one is authoritative.

## Guidance

**Key the door lookup on the cell index / `(la, ln)` pair taken from the `lc`
table.** That is what the original actually consults at runtime:

- A move targets one cell, `:2030` `p=br+po(sp)+x`, and only a street cell
  (grid code `156`) is walked onto: `:2035` `ifpeek(p)<>156goto2045`, else
  `:2040` `po(sp)=po(sp)+x:ms=ms-1`. Tested by `test_step_onto_street_costs_one_ms`
  and `test_blocked_cell_does_not_move_or_spend`.
- Any other target cell (after the two map-event cells,
  `:2045` `ifpo(sp)+x=569thenla=13` and `:2046` `ifpo(sp)+x=861thenla=14`) goes to
  the `lc` routine, which matches that cell against the door table: `:2050` `syslc,p:la=peek(ua+1):ln=peek(ua+2):ifla=0goto2005`.
  Tested by `test_enter_slw_via_door_target` and
  `test_door_lookup_is_cell_keyed_across_multiple_locations`.

Adjacency is not consulted — a location is entered by a move whose *target* is
its door cell. The player never stands on the door: `po` changes only on the
`156` path, so it stays where it was (also asserted by
`test_enter_slw_via_door_target`).

**Do not cross-assert against the `karte_code` column** in the sibling
`../research/` checkout (outside this repo — a doc-claims validator will flag
the path as unresolvable, which is expected). It is a
human-readable transcription layer, not a byte-faithful dump. Treating a
mismatch there as a decode bug sends you looking for a fault that does not
exist.

## Why This Matters

The research checkout is authoritative for *game knowledge* — rules,
probabilities, formulas — but its derived columns are an interpretation layer.
This is the same trap that produced the inverted relational-sign convention
(see `basic-relational-boolean-is-minus-one-when-porting.md`): a value the
research layer *computed* was mistaken for a value it *observed*.

When the decompiled source and a research column disagree, the source wins
(KTD-9).

## When to Apply

- Verifying any decoded map cell.
- Adding a location door: derive the cell from `lc`, not from `karte_code`.
- Any time a research-data column is about to be used as a correctness oracle —
  check whether it is transcribed or derived first.

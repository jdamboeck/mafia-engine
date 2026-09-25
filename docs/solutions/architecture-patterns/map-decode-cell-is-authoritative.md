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
table.** That is what the original actually consults at runtime: movement gates
on grid code `156` and the `lc` routine matches a target cell against the door
table. Adjacency is not consulted — a location is entered by stepping *onto*
its door cell.

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
(see `basic-relational-boolean-is-plus-one-when-porting.md`): a value the
research layer *computed* was mistaken for a value it *observed*.

When the decompiled source and a research column disagree, the source wins
(KTD-9).

## When to Apply

- Verifying any decoded map cell.
- Adding a location door: derive the cell from `lc`, not from `karte_code`.
- Any time a research-data column is about to be used as a correctness oracle —
  check whether it is transcribed or derived first.

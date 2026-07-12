# Mafia Oracle — reference

Deeper detail for the [mafia-oracle](SKILL.md) skill: the research file map, how to
route a question to the right source, and the provenance model. All paths are under
`../research/` (sibling of this engine repo).

## The authoritative source

**`src/decompiled_basic/mf-prg.bas`** — the 870-line decompiled BASIC program is the
ground truth for *behavior*. Line numbers are sparse; use the section map:

| Lines | Section |
|-------|---------|
| 100–155 | Title screen + boot |
| 170–350 | Player setup (name, year, players, gangsters) |
| 1000–1100 | Main loop (turn rotation, per-turn upkeep) |
| 1350–1520 | Shared subroutines (stat display, jail turn) |
| 2000–3110 | Map render, movement, location dispatch (`SYS lc`/`i1`/`ie`) |
| 10000–26080 | Location handlers (12 locations + special cells) |
| 27000–30500 | Combat (bandenkrieg, jail fight, grid, damage, AI) |
| 40000–40166 | Win / lose / tie |
| 50100–50700 | DATA tables (weapons, vehicles, ranks) |

Machine code (`mf-ml`, load `$C000`) is disassembled 8/8 routines — cite a `$addr`
(e.g. `mf-ml:$C000` = the `cr` combat-AI targeting routine).

## Research file map — where each kind of answer lives

| Question is about… | Read |
|---|---|
| What a specific BASIC line does | `oracle.py line <n>` → `research-data/pass-1/basic-extraction.yaml` (`line_documentation`) |
| A menu option's behavior/guards | `research-data/pass-2/location-handlers.yaml` |
| Menu trees, option→handler mapping | `research-data/pass-1/location-extraction.yaml` |
| RNG outcome tables, combat AI, score/rank | `research-data/pass-2/game-logic.yaml` |
| Combat / economy / wanted rules, the ~108 vars | `research-data/pass-2/systems-analysis.yaml`, `data-structures.yaml` |
| A formula (damage/energy/payout/win) | `oracle.py node <id>` → KG `Formula`/`CombatRule` nodes |
| Gangster stats | `research-data/pass-1/gan-extraction.yaml` (30 files) |
| Graphics / picture / charset / sprite format | `research-data/pass-2/graphics-formats.yaml` |
| Sound (SID) | `research-data/pass-2/data-structures.yaml` (`sid_write_sequences`) |
| Machine-code routine (`cr`/`i1`/`ie`/`rc`/`lc`/`lh`/`so`/`bl`) | `research-data/verification/ml-core-disassembly.yaml` + KG nodes |
| Edge cases / boundaries | `research-data/pass-4/edge-cases.yaml` |
| A community/wiki claim | `research-data/pass-4/community-claims-verification.yaml` |
| Human-readable system summary | `docs/systems/{combat,economy,wanted,location}-system.md`, `game-flow.md` |

## The knowledge graph (`research-data/pass-3/knowledge-graph.yaml`)

76 nodes, 126 edges. Every node cites source-backed `evidence` (`line_quote` /
`byte_offset` / `vice_read`) and carries `verified` + `verification_method`.

Node types: **System** (9), **DataStructure** (11), **Entity** (33, incl. 30 gangsters),
**Location** (12), **CombatRule** (5), **Formula** (4), **GameFlow** (2).

Key ids to know: `combat-system`, `economy-system`, `wanted-system`, `location-system`,
`damage-formula`, `combat-damage-rule`, `combat-ai-rule`, `cr-combat-targeting`,
`win-condition-formula`, `payout-20050-formula`, `map-karte-structure`, `mafia-loader-boot`.

## Provenance — extracted vs interpreted (pass it through in answers)

Every datum is one of two tiers. Keep them distinct in your answers:

- **Extracted** — a byte-level fact reproducible from `src/`: a DATA table, a byte range,
  a load address, a raw BASIC line, a stat string. Not opinion.
- **Interpreted** — the *meaning* built on top of an extracted fact: what a line does
  in-game, what a byte range is FOR, a System/Formula/CombatRule. Must cite its extracted
  basis.

The `line_documentation` entries and KG nodes carry a `provenance` field and a
`confidence` (high/medium/low). When you answer, say which tier and confidence — don't
present an `interpreted`/`medium` reading as settled fact. See
`../research/docs/PROVENANCE.md`.

## Verifying claims — the evidence kinds

`oracle.py verify "<claim>"` ranks KG nodes by relevance and prints their evidence.
Evidence kinds and what they prove:

- `line_quote` — the exact BASIC line implementing the claim (strongest for behavior).
- `byte_offset` — a byte range in a binary file (for data structures / ML).
- `vice_read` — a live/disassembly read from the running emulator (runtime-confirmed).

A claim is **CONFIRMED** only when it matches a citation of one of these kinds. If it
contradicts the citation, it's **WRONG** — state what the source actually says. If no
node addresses it, it's **UNVERIFIED**.

## Fidelity note for the engine

The engine's bar is **behavioral, not bit-exact** (`../engine/CLAUDE.md`): match the
original's formulas, probabilities, rewards, and outcomes exactly, but internal
representation and RNG draw-order may differ. So when the oracle gives you a formula,
port the *formula and its inputs* faithfully; you need not replicate the exact `rnd(1)`
call sequence. A seeded run matches statistically/mechanically, not byte-for-byte.

## Tooling in the research project (to re-verify, not just read)

Run with `../research/.venv/bin/python3` from the research dir:

- `tools/validate_knowledge_graph.py` — schema + evidence validity of the KG.
- `tools/check_provenance.py` — every interpreted datum traces to an extracted fact.
- `tools/disasm_ml_region.py src/mf-ml <start> <end>` — annotated 6502 disassembly of
  any machine-code range (e.g. `0xC000 0xC0EA` for `cr`).
- `tools/analyze_source_coverage.py` — regenerates the coverage metrics `status` reports.

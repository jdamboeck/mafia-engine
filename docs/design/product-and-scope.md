# Product and Scope

## Project goal

Build a fresh Python reimplementation of the 1986 Commodore 64 game **Mafia** by Igelsoft as the reference title for a reusable **genre engine**.

There is no legacy game code to reuse. The implementation ports behavior from the reverse-engineered research pipeline into modern Python data structures, handlers, and tests.

## Genre-engine scope

The engine abstracts **turn-based, board-game-like strategy games with tactical combat**:

- city or board maps with movement points;
- menu-driven locations;
- RNG-driven outcomes;
- progression, score, rank, and win conditions;
- separate tactical-grid combat;
- a deterministic interaction protocol suitable for terminal, WebSocket, and later graphical clients.

The reusable core is the genre machinery: game FSM, turn loop, movement economy, location/menu shell, handler driver, interaction protocol, effect/event application, logged RNG, replay contract, and tactical combat system.

## Reference game role

The reference game is Mafia: the player is a 1920s Chicago gangster who moves around a city map, visits locations such as pub, gun shop, bank, casino, loan shark, and police station, commits crimes, earns money, recruits and trains a gang, fights rival gangs on a tactical grid, and climbs ten criminal ranks.

Mafia proves the genre seam. Its content, rules, formulas, strings, assets, setup flow, and Python handlers live under `data/game_configs/mafia_1920s/`, not inside `engine/`.

## Fidelity bar

The fidelity target is **behavioral**, not byte-exact:

- Reproduce the original formulas, probabilities, rewards, outcomes, and documented quirks exactly as verified by research.
- Use clean modern representations where appropriate.
- Do **not** require the exact original RNG draw order.
- A seeded run should match the original statistically and mechanically, not byte-for-byte.

## Source-of-truth policy

All reference-game rules come from the reverse-engineering pipeline in `../research/`.

| Resource | Path | Use |
|---|---|---|
| Decompiled BASIC | `../research/src/decompiled_basic/mf-prg.bas` | Authoritative handler logic and BASIC line blocks. |
| Systems analysis | `../research/research-data/pass-2/systems-analysis.yaml` | Combat, economy, wanted, and location rules. |
| Game logic | `../research/research-data/pass-2/game-logic.yaml` | RNG tables, combat AI, score and rank formulas. |
| Location handlers | `../research/research-data/pass-2/location-handlers.yaml` | Per-option behavior and guards. |
| Data structures | `../research/research-data/pass-2/data-structures.yaml` | Map/combat grids and variables. |
| Menu trees | `../research/research-data/pass-1/location-extraction.yaml` | Menu labels and option-to-handler mapping. |
| Dialogue | `../research/research-data/pass-2/location-dialogue.yaml` | Verbatim location script and printed responses. |
| Text corpus | `../research/research-data/pass-1/game-text.yaml` | Full on-screen text corpus. |
| System docs | `../research/docs/systems/*.md` | Human-readable summaries. |

When a mechanic is unclear, read the BASIC line block it maps to. Do not invent behavior.

## What should and should not be generalized

Generalize only mechanics another same-genre game would share:

- top-level FSM;
- turn and movement economy;
- location shell and guard evaluation;
- handler interaction protocol;
- effect/event vocabulary;
- deterministic RNG and replay;
- tactical-grid combat framework;
- config loading and validation;
- theme/string resolution.

Do **not** generalize Mafia-specific facts into the engine:

- Mafia formulas and numeric constants;
- Mafia location ids, menus, dialogue, and handlers;
- 1920s assets, names, ranks, weapons, vehicles, and special map events;
- game-specific setup choices and win-condition parameters.

A new title in the genre should start by copying `data/game_configs/mafia_1920s/` and editing its data, formulas, strings, assets, and handlers. The engine itself should remain untouched unless a second config proves a real shared seam.

# Mafia Engine

Mafia Engine is a fresh Python reimplementation of the 1986 Commodore 64 game **Mafia** by Igelsoft, built as the reference title for a reusable genre engine for turn-based, board-game-like strategy games with tactical combat.

The engine is not a single-title clone and not a fully generic game engine. It provides the reusable runtime for games with a map, movement economy, menu-driven locations, RNG outcomes, progression, and tactical-grid combat. Mafia-specific rules, strings, formulas, assets, and handlers live in a game configuration.

## Current status

Implementation is in progress on the first vertical slice. The project has a Python skeleton, pytest-based test suite, soft lint gate, config-loading scaffolding, interaction/effect primitives, movement/map support, and early location-slice tests.

The design is split across focused documents under `docs/design/`. The active implementation plan is tracked separately.

## Quickstart

```bash
pip install -e '.[dev]'
make check
```

`make check` runs `pytest` and a soft `ruff` lint check when `ruff` is installed.

## Documentation map

- [Product and scope](docs/design/product-and-scope.md) — project goal, genre boundary, fidelity policy, source-of-truth rules, and generalization policy.
- [Engine architecture](docs/design/engine-architecture.md) — FSM, interaction protocol, `EngineResult`, events/effects, commit pipeline, handler driver, cancellation, replay, RNG, and layering.
- [Mafia rules spec](docs/design/mafia-rules-spec.md) — reference-game facts, formulas, locations, combat, economy, scoring, police/jail rules, quirks, and research references.
- [Config and content contract](docs/design/config-and-content-contract.md) — game-config directory layout, handler/setup ownership, `engine_api`, YAML shells, guards, consequences, strings/themes, schemas, and import rules.
- [Current action plan](docs/plans/current-action-plan.md) — active implementation plan pointer.

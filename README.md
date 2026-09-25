# Mafia Engine

Mafia Engine is a fresh Python reimplementation of the 1986 Commodore 64 game **Mafia** by Igelsoft, built as the reference title for a reusable genre engine for turn-based, board-game-like strategy games with tactical combat.

The engine is not a single-title clone and not a fully generic game engine. It provides the reusable runtime for games with a map, movement economy, menu-driven locations, RNG outcomes, progression, and tactical-grid combat. Mafia-specific rules, strings, formulas, assets, and handlers live in a game configuration.

## Current status

The first vertical slice plays end to end: set up a game, walk the city, use five locations, fight, save, and reach the original's year-end ending. Crime, wanted, arrest and jail, the two map-triggered win flows, and the remaining locations are the next slice.

The design is split across focused documents under `docs/design/`. Implementation plans live in `docs/plans/`; `CLAUDE.md` explains how to tell which one is active.

## How to play

```bash
pip install -e .
python -m clients.terminal
```

Setup asks for the year the game ends (`spielende`, 1928-1978) and the score weight (`punktewertigkeit`, 0.1-2). The clock starts in January 1925. When the end year arrives, the player with the most points wins.

| Key | Where | Does |
|---|---|---|
| `W` `A` `S` `D` | map | move; walk into a door to enter a location |
| number | location menu | pick an option |
| `P` | map | save the game |
| `Q` | map, turn-over | quit |
| `W` `A` `S` `D` | fight | move the active gangster |
| `F` then `W` `A` `S` `D` | fight | shoot in that direction |
| `P` | fight | pass |
| `surrender` | fight | give up the fight |

Useful flags (`python -m clients.terminal --help` lists them all):

- `--player NAME:GANG`, repeatable up to 4 times, for hot-seat play.
- `--end-year 1930 --score-weight 1` to skip the setup questions.
- `--load PATH` to resume a saved game; `--save PATH` to choose where `P` saves (default `mafia-save.jsonl`).
- `--seed N` for a reproducible new game.
- `--watch-ai` to see the board after each computer move in a fight.

To debug fights on their own, see the fight lab: [clients/terminal/FIGHTLAB.md](clients/terminal/FIGHTLAB.md).

### What works today

- Setup with the original's end-year and score-weight questions, 1-4 hot-seat players.
- The city map with movement points per vehicle and turn rotation.
- Five locations: pub (`pub`), casino (`sph`), apartment rental (`slw`), loan shark (`kdh`) and weapon shop (`waf`), including jobs and turn-start upkeep (rent, debt collectors, promotions).
- Tactical combat on the 40x13 grid against computer-controlled gangs, with recording and replay.
- Standings after every round, and the year-end winner or tie screen.
- Save and resume with the exact same random sequence.

## Development

```bash
pip install -e '.[dev]'
make check
```

`make check` runs `pytest`, then `ruff check` and `ruff format --check`. It is the gate for every commit, and CI runs it on every push and pull request.

## Documentation map

- [Product and scope](docs/design/product-and-scope.md) — project goal, genre boundary, fidelity policy, source-of-truth rules, and generalization policy.
- [Engine architecture](docs/design/engine-architecture.md) — FSM, interaction protocol, `EngineResult`, events/effects, commit pipeline, handler driver, cancellation, replay, RNG, and layering.
- [Mafia rules spec](docs/design/mafia-rules-spec.md) — reference-game facts, formulas, locations, combat, economy, scoring, police/jail rules, quirks, and research references.
- [Config and content contract](docs/design/config-and-content-contract.md) — game-config directory layout, handler/setup ownership, `engine_api`, YAML shells, guards, consequences, strings/themes, schemas, and import rules.
- [Plans](docs/plans/) — implementation plans; `CLAUDE.md` § Current state explains how to find the active one.

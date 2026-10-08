# Mafia Engine

Mafia Engine is a fresh Python reimplementation of the 1986 Commodore 64 game **Mafia** by Igelsoft, built as the reference title for a reusable genre engine for turn-based, board-game-like strategy games with tactical combat.

The engine is not a single-title clone and not a fully generic game engine. It provides the reusable runtime for games with a map, movement economy, menu-driven locations, RNG outcomes, progression, and tactical-grid combat. Mafia-specific rules, strings, formulas, assets, and handlers live in a game configuration.

## Current status

The whole game plays in the terminal client, for 1 to 4 hot-seat players: setup as in the original, all 12 locations, crime, the police and jail, both map-triggered win flows, the early win, the gang war, the prison brawl, and the year-end ending. Every line block of the original's BASIC is accounted for in `docs/coverage-ledger.yaml`, which the test suite keeps in sync with the code.

The design is split across focused documents under `docs/design/`. Implementation plans live in `docs/plans/`; `CLAUDE.md` explains how to tell which one is active.

## How to play

```bash
pip install -e .
python -m clients.terminal
```

A new game starts with the original's setup screens:

1. The year the game ends (`spielende`, 1928-1978) and the score weight (`punktewertigkeit`, 0.1-2).
2. The house rules: Enter plays every quirk of the original as it was; `h` lists them, so you can switch each one to how it was probably meant.
3. The number of players (`spieleranzahl`, 1-4).
4. For each player in turn: their name and their gang's name (1-13 characters each), then the `eigenschaften` screen. Kraft, intelligenz and brutalitaet each cycle through values until you press a key, and the value on screen is the one you keep. Energie (5) and your starting cash follow.

The clock starts in January 1925. Players take turns at the same keyboard. When the end year arrives, the player with the most points wins.

| Key | Where | Does |
|---|---|---|
| number | turn menu | overview, walk the city, gang war, next player |
| `W` `A` `S` `D` | map | move; walk into a door to enter a location |
| `M` | map | back to the turn menu |
| `1`-`9` | location menu | pick an option by its number, one key, no Enter |
| `P` | turn menu, map | save the game |
| `Q` | turn menu, map, turn-over | quit |
| `W` `A` `S` `D` | fight | move the active gangster |
| `F` then `W` `A` `S` `D` | fight | shoot in that direction |
| `P` | fight | pass |
| `surrender` | fight | give up the fight |

Useful flags (`python -m clients.terminal --help` lists them all):

- `--player NAME:GANG`, repeatable up to 4 times: the game skips the player count and name questions. Each player still rolls their stats on the eigenschaften screen.
- `--end-year 1930 --score-weight 1` to skip those two setup questions.
- `--load PATH` to resume a saved game; `--save PATH` to choose where `P` saves (default: the `--load` file, else `mafia-save.jsonl`).
- `--seed N` for a reproducible new game. Stat rolls you stop by hand depend on your timing; with piped input every roll stops on its first value, so a scripted game with a seed always starts the same.
- `--watch-ai` to see the board after each computer move in a fight.
- `--theme NAME|PATH` to word the game in another theme (default `classic`).

To debug fights on their own, see the fight lab: [clients/terminal/FIGHTLAB.md](clients/terminal/FIGHTLAB.md).

### What works today

- The original's setup: end year, score weight, 1-4 players with names and gang names, and the eigenschaften screen where each player stops their own stat rolls. Optional house rules switch the original's quirks to their intended behavior, among them the C64's float arithmetic in the score and the negative numbers the C64 accepts at some prompts.
- The city map with movement points per vehicle and hot-seat turn rotation.
- All 12 locations: the motel (`slw`), pub (`pub`), weapon shop (`waf`), car dealer (`aut`), loan shark (`kdh`), casino (`sph`), the shop to squeeze for protection money (`sgl`), subway (`sub`), railway station (`bhf`), bank and post office (`ban`), police headquarters (`pol`) and the counterfeiter (`ble`), with jobs and turn-start upkeep (rent, debt collectors, promotions).
- Crime and the police: roadblocks and wanted posters, arrest, the trial with its lawyer, bribing the police chief or the guards, jail and the prison brawl.
- The two map-triggered win flows (the cash transport and the mayor), the early win at the top rank, and the gang war between players.
- Tactical combat on the 40x13 grid against computer-controlled gangs, with recording and replay.
- The original's pacing: a location result, a fight's outcome and the screens that wait in the original wait for a key; the turn-start upkeep messages share one screen.
- Standings after every round, and the year-end winner or tie screen.
- Save and resume with the exact same random sequence.

## Development

```bash
pip install -e '.[dev]'
make check
```

`make check` runs `pytest`, then `ruff check` and `ruff format --check`, then `pyright`. It is the gate for every commit, and CI runs it on Python 3.11 and 3.14 for every push and pull request.

## Documentation map

- [Product and scope](docs/design/product-and-scope.md) — project goal, genre boundary, fidelity policy, source-of-truth rules, and generalization policy.
- [Engine architecture](docs/design/engine-architecture.md) — FSM, interaction protocol, `EngineResult`, events/effects, commit pipeline, handler driver, cancellation, replay, RNG, and layering.
- [Mafia rules spec](docs/design/mafia-rules-spec.md) — reference-game facts, formulas, locations, combat, economy, scoring, police/jail rules, quirks, and research references.
- [Config and content contract](docs/design/config-and-content-contract.md) — game-config directory layout, handler/setup ownership, `engine_api`, YAML shells, guards, consequences, strings/themes, schemas, and import rules.
- [Plans](docs/plans/) — implementation plans; `CLAUDE.md` § Current state explains how to find the active one.

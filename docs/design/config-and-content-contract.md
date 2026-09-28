# Config and Content Contract

## Contract overview

The engine provides the genre runtime; a game config provides the game. Neither side reaches across the boundary except through documented public surfaces.

| Engine provides | Game config provides |
|---|---|
| Game FSM, turn runner (the order of a turn), movement economy | Locations and the turn menu as YAML menu/guard shells |
| Interaction driver and protocol | Handlers registered by id, including the turn hooks |
| Effect application, generic effects, and the genre-level `GameState` | Game effects, registered by tag |
| Registries for effects, state schema, and guard variables | The state schema and the guard variables its shells read |
| Combat system | Entities: weapons, vehicles, gangsters, ranks |
| Seedable logged RNG | Formula parameters and win conditions |
| Persistence/replay contract | Strings, assets, sounds, and default theme |
| String/theme resolution and networking adapters | `engine_api` version target, config-owned setup and formulas, house rules |

## Config directory structure

A game config is a self-contained directory:

```text
data/game_configs/mafia_1920s/
├── __init__.py
├── setup.py
├── effects.py           # game effects, registered by tag
├── state_schema.yaml    # declared value maps: names, types, defaults
├── state.py             # typed accessors over the value maps
├── handlers/
│   ├── slw.py
│   └── turn.py          # turn hooks under fixed keys
├── config.yaml
├── content/
│   ├── map/city.yaml
│   ├── menus/turn.yaml
│   ├── locations/*.yaml
│   ├── entities/*.yaml
│   └── house_rules.yaml
└── themes/classic/
    ├── strings/
    ├── assets/
    ├── sounds/
    └── renderer/
```

A new same-genre title should copy this directory and edit data, formulas, handlers, strings, and assets. The engine loads configs by path, so a copied config does not need to be installed as an engine package.

## Config-owned setup/formulas/handlers

The following are config code, not engine code:

- `setup.py` with `new_game`, setup choices, `fnm`, formula helpers, and entity loading;
- `handlers/*.py` with generator handlers for menu actions, and the turn hooks the engine's turn runner calls under fixed keys (upkeep, turn start, job skip, score truncation, jail skip, roadblock, special cells);
- `effects.py` with the game's effects, each carrying its own `apply`;
- the state schema and its typed accessors;
- the guard variables its shells read;
- the house-rules catalogue and its switches;
- formula constants and win-condition parameters;
- location YAML, map data, entities, strings, assets, sounds, and renderer theme data.

The engine keeps only generic mechanisms: config loader, schema validation, the handler, effect, state-schema and guard-variable registries, interaction types, generic effects, the turn runner, movement, combat framework, RNG, and effect application. Which effect and which piece of state lives where is listed in `engine-architecture.md`, "Engine/config seam".

## `engine_api`

`config.yaml` declares:

```yaml
engine_api: 2
```

The engine refuses or adapts configs whose targeted API it cannot satisfy. Bump `engine_api` only on breaking changes to:

- handler API, including the public state-update helpers a config effect's `apply` uses;
- interaction catalog;
- generic effect catalog and the effect registration contract;
- config schema, including the state schema format;
- entity schemas;
- guard/consequence formats and the guard-variable registration contract;
- turn hook keys.

Version 2 is the engine/config seam. Version 1 had game effects and game state inside `engine/`. The bump is taken once, together with the save format's `SCHEMA_VERSION` (`engine-architecture.md`, "Save/replay at interaction boundaries"). Later additions ride on version 2: a new game effect or a new declared state key is a config change, not an API change.

## YAML location shell format

Location YAML owns menu structure, guards, denial messages, handler ids, and simple data-only consequences.

```yaml
key: pub
options:
  - id: recruit
    guard:
      and:
        - {stat: rank, op: '>', value: 4}
        - {stat: gang_size, op: '<', value: 10}
    on_denied: system.pub.rank_too_low
    handler: pub.recruit

  - id: leave
    resolve:
      consequences:
        - {type: ms_change, amount: -5}
```

The engine owns menu rendering, guard evaluation, denial messages, movement-point bookkeeping, and string resolution. Procedural behavior is referenced by handler id.

## Guard DSL

The guard DSL supports:

- operators: `=`, `!=`, `>=`, `<=`, `>`, `<`, `in`;
- connectives: `and`, `or`;
- nesting depth at most 2;
- no `not` operator.

The no-`not` rule keeps guards simple; rewrite guards positively instead. This DSL is sufficient for every documented Mafia menu guard in `location-handlers.yaml`.

## YAML consequences

Simple options may resolve through pure-data consequences. A consequence's `type` is the consequence name an effect registered alongside its tag. The tag is the effect's class name (`ScoreChange`), and it is what a save writes; the consequence name is the snake_case name a shell writes (`score_change`). The engine registers consequence names for its generic effects, for example:

- `money_change`;
- `score_change`;
- `ms_change`;
- `set_position`;
- `teleport`;
- `set_entry_context`;
- `stat_change_capped`;
- `assign_weapon`.

The config registers its game effects the same way (for example `score_and_rank`), and its shells may use those names too. There is no separate consequence list to keep: an effect becomes a consequence by naming one when it registers.

Anything involving input loops, RNG branches, minigames, combat, computed outcomes, or multi-step dialogue belongs in a Python handler.

## Handler API

A handler is a generator `def handler(ctx): ...` and may use only:

- `ctx.state` — read-only `GameState` view;
- `ctx.rng` — seedable logged RNG;
- `yield <Interaction>` — the public interaction catalog;
- `ctx.apply(<Effect>)` — public effect API, for generic engine effects and the config's own effects;
- engine helpers for shared mechanism such as yes/no and combat entry, and the public state-update helpers;
- the config's own shared helpers for game routines such as the score update, the gangster picker and police capture.

Handlers may not:

- import from `server/` or `clients/`;
- access sockets, transports, renderers, or client APIs;
- read wall-clock time or OS randomness;
- mutate `GameState` in place;
- import private engine internals;
- rely on ambient nondeterminism.

Handlers must be deterministic given `(ctx.state, ctx.rng, Responses)`.

A config effect's `apply` may use only the engine's public state-update helpers (for example, set one player's value) and the state it is given. It never rebuilds the state graph itself and never reads `ctx`.

An interaction answered by someone other than the active player names that player through its player index. A handler sets it; in a fight, each side's owner sets it for that side's prompts.

## Config effects, state and guard variables

A config registers three kinds of thing when its package is imported:

- **Effects.** A frozen dataclass with an `apply(state) -> state` method, registered under a tag by a decorator. Re-registering a tag replaces the old entry, so a config reload is safe.
- **State.** A schema of names, types and defaults for the per-player and the global value maps. The engine freezes the maps, saves them, and fills a declared default for a missing key on load. An unknown key is refused.
- **Guard variables.** A name and a resolver `(state, ln)`. The engine's guard DSL resolves only registered names. It resolves none on its own.

The loaded config returns all three registries, as it returns `handlers`. Save loading and replay take them. The engine never imports a config effect or names a config key.

## House rules

The original has bugs whose intent is clear. The config ports the faithful behavior by default and offers a switch where the intent differs.

- The quirk catalogue is one YAML file in the config. Each entry has an id, a citation, the faithful behavior, the intent behavior (or a reason there is none), and whether it has a switch.
- The switches live on `state.config` as a frozen map. No effect may write it. Handlers read it through `ctx.state`.
- The combat rules bundle carries the same map as a frozen data field. Recordings serialize it, and loading a recording compares it.
- The engine never reads a switch by its id. A quirk in engine code, such as the shared direction memory, gets a neutral mechanism setting that the config sets from the map.
- Setup shows the switchable entries. The map is stored in saves, recordings and scenarios. Replay refuses on a mismatch.

## Theme/string conventions

The engine emits string keys and params; themes format display text.

- No hardcoded display strings in `engine/`.
- Handler branching logic may be Python, but displayed text is externalized.
- Strings are parameterized templates, e.g. `pro monat kostet das {price}$ miete`.
- Themes are runtime-swappable and deep-merge over the config default theme at key level.
- Themes cannot change rules or outcomes.

Key conventions:

- `locations.{id}.menu.{option}`;
- `locations.{id}.dialogue.{node}.{id}`;
- `system.{subsystem}.{msg}`;
- `ui.{label}`;
- `entities.{type}.{id}`.

## Entity/config schemas

The config schema covers:

- `config.yaml`: `engine_api`, config id, default theme, setup parameters, win-condition knobs;
- locations: keys, options, guards, handler ids, simple consequences;
- map: 40×25 city grid, door placements, tenancy, special cells;
- weapons: price, accuracy `ts`, damage `tg`, sound `ws`, range category;
- vehicles: price, speed, and display metadata;
- gangsters: names and stats such as energy, `kraft`, intelligence, brutality, weapon; the declared stat names are the only names a stat effect accepts;
- state: the per-player and global value maps, with names, types and defaults;
- ranks: rank ids, labels, thresholds where needed;
- formulas: score weight, end year, costs, payout constants, stat caps, and other tunables;
- themes: string templates, assets, sounds, renderer metadata.

`engine/types/` holds abstract contracts only for what the engine itself reads, such as a weapon's `range` in combat. Fields only the game reads, such as per-weapon stat minimums, are declared and validated by the config. Game data lives under `data/game_configs/<game>/`.

## Allowed config imports

Config Python may import:

- public engine types from `engine.types`;
- interaction classes from `engine.interactions`;
- generic effect classes, the effect registration decorator, and the public state-update helpers from `engine.effects`;
- public registration APIs: handlers from `engine.locations`, and the state-schema and guard-variable registration the engine documents;
- documented engine helper APIs.

Config Python must not import:

- `server/`;
- `clients/`;
- transport or rendering libraries;
- private engine modules or implementation details;
- other game configs except through explicit data-copy/fork workflows.

The engine must not statically import `data/`. It loads a config by path with runtime import machinery and validates it against the public schema.

## Project structure target

```text
mafia/
├── engine/              # pure Python simulation, generic framework only
│   ├── state/
│   ├── rng.py
│   ├── interactions.py
│   ├── effects.py
│   ├── conditions.py
│   ├── locations.py
│   ├── types/
│   ├── config_loader.py
│   └── combat/
├── server/              # async transport adapter over interaction protocol
├── clients/
│   ├── terminal/
│   └── pygame/
└── data/game_configs/mafia_1920s/
```

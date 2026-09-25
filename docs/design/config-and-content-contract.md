# Config and Content Contract

## Contract overview

The engine provides the genre runtime; a game config provides the game. Neither side reaches across the boundary except through documented public surfaces.

| Engine provides | Game config provides |
|---|---|
| Game FSM, turn loop, movement economy | Locations as YAML menu/guard shells |
| Interaction driver and protocol | Handlers registered by id |
| Effect/event application and `GameState` | Entities: weapons, vehicles, gangsters, ranks |
| Combat system | Formula parameters and win conditions |
| Seedable logged RNG | Strings, assets, sounds, and default theme |
| Persistence/replay contract | `engine_api` version target |
| String/theme resolution and networking adapters | Config-owned setup and formulas |

## Config directory structure

A game config is a self-contained directory:

```text
data/game_configs/mafia_1920s/
├── __init__.py
├── setup.py
├── handlers/
│   └── slw.py
├── config.yaml
├── content/
│   ├── map/city.yaml
│   ├── locations/*.yaml
│   └── entities/*.yaml
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
- `handlers/*.py` with generator handlers for menu actions;
- formula constants and win-condition parameters;
- location YAML, map data, entities, strings, assets, sounds, and renderer theme data.

The engine keeps only generic mechanisms: config loader, schema validation, handler registry/decorator, interaction/effect types, movement, combat framework, RNG, and state reducers.

## `engine_api`

`config.yaml` declares:

```yaml
engine_api: 1
```

The engine refuses or adapts configs whose targeted API it cannot satisfy. Bump `engine_api` only on breaking changes to:

- handler API;
- interaction catalog;
- effect/event catalog;
- config schema;
- entity schemas;
- guard/consequence formats.

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

Simple options may resolve through pure-data consequences. Supported effect/event types include:

- `money_change`;
- `stat_change`;
- `score_change`;
- `wanted_change`;
- `flag_set`;
- `ms_change`;
- `energy_change`;
- `jail`;
- `teleport`;
- `spawn_fighter`.

Anything involving input loops, RNG branches, minigames, combat, computed outcomes, or multi-step dialogue belongs in a Python handler.

## Handler API

A handler is a generator `def handler(ctx): ...` and may use only:

- `ctx.state` — read-only `GameState` view;
- `ctx.rng` — seedable logged RNG;
- `yield <Interaction>` — the public interaction catalog;
- `ctx.apply(<Effect>)` — public effect API;
- engine helpers for shared BASIC subroutines such as not-enough-money, gangster picker, score update, and combat entry.

Handlers may not:

- import from `server/` or `clients/`;
- access sockets, transports, renderers, or client APIs;
- read wall-clock time or OS randomness;
- mutate `GameState` in place;
- import private engine internals;
- rely on ambient nondeterminism.

Handlers must be deterministic given `(ctx.state, ctx.rng, Responses)`.

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
- gangsters: names and stats such as energy, `kraft`, intelligence, brutality, weapon;
- ranks: rank ids, labels, thresholds where needed;
- formulas: score weight, end year, costs, payout constants, stat caps, and other tunables;
- themes: string templates, assets, sounds, renderer metadata.

Schemas live in `engine/types/` as abstract contracts. Game data lives under `data/game_configs/<game>/`.

## Allowed config imports

Config Python may import:

- public engine types from `engine.types`;
- interaction classes from `engine.interactions`;
- effect classes/helpers from `engine.effects`;
- public registration API from `engine.locations`;
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

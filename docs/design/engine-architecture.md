# Engine Architecture

## Core design idea

Location logic is genuinely procedural. Mafia handlers include input loops, many RNG sites, arithmetic branch tables, minigames, and combat that starts mid-handler and resumes with a result.

The engine therefore has two layers:

1. **Declarative YAML shell**: menu structure, guards, denial messages, simple consequences.
2. **Procedural Python handlers**: one generator function per menu action, referenced by handler id.

The generator/interaction protocol is the spine of local play, tests, replay, and eventual networking.

## Top-level FSM

The whole game is one explicit finite state machine:

```text
Title ──▶ Setup ──▶ MainLoop ──▶ Win/Lose ──▶ restart
                       │
                       └── Turn runner (engine)
                           ├── next player; on wrap: round standings, year-end check
                           ├── upkeep, then the turn-start check (early win)
                           ├── movement points; job skip
                           ├── score truncation; jail skip
                           └── Turn menu (declarative shell)
                               ├── Overview   read-only screen
                               ├── Walk ──▶ map step ──▶ Location ──▶ handler generator
                               ├── Gang war ──▶ combat sub-FSM
                               └── Next player
```

Nested lifecycles are driven by the level above:

```text
Game FSM
└─ Turn runner            owns the order of a turn
   └─ Turn menu / map step   ends on ms <= 0 or handler-forced
      └─ Location visit / Combat entry / hook
         └─ Handler generator
            └─ Interaction       one prompt/message/combat request ↔ one screen state
```

### The turn runner

The engine owns the order of a turn. The config owns every rule in it. The runner follows the reference title's order:

1. `:1010`: next player; on wrap, the round standings and the year-end check.
2. `:1011`: upkeep, then the config's turn-start check (early win).
3. `:1012`: movement points and the job skip.
4. `:1013`: score truncation and the jail skip.
5. The turn menu.
6. The map step (`:2035-2060`): the map-move prompt; the special-cell hook before a move onto an event cell (569/861), since an armed cell is not a street; the step and the roadblock hook (a stop it reports costs the door's 5 points, `:2041 gosub6000:goto2060`); or the door entry, the location menu and its option, then the previous tile.

The config supplies each rule as a handler under a fixed key, the way `upkeep.turn_start` works today. Score truncation, the job skip, the jail skip, the roadblock and the special cells are game rules, so they are hooks. The runner only calls them in order.

The runner is an engine generator. It yields interactions to its driver, like a handler does, so a transport can hold one runner per session. It runs each hook and handler with `yield from step(...)`. `step()` is the generator form of the driver: it yields every interaction, including those from sub-states and fights, and returns what `run()` returns. `run()` is a thin loop over `step()`.

The runner commits each step separately. One commit per turn would hide the map between steps. Its own writes are generic engine effects: advance the turn, set movement points, set the previous tile, set the turn phase.

Two flow rules hold:

- When movement points reach 0 on the map, the turn ends with no menu. The menu reappears only when the player leaves the map with points left.
- After each location handler and each police capture, the runner re-reads movement points from state. A handler can raise them (a car purchase) or zero them (a sentence).

The turn phase (menu, walking) is part of `GameState`. Saving is offered only at the turn menu and the map-move prompt. A resumed save re-enters that phase without re-running turn start.

The turn menu is a declarative shell, loaded by the same code as a location shell. It has one handler per option: overview, walk, gang war, next player. Its guards read config-registered guard variables. The loaded config exposes its shells, so clients never find them by path.

The Game FSM state, active turn, turn phase, and any suspended handler state are part of `GameState` so saves and replay can reconstruct where execution stopped.

## Interaction protocol

A handler is a generator:

```python
Generator[Interaction, Response, list[Event]]
```

A handler yields an `Interaction`; the driver turns it into a typed, JSON-serializable screen state, obtains a `Response`, and sends that response back into the generator. The Phase 1 driver is in-process and synchronous. A future WebSocket server is the same driver behind an async transport.

Interaction catalog:

| Interaction | Fields | Response | Notes |
|---|---|---|---|
| `ShowMessage` | `key`, `params` | `Ack` | Display only. |
| `PromptInt` | `key`, `min`, `max` | `int` or cancel | Driver enforces range/type validation. |
| `PromptChoice` | `key`, `options[]` | chosen index/id or cancel | Menus, submenus, gangster picker. |
| `Confirm` | `key` | `bool` or cancel | Yes/no. |
| `StartCombat` | `scenario` (fighters, arena, rules — one payload) | `CombatResult` | Suspends turn and runs combat sub-FSM. |
| `LoadSubState` | `kind`, `params` | sub-state result | Nested minigames such as safe-cracking. |
| `Acknowledge` | `key`, `params`, `player` | `Ack` | The runner's upkeep, turn-over, standings and year-end screens. |
| `Heading` | `key`, `params` | ignored | Display only: a runner screen opens (the job shift, a location with no shell). |
| `MapMove` | `outcome`, `directions[]`, `commands[]` (save, quit), `player` | a direction or a command | The runner's map-move prompt, before each map step. `outcome` is what the last answer did (step, enter, wall, edge, special). Save is the driver's: the runner asks again. |
| `LocationMenu` | `location`, `options[]`, `ln`, `player` | chosen index | The location shell's options whose guard passes. Any other answer leaves the location. |
| `OptionDone` | `location`, `option`, `status`, `player` | ignored | Display only: the chosen option ran (`completed`, or `cancelled` with nothing committed). |

`MapMove`, `LocationMenu` and `OptionDone` are the runner's own; a handler never yields them.

Every interaction carries an optional player index. It defaults to the active player. An interaction answered by someone else names that player: the freed player's payment at the police station, the defender's side in a gang war, the jailed player's side in a prison brawl. Combat marks each side's controller this way. A client prints its whose-turn line from this field.

Shared BASIC subroutines become shared helpers. A routine that is mechanism (wait, yes/no, combat entry) is an engine helper or interaction. A routine that ports a game rule (the score update, the gangster picker, police capture, a declared fight) is config code, defined once and shared by the config's handlers.

## `EngineResult`

Driver advancement should return an explicit `EngineResult` rather than leaking generator mechanics to clients or transports. The result represents one of these outcomes:

- a rendered/screen-ready interaction state waiting for a response;
- a committed set of events/effects plus the next state;
- a transition into or out of a sub-FSM such as combat;
- turn end, player advance, win/loss, or fatal protocol error.

`EngineResult` is the stable boundary between simulation and adapters: terminal clients, tests, and WebSocket transport all consume the same result shape.

## Events vs effects

Interactions control **execution flow**. Effects mutate **game state**.

Handlers must not mutate `GameState` directly. They call `ctx.apply(effect)`.

An effect is a frozen dataclass with an `apply(state) -> state` method, registered under a tag by a decorator. Its fields are pure data, so it still serializes as a replay event. `commit()` folds `effect.apply` over the buffer. It dispatches through the effect itself, never by type lookup, and it names no effect class.

There are two kinds of effect:

- **Generic engine effects** live in `engine/effects.py`. They change what the engine itself owns: cash, score, movement points, position, entry context, the roster, a combatant's attributes, vitality and equipment, combat fighters, and the turn. Examples, by tag: `MoneyChange`, `ScoreChange`, `MsChange`, `SetPosition`, `Teleport`, `StatChange`, `EnergyChange`, `SpawnFighter`, `RosterAppend`. An effect's tag is its class name; it is what a save writes.
- **Game effects** live in the config (`data/game_configs/<game>/effects.py`). They change the config's declared state: debt, jobs, jail, tips, tenancy, contraband, the hired-candidate set. The config registers them when its package is imported, the same way `@register` fills the handler registry.

A game effect's `apply` composes the engine's public state-update helpers (for example, set one player's value). It never rebuilds the state graph by hand. The helpers are part of the handler API.

The registry must survive a config reload, because loading a config re-executes its package. So:

- the registry is returned on the loaded config, like `handlers`, and passed to save loading and replay;
- re-registering a tag replaces the old entry;
- `commit()` never looks an effect up by type.

Registration is the only list a new effect joins. There is no apply chain, export list, nested-field table or consequence table to keep in step with it.

The rejected alternative is effects as bare data with a tag-to-reducer registry. It needs two artifacts per effect that can drift apart, and a registry keyed by type breaks on reload.

Effects are also the replay event vocabulary. Every state change is pure data and versioned. `engine/` names no game effect: see [Engine/config seam](#engineconfig-seam) for which effect lives where.

## Commit pipeline

Effects produced during a handler action are buffered, validated, and then committed atomically as events. The pipeline is:

1. handler calls `ctx.apply(effect)`;
2. action-local buffer records the effect;
3. driver validates the effect against state and schema;
4. cancellation or failure discards the buffer;
5. successful action commits buffered effects as ordered events;
6. each effect's `apply` builds the new `GameState` (pure; the input state is never mutated);
7. committed events are available for replay/logging.

This keeps cancelable prompts atomic and makes tests assert both emitted interactions and committed events.

## Handler driver

The driver owns:

- starting handlers by id;
- advancing suspended generators;
- converting interactions to screen states;
- validating client responses;
- sending responses back to handlers;
- running nested sub-FSMs such as combat or minigames;
- applying the commit pipeline;
- surfacing `EngineResult` to callers.

Handlers remain deterministic functions of `(ctx.state, ctx.rng, Responses)`.

## Cancellation decision

The original returns to the menu on `0` input at multiple prompt sites and aborts some combat-step choices. The protocol must support “abort this action cleanly, commit no partial effects, return to menu.”

The chosen design is **exception-driven cancellation**: the driver throws a `Cancelled` exception into the generator and discards the action-local effect buffer. Handlers may use `finally` for local cleanup, but do not manually check cancel sentinels after every prompt. Cancellation is atomic at the action boundary.

## Save/replay at interaction boundaries

The original has no save game; this engine adds one. Replay is defined as:

```text
initial seed + setup + ordered event log = game
```

Rules:

1. All nondeterminism flows through `ctx.rng`.
2. RNG draws are logged events, so replay does not re-roll.
3. Events are pure data and applying an event is a pure function.
4. A snapshot plus remaining event log can restore exact `GameState`.
5. Saves occur at interaction boundaries, where the active FSM state and suspended handler position are explicit.

The store is append-only JSONL events plus per-turn snapshots. The schema and determinism contract exist from the beginning, even if the physical store is implemented later.

Saves and replay need the loaded config, because the engine cannot name what the config declares:

- `load_game` and `replay` take the loaded config's registries. They rebuild each effect by its tag through the effect registry, and each value map through the declared state schema. No hand-kept class list or nested-field table remains.
- A combat recording keeps its `rules=` signature. It holds only combat scenarios and RNG draws, never effects or player state.
- The save header records the config's id and a config-owned content version. A save loaded under a different config or content version is refused with one line.
- On load, the state schema fills the declared default for a missing key and refuses an unknown key. So a later change can add a key without a version bump.

Saves and recordings share one `SCHEMA_VERSION`. The seam takes one bump, from 1 to 2, after the seam lands and before new content. `ENGINE_API` and the config's `engine_api` go to 2 in the same step. A version mismatch on load is a one-line client message, and the legacy field backfill (`LEGACY_FIELD_DEFAULTS`) goes away. Fields added after that bump ride on version 2: no version-2 artifact exists outside the branch before they land. The house-rules map is the exception: a save without it is refused, never default-filled.

## RNG/determinism

Use a seedable, loggable RNG from day one:

- `rng.hit(a, b)` for probability checks;
- `rng.range(n)` for integer ranges;
- every RNG call can be recorded.

Behavioral fidelity means matching probabilities and formulas, not the original C64 draw order. No handler may use wall-clock time, OS randomness, or ambient nondeterminism.

## GameState overview

`GameState` is modular. The engine's dataclasses hold only what the engine operates on. Game state lives in declared value maps:

```text
GameState
├── players: tuple[Player]
│   ├── name, cash, score, rank, map position, vehicle, movement points
│   ├── entry context (location id, tile ln), previous tile
│   ├── roster: tuple[Combatant]  # engine blueprint; the game supplies the
│   │                             # concrete type — `Gangster` in mafia_1920s
│   └── values: game state, declared by the config (debt, job, jail, tips, ...)
├── values: global game state, declared by the config (tenancy, hired candidates, ...)
├── combat: both sides' fighters, 40x13 grid, direction memory, cursors, result flag
├── clock: year, month, end year, active player, player count, turn phase
└── config: formula params, house rules (both opaque config data)
```

Per-player game state is a frozen, namespaced value map on `Player`. Game state with no player dimension is the same kind of map on `GameState`. The config declares the schema of both: names, types, defaults. This is how gangster stats already live in `Combatant.attrs`. Saved state never holds a config class, so a reload that changes a class's identity cannot break a save or an equality check.

The maps go through the same freeze coercion as every other collection, and the purity harness's type fingerprint (`run_pure`'s `_shape()`) walks into them. The config wraps them in typed accessors, so handler code stays readable.

The rejected alternative is config state classes that the engine rebuilds through a registry. Saved state would hold classes that change identity on every reload, and the engine would reach a config type across the layer line (`docs/solutions/architecture-patterns/subclass-across-a-layer-boundary-needs-cross-class-eq.md`).

Unpack the original packed strings into plain fields or value-map keys, but preserve documented formulas exactly. Rename original variable collisions where needed, such as player debt versus gangster `kraft`.

## Engine/config seam

`engine/` names no game stat, game effect, or game state entity. A new title copies the config and leaves `engine/` untouched. This section says where each thing lives.

### The rule

The engine owns mechanism: sequencing, geometry, applying, clamping, and termination. For everything else:

- A fact that varies per entity is an **attribute**. It lives on the entity: in `attrs`, in a value map, or in entity data.
- A fact that is uniform across entities is a **game formula**. It belongs to the config: a hook, a formula parameter, or a config effect.
- A fact that is true of any game in the genre is **engine mechanism**. It stays in `engine/`.

Two tie-breakers follow from the rule:

- **Engine offers, game opts in.** The engine supplies a clamp; the config supplies its bounds. The only bound the engine imposes on its own is `vitality >= 0`.
- **One fact, one source.** When the engine holds a copy of something the config already owns, the copy is deleted, not reconciled (`docs/solutions/architecture-patterns/two-sources-for-one-fact-remove-dont-reconcile.md`).

A name grep is not enough to find a leak. `weapon < 4` hardcodes a weapon taxonomy without naming a stat, and a BASIC variable name (`ka`, `gf`, `tr`) is game vocabulary even on a genre-level field.

### Verdicts

The tables below state the target. Until the seam refactor lands, the rows marked as moving still describe code in `engine/`. The verdicts are:

- **keep**: stays in the engine, unchanged in meaning.
- **move**: becomes a config effect, registered by the config.
- **value map**: becomes a declared key in a per-player or global value map.
- **hook**: becomes a config handler under a fixed key that the engine calls.
- **config data**: becomes data the config declares and validates.
- **delete**: goes away with no replacement.

#### Effects (`engine/effects.py`)

| Class | Verdict | Rule |
|---|---|---|
| `MoneyChange` | keep | Cash is a genre-level player field; adding to it is mechanism. |
| `ScoreChange` | keep | Score is genre-level. Its `[0, 100]` clamp is a uniform bound from `:1160-1161`, so the bound becomes caller-supplied, like `StatChangeCapped`'s cap. |
| `MsChange` | keep | Movement points are the engine's turn economy; `ms=0` ends a turn. |
| `SetPosition` | keep | Map position is geometry. |
| `SetEntryContext` | keep | The entry context (`la`, `ln`) is location-entry sequencing, written by the engine's door entry. |
| `Teleport` | keep | Forced relocation is geometry. |
| `StatChange` | keep | It writes `attrs[name]` with the name as data. Validation reads the config's declared stat names instead of `_STAT_NAMES`. |
| `StatChangeCapped` | keep | As `StatChange`; the cap is already caller-supplied. |
| `AssignWeapon` | keep | It sets the blueprint's opaque equipment id. |
| `ScoreAndRank` | move | It ports `:1160`/`:1165`: the score weight and the rank divisor are a uniform game formula. |
| `FlagSet` | keep | Setting a named global value is mechanism. It is retargeted from `Flags` fields to the declared global value map. |
| `SetTenancy` | move | Tenancy by `ln` is this game's rent rule (`:10040`). |
| `RentAccrue` | move | Prepaid rent months are game state. |
| `EnergyChange` | keep | It changes the blueprint's `vitality` slot and clamps to a caller-supplied cap. Combat writes energy back through it. |
| `RankCommit` | move | It commits this game's two-step rank (`nr` to `ra`, `:4030`). Clients read the rank through state instead of matching the effect. |
| `WantedChange` | delete | No BASIC variable backs it, and nothing applies it. |
| `Jail` | move | Jail months are game state. It becomes a config effect that sets the months. |
| `SpawnFighter` | keep | Placing a fighter on a combat side is combat mechanism. |
| `DebtChange` | move | The loan-shark balance and grace counter are game state. |
| `DebtClear` | move | As `DebtChange`. |
| `ShopChange` | move | Shop ownership and capital are game state. |
| `BarrelChange` | move | Alcohol stock is game state. |
| `TipSet` | move | The heist tip is game state. |
| `TipClear` | move | As `TipSet`. |
| `JobSet` | move | Jobs are game state. |
| `JobClear` | move | As `JobSet`. |
| `RosterAppend` | keep | The roster is genre-level; appending is mechanism. |
| `RosterTruncate` | keep | As `RosterAppend`. |
| `GangsterMarkHired` | move | The hired-candidate set is game state with no player dimension. |
| `CommitResult` | keep | Not an effect: the result of `commit()`, the engine's commit mechanism. |

Supporting names in the same module:

| Name | Verdict | Rule |
|---|---|---|
| `_STAT_NAMES` | delete | A list of this game's stats. Validation reads the config's declared stat names. |
| `LEGACY_FIELD_DEFAULTS` | delete | Removed with the version bump; a mismatched save is refused instead. |
| `_apply` isinstance chain | delete | Each effect carries its own `apply`; `commit()` folds it. |
| `SCHEMA_VERSION` | keep | The engine's save and recording format version. |

#### State (`engine/state/__init__.py`)

| Entity | Verdict | Rule |
|---|---|---|
| `Combatant` | keep | The engine's roster blueprint: identity, equipment id, `vitality`, opaque `attrs`. |
| `Fighter` | keep | The on-grid blueprint. It gains an `owner` field (the owning player's index, or none for NPCs), so combat writes energy back to each side's owner. |
| `CombatState` | keep | Combat mechanism: sides, grid, cursors, losses, result flag. `dir_memory` stays; the shared-memory quirk gets a neutral mechanism setting that the config sets. |
| `Clock` | keep | Turn sequencing and termination. See the field table. |
| `Player` | keep, split | Genre-level fields stay; game fields become value-map keys. See the field table. |
| `GameState` | keep | The aggregate. It gains the global value map. |
| `Job` | value map | This game's job (`jo`, `jl`, `jd`). |
| `Debt` | value map | This game's loan-shark debt (`kr`, `kz`). |
| `Business` | value map | This game's shop (`kg`, `kk`). |
| `Contraband` | value map | This game's papers, counterfeit money and barrels. |
| `Wanted` | value map | Jail months, chief-bribe months and the two win flags `x5`/`x6` are this game's. |
| `MapState` | delete | `grid` and `special_cells` are never filled: the city map is config data that the movement code loads, so the state copies are a second source. `tenancy` moves to the global value map. |
| `Config` | keep, split | See the field table. |
| `Flags` | delete | `graphics_mode` and `loaded` are never read. `hired_gangsters` moves to the global value map, which replaces the class. |

Fields of the mixed classes:

| Field | Verdict | Rule |
|---|---|---|
| `Player.name` | keep | Identity label. |
| `Player.gang_name` | value map | A game label the engine never reads. |
| `Player.ka` | keep | Cash is genre-level. |
| `Player.gf` | keep | Score is genre-level. Its bounds and truncation are config rules. |
| `Player.rank` | keep | Rank is genre-level. |
| `Player.nr` | value map | The pending rank of this game's two-step rank formula (`:1165`). |
| `Player.po` | keep | Map position is geometry. |
| `Player.vehicle` | keep | Genre-level. What it grants (`tr`) is vehicle data the config reads. |
| `Player.speed` | delete | Never read or written. |
| `Player.ms` | keep | The movement economy. |
| `Player.roster` | keep | Genre-level; `roster[0]` is the player's own combatant. |
| `Player.jobs`, `.debt`, `.business`, `.contraband`, `.wanted` | value map | As their classes above. |
| `Player.tip_target` | value map | This game's tip (`tp`). |
| `Player.safe_skill` | value map | Game state; unread today. |
| `Player.last_location`, `.last_la` | keep | Location-entry sequencing (`ln`, `la`), written by door entry. |
| `Player.previous_tile` | keep | `ll(sp)`, the `(la, ln)` of the last visit this turn: map sequencing, written by the runner after each visit (`:2055`) and cleared at the turn start (`:1012`). |
| previous tile (`ll`, new) | keep | The runner writes it after each location handler and clears it at turn start: map sequencing. It is saved. |
| `Player.rented_months` | value map | This game's prepaid rent (`um`). |
| `Clock.year`, `.month` | keep | The calendar the runner advances on each wrap. The start year is setup data. |
| `Clock.end_year` | keep | Termination bound. The config sets its value at setup. |
| `Clock.active_player`, `.player_count` | keep | Turn sequencing. |
| turn phase (new) | keep | Where a resumed save re-enters the runner. |
| `Config.score_mult` | config data | The score weight `x8` is a game formula parameter; it joins `formula_params`. |
| `Config.action_costs` | delete | The config never fills it; costs live in `formula_params`. |
| `Config.formula_params` | keep | Opaque config data the engine never inspects. |
| house rules (new) | keep | A frozen map the config fills; no effect may write it. |

#### Guard variables (`engine/conditions.py`)

The engine keeps the guard DSL and a registry of variable resolvers. The config registers the variable names its shells use. The engine resolves none by name.

| Variable | Verdict | Rule |
|---|---|---|
| `rank` | config-registered | The name is this game's guard vocabulary. |
| `ka` | config-registered | A BASIC name for cash. |
| `gf` | config-registered | A BASIC name for score. |
| `tenancy` | config-registered | It reads game state (the tenancy map) by `ln`. |
| `ms`, `po`, `sp` | config-registered | BASIC names for genre-level values; the config registers them over engine fields. |
| `gang_size` | config-registered | A name the config chooses over the roster length. |

#### The runner's own writes (`engine/movement.py`)

| Name | Verdict | Rule |
|---|---|---|
| `advance_turn` | keep, as effects | Advancing the active player and the calendar is sequencing. The runner commits it as generic engine effects. The movement-point refill reads the vehicle's `tr`, a game field, so the `:1012` rule is a hook that returns the value. |
| `start_free_turn` | hook | `:1013` score truncation is a uniform game formula. It becomes the config's hook, built on the engine's C64 float helper. |
| year-end check | keep | Termination: `year >= end_year`, with `end_year` set by the config. |
| `police_interrupt_would_fire` | hook | The roadblock gate (`:2041`, rank above 3) is a game rule. It is the config's roadblock hook (`turn.roadblock`), asked after each street step; the gate is config code. |

#### Entity contracts (`engine/types/__init__.py`)

| Name | Verdict | Rule |
|---|---|---|
| `WeaponInstance.req_int`, `.req_kraft`, `.req_brut` | config data | Per-weapon stat minimums name this game's stats. They become a config-declared requirement map, read by the weapon shop handler. A requirement naming an undeclared stat is refused at config load. |

## Layering rules

The `engine/` package is headless and pure simulation:

- `engine/` imports nothing from `server/`, `clients/`, transport libraries, render libraries, or `data/`.
- Configs are loaded by path at runtime.
- Presentation and transport depend on the engine, never the reverse.
- The server is an authoritative thin async adapter over the interaction protocol.
- Clients are thin render/input adapters with no game logic or local simulation state. They render the turn runner's interactions; they do not own the order of a turn.
- `engine/` names no game stat, game effect, game state entity, or house-rule id. It applies config effects and state through the registries the loaded config returns ([Engine/config seam](#engineconfig-seam)).

## Build order

1. **Phase 0 — Core + contracts:** state dataclasses, RNG, config loader, interaction protocol, driver, effect API, cancellation, handler API, `engine_api`, event schema.
2. **Phase 1 — Playable slice:** map movement, `ms`, location entry, guards, strings, `slw`, and one guarded `pub` neighbor.
3. **Phase 2 — Hard protocol cases:** casino gambling and weapon shop buy/train flows.
4. **Phase 3 — Combat + `StartCombat`:** original AI/damage/energy, bank, police/jail loop.
5. **Phase 4 — Content complete:** remaining locations and map events.
6. **Phase 5 — Event sourcing/snapshots/replay tests.**
7. **Phase 6 — WebSocket server.**
8. **Phase 7 — Pygame client and fuller theme/string externalization.**
9. **Phase 8 — Codegen only if profiling demands, plus second config to validate moddability.**

The engine/config seam is not left to Phase 8. It lands before Phase 4 content, because each new location would otherwise add game effects and state to `engine/` (`product-and-scope.md`, "What should and should not be generalized").

## Testing and verification

- Formula unit tests assert research-documented formulas and value ranges.
- Interaction-driver tests feed scripted responses and assert interaction sequences and state/events.
- Vertical-slice integration tests cover a seeded run through location, crime, wanted, arrest, and jail flows.
- Manual terminal runs confirm complete playable loops.
- Replay tests re-run logged event and RNG streams and assert identical final state.
- Guard DSL tests verify every menu guard is expressible within the configured DSL limits.

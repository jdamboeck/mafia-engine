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
                       ├── Overview        read-only screen
                       ├── Location  ──▶ handler generator
                       ├── Combat    ──▶ combat sub-FSM
                       └── NextPlayer ──▶ advance player and turn upkeep
```

Nested lifecycles are driven by the level above:

```text
Game FSM
└─ Player turn            movement economy; ends on ms <= 0 or handler-forced
   └─ Location visit / Combat entry
      └─ Handler generator
         └─ Interaction       one prompt/message/combat request ↔ one screen state
```

The Game FSM state, active turn, and any suspended handler state are part of `GameState` so saves and replay can reconstruct where execution stopped.

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
| `StartCombat` | `fighters`, `arena` | `CombatResult` | Suspends turn and runs combat sub-FSM. |
| `LoadSubState` | `kind`, `params` | sub-state result | Nested minigames such as safe-cracking. |

Shared BASIC subroutines become engine helpers/interactions: wait, yes/no, not-enough-money, gangster picker, score update, combat entry, and similar common routines.

## `EngineResult`

Driver advancement should return an explicit `EngineResult` rather than leaking generator mechanics to clients or transports. The result represents one of these outcomes:

- a rendered/screen-ready interaction state waiting for a response;
- a committed set of events/effects plus the next state;
- a transition into or out of a sub-FSM such as combat;
- turn end, player advance, win/loss, or fatal protocol error.

`EngineResult` is the stable boundary between simulation and adapters: terminal clients, tests, and WebSocket transport all consume the same result shape.

## Events vs effects

Interactions control **execution flow**. Effects mutate **game state**.

Handlers must not mutate `GameState` directly. They call `ctx.apply(effect)` using pure-data effects such as:

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

Effects are also the replay event vocabulary. Every state change is pure data and versioned.

## Commit pipeline

Effects produced during a handler action are buffered, validated, and then committed atomically as events. The pipeline is:

1. handler calls `ctx.apply(effect)`;
2. action-local buffer records the effect;
3. driver validates the effect against state and schema;
4. cancellation or failure discards the buffer;
5. successful action commits buffered effects as ordered events;
6. event application mutates `GameState` through pure reducer logic;
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

## RNG/determinism

Use a seedable, loggable RNG from day one:

- `rng.hit(a, b)` for probability checks;
- `rng.range(n)` for integer ranges;
- every RNG call can be recorded.

Behavioral fidelity means matching probabilities and formulas, not the original C64 draw order. No handler may use wall-clock time, OS randomness, or ambient nondeterminism.

## GameState overview

`GameState` is modular; each subsystem owns its data:

```text
GameState
├── players: list[Player]
│   ├── cash, score, rank, map position, vehicle, speed, movement points
│   ├── roster: list[Gangster]
│   ├── jobs, debt, business, contraband, wanted, tips, safe skill
├── map: grid, tenancy, special cells
├── combat: enemy roster, 40x13 grid, direction memory, result flag
├── clock: year, end year, active player, player count
├── config: score multiplier, action costs, formula params
└── flags: global flags distinct from per-player bitfields
```

Unpack the original packed strings into plain fields, but preserve documented formulas exactly. Rename original variable collisions where needed, such as player debt versus gangster `kraft`.

## Layering rules

The `engine/` package is headless and pure simulation:

- `engine/` imports nothing from `server/`, `clients/`, transport libraries, render libraries, or `data/`.
- Configs are loaded by path at runtime.
- Presentation and transport depend on the engine, never the reverse.
- The server is an authoritative thin async adapter over the interaction protocol.
- Clients are thin render/input adapters with no game logic or local simulation state.

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

## Testing and verification

- Formula unit tests assert research-documented formulas and value ranges.
- Interaction-driver tests feed scripted responses and assert interaction sequences and state/events.
- Vertical-slice integration tests cover a seeded run through location, crime, wanted, arrest, and jail flows.
- Manual terminal runs confirm complete playable loops.
- Replay tests re-run logged event and RNG streams and assert identical final state.
- Guard DSL tests verify every menu guard is expressible within the configured DSL limits.

# Vertical Slice Review & Architectural Analysis

This document provides an exhaustive, code-level review and architectural critique of the first vertical-slice implementation of the **Mafia Engine**. It analyzes core architectural decisions, evaluates the strengths and weaknesses of the current state, identifies critical optimizations and missing features, reviews the testing infrastructure, and proposes a structured blueprint for next-step planning.

---

## 1. Executive Summary & Vertical-Slice Overview

The current vertical slice is an in-progress, headless implementation representing the core "spine" of the Mafia strategy game. It successfully demonstrates an end-to-end traversal of:
1. **Seeded game initialization** (setting up players, gangs, and starting attributes).
2. **Pure, deterministic map movement** on a row-major 40×25 grid (`engine/movement.py`).
3. **Location entry context tracking** using doors and entry-point semantics.
4. **Guard-gated declarative location shells** (`engine/locations.py` and YAML files).
5. **Interactive coroutine-based procedural location handlers** (`data/game_configs/mafia_1920s/handlers/`).
6. **Transactional action/commit pipelines** which partition state mutation (effects) from user interaction (interactions).
7. **Tactical combat mechanics** featuring sequencers, grid calculations, AI, and per-side controllers.

### Trajectory Trace
The vertical slice integration test (`tests/test_slice_integration.py`) validates a full play-through trajectory:
* A player starts with an initial layout (seed 42).
* The player walks DOWN and LEFT on the city grid, spending movement points (MS), and hits coordinate `180`, which triggers entry into the Schlupfwinkel (motel/motel lobby, `slw`, `ln=2`).
* An entry effect (`SetEntryContext`) is committed, recording `last_location = 2` on the player.
* The player initiates the `rent` option. The declarative shell evaluates the guard (the room must be unoccupied: `uk(2) == 0`).
* Since the guard passes, the handler `slw_rent` executes:
  - It yields `ShowMessage` with a rent quote.
  - It yields `PromptInt` requesting the number of months.
  - The driver feeds the inputs and receives the response (e.g., 2 months).
  - The handler calculates the rent (2 × 50 = 100) and applies effects: `MoneyChange(-100)`, `SetTenancy(ln=2)`, and `RentAccrue(2)`.
  - The effects are committed atomically to the state.
* The trajectory goes on to showcase:
  - **Premium rent mechanics** on tile `ln=1` (costing 300 for 2 months, proving no hardcoded prices).
  - **Guard exclusions**: Trying to rent an occupied room, or trying to pay rent for a room where you aren't the resident, is blocked by the declarative shell guard, yielding `OptionDenied` without ever entering the python handler.
  - **Quiet cancels**: Prompting for 0 months cleanly returns without committing any effects.
  - **Rank-gated denials**: Walking to the pub and attempting to recruit fails at rank 1, proving that complex conditions are handled appropriately.

---

## 2. In-Depth Review of Architectural Decisions

The engine architecture is built upon several key, highly deliberate design decisions that shape its extensibility, portability, and safety.

### 2.1 The Two-Layer Spine (Declarative Shell vs. Procedural Handlers)
A central decision is the division of location interactions into two layers:
1. **Declarative Shell (YAML)**: This layer defines the menu structure, guards, and optional simple consequences. It is pure, easily modifiable data.
2. **Procedural Handlers (Python generator coroutines)**: Because location logic can include complex interactive loops, RNG checks, and sub-games, flat data is insufficient. Writing handlers as generators allows procedural, sequential-looking code to run in a non-blocking, yield-based environment.

#### Assessment:
This is an exceptional design pattern. By yielding typed `Interaction` objects, the handler suspends itself, and the *driver* takes responsibility for turning the interaction into a client-facing screen, gathering inputs, and resuming the generator with `.send()`. This isolates the handler from rendering details, terminal APIs, and network transport boundaries.

### 2.2 Interactions vs. Effects (Control vs. State Mutation)
The codebase enforces a strict separation between how execution flow is controlled and how state is changed:
* **Interactions**: Yielded by handlers to block and prompt the user (e.g., `PromptInt`, `Confirm`). They represent suspend-points.
* **Effects**: Dispatched by handlers via `ctx.apply(effect)`. These are pure-data records indicating intent to change state.

#### Assessment:
By ensuring handlers *never* mutate `GameState` directly and instead buffer effects, the engine achieves absolute **transactional safety**. If a player cancels midway through a multi-prompt interaction (such as bailing out of a room rental or casino wager), the driver throws a `Cancelled` exception into the coroutine generator, the local effect buffer is discarded, and the game state remains completely untouched. This solves the "partial mutation" bug category permanently.

### 2.3 Headless Pure Simulation & Layering Rules
The `engine/` package holds zero dependencies on presentation (`clients/`) or transport/server libraries. It communicates entirely through serializable `EngineResult` payloads and `Interaction` schemas.

#### Assessment:
This design rule is aggressively and successfully enforced. It is proven by the headless integration tests which execute full trajectories without importing terminal graphics or raw rendering pipelines. The `clients/terminal/` package is a pure visual adapter consuming the output of this engine. This guarantees the engine is network-ready; a WebSocket server can easily drive the same generators across a TCP connection.

### 2.4 The Combat Blueprint (Combatant vs. Gangster)
An architectural decision was made to make combat attribute-agnostic.
* **`Combatant` (Engine-level)**: Defines the absolute structural minimum needed for grid coordinates, activation sequence, and life tracking (`position`, `down`, `vitality`, `attrs`, `identity`, `equipment`).
* **`Gangster` (Game-level)**: The concrete entity defined by the game config. Its stats (e.g., `kraft`, `brutalitaet`, `intelligenz`) are packed as opaque metadata inside the `attrs` map of the `Combatant` blueprint.

#### Assessment:
This allows the core tactical grid and activation loop to remain entirely in the engine package without hardcoding game-specific terminology. Formulas (like hit chance or damage) are supplied by the game config inside a `RulesBundle`, allowing complete gameplay moddability.

---

## 3. Strengths and Weaknesses Analysis

### 3.1 Structural Strengths
1. **Deterministic Replay Guarantee**: Because all randomness is funnelled through a seedable, recordable `Rng` wrapper and all state transitions occur via pure-data effects, any game session can be perfectly reconstructed by replaying the seed + input sequence. This is incredibly valuable for debugging, telemetry, and automated regression testing.
2. **Declarative Guard Evaluation**: Validating YAML guards at load time and using an expressive but limited DSL (`engine/conditions.py`) prevents complex, bug-ridden logic from creeping into declarative data files.
3. **Rigorous Layer Separation**: No component in `engine/` can import from `clients/`, `server/`, or `data/` directly. This keeps the codebase modular and prevents cyclic dependency spaghetti.

### 3.2 Technical Debt & Weaknesses
1. **Effect Vocabulary Pollution**:
   - Despite the core architecture striving to be a generic *genre engine*, `engine/effects.py` is saturated with 1920s Mafia domain concepts. Effects like `RentAccrue`, `BarrelChange`, `TipSet`/`TipClear`, `DebtChange`/`DebtClear`, `JobSet`/`JobClear`, `GangsterMarkHired`, `Jail`, `WantedChange`, and `ShopChange` are hardcoded in the engine.
   - For a truly generic turn-based board engine, these domain-specific effects should either be replaced by generic primitives (such as `ModifyValue(path, delta)`) or the engine should support registering custom effect handlers dynamically from the game config.
2. **Game State Graph Pollution**:
   - `GameState` carries variables that the engine never reads or reasons about. Systems like `Wanted`, `MapState.tenancy`, `Business`, `Contraband`, `Debt`, and `Job` are carried inside the core engine's state records, even though they are only accessed and modified by config-owned handlers or domain-specific effects.
   - This represents game-specific data structures hardcoded directly inside the engine's core graph.
3. **Whole-Graph Eager Copying (`copy.deepcopy`)**:
   - `engine/effects.py`'s `commit()` function executes a full `copy.deepcopy` of the `GameState` on every transaction.
   - While correct and safe, this is a heavy performance bottleneck for high-frequency or large-scale state operations. As maps expand and roster sizes increase, eager whole-graph deep copying will degrade performance.
4. **Hardcoded Gated Gaps in `WeaponInstance`**:
   - `WeaponInstance` in `engine/types/__init__.py` enforces the existence of `req_int`, `req_kraft`, and `req_brut`, forcing any weapon-bearing mod to adopt the exact stats of the 1920s Mafia game. This is a subtle coupling leak that contradicts the attribute-agnostic design.

---

## 4. Optimizations & Missing Elements

To elevate the current implementation to a highly optimized, fully featured production state, several key components must be addressed.

### 4.1 Recommended Optimizations
1. **Structural Sharing via Copy-on-Write**:
   - Replace the eager `copy.deepcopy()` in `commit()` with shallow copying of updated sub-trees (structural sharing). Since `GameState` and its sub-records are frozen, we can safely share unmodified sub-branches (e.g., players, map cells, clock) between states rather than copying the entire graph.
2. **Eager Guard Caching / Memoization**:
   - For a large map with multiple players, evaluating option guards for every menu rendering can become expensive. Caching the results of available options per cell coordinate and only clearing the cache when an effect modifies player attributes or map tenancy will save CPU cycles.
3. **Incremental Terminal Rendering**:
   - The terminal client currently draws frames of the 40×25 map and combat grid by printing complete line buffers. This can cause screen flickering.
   - Optimizing `clients/terminal/renderers.py` to use ANSI cursor positioning codes (`\033[H`, `\033[y;xH`) to only overwrite changed cells (like the player's moving avatar `@` or project lines) would create buttery-smooth terminal rendering.

### 4.2 Missing Elements of a Production-Ready Slice
1. **Physical Save/Load Persistence**:
   - Although `engine/persistence.py` exists to encode/decode state graphs to/from JSON, a physical, production-ready save-file folder manager (with auto-saves, slot lists, and replay event-store logging) is missing.
2. **Upkeep Phase Visual Loop**:
   - The monthly upkeep flow (regenerating gangster energy, promotions, debt checks, paying wages, shop income) runs headlessly in the backend but is not fully integrated into a player-facing interactive visual loop in the CLI client.
3. **Network Transport Adapter (WebSockets)**:
   - A fully functional horizontal slice requires demonstrating remote playability. The WebSocket server and async transport layer are currently deferred.
4. **Special Map Cell Event Flow implementation**:
   - The map triggers for Cash Transport (`la=13` on cell 569) and Mayor Hit (`la=14` on cell 861) are not fully realized in the movement pipeline; they are currently bypassed as `not_implemented` placeholders.

---

## 5. Review of Testing and Testing Infrastructure

The testing suite in this repository is exceptionally rigorous, representing best-in-class engineering practices for strategy game development.

### 5.1 Test Purity Harness (`run_pure`)
The centerpiece of the helper test suite is `run_pure` in `tests/helpers.py`:
* It captures a pre-run snapshot of the `GameState` values and structural types.
* It drives the handler under test using `engine.interactions.run`.
* It independently reconstructs a clean state from the pre-run snapshot and applies the returned effects using `commit()`.
* Finally, it asserts that:
  - The input state was not mutated in place (including catching illegal direct modifications bypassed via `object.__setattr__`).
  - The resulting state is exactly equal to the independently replayed effects state, ensuring no "silent/undocumented" mutations occurred inside the driver or handler.

This is a stellar validation seam that ensures absolute replay-integrity and completely eliminates a massive category of game-state desynchronization bugs.

### 5.2 Scripted Input Source (`scripted`)
The `scripted` helper acts as a mock driver input source.
* By listening to `ShowMessage` and recording them without consuming scripted answers, it ensures tests are robust against changing flavor texts or narrations.
* It fails loudly if the handler requests more inputs than the test script provided, preventing tests from silently passing with stale or fabricated data.

### 5.3 Test Failure Risks: Color Support Sensitivity
The ANSI generation suite (`tests/test_terminal_palette.py`, `tests/test_terminal_renderers.py`, etc.) features tests that assert on explicit truecolor sequences (like `\033[38;2;...`).
* **The Sensitivity**: `ColorSupport` capability detection reads the host environment's `COLORTERM` and `TERM` variables. If run on a terminal or CI environment where `COLORTERM` is not set to `truecolor` or `24bit`, the system falls back to 256 colors or 8-color mode.
* This makes the tests fail immediately, as the emitted escape codes switch from truecolor (`\033[38;2;...`) to 256-color modes (`\033[38;5;...`).
* **The Solution**: Tests that verify ANSI codes should explicitly mock `term_color_support` or override the `support` parameter in `fg()` and `bg()` calls to force `ColorSupport.TRUECOLOR`, decoupling test results from the host machine's ambient environment settings.

---

## 6. Brainstorm for Next-Step Plan (Blueprint for Phase 2 & Beyond)

On top of the current vertical slice, subsequent implementation phases must focus on extending the engine’s capability and solidifying its role as a reusable *genre engine*.

```
   [Phase 2: Protocol Stress & Content]
     - Integrate SPH (Casino) and WAF (Weapons)
     - Validate complex interaction patterns
                     │
                     ▼
   [Phase 3: Core Strategy FSM & Upkeep]
     - Upkeep loop visualization
     - Roadblocks and police arrest / jail loop
                     │
                     ▼
   [Phase 4: Save, Replay & Telemetry]
     - Append-only JSONL event logger
     - Physical slot-save manager
                     │
                     ▼
   [Phase 5: WebSocket Server Transport]
     - Async driver adapter
     - Remote client-server protocol
```

### 6.1 Phase 2: Protocol Stress & Content (SPH & WAF)
* **Goal**: Fully integrate the `sph` (casino gambling) and `w_a_f` (weapon buying and training) locations.
* **Why**: These locations represent complex, multi-prompt interaction loops (wagering, inventory lookups, weapon attribute gating). They will stress-test the driver’s prompt-cancellability and error recovery in highly dynamic configurations.
* **Key Tasks**:
  1. Build the declarative shells for `sph.yaml` and `waf.yaml`.
  2. Implement `sph.wager` and `waf.buy`/`waf.train` generators in the config package.
  3. Validate weapon buying/training mechanics using the attribute-agnostic lookup schemas.

### 6.2 Phase 3: Core Strategy FSM, Upkeep, and Police Loops
* **Goal**: Establish the complete macro-level game loop including turn transitions, upkeep calculations, and roadblocks.
* **Key Tasks**:
  1. Implement Upkeep sequence handlers (wage pay, regeneration, promotions, debt check, default collectors fight).
  2. Implement Roadblock police interrupts. Integrate the roadblock encounter flow (arrest, bribe, escape, jail).
  3. Integrate Upkeep and Roadblocks into the synchronous turn execution pipeline.

### 6.3 Phase 4: Save/Replay, Snapshotting, and Telemetry
* **Goal**: Build an append-only JSONL event-log file writer and slots manager.
* **Key Tasks**:
  1. Implement a slot-save file writer that serializes the seed, clock, and the accumulated committed effects log.
  2. Create a replay engine that reconstructs exact game states by running the initial seed and folding the effect log.
  3. Implement snapshot caches to speed up seeking / backward stepping in replay streams.

### 6.4 Phase 5: WebSocket Server & Remote Transport Layer
* **Goal**: Transition from an in-process synchronous loop to a client-server network model.
* **Key Tasks**:
  1. Wrap `engine.interactions.run` in an async task driver.
  2. Implement a thin, async WebSocket adapter that translates yielded `Interaction` dataclasses to JSON and awaits WS responses.
  3. Create a lightweight Python-based client client-connector to drive remote playability.

---

## 7. Operational Guidelines for Future Agents

* **The Green-Tree Rule**: Never commit on a red tree. Always run the tests with `COLORTERM=truecolor pytest` or `make check` before declaring any work complete.
* **No Inline State Mutation**: Handlers must remain pure and deterministic. Always use `ctx.apply(effect)` for state changes.
* **Honor the Seams**: Avoid hardcoding stats, labels, or content paths in the core `engine/` directory. Use the `RulesBundle` and custom configurations.
* **Characterization-First Testing**: When modifying legacy code, write a characterization test mapping the exact current inputs and outputs before restructuring.

---
*Document prepared by Jules, Senior Software Engineer.*

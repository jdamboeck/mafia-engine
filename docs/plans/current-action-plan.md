# Current Action Plan — State/Event Foundation

## Status

The documentation split is complete. This plan covers the remaining implementation work for the state/event foundation refactor.

## Objective

Unify runtime action results around `EngineResult`, separate semantic `events` from primitive state-mutating `effects`, make movement pure, convert handler execution to the same result model, add strict YAML consequence conversion, and centralize selected location-option execution.

## Non-goals

This plan does not implement:

- `sph` casino content.
- `waf` weapon/training content.
- Combat.
- Save-game persistence store.
- WebSocket/server transport.
- Special cell event flows.
- Content-specific domain events such as `RoomRented`.

The next implementation phase after this plan should be protocol-stress content: `sph` and `waf`.

## Architectural decisions

### Events vs effects

`EngineResult` carries both:

- `events`: semantic/audit records of what happened or what was attempted.
- `effects`: primitive committed state mutations.

Events may exist without effects. For example, a blocked move or denied option emits an event but has no state mutation.

Effects are the only things applied by `commit()`.

### Error semantics

Programmer, config, and unexpected handler errors should raise exceptions.

`EngineResult.error` and `status="error"` exist for future recoverable runtime errors, but this plan should not convert programmer/config bugs into error results.

### Handler atomicity

Handlers buffer both events and effects.

On clean completion:

- buffered events are returned in `EngineResult.events`;
- buffered effects are committed and returned in `EngineResult.effects`.

On cancellation:

- buffered events are discarded;
- buffered effects are discarded;
- returned state is unchanged;
- status is `"cancelled"`.

## Task 1 — Add `EngineResult`, event/effect result types, and central commit

### Target files

- `engine/actions.py`
- `engine/effects.py`
- tests as needed

### Implementation

Create `engine/actions.py`.

Add:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from engine.state import GameState

EngineStatus = Literal[
    "completed",
    "blocked",
    "cancelled",
    "turn_over",
    "needs_input",
    "started_combat",
    "not_implemented",
    "error",
]


@dataclass(frozen=True)
class EngineResult:
    state: GameState
    events: list
    effects: list
    status: EngineStatus
    payload: Any = None
    error: Exception | None = None


@dataclass(frozen=True)
class HandlerResult:
    returned: Any = None


@dataclass(frozen=True)
class DeniedResult:
    location_key: str
    option_id: str
    reason_key: str | None = None
    guard: dict | None = None
```

`DeniedResult.guard` is debug/internal data. It should not be treated as player-facing UI data.

Update `engine/effects.py`.

Add:

```python
@dataclass(frozen=True)
class CommitResult:
    state: GameState
    effects: list
```

Refactor application:

- Keep `apply(state, effect) -> GameState`.
- Add private `_apply_in_place(state, effect) -> None`.
- Add `commit(state, effects) -> CommitResult`.
- `commit()` deep-copies the input state once, applies all effects in order, and returns `CommitResult`.

### Acceptance criteria

- Existing individual `apply()` behavior remains pure.
- `commit()` applies multiple effects with a single deep copy.
- `commit()` returns committed effects in order.
- Unknown effects still raise loudly.

## Task 2 — Add primitive movement effects

### Target files

- `engine/effects.py`
- tests for effects

### Implementation

Add:

```python
@dataclass(frozen=True)
class SetPosition:
    cell: int
    player: int | None = None
```

Add:

```python
@dataclass(frozen=True)
class SetEntryContext:
    la: int
    ln: int
    player: int | None = None
```

Application rules:

- `SetPosition` sets the target player's `po`.
- `SetEntryContext` sets:
  - `player.last_location = ln`
  - `player.last_la = la`

Keep `Teleport` as a separate effect for future forced/special relocation.

### Acceptance criteria

- `SetPosition` updates returned state only.
- `SetEntryContext` sets both `last_location` and `last_la`.
- Input state is not mutated.
- Target player behavior matches existing effect targeting convention.

## Task 3 — Add semantic event catalog

### Target files

- `engine/events.py`
- tests as needed

### Implementation

Create `engine/events.py`.

Add movement events:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MoveStep:
    player: int
    from_cell: int
    to_cell: int
    delta: int


@dataclass(frozen=True)
class EnterLocation:
    player: int
    from_cell: int
    door_cell: int
    delta: int
    la: int
    ln: int


@dataclass(frozen=True)
class MoveBlocked:
    player: int
    from_cell: int
    target: int | None
    delta: int
    reason: str
```

Add option event:

```python
@dataclass(frozen=True)
class OptionDenied:
    location_key: str
    option_id: str
    reason_key: str | None = None
```

Add generic location action events:

```python
@dataclass(frozen=True)
class LocationActionCompleted:
    location_key: str
    option_id: str


@dataclass(frozen=True)
class LocationActionRejected:
    location_key: str
    option_id: str
    reason_key: str | None = None


@dataclass(frozen=True)
class LocationActionCancelled:
    location_key: str
    option_id: str
```

Do not add content-specific events such as `RoomRented` in this plan.

### Acceptance criteria

- Events are frozen dataclasses.
- Events do not mutate state.
- `OptionDenied` does not include raw guard data.

## Task 4 — Convert movement to pure `EngineResult`

### Target files

- `engine/movement.py`
- `tests/test_movement.py`
- `tests/test_slice_integration.py`

### Implementation

Change `try_move(state, city, delta)` so it returns `EngineResult`.

Update `MoveResult` to include semantic movement context:

```python
@dataclass
class MoveResult:
    kind: str
    turn_over: bool = False
    la: int | None = None
    ln: int | None = None
    target: int | None = None
    from_cell: int | None = None
    delta: int | None = None
```

Behavior:

### Already turn over

If `ms <= 0` before movement:

- `events=[MoveBlocked(reason="turn_over", ...)]`
- `effects=[]`
- `status="turn_over"`
- `payload=MoveResult(kind="turn_over", turn_over=True, ...)`
- state unchanged

### Out of bounds

- `events=[MoveBlocked(reason="oob", ...)]`
- `effects=[]`
- `status="blocked"`
- `payload=MoveResult(kind="oob", ...)`
- state unchanged

### Street step

- `events=[MoveStep(...)]`
- `effects=[SetPosition(target), MsChange(-STEP_COST)]`
- commit effects
- `status="completed"`
- `payload=MoveResult(kind="step", ...)`

### Door entry

- `events=[EnterLocation(...)]`
- `effects=[SetEntryContext(la, ln), MsChange(-ENTER_COST)]`
- commit effects
- `status="completed"`
- `payload=MoveResult(kind="enter", la=la, ln=ln, ...)`

### Special cell

Special cell event flows remain out of scope.

Default behavior for this plan:

- `events=[]`
- `effects=[]`
- `status="not_implemented"`
- `payload=MoveResult(kind="special", ...)`
- state unchanged

### Wall

- `events=[MoveBlocked(reason="wall", ...)]`
- `effects=[]`
- `status="blocked"`
- `payload=MoveResult(kind="wall", ...)`
- state unchanged

### Acceptance criteria

- `try_move()` does not mutate its input state.
- Callers adopt `state = result.state`.
- Existing movement behavior is preserved.
- Existing vertical slice trajectory still works after test updates.
- Movement emits expected events/effects for step, enter, wall, out-of-bounds, turn-over, and special-cell outcomes.

## Task 5 — Convert interaction driver to `EngineResult`

### Target files

- `engine/interactions.py`
- `tests/test_driver.py`
- `tests/test_slw.py`
- `tests/test_slice_integration.py`

### Implementation

Replace `DriverResult` with `EngineResult`.

Change `run()` to return `EngineResult`.

Add event buffering to `Ctx`:

```python
def record(self, event: Any) -> None:
    self._events.append(event)
```

Keep:

```python
def apply(self, effect: Any) -> None:
    self._effects.append(effect)
```

Normal completion:

```python
EngineResult(
    state=commit_result.state,
    events=list(ctx._events),
    effects=commit_result.effects,
    status="completed",
    payload=HandlerResult(returned=stop.value),
)
```

Cancellation:

```python
EngineResult(
    state=state,
    events=[],
    effects=[],
    status="cancelled",
    payload=HandlerResult(returned=None),
)
```

Rules:

- Cancellation discards buffered events and effects.
- Handler exceptions raise.
- `ShowMessage` auto-ack behavior remains unchanged.
- `StartCombat` and `LoadSubState` remain out of scope and may still raise `NotImplementedError`.

### Acceptance criteria

- Existing handler behavior is preserved.
- Cancellation returns `status="cancelled"`.
- Cancellation has empty events/effects.
- Successful handler runs return committed effects.
- Tests no longer use `DriverResult.committed_effects`; they use `EngineResult.effects`.

## Task 6 — Add strict YAML consequence conversion

### Target files

- `engine/consequences.py`
- tests for consequence conversion

### Implementation

Create `engine/consequences.py`.

Add:

```python
def effect_from_dict(raw: dict) -> object:
    ...
```

```python
def effects_from_dicts(raws: list[dict]) -> list[object]:
    ...
```

Support at least:

- `ms_change` -> `MsChange`
- `money_change` -> `MoneyChange`
- `score_change` -> `ScoreChange`
- `teleport` -> `Teleport`
- `set_position` -> `SetPosition`
- `set_entry_context` -> `SetEntryContext`

Rules:

- Unknown `type` raises `ValueError`.
- Missing required fields raise `ValueError`.
- Unknown extra fields raise `ValueError`.
- Conversion does not mutate state.
- Conversion returns typed effect dataclasses.

### Acceptance criteria

- Known consequence dicts convert to expected effect dataclasses.
- Unknown consequence type raises.
- Missing fields raise.
- Extra fields raise.

## Task 7 — Add `run_option()`

### Target files

- `engine/actions.py`
- tests for option execution
- possibly `engine/locations.py` only if small helper extraction is needed

### Implementation

Add:

```python
def run_option(
    location: Location,
    option_id: str,
    state: GameState,
    *,
    ln: int | None,
    input_source=None,
    rng=None,
) -> EngineResult:
    ...
```

Behavior:

1. Look up `option_id`.
   - Unknown option id raises `ValueError`.

2. Evaluate guard.

3. If guard fails:
   - return:
     ```python
     EngineResult(
         state=state,
         events=[
             OptionDenied(
                 location_key=location.key,
                 option_id=option_id,
                 reason_key=option.on_denied,
             )
         ],
         effects=[],
         status="blocked",
         payload=DeniedResult(
             location_key=location.key,
             option_id=option_id,
             reason_key=option.on_denied,
             guard=option.guard,
         ),
     )
     ```

4. If option has consequences:
   - convert via `effects_from_dicts`.
   - commit effects.
   - return `EngineResult(status="completed")`.
   - include generic `LocationActionCompleted` event.

5. If option has handler:
   - require `input_source`.
   - call `engine.interactions.run()`.
   - wrap or augment result with generic location action events:
     - `LocationActionCompleted` on completed handler with effects or normal return.
     - `LocationActionCancelled` on cancellation.
     - `LocationActionRejected` for known no-effect rejection paths only if there is enough explicit signal.
   - If there is not enough signal to distinguish rejection from quiet no-op, do not guess.

Default implementation choice:

- Generic lifecycle events are emitted by `run_option()`, not by bare `run()`.
- `run()` remains handler-protocol-focused and location-agnostic.

### Acceptance criteria

- Guard-denied option returns blocked `EngineResult`.
- Guard-denied option emits `OptionDenied`.
- Guard-denied option has zero effects and unchanged state.
- Consequence option converts and commits effects.
- Handler option delegates to `run()`.
- Handler option requires `input_source`.
- Unknown option raises.

## Task 8 — Add handler purity test harness

### Target files

- tests/helpers or relevant test module
- `tests/test_slw.py`
- `tests/test_driver.py`

### Implementation

Add a test helper that:

1. Takes an input `GameState`.
2. Snapshots/deep-copies it.
3. Runs a handler through `engine.interactions.run()`.
4. Independently applies `result.effects` to the original snapshot using `commit()`.
5. Asserts:
   - original input state was not mutated;
   - `result.state` equals the independently committed state.

Apply to at least:

- `slw.rent` positive rent path.
- `slw.rent` negative rent path.
- `slw.rent` insufficient cash path.
- `slw.rent` quiet zero-month return.
- cancellation path if available.

### Acceptance criteria

- Handlers cannot accidentally mutate state directly without tests catching it.
- Existing `slw` behavior still passes.
- No read-only proxy is required in this plan.

## Task 9 — Add state/event integration acceptance test

### Target files

- `tests/test_slice_integration.py`
- or new `tests/test_state_event_integration.py`

### Implementation

Add/extend integration coverage proving:

1. Movement step:
   - returns `EngineResult`;
   - has `MoveStep`;
   - has `SetPosition` and `MsChange`;
   - returns new state;
   - input state unchanged.

2. Location entry:
   - has `EnterLocation`;
   - has `SetEntryContext` and `MsChange`;
   - sets `last_location` and `last_la`.

3. Guard denial:
   - `run_option(pub, "recruit", rank-1-state, ...)`
   - returns `status="blocked"`;
   - has `OptionDenied`;
   - has `DeniedResult`;
   - has zero effects;
   - state unchanged.

4. Handler option:
   - `run_option(slw, "rent", ...)`
   - returns `EngineResult`;
   - effects are committed;
   - returned state reflects effects;
   - input state unchanged.

5. Existing vertical slice still proves:
   - setup;
   - real map movement into `slw`;
   - positive rent;
   - negative rent quirk;
   - guard denial;
   - pub recruit denial;
   - deterministic final state.

### Acceptance criteria

- Full test suite passes.
- State/event integration test passes.
- Existing slice behavior is preserved under the new result/event/effect architecture.

## Suggested implementation order

1. Task 1 — result/commit foundation.
2. Task 2 — primitive movement effects.
3. Task 3 — semantic event catalog.
4. Task 4 — pure movement.
5. Task 5 — interaction driver conversion.
6. Task 6 — consequence conversion.
7. Task 7 — `run_option()`.
8. Task 8 — handler purity harness.
9. Task 9 — state/event integration acceptance test.

Keep tests green after each task.

## Open details implementers may decide

These are not big decisions and do not need another planning round unless implementation reveals a major conflict.

1. Exact import organization between `engine.actions`, `engine.events`, and `engine.effects`.
2. Whether `MoveResult` remains in `engine.movement` or moves later.
3. Whether `CommitResult` lives in `engine.effects` or is imported/re-exported elsewhere.
4. Whether generic location lifecycle events are added immediately for all `run_option()` paths or only where unambiguous.
5. Whether special-cell result uses `status="not_implemented"` exactly; default should be `"not_implemented"` for this plan.

## Big decisions that require user confirmation if reopened

Ask before changing these:

1. Removing separate `events` and `effects`.
2. Making semantic events apply state directly.
3. Reintroducing direct state mutation in movement.
4. Catching handler exceptions and converting them to `EngineResult(error=...)`.
5. Adding content-specific events like `RoomRented`.
6. Implementing `sph`, `waf`, combat, persistence, or server work inside this plan.

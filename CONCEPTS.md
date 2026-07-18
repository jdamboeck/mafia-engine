# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Action Spine

### Effect
A primitive, typed record of one state mutation — the only way game state ever changes.
*Avoid:* mutation, state change (as nouns for the record)

Effects are pure data. They are buffered during an action and applied atomically at the end (folded in order over the frozen state graph, each producing a new state); a cancelled action discards its buffer and leaves state untouched. The ordered stream of committed effects, together with logged RNG draws, is the replay record — replaying it reconstructs a game deterministically.

### Semantic Event
An audit/UI record of what happened or was attempted during an action — never applied to state and never part of the replay record.

A Semantic Event may exist with no matching Effect: a blocked move or a denied option emits an event but mutates nothing. Events carry machine keys (reason codes), never display text; themes resolve them to player-facing wording.

### Interaction
A typed suspend-point a Handler yields to ask the client something — show a message, prompt for a number or choice, confirm.

Interactions drive execution flow while Effects drive state: a Handler yields an Interaction, the driver relays it to the client and sends the response back in. The same interaction protocol is the intended future network surface — a remote client drives the identical exchange.

### Handler
A generator coroutine implementing one menu action's procedural logic — the imperative half of a location, referenced by id from the declarative YAML shell.

Handlers are config code, not engine code. A Handler may only read state, draw from the provided RNG, yield Interactions, and buffer Effects and Semantic Events; it never mutates state directly. Its buffered work lands atomically on clean completion and is wholly discarded when the player cancels mid-flight.

### Guard
A declarative precondition on a menu option, expressed in the config's condition DSL and evaluated before the option runs.

A failed Guard is a normal outcome, not an error: the option is denied (or excluded from the offered menu) with a reason key, no Handler runs, and no state changes. Guards can depend on which tile of the location the player entered from, not just on player state.

## Flagged ambiguities

- Older design prose said effects are "committed as events" — the terms are now distinct: Effects mutate and replay; Semantic Events are audit-only and never replay.

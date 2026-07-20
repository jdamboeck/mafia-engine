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

## Game state

### Frozen State Graph
The game state as a whole: a tree of immutable records where no field can be written after construction and every collection it holds is read-only.

Immutability is structural, not conventional — an attempted write fails where it is written rather than corrupting a later save. Applying an Effect therefore never modifies state in place; it rebuilds the affected records and returns a new graph, with untouched branches shared rather than copied. Freezing the records alone is not enough: a frozen record can still hand out a mutable collection, so read-only collections are established when state is *built*, at every construction site (new game, effect application, and load). This is what makes the replay guarantee enforceable rather than merely documented — with no way to mutate state outside an Effect, the effect log necessarily accounts for every change.

## Turn processing

### Upkeep Flow
The per-player processing that runs at the start of each turn, before the free turn — banner, gangster energy regeneration, rank promotion commit, debt check, shop income, arms-deal resolution.

The Upkeep Flow is config handler code driven by the engine through the same generator/interaction/effect protocol as location handlers; it can show screens and start combat (the debt-default collectors fight lives here). Its "monthly" ticks fire per player turn, which equals monthly because one full player round is one month.

### Job Shift
A turn that is replaced by working an active pub job — no map movement, no location menu; the shift (quiet day, croupier trick, or a fight) is the whole turn.

A job runs for a fixed number of shifts and pays its full wage once, as a lump sum after the final successful shift; a lost shift fight ends the job unpaid. There is no monthly wage.

## Combat

> Planned vocabulary. These terms are defined by the active plan
> (`docs/plans/2026-07-20-003-refactor-combat-engine-foundation-plan.md`) and
> land as its units do; the entries are recorded up front so naming stays
> consistent during implementation.

### Combatant
The engine's blueprint for anything that fights: the minimum any implementation
needs, plus a structure the game extends. Four slots — `position` (grid cell,
absent when off-grid), `down` (out of the fight), `vitality` (the depleting
resource whose exhaustion means down), and `attrs` (an opaque map the engine
carries but never reads).

A game supplies the concrete type. In mafia_1920s that is the **Gangster**,
whose `attrs` hold kraft, brutalitaet, and intelligenz. The engine never learns
those names: it asks the Rules Bundle for a roll and applies the result.
*Avoid:* Fighter (the pre-refactor engine name for the same concept)

### Vitality
The depleting resource whose exhaustion takes a Combatant out of a fight. Named
by the engine because termination depends on it — everything else about a
combatant is opaque. The game decides what it is called in its own vocabulary
(`energie` in mafia_1920s) and what depletes it.

### Rules Bundle
The game's combat policy, handed to the engine at fight construction: which
attribute fills which engine role (accuracy, damage, vitality), and the formulas
that turn a roll into a hit or a damage number. The engine owns the mechanism —
sequence, apply, clamp, detect termination — and calls into the bundle for every
value it cannot derive itself.

### Scenario
A complete description of one fight as a value: the two sides, the arena grid,
the Rules Bundle, and an optional seed. Constructible without any game state,
which is what lets the same fight run in-game, headlessly in a simulation, or in
the debug tool. Carries the fight only — never its rewards or penalties, which
belong to whatever invoked it.

### Encounter
A Scenario declared in config data rather than assembled in handler code, plus
optional outcome declarations. The fight setup is always declarable; a
consequence needing live state or belonging to a surrounding flow stays in the
handler, and the Encounter simply omits it.

### Driver
Whatever decides a side's next move: a human at a client, the AI routine, a
policy function, or a replay log. Assigned per side, so a fight can be
human-vs-AI, AI-vs-AI, human-vs-human, or policy-driven, and reassignable
mid-fight.

Distinct from the **Gang** (the roster that wins or loses) and the **Controlling
Player** (who owns that gang). A human-owned gang may be driven by AI without
changing ownership; combat knows only the driver.

### Combat Result
A finished fight's outcome: the winning side and the per-side losses. What the
engine hands back to whatever started the fight, which then applies the
consequences.

## Flagged ambiguities

- Older design prose said effects are "committed as events" — the terms are now distinct: Effects mutate and replay; Semantic Events are audit-only and never replay.
- **Fighter** and **Gangster** were separate engine dataclasses with overlapping fields. They are one concept at two layers: **Combatant** is the engine's blueprint, **Gangster** the game's filling of it. Use Combatant for the engine-level term; reserve Gangster for mafia_1920s' concrete entity.

# Mafia Engine — Design & Build Plan

> **Status:** Design complete; ready for implementation.
> **This is the single, self-contained design document for the engine.** Everything
> needed to start building is here. The authoritative game *knowledge* (formulas,
> data, decompiled source) lives in the research pipeline at `../research`; this plan
> tells you how to turn that knowledge into a working engine.

---

## 1. What we are building

A fresh Python reimplementation of the 1986 Commodore 64 game **"Mafia" by Igelsoft**,
built as the reference title for a **genre engine**, not a single-title clone and not a
fully generic game engine. There is no legacy code to reuse.

**The genre we abstract:** *turn-based, board-game-like strategy games with tactical
combat* — games with a map you move around under a movement economy, locations that
open menu-driven interactions, RNG-driven outcomes, a progression/score ladder, and a
separate tactical-grid combat mode. The **reusable core** is those genre mechanics (turn
loop, movement economy, location/menu handlers, tactical-grid combat, interaction
protocol, RNG). **Mafia-specific** rules, content, formulas, strings, and assets live in
a game configuration, not the engine.

**The seam:** genre-level engine ↔ Mafia-specific content. The engine hosts games *of
this shape*; it does not try to host arbitrary games. A new title in the genre starts by
**copying `data/game_configs/mafia_1920s`** and editing its data, formulas, strings,
assets, and Python handlers — the engine itself is untouched. This keeps the abstraction
honest: we generalize only what a second same-genre game would actually share, and only
once a second config proves the seam (§8, Phase 8).

The reference game: the player is a 1920s Chicago gangster who moves around a city map,
visits locations (pub, gun shop, bank, casino, loan shark, police station, …) to commit
crimes, earn money, recruit and train a gang, fight rival gangs on a tactical grid, and
climb ten criminal ranks — winning either by reaching the top rank with two special
deeds done, or by having the highest score at the game's end year.

### Source of truth

All game rules come from the reverse-engineering pipeline in `../research`:

| Resource | Path | Use |
|---|---|---|
| Decompiled BASIC | `../research/src/decompiled_basic/mf-prg.bas` | 870 lines, 108 vars — the authoritative logic each handler is a port of |
| Systems analysis | `../research/research-data/pass-2/systems-analysis.yaml` | combat/economy/wanted/location rules |
| Game logic | `../research/research-data/pass-2/game-logic.yaml` | RNG tables, combat AI, score/rank formulas |
| Location handlers | `../research/research-data/pass-2/location-handlers.yaml` | per-option behavior + guards |
| Data structures | `../research/research-data/pass-2/data-structures.yaml` | map/combat grids, variables |
| Menu trees | `../research/research-data/pass-1/location-extraction.yaml` | menu labels + option → handler |
| System docs | `../research/docs/systems/*.md` | human-readable summaries |

**Rule:** when a mechanic is unclear, read the BASIC line block it maps to (line
numbers are cited throughout the research); do not invent behavior.

### Fidelity bar: **behavioral**

We reproduce the original's **formulas, probabilities, rewards, and outcomes** exactly
as verified in the research — but we are free to use clean modern representations and
we do **not** reproduce the original's exact RNG draw order. A seeded run matches the
original *statistically and mechanically*, not byte-for-byte.

---

## 2. Core design idea

The opening insight that shapes everything: **location logic is genuinely procedural.**
A location menu option is not just "check a guard, apply a fixed list of effects." The
real handlers (in the decompiled BASIC) contain input loops, 64 distinct `rnd()` sites,
outcome tables whose branch index is arithmetic over game state
(`on int(rnd*4)-(w=2)-(la<>9) goto ...`), minigames (safe-cracking), and combat that
starts mid-handler and resumes on its result.

Therefore the engine is built around **two layers**:

1. **A declarative shell (YAML)** — the menu structure and *guards* (preconditions).
   This is pure data, safe for modders to edit, and covers the whole menu surface.
2. **Procedural handlers (Python)** — one function per menu action, referenced from the
   YAML by a string id. Handlers are **generator coroutines** that `yield` typed
   *interactions* (a prompt, a choice, a message, "start a fight") and receive the
   response back.

The generator/interaction protocol is the **spine** of the whole engine. It is:
- the faithful expression of every procedural handler,
- the per-handler state machine ("all locations are state machines"),
- and — unchanged — the eventual **network message protocol** (the server is just an
  async transport that drives the same generators).

```python
# A handler is: Generator[Interaction, Response, list[Event]]
def gambling(ctx):
    game = yield PromptChoice("game", ["poker", "black_jack", "roulette"])
    bet  = yield PromptInt("bet", min=1, max=ctx.player.ka)
    ctx.apply(MoneyChange(-bet))
    if ctx.rng.hit(1, 1 + game.index):            # logged, seedable RNG
        ctx.apply(MoneyChange(int(bet * (0.5 + game.index))))
        yield ShowMessage("system.sph.won", {"amount": bet})
    else:
        yield ShowMessage("system.sph.lost", {})
```

---

## 3. Game systems (the spec to implement)

All values below are verified against `../research`. Cited BASIC line numbers point at
the authoritative implementation.

### 3.1 Map & movement
- **City map:** 40×25 grid, 1000 cells, row-major `index = row*40 + col`.
- **Walkable:** only onto cell code **156** (a door); everything else blocks.
- **Moves:** deltas `-1 / +1 / -40 / +40` (left/right/up/down). Leaving costs `ms -= 5`.
- **Special cells:** **569** = cash-transport robbery (event flow `la=13`), **861** =
  mayor hit (event flow `la=14`), **911** = jail cell (set after arrest).
- The city map (40×25) and the combat grid (40×13) are **different coordinate spaces**
  — never conflate them.

### 3.2 Turn system & movement points (`ms`)
- Each player takes a complete turn, then the next player begins; `sp` cycles `0..sz-1`.
- **Movement points `ms`** are replenished each turn from the player's vehicle speed
  (`ms = speed(vehicle)`, line 1012). Actions cost `ms`; the turn ends when `ms <= 0`.
- **`ms` is also a control-flow signal:** some handlers set `ms = 0` to force the turn
  to end (e.g. taking a job at 12335, lawyer acquittal 26070). The turn system must
  support handler-forced turn-end, not only cost decrement.
- Default action costs (configurable): map step `1`; enter/exit location `5`;
  enter/exit combat `10`. Verify against BASIC before finalizing.

### 3.3 Locations
Twelve menu-driven locations, plus two map-triggered event flows:

| la | key | Location | la | key | Location |
|----|-----|----------|----|-----|----------|
| 1 | slw | Schlupfwinkel (motel) | 7 | sgl | Laden (racket) |
| 2 | pub | Pub/Bar | 8 | sub | Subway |
| 3 | waf | Waffenladen (gun shop) | 9 | bhf | Bahnhof (station) |
| 4 | aut | Automobil (car dealer) | 10 | ban | Bank |
| 5 | kdh | Kredit-Hai (loan shark) | 11 | pol | Polizei |
| 6 | sph | Spielhölle (casino) | 12 | ble | Blüten-Eddie (forger) |
| **13** | — | **Cash transport** (map cell 569, no menu) | **14** | — | **Mayor hit** (map cell 861, no menu) |

- Each menu location resolves an option to **leave**, a **handler**, or a **sub-menu**.
  Only two real sub-menus exist (casino game choice, racket follow-up); both are handled
  as in-handler `PromptChoice` interactions, not a generic menu tree.
- **`ln`** (within-location tile index, 1–9) is a **first-class handler input**: it
  changes rent price (`fnm(ln)`), which pub serves alcohol (`ln in {4,5}`), racket
  outcomes (`ln in {1,4,5}`), etc. It is not merely an option index.

### 3.4 Combat (a core engine system, not a minigame)
- **Grid:** 40×13, indices `0..520`, stored flat. Row = `int(pos/40)`.
- **Energy:** init **5** per gangster; restored each turn `en = en + int(kr/10) + 1`,
  capped at `en = 2 + int(kr/4) + int(bt/4)`.
- **Weapons** (DATA 50100–50115, 9 total): price / accuracy `ts` / damage `tg` / sound
  `ws`. From `haende` (0$, ts2, tg2) to `handgranaten` (10000$, ts7, tg18).
- **Range:** 2 base; 15 if weapon index > 3; 20 if index 6 or 7.
- **Hit:** miss if `int(rnd*ts) = 0` **or** `int(rnd*(kr/10 + 1)) = 0`.
- **Damage:** `y = int(rnd*tg + bt/10) + 1` (always ≥ 1 on a hit).
- **AI (BASIC 30400–30492):** step toward nearest enemy; melee weapons (index < 4) or a
  50% roll force the approach branch; **anti-oscillation via per-fighter direction
  memory `ri()`**; collision + bounds checked before moving. Implement this concrete
  algorithm as the default; make it swappable only later.
- **End:** one side all dead, or surrender. Result exposed as a flag (`s`) the calling
  handler resumes on.

### 3.5 Economy
- **Bank:** daytime hold-up (needs a tip target) or night break-in (needs `in>=40,
  kr>=15, bt>=20`, safe-cracking minigame). Payout
  `p = int(rnd*3000) + 4000 - 500*(direct)`; +3000 bonus for postzug/cash-transport.
- **Gambling (casino):** poker/blackjack/roulette; win if `int(rnd*(1+x)) = 0`
  (x = game index 1–3); gross payout `p = int(p*(0.5+x))` (stake already deducted). All
  three are −EV.
- **Rewards:** jobs (bouncer/croupier/killer, paid over months), extortion, pickpocket
  loot table, debt collection, jail-break bribe, croupier cheat — all with documented
  formulas in `game-logic.yaml`.
- **Training:** shooting range and training camp raise gangster stats
  (`fnr = int(rnd*8)+8`, i.e. 8–15, capped 99) and add to score.
- **Loans:** borrow 0–5000$ (must have no existing debt), repay within 6 months; own /
  capitalize a loan shop.

### 3.6 Score, rank & winning
- **Score `gf`** (0–100): updated at ~25 sites via `gf += x * x8` where `x` is a
  hand-tuned per-event delta (+4 bank heist, −10 arrest, −5 safe caught, …) and `x8` is
  the game's difficulty multiplier (0.1–2, chosen at setup).
- **Rank `ra`** (1–10): `nr = int(gf/11.1) + 1`; promotes when it changes.
- **Win flags:** `x5%` (cash-transport success), `x6%` (mayor hit).
- **Early win:** rank **10** AND `x5%` AND `x6%`.
- **Year-end win:** at `int(ja) = x9` (end year), highest `gf` wins.

### 3.7 Wanted, police & jail
- **Street stop:** 1/5 chance when `ra > 3` and `ms/20` is integer → police combat.
- **Caught:** bribe (`500 + 500*ra`, 1/5 still jailed), escape
  (`int(rnd*tr/11) = 0`), or surrender.
- **Jail:** `gs` months (`int(ra/2 + .5)` on surrender); position `911`; lawyer if
  `ra >= 5` (pay 0–10000$ to reduce months); jail-break option at the police station.

---

## 4. GameState

Modular dataclasses; each subsystem owns its data. Enumerated against the ~108 real
variables (many are per-player or per-gangster arrays).

```
GameState
├── players: list[Player]
│   ├── ka (cash), gf (score 0-100), rank/nr, po (map pos)
│   ├── vehicle, speed, ms (movement pts)
│   ├── roster: list[Gangster]        # name, weapon, stats (energy/kraft/intelligenz/brutalitaet)
│   ├── jobs: {type, pending_pay, months_left}
│   ├── debt: {amount, months}        # RENAMED: source `kr(sp)` collides with gangster stat "kraft"
│   ├── business: {shop_owner, shop_capital}
│   ├── contraband: {fake_papers, counterfeit, alcohol_barrels}
│   ├── wanted: {jail_months, bribe_months, x5, x6}
│   └── tip_target, safe_skill, last_location
├── map:    {grid 40x25, tenancy[ln], special_cells}
├── combat: CombatState {enemy roster, grid 40x13, dir_memory ri(), result_flag s}
├── clock:  {year ja, end_year x9, active_player sp, player_count sz}
├── config: {score_mult x8, action costs, formula params}
└── flags:  {global graphics/loaded flags}   # distinct from per-player bitfields above
```

Notes: unpack the original's packed 8-char stat string (`ge$`) into plain ints, but
preserve every documented formula (e.g. the energy cap). Distinguish per-player
bitfields (contraband `ag`) from global flags.

---

## 5. Engine architecture

### 5.0 Top-level state machine

The whole game is one explicit finite state machine; every other lifecycle nests inside
it. This is the single consistent lifecycle all handlers, turns, and combat flows use.

```
Title ──▶ Setup ──▶ MainLoop ──▶ Win/Lose ──▶ (restart)
                       │
                       ├── Overview        (read-only screen)
                       ├── Location  ──▶ handler generator (§5.1)
                       ├── Combat    ──▶ combat sub-FSM (§3.4)
                       └── NextPlayer (advance sp, run turn upkeep)
```

**Nesting of lifecycles** (each level is driven by the one above it):

```
Game FSM
└─ Player turn            (movement economy: ms; ends on ms<=0 or handler-forced)
   └─ Location visit / Combat entry
      └─ Handler generator   (yields interactions §5.1, applies effects §5.2)
         └─ Interaction       (one prompt/message/combat request ↔ one screen state)
```

A single **driver** advances whichever level is active by turning `yield`ed
interactions into screen states and feeding responses back. The Game FSM state, the
active player's turn, and any in-flight handler generator are all part of `GameState`
so a save/replay (§5.5) can reconstruct exactly where execution was suspended.

### 5.1 The interaction protocol (spine)

A **handler** is `Generator[Interaction, Response, list[Event]]`. It `yield`s an
**Interaction**; the **driver** turns that into a typed, JSON-serializable **screen
state**, obtains a **Response**, and `.send()`s it back. The Phase 1 driver is
in-process and synchronous; the later WebSocket server (§7) is the *same* driver over an
async transport — handlers never change.

**Interactions vs. Effects (a firm distinction).** Interactions control **execution
flow** (they suspend the handler to ask the client something); Effects (§5.2) mutate
**game state**. A handler never mutates state directly — it `yield`s interactions and
calls `ctx.apply(effect)`. Keeping the two categories separate is what makes handlers
testable (feed scripted Responses, assert emitted Effects) and network-transparent.

**Interaction catalog** — each with its fields and the Response it produces:

| Interaction | Fields | Response | Notes |
|---|---|---|---|
| `ShowMessage` | `key`, `params` | `Ack` (none) | Display only; no input. |
| `PromptInt` | `key`, `min`, `max` | `int` (or Cancel) | Re-prompts on out-of-range/non-numeric input (driver-enforced, not handler). |
| `PromptChoice` | `key`, `options[]` | chosen `index`/`id` (or Cancel) | Menu/sub-menu, gangster-picker. |
| `Confirm` | `key` | `bool` (or Cancel) | Yes/no (BASIC sub `1110`). |
| `StartCombat` | `fighters`, `arena` | `CombatResult` | Suspends the turn; see resume signature below. |
| `LoadSubState` | `kind`, `params` | sub-state result | Minigames (safe-cracking); a nested driver loop. |

**Error / invalid input handling.** Range and type validation live in the **driver**, so
a `PromptInt` handler receives only a valid `int` (or Cancel) — handlers never re-loop
for validation. Protocol/transport errors (malformed response, disconnect) are the
server's concern (§7: freeze + await reconnect), not the handler's.

**`StartCombat` resume signature.** Combat is a sub-FSM (§3.4) the driver runs to
completion, then resumes the handler with:
```python
CombatResult(outcome: Literal["won","lost","surrendered"],   # maps to BASIC flag `s`
             survivors: list[FighterId], loot: int | None)
```
Handlers branch on `outcome` (e.g. bank robbery: `lost`/`surrendered` → arrest flow).

**Open Phase-0 decision — cancellation.** The original returns to the menu on a `0`
input at ~12 prompt sites (BASIC 13020, 15020, 15051, 15208, 16015, 21011, plus the
combat-step aborts 30450–30462). The protocol must support "abort this action cleanly,
commit no partial effects, return to the menu." Two candidate mechanisms, to be chosen
when the driver is built:
- **(a) Raise `Cancelled` into the generator** (`gen.throw`) — handler unwinds via
  `try/finally`; the driver discards effects buffered for this action. Uniform; no
  per-handler bookkeeping.
- **(b) A `CANCEL` sentinel Response** the handler checks after each cancellable prompt —
  closer to the BASIC `if x=0 then return`, but each handler must remember to check.
Effect-commit semantics (buffer-then-commit per action, so a cancel is atomic) are part
of this decision.

**Shared BASIC subroutines become engine helpers/interactions:** wait, yes/no
(`Confirm`, sub `1110`), not-enough-money (sub `1125`), gangster-picker (`PromptChoice`,
sub `1130`), score update (Effect, sub `1160`), combat entry (`StartCombat`, sub `5000`).
This helper set is part of the Handler API contract (§5.2a).

### 5.2 Location shell (YAML)
```yaml
# data/game_configs/mafia_1920s/content/locations/pub.yaml
key: pub
options:
  - id: recruit
    guard: {and: [{stat: rank, op: '>', value: 4}, {stat: gang_size, op: '<', value: 10}]}
    on_denied: "system.pub.rank_too_low"     # string key
    handler: "pub.recruit"                     # id in the Python HANDLERS registry
  - id: leave
    resolve: {consequences: [{type: ms_change, amount: -5}]}   # simple option: pure data
```
The engine owns menu render, guard evaluation, the denial message, `ms` bookkeeping,
and string resolution. A simple option may resolve via a flat `consequences[]` list;
anything procedural names a `handler`.

**Condition DSL (guards):** operators `= != >= <= > < in`; connectives `and` / `or`;
nesting depth ≤ 2; **no NOT** (restructure to avoid). Verified sufficient for every
guard in `location-handlers.yaml`.

**Effect API (consequences & `ctx.apply`):** `money_change`, `stat_change`,
`score_change`, `wanted_change`, `flag_set`, `ms_change`, `energy_change`, `jail`,
`teleport`, `spawn_fighter`. Effects are **pure data** (typed, serializable) — they are
also the replay events (§5.5).

### 5.2a Handler API contract — the only programming interface

A game configuration's handlers are the **sole** place config code runs, and they may
touch **only** the surface below. Everything else in `engine/` is internal and off-limits
— this is what keeps configs portable across engine versions (paired with `engine_api`
versioning, §6a).

A handler is a generator `def handler(ctx): ...` and may:

| May use | What it is |
|---|---|
| `ctx.state` | **Read-only** view of `GameState` (current player, roster, cash, position, flags, …). Never mutated directly. |
| `ctx.rng` | The seedable, logged RNG (`hit(a,b)`, `range(n)`). **The only source of nondeterminism** (§5.5). |
| `yield <Interaction>` | The §5.1 catalog — the only way to reach the client. |
| `ctx.apply(<Effect>)` | The §5.2 Effect API — the only way to mutate state. |
| engine **helpers** | The named shared-subroutine set (§5.1): not-enough-money guard, gangster-picker, score update, combat entry, etc. |

A handler **may not**: import from `server/` or `clients/`, touch a transport/socket, read
wall-clock time or the OS RNG, mutate `GameState` in place, or reach into engine internals
not listed above. Handlers must be **deterministic given `(ctx.state, ctx.rng, Responses)`**
so replay (§5.5) is exact. Violations are the boundary bugs this contract exists to prevent.

### 5.3 Strings
- **Zero hardcoded *display* strings** in the engine (handler *branching logic* stays
  in Python — only text is externalized).
- Strings are **parameterized templates** resolved by a theme:
  `"pro monat kostet das {price}$ miete"`. The engine emits `(key, params)`; the theme
  formats. This covers both menu labels and **handler-emitted messages**
  (`ShowMessage`).
- Key convention: `locations.{id}.menu.{option}`, `locations.{id}.dialogue.{node}.{id}`,
  `system.{subsystem}.{msg}`, `ui.{label}`, `entities.{type}.{id}`.

### 5.4 RNG & determinism
- A **seedable, loggable RNG** from day one: `rng.hit(a, b)`, `rng.range(n)`, every call
  recordable. Behavioral fidelity ⇒ we match probabilities, not the original draw order.

### 5.5 Event sourcing, replay & saves

The original has **no save game**; we add one. The design splits into a **schema fixed in
Phase 0** and a **store built in Phase 5** — so handlers emit replay-safe events from day
one and nothing has to be retrofitted later.

**Fixed in Phase 0 (design):**
- **Typed, versioned Event schema.** Every state change is a pure-data Event (the §5.2
  Effects are the event vocabulary), tagged with a schema `version` so old logs stay
  readable as the schema evolves.
- **Determinism contract:**
  1. *All* nondeterminism flows through `ctx.rng` (seedable + logged); no wall-clock, no
     OS RNG, no ambient state (enforced by §5.2a).
  2. Events are pure data; applying an event to a state is a pure function.
  3. A game = `initial seed + setup + ordered Event log`. **Replay = load snapshot, then
     re-apply the event log**; it must reproduce the exact `GameState`, including a
     suspended handler generator's position (§5.0).
  4. RNG draws are themselves logged events, so replay does not re-roll.

**Built in Phase 5 (store):** an append-only **JSONL event log** + per-turn snapshots;
`SaveGame` writes log + latest snapshot; load restores the snapshot and (optionally)
fast-forwards remaining events. Additive — handlers are untouched because the schema and
contract already held.

---

## 6. Modding: game configs & themes

Two independent axes.

**Game configuration** (the "rules & world") — build-time, frozen per game:
locations, entities (weapons/vehicles/gangsters/ranks — stats & prices), formula
parameters, win conditions, turn costs, and the handler registry (Python handlers may
be overridden/added by id). It answers *what exists and how it works.* A config may add
new locations/entities/rules. It **cannot** change the engine's core simulation.

**Theme** (the "presentation") — runtime-swappable: all displayable text (as templates),
images, sounds, and rendering config (fonts, colors, layout). It answers *how it looks
and sounds.* A theme provides only what it changes and **deep-merges over the config's
default theme at the key level** (change one string or one weapon's art without copying
the file). A theme **cannot** change rules or outcomes.

Relationship: each config declares a default theme; themes live inside the config; a
player selects a theme per client at runtime, and the server re-resolves strings on
switch.

### 6a. Engine ↔ Game-Config contract (the engine's public API)

The boundary is a contract: the engine provides the genre runtime; a config provides the
game. Neither reaches across it except through the surfaces below.

| The **engine** provides | The **game config** must provide |
|---|---|
| Game FSM + turn loop + movement economy (§5.0) | Locations as YAML menu/guard shells (§5.2) |
| Interaction driver + protocol (§5.1) | Handlers registered by id (Python generators, §5.2a) |
| Effect application + `GameState` (§5.2) | Entities: weapons/vehicles/gangsters/ranks (stats, prices) |
| Combat system (grid, AI, damage/energy) (§3.4) | Formula parameters & win conditions |
| Seedable logged RNG (§5.4) | Strings as parameterized templates + assets/sounds (theme) |
| Persistence / replay (schema + store) (§5.5) | `default_theme` declaration |
| String/theme resolution + networking (§7) | `engine_api:` version it targets |

**Interface versioning.** `config.yaml` declares `engine_api: N`. The engine refuses (or
adapts) configs whose targeted version it can't satisfy, so the engine can evolve while
older configs keep working. Bump `N` only on a breaking change to the handler API (§5.2a),
the Effect/Interaction catalogs, or the config schema.

**Layering rule (enforced structurally).** The `engine/` package **imports nothing** from
`server/`, `clients/`, or any transport/render library. Simulation is fully headless and
testable in isolation; presentation and transport depend on the engine, never the reverse.

---

## 7. Server & clients (later phases)

- **Server:** authoritative — owns all state and logic; a **thin async transport
  adapter over the interaction protocol** (JSON over WebSocket). Runs embedded
  (in-process with a client) or standalone (headless, remote clients). Resolves strings
  per-client against their theme. On disconnect the game freezes and awaits reconnect;
  reconnect re-syncs from snapshot + event log.
- **Clients:** maximally thin — render the pushed screen state, collect input, speak the
  protocol, apply theme assets. **No game logic, no local state** (a pause menu is the
  one client-only overlay). Terminal client first (carries phases 1–6); pygame client
  later.

---

## 8. Build order

Just enough shared foundation, then a playable single-player slice; networking, event
store, codegen, and pygame are adapters over a proven core.

- **Phase 0 — Core + contracts.** GameState dataclasses; seedable loggable RNG; runtime
  YAML loader; the interaction protocol + in-process synchronous driver; the effect API.
  Also **finalize the design contracts** here: the formal interaction protocol spec
  (§5.1) including the **cancellation decision**; the handler API contract (§5.2a); the
  Engine↔Config contract + `engine_api` version (§6a); and the **Event schema +
  determinism contract** (§5.5) — even though the event *store* is deferred to Phase 5.
- **Phase 1 — Playable slice (terminal, single-player).** Map movement, `ms` economy
  with handler-forced turn-end, location entry, guard evaluation, string keys. First
  location: **slw** (rent input loop, money; no combat).
- **Phase 2 — Protocol proven on hard cases.** **sph** (gambling: input loop + RNG
  payout) and **waf** (buy/train: stat mutation + gangster picker).
- **Phase 3 — Combat + `StartCombat`.** Hardwire original AI/damage/energy; wire the
  post-combat resume. Enables **ban** and **police/jail** → first complete loop
  (crime → wanted → arrest → jail).
- **Phase 4 — Content complete.** Remaining 9 locations + the 2 map events (la=13/14).
- **Phase 5 — Event sourcing / snapshots / replay tests** (additive).
- **Phase 6 — WebSocket server** as a thin adapter over the interaction protocol
  (multiplayer, disconnect/reconnect, per-client theme resolution).
- **Phase 7 — Pygame client + theme/string externalization.**
- **Phase 8 — Codegen (only if profiling demands)** + a second game-config to validate
  moddability.

---

## 9. Project structure

```
mafia/
├── engine/     # pure Python simulation (no rendering, no transport)
│   ├── state/          # GameState + subsystem dataclasses
│   ├── rng.py          # seedable, loggable RNG
│   ├── interactions.py # Interaction/Response types + the driver
│   ├── effects.py      # typed effect API
│   ├── conditions.py   # guard DSL evaluator
│   ├── locations.py    # YAML shell loader + HANDLERS registry
│   ├── handlers/       # one module per location; generator functions
│   └── combat/         # grid, AI, damage/energy
├── server/     # (Phase 6) async transport adapter over the interaction protocol
├── clients/
│   ├── terminal/       # (Phase 1) thin renderer + input
│   └── pygame/         # (Phase 7)
└── data/
    └── game_configs/
        └── mafia_1920s/
            ├── config.yaml
            ├── content/  { locations/*.yaml, entities/*.yaml }
            └── themes/classic/ { strings/, assets/, sounds/, renderer/ }
```

---

## 10. Testing & verification

- **Formula unit tests:** engine output equals the research-documented formulas (damage
  bounds, energy cap, bank payout, score/rank, extortion) — the behavioral-fidelity gate.
- **Interaction-driver tests:** run a handler generator with scripted responses; assert
  the emitted interaction sequence and resulting state (e.g. gambling: bet → branch →
  money delta).
- **Vertical-slice integration test (end of Phase 3):** a seeded single-player run
  through slw → sph → waf → ban → arrest → jail, asserting the full state trajectory.
- **End-to-end manual run:** drive the terminal client through the slice and confirm one
  complete crime → wanted → jail loop plays.
- **Replay tests (Phase 5+):** re-run a logged event + RNG stream; assert identical
  final state.
- **Cross-check:** every menu guard in `location-handlers.yaml` is expressible in the
  depth-2 / no-NOT condition DSL (spot-checked — it is).

---

*End of plan.*

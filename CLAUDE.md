# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Building the first vertical slice** — unit-by-unit against a plan (derive the *current*
status, active plan, and open work per point 1 below; don't read a fixed status here).
The project bootstrap (U0) is done: there is a Python skeleton, a `pytest` + `Makefile`
toolchain, and a durable per-unit tracking board.

**If you are starting a work session, read `docs/AGENTS.md` first** — it is the
operating manual: the one-orchestrator/one-subagent execution model, the green-tree rule,
commit + branch conventions, and how to pick up the next unit. Then:

1. **Derive current state — do not trust this file's memory of it.** *"Which plan is
   active"* and *"which issues are open"* go stale the moment a plan closes, so this
   file must never hardcode them (it did twice, and misled two sessions). Instead,
   derive them from the tree — the same rule the rest of the project lives by
   (*facts live in git and the board, not in a doc*):
   - **Active plan** = the newest file in `docs/plans/` **not** marked
     `artifact_readiness: superseded` in its frontmatter, and **not** a
     `*-brainstorm-basis` / `*-NEXT-STEPS` / `*-amendments` companion. If the newest
     such file is a `brainstorm-basis` doc, **there is no active plan** — the previous
     one is done and the next hasn't been planned yet; the brainstorm doc is the
     forward pointer.
   - **How far it's executed** = `git log --oneline` (units land as commits).
   - **The board** = `gh issue list` (GitHub Issues on `origin`; if there is no
     remote, `docs/PROGRESS.md` instead). Close an issue when its commit lands green.
   - A plan is **done** when its final unit's commit has landed green and a
     `*-brainstorm-basis` doc for the next plan exists.
   - Drafts marked `artifact_readiness: superseded` **must not be executed**.
2. **Landed, do not re-open:** the State/Event Foundation (T1–T9,
   `docs/plans/current-action-plan.md`), the first-slice deepening plan
   (`2026-07-12-001-…`, including U10 terminal client and U12 save/load), the
   armed-closure plan (`2026-07-18-002-…`), and the Combat Engine Foundation plan
   (`2026-07-20-003-…`, U1–U8 + U6a — engine made attribute-agnostic, scenarios,
   per-side drivers, data-defined encounters, recording/replay, terminal debug tool).
   *(This is an append-only ledger of closed work — safe to grow, never goes stale.
   The **active** plan is derived per point 1, never listed here.)*
3. **Work lands on** the `feat/vertical-slice` branch off `main` (per `docs/AGENTS.md`).
4. **Setup / green-tree gate:** `pip install -e '.[dev]'` then `make check` (→ `pytest` +
   soft lint). Never dispatch a subagent or commit on a red tree.

`docs/design/` contains the authoritative spec for *how to build* the engine (architecture,
phasing). Read it before making architectural decisions — the architectural invariants
below were made deliberately and are easy to violate accidentally. The plan in
`docs/plans/` is the execution-level enrichment of the design docs for this slice.

## Two sources of truth (do not confuse them)

- **`docs/design/`** — the engine design (architecture, phasing, what to build). The engine
  is a **genre engine** — turn-based, board-game-like strategy games with tactical combat
  — with the 1986 "Mafia" as its reference title. It is *not* a generic game engine and
  *not* a single-title clone. A new game in the genre = copy `data/game_configs/mafia_1920s`
  and edit its data/handlers; the engine is untouched (`docs/design/product-and-scope.md` and `docs/design/config-and-content-contract.md`).
- **`../research/`** — the authoritative game *knowledge* (the reverse-engineered rules
  of the original 1986 C64 game "Mafia" by Igelsoft). **Never invent game behavior.**
  When a mechanic is unclear, read the decompiled BASIC line block it maps to; every
  research file cites BASIC line numbers. **For any game-behavior question — "how does X
  work", "is it true that Mafia does Y", "which BASIC line drives Z" — invoke the
  `mafia-oracle` skill; it answers from this research and is the preferred path over
  reading the raw files below.**

Supporting knowledge stores (not sources of truth):

- `docs/solutions/` — documented learnings from past work (bugs, architecture patterns,
  conventions), organized by category with YAML frontmatter (`module`, `tags`,
  `problem_type`). Relevant when implementing or debugging in documented areas.
- `CONCEPTS.md` — shared domain vocabulary (entities, named processes, status concepts).
  Relevant when orienting to the codebase or discussing domain concepts.

The `../research/` file layout (which YAML holds which rules, dialogue, text corpus,
etc.) is indexed in **`docs/research-map.md`** — consult it, or the `mafia-oracle`
skill, before opening raw research files. Don't reproduce that index here.

**Fidelity bar is behavioral, not bit-exact:** match the original's formulas,
probabilities, rewards, and outcomes exactly — but internal representation and RNG
draw-order may differ. A seeded run matches statistically/mechanically, not byte-for-byte.

## Architecture that spans multiple files (the big picture)

The engine is built around **one spine**: location logic is genuinely procedural (input
loops, 64 RNG sites, computed jump tables, minigames, mid-handler combat), so it cannot
live in flat data. Instead there are **two layers**:

1. **Declarative shell (YAML)** — menu structure + *guards* (preconditions). Pure data,
   mod-safe, covers the whole menu surface.
2. **Procedural handlers (Python)** — one generator function per menu action, referenced
   from the YAML by a string id in a `HANDLERS` registry.

**Handlers are generator coroutines**: `Generator[Interaction, Response, list[Event]]`.
A handler `yield`s a typed *interaction* (`ShowMessage`, `PromptInt`, `PromptChoice`,
`Confirm`, `StartCombat`, `LoadSubState`) and receives the response via `.send()`. A
**driver** advances the generator, turning each `yield` into a JSON-serializable screen
state. This same protocol is:
- the faithful expression of every procedural handler,
- the per-handler state machine,
- and — unchanged — the eventual **network message protocol** (the server is just an
  async transport driving the same generators).

**Consequence of the spine:** build the in-process synchronous driver first. The
WebSocket server is a *later, thin adapter over the identical protocol* — never build
networking before the driver is proven, and never let a handler depend on the transport.

Other cross-cutting invariants:

- **Layering:** the `engine/` package **imports nothing** from `server/`, `clients/`, or
  any transport/render library. Simulation is fully headless; presentation and transport
  depend on the engine, never the reverse (`engine-architecture.md` → "Layering rules").
- **Handler API is the only config interface:** a config's handlers may touch only
  `ctx.state` (read-only), `ctx.rng`, `yield <Interaction>`, `ctx.apply(<Effect>)`, and
  the named engine helpers — nothing else in `engine/`
  (`config-and-content-contract.md` → "Handler API"). This is what keeps configs portable
  across `engine_api` versions.
- **Interactions vs. Effects:** interactions drive execution flow (suspend to ask the
  client); effects mutate state. A handler never mutates state directly. Effects are pure
  data and double as the replay events (`engine-architecture.md` → "Events vs effects").
- **Guard DSL:** operators `= != >= <= > < in`; connectives `and`/`or`; nesting depth ≤2;
  **no NOT** (restructure to avoid). Verified sufficient for every real guard.
- **Strings:** zero hardcoded *display* text in the engine. The engine emits
  `(key, params)`; themes resolve them as parameterized templates
  (`"kostet das {price}$ miete"`). Handler *branching logic* stays in Python — only text
  is externalized.
- **RNG:** one seedable, loggable RNG from the start (`rng.hit(a,b)`, `rng.range(n)`);
  every call recordable so event-sourced replay is additive later.
- **Two distinct coordinate spaces — never conflate them.** They are different modes of
  play with different dimensions *and* different addressing:
  - **City map** — the 40×25 strategy board you move around between locations.
  - **Combat grid** — the 40×13 tactical board a fight happens on, addressed as *linear*
    cells `0..520` (= 13×40), not `(row, col)` pairs.
  A cell index is only meaningful together with which space it belongs to; the same
  integer means different tiles in each.

## Modding model (two independent axes)

- **Game config** = rules & world (locations, entity stats/prices, formula params, win
  conditions, handler registry). **Build-time, frozen per game.** May add
  content/handlers; may not change core simulation.
- **Theme** = presentation (text templates, images, sounds, renderer config).
  **Runtime-swappable**, deep-merged at the key level over the config's default theme.
  May not change rules or outcomes.

## Naming/state gotchas from the source

- The original's `kr(sp)` (per-player debt) collides with the gangster stat `kraft` —
  **give them distinct names** in the port.
- There are 12 menu locations **plus 2 map-triggered event flows** (`la=13` cash
  transport at cell 569, `la=14` mayor hit at cell 861) — no menu, they carry the two
  win flags `x5%`/`x6%`.
- `ln` (within-location tile index 1–9) is a **first-class handler input** (it changes
  rent price, which pub serves alcohol, racket outcomes) — not just an option index.
- `ms` (movement points) doubles as a **turn-end control signal**: handlers set `ms=0`
  to force the turn to end. The turn system must support handler-forced turn-end.

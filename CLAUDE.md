# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Implementation in progress — first vertical slice.** The project bootstrap (U0) is
done: there is a Python skeleton, a `pytest` + `Makefile` toolchain, and a durable
per-unit tracking board. Implementation runs unit-by-unit against a plan.

**If you are starting a work session, read `docs/AGENTS.md` first** — it is the
operating manual: the one-orchestrator/one-subagent execution model, the green-tree rule,
commit + branch conventions, and how to pick up the next unit. Then:

1. **The active plan** is `docs/plans/2026-07-12-001-design-first-vertical-slice-deepening-plan.md`
   (`ce-unified-plan/v1`, implementation-ready). Its § Execution Workflow and § Implementation
   Units (U0–U12) are the source of *what to build next*. Do not edit the plan body during
   execution — progress lives in git and the board, not the doc.
2. **The tracking board** is GitHub Issues on the `origin` remote (one open issue per
   unfinished unit, `dep:U<N>` labels). `gh issue list` shows the board; the next unit is the
   earliest in the serial order (`U0 → U3 → U2 → U1 → U4 → U5 → U6 → U8 → U7 → U9 → U11 → U12 → U10`)
   whose dependency issues are all closed. Close a unit's issue when its commit lands green.
   (If there is no remote, the board is `docs/PROGRESS.md` instead.)
3. **Work lands on** the `feat/vertical-slice` branch off `main` (per `docs/AGENTS.md`).
4. **Setup / green-tree gate:** `pip install -e '.[dev]'` then `make check` (→ `pytest` +
   soft lint). Never dispatch a subagent or commit on a red tree.

`PLAN.md` remains the authoritative spec for *how to build* the engine (architecture,
phasing). Read it before making architectural decisions — the decisions below were made
deliberately and are easy to violate accidentally. The plan in `docs/plans/` is the
execution-level enrichment of `PLAN.md` for this slice.

## Two sources of truth (do not confuse them)

- **`PLAN.md`** — the engine design (architecture, phasing, what to build). The engine
  is a **genre engine** — turn-based, board-game-like strategy games with tactical combat
  — with the 1986 "Mafia" as its reference title. It is *not* a generic game engine and
  *not* a single-title clone. A new game in the genre = copy `data/game_configs/mafia_1920s`
  and edit its data/handlers; the engine is untouched (`PLAN.md` §1, §6a).
- **`../research/`** — the authoritative game *knowledge* (the reverse-engineered rules
  of the original 1986 C64 game "Mafia" by Igelsoft). **Never invent game behavior.**
  When a mechanic is unclear, read the decompiled BASIC line block it maps to; every
  research file cites BASIC line numbers.

Key research files (all under `../research/`):

| File | Contains |
|---|---|
| `src/decompiled_basic/mf-prg.bas` | The authoritative logic (870 lines, 108 vars). Each Python handler is a port of a labeled line block (10000–26080). |
| `research-data/pass-2/location-handlers.yaml` | Per-menu-option behavior + guards — source for YAML shells and handler ids. |
| `research-data/pass-2/game-logic.yaml` | RNG outcome tables, combat AI (`ri()` direction memory), score/rank formulas. |
| `research-data/pass-2/systems-analysis.yaml`, `data-structures.yaml` | Combat/economy/wanted rules, the ~108 variables, grid dimensions. |
| `research-data/pass-1/location-extraction.yaml` | Menu trees + option→handler mapping. |
| `research-data/pass-2/location-dialogue.yaml` | **Dialogue source of truth** — each location's complete verbatim script (entry prompt + every option + the game's printed responses, each cited to `mf-prg.bas:<line>`). Port the exact strings from here. |
| `research-data/pass-1/game-text.yaml` | The full verbatim text corpus (all print/input/data/assign strings + SEQ menus). The authority for any on-screen string (narration, prompts, weapon/rank/vehicle/opponent names). |
| `docs/systems/*.md` | Human-readable system summaries. |

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
  depend on the engine, never the reverse (`PLAN.md` §6a).
- **Handler API is the only config interface:** a config's handlers may touch only
  `ctx.state` (read-only), `ctx.rng`, `yield <Interaction>`, `ctx.apply(<Effect>)`, and
  the named engine helpers — nothing else in `engine/` (`PLAN.md` §5.2a). This is what
  keeps configs portable across `engine_api` versions.
- **Interactions vs. Effects:** interactions drive execution flow (suspend to ask the
  client); effects mutate state. A handler never mutates state directly. Effects are pure
  data and double as the replay events (`PLAN.md` §5.5).
- **Guard DSL:** operators `= != >= <= > < in`; connectives `and`/`or`; nesting depth ≤2;
  **no NOT** (restructure to avoid). Verified sufficient for every real guard.
- **Strings:** zero hardcoded *display* text in the engine. The engine emits
  `(key, params)`; themes resolve them as parameterized templates
  (`"kostet das {price}$ miete"`). Handler *branching logic* stays in Python — only text
  is externalized.
- **RNG:** one seedable, loggable RNG from the start (`rng.hit(a,b)`, `rng.range(n)`);
  every call recordable so event-sourced replay is additive later.
- **Two coordinate spaces:** the city map is 40×25; the combat grid is 40×13. They are
  different spaces — never conflate them.

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

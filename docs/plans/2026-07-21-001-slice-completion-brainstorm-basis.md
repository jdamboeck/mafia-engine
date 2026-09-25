---
title: "Vertical Slice Completion — Brainstorm Basis"
type: brainstorm-input
date: 2026-07-21
topic: vertical-slice-completion
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
branch: feat/vertical-slice
status: input-for-brainstorm
---

# Vertical Slice Completion — Brainstorm Basis

> **This is a brainstorm INPUT, not a plan.** It gathers everything open, stubbed,
> deferred, or missing before the first vertical slice can merge to `main`. Feed it
> into `ce-brainstorm` (to scope what the slice actually needs) and then `ce-plan`.
> Nothing here is decided — the items are candidates with evidence, sized roughly.
> Every claim below was verified against the tree at HEAD `07f46c6`, **not** taken
> from a doc or a prior report.

## 0. Where the slice stands (verified)

- **Combat Engine Foundation plan (U1–U8 + U6a): fully done.** Issues #52–#60 closed;
  the plan's Definition of Done §12 is met. Amendments A1–A7 recorded.
- **Tree:** `feat/vertical-slice`, **995 tests pass, 1 skipped**, `make check` green
  (pytest + ruff, both hard). 134 commits ahead of `main`, 24 ahead of origin.
  **Never pushed, no PR. `main` is still clean.**
- **The game launches and plays end-to-end today:** `python -m clients.terminal --seed 42`
  renders the title, the map, lets you move, enter locations, and fight. Verified.
- **The gate (AGENTS.md):** *"`main` stays clean; the slice merges back when the
  Definition of Done holds."* The COMBAT plan's DoD holds. Whether the SLICE's DoD
  holds is the open question this doc exists to scope — see §1.

**The core tension:** this slice has extraordinary *depth* (attribute-agnostic engine,
recording/replay, a debug tool) but is missing some *end-to-end connective tissue* a
first vertical slice usually carries. A vertical slice's whole point is **one complete
path through every layer, start to a real outcome.** Several links in that path are
stubbed or unwired.

---

## 1. The defining question for the brainstorm

**What is the actual Definition of Done for the *slice* (not the combat plan)?**

The design doc (`engine-architecture.md:201`) says the slice's integration tests should
cover *"a seeded run through location, crime, wanted, arrest, and jail flows"* — but
**wanted / arrest / jail are stubbed** (§3.1). So either:

- (a) the slice's DoD is narrower than the design doc states, and we ratify a reduced
  scope explicitly, **or**
- (b) those flows are in scope and are real remaining work.

Every sizing decision below depends on answering this. **Recommend deciding (a) vs (b)
first, then this doc's items sort into "in the slice" vs "next slice."**

---

## 2. HIGH — the slice isn't fully "vertical" without these

### 2.1 A reachable win condition (biggest gap)
- `data/game_configs/mafia_1920s/content/map/city.yaml:178-179` declares BOTH win flows:
  `{cell: 569, la: 13, flow: cash_transport, win_flag: x5}` and
  `{cell: 861, la: 14, flow: mayor_hit, win_flag: x6}`.
- `GameState` has the slots: `engine/state/__init__.py:294-295` (`x5`, `x6`).
- **But `movement.py` STUBS the flows:** `:300` — *"la=13/14 event cells — OUT OF SCOPE
  this unit: detect and skip (no-op)."* Also `:112`, `:149`, `:196`.
- **Consequence: a player can move, fight, and earn money but can NEVER WIN.** The game
  only ends on a year limit (`advance_turn`'s `end_year` hook, `:392`).
- The combat + encounter machinery these flows need already exists (U6a declares
  encounters; U6 drives them). So one flow is likely "wire the trigger + the win-flag
  set + the end-game check," not new combat work.
- **Candidate:** wire at least ONE of the two flows end-to-end so "you can win" is
  demonstrable. Decide whether both are in-slice.

### 2.2 Save/load is unreachable by a player
- The engine HAS it: `engine/persistence.py` — `save_game` (`:288`), `load_game`
  (`:337`), `state_from_dict` (`:244`). The immutable state graph round-trips;
  recording/replay share the same `json_safe` path. (CLAUDE.md marks U12 landed.)
- **The terminal client never calls them:** `clients/terminal/__main__.py` imports
  none of the persistence functions; there is no save key, no `--load`, no menu.
- A proven capability is stranded. **Candidate:** wire a save/load key into the turn
  loop (and/or a `--load PATH` flag), with a tests covering the round-trip through the
  client.

### 2.3 A lose condition
- There is **no failure end-state**: grep finds no bankruptcy / death / jail-out /
  game-over-by-failure. The only game end is the year limit (§2.1).
- A vertical slice usually lets you both win AND lose. **Candidate:** decide whether a
  lose condition (e.g. bankruptcy, or losing a mandatory fight with no recovery) is
  in-slice, and if so wire it to the same end-game hook as the win flags.

---

## 3. MEDIUM — intended-scope flows that are stubbed

### 3.1 Wanted / arrest / jail (named in the design doc's slice tests)
- `engine/effects.py`: `WantedChange` (`:336`) and `Jail` (`:345`) both **raise
  `NotImplementedError`** — *"Deferred — application in a later unit."*
- `FlagSet` non-`"global"` scope also raises `NotImplementedError` (`:741-743`) —
  per-player flag bitfields are a later unit. (4 `NotImplementedError` sites total in
  `engine/`+`data/`.)
- The upkeep jail gate is a documented no-op: `handlers/upkeep.py:225` — *"jail is
  declared-but-stubbed (KTD-7) and nothing can imprison a player."*
- **This is the (a)-vs-(b) decision from §1.** If the design doc's slice-test scope
  (crime/wanted/arrest/jail) is authoritative, these are real remaining work; if the
  slice is narrower, ratify that and defer them cleanly.

### 3.2 Location surface — 5 of 12 built
- CLAUDE.md: *"12 menu locations plus 2 map-triggered event flows."*
- **Built (YAML shell + handler):** `kdh`, `pub`, `slw`, `sph`, `waf` (5). Registered
  player actions: pub.recruit/drink/tip/job, slw.rent, sph, kdh.borrow/repay/trade/
  capital/collect, waf.buy/train, job.shift, upkeep.
- The design doc's Phase-1 slice (`engine-architecture.md:188`) only promised *"slw and
  one guarded pub neighbor"* — so 5 locations may already EXCEED the intended slice.
  **Decision needed:** is 5 the slice's target, or all 12? (Likely 5+ is fine; confirm
  and write it down so "12 locations" in CLAUDE.md doesn't read as unfinished work.)

---

## 4. Open bug issues (pre-existing; plan explicitly DEFERRED all three)

The combat plan's frontmatter `defers: ["#45", "#49", "#51"]`; §7.2 gives rationale.
None block the combat DoD, but they're open on the branch.

### 4.1 #49 — two vacuous `test_debt_default` "win" tests
- `_StubRng` built with ZERO scripted values → the fight never resolves; assertions pass
  because *nothing happened* (verified in the issue). The test named "win changes
  nothing" never reaches a win — it would pass even if the win branch were deleted.
- Plan: **"Trivial after U6"** — U6/KTD-6's zero-variance weapon makes a real
  deterministic win cheap now. **Candidate:** fix both tests to actually resolve a win;
  prove red first. *Small.*

### 4.2 #51 — miscovered EOF test
- `test_eof_during_sph_wager_prompt` fires EOF at the MAP loop, not mid-handler, so the
  mid-handler EOF path is uncovered (another vacuous-test shape, like #48/#49).
- Plan: the issue's own hypothesis is **disproven**; *"probe where input is actually
  consumed before editing keys."* Unrelated to combat. *Small, needs investigation.*

### 4.3 #45 — no observation frame between AI activations
- CPU activations resolve without yielding a `CombatScreen`, so a human watching sees
  enemies "teleport." Plan ruled this **faithful, not a defect** (`mf-prg.bas:30110`'s
  CPU path has no key read). U6's driver abstraction + U7's recording make an opt-in
  observation frame trivial if wanted.
- **Candidate:** either close as won't-fix (faithful) OR add the optional frame.
- **Rider:** `dir_memory` is 0-based while the source's `ri(i)` is 1-based — matters
  only if persistence ever serializes `dir_memory` (§2.2 makes that reachable).

---

## 5. First-slice hygiene currently absent (unrecorded anywhere)

### 5.1 No CI
- There is a `Makefile` with `make check` but **no `.github/workflows/`** — the
  green-tree gate runs only locally. Merging 134 commits to `main` with no CI is risky.
- **Candidate:** a ~15-line workflow: `pip install -e '.[dev]'` + `make check` on push/PR.

### 5.2 README doesn't tell a human how to PLAY
- The Quickstart is `pip install` + `make check` — how to *test*, not *play*. Nothing
  documents `python -m clients.terminal --seed 42` (which launches the game) or the
  fightlab tool (`clients/terminal/FIGHTLAB.md` exists but isn't linked).
- **Candidate:** add a "How to play" block + link FIGHTLAB.md + a short "what works
  today" capability list (move, locations, buy/train, jobs, fight, watch recordings).
  The README currently undersells a genuinely playable slice.

### 5.3 No top-level error handling at the client boundary
- No visible catch-and-message around the turn loop — a config typo or bad save would
  dump a raw traceback at a player. First slices usually guard the client entry point.
- **Candidate:** a top-level try/except at `clients/terminal/__main__.py:main` that
  prints a clean message instead of a stack trace.

### 5.4 Truecolor ANSI tests may be host-env-sensitive (rider on §5.1)
- The palette/renderer suites assert explicit truecolor sequences (`\033[38;2;...`).
  `ColorSupport` detection reads the host's `COLORTERM`/`TERM`; on a runner where
  `COLORTERM` isn't `truecolor`/`24bit`, emitted codes fall back to 256-color and the
  asserts break. Not observed locally (`make check` green), but the failure surfaces
  first in CI. *(Raised by the external review; verify before relying on it.)*
- **Candidate (rider on §5.1):** when the CI workflow lands, confirm the ANSI tests
  pin `ColorSupport.TRUECOLOR` (mock/override) rather than read the runner's env.
  *Trivial if it bites; skip if the tests already force support.*

---

## 6. Deferred by the combat plan — explicitly NEXT-SLICE, not this one

Captured so the brainstorm can consciously keep them OUT (plan §7.2 defers them with
rationale; they should reuse the boundary combat just proved):

- **Engine-wide blueprint boundary.** DoD #1 holds *for combat*, but real game-stat
  names remain in engine CODE (not just docstrings): `engine/effects.py:48`
  `_STAT_NAMES = ("kraft","intelligenz","brutalitaet")`; `engine/types/__init__.py:142`
  `req_kraft: int` (WeaponInstance dictates this game's 3 stats). Plus `engine/effects.py`
  carries ~40 game-vocab hits (`RentAccrue`, `BarrelChange`, `TipSet`, `DebtClear`,
  `JobSet`, `Jail`, `WantedChange`, `ShopChange`) and `engine/state/` holds game
  entities (`Wanted`, `MapState`, `Clock`, `Business`, `Contraband`, `Debt`, `Job`,
  `Flags`) with zero engine reads. **A separable, larger unit — reuse §2.2/§2.3's rule.**
  - *Possible sub-lift:* the `WeaponInstance` stat-gate (`req_int/req_kraft/req_brut`,
    `engine/types/__init__.py:141-143`) may be liftable on its own, ahead of the full
    engine-wide extraction — same disease (this game's 3 stats named in engine types),
    smaller blast radius. Still next-slice, still gated on the second-config moment;
    noted only so it isn't lost inside the big unit. *(Raised by the external review.)*
  - *Note on the "replace with generic primitives" framing:* the review that surfaced
    this proposed swapping typed effects for `ModifyValue(path, delta)`. **Reject that
    mechanism** — it trades the typed/validated/replay-versioned effect catalog for
    stringly-typed path mutation and weakens the replay contract. The *observation*
    (game vocab in engine code) is right; the extraction target is config-registered
    **typed** effects, not path-poking. Decide the seam as a `docs/design/` amendment,
    not a cleanup refactor.
- **PvP fight initiation from the map/turn loop.** U6 makes human-vs-human *expressible*
  (two human drivers) but not *initiable* — needs turn-loop + game-rules decisions
  (how triggered? loser's roster? atomic commits across two players?). Deliberately out.

---

## 7. Ship-the-slice mechanics (once §1–§5 land)

- **Re-confirm green + fidelity** on the full branch immediately before the PR (135
  commits accumulated; DoD §12.13).
- **Delete the stale scratch artifact** `docs/plans/2026-07-20-003-NEXT-STEPS.md` — a
  U2-era handoff marked *"delete once consumed"*; all its units are done. *Trivial.*
- **Open the PR** `feat/vertical-slice` → `main`; PR body covers the combat arc
  (U1–U8 + U6a) and references amendments A1–A7. The PR-level commit carries the
  attribution footer (AGENTS.md: incremental commits stay clean).
- **Full-slice end-to-end smoke** before merge: drive a real session through the
  terminal client — map → enter a location → trigger a real fight → resolve → (ideally)
  reach a win via §2.1 — not just the per-unit checks.
- Consider a **`v0.1` slice tag / release note** capturing what shipped.

---

## 8. Suggested brainstorm agenda (the decisions to make)

1. **Ratify the slice DoD** — narrow (combat + 5 locations + a win path) vs wide
   (design doc's crime/wanted/arrest/jail). This gates everything. (§1, §3.1)
2. **Win path:** one flow or both? (§2.1) **Lose condition:** in-slice or not? (§2.3)
3. **Save/load:** wire it now (capability exists, just unwired)? (§2.2)
4. **Bug issues:** fix #49 now (trivial-after-U6), close #45 as faithful, investigate
   #51 — or carry all three as post-merge follow-ups? (§4)
5. **Hygiene:** CI + README-how-to-play + client error guard before merge? (§5)
6. **Confirm the exclusions** (engine-wide boundary, PvP init) stay next-slice. (§6)

Once these are decided, `ce-plan` can turn the "in-slice" set into ordered units on the
same one-orchestrator/one-subagent, proof-first, green-tree workflow the combat plan
used.

---
title: "Next Slice — Brainstorm Basis"
type: brainstorm-input
date: 2026-10-16
topic: next-slice
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
execution: code
status: input-for-brainstorm
---

# Next Slice — Brainstorm Basis

> **This is a brainstorm INPUT, not a plan.** It is the newest file in `docs/plans/` so
> that the Engine Contracts plan (`2026-10-15-001-refactor-engine-contracts-plan.md`,
> U1–U15, closed #189–#203) reads as done. Nothing here is decided; each item needs a
> brainstorm and a plan before anyone builds it. It replaces the previous basis
> (`2026-10-12-002`), whose open items are carried below.

## Where the engine stands

The whole game plays in the terminal client for 1–4 hot-seat players. Since the last basis:
- One config is live per process: a handle to a replaced config raises `StaleConfigError`
  when it saves, loads, replays, builds a turn runner or runs an owned action; a refused
  load restores the registries (`engine/config_owner.py`).
- Content is read once at the config package's import; a running game never rereads it.
- Saves are checkpoints (snapshot + RNG history at the turn menu or map prompt), keep map
  key types, refuse an invalid state before writing, and are schema 3 (older saves are
  refused). `resume_pending_action` and `pending_action` are gone.
- A dry input source raises `InputExhausted`; a substate may return before its first
  yield; city maps other than 40×25 and positional menus over 9 options are refused.
- The intent arm of `c64_float_score` keeps one exact score (`exact_score`) and the typed
  weight; `gf` is derived.
- The interaction protocol lives in `engine/protocol.py`; `play()` takes `PlayOptions`;
  the engine's `WeaponInstance` holds only `name` and `range`.
- Tests read a vendored copy of the BASIC and the dialogue corpus (`tests/fixtures/research/`),
  so CI runs every port test; a research skip fails the run. The coverage ledger is a
  citation audit held per printed line; shell tests resolve the keys their runs emit.

## Open issues

- **#176** — watch the end-to-end runs in the real terminal client (the demo request).
- **#178** — roadmap: network/server transport and a second game config.
- **#204** — the title screen's `:151-153` lines are not shown (deferred in the ledger).
- **#205** — `c64_int_divide` floors two negative-quotient VICE cases one lower than the ROM.

## Left out of the Engine Contracts slice on purpose

- Per-config isolated registries and several different configs at once (#178); direct
  `handlers`/`effects` access through a stale handle is unchecked until then. Shells from
  an old handle keep their load-time handler functions after a same-path reload.
- A read-only state view projected through buffered effects (#157-5); handlers keep their
  local tracking (police `_Run`, upkeep, `roster_after`).
- Mid-handler save/resume, event-sourced whole-game replay, an RNG-state checkpoint
  (`Rng.replayed` re-draws the whole history on load; load time of a long save unmeasured).
- Separate version policies for saves and combat recordings (one `SCHEMA_VERSION` today).
- General board geometry; wheel packaging and installed-artifact play.
- #158 A8: response normalization differs per prompt type, and `HandlerFunc` still
  describes a list return; a serializable outward interaction envelope for a second
  transport.
- #158 A10: lazy-import cycles among actions, interactions, fight loop and recording.
- #177: comment and docstring volume (about 46% of non-blank lines).

## Fidelity items carried from the 2026-10-12-002 basis

**Menus and keys (U1)**
- The weapon shop's venue and the safe still print option labels the source does not:
  `:13105` prints only `(s)chiesstand oder (t)rainingscamp ?`, and `:20115` reads F1/F3/F5 with
  no menu text of its own.
- Modified function keys (`ESC [1;2P` and the like) are not mapped; only the plain F1/F3/F5
  sequences are.
- A burst of keys typed at once keeps the rest for later reads, where a C64 `getx$` loop
  would see them one by one from the keyboard buffer; not compared against the source.

**Screens and text**
- Texts the source prints with several `PRINT`s (the pub job and recruit, kdh, the job shifts,
  tip 4's trailing blank row) are joined with one space; the source breaks the rows. (U3)
- More `{rvon}` texts are not in the reverse list: the `:25000` shift header, `:30235`
  `verfehlt!`, `:30350` `treffer!`; the `:30115` fighter panel is drawn plain; the human label
  is not kept on observe frames, whose payload lacks the last human fighter. (U4)
- A `--theme` without its own `renderer/reverse.yaml` draws plain instead of merging over the
  classic list, so a strings-only mod theme loses reverse video. (U4)

**Input**
- `val("- 3")` at the `x$` prompts (kdh `:15015`/`:15051`/`:15208`, the lawyer `:26060`, the
  thank-you `:21252`) is assumed to read -3 like `INPUT`; not captured in VICE. Blanks between
  digits (`1 2` read as 12) are not ported. (U6)

**Found by the branch's code review (left open)**
- Between one-key reads the terminal is back in cooked mode with echo on, so a key typed
  ahead is echoed onto the drawn screen before the next read takes it.
- A key whose character takes several UTF-8 bytes is read a byte at a time on a terminal
  and becomes replacement characters; it answers no prompt either way.
- A lone ESC followed within 50 ms by a typed-ahead key is read as one unknown sequence,
  and that key is lost.
- The gang war's own number (`:27017 ...orus=spgoto27015`) is asked again by the handler,
  which reprints `mit wem willst du dich anlegen:`; the source waits at `getx$` silently.
- Freeing a prisoner (`:21120`/`:21125 g=val(x$):ifg=0thenreturn`): on the C64 a letter key
  is `val` 0 and leaves; the one-key client ignores letters. The departure is recorded for
  the car dealer and the gang war, not here.
- A future network or agent client must map keys to option indexes itself
  (`answer_keys[i]`, or the number `i + 1`, to `i`), as the terminal client does.

## Recorded departures (in `content/house_rules.yaml`'s header and the handler docstrings)

- The turn-over screen after every turn, the `:30030` banner pause, setup's re-ask of a weight
  like `2a` and the location splash's Enter (added by U9).
- The turn-start upkeep messages share one screen and one key (`handlers/upkeep.py`).
- Fractional and empty answers at the prompts listed in the header.
- After a lost fight, capture receives `p=0` where the source's `p` is stale.
- Theme stand-ins for picture-only or sound-only screens.
- The stale `kf$` backdrop at `:15312`, the always-drawn second miss factor at `:30247`, and the
  debug switches at `:1208` and `:315`.

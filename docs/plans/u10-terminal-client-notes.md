# U10 Terminal Client — POV notes (deferred behind sph/waf)

Status: **deferred** — U10 sits behind the sph/waf protocol-stress run and depends on it
(U10's own verification names "string resolution works for slw/pub **+ new sph/waf
content**"). These notes correct an external-AI spec against the frozen engine contracts so
the real work starts from truth, not from the spec's memory-derived API. Do not pick up U10
while the sph/waf run is open.

## Verdict on the external spec: partial adopt — keep the shape, rewrite the core

The thin-client *philosophy* matched the design docs; the *mechanism* was built against an
engine API that does not exist. Verified against source this session.

### Keep
- Thin client: no state, no rules, protocol-driven (already the design mandate).
- String externalization as a principle.
- Layering direction (client imports engine types; engine never imports client) — enforced
  by `test_headless_no_clients_import` (`tests/test_slice_integration.py:376`).
- Debug mode dumping `EngineResult` (status/events/effects/payload).
- Module split (renderer / input / display) — once re-pointed at the real API.

### Discard / correct (each verified against `file:line`)
- **Main loop.** The spec's `play_game()` does `next(driver_gen)` / `driver_gen.send()` on
  the driver. Real API: `run(handler, input_source, *, state, rng) -> EngineResult`
  (`engine/interactions.py:212`). The client is an **`input_source` callable** the driver
  pulls from — inverted control flow. The spec's whole §1 loop is unbuildable.
- **`ShowMessage`** is **auto-acked** by the driver without consulting the input_source
  (`engine/interactions.py:324`; note at `tests/test_slice_integration.py:96`). The client
  does not prompt on it.
- **Interaction signatures are wrong.** Real: `PromptInt(key, min, max, cancellable)`,
  `PromptChoice(key, options: list, cancellable)`, `Confirm(key)`. **No `params`** on any
  prompt — only `ShowMessage(key, params)` (`engine/interactions.py:60-96`). Spec's
  `prompt_key`/`min_val`/`max_val`/`params`/`options: list[dict]` are all phantom.
- **Cancel.** Client sends the `CANCEL` *input* sentinel; the driver throws `Cancelled`
  *into* the handler (`engine/interactions.py:152,262`). Do not send `Cancelled`.
- **Resolver placement.** Spec puts string resolution in `clients/terminal/renderer.py`.
  Themes are config-owned and every future client (server, pygame) needs resolution →
  belongs in a **shared headless resolver**, not the terminal client.
- **Screen clear.** `os.system('cls'/'clear')` is an anti-pattern → use raw ANSI `\033[2J`.
- **`clients/terminal/__init__.py` already exists** (empty); the spec proposed creating the
  tree from scratch.

## Decisions (this POV)

1. **Main-loop design — derive from contracts, ignore the spec.** The terminal client is
   **two callables + a REPL**, holding no generator logic of its own:
   - `input_source(interaction) -> response` — the client's ONLY coupling to the handler
     protocol. Renders the prompt via the resolver, reads one `input()`, returns the typed
     response or `CANCEL`. The driver owns the loop / validation / re-prompt / cancel-throw.
     This is the existing test `recorder` (`tests/test_slice_integration.py:159`) promoted
     to real I/O.
   - `render(result: EngineResult)` — between actions, render status + events + a status bar
     off `result.state`.
   - map REPL — read a key, call `try_move` / `run_option`, **adopt `result.state`**,
     render, loop until `ms<=0` / turn end.
   This shape is forced by the frozen `input_source` + `EngineResult` contracts — there is
   exactly one shape that respects them.

2. **Resolver — shared headless resolver.** Client-agnostic `(key, params) -> text` over
   the config's theme YAMLs, consumed by the terminal client and reused by server/pygame.
   Not client-local. Must stay headless (no import from `clients/`).

3. **TUI deps — ANSI escapes, no new dependency.** Assessed: `rich` saves ~20-40 lines for
   this flat prompt/message/menu surface but adds a dependency and pollutes stdout capture
   (harder assertions) — not worth it. `curses` is stdlib but a different paradigm that
   fights the input_source model and is untestable under piped stdin — negative value here.
   Raw ANSI (`\033[2J`, color escapes) is ~15 lines of constants, stays stdlib-only, keeps
   stdout assertable, and delivers the retro color/clear. Best ratio. Revisit only if a
   real full-screen TUI is wanted later.

## When U10 resumes
Start from the `recorder` in `tests/test_slice_integration.py`, not from the external spec.
Build the shared resolver first (it unblocks both the client and eventual server), then the
`input_source` + `render` + map REPL, then ANSI polish, then tests (mocked stdin).

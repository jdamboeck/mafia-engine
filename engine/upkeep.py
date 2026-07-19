"""The turn-start upkeep runner — ENGINE mechanism, config-driven body (U3, KTD-3).

Per-turn upkeep (``mf-prg.bas:4000-4090``) runs through the SAME generator/interaction/
effect protocol as a location handler — it is not special-cased machinery, just another
generator the driver drives. What IS special-cased is *who calls it and when*: **the
engine owns the coupling**. :func:`run_upkeep` is the one engine-level turn-start entry
point; no transport gets to rotate a player onto their free turn without it running
first. A client (or a future server) calls this, never decides on its own whether to.

The upkeep generator itself is **config-owned game code** — registered by the game
config under :data:`UPKEEP_HANDLER_KEY` in :data:`engine.locations.HANDLERS` (the SAME
registry location handlers use; upkeep is not a location, but it is still one handler id
among many, and reusing the registry avoids a second parallel registration mechanism).
This module only supplies the generic *runner*: it looks up the registered generator,
drives it via :func:`engine.interactions.run`, and returns the resulting
:class:`~engine.actions.EngineResult` unchanged for the caller to adopt
(``state = result.state``, mirroring every other pure driver call in this codebase).

Upkeep issues no cancellable prompts (per the plan's Verification Contract: "upkeep
interactions offer no cancel path"). Its only interactions are ``ShowMessage`` (the
turn banner, the promotion screen) which the driver auto-acks without consulting the
input source at all — so :func:`run_upkeep`'s default ``input_source`` is a callable
that raises if anything ever calls it, catching a future handler that adds a real
prompt to upkeep without updating this contract.

``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

from typing import Any

from engine.actions import EngineResult
from engine.interactions import run

__all__ = ["UPKEEP_HANDLER_KEY", "run_upkeep"]

#: The registry key the game config's upkeep generator is registered under, in the
#: SAME :data:`engine.locations.HANDLERS` registry a location option's ``handler``
#: string resolves against (KTD-3: one generator/interaction/effect protocol, one
#: registry — upkeep is not a location, but it is still just another handler id).
UPKEEP_HANDLER_KEY = "upkeep.turn_start"


def _refuse_input(interaction: Any) -> Any:
    """The FALLBACK ``input_source`` for upkeep: fail loudly if ever consulted.

    Upkeep's non-combat steps yield only ``ShowMessage`` (auto-acked by the driver —
    never consults the input source) per the plan's no-cancel-path contract. This
    remains the default so a caller that passes no ``input_source`` still surfaces any
    accidental prompt at the call site rather than silently answering it with a
    fabricated response.

    U12 added the one legitimate exception: the debt-default collectors fight
    (``mf-prg.bas:4350``) is a ``StartCombat`` sub-protocol, and combat activations DO
    require real per-activation input. A caller whose player may be in default must
    therefore pass a real ``input_source``. That does not reopen a cancel path —
    combat prompts are non-cancellable by KTD-9 (the client's quit vocabulary maps to
    surrender, which LOSES the fight rather than discarding the flow), so upkeep still
    cannot be escaped, only lost.
    """
    raise AssertionError(
        f"upkeep asked the input source for a response to {interaction!r}; "
        "upkeep must offer no cancel/prompt path (Verification Contract) — "
        "ShowMessage is auto-acked by the driver and never reaches here"
    )


def run_upkeep(
    state: Any,
    *,
    input_source: Any = None,
    rng: Any = None,
    handlers: dict | None = None,
) -> EngineResult:
    """Run the active player's turn-start upkeep flow and return its result.

    This is THE engine-level turn-start entry point (KTD-3): a client's turn loop calls
    this before presenting the free turn (or a future job shift, U10), rather than
    deciding for itself whether upkeep runs — the coupling lives here, not in the
    client.

    Looks up the config's registered generator under :data:`UPKEEP_HANDLER_KEY` in
    ``handlers`` (defaults to the live :data:`engine.locations.HANDLERS` registry —
    the config populates it at load time via ``@register``), then drives it exactly
    like any other handler via :func:`engine.interactions.run`. Effects commit
    atomically on clean completion (the same commit-or-discard contract every handler
    gets); upkeep offers no cancel path, so ``result.status`` is always
    ``"completed"`` in practice, never ``"cancelled"`` — including through the U12
    collectors fight, whose surrender loses the fight without discarding the flow.

    Args:
        state: The game state upkeep runs against (the ACTIVE player, per
            ``state.clock.active_player`` — the generator reads this off ``ctx.state``
            exactly as a location handler would).
        input_source: The client's response callback, forwarded to the driver.
            Defaults to :func:`_refuse_input`, which raises if consulted — correct for
            every upkeep step EXCEPT the U12 collectors fight, which needs real combat
            input. Callers that can reach a debt default (the terminal client's turn
            loop) must pass a real one.
        rng: The session RNG (threaded through so an upkeep slot — debt, shop income,
            arms deal — can draw from it).
        handlers: Override registry (test seam); defaults to the live
            :data:`engine.locations.HANDLERS`.

    Returns:
        The :class:`~engine.actions.EngineResult` from driving the upkeep generator.
        The caller MUST adopt ``result.state`` (this function is pure, like every
        other driver entry point in this codebase).

    Raises:
        KeyError: if no config has registered :data:`UPKEEP_HANDLER_KEY` (a config
            bug — every game config using this engine must register an upkeep
            handler, even a trivial one, since :func:`run_upkeep` is unconditionally
            wired into the turn-start seam).
    """
    if handlers is None:
        from engine.locations import HANDLERS as handlers  # noqa: N811 - local alias

    factory = handlers.get(UPKEEP_HANDLER_KEY)
    if factory is None:
        raise KeyError(
            f"no handler registered under {UPKEEP_HANDLER_KEY!r}; every game config "
            "must register a turn-start upkeep generator (even a no-op one) since "
            "engine.upkeep.run_upkeep is unconditionally called at turn start"
        )
    return run(factory, input_source or _refuse_input, state=state, rng=rng)

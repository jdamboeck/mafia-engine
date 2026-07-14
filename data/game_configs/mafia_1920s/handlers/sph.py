"""The sph (Spielhoelle / casino) handler — U4, the warm-up handler.

A faithful port of the casino block ``mf-prg.bas:16010-16040``. The three games —
poker (``x=1``), black jack (``x=2``), roulette (``x=3``) — are mechanically identical
except for ``x``, which sets both the win probability and the payout multiplier (verified
``16030`` is the sole resolver for all three), so one code path parameterized by the
chosen game index is faithful (A3).

KTD-7 conformance: touches ONLY ``ctx.state`` (read-only), ``ctx.rng``, ``yield
<Interaction>``, and ``ctx.apply(<Effect>)`` — nothing else in ``engine/``. It never
mutates state directly and emits no content-specific events (KTD-6): the casino outcome
is fully reconstructable from the committed ``MoneyChange`` + the logged RNG draw.

The negative-EV house edge (``1/(1+x)`` win chance vs a ``0.5+x`` gross multiplier) is
faithful and must NOT be "corrected" (behavioral fidelity, CLAUDE.md).

Cash math (verified against source)
-----------------------------------
``16026`` deducts the stake up front (``ka -= p``) and ``16040`` re-adds the gross payout
on a win (``ka += p`` where ``p`` was reassigned to the gross). So the NET delta is
``payout - stake`` on a win and ``-stake`` on a loss. Modelled as a single
``MoneyChange`` per resolved bet.
"""

from __future__ import annotations

from engine.effects import MoneyChange
from engine.interactions import PromptChoice, PromptInt, ShowMessage
from engine.locations import register

__all__ = ["sph"]

#: Sane upper bound on the wager PromptInt (the original reads a free int; a cap keeps
#: the prompt well-formed without altering behavior for real inputs — mirrors slw).
_MAX_WAGER = 10_000_000


@register("sph")
def sph(ctx):
    """Play a casino game at the Spielhoelle — ports ``mf-prg.bas:16010-16040``.

    Steps (faithful to the BASIC line block):

    1. ``16010-16016`` — pick a game (poker/black jack/roulette). Cancelling the menu
       (original ``x=0``) aborts the whole action via the driver's atomic discard. The
       0-based choice maps to the game index ``x = choice + 1`` (poker=1..roulette=3).
    2. ``16020`` — show cash, ask for a wager. ``wager <= 0`` is a quiet abort (return
       with no effect). The ``PromptInt`` min is 0 so 0 reaches the handler as the abort.
    3. ``16025`` — affordability: ``wager > ka`` emits ``not_enough_money`` and aborts.
    4. ``16026-16040`` — resolve with ONE rng draw: win iff ``rng.range(1+x) == 0``. On a
       win the gross payout is ``int(stake*(offset+x))`` (offset 0.5 from config), and the
       net cash delta is ``payout - stake``; on a loss the delta is ``-stake``. Emit the
       win/loss message.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    params = ctx.state.config.formula_params
    payout_offset = params["casino_payout_offset"]

    # 16010-16016 — game menu; cancelling aborts the whole action (driver discard).
    choice = yield PromptChoice(
        "locations.sph.game_menu",
        options=[
            "locations.sph.poker",
            "locations.sph.blackjack",
            "locations.sph.roulette",
        ],
        cancellable=True,
    )
    x = choice + 1  # game index: poker=1, black jack=2, roulette=3

    # 16020 — show cash, ask for a wager; wager <= 0 is a quiet abort.
    yield ShowMessage("locations.sph.cash", {"cash": active.ka})
    stake = yield PromptInt("locations.sph.wager_prompt", min=0, max=_MAX_WAGER)
    if stake <= 0:
        return []

    # 16025 — affordability.
    if stake > active.ka:
        yield ShowMessage("system.not_enough_money")
        return []

    # 16026-16040 — resolve with a single rng draw.
    yield ShowMessage("locations.sph.at_the_table")
    if ctx.rng.range(1 + x) == 0:
        payout = int(stake * (payout_offset + x))  # gross incl. returned stake
        ctx.apply(MoneyChange(payout - stake))  # net win = payout - stake
        yield ShowMessage("locations.sph.won", {"amount": payout})
    else:
        ctx.apply(MoneyChange(-stake))  # 16026 stake gone, no re-add
        yield ShowMessage("locations.sph.lost")
    return []

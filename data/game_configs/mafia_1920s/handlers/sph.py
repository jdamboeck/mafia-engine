"""The sph (Spielhoelle / casino) handler.

A faithful port of the casino block ``mf-prg.bas:16010-16040``. The three games —
poker (``x=1``), black jack (``x=2``), roulette (``x=3``) — are mechanically identical
except for ``x``, which sets both the win probability and the payout multiplier (verified
``16030`` is the sole resolver for all three), so one code path parameterized by the
chosen game index is faithful.

Handler-API conformance: touches ONLY ``ctx.state`` (read-only), ``ctx.rng``, ``yield
<Interaction>``, and ``ctx.apply(<Effect>)`` — nothing else in ``engine/``. It never
mutates state directly and emits no content-specific events: the casino outcome
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
from engine.interactions import READ_LINE, PromptChoice, ShowMessage
from engine.locations import register

from ..house_rules import C64_INPUT_NEGATIVES, intent, number_prompt
from ..setup import KEY_WAIT

__all__ = ["sph"]

#: Sane upper bound on the wager PromptInt (the original reads a free int; a cap keeps
#: the prompt well-formed without altering behavior for real inputs — mirrors slw).
_MAX_WAGER = 10_000_000
#: The stake prompt's floor under faithful ``c64_input_negatives``: the C64 INPUT at
#: ``:16020`` takes a negative stake, which the same line turns away quietly.
_ANY_NEGATIVE = -(2**31)


@register("sph")
def sph(ctx):
    """Play a casino game at the Spielhoelle — ports ``mf-prg.bas:16010-16040``.

    Steps (faithful to the BASIC line block):

    1. ``16010-16016`` — pick a game (poker/black jack/roulette). Cancelling the menu
       (original ``x=0``) aborts the whole action via the driver's atomic discard. The
       0-based choice maps to the game index ``x = choice + 1`` (poker=1..roulette=3).
    2. ``16020`` — show cash, ask for a wager. ``wager <= 0`` is a quiet abort (return
       with no effect). The prompt's floor is 0 under intent ``c64_input_negatives``
       (a negative is asked again) and below every negative under faithful.
    3. ``16025`` — affordability: ``wager > ka`` emits ``not_enough_money`` and aborts.
    4. ``16026-16040`` — resolve with ONE rng draw: win iff ``rng.range(1+x) == 0``. On a
       win the gross payout is ``int(stake*(offset+x))`` (offset 0.5 from config), and the
       net cash delta is ``payout - stake``; on a loss the delta is ``-stake``. Emit the
       win/loss message.

    A played hand and the broke refusal end in the key wait (``:1100``), reached by
    ``goto1100`` and ``goto1125``: the handler yields ``KEY_WAIT``. Choosing nothing
    (``:16015 ifx=0thenreturn``) and a wager of 0 return without one.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    params = ctx.state.config.formula_params
    payout_offset = params["casino_payout_offset"]

    # 16010-16016 — game menu; cancelling aborts the whole action (driver discard).
    # :16015 ``input"{down}was soll's sein:";x:ifx=0thenreturn`` reads a line (Enter),
    # and 0 returns: a client relays it as cancel.
    # The C64 INPUT at :16015 takes a fraction, and 2.5 passes :16016's 1-3 check and
    # plays with x=2.5; the menu offers the three games only (a departure, noted in
    # content/house_rules.yaml).
    choice = yield PromptChoice(
        "locations.sph.game_menu",
        options=[
            "locations.sph.poker",
            "locations.sph.blackjack",
            "locations.sph.roulette",
        ],
        cancellable=True,
        read=READ_LINE,
        prompt="locations.sph.game_prompt",
    )
    x = choice + 1  # game index: poker=1, black jack=2, roulette=3

    # 16020 — the cash and the question are one line, the stake read under it; wager
    # <= 0 is a quiet abort (``return``, no key wait; nor does choosing nothing at
    # :16015 wait).
    # The C64 INPUT takes a fractional stake; the prompt reads whole numbers only. A
    # negative stake reaches the abort under faithful ``c64_input_negatives``; under
    # intent it is asked again, and 0 still leaves.
    floor = 0 if intent(ctx.state, C64_INPUT_NEGATIVES) else _ANY_NEGATIVE
    stake = yield number_prompt(
        ctx.state,
        "locations.sph.wager_prompt",
        min=floor,
        max=_MAX_WAGER,
        params={"cash": active.ka},
    )
    if stake <= 0:
        return []

    # 16025 — affordability.
    if stake > active.ka:
        yield ShowMessage("system.not_enough_money")
        yield KEY_WAIT  # :16025 goto1125 -> :1125 ...:goto1100
        return []

    # 16026-16040 — resolve with a single rng draw.
    # :16030 `int(rnd(1)*(1+x))=0` wins; the payout is :16030 `p=int(p*(.5+x))`.
    yield ShowMessage("locations.sph.at_the_table")
    if ctx.rng.range(1 + x) == 0:
        payout = int(stake * (payout_offset + x))  # gross incl. returned stake
        ctx.apply(MoneyChange(payout - stake))  # net win = payout - stake
        yield ShowMessage("locations.sph.won", {"amount": payout})
    else:
        ctx.apply(MoneyChange(-stake))  # 16026 stake gone, no re-add
        yield ShowMessage("locations.sph.lost")
    # :16035 print"{down}leider verloren!":goto1100 / :16040 ...:goto1100 -- the hand's
    # result stays on screen until a key.
    yield KEY_WAIT
    return []

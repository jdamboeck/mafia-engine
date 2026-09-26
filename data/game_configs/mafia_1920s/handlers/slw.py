"""The slw (Schlupfwinkel / motel) rent handler.

A faithful port of the shared rent block ``mf-prg.bas:10020-10045``. In the
original, BOTH menu option 1 ("rent a room") and option 2 ("pay/extend rent",
re-entering at ``:10105``) jump to this same block; the difference between them
is only which SHELL guard admits you (room-free vs you-are-the-tenant). So this
one generator serves both options.

The GUARDS live in the location shell (``content/locations/slw.yaml``), not here —
the shell owns guard evaluation and denial: ``uk(ln)!=0`` gates renting,
``uk(ln)!=sp`` gates paying — the shell only enters this handler once the guard
passes, so the handler body is purely the rent block.

Handler-API conformance: this handler touches ONLY ``ctx.state`` (read-only),
``yield <Interaction>``, ``ctx.apply(<Effect>)``, and the named ``fnm`` helper —
nothing else in ``engine/``. It never mutates state directly.

This handler is **config-owned game code**: it registers against the engine's
generic ``@register`` decorator (an engine API) but imports ``fnm`` from its OWN
config's setup module (``..setup``) — a handler depends on its own config, not on
the engine.

The ``ln`` seam
---------------
The handler needs the within-location tile index ``ln`` (it changes the rent
price via ``fnm``). It reads ``ln`` from the active player's ``last_location``
field, which door entry (``engine.movement``, via ``SetEntryContext``) records
before the location's menu runs.
"""

from __future__ import annotations

from engine.effects import MoneyChange, RentAccrue, SetTenancy
from engine.interactions import PromptInt, ShowMessage
from engine.locations import register

from ..setup import fnm

__all__ = ["slw_rent"]

#: Sane upper bound on months for the PromptInt (the original reads a free int;
#: a cap keeps the prompt well-formed without altering behavior for real inputs).
_MAX_MONTHS = 999


@register("slw.rent")
def slw_rent(ctx):
    """Rent / pay-rent at the current slw tile — ports ``mf-prg.bas:10020-10045``.

    Steps (faithful to the BASIC line block):

    1. ``p = fnm(ln)`` — per-tile monthly rent (``:10020``), via the ``fnm`` helper
       and the ``formula_params.fnm`` config block (``fnm(1) == 150`` premium,
       ``fnm(3) == fnm(4) == 100``, else 50).
    2. Quote the rent (``rent_quote``) and ask for a month count (``:10025``).
    3. ``:10030`` — ``x <= 0`` is a quiet abort: return immediately with NO effects
       (atomic by construction, since nothing was applied yet).
    4. ``:10035`` — affordability: if ``ka < x*p`` emit ``not_enough_money`` and
       return with no deduction.
    5. ``:10040-10045`` — success: deduct ``x*p``, set tenancy ``uk(ln)=sp``, accrue ``um(sp)+=x``, then greet.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location  # the ln seam (see module docstring)

    fnm_params = ctx.state.config.formula_params["fnm"]
    p = fnm(ln, fnm_params)  # :10020 — per-tile monthly rent

    # :10025 — quote the rent, then ask how many months.
    yield ShowMessage("locations.slw.rent_quote", {"price": p})
    x = yield PromptInt("locations.slw.months_prompt", min=0, max=_MAX_MONTHS)

    # :10030 — "ifx=0orx<0thennm=1:return": zero/negative months = quiet abort.
    if x <= 0:
        return []

    # :10035 — affordability.
    if active.ka < x * p:
        yield ShowMessage("system.not_enough_money")
        return []

    # :10040-10045 — success. Deduct rent,
    # set tenancy uk(ln)=sp, accrue prepaid months um(sp)+=x, then greet.
    ctx.apply(MoneyChange(-x * p))
    ctx.apply(SetTenancy(ln))
    ctx.apply(RentAccrue(x))
    yield ShowMessage("locations.slw.success")
    return []

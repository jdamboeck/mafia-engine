"""The slw (Schlupfwinkel / motel) rent handlers.

A faithful port of ``mf-prg.bas:10000-10105``. In the original, BOTH menu option 1
("rent a room", ``:10010``) and option 2 ("pay/extend rent", ``:10100``) end in the
same rent block ``:10020-10045``; they differ only in the check that comes first:

* ``slw.rent`` -- ``:10010 ifuk(ln)<>0thenprint"'nichts mehr frei!'":goto1100``: a
  room anyone holds is not for rent, the renter's own included.
* ``slw.pay_rent`` -- ``:10100 ifuk(ln)<>spthenprint"du wohnst hier nicht!"``: only
  the tenant pays.

Both checks run INSIDE the handler, after the option was picked, as in the source:
the menu (``content/locations/slw.yaml``) always offers all three options (``:3030``
prints the file's ``aw`` options with no precondition). The source's players are
1-based, so its vacant ``uk(ln)=0`` cannot collide with a player; this port's are
0-based, so a vacant room reads ``None`` from :func:`~..state.tenant`, never 0.

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

from engine.effects import MoneyChange
from ..effects import RentAccrue, SetTenancy
from engine.interactions import PromptInt, ShowMessage
from engine.locations import register

from ..setup import fnm
from ..state import tenant

__all__ = ["slw_rent", "slw_pay_rent"]

#: Sane upper bound on months for the PromptInt (the original reads a free int;
#: a cap keeps the prompt well-formed without altering behavior for real inputs).
_MAX_MONTHS = 999


@register("slw.rent")
def slw_rent(ctx):
    """Rent the room at the current slw tile -- ports ``mf-prg.bas:10010-10045``.

    ``:10010``: a room anyone holds refuses (``locations.slw.no_room``), then the rent
    block runs (:func:`_rent_block`).
    """
    ln = ctx.state.players[ctx.state.clock.active_player].last_location
    if tenant(ctx.state, ln) is not None:  # :10010 ifuk(ln)<>0
        yield ShowMessage("locations.slw.no_room")
        return []
    return (yield from _rent_block(ctx))


@register("slw.pay_rent")
def slw_pay_rent(ctx):
    """Pay rent for the room at the current slw tile -- ports ``mf-prg.bas:10100-10105``.

    ``:10100``: anyone but the tenant is refused (``locations.slw.not_resident``);
    ``:10105 goto10020`` sends the tenant through the same rent block.
    """
    sp = ctx.state.clock.active_player
    ln = ctx.state.players[sp].last_location
    if tenant(ctx.state, ln) != sp:  # :10100 ifuk(ln)<>sp
        yield ShowMessage("locations.slw.not_resident")
        return []
    return (yield from _rent_block(ctx))


def _rent_block(ctx):
    """The shared rent block -- ports ``mf-prg.bas:10020-10045``.

    Steps (faithful to the BASIC line block):

    1. ``p = fnm(ln)`` — per-tile monthly rent (``:10020``), via the ``fnm`` helper
       and the ``formula_params.fnm`` config block (``fnm(1) == 150`` premium,
       ``fnm(3) == fnm(4) == 100``, else 50).
    2. Quote the rent (``rent_quote``) and ask for a month count (``:10025``).
    3. ``:10030`` — ``x=0orx<0`` (``x <= 0``) is a quiet abort: return immediately with NO
       effects (atomic by construction, since nothing was applied yet).
    4. ``:10035`` — affordability: if ``ka < x*p`` emit ``not_enough_money`` and
       return with no deduction.
    5. ``:10040-10045`` — success: deduct ``x*p``, set tenancy ``uk(ln)=sp``, accrue ``um(sp)+=x``, then greet.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location  # the ln seam (see module docstring)

    fnm_params = ctx.state.config.formula_params["fnm"]
    p = fnm(ln, fnm_params)  # :10020 `p=fnm(ln)` — per-tile monthly rent

    # :10025 — quote the rent, then ask how many months.
    yield ShowMessage("locations.slw.rent_quote", {"price": p})
    x = yield PromptInt("locations.slw.months_prompt", min=0, max=_MAX_MONTHS)

    # :10030 — "ifx=0orx<0thennm=1:return": zero/negative months = quiet abort.
    if x <= 0:
        return []

    # :10035 — affordability, `ka(sp)<x*p`.
    if active.ka < x * p:
        yield ShowMessage("system.not_enough_money")
        return []

    # :10040-10045 — success. Deduct rent (:10040 `ka(sp)=ka(sp)-x*p`),
    # set tenancy uk(ln)=sp, accrue prepaid months um(sp)+=x, then greet.
    ctx.apply(MoneyChange(-x * p))
    ctx.apply(SetTenancy(ln))
    ctx.apply(RentAccrue(x))
    yield ShowMessage("locations.slw.success")
    return []

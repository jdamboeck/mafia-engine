"""The sgl (Einfacher Laden, protection racket) handlers — ports ``mf-prg.bas:17000-17592``.

``:17010 onwgoto17015,17100,17200,17300``: four ways to squeeze the shopkeeper behind a
guardless shell (``content/locations/sgl.yaml``), and a leave. The menu (``:3040``)
comes first, and leave (``:3045 ifw=awthenms=ms-5:return``) goes back to the map
before ``:17000`` is reached, so leaving meets neither check below. Each of the four
options then runs the same prologue, in the source's order:

1. ``:17005 ifra(sp)>1goto17007`` / ``:17006`` — a rank-1 player is sent away.
2. ``:17007 ifll(sp)<>20*la+lngoto17010`` / ``:17008-17009`` — the revisit trap: when
   the player's last location visit this turn (``ll(sp)``, written by the turn runner
   after every visit, ``:2055``, and cleared at the turn start, ``:1012``) was this
   very tile, the police wait outside: ``kf$="ks":goto26000``, the police fight.

Then the option (``ln`` is the tile, 1..9; "the boss" is gangster 1, ``a=sp:b=1``):

- ``sgl.threat`` (``:17015-17020``) — the boss needs brutality 30 (``:17016``) and the
  tile must be 1, 7 or 9 (``:17017``); otherwise the shopkeeper calls the police
  (``:17020``, the police fight).
- ``sgl.sob_story`` (``:17100-17105``) — works on tiles 1, 4 and 5.
- ``sgl.protection`` (``:17200-17225``) — works on tiles 2, 3 and 8. Elsewhere Narben-
  Jack's gang fights (``:17210``, 3 men, 5 against a gang of more than 5); lost, the
  player just goes (``:17215``); won, the payout follows.
- ``sgl.fake_police`` (``:17300-17306``) — tiles 1, 3 and 6, and the boss needs
  intelligence 30.

The payout (``:17500-17592``) scores +2 first (``:17500``), then 1 time in 3 pays
100..199 (``:17530``) and otherwise 800..999 plus 300 on tile 2, 200 on tiles 7 and 9,
and 600 LESS when ``w=2`` (``:17505`` ``+600*(w=2)``: C64 true is -1, so the term is
-600, while the tile terms ``-300*(ln=2)`` add); ``w`` also picks the reply
(``:17510``). The money
is credited at once (``:17550``), before the player picks what next: take it, wreck
the shop (the thugs fight on tiles 2, 6, 7 and 8; won or on another tile, the till's
300..399, ``:17575``) or finish the owner (he fights on tiles 1 and 4; won or on another
tile, his 200..299, ``:17590``). A lost thug or owner fight returns with the payment
kept (``:17573``, ``:17588``). After Jack's fight, the thugs or the owner meet the gang
as that fight left it: every hit is stored at once (``:30265`` ``gosub1365``).

``w`` after Jack's fight
------------------------
``w`` is the option number (1..4) — except after a won Jack fight. The fight sets
``w=gw(ks(s),f)`` at every shot (``:30215``), so ``w`` then holds the weapon of the
gangster who fired the last, killing shot, and that weapon picks the reply and the
-600 (a knueppel, weapon 2, takes it off; a weapon outside 1..4 falls through
``:17510``'s ``onwgoto`` to the first reply). The engine's :class:`~engine.combat.CombatResult`
reports that shooter. House rule ``jack_fight_reply_by_killing_weapon``, faithful-only.

Two more notes. ``:17516 ll(sp)=0`` after the sob-story reply is dead: ``:2055``
overwrites ``ll(sp)`` when the visit returns, so the port writes nothing there. And the
source's gang is never empty (``gz(sp)`` only grows, and the eviction sets it to 1,
``:4651``); a port state with no gangster, where there is no boss to read, leaves the
shop quietly with nothing changed.

The key wait (``:1100``) comes where the source reaches it: every refusal
(``goto1100``), each warning before a fight (``gosub1100``), Jack's leaving before the
payout (``:17221``) and the wrecked shop or finished owner (``:17578 ...goto1100``).
Taking the money (``:17565 return``) and a lost thug, owner or Jack fight
(``ifs=2thenreturn``) return with no wait beyond the fight outcome screen's own.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import MoneyChange
from engine.interactions import PromptChoice, ShowMessage
from engine.locations import register

from ..setup import (
    KEY_WAIT,
    load_encounter,
    load_weapons,
    roster_after,
    run_encounter,
    score_and_rank,
)
from .police import Arrest, police_fight

__all__ = [
    "revisit_trap",
    "sgl_fake_police",
    "sgl_protection",
    "sgl_sob_story",
    "sgl_threat",
]

_CONFIG_DIR = Path(__file__).resolve().parents[1]
_ENCOUNTERS = _CONFIG_DIR / "content" / "encounters"

#: Narben-Jack's gang (``:17210``): variant 0 is 3 men, variant 1 is 5.
_JACK = load_encounter(_ENCOUNTERS / "sgl_jack.yaml")
#: The thugs (``:17573``) and the shopkeeper (``:17587``).
_THUGS = load_encounter(_ENCOUNTERS / "sgl_thugs.yaml")
_OWNER = load_encounter(_ENCOUNTERS / "sgl_owner.yaml")

#: ``kf$="ks"`` before each ``goto26000`` (``:17009``, ``:17020``).
_POLICE_GRID = "ks"

#: ``:17510 onwgoto17511,17515,17520,17525`` — the reply for ``w`` 1..4; any other
#: ``w`` falls through to ``:17511``.
_REPLIES = {
    1: "locations.sgl.reply_pays",
    2: "locations.sgl.reply_sob",
    3: "locations.sgl.reply_enough",
    4: "locations.sgl.reply_forged",
}

#: ``:17550-17551`` — what next, keys 1..3 (``:17555``), in the source's order.
_AFTER = ("locations.sgl.after_take", "locations.sgl.after_demolish", "locations.sgl.after_kill")
_TAKE, _DEMOLISH, _KILL = range(3)


def _prologue(ctx):
    """``:17005-17009``: the rank gate, then the revisit trap. Returns whether the
    visit is over."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    if not active.roster:  # see the module docstring: no boss, nothing to do
        return True
    # :17005 ``ifra(sp)>1goto17007`` / :17006 ``print"'verschwinde, du milchgesicht!'"``
    if active.rank <= params["sgl_rank_floor"]:
        yield ShowMessage("locations.sgl.milksop")
        yield KEY_WAIT  # :17006 ...:goto1100
        return True
    # :17007 ``ifll(sp)<>20*la+lngoto17010``
    return (yield from revisit_trap(ctx))


def revisit_trap(ctx):
    """The revisit trap, ``:17007-17009``, shared with the bank (``:20004
    ifll(sp)=20*la+lngoto17008``). Returns whether it sprang (the visit is then over).

    When the previous tile this turn (``ll(sp)``) is this very tile, the police wait
    outside: ``:17008-17009`` "vor dem laden erwartet dich die poliyei!" (the bank shows
    the shop's text too), then ``kf$="ks":goto26000``, the police fight.
    """
    active = ctx.state.players[ctx.state.clock.active_player]
    if active.previous_tile != (active.last_la, active.last_location):
        return False
    yield ShowMessage("locations.sgl.police_waiting")  # :17008-17009
    yield KEY_WAIT  # :17009 ...:gosub1100, before the police
    yield from police_fight(ctx, Arrest(), grid=_POLICE_GRID)  # :17009 goto26000
    return True


@register("sgl.threat")
def sgl_threat(ctx):
    """'Den Zaster her...' — ports ``mf-prg.bas:17015-17020``."""
    if (yield from _prologue(ctx)):
        return []
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    # :17015 ``a=sp:b=1:gosub1350`` — the boss's stats.
    boss = active.roster[0]
    # :17016 ``ifbt<30thenprint"du schindest keinen eindruck...":print:goto17020``
    if boss.attrs["brutalitaet"] < params["sgl_threat_brutalitaet"]:
        yield ShowMessage("locations.sgl.no_impression")
    elif active.last_location in params["sgl_threat_tiles"]:  # :17017
        yield from _extort(ctx, 1)
        return []
    # :17020 ``print"der kerl ruft die polizei!!!":gosub1100:kf$="ks":goto26000``
    yield ShowMessage("locations.sgl.calls_police")
    yield KEY_WAIT  # :17020 ...:gosub1100, before the police
    yield from police_fight(ctx, Arrest(), grid=_POLICE_GRID)
    return []


@register("sgl.sob_story")
def sgl_sob_story(ctx):
    """'Bitte, bitte...' — ports ``mf-prg.bas:17100-17105``."""
    if (yield from _prologue(ctx)):
        return []
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    if active.last_location in params["sgl_sob_tiles"]:  # :17100
        yield from _extort(ctx, 2)
        return []
    yield ShowMessage("locations.sgl.sob_refused")  # :17105
    yield KEY_WAIT  # :17105 ...:goto1100
    return []


@register("sgl.protection")
def sgl_protection(ctx):
    """'Wir schuetzen dich...' — ports ``mf-prg.bas:17200-17225``."""
    if (yield from _prologue(ctx)):
        return []
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    if active.last_location in params["sgl_protection_tiles"]:  # :17200
        yield from _extort(ctx, 3)
        return []
    yield ShowMessage("locations.sgl.jack_warning")  # :17205-17206
    yield KEY_WAIT  # :17206 ...:gosub1100, before the fight
    # :17210 ``gz(0)=3-2*(gz(sp)>5):w=7:e=30:kf$="ksgl":gosub5000``
    big = len(active.roster) > params["sgl_jack_big_gang"]
    result = yield from run_encounter(ctx, _JACK, variant=1 if big else 0)
    if result.winner == 2:  # :17215 ``ifs=2thenreturn`` -- no wait beyond :30520's
        return []
    yield ShowMessage("locations.sgl.jack_leaves")  # :17220-17221
    yield KEY_WAIT  # :17221 ...:gosub1100, then :17225 goto17500
    # :17225 ``goto17500`` with ``w`` as the fight left it (:30215): the last shooter's
    # weapon; :17210's ``w=7`` if nobody fired.
    shooter = result.last_shooter
    w = _JACK.variants[0].weapon if shooter is None else shooter.weapon
    # The thugs or the owner after the payment meet the gang as Jack's fight left it:
    # every hit is stored at once (:30260 ``gosub1350:en=en-y``, :30265 ``gosub1365``),
    # while this fight's write-back is still buffered.
    yield from _extort(ctx, w, roster=roster_after(active.roster, result))
    return []


@register("sgl.fake_police")
def sgl_fake_police(ctx):
    """'Polizei! ...Falschgeld' — ports ``mf-prg.bas:17300-17306``."""
    if (yield from _prologue(ctx)):
        return []
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    # :17300 ``a=sp:b=1:gosub1350:if(ln=1orln=3orln=6)andin>=30goto17500``
    boss = active.roster[0]
    if (
        active.last_location in params["sgl_police_tiles"]
        and boss.attrs["intelligenz"] >= params["sgl_police_intelligenz"]
    ):
        yield from _extort(ctx, 4)
        return []
    yield ShowMessage("locations.sgl.fake_police_refused")  # :17305-17306
    yield KEY_WAIT  # :17306 ...:goto1100
    return []


def _extort(ctx, w: int, *, roster=None):
    """The payout and what follows — ``:17500-17592``; ``w`` as :17505 reads it.

    ``roster`` is the gang a fight after the payment starts from: the one an earlier
    fight in this action left (:func:`~..setup.roster_after`), or ``None`` for the
    gang as ``ctx.state`` holds it.
    """
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    ln = active.last_location

    # :17500 ``x=2:gosub1160:ifint(rnd(1)*3)=0goto17530`` — the score comes first.
    ctx.apply(score_and_rank(params["sgl_score"], params))
    if ctx.rng.range(params["sgl_small_roll"]) == 0:
        # :17530 ``p=int(rnd(1)*100)+100:print"{clr}{down}'ich habe leider nur"p"$!"``
        p = ctx.rng.range(params["sgl_small_spread"]) + params["sgl_small_min"]
        yield ShowMessage("locations.sgl.reply_small", {"p": p})
    else:
        # :17505 ``p=int(rnd(1)*200)+800-300*(ln=2)-200*(ln=7)-200*(ln=9)+600*(w=2)``:
        # C64 true is -1, so each tile term adds, and ``+600*(w=2)`` takes 600 off.
        p = ctx.rng.range(params["sgl_pay_spread"]) + params["sgl_pay_min"]
        for tile, amount in zip(params["sgl_bonus_tiles"], params["sgl_bonus_amounts"]):
            if ln == tile:
                p += amount
        if w == params["sgl_sob_weapon"]:
            p += params["sgl_sob_amount"]  # +600*(w=2) is 600*(-1)
        # :17510 ``onwgoto17511,17515,17520,17525``; any other ``w`` falls through.
        yield ShowMessage(_REPLIES.get(w, _REPLIES[1]), {"p": p})
        # :17516 ``ll(sp)=0`` (after the w=2 reply) is dead: :2055 overwrites it.

    # :17550 ``ka(sp)=ka(sp)+p`` — credited before the choice, kept whatever follows.
    ctx.apply(MoneyChange(p))
    # :17550-17560 ``getx$:ifx$<"1"orx$>"3"goto17555``: only 1..3 is read.
    choice = yield PromptChoice("locations.sgl.after_menu", options=list(_AFTER))
    if choice == _DEMOLISH:
        yield from _demolish(ctx, ln, roster)
    elif choice == _KILL:
        yield from _kill_owner(ctx, ln, roster)
    # :17565 ``return`` — the money taken, with no key wait.


def _demolish(ctx, ln: int, roster):
    """Wreck the shop — ``:17570-17578``."""
    params = ctx.state.config.formula_params
    # :17570 ``ifln<>2andln<>6andln<>7andln<>8goto17575``
    if ln in params["sgl_demolish_tiles"]:
        yield ShowMessage("locations.sgl.thugs_called")  # :17571-17572
        yield KEY_WAIT  # :17572 ...:gosub1100, before the fight
        # :17573 ``bn$(0)="schlaeger":gz(0)=5:w=3:e=20:kf$="ksgl":gosub5000:ifs=2thenreturn``
        result = yield from run_encounter(ctx, _THUGS, roster=roster)
        if result.winner == 2:
            return  # :17573 ...:ifs=2thenreturn -- no wait beyond :30520's
    # :17575 ``p=int(rnd(1)*100)+300``
    p = ctx.rng.range(params["sgl_demolish_spread"]) + params["sgl_demolish_min"]
    yield ShowMessage("locations.sgl.demolished", {"p": p})  # :17576-17577
    _settle(ctx, p)
    yield KEY_WAIT  # :17578 ...:goto1100


def _kill_owner(ctx, ln: int, roster):
    """Finish the shopkeeper — ``:17580-17592``."""
    params = ctx.state.config.formula_params
    # :17580 ``ifln<>1andln<>4goto17590``
    if ln in params["sgl_kill_tiles"]:
        # :17585-17586 ``...und laedt seine "wa$(7)"!"``
        weapon = load_weapons(_CONFIG_DIR / "entities" / "weapons.yaml")[_OWNER.variants[0].weapon]
        yield ShowMessage("locations.sgl.owner_arms", {"weapon": weapon["name"]})
        yield KEY_WAIT  # :17586 ...:gosub1100, before the fight
        result = yield from run_encounter(ctx, _OWNER, roster=roster)  # :17587
        if result.winner == 2:  # :17588 ``ifs=2thenreturn`` -- no wait beyond :30520's
            return
    # :17590 ``p=int(rnd(1)*100)+200``
    p = ctx.rng.range(params["sgl_kill_spread"]) + params["sgl_kill_min"]
    yield ShowMessage("locations.sgl.owner_dead", {"p": p})  # :17591-17592
    # :17592 ``x=1:gosub1160:goto17578`` — a score before :17578's own.
    ctx.apply(score_and_rank(params["sgl_settle_score"], params))
    _settle(ctx, p)
    yield KEY_WAIT  # :17578 ...:goto1100


def _settle(ctx, p: int) -> None:
    """``:17578 ka(sp)=ka(sp)+p:x=1:gosub1160:goto1100``."""
    params = ctx.state.config.formula_params
    ctx.apply(MoneyChange(p))
    ctx.apply(score_and_rank(params["sgl_settle_score"], params))

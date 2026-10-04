"""The ban (Bank/Postamt) handlers — ports ``mf-prg.bas:20000-20060``.

``:20005 onwgoto20009,20100``: two options behind a guardless shell
(``content/locations/ban.yaml``), and a leave (``:3045``), which goes back to the map
before ``:20000`` is reached. Both options first run the same prologue, in the source's
order (``la=10``; ``ln`` is the tile, 1..5):

1. ``:20003 ifra(sp)<3thenprint"werde erst '"ra$(3)"'!":goto1100`` — a player below
   rank 3 is told the rank to reach (its name, "kleiner fisch").
2. ``:20004 ifll(sp)=20*la+lngoto17008`` — the revisit trap: the shop's
   (:func:`.sgl.revisit_trap`, ``:17008-17009``), text and police fight on "ks" included.

``ban.holdup`` (``:20009-20015``) — the daytime hold-up:

- ``:20009 ifgz(sp)=1thenprint"du brauchst einen begleiter!"`` — the boss alone is
  refused (the source tests exactly 1; the gang is never empty, see below).
- ``:20010 ifint(rnd(1)*3)=0goto20050`` — 1 time in 3 the guards miss it and the payout
  follows at once; 2 times in 3 ``:20011-20012`` "leider hast du die drei wachmaenner am
  eingang uebersehen...", and the guards fight: ``gz(0)=3-(ln=1)`` (C64 true is -1, so
  4 on tile 1, 3 elsewhere; the text says "drei" either way, house rule
  ``bank_guards_text_says_three``), ``:20015 bn$(0)="wachmaenner":e=30:w=6:kf$="kb"``
  (``content/encounters/ban_guards.yaml``).
- Lost, ``ifs=2goto26020``: the arrest, with no police fight. Nothing was taken before
  the fight, so capture sees the cash as it was. The ``p`` the fight leaves is not
  reported, so the arrest gets 0, as after a lost police fight (``handlers/police.py``).
- Won (or not fought), ``:20050``: :func:`heist_payout`, with ``+500`` on tile 1 and the
  pub's tip 2 ("die bank an der hauptstrasse", ``:12235``) paying only on tile 2.

``ban.safe`` (``:20100-20150``) — the night safe-crack, after the same prologue
(``:20003-20004`` come before ``:20005``'s dispatch):

- ``:20100 a=sp:b=1:gosub1350:ifin>=40andkr>=15andbt>=20goto20102`` — the **boss's**
  stats gate it, before anyone is picked; ``:20101`` "du musst noch trainieren!"
  otherwise (house rule ``safe_gate_checks_the_boss``).
- ``:20104`` "wer soll den kasten knacken:" and the picker (``:1130``); ``ify=0then
  return`` leaves. The picker's ``:1155 gosub1350`` loads the cracker's stats, so from
  here on ``in`` is the cracker's.
- ``:20105-20107`` the stethoscope text, then the minigame, the :func:`safe_crack`
  sub-state (``:20110-20135``), which returns :data:`CRACKED` or :data:`FAILED`.
  Sub-states may not start fights, so this handler acts on the outcome:
- cracked, ``:20150 x=-1:gosub1160:goto20050``: the score dip, then the bank's payout
  (as the hold-up's, ``:20050-20060``, with its own ``x=4:gosub1160``) — two score
  effects in the source's order, each clamped to 0..100 on its own (``:1160-1161``);
- failed, ``:20141-20142`` "'teufel...! da ist was schiefgegangen! es kommt jemand!'",
  then ``kf$="kb":goto26000``: the police fight on "kb". The ``p`` capture gets is the
  one the source holds there: nothing in option 2 sets it, nor does the location menu
  (``:3000-3045``), so it is the map step's ``:2030 p=br+po(sp)+x``, 52224 plus the
  bank tile's door cell.

The source's gang is never empty (``gz(sp)`` only grows, and the eviction sets it to 1,
``:4651``); a port state with no gangster, where there is nobody to fight, leaves the
hold-up quietly with nothing changed once the prologue has passed. At the safe, the
source's ``gosub1350`` on an empty ``ge$(sp,1)`` reads every stat as ``val("")`` = 0,
so a port state with no boss is told to train.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import MoneyChange
from engine.interactions import LoadSubState, PromptChoice, ShowMessage
from engine.locations import register
from engine.substates import register_substate

from ..effects import SafeSkillSet, TipClear
from ..setup import load_encounter, load_ranks, pick_gangster, run_encounter, score_and_rank
from ..state import safe_skill, tip_target
from .police import Arrest, caught, police_fight
from .sgl import revisit_trap
from .sub import door_cells

__all__ = ["CRACKED", "FAILED", "ban_holdup", "ban_safe", "heist_payout", "safe_crack"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The guards at the door (``:20012-20015``): variant 0 is 3 men, variant 1 (tile 1) 4.
_GUARDS = load_encounter(_CONFIG_DIR / "content" / "encounters" / "ban_guards.yaml")

#: The police fight's backdrop after a failed crack (``:20142 kf$="kb"``).
_POLICE_GRID = "kb"

#: The safe-crack sub-state's outcomes.
CRACKED = "cracked"
FAILED = "failed"

#: The three dial keys (``:20115``: F1, F3, F5 are PETSCII 133-135; :20116 ``x=x-133``).
_DIAL_KEYS = ("locations.ban.safe_f1", "locations.ban.safe_f3", "locations.ban.safe_f5")


def _prologue(ctx):
    """``:20003-20004``: the rank gate, then the revisit trap. Returns whether the visit
    is over."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    rank = params["ban_rank_min"]
    # :20003 ``ifra(sp)<3thenprint"werde erst '"ra$(3)"'!":goto1100``
    if active.rank < rank:
        ranks = load_ranks(_CONFIG_DIR / "entities" / "ranks.yaml")
        yield ShowMessage("locations.ban.rank_too_low", {"rank": ranks[rank - 1]})
        return True
    # :20004 ``ifll(sp)=20*la+lngoto17008``
    return (yield from revisit_trap(ctx))


@register("ban.holdup")
def ban_holdup(ctx):
    """The daytime hold-up — ports ``mf-prg.bas:20009-20015``, then ``:20050-20060``."""
    if (yield from _prologue(ctx)):
        return []
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    ln = active.last_location
    if not active.roster:  # see the module docstring: nobody to fight
        return []
    # :20009 ``ifgz(sp)=1thenprint"du brauchst einen begleiter!":goto1100``
    if len(active.roster) == params["ban_alone_gang"]:
        yield ShowMessage("locations.ban.alone")
        return []
    # :20010 ``ifint(rnd(1)*3)=0goto20050``
    if ctx.rng.range(params["ban_fight_roll"]) != 0:
        yield ShowMessage("locations.ban.guards")  # :20011-20012, then :1100's key
        # :20012 ``gz(0)=3-(ln=1)``; :20015 ``bn$(0)="wachmaenner":e=30:w=6:kf$="kb":
        # gosub5000:ifs=2goto26020``
        variant = 1 if ln == params["ban_main_tile"] else 0
        result = yield from run_encounter(ctx, _GUARDS, variant=variant)
        if result.winner == 2:
            yield from caught(ctx, Arrest(p=0))
            return []
    yield from _bank_payout(ctx)
    return []


def _bank_payout(ctx):
    """``:20050-20060`` from the bank (``la=10``), the tile's terms filled in."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    ln = active.last_location
    # :20050 ``-500*(la=10andln=1)``: C64 true is -1, so +500 on tile 1.
    # :20051 ``(x=2andla=10andln=2)``: the pub's tip 2 pays on tile 2 only.
    yield from heist_payout(
        ctx,
        tip_bonus=tip_target(active) == params["ban_tip"] and ln == params["ban_tip_tile"],
        adjust=params["ban_main_bonus"] if ln == params["ban_main_tile"] else 0,
    )


@register("ban.safe")
def ban_safe(ctx):
    """The night safe-crack — ports ``mf-prg.bas:20100-20150``; see the module docstring."""
    if (yield from _prologue(ctx)):
        return []
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    # :20100 ``a=sp:b=1:gosub1350:ifin>=40andkr>=15andbt>=20goto20102`` — the boss's
    # stats (an empty ``ge$(sp,1)`` reads as 0 each).
    boss = active.roster[0].attrs if active.roster else {}
    if not (
        boss.get("intelligenz", 0) >= params["ban_safe_intelligenz"]
        and boss.get("kraft", 0) >= params["ban_safe_kraft"]
        and boss.get("brutalitaet", 0) >= params["ban_safe_brutalitaet"]
    ):
        yield ShowMessage("locations.ban.safe_untrained")  # :20101, then :1100's key
        return []
    # :20104 ``print"...wer soll den kasten knacken:":gosub1130:ify=0thenreturn``
    yield ShowMessage("locations.ban.safe_who")
    y = yield from pick_gangster(ctx, cancellable=True)
    if y is None:
        return []
    # :20105-20107 the stethoscope, then :1100's key.
    yield ShowMessage("locations.ban.safe_stethoscope")
    # :20110-20135 the minigame; :1155 ``gosub1350`` loaded the cracker's ``in``.
    outcome = yield LoadSubState(
        "safe_crack", {"intelligenz": active.roster[y].attrs["intelligenz"]}
    )
    if outcome == CRACKED:
        # :20150 ``x=-1:gosub1160:goto20050`` — the dip, then the payout's ``x=4``.
        ctx.apply(score_and_rank(params["ban_safe_score"], params))
        yield from _bank_payout(ctx)
        return []
    # :20141-20142 ``print"...'teufel...!..."es kommt jemand!'":gosub1100:kf$="kb":
    # goto26000`` — ``p`` is the map step's (:2030 ``p=br+po(sp)+x``); see the module
    # docstring.
    yield ShowMessage("locations.ban.safe_failed")
    door = door_cells()[(active.last_la, active.last_location)]
    yield from police_fight(ctx, Arrest(p=params["map_screen_base"] + door), grid=_POLICE_GRID)
    return []


@register_substate("safe_crack")
def safe_crack(ctx, params):
    """The safe-crack minigame — ports ``mf-prg.bas:20110-20135``.

    ``params["intelligenz"]`` is the cracker's ``in``; the tile ``ln`` and the
    safecracker bonus ``s9(sp)`` are read off ``ctx.state``. Returns :data:`CRACKED`
    or :data:`FAILED`.

    - ``:20110 rd(i)=1+i:cd(i)=int(rnd(1)*10)`` — the dials start at 1, 2, 3 (the
      ``trs1`` screen shows them), and the code is three draws of 0..9.
    - ``:20111 y=20+int(in/10)+3*(ln=1)+s9(sp):s9(sp)=s9(sp)-1:ifs9(sp)<0thens9(sp)=0``
      — the tries (C64 true is -1: 3 fewer on tile 1); the bonus is used up by one,
      win or lose.
    - ``:20115 getx$:x=asc(x$+chr$(0)):ifx<133orx>135goto20115`` — only F1, F3, F5 turn
      a dial; any other key is ignored (the driver asks again). There is no way out:
      the prompt is not cancellable.
    - ``:20116`` the dial turns one digit, 9 to 0; ``:20120`` the new digit is shown.
    - ``:20125 ifint(rnd(1)*(in/8))=0orrd(x)<>cd(x)thensysso,7:goto20135`` — the
      roll is drawn on every press (``or`` does not short-circuit). A slip
      (``int(rnd(1)*(in/8))=0``, which is ``rnd(1)*in<8``, so ``range(in)<8``) or a
      wrong digit sounds the same: a correct digit can sound wrong, so no press
      tells a match for sure. A cracker below intelligence 8 always slips.
    - ``:20130 sysso,8:fori=0to2:ifrd(i)=cd(i)thennext:gosub1190:goto20150`` — the
      click, and only on a click is the whole code checked; it opens without costing
      the press a try.
    - ``:20135 y=y-1:ify>0goto20115`` — every other press costs a try; none left is a
      failure.

    The feedback is the screen's dials and the press's sound (``safe_click`` for
    ``sysso,8``, ``safe_slip`` for ``sysso,7``); the code is never shown.
    """
    state = ctx.state
    active = state.players[state.clock.active_player]
    rules = state.config.formula_params
    intelligenz = params["intelligenz"]
    digits = rules["ban_safe_digits"]
    dials = list(rules["ban_safe_dials"])
    code = [ctx.rng.range(digits) for _ in dials]  # :20110
    # :20111 (int(in/10) is a floor: in is never negative)
    tries = (
        rules["ban_safe_tries"]
        + intelligenz // rules["ban_safe_tries_divisor"]
        - (
            rules["ban_safe_main_tile_tries"]
            if active.last_location == rules["ban_main_tile"]
            else 0
        )
        + safe_skill(active)
    )
    ctx.apply(SafeSkillSet(max(safe_skill(active) - 1, 0)))
    yield ShowMessage("locations.ban.safe_dials", _dial_params(dials))
    while True:
        x = yield PromptChoice("locations.ban.safe_turn", options=list(_DIAL_KEYS))  # :20115
        dials[x] = (dials[x] + 1) % digits  # :20116
        # :20125 (the roll first: it is drawn on every press)
        slip = ctx.rng.range(max(intelligenz, 1)) < rules["ban_safe_slip_divisor"]
        if slip or dials[x] != code[x]:
            yield ShowMessage("locations.ban.safe_slip", _dial_params(dials))  # sysso,7
        else:
            yield ShowMessage("locations.ban.safe_click", _dial_params(dials))  # sysso,8
            if dials == code:  # :20130
                return CRACKED
        tries -= 1  # :20135
        if tries <= 0:
            return FAILED


def _dial_params(dials: list[int]) -> dict:
    """The three dials as the screen shows them (``:20120``, columns 16, 19, 22)."""
    return {f"d{i}": digit for i, digit in enumerate(dials, start=1)}


def heist_payout(ctx, *, tip_bonus: bool, adjust: int = 0):
    """The heist payout, ``mf-prg.bas:20050-20060``: the bank's, shared with the railway
    station's mail train (``:19040 goto20050``) and the cash transport.

    ``:20050 p=int(rnd(1)*3000)+4000-500*(la=10andln=1):x=tp(sp)`` — ``adjust`` is the
    caller's ``-500*(la=10andln=1)`` term (C64 true is -1, so it is +500 on the bank's
    tile 1, 0 elsewhere). ``:20051 if(x=1andla=9)or(x=2andla=10andln=2)or(x=3andla=13)
    thentp(sp)=0:p=p+3000`` — ``tip_bonus`` is whether the held tip matches the heist.
    ``:20055-20060`` the loot is shown, ``ka(sp)=ka(sp)+p:x=4:gosub1160``.
    """
    params = ctx.state.config.formula_params
    p = ctx.rng.range(params["heist_pay_spread"]) + params["heist_pay_min"] + adjust
    if tip_bonus:
        ctx.apply(TipClear())
        p += params["heist_tip_bonus"]
    yield ShowMessage("locations.ban.loot", {"p": p})
    ctx.apply(MoneyChange(p))
    ctx.apply(score_and_rank(params["heist_score"], params))

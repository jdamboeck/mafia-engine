"""The bhf (Bahnhof, railway station) handlers — ports ``mf-prg.bas:19000-19050``.

``:19005 onwgoto19010,19050,19015``: three playable options behind a guardless shell
(``content/locations/bhf.yaml``), and a leave (``:3045``). Nothing here reads the tile
``ln``: the station has one door (cell 68).

- ``bhf.pub`` (``:19010 ln=5:la=2:goto3000``) — the station pub. The ``goto`` re-enters
  the menu routine inside the same ``gosub3000`` the door opened (``:2055``), so the pub's
  menu follows in the same visit with no second door charge. The handler sets the entry
  context to the pub on tile 5, and the turn runner opens that menu (a handler that
  changes the entry context moves the visit, ``engine/turns.py``). Whatever the player
  then picks in the pub ends the visit as a pub visit: a pub option returns to
  ``:2055``, which writes the previous tile as the pub's (``ll(sp)=20*la+ln`` with
  ``la=2``, ``ln=5``), then ``:2060`` charges the door. The pub's leave costs its own 5
  first (``:3045``), so leaving the station through its pub costs 10. On tile 5 the
  pub sells alcohol (``:12010 ifln=4orln=5goto12020``, ``handlers/pub.py``).
- ``bhf.pickpocket`` (``:19050 w=1:goto18035``) — the subway's pickpocketing body
  (:func:`.sub.pickpocket`) with ``w=1``. It reads ``la=9`` from the entry context, so
  ``:18045``'s ``-(la<>9)`` adds nothing: the station's loot is the handbag, the camera,
  the pearls or the watch.
- ``bhf.mail_train`` (``:19015-19040``) — rob the mail train:

  1. ``:19015 iftp(sp)<>1thenprint"kein postzug zu sehen...":goto1100`` — only pub
     tip 1 (the mail train, ``:12230``) opens it.
  2. ``:19016 ifgz(sp)<3thenprint"du hast zu wenig gangster!":tp(sp)=0:goto1100`` —
     fewer than 3 gangsters (the boss counts) and the tip is lost (house rule
     ``mail_train_small_gang_loses_tip``, faithful-only).
  3. ``:19025-19027`` the storm, then ``:19030`` the guards: ``bn$(0)="wachen":gz(0)=3:
     w=7:e=30:kf$="kpzug":gosub5000`` (``content/encounters/bhf_guards.yaml``).
  4. Lost, ``ifs=2goto26020``: the arrest, with no police fight. The tip is still 1,
     and only the trial clears it (``:26045``). The ``p`` the fight leaves is not
     reported, so the arrest gets 0, as after a lost police fight (``handlers/police.py``).
  5. Won, ``:19040 goto20050``: the bank's heist payout, :func:`.ban.heist_payout`.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import SetEntryContext
from engine.interactions import ShowMessage
from engine.locations import register

from ..effects import TipClear
from ..setup import load_encounter, run_encounter
from ..state import tip_target
from .ban import heist_payout
from .police import Arrest, caught
from .sub import pickpocket

__all__ = ["bhf_mail_train", "bhf_pickpocket", "bhf_pub"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The mail train's guards (``:19030``).
_GUARDS = load_encounter(_CONFIG_DIR / "content" / "encounters" / "bhf_guards.yaml")


@register("bhf.pub")
def bhf_pub(ctx):
    """The station pub — ports ``mf-prg.bas:19010`` ``ln=5:la=2:goto3000``."""
    params = ctx.state.config.formula_params
    ctx.apply(SetEntryContext(la=params["bhf_pub_la"], ln=params["bhf_pub_ln"]))
    return []
    yield  # a generator, as every handler is


@register("bhf.pickpocket")
def bhf_pickpocket(ctx):
    """Pickpocketing at the station — ports ``mf-prg.bas:19050`` ``w=1:goto18035``."""
    return (yield from pickpocket(ctx, w=ctx.state.config.formula_params["bhf_pickpocket_w"]))


@register("bhf.mail_train")
def bhf_mail_train(ctx):
    """Rob the mail train — ports ``mf-prg.bas:19015-19040``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    # :19015 ``iftp(sp)<>1thenprint"kein postzug zu sehen...":goto1100``
    if tip_target(active) != params["bhf_mail_train_tip"]:
        yield ShowMessage("locations.bhf.no_train")
        return []
    # :19016 ``ifgz(sp)<3thenprint"du hast zu wenig gangster!":tp(sp)=0:goto1100``
    if len(active.roster) < params["bhf_mail_train_gang"]:
        yield ShowMessage("locations.bhf.too_few")
        ctx.apply(TipClear())
        return []
    # :19025-19027 ``du stuermst in den panzerwaggon ...``, then :1100's key.
    yield ShowMessage("locations.bhf.storm")
    # :19030 ``bn$(0)="wachen":gz(0)=3:w=7:e=30:kf$="kpzug":gosub5000:ifs=2goto26020``
    result = yield from run_encounter(ctx, _GUARDS)
    if result.winner == 2:
        yield from caught(ctx, Arrest(p=0))
        return []
    # :19040 ``goto20050``. :20051's ``(x=1andla=9)`` holds: the tip is 1, at the station.
    yield from heist_payout(ctx, tip_bonus=True)
    return []

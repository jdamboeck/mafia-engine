"""The turn hooks — this game's rules at the engine turn runner's fixed keys.

The engine turn runner (:mod:`engine.turns`) owns the ORDER of a turn
(``mf-prg.bas:1010-1013``); every rule in it is a handler registered here under one of
its hook keys, in the SAME :data:`engine.locations.HANDLERS` registry location handlers
use:

* :data:`~engine.turns.EARLY_WIN_HOOK_KEY` — ``:1011``'s turn-start check
  ``ifra(sp)=10andx5%(sp)>0andx6%(sp)>0thensyslh,"sieg-pic":goto40000``. A no-op: the
  two win flags come from the cash-transport and mayor flows (``la=13``/``14``), which
  are not built, so the check can never pass yet.
* :data:`~engine.turns.MOVEMENT_POINTS_HOOK_KEY` — ``:1012`` ``ms=tr(tm(sp))``: the
  active player's vehicle's ``tr``. Returns the value; the runner writes it. The same
  line's ``nr(sp)=ra(sp)`` is applied here too.
* :data:`~engine.turns.JOB_HOOK_KEY` — ``:1012`` ``ifjo(sp)thengosub25000:goto1010``:
  whether the active player holds a job (the runner then runs ``job.shift``).
* :data:`~engine.turns.SCORE_TRUNCATION_HOOK_KEY` — ``:1013``
  ``gf(sp)=int(gf(sp)*100)/100``.
* :data:`~engine.turns.JAIL_HOOK_KEY` — ``:1013`` ``ifgs(sp)thengosub1500:goto1010``:
  a jailed player's turn shows the jail screen (:data:`JAIL_SCREEN`, ``:1500-1515``),
  counts the sentence down by one, and ends.
* :data:`~engine.turns.ROADBLOCK_HOOK_KEY` — ``:2041`` the roadblock: registered by
  :mod:`.roadblock` (the gate and ``:6000-6036``).
* :data:`~engine.turns.SPECIAL_CELL_HOOK_KEY` — ``:2045``/``:2046`` the cash-transport
  and mayor cells (569/861, ``la=13``/``14``), armed by ``:2002``/``:2003`` for the
  player holding that tip (``tp(sp)=3``/``5``). A no-op: the two flows are not
  built, so the cell is never armed and stays the street it is on the map.

The turn menu's options (``content/menus/turn.yaml``, ``:1015-1050``) are handlers here
too; each returns what the runner does next:

* ``turn.overview`` — ``:1200-1245`` the overview: the player's state (with the
  passport and counterfeit marks, ``:1220-1222``), then the gang (``:1230-1240``, each
  gangster as ``:1300-1320`` prints it, a key after each and one more at the end).
  Returns nothing: back to the menu (``:1045``).
* ``turn.walk`` — ``:1035 onxgosub1200,2000,27000``, option 2: returns
  :data:`~engine.turns.MENU_WALK`.
* ``turn.next_player`` — ``:1031 ifx=4goto1010``: returns
  :data:`~engine.turns.MENU_END_TURN`.

Handler-API conformance: touches only ``ctx.state`` (read-only), ``ctx.apply(<Effect>)``
and this config's own helpers. None of them draws from ``ctx.rng``; only the overview
and the jail skip yield (display-only screens).
"""

from __future__ import annotations

import math
from pathlib import Path

from engine.effects import SetScore
from engine.interactions import Acknowledge
from engine.locations import register
from engine.turns import (
    EARLY_WIN_HOOK_KEY,
    JAIL_HOOK_KEY,
    JOB_HOOK_KEY,
    MENU_END_TURN,
    MENU_WALK,
    MOVEMENT_POINTS_HOOK_KEY,
    SCORE_TRUNCATION_HOOK_KEY,
    SPECIAL_CELL_HOOK_KEY,
)

from ..effects import Jail, PendingRankReset
from ..setup import gangster_line, load_ranks, load_vehicles, load_weapons
from ..state import contraband, job, next_rank, rented_months, wanted

__all__ = [
    "early_win",
    "movement_points",
    "has_job",
    "score_truncation",
    "jail",
    "special_cell",
    "truncated_score",
    "overview",
    "walk",
    "next_player",
    "overview_lines",
    "JAIL_SCREEN",
    "gang_lines",
    "OVERVIEW_SCREEN",
    "GANG_SCREEN",
]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: Decimal places ``gf * 100`` is rounded to before :func:`truncated_score` floors it.
#: A representation guard, not a rule: IEEE doubles store most whole-cent scores a hair
#: off (``0.29 * 100`` is ``28.999999999999996``), and a plain floor would take a cent
#: off such a score every turn. Rounding to 1e-6 of a cent (5e-9 in ``gf``) absorbs
#: that drift -- at ``gf <= 100`` it is thousands of times the double's own error --
#: while staying at or below the C64's float resolution there (a 32-bit mantissa is
#: about 7e-9 at ``gf`` = 25), so no difference the original could hold is erased.
_SCORE_SNAP_DECIMALS = 6


def truncated_score(gf: float) -> float:
    """``:1013`` ``gf(sp)=int(gf(sp)*100)/100``: the score cut to two decimals.

    BASIC ``int`` is floor, so a negative score goes toward -inf (-0.125 becomes
    -0.13). ``gf * 100`` is rounded to :data:`_SCORE_SNAP_DECIMALS` places first, so
    every whole-cent score is a fixed point and a second truncation changes nothing.
    """
    return math.floor(round(gf * 100, _SCORE_SNAP_DECIMALS)) / 100


@register(EARLY_WIN_HOOK_KEY)
def early_win(ctx):
    """``:1011``'s early-win check. Never passes yet (the win flows are not built)."""
    yield from ()
    return False


@register(MOVEMENT_POINTS_HOOK_KEY)
def movement_points(ctx):
    """``:1012`` ``ms=tr(tm(sp)):nr(sp)=ra(sp)``: the movement points, and the pending rank.

    Returns the active player's vehicle's ``tr`` (the runner writes ``ms``). The same
    line sets the pending rank ``nr`` back to the committed rank ``ra``; ``:4030`` has
    just made them equal, so the effect is applied only when they differ.
    """
    yield from ()
    active = ctx.state.players[ctx.state.clock.active_player]
    if next_rank(active) != active.rank:  # :1012 nr(sp)=ra(sp)
        ctx.apply(PendingRankReset())
    return load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")[active.vehicle]["tr"]


@register(JOB_HOOK_KEY)
def has_job(ctx):
    """``:1012`` ``ifjo(sp)``: an employed player works a shift instead of the free turn."""
    yield from ()
    active = ctx.state.players[ctx.state.clock.active_player]
    return bool(job(active).type)


@register(SCORE_TRUNCATION_HOOK_KEY)
def score_truncation(ctx):
    """``:1013`` ``gf(sp)=int(gf(sp)*100)/100``, once per free turn, after upkeep.

    It runs only on the path to a free turn: after upkeep (``:1011``), so the upkeep
    screens show the score before truncation; after ``:1012``'s job dispatch, so an
    employed player's turn never reaches it; and after ``:1010``'s year-end jump, so the
    final scoring sees each score as it stood when that player's last turn ended.
    """
    yield from ()
    active = ctx.state.players[ctx.state.clock.active_player]
    ctx.apply(SetScore(truncated_score(active.gf)))
    return []


#: Acknowledge: the jail screen (``:1510-1515``). ``params``: ``months``, the months
#: left before this turn's decrement (``gs(sp)+1`` after ``:1500``'s ``gs(sp)-1``).
JAIL_SCREEN = "turn.jail"


@register(JAIL_HOOK_KEY)
def jail(ctx):
    """``:1013`` ``ifgs(sp)thengosub1500:goto1010``: a jailed player's turn is skipped.

    ``:1500`` ``gs(sp)=gs(sp)-1`` counts the sentence down, then ``:1510-1515`` show
    ``gs(sp)+1`` -- the months as they stood before the decrement -- and wait for a key
    (``goto1100``). Returns truthy for a jailed player: the turn ends with no menu and
    no map. It runs after upkeep (``:1011``) and the score truncation, so both still
    happen on a skipped turn, and after ``:1012``'s job dispatch (a convict holds no
    job, ``:26080``). A sentence of N months skips exactly N turns.
    """
    months = wanted(ctx.state.players[ctx.state.clock.active_player]).jail_months
    if not months:
        return False
    ctx.apply(Jail(months=months - 1))  # :1500 gs(sp)=gs(sp)-1
    yield Acknowledge(JAIL_SCREEN, {"months": months})  # :1510 gs(sp)+1
    return True


@register(SPECIAL_CELL_HOOK_KEY)
def special_cell(ctx, *, cell, la):
    """``:2045``/``:2046`` the event cells 569/861: never armed yet (no flow is built)."""
    yield from ()
    return False


# --------------------------------------------------------------------------- #
# The turn menu's options (content/menus/turn.yaml)                           #
# --------------------------------------------------------------------------- #
#: Acknowledge: the overview's first screen (``:1200-1225``). ``params``: ``lines``,
#: ``(key, params)`` pairs, one per printed line.
OVERVIEW_SCREEN = "turn.overview"
#: Acknowledge: the overview's gang screen (``:1230-1240``). ``params``: ``lines``.
GANG_SCREEN = "turn.overview_gang"

#: ``:1221`` the ``gegenstaende`` line by the marks held -- ``(papers, counterfeit)``,
#: ``ag(sp)`` bits 0 and 1 (``ag$`` = ``papiere``/``falschgeld``, ``:50600``).
_ITEMS_KEYS = {
    (True, False): "turn.overview.items_papers",
    (False, True): "turn.overview.items_counterfeit",
    (True, True): "turn.overview.items_both",
}


def overview_lines(state) -> list[tuple[str, dict]]:
    """``:1200-1225``: the active player's state, one ``(key, params)`` per line.

    ``:1208``'s free-memory line is a debug switch (``peek(53247)=1``) and not shown.
    """
    active = state.players[state.clock.active_player]
    ranks = load_ranks(_CONFIG_DIR / "entities" / "ranks.yaml")
    vehicles = load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")
    held = contraband(active)
    lines: list[tuple[str, dict]] = [
        ("turn.overview.title", {"name": active.name}),  # :1200
        ("turn.overview.score", {"score": active.gf}),  # :1209
        ("turn.overview.rank", {"rank_name": ranks[active.rank - 1]}),  # :1210
        (  # :1215
            "turn.overview.vehicle",
            {"vehicle": vehicles[active.vehicle]["name"], "movement": active.ms},
        ),
        ("turn.overview.alcohol", {"barrels": held.alcohol_barrels}),  # :1219
    ]
    marks = (bool(held.fake_papers), bool(held.counterfeit))
    if any(marks):  # :1220 ifag(sp)=0goto1225
        lines.append((_ITEMS_KEYS[marks], {}))  # :1221-1222
    lines.append(("turn.overview.bribes", {"months": wanted(active).bribe_months}))  # :1225
    lines.append(("turn.overview.rent", {"months": rented_months(active)}))  # :1225
    return lines


def gang_lines(state) -> list[tuple[str, dict]]:
    """``:1230-1240``: the gang, each gangster as ``:1300-1320`` prints it.

    ``:1300`` reads the stats ``x$=ge$(a,b)``; ``:1315`` prints the four of them, two
    digits each (``:1385`` pads a one-digit stat with a ``0``); ``:1320`` the weapon's
    name.
    """
    active = state.players[state.clock.active_player]
    lines: list[tuple[str, dict]] = [("turn.overview.gang_title", {})]
    if not active.roster:  # :1230 ifgz(sp)=0thenprint"{down}keine!"
        lines.append(("turn.overview.no_gang", {}))
        return lines
    weapons = load_weapons(_CONFIG_DIR / "entities" / "weapons.yaml")
    for member in active.roster:
        lines.append(("turn.overview.gangster", gangster_line(member, weapons)))
    return lines


@register("turn.overview")
def overview(ctx):
    """``:1200-1245`` the overview: the player's state, then the gang; nothing changes.

    The state screen ends in ``:1100``'s key press (``:1230 gosub1100``). The gang page
    waits for a key after each gangster (``:1235 poke198,0:wait198,1``) while it fills
    one screen, then once more (``:1245 goto1100``); an empty gang prints ``keine!``
    and waits once (``:1230``). Each gang :class:`Acknowledge` therefore carries the
    gangsters printed so far, and the last one the whole gang again.
    """
    yield Acknowledge(OVERVIEW_SCREEN, {"lines": overview_lines(ctx.state)})
    lines = gang_lines(ctx.state)
    gangsters = len(ctx.state.players[ctx.state.clock.active_player].roster)
    for shown in range(1, gangsters + 1):  # :1235-1240, a key after each gangster
        yield Acknowledge(GANG_SCREEN, {"lines": lines[: 1 + shown]})
    yield Acknowledge(GANG_SCREEN, {"lines": lines})  # :1245 goto1100 / :1230 keine!
    return None


@register("turn.walk")
def walk(ctx):
    """``:1035 onxgosub1200,2000,27000``, option 2: the player walks the map."""
    yield from ()
    return MENU_WALK


@register("turn.next_player")
def next_player(ctx):
    """``:1031 ifx=4goto1010``: the turn ends, the movement points unspent."""
    yield from ()
    return MENU_END_TURN

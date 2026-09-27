"""Standings and year-end generators — ports ``mf-prg.bas:4500-4515`` and ``:40100-40166``.

Registered under :data:`engine.game_end.STANDINGS_HANDLER_KEY` and
:data:`engine.game_end.YEAR_END_HANDLER_KEY` in the SAME
:data:`engine.locations.HANDLERS` registry location handlers use; the engine
runners :func:`engine.game_end.run_standings` / :func:`engine.game_end.run_year_end`
look these up and drive them. The RANKING RULE lives here, in game code — the engine
knows nothing about scores or winners.

Both flows are display-only: they yield only ``ShowMessage`` and apply no effects.

MESSAGE / KEY DESIGN
--------------------
The resolver fills templates with ``str.format`` and cannot iterate a list, and a
handler may not resolve or build display text. So every variable-length list is emitted
as ONE ``ShowMessage`` PER ROW with a row key and structured params — all text and
layout stay in the theme (``themes/classic/strings/game_end.yaml``):

* standings (``:4500-4510``): ``game_end.standings_header`` ``{year, month}`` (the
  ``spielstand YYYY-M`` line plus the ``spieler: kapital: punkte:`` column header),
  then ``game_end.standings_row`` ``{name, cash, score}`` once per player in
  ``state.players`` order.
* sole winner (``:40115-40117``): ``game_end.winner`` ``{name}``.
* tie (``:40150-40160``): ``game_end.tie_header``, then ``game_end.tie_name``
  ``{name}`` once per tied player in player order, then ``game_end.tie_footer``.

A client renders the consecutive messages of one flow as one screen.
"""

from __future__ import annotations

from engine.game_end import STANDINGS_HANDLER_KEY, YEAR_END_HANDLER_KEY
from engine.interactions import ShowMessage
from engine.locations import register

__all__ = ["standings", "top_scorers", "year_end"]


def top_scorers(players) -> list[int]:
    """Return the indices of the year-end winner(s), in player order (``:40100-40110``).

    ``:40100`` seeds the list with player 1 (``g=1:g(g)=1``). ``:40105`` scans players
    2..sz: a STRICTLY higher score (``gf(i)>gf(g(g))``) resets the list to that single
    player; ``:40106`` an EQUAL score (``gf(i)=gf(g(g))``) appends the player to it.
    A lower score is ignored. One entry = sole winner, more = shared victory (``:40110``).
    """
    winners = [0]
    for i in range(1, len(players)):
        lead = players[winners[-1]].gf
        if players[i].gf > lead:
            winners = [i]
        elif players[i].gf == lead:
            winners.append(i)
    return winners


def _standings_messages(state):
    """Yield the standings screen (``:4500-4510``): date + column header, then one row
    per player. ``month`` is displayed 1-based (``1+int((ja-x)*12)``; ``clock.month``
    is the 0-based twelfths counter)."""
    yield ShowMessage(
        "game_end.standings_header",
        {"year": state.clock.year, "month": state.clock.month + 1},
    )
    for p in state.players:
        yield ShowMessage("game_end.standings_row", {"name": p.name, "cash": p.ka, "score": p.gf})


@register(STANDINGS_HANDLER_KEY)
def standings(ctx):
    """The between-rounds standings table (``mf-prg.bas:4500-4515``)."""
    yield from _standings_messages(ctx.state)
    return []


@register(YEAR_END_HANDLER_KEY)
def year_end(ctx):
    """The year-end result (``mf-prg.bas:40100-40166``): standings, then the winner(s)."""
    state = ctx.state
    yield from _standings_messages(state)  # :40100 gosub4500
    winners = top_scorers(state.players)
    if len(winners) == 1:  # :40110 falls through to 40115
        yield ShowMessage("game_end.winner", {"name": state.players[winners[0]].name})
        return []
    yield ShowMessage("game_end.tie_header")  # :40150
    for i in winners:  # :40155
        yield ShowMessage("game_end.tie_name", {"name": state.players[i].name})
    yield ShowMessage("game_end.tie_footer")  # :40160
    return []

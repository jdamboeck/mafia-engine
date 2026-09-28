"""The turn loop + city-map movement economy — ENGINE mechanism.

This is generic engine machinery, not game-specific content (docs/design/config-and-content-contract.md: "Game
FSM + turn loop + movement economy" is engine-provided). It ports the original's
turn/movement layer from the decompiled BASIC:

* **Turn loop helpers** — ``mf-prg.bas:1010-1013``: :func:`advance_turn` and
  :func:`start_free_turn`, for callers that step turns by hand. The turn ORDER is
  :mod:`engine.turns`'s (the engine turn runner), which commits the same rotation
  as effects and asks the config's hooks for the game rules in it.
* **Movement** — ``mf-prg.bas:2000-2065``: one step per direction key on the
  40-wide grid. A move toward the target cell ``p = po + delta`` either STEPS onto
  a walkable street (grid code 156, ``ms -= 1``), ENTERS a location if ``p`` is a
  door-table cell (``ms -= 5``, ``po`` unchanged — entering is the action, not a
  move), or is rejected as a WALL. ``ms <= 0`` ends the turn (``mf-prg.bas:2005``).
* **Police interrupt** — ``mf-prg.bas:2041``: the ``rank > 3`` gate (never fires
  at rank 1). Only the *gate* is implemented; the roadblock body is not built.
* **Event cells** — the la=13/14 map-triggered cells (cash transport, mayor hit) are
  detected and skipped; their flows are not built.

**The ``ln`` seam (formalized here).** When a move enters a location with resolved
``(la, ln)``, a :class:`~engine.effects.SetEntryContext` effect sets the active
player's ``last_location = ln`` (and ``last_la = la``) **before** that location's
handler runs. Location handlers read it (e.g. slw keys ``fnm(ln)`` off
``last_location``); door entry is what sets it.

``engine/`` imports nothing from ``server``/``clients``/transport, and this module
holds no display text. City data (the grid + door table) is CONFIG data, passed in
as a plain dict (loaded from the config's ``content/map/city.yaml``) — the engine
never statically imports anything under ``data/``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from engine.actions import EngineResult
from engine.effects import (
    MONTHS_PER_YEAR,
    AdvanceTurn,
    MsChange,
    SetEntryContext,
    SetMovementPoints,
    SetPosition,
    SetScore,
    commit,
)
from engine.state import GameState
from engine.events import EnterLocation, MoveBlocked, MoveStep

__all__ = [
    "LEFT",
    "RIGHT",
    "UP",
    "DOWN",
    "GRID_WIDTH",
    "STREET_CODE",
    "STEP_COST",
    "ENTER_COST",
    "MONTHS_PER_YEAR",
    "City",
    "MoveResult",
    "load_city",
    "try_move",
    "advance_turn",
    "start_free_turn",
    "police_interrupt_would_fire",
]

# --- Movement deltas on the 40-wide grid (mf-prg.bas:2015-2018) ------------
GRID_WIDTH = 40
LEFT = -1
RIGHT = 1
UP = -GRID_WIDTH
DOWN = GRID_WIDTH

#: The walkable-street grid code. Only cells with this code may be STEPPED on
#: (mf-prg.bas:2035/2040). Door cells decode to other codes (e.g. 83, 144), so the
#: 156-gate correctly refuses to step onto a door — entry is via the door table.
STREET_CODE = 156

#: Movement-point costs: 1 per street step (:2040), 5 to enter a location (:2060).
STEP_COST = 1
ENTER_COST = 5

#: Cell-index bounds on the 40×25 city map (mf-prg.bas:2030).
_MIN_CELL = 0
_MAX_CELL = 999


@dataclass
class City:
    """The parsed city map: the 40×25 grid + the door table.

    ``grid`` is ``rows`` lists of ``cols`` int codes (25×40). ``color_grid`` is
    the per-cell C64 color RAM index (0-15) — same dimensions as ``grid``.
    ``doors`` maps a cell index to its ``(la, ln)`` — the RESOLVED door mechanic
    (``syslc(p)``): a move whose TARGET cell is a door entry ENTERS that location,
    it is NOT adjacency. ``special_cells`` maps a cell to its event ``la`` (13/14).
    """

    grid: list[list[int]]
    doors: dict[int, tuple[int, int]]
    special_cells: dict[int, int]
    cols: int = GRID_WIDTH
    color_grid: list[list[int]] | None = None

    def code(self, cell: int) -> int:
        """The grid code at absolute cell index ``cell`` (row-major, 40 wide)."""
        return self.grid[cell // self.cols][cell % self.cols]

    def color(self, cell: int) -> int:
        """The C64 color RAM index (0-15) at absolute cell index ``cell``."""
        if self.color_grid is None:
            return 12  # default grey
        return self.color_grid[cell // self.cols][cell % self.cols]

    def door(self, cell: int) -> tuple[int, int] | None:
        """``(la, ln)`` if ``cell`` is a door-table entry, else ``None`` (``syslc(p)``)."""
        return self.doors.get(cell)

    def is_special(self, cell: int) -> bool:
        """Whether ``cell`` is an la=13/14 event cell (detected; the flows are not built)."""
        return cell in self.special_cells


def load_city(raw: dict) -> City:
    """Parse a ``city.yaml`` dict (from ``yaml.safe_load``) into a :class:`City`.

    Reads ``grid`` (25×40 int codes), ``color_grid`` (25×40 C64 color indices),
    ``doors`` (list of ``{cell, la, ln, ...}``), and ``special_cells`` (list of
    ``{cell, la, ...}``). City data is CONFIG-owned; the engine takes it as plain
    data, never importing it.
    """
    grid = [list(row) for row in raw["grid"]]
    color_grid = [list(row) for row in raw.get("color_grid") or []]
    # If no color_grid in config, default to all grey (12)
    if not color_grid or len(color_grid) != len(grid):
        color_grid = [[12] * len(row) for row in grid]
    doors: dict[int, tuple[int, int]] = {}
    for d in raw.get("doors", []) or []:
        doors[d["cell"]] = (d["la"], d["ln"])
    special: dict[int, int] = {}
    for s in raw.get("special_cells", []) or []:
        special[s["cell"]] = s.get("la", 0)
    cols = (raw.get("dims") or {}).get("cols", GRID_WIDTH)
    return City(grid=grid, color_grid=color_grid, doors=doors, special_cells=special, cols=cols)


@dataclass
class MoveResult:
    """The outcome of one :func:`try_move`.

    ``kind`` is one of:
      * ``"step"``     — stepped onto a street cell (``po`` moved, ``ms -= 1``);
      * ``"enter"``    — entered a location (``po`` unchanged, ``ms -= 5``);
        ``la``/``ln`` carry the resolved location + tile;
      * ``"wall"``     — the target was a wall (no change);
      * ``"oob"``      — the target was out of bounds (no change);
      * ``"special"``  — the target was an la=13/14 event cell (detected and skipped);
      * ``"turn_over"``— the turn was already over (``ms <= 0``); no move happened.

    ``turn_over`` is ``True`` once the active player's ``ms <= 0`` after the move
    (or was already ``<= 0``) — the movement loop should return to the turn loop.

    ``from_cell`` is the active player's origin cell (``po`` before the move) and
    ``delta`` is the signed step attempted — semantic movement context that mirrors
    the emitted event fields.
    """

    kind: str
    turn_over: bool = False
    la: int | None = None
    ln: int | None = None
    target: int | None = None
    from_cell: int | None = None
    delta: int | None = None


def try_move(state: GameState, city: City, delta: int) -> EngineResult[GameState]:
    """Attempt one directional move for the active player (mf-prg.bas:2000-2065).

    ``delta`` is one of :data:`LEFT`/:data:`RIGHT`/:data:`UP`/:data:`DOWN`. The
    move targets ``p = po + delta`` and resolves per the source, producing exactly
    one :class:`~engine.actions.EngineResult`. **This function is pure**: the input
    ``state`` is NEVER mutated — mutating outcomes go through
    :func:`engine.effects.commit` (one deep copy), and non-mutating outcomes return
    the input ``state`` object untouched.

    Semantic **events** are audit/UI records only; the primitive **effects** are the
    sole mutations. The two are kept distinct: a blocked move emits an event with an
    empty effects list and an unchanged state.

    1. Turn-end gate first (:2005): if ``ms <= 0`` the turn is already over — no
       move happens. Emits :class:`~engine.events.MoveBlocked` (reason ``"turn_over"``),
       no effects, ``status="turn_over"``, ``kind="turn_over"``.
    2. Bounds (:2030): ``p`` outside ``[0, 999]`` rejects the move. Emits
       :class:`~engine.events.MoveBlocked` (reason ``"oob"``), no effects,
       ``status="blocked"``, ``kind="oob"``.
    3. Walkable street (:2035/2040): ``code(p) == 156`` -> STEP: commits
       ``SetPosition(p)`` + ``MsChange(-1)``, emits :class:`~engine.events.MoveStep`,
       ``status="completed"``, ``kind="step"``.
    4. Else try to ENTER (:2045-2060): if ``p`` is a door-table cell -> ENTER that
       location: commits ``SetEntryContext(la, ln)`` (the ``ln`` seam) +
       ``MsChange(-5)``, emits :class:`~engine.events.EnterLocation`; ``po`` stays put
       (``kind="enter"``, ``la``/``ln`` set), ``status="completed"``.
    5. Special cell (la=13/14): detected and skipped (the flows are not built) — no events, no
       effects, ``status="not_implemented"``, ``kind="special"``.
    6. Otherwise a WALL (:2050): reject the move. Emits
       :class:`~engine.events.MoveBlocked` (reason ``"wall"``), no effects,
       ``status="blocked"``, ``kind="wall"``.

    After any step/entry the turn-end gate is re-checked: the payload's ``turn_over``
    is ``True`` when the resulting ``ms <= 0``.
    """
    player = state.players[state.clock.active_player]
    active = state.clock.active_player
    from_cell = player.po
    target = from_cell + delta

    # :2005 — the turn ends when ms <= 0. If already over, no move happens.
    if player.ms <= 0:
        event = MoveBlocked(
            player=active,
            from_cell=from_cell,
            target=target,
            delta=delta,
            reason="turn_over",
        )
        payload = MoveResult(kind="turn_over", turn_over=True, from_cell=from_cell, delta=delta)
        return EngineResult(
            state=state,
            events=[event],
            effects=[],
            status="turn_over",
            payload=payload,
        )

    # :2030 — bounds. An out-of-bounds step has no valid target (event target None).
    if target < _MIN_CELL or target > _MAX_CELL:
        event = MoveBlocked(
            player=active,
            from_cell=from_cell,
            target=None,
            delta=delta,
            reason="oob",
        )
        payload = MoveResult(kind="oob", turn_over=player.ms <= 0, from_cell=from_cell, delta=delta)
        return EngineResult(
            state=state,
            events=[event],
            effects=[],
            status="blocked",
            payload=payload,
        )

    # :2035/2040 — walkable street: STEP (1 ms). po = target, ms -= 1.
    if city.code(target) == STREET_CODE:
        result = commit(state, [SetPosition(target), MsChange(-STEP_COST)])
        new_player = result.state.players[active]
        event = MoveStep(player=active, from_cell=from_cell, to_cell=target, delta=delta)
        payload = MoveResult(
            kind="step",
            turn_over=new_player.ms <= 0,
            target=target,
            from_cell=from_cell,
            delta=delta,
        )
        return EngineResult(
            state=result.state,
            events=[event],
            effects=result.effects,
            status="completed",
            payload=payload,
        )

    # :2045-2060 — otherwise try to ENTER a location via the door table.
    door = city.door(target)
    if door is not None:
        la, ln = door
        # The ln seam: SetEntryContext records last_la/last_location BEFORE the
        # location's handler runs. po does NOT move onto the door — entering is the
        # action (mf-prg.bas:2060). ms -= 5 unconditionally (may go negative).
        result = commit(state, [SetEntryContext(la=la, ln=ln), MsChange(-ENTER_COST)])
        new_player = result.state.players[active]
        event = EnterLocation(
            player=active,
            from_cell=from_cell,
            door_cell=target,
            delta=delta,
            la=la,
            ln=ln,
        )
        payload = MoveResult(
            kind="enter",
            turn_over=new_player.ms <= 0,
            la=la,
            ln=ln,
            target=target,
            from_cell=from_cell,
            delta=delta,
        )
        return EngineResult(
            state=result.state,
            events=[event],
            effects=result.effects,
            status="completed",
            payload=payload,
        )

    # la=13/14 event cells — detected and skipped (no-op); their flows are not built.
    if city.is_special(target):
        payload = MoveResult(
            kind="special",
            turn_over=player.ms <= 0,
            target=target,
            from_cell=from_cell,
            delta=delta,
        )
        return EngineResult(
            state=state,
            events=[],
            effects=[],
            status="not_implemented",
            payload=payload,
        )

    # :2050 — not 156, not a door, not special -> a WALL: reject the move.
    event = MoveBlocked(
        player=active,
        from_cell=from_cell,
        target=target,
        delta=delta,
        reason="wall",
    )
    payload = MoveResult(
        kind="wall",
        turn_over=player.ms <= 0,
        target=target,
        from_cell=from_cell,
        delta=delta,
    )
    return EngineResult(
        state=state,
        events=[event],
        effects=[],
        status="blocked",
        payload=payload,
    )


def advance_turn(state: GameState, vehicles: list[dict]) -> tuple[GameState, bool]:
    """End the active player's turn and rotate to the next (mf-prg.bas:1010-1012).

    A convenience over the turn runner's own effects, for callers that step turns by
    hand (tests, walk planners): :class:`~engine.effects.AdvanceTurn` rotates the active
    player and, on a wrap, advances the calendar one month; then the new active
    player's movement points are refilled from its vehicle's ``tr`` in ``vehicles``
    (``ms = tr(tm(sp))``, :1012) with :class:`~engine.effects.SetMovementPoints`. The
    engine turn runner (:mod:`engine.turns`) does not call this: it commits the
    rotation at ``:1010`` and asks the config's movement-points hook for ``ms`` at
    ``:1012``, after upkeep.

    Pure, like :func:`try_move`: **callers must adopt the returned state**.

    Returns:
        ``(new_state, game_over)`` where ``game_over`` is ``True`` if the game has
        reached ``end_year``.
    """
    rotated = commit(state, [AdvanceTurn()]).state
    active = rotated.players[rotated.clock.active_player]
    new_state = commit(rotated, [SetMovementPoints(vehicles[active.vehicle]["tr"])]).state
    return new_state, int(new_state.clock.year) >= new_state.clock.end_year


#: Decimal places ``gf * 100`` is rounded to before :func:`start_free_turn` floors it.
#: A representation guard, not a rule: IEEE doubles store most whole-cent scores a hair
#: off (``0.29 * 100`` is ``28.999999999999996``), and a plain floor would take a cent
#: off such a score every turn. Rounding to 1e-6 of a cent (5e-9 in ``gf``) absorbs
#: that drift -- at ``gf <= 100`` it is thousands of times the double's own error --
#: while staying at or below the C64's float resolution there (a 32-bit mantissa is
#: about 7e-9 at ``gf`` = 25), so no difference the original could hold is erased.
_SCORE_SNAP_DECIMALS = 6


def start_free_turn(state: GameState) -> GameState:
    """Truncate the active player's score to two decimals (mf-prg.bas:1013).

    ``:1013`` ``gf(sp)=int(gf(sp)*100)/100`` runs once per turn start, and only on
    the path to a free turn:

    * after upkeep (``:1011`` ``gosub4000``), so the upkeep screens show the score
      before truncation;
    * after ``:1012``'s job dispatch (``ifjo(sp)thengosub25000:goto1010``), so an
      employed player's turn never reaches it;
    * after ``:1010``'s year-end jump (``goto40100``), so the final scoring sees each
      score as it stood when that player's last turn ended.

    BASIC ``int`` is floor, so a negative score goes toward -inf (-0.125 becomes
    -0.13). ``gf * 100`` is rounded to :data:`_SCORE_SNAP_DECIMALS` places first, so
    every whole-cent score is a fixed point and a second call changes nothing.

    The engine turn runner (:mod:`engine.turns`) does not call this: ``:1013`` is a
    game formula, so the runner asks the config's score-truncation hook
    (:data:`engine.turns.SCORE_TRUNCATION_HOOK_KEY`). This engine copy stays for the
    callers that step turns by hand and its port test; ``tests/test_turn_runner.py``
    holds it and the mafia_1920s hook to the same values. Pure: returns a NEW state.
    """
    active = state.players[state.clock.active_player]
    cents = math.floor(round(active.gf * 100, _SCORE_SNAP_DECIMALS))
    return commit(state, [SetScore(cents / 100)]).state


def police_interrupt_would_fire(state, ms: int, rng: Any) -> bool:
    """Whether the roadblock police interrupt would fire (mf-prg.bas:2041).

    The source gate is ``if ms%20==0 and rnd(5)==0 and ra(sp)>3 then <roadblock>``.
    This returns whether ALL three conditions hold; the roadblock BODY is not built
    (only the gate is implemented). The ``rank > 3`` term is why a rank-1
    player is NEVER interrupted.

    ``rng`` supplies ``range(5)`` (``rnd(5)``); ``range(5) == 0`` is the roll hit.
    """
    active = state.players[state.clock.active_player]
    if active.rank <= 3:  # ra(sp) > 3 gate — false at ranks 1..3
        return False
    if ms % 20 != 0:
        return False
    return rng.range(5) == 0

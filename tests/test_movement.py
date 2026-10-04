"""Tests for the movement + turn economy + pub denial — U9.

Proof-first: written and observed RED (``engine.movement`` missing) BEFORE the
implementation.

This exercises the whole movement spine on the REAL 40×25 city map (loaded from
the config's ``content/map/city.yaml``): the ``ms`` movement economy, stepping on
walkable street cells (code 156), the RESOLVED door-entry mechanic (a move whose
TARGET cell is a door-table entry ENTERS that location — NOT adjacency), the
turn-loop rotation + ``ms`` replenishment from the vehicle table, the ``ln`` seam
that sets the active player's ``last_location`` on entry (formalizing what U7
stubbed), the pub recruit refusal reached by WALKING at rank 1, and the police
interrupt gated off at rank 1.

Ports (all oracle-gated):
  * turn loop        — mf-prg.bas:1010-1013 (sp wrap, ms = tr(tm(sp)))
  * movement         — mf-prg.bas:2000-2065 (deltas, 156-step, door-entry, walls)
  * police interrupt — mf-prg.bas:2041 (rank>3 gate; never fires at rank 1)
  * pub recruit      — mf-prg.bas:12100/12105 (ra>4 AND gz<10), checked in the handler
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dataclasses import replace

from engine.effects import MsChange, SetEntryContext, SetPosition
from engine.events import EnterLocation, MoveBlocked, MoveStep
from engine.locations import available_options, load_location
from engine.movement import (
    ENTER_COST,
    STEP_COST,
    STREET_CODE,
    RIGHT,
    UP,
    DOWN,
    load_city,
    try_move,
)
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster
from engine.config_loader import load_game_config
from engine.actions import run_option
from tests.helpers import next_turn_by_hand, scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"
_PUB_SHELL = _CONFIG_DIR / "content" / "locations" / "pub.yaml"
_PUB_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "pub.yaml"

# Registers the config's turn hooks: the rotation below refills ``ms`` through its
# movement-points hook (on foot, vehicle 0: tr=25).
_CONFIG = load_game_config(_CONFIG_DIR)


def _city():
    return load_city(yaml.safe_load(_CITY.read_text(encoding="utf-8")))


def _state(*, po=162, ms=25, rank=1, active=0, players=1, vehicle=0, roster=1):
    plist = tuple(
        Player(
            ka=5000,
            po=po,
            ms=ms,
            rank=rank,
            vehicle=vehicle,
            roster=tuple(Gangster() for _ in range(roster)),
        )
        for _ in range(players)
    )
    return GameState(
        players=plist,
        clock=Clock(active_player=active, player_count=players),
        config=Config(),
    )


# --------------------------------------------------------------------------- #
# City loader: the real map has the expected shape + known cells.             #
# --------------------------------------------------------------------------- #
def test_city_loads_known_cells():
    city = _city()
    assert city.code(162) == 156  # walkable street
    assert city.code(122) == 83  # slw door cell (NOT 156)
    assert city.door(122) == (1, 1)  # (la, ln) for slw
    assert city.door(162) is None  # not a door


# --------------------------------------------------------------------------- #
# 156-step: costs 1 ms, moves po by the delta.                                #
# --------------------------------------------------------------------------- #
def test_step_onto_street_costs_one_ms():
    st = _state(po=162, ms=25)
    city = _city()
    # From 162, RIGHT (+1) -> 163. Confirm 163 is walkable first.
    assert city.code(163) == 156
    res = try_move(st, city, RIGHT)
    assert res.payload.kind == "step"
    assert res.status == "completed"
    # Purity: the INPUT state is untouched (a new state object is returned).
    assert st.players[0].po == 162
    assert st.players[0].ms == 25
    assert res.state is not st
    # The move committed onto the RETURNED state: moved by +1, spent exactly 1.
    assert res.state.players[0].po == 163
    assert res.state.players[0].ms == 24
    # Events (audit) + effects (mutations) emitted for the step.
    assert res.events == [MoveStep(player=0, from_cell=162, to_cell=163, delta=RIGHT)]
    assert res.effects == [SetPosition(163), MsChange(-STEP_COST)]


# --------------------------------------------------------------------------- #
# Wall: a non-156, non-door cell rejects the move (no po/ms change).          #
# --------------------------------------------------------------------------- #
def test_blocked_cell_does_not_move_or_spend():
    st = _state(po=162, ms=25)
    city = _city()
    # DOWN (+40) from 162 -> 202, which is code 160 (a building wall): not 156,
    # not a door, not special.
    assert city.code(202) != 156 and city.door(202) is None
    assert not city.is_special(202)
    before = st  # frozen graph: the state is its own snapshot
    res = try_move(st, city, DOWN)
    assert res.payload.kind == "wall"
    assert res.status == "blocked"
    # A blocked move mutates NOTHING: input unchanged AND the returned state is the
    # same input object (no commit happened).
    assert st.players[0].po == before.players[0].po  # unchanged
    assert st.players[0].ms == before.players[0].ms  # nothing spent
    assert res.state is st
    # An event with NO matching effect (a wall blocks with zero effects).
    assert res.events == [
        MoveBlocked(player=0, from_cell=162, target=202, delta=DOWN, reason="wall")
    ]
    assert res.effects == []


# --------------------------------------------------------------------------- #
# Out of bounds: a target off the 40×25 grid rejects the move (no mutation).  #
# --------------------------------------------------------------------------- #
def test_out_of_bounds_rejects_move():
    from engine.movement import LEFT

    st = _state(po=0, ms=25)
    city = _city()
    before = st  # frozen graph: the state is its own snapshot
    res = try_move(st, city, LEFT)  # target -1: off the grid
    assert res.payload.kind == "oob"
    assert res.status == "blocked"
    # Nothing mutated: input unchanged, returned state IS the input object.
    assert st.players[0].po == before.players[0].po
    assert st.players[0].ms == before.players[0].ms
    assert res.state is st
    # oob has no valid target cell -> the event's target is None.
    assert res.events == [MoveBlocked(player=0, from_cell=0, target=None, delta=LEFT, reason="oob")]
    assert res.effects == []


# --------------------------------------------------------------------------- #
# Special cell (la=13/14 event flows): detected, but OUT OF SCOPE this slice. #
# --------------------------------------------------------------------------- #
def test_special_cell_is_not_implemented():
    # On the real map both la=13/14 special cells (569, 861) happen to be code-156
    # street, so the STEP branch wins before the special branch is ever reached
    # (branch order preserved from the source). To exercise the special branch we
    # build a minimal City whose special target is neither street nor a door.
    from engine.movement import City

    grid = [[0] * 40 for _ in range(25)]  # all code 0: not street, not a door
    city = City(grid=grid, doors={}, special_cells={1: 13})
    st = _state(po=0, ms=25)
    assert city.is_special(1) and city.code(1) != STREET_CODE and city.door(1) is None
    before = st  # frozen graph: the state is its own snapshot
    res = try_move(st, city, RIGHT)  # 0 --RIGHT--> 1 (special)
    assert res.payload.kind == "special"
    assert res.status == "not_implemented"
    # No events, no effects, state untouched (the event flow is a later unit).
    assert res.events == []
    assert res.effects == []
    assert res.state is st
    assert st.players[0].po == before.players[0].po
    assert st.players[0].ms == before.players[0].ms


# --------------------------------------------------------------------------- #
# Door entry (THE resolved mechanic): target-cell lookup, NOT adjacency.      #
# From 162, UP (-40) targets 122 = slw door (la=1, ln=1): enter (no charge), po #
# stays at 162, and the ln seam sets last_location=1.                         #
# --------------------------------------------------------------------------- #
def test_enter_slw_via_door_target():
    st = _state(po=162, ms=25, active=0)
    city = _city()
    res = try_move(st, city, UP)
    assert res.payload.kind == "enter"
    assert res.status == "completed"
    assert res.payload.la == 1 and res.payload.ln == 1
    # Purity: the input state is untouched; entry commits onto the returned state.
    assert st.players[0].ms == 25
    assert st.players[0].last_location == 0  # default; entry did not touch input
    assert res.state is not st
    # You do NOT stand on the door cell — po is unchanged (no SetPosition on entry).
    assert res.state.players[0].po == 162
    # No charge at the door: :2060 ms=ms-5 comes after the visit (the turn runner's).
    assert res.state.players[0].ms == 25
    # The ln seam: entering set the active player's last_location = ln (and last_la).
    assert res.state.players[0].last_location == 1
    assert res.state.players[0].last_la == 1
    # Events + effects: EnterLocation event, the SetEntryContext effect only.
    assert res.events == [
        EnterLocation(player=0, from_cell=162, door_cell=122, delta=UP, la=1, ln=1)
    ]
    assert res.effects == [SetEntryContext(la=1, ln=1)]


def test_entering_with_fewer_points_than_the_door_costs_is_not_blocked():
    # :2050 enters whatever ms is left; the door's 5 come after the visit (:2060
    # ms=ms-5, the turn runner's), so the handler runs on the 3 points left.
    st = _state(po=162, ms=3, active=0)
    res = try_move(st, _city(), UP)  # target 122 = slw door
    assert res.payload.kind == "enter"
    assert res.state.players[0].ms == 3
    assert res.payload.turn_over is False
    assert ENTER_COST == 5


# --------------------------------------------------------------------------- #
# ms economy / turn end: ms=0 ends the turn; a handler-forced ms=0 too.       #
# --------------------------------------------------------------------------- #
def test_ms_zero_ends_turn():
    # One ms left: a step spends it to 0 and signals turn-end.
    st = _state(po=162, ms=1)
    city = _city()
    res = try_move(st, city, RIGHT)  # 162 -> 163, ms 1 -> 0
    assert res.payload.kind == "step"
    assert res.state.players[0].ms == 0
    assert res.payload.turn_over is True  # ms<=0 -> turn ends (mf-prg.bas:2005)
    assert st.players[0].ms == 1  # input untouched


def test_handler_forced_ms_zero_ends_turn():
    # A handler that drives ms to 0 (MsChange semantics) also ends the turn:
    # the next move attempt is refused because the turn is already over.
    st = _state(po=162, ms=0)
    city = _city()
    res = try_move(st, city, RIGHT)
    assert res.payload.turn_over is True
    assert res.payload.kind == "turn_over"  # no move happens; turn already ended
    assert res.status == "turn_over"
    assert res.state is st  # no commit — the input state is returned untouched
    assert st.players[0].po == 162  # unchanged
    # A blocked move emits an event with zero effects.
    assert res.events == [
        MoveBlocked(player=0, from_cell=162, target=163, delta=RIGHT, reason="turn_over")
    ]
    assert res.effects == []


# --------------------------------------------------------------------------- #
# Turn rotation: the engine's rotation (AdvanceTurn) moves active_player,      #
# wraps, and a full round advances the MONTH by 1 (year only every 12 rounds,  #
# KTD-4); the config's movement-points hook replenishes ms = tr.               #
# --------------------------------------------------------------------------- #
def test_turn_rotation_and_ms_replenish():
    st = _state(ms=3, active=0, players=2, vehicle=0)  # player 0 spent down to ms=3
    year0 = st.clock.year
    month0 = st.clock.month
    st, over = next_turn_by_hand(st)
    # Rotated to player 1; player 1's ms replenished to tr(0) = 25.
    assert st.clock.active_player == 1
    assert st.players[1].ms == 25
    assert st.clock.year == year0  # no wrap yet
    assert st.clock.month == month0  # no wrap yet
    # Advance again -> wraps to player 0, a full round -> month + 1 (year unchanged).
    st, over = next_turn_by_hand(st)
    assert st.clock.active_player == 0
    assert st.players[0].ms == 25  # replenished on wrap
    assert st.clock.year == year0  # a single round is a MONTH, not a year (KTD-4)
    assert st.clock.month == month0 + 1


def test_a_wrap_reaching_the_end_year_reports_game_over():
    """The game-over check fires when a wrap reaches end_year (12th month wrap)."""
    st = _state(ms=0, active=0, players=1, vehicle=0)
    # month=11 (the 12th round of the year): the NEXT wrap rolls year 1929 -> 1930.
    st = replace(st, clock=replace(st.clock, year=1929, month=11, end_year=1930))

    st, over = next_turn_by_hand(st)
    assert st.clock.year == 1930
    assert st.clock.month == 0  # wrapped
    assert over is True


def test_single_player_wraps_every_turn():
    st = _state(ms=0, active=0, players=1, vehicle=0)
    year0 = st.clock.year
    month0 = st.clock.month
    st, _over = next_turn_by_hand(st)
    assert st.clock.active_player == 0  # wrapped to itself
    assert st.players[0].ms == 25  # replenished
    assert st.clock.year == year0  # a one-player round is one MONTH, not a year
    assert st.clock.month == month0 + 1


def test_twelve_full_rounds_advance_the_year_exactly_once():
    """KTD-4 headline: 12 full rounds (one lap of the wrap each) advance year by
    exactly 1, and month is visible/incrementing at every intermediate wrap."""
    st = _state(ms=0, active=0, players=1, vehicle=0)
    year0 = st.clock.year
    for expected_month in range(1, 12):
        st, over = next_turn_by_hand(st)
        assert st.clock.year == year0  # no year rollover yet
        assert st.clock.month == expected_month
        assert over is False
    # The 12th wrap rolls the year and resets month to 0.
    st, over = next_turn_by_hand(st)
    assert st.clock.year == year0 + 1
    assert st.clock.month == 0


# --------------------------------------------------------------------------- #
# THE HEADLINE: walk to the pub, recruit refused at rank 1 (:12100-12102).   #
# --------------------------------------------------------------------------- #
def test_walk_to_pub_recruit_refused_at_rank_1():
    # Load the pub shell (its handler must be registered -> load the config).
    from engine.config_loader import load_game_config

    load_game_config(_CONFIG_DIR)
    pub = load_location(yaml.safe_load(_PUB_SHELL.read_text(encoding="utf-8")))

    city = _city()
    # Stand at 473 (code 156); UP (-40) targets 433 = pub door (la=2, ln=1).
    assert city.code(473) == 156
    assert city.door(433) == (2, 1)
    st = _state(po=473, ms=25, rank=1, active=0)
    res = try_move(st, city, UP)
    assert res.payload.kind == "enter"
    assert res.payload.la == 2 and res.payload.ln == 1
    state = res.state  # adopt the returned state (fold effects forward)
    assert state.players[0].po == 473  # did not stand on the door
    assert state.players[0].last_location == 1  # ln seam set

    # The menu is fixed (#122): recruit is offered at rank 1 and refuses inside its
    # handler (:12100-12102), naming the rank.
    ids = [o.id for o in available_options(pub, state, ln=res.payload.ln)]
    assert ids == ["drink", "recruit", "tip", "job", "leave"]
    src = scripted()
    result = run_option(pub, "recruit", state, ln=res.payload.ln, input_source=src)
    assert src.message_keys() == ["locations.pub.rank_too_low"]
    # No effects committed — the refusal changes nothing.
    assert result.effects == []
    assert result.state.players[0].ka == 5000


def test_pub_recruit_offered_whatever_the_rank_and_gang_size():
    from engine.config_loader import load_game_config

    load_game_config(_CONFIG_DIR)
    pub = load_location(yaml.safe_load(_PUB_SHELL.read_text(encoding="utf-8")))
    for st in (_state(rank=5, roster=1), _state(rank=5, roster=10), _state(rank=1, roster=10)):
        ids = [o.id for o in available_options(pub, st, ln=1)]
        assert ids == ["drink", "recruit", "tip", "job", "leave"]


# --------------------------------------------------------------------------- #
# Strings: the pub denial string loads verbatim.                              #
# --------------------------------------------------------------------------- #
def test_pub_strings_load_verbatim():
    data = yaml.safe_load(_PUB_STRINGS.read_text(encoding="utf-8"))

    def get(dotted):
        if dotted in data:
            return data[dotted]
        node = data
        for part in dotted.split("."):
            node = node[part]
        return node

    assert get("locations.pub.entry_prompt") == (
        "'EH, AMIGO, WILLST DU ''NE MILCH? ODER   SUCHST DU WAS ANDERES?'"
    )
    # Rank-too-low recruit refusal (mf-prg.bas:12101-12102), verbatim.
    assert get("locations.pub.rank_too_low") == (
        "'als {rank} kannst du noch keine eigenen leute haben!'"
    )
    # Recruit menu option (mf-prg.bas:12100 option 2), verbatim from research —
    # note the 4-space run before GANG and the trailing '...'. NOT paraphrased.
    assert get("locations.pub.menu.recruit") == (
        "'ICH SUCHE EIN PAAR JUNGS FUER MEINE    GANG. MAL SEHEN, WER DA IST...'"
    )

"""Tests for the movement + turn economy + pub denial — U9.

Proof-first: written and observed RED (``engine.movement`` missing) BEFORE the
implementation.

This exercises the whole movement spine on the REAL 40×25 city map (loaded from
the config's ``content/map/city.yaml``): the ``ms`` movement economy, stepping on
walkable street cells (code 156), the RESOLVED door-entry mechanic (a move whose
TARGET cell is a door-table entry ENTERS that location — NOT adjacency), the
turn-loop rotation + ``ms`` replenishment from the vehicle table, the ``ln`` seam
that sets the active player's ``last_location`` on entry (formalizing what U7
stubbed), the pub recruit denial reached by WALKING at rank 1, and the police
interrupt gated off at rank 1.

Ports (all oracle-gated):
  * turn loop        — mf-prg.bas:1010-1013 (sp wrap, ms = tr(tm(sp)))
  * movement         — mf-prg.bas:2000-2065 (deltas, 156-step, door-entry, walls)
  * police interrupt — mf-prg.bas:2041 (rank>3 gate; never fires at rank 1)
  * pub recruit      — mf-prg.bas:12100/12105 (ra>4 AND gz<10)
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.locations import available_options, load_location
from engine.movement import (
    RIGHT,
    UP,
    DOWN,
    advance_turn,
    load_city,
    try_move,
)
from engine.state import Clock, Config, Gangster, GameState, MapState, Player

_CONFIG_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
)
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"
_PUB_SHELL = _CONFIG_DIR / "content" / "locations" / "pub.yaml"
_PUB_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "pub.yaml"

# On-foot vehicle table (index 0 -> tr=25), enough for the turn loop.
_VEHICLES = [{"name": "fuesse", "tank": 50, "tr": 25}]


def _city():
    return load_city(yaml.safe_load(_CITY.read_text(encoding="utf-8")))


def _state(*, po=162, ms=25, rank=1, active=0, players=1, vehicle=0, roster=1):
    plist = [
        Player(
            ka=5000,
            po=po,
            ms=ms,
            rank=rank,
            vehicle=vehicle,
            roster=[Gangster() for _ in range(roster)],
        )
        for _ in range(players)
    ]
    return GameState(
        players=plist,
        clock=Clock(active_player=active, player_count=players),
        config=Config(),
        map=MapState(),
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
    assert res.kind == "step"
    assert st.players[0].po == 163  # moved by +1
    assert st.players[0].ms == 24  # spent exactly 1


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
    before_po, before_ms = st.players[0].po, st.players[0].ms
    res = try_move(st, city, DOWN)
    assert res.kind == "wall"
    assert st.players[0].po == before_po  # unchanged
    assert st.players[0].ms == before_ms  # nothing spent


# --------------------------------------------------------------------------- #
# Door entry (THE resolved mechanic): target-cell lookup, NOT adjacency.      #
# From 162, UP (-40) targets 122 = slw door (la=1, ln=1): enter, 5 ms, po     #
# stays at 162, and the ln seam sets last_location=1.                         #
# --------------------------------------------------------------------------- #
def test_enter_slw_via_door_target():
    st = _state(po=162, ms=25, active=0)
    city = _city()
    res = try_move(st, city, UP)
    assert res.kind == "enter"
    assert res.la == 1 and res.ln == 1
    # You do NOT stand on the door cell — po is unchanged.
    assert st.players[0].po == 162
    # Location-visit cost is 5 ms (mf-prg.bas:2060).
    assert st.players[0].ms == 20
    # The ln seam: entering set the active player's last_location = ln.
    assert st.players[0].last_location == 1


def test_enter_charges_5ms_unconditionally_and_can_go_negative():
    # mf-prg.bas:2060 charges ms-=5 UNCONDITIONALLY, then checks >0 — so entering
    # with fewer than 5 ms drives ms negative and ends the turn (faithful; entry is
    # NOT blocked for lack of budget).
    st = _state(po=162, ms=3, active=0)
    res = try_move(st, _city(), UP)  # target 122 = slw door
    assert res.kind == "enter"
    assert st.players[0].ms == -2  # 3 - 5, not clamped, not blocked
    assert res.turn_over is True   # ms <= 0 ends the turn


# --------------------------------------------------------------------------- #
# ms economy / turn end: ms=0 ends the turn; a handler-forced ms=0 too.       #
# --------------------------------------------------------------------------- #
def test_ms_zero_ends_turn():
    # One ms left: a step spends it to 0 and signals turn-end.
    st = _state(po=162, ms=1)
    city = _city()
    res = try_move(st, city, RIGHT)  # 162 -> 163, ms 1 -> 0
    assert res.kind == "step"
    assert st.players[0].ms == 0
    assert res.turn_over is True  # ms<=0 -> turn ends (mf-prg.bas:2005)


def test_handler_forced_ms_zero_ends_turn():
    # A handler that drives ms to 0 (MsChange semantics) also ends the turn:
    # the next move attempt is refused because the turn is already over.
    st = _state(po=162, ms=0)
    city = _city()
    res = try_move(st, city, RIGHT)
    assert res.turn_over is True
    assert res.kind == "turn_over"  # no move happens; turn already ended
    assert st.players[0].po == 162  # unchanged


# --------------------------------------------------------------------------- #
# Turn rotation: advance rotates active_player, wraps, replenishes ms = tr,   #
# and a full round advances the year by 1.                                    #
# --------------------------------------------------------------------------- #
def test_turn_rotation_and_ms_replenish():
    st = _state(ms=3, active=0, players=2, vehicle=0)
    st.players[0].ms = 3  # spent down
    year0 = st.clock.year
    advance_turn(st, _VEHICLES)
    # Rotated to player 1; player 1's ms replenished to tr(0) = 25.
    assert st.clock.active_player == 1
    assert st.players[1].ms == 25
    assert st.clock.year == year0  # no wrap yet
    # Advance again -> wraps to player 0, a full round -> year + 1.
    advance_turn(st, _VEHICLES)
    assert st.clock.active_player == 0
    assert st.players[0].ms == 25  # replenished on wrap
    assert st.clock.year == year0 + 1


def test_single_player_wraps_every_turn():
    st = _state(ms=0, active=0, players=1, vehicle=0)
    year0 = st.clock.year
    advance_turn(st, _VEHICLES)
    assert st.clock.active_player == 0  # wrapped to itself
    assert st.players[0].ms == 25  # replenished
    assert st.clock.year == year0 + 1  # a one-player round is one turn


# --------------------------------------------------------------------------- #
# Police interrupt: rank gate (rank>3) is FALSE at rank 1 -> never fires,     #
# regardless of ms / rng.                                                     #
# --------------------------------------------------------------------------- #
def test_police_interrupt_never_fires_at_rank_1():
    from engine.movement import police_interrupt_would_fire

    # rank 1, and even with ms a multiple of 20 and the rng "hit", the gate is
    # rank>3 which is false at rank 1.
    st = _state(po=162, ms=20, rank=1)

    class _AlwaysRng:
        def range(self, n):
            return 0  # rnd(5)==0 would satisfy the roll gate

    assert police_interrupt_would_fire(st, ms=20, rng=_AlwaysRng()) is False
    # And it WOULD be eligible at rank 4+ (the gate is correct for later ranks).
    st4 = _state(po=162, ms=20, rank=4)
    assert police_interrupt_would_fire(st4, ms=20, rng=_AlwaysRng()) is True


# --------------------------------------------------------------------------- #
# THE HEADLINE: walk to the pub, recruit denied at rank 1 (guard rank>4).     #
# --------------------------------------------------------------------------- #
def test_walk_to_pub_recruit_denied_at_rank_1():
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
    assert res.kind == "enter"
    assert res.la == 2 and res.ln == 1
    assert st.players[0].po == 473  # did not stand on the door
    assert st.players[0].last_location == 1  # ln seam set

    # available_options at rank 1 EXCLUDES recruit (guard rank>4 fails).
    avail = available_options(pub, st, ln=res.ln)
    ids = [o.id for o in avail]
    assert "recruit" not in ids
    recruit = next(o for o in pub.options if o.id == "recruit")
    assert recruit.on_denied == "locations.pub.rank_too_low"
    # No effects committed — denial is a shell exclusion, nothing mutated.
    assert st.players[0].ka == 5000


def test_pub_recruit_available_when_rank_high_and_room():
    from engine.config_loader import load_game_config

    load_game_config(_CONFIG_DIR)
    pub = load_location(yaml.safe_load(_PUB_SHELL.read_text(encoding="utf-8")))
    # rank 5 (>4) AND gang size 1 (<10) -> recruit available.
    st = _state(rank=5, roster=1)
    ids = [o.id for o in available_options(pub, st, ln=1)]
    assert "recruit" in ids
    # Full gang (10) -> denied even at high rank.
    full = _state(rank=5, roster=10)
    ids_full = [o.id for o in available_options(pub, full, ln=1)]
    assert "recruit" not in ids_full


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

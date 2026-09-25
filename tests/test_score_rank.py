"""Tests for the fused ``ScoreAndRank`` effect (U3, KTD-5).

``ScoreAndRank`` is the single-effect port of ``gosub 1160/1165``:
- ``1160``: ``gf = gf + (x*x8)``, capped at 100 (``x8`` == ``Config.score_mult``)
- ``1161``: floored at 0
- ``1165``: ``nr = int(gf/rank_divisor)+1`` — recomputed from the CLAMPED gf

Rank is computed from the *clamped* gf inside one ``apply`` branch, so the
clamp-then-rank ordering hazard a two-effect helper would face cannot occur.
The ``rank_divisor`` (11.1) is a config parameter, NOT hardcoded in the engine.
"""

from __future__ import annotations

from engine.effects import ScoreAndRank, apply
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster


def make_state(*, gf=50.0, score_mult=1.0):
    p = Player(name="p0", ka=5000, gf=gf, roster=[Gangster(name="g0")])
    return GameState(
        players=[p],
        clock=Clock(active_player=0),
        config=Config(score_mult=score_mult),
    )


def test_score_and_rank_normal_delta_and_rank():
    # gf=50, amount=2, x8=1.0 -> gf=52; nr=int(52/11.1)+1 = 4+1 = 5.
    state = make_state(gf=50.0, score_mult=1.0)
    out = apply(state, ScoreAndRank(amount=2, rank_divisor=11.1))
    assert out.players[0].gf == 52.0
    assert out.players[0].nr == int(52.0 / 11.1) + 1 == 5


def test_score_and_rank_caps_at_100_and_ranks_from_clamped():
    # gf=99, amount=2 -> raw 101 clamps to 100; nr computed from 100, NOT 101.
    state = make_state(gf=99.0, score_mult=1.0)
    out = apply(state, ScoreAndRank(amount=2, rank_divisor=11.1))
    assert out.players[0].gf == 100.0
    assert out.players[0].nr == int(100.0 / 11.1) + 1  # rank from clamped gf


def test_score_and_rank_floors_at_0():
    state = make_state(gf=1.0, score_mult=1.0)
    out = apply(state, ScoreAndRank(amount=-5, rank_divisor=11.1))
    assert out.players[0].gf == 0.0
    assert out.players[0].nr == int(0.0 / 11.1) + 1 == 1


def test_score_and_rank_weights_by_score_mult():
    # x8=2.0, amount=3 -> gf += 6.
    state = make_state(gf=10.0, score_mult=2.0)
    out = apply(state, ScoreAndRank(amount=3, rank_divisor=11.1))
    assert out.players[0].gf == 16.0


def test_score_and_rank_purity():
    state = make_state(gf=50.0)
    out = apply(state, ScoreAndRank(amount=2, rank_divisor=11.1))
    assert state.players[0].gf == 50.0  # ORIGINAL untouched
    assert out is not state


def test_score_and_rank_divisor_is_a_parameter_not_hardcoded():
    # Config-boundary (KTD-10): pass a NON-11.1 divisor -> rank computed from IT.
    state = make_state(gf=60.0, score_mult=1.0)
    out = apply(state, ScoreAndRank(amount=0, rank_divisor=10.0))
    assert out.players[0].nr == int(60.0 / 10.0) + 1 == 7


def test_score_and_rank_explicit_player_targeting():
    p0 = Player(name="p0", gf=50.0, roster=[Gangster()])
    p1 = Player(name="p1", gf=20.0, roster=[Gangster()])
    state = GameState(players=[p0, p1], clock=Clock(active_player=0), config=Config(score_mult=1.0))
    out = apply(state, ScoreAndRank(amount=5, rank_divisor=11.1, player=1))
    assert out.players[1].gf == 25.0
    assert out.players[0].gf == 50.0  # active untouched

"""Tests for the fused ``ScoreAndRank`` effect (U3, KTD-5).

``ScoreAndRank`` is the single-effect port of ``gosub 1160/1165``:
- ``1160``: ``gf = gf + (x*x8)``, capped at 100 (``x8`` == ``formula_params["score_mult"]``)
- ``1161``: floored at 0
- ``1165``: ``nr = int(gf/rank_divisor)+1`` — recomputed from the CLAMPED gf

Rank is computed from the *clamped* gf inside one ``apply`` branch, so the
clamp-then-rank ordering hazard a two-effect helper would face cannot occur.
The ``rank_divisor`` (11.1) is a config parameter, NOT hardcoded in the engine.
"""

from __future__ import annotations

from engine.effects import apply
from data.game_configs.mafia_1920s.effects import ScoreAndRank
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster
import data.game_configs.mafia_1920s.state as game


def make_state(*, gf=50.0, score_mult=1.0):
    p = Player(name="p0", ka=5000, gf=gf, roster=(Gangster(name="g0"),))
    return GameState(
        players=(p,),
        clock=Clock(active_player=0),
        config=Config(formula_params={"score_mult": score_mult}),
    )


def test_score_and_rank_normal_delta_and_rank():
    # gf=50, amount=2, x8=1.0 -> gf=52; nr=int(52/11.1)+1 = 4+1 = 5.
    state = make_state(gf=50.0, score_mult=1.0)
    out = apply(state, ScoreAndRank(amount=2, rank_divisor=11.1))
    assert out.players[0].gf == 52.0
    assert game.next_rank(out.players[0]) == int(52.0 / 11.1) + 1 == 5


def test_score_and_rank_caps_at_100_and_ranks_from_clamped():
    # gf=99, amount=2 -> raw 101 clamps to 100; nr computed from 100, NOT 101.
    state = make_state(gf=99.0, score_mult=1.0)
    out = apply(state, ScoreAndRank(amount=2, rank_divisor=11.1))
    assert out.players[0].gf == 100.0
    assert game.next_rank(out.players[0]) == int(100.0 / 11.1) + 1  # rank from clamped gf


def test_score_and_rank_floors_at_0():
    state = make_state(gf=1.0, score_mult=1.0)
    out = apply(state, ScoreAndRank(amount=-5, rank_divisor=11.1))
    assert out.players[0].gf == 0.0
    assert game.next_rank(out.players[0]) == int(0.0 / 11.1) + 1 == 1


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
    assert game.next_rank(out.players[0]) == int(60.0 / 10.0) + 1 == 7


def test_score_and_rank_explicit_player_targeting():
    p0 = Player(name="p0", gf=50.0, roster=(Gangster(),))
    p1 = Player(name="p1", gf=20.0, roster=(Gangster(),))
    state = GameState(
        players=(p0, p1),
        clock=Clock(active_player=0),
        config=Config(formula_params={"score_mult": 1.0}),
    )
    out = apply(state, ScoreAndRank(amount=5, rank_divisor=11.1, player=1))
    assert out.players[1].gf == 25.0
    assert out.players[0].gf == 50.0  # active untouched


# --------------------------------------------------------------------------- #
# The exact score under the c64_float_score house rule's intent arm (R15)      #
# --------------------------------------------------------------------------- #
#: A typed weight with no exact binary fraction and more digits than a double keeps
#: after a round trip through ``float`` of the running sum.
_THIRD = "0.3333333333333333"
_NAMES = [("alcapone", "outfit"), ("bugsy", "syndikat")]


def _intent_game(weight: str = _THIRD, players=_NAMES):
    from data.game_configs.mafia_1920s.setup import new_game

    return new_game(
        seed=1,
        end_year=1930,
        score_weight=weight,
        players=players,
        house_rules={"c64_float_score": "intent"},
    )


def _awards(state, amounts, player: int):
    for x in amounts:
        state = apply(state, ScoreAndRank(amount=x, rank_divisor=11.1, player=player))
    return state


def _exact(player):
    """The exact score the value map holds, as a ``Decimal`` (``None`` if it holds none)."""
    from decimal import Decimal

    text = player.values.get("exact_score")
    return None if text is None else Decimal(text)


def _derived(player) -> bool:
    exact = _exact(player)
    return exact is not None and player.gf == float(exact)


def test_intent_split_awards_score_and_rank_as_one_award():
    """R15: 4+2 awards and one award of 6 at weight 1/3 are the same exact score, 6w,
    and so the same score and rank. Summing through ``float`` between the awards lands
    the split on 2.0 and the single award on 1.9999999999999998."""
    from decimal import Decimal

    split = _awards(_intent_game(), [4, 2], player=0)
    whole = _awards(_intent_game(), [6], player=0)
    a, b = split.players[0], whole.players[0]
    assert a.gf == b.gf
    assert game.next_rank(a) == game.next_rank(b)
    assert _exact(a) == _exact(b)
    assert _exact(a) == 6 * Decimal(_THIRD)


def test_intent_split_awards_rank_alike_at_year_end():
    """R15 at ``:40105-40106``: two players with equal exact scores share the win."""
    from engine.game_end import run_year_end

    state = _awards(_awards(_intent_game(), [4, 2], player=0), [6], player=1)
    seen = []
    run_year_end(state, input_source=lambda i: seen.append(i))
    assert [m.params["name"] for m in seen if m.key == "game_end.tie_name"] == [
        "alcapone",
        "bugsy",
    ]


def test_intent_year_end_ranks_by_the_exact_score_not_its_float():
    """Two exact scores with the same float: the higher exact score wins alone."""
    from dataclasses import replace

    from engine.game_end import run_year_end

    state = _intent_game()
    low, high = "0.3", "0.30000000000000001"  # float() makes both 0.3
    players = tuple(
        replace(p, gf=0.3, values={**p.values, "exact_score": exact})
        for p, exact in zip(state.players, (low, high))
    )
    seen = []
    run_year_end(replace(state, players=players), input_source=lambda i: seen.append(i))
    assert [m.params["name"] for m in seen if m.key == "game_end.winner"] == ["bugsy"]


def test_intent_reads_the_weight_as_typed():
    """The typed weight's text, not its float: 3 * 0.33333333333333333 is
    0.99999999999999999, where the float's repr gives 0.9999999999999999."""
    from decimal import Decimal

    weight = "0.33333333333333333"
    out = _awards(_intent_game(weight), [3], player=0)
    assert _exact(out.players[0]) == 3 * Decimal(weight)


def test_a_weight_longer_than_28_digits_is_stored_as_typed():
    """``Decimal``'s default context holds 28 digits: normalizing ``0.1999...9`` (33
    digits) under it stores ``0.2``, a weight above the one typed."""
    from data.game_configs.mafia_1920s.score_weight import _exact_weight_text

    weight = "0.199999999999999999999999999999999"
    assert _exact_weight_text(weight) == weight
    assert _exact_weight_text(" 1.50000000000000000000000000000000000 ") == "1.5"


def test_intent_gf_is_the_float_of_the_exact_score_after_every_write():
    """Every intent-arm write -- the award (:1160), the shop's steps (:13065/:13072,
    :13073) and the turn's cut (:1013) -- leaves ``gf`` the float of the exact score."""
    from data.game_configs.mafia_1920s.effects import WeaponBuyScore
    from engine.interactions import run
    from engine.locations import HANDLERS
    from engine.turns import SCORE_TRUNCATION_HOOK_KEY

    state = _intent_game()
    steps = [
        lambda s: apply(s, ScoreAndRank(amount=7, rank_divisor=11.1)),
        lambda s: apply(s, WeaponBuyScore()),
        lambda s: apply(s, WeaponBuyScore(downgrade=True)),
        lambda s: run(HANDLERS[SCORE_TRUNCATION_HOOK_KEY], lambda i: None, state=s).state,
        lambda s: apply(s, ScoreAndRank(amount=-1, rank_divisor=11.1)),
    ]
    for n, step in enumerate(steps):
        state = step(state)
        assert _derived(state.players[0]), (n, state.players[0].gf, _exact(state.players[0]))

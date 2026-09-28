"""Building a gang end to end: recruit three gangsters, arm each, train each.

Drives the real pub and weapon-shop shells through ``engine.actions.run_option``
(guards, handlers and effect commits together) on a real ``new_game`` state, adopting
the returned state after every step, and checks the resulting gang.

The three recruits are unarmed candidates, so buying a weapon settles without a
trade-in offer:

    candidate  1 joe, 'das hirn'   2000$  kraft 20  int 90  brut 30  -> knueppel (2)
    candidate  2 eddie, der spieler 2500$  kraft 30  int 60  brut 40  -> schlagkette (3)
    candidate 15 billy              2200$  kraft 20  int 10  brut 30  -> revolver (5)

Everything happens at weapon-shop tile ln=2: it stocks weapons 1-5 with no stock roll
(mf-prg.bas:13012), and its range training adds kraft +5, intelligenz +3 and
brutalitaet +5 (mf-prg.bas:13125-13127, C64 true=-1: 2-3*(ln=2) is 5 at ln=2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.actions import run_option
from engine.config_loader import load_game_config
from engine.locations import load_location
from tests.helpers import StubRng, scripted, with_player, with_tenancy
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)
_LOCATIONS = _CONFIG_DIR / "content" / "locations"
_GANGSTERS = yaml.safe_load((_CONFIG_DIR / "entities" / "gangsters.yaml").read_text())["gangsters"]
_WEAPONS = yaml.safe_load((_CONFIG_DIR / "entities" / "weapons.yaml").read_text())["weapons"]

#: candidate id -> the weapon index bought for that recruit.
_PLAN = {1: 2, 2: 3, 15: 5}
_RECRUITS = list(_PLAN)  # recruit order == roster order 1..3 (the boss is roster[0])

_START_CASH = 50_000
_START_SCORE = 45.0  # rank int(45/11.1)+1 = 5: recruit needs rank > 4
_RANK = 5
_PUB_LN = 1  # any pub tile but 3 (mf-prg.bas:12107: ln=3 has nobody to recruit)
_WAF_LN = 2


def _shell(key):
    return load_location(yaml.safe_load((_LOCATIONS / f"{key}.yaml").read_text()))


def _start_state():
    state = _CONFIG.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("al", "outfit")]
    )
    state = with_player(state, ka=_START_CASH, gf=_START_SCORE, rank=_RANK)
    # Housing: one apartment slot rented by player 0 (mf-prg.bas:12103).
    return with_tenancy(state, ln=1, owner=0)


def _run(shell, option, state, ln, answers, rng):
    state = with_player(state, last_location=ln)
    source = scripted(*answers)
    result = run_option(shell, option, state, ln=ln, input_source=source, rng=rng)
    assert result.status == "completed", (option, result.status)
    return result.state, source


def _build_gang():
    """Recruit, arm and train; return (start state, final state, per-step records)."""
    pub, waf = _shell("pub"), _shell("waf")
    start = state = _start_state()
    seen = {}

    # Recruit: roll 3 offers (range(4) -> 3), then one candidate draw each (range(30)).
    # One Confirm per offer. The strict stub fails on any draw not scripted here.
    rng = StubRng(3, *_RECRUITS)
    state, seen["recruit"] = _run(pub, "recruit", state, _PUB_LN, [True] * 3, rng)
    assert rng.calls == [("range", 4)] + [("range", 30)] * 3, rng.calls

    # Arm: weapon index, then the gangster's number (:1145, 1-based: the boss is 1).
    # Schlagkette is first offered to billy (roster 3, brutalitaet 30 < 40), who is
    # refused, then to eddie (roster 2).
    buy_answers = {
        1: [2, 2],  # knueppel -> joe
        2: [3, 4, 3],  # schlagkette -> billy (refused), then eddie
        3: [5, 4],  # revolver -> billy
    }
    for roster_idx, answers in buy_answers.items():
        state, seen[f"buy{roster_idx}"] = _run(waf, "buy", state, _WAF_LN, answers, StubRng())

    # Train each recruit at the range: gangster number, venue 0 (range; rank >= 5
    # asks), confirm.
    for roster_idx in (1, 2, 3):
        state, seen[f"train{roster_idx}"] = _run(
            waf, "train", state, _WAF_LN, [roster_idx + 1, 0, True], StubRng()
        )

    return start, state, seen


@pytest.fixture(scope="module")
def gang():
    return _build_gang()


def _player(state):
    return state.players[0]


def test_the_gang_is_the_boss_plus_the_three_recruits_in_order(gang):
    start, final, _ = gang
    roster = _player(final).roster
    assert len(roster) == 4
    assert roster[0] == _player(start).roster[0], "the boss must be untouched"
    assert [g.name for g in roster[1:]] == [_GANGSTERS[c]["name"] for c in _RECRUITS]


def test_each_recruit_carries_a_distinct_weapon_as_bought(gang):
    _, final, _ = gang
    weapons = [g.weapon for g in _player(final).roster[1:]]
    assert weapons == [_PLAN[c] for c in _RECRUITS]
    assert len(set(weapons)) == 3


def test_the_stat_gate_refused_billy_the_schlagkette(gang):
    _, _, seen = gang
    assert "locations.waf.not_brutal" in seen["buy2"].message_keys()
    assert "locations.waf.not_brutal" not in seen["buy1"].message_keys()
    assert "locations.waf.not_brutal" not in seen["buy3"].message_keys()


def test_training_raised_each_recruits_stats_by_the_ln2_range_gains(gang):
    _, final, _ = gang
    gains = {"kraft": 5, "intelligenz": 3, "brutalitaet": 5}
    for g, c in zip(_player(final).roster[1:], _RECRUITS, strict=True):
        for stat, gain in gains.items():
            expected = min(_GANGSTERS[c][stat] + gain, 99)
            assert g.attrs[stat] == expected, (g.name, stat)


def test_recruits_join_at_energy_5_and_are_marked_hired(gang):
    _, final, _ = gang
    assert [g.vitality for g in _player(final).roster[1:]] == [5, 5, 5]
    assert set(_RECRUITS) <= set(game.hired_ids(final))


def test_cash_paid_for_exactly_three_recruits_three_weapons_and_three_trainings(gang):
    _, final, _ = gang
    recruits = sum(_GANGSTERS[c]["price"] for c in _RECRUITS)
    weapons = sum(_WEAPONS[w]["price"] for w in _PLAN.values())
    training = 3 * (800 + 200 * _RANK)  # mf-prg.bas:13110, range price
    assert _player(final).ka == _START_CASH - recruits - weapons - training


def test_score_rose_by_one_per_first_weapon_and_one_per_training(gang):
    _, final, _ = gang
    # A first weapon raises the score by x8=1 while gf<100 (:13065, true=-1); range
    # training adds 1 (:13130).
    assert _player(final).gf == pytest.approx(_START_SCORE + 3 + 3)

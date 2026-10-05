"""Tests for the waf.train handler — U6.

Proof-first. Ports mf-prg.bas:13100-13175 (schiesstand range + trainingscamp).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config_loader import load_game_config
from engine.effects import MoneyChange, StatChangeCapped
from data.game_configs.mafia_1920s.effects import ScoreAndRank
from engine.interactions import (
    Ack,
    Acknowledge,
    Confirm,
    Ctx,
    PromptChoice,
    PromptInt,
    ShowMessage,
)
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster
from tests.helpers import StubRng as _StubRng, run_pure, scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PARAMS = {
    "stat_cap": 99,
    "rank_divisor": 11.1,
    "range_base": 800,
    "range_per_rank": 200,
    "camp_base": 2500,
    "camp_per_rank": 500,
    "camp_gain_min": 8,
    "camp_gain_max": 15,
}


def _state(*, ka=100000, ln=3, rank=1, score_mult=1.0, roster=None):
    active = Player(
        name="p0",
        ka=ka,
        rank=rank,
        roster=(
            roster
            if roster is not None
            else (Gangster(name="g0", kraft=10, intelligenz=10, brutalitaet=10),)
        ),
        last_location=ln,
    )
    return GameState(
        players=(active,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params={**_PARAMS, "score_mult": score_mult}),
    )


def _source(answers):
    iters = {k: iter(v) for k, v in answers.items()}

    def source(interaction):
        if isinstance(interaction, (ShowMessage, Acknowledge)):
            # #43: narration is DELIVERED, not asked. It consumes no scripted answer,
            # and the driver acks regardless of what we return here. So does the
            # :1100 key wait (``Acknowledge(KEY_WAIT_SCREEN)``).
            return None
        for typ, it in iters.items():
            if isinstance(interaction, typ):
                return next(it)
        raise AssertionError(f"unscripted interaction {interaction!r}")

    return source


def _observe(handler, state, rng, answers):
    """Step out-of-band recording every yield (incl. auto-acked ShowMessage)."""
    iters = {k: iter(v) for k, v in answers.items()}
    ctx = Ctx(state=state, rng=rng)
    gen = handler(ctx)
    seen = []
    interaction = next(gen)
    try:
        while True:
            seen.append(interaction)
            if isinstance(interaction, (ShowMessage, Acknowledge)):
                interaction = gen.send(Ack)
            else:
                for typ, it in iters.items():
                    if isinstance(interaction, typ):
                        interaction = gen.send(next(it))
                        break
                else:
                    raise AssertionError(f"unscripted {interaction!r}")
    except StopIteration:
        pass
    return seen


# --------------------------------------------------------------------------- #
# No gangster (R10)                                                            #
# --------------------------------------------------------------------------- #
def test_no_gangster_aborts():
    st = _state(roster=())
    seen = _observe(HANDLERS["waf.train"], st, _StubRng(), {})
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "locations.waf.no_gangster" in keys
    result = run_pure(HANDLERS["waf.train"], _source({}), state=_state(roster=()), rng=_StubRng())
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Venue gate (R11)                                                            #
# --------------------------------------------------------------------------- #
def test_venue_gate_rank_below_5_range_only():
    # rank 4 -> no (s)/(t) prompt: after the gangster pick (the shared picker's number
    # prompt) it goes straight to the range cost (no PromptChoice at all).
    st = _state(rank=4)
    seen = _observe(
        HANDLERS["waf.train"],
        st,
        _StubRng(),
        {PromptInt: [1], Confirm: [False]},  # pick gangster 1, decline range
    )
    assert [i.key for i in seen if isinstance(i, PromptInt)] == ["turn.picker.prompt"]
    assert not any(isinstance(i, PromptChoice) for i in seen)  # no venue choice


def test_venue_gate_rank_5_offers_choice():
    st = _state(rank=5)
    seen = _observe(
        HANDLERS["waf.train"],
        st,
        _StubRng(),
        {PromptInt: [1], PromptChoice: [0], Confirm: [False]},  # gangster 1, range, decline
    )
    prompt_choices = [i for i in seen if isinstance(i, PromptChoice)]
    assert [i.key for i in prompt_choices] == ["locations.waf.venue_prompt"]


# --------------------------------------------------------------------------- #
# Range gains (R12)                                                           #
# --------------------------------------------------------------------------- #
def test_range_default_ln_gains():
    # ln=3 (default): kr+5, int+3, brut+2. Cost 800+200*1 = 1000. Score x=1.
    st = _state(ln=3, rank=1, ka=100000)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    assert result.status == "completed"
    assert MoneyChange(-1000) in result.effects
    assert StatChangeCapped("kraft", 5, cap=99, gangster=0) in result.effects
    assert StatChangeCapped("intelligenz", 3, cap=99, gangster=0) in result.effects
    assert StatChangeCapped("brutalitaet", 2, cap=99, gangster=0) in result.effects
    assert ScoreAndRank(amount=1, rank_divisor=11.1) in result.effects


def test_range_ln1_intelligence_plus_five():
    # :13126 `in = in + 3 - 2*(ln=1)`; at ln=1 the relational is true = -1, so
    # in += 3 - 2*(-1) = 5. (#47 audit: this asserted 1 under the reversed pin.)
    st = _state(ln=1, rank=1, ka=100000)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    assert StatChangeCapped("intelligenz", 5, cap=99, gangster=0) in result.effects
    assert StatChangeCapped("kraft", 5, cap=99, gangster=0) in result.effects


def test_range_ln2_brutality_plus_five():
    # :13127 `bt = bt + 2 - 3*(ln=2)`; at ln=2 -> bt += 2 - 3*(-1) = 5.
    #
    # #47 audit: this asserted -1 — i.e. that a paid shooting-range session made the
    # gangster LESS brutal. That was the true=+1 reading; a training stat gain is
    # never negative in the source.
    st = _state(ln=2, rank=1, ka=100000)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    assert StatChangeCapped("brutalitaet", 5, cap=99, gangster=0) in result.effects


def test_range_gains_cap_at_99():
    # Gangster starts at kraft 98 -> +5 clamps to 99 (the effect carries cap=99, the
    # StatChangeCapped apply enforces it).
    roster = (Gangster(name="g", kraft=98, intelligenz=10, brutalitaet=10),)
    st = _state(ln=3, rank=1, ka=100000, roster=roster)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    assert result.state.players[0].roster[0].kraft == 99  # 98+5 clamped


def test_range_declined_no_effects():
    st = _state(ln=3, rank=1, ka=100000)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], Confirm: [False]}),
        state=st,
        rng=_StubRng(),
    )
    assert result.effects == []


def test_range_unaffordable_no_effects():
    st = _state(ln=3, rank=1, ka=100)  # need 1000
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    assert result.effects == []
    assert result.state.players[0].ka == 100


# --------------------------------------------------------------------------- #
# Camp gains (R13)                                                            #
# --------------------------------------------------------------------------- #
def test_camp_gains_each_stat_by_own_fnr_draw():
    # rank 5 -> venue choice; pick camp (1). Cost 2500+500*5 = 5000. Each of int/brut/kraft
    # rises by its OWN rng.hit(8,15). Score x=2.
    st = _state(ln=3, rank=5, ka=100000)
    rng = _StubRng(8, 12, 15)  # int gain 8, brut 12, kraft 15
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], PromptChoice: [1], Confirm: [True]}),  # gangster 1, camp, yes
        state=st,
        rng=rng,
    )
    assert result.status == "completed"
    assert MoneyChange(-5000) in result.effects
    assert StatChangeCapped("intelligenz", 8, cap=99, gangster=0) in result.effects
    assert StatChangeCapped("brutalitaet", 12, cap=99, gangster=0) in result.effects
    assert StatChangeCapped("kraft", 15, cap=99, gangster=0) in result.effects
    assert ScoreAndRank(amount=2, rank_divisor=11.1) in result.effects
    assert rng.calls == [("hit", 8, 15)] * 3  # three separate draws


def test_camp_unaffordable_no_effects():
    st = _state(ln=3, rank=5, ka=100)  # need 5000
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], PromptChoice: [1], Confirm: [True]}),
        state=st,
        rng=_StubRng(8, 8, 8),
    )
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Cancel atomicity                                                            #
# --------------------------------------------------------------------------- #
def test_stat_effects_apply_before_score():
    # Ordering: the three stat effects precede the ScoreAndRank in the effects list
    # (mirrors 13125-13127 -> 13130).
    st = _state(ln=3, rank=1, ka=100000)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptInt: [1], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    types = [type(e).__name__ for e in result.effects]
    assert types.index("ScoreAndRank") == len(types) - 1  # score is last
    assert types.count("StatChangeCapped") == 3


# --------------------------------------------------------------------------- #
# The :1100 key wait at each exit (#160)                                       #
# --------------------------------------------------------------------------- #
_TRAIN_EXITS = [
    # id, state kwargs, rng draws, answers, waits
    # :13100 ifgn$(sp,1)=""thenprint"leider hast du nicht einen gangster!":goto1100
    ("13100-no-gangster", {"roster": ()}, (), (), 1),
    # :13102 gosub1130:ify=0thenreturn
    ("13102-pick-0", {}, (), (0,), 0),
    # :13115 gosub1110:ifx$="n"thenreturn
    ("13115-range-declined", {}, (), (1, False), 0),
    # :13116 ifka(sp)<pgoto1125
    ("13116-range-too-poor", {"ka": 10}, (), (1, True), 1),
    # :13130 gosub1365:x=1:gosub1160:goto1100
    ("13130-range-done", {}, (), (1, True), 1),
    # :13155 gosub1110:ifx$="n"thenreturn
    ("13155-camp-declined", {"rank": 5}, (), (1, 1, False), 0),
    # :13160 ifka(sp)<pgoto1125
    ("13160-camp-too-poor", {"rank": 5, "ka": 10}, (), (1, 1, True), 1),
    # :13175 gosub1365:x=2:gosub1160:print"{down}...da ist er wieder!":goto1100
    ("13175-camp-done", {"rank": 5}, (8, 8, 8), (1, 1, True), 1),
]


@pytest.mark.parametrize(
    ("kwargs", "draws", "answers", "waits"),
    [case[1:] for case in _TRAIN_EXITS],
    ids=[case[0] for case in _TRAIN_EXITS],
)
def test_each_train_exit_waits_for_a_key_where_the_source_does(kwargs, draws, answers, waits):
    src = scripted(*answers)
    run_pure(HANDLERS["waf.train"], src, state=_state(**kwargs), rng=_StubRng(*draws))
    assert src.key_waits() == waits
    if waits:
        assert src.ends_in_key_wait(), "the exit did not end in the :1100 key wait"

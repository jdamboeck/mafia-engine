"""Tests for the waf.train handler — U6.

Proof-first. Ports mf-prg.bas:13100-13175 (schiesstand range + trainingscamp).
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import MoneyChange, ScoreAndRank, StatChangeCapped
from engine.interactions import Ack, Confirm, Ctx, PromptChoice, ShowMessage
from engine.locations import HANDLERS
from engine.state import Clock, Config, Gangster, GameState, Player
from tests.helpers import run_pure

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


class _StubRng:
    def __init__(self, *values):
        self._it = iter(values)
        self.calls = []

    def hit(self, a, b):
        self.calls.append(("hit", a, b))
        return next(self._it)

    def range(self, n):  # pragma: no cover - train uses hit only
        self.calls.append(("range", n))
        return next(self._it)


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
        config=Config(score_mult=score_mult, formula_params=_PARAMS),
    )


def _source(answers):
    iters = {k: iter(v) for k, v in answers.items()}
    seen: list = []

    def source(interaction):
        seen.append(interaction)
        if isinstance(interaction, ShowMessage):
            # #43: narration is DELIVERED, not asked. It consumes no scripted answer,
            # and the driver acks regardless of what we return here.
            return None
        for typ, it in iters.items():
            if isinstance(interaction, typ):
                return next(it)
        raise AssertionError(f"unscripted interaction {interaction!r}")

    source.seen = seen
    source.messages = lambda: [i for i in seen if isinstance(i, ShowMessage)]
    source.message_keys = lambda: [i.key for i in seen if isinstance(i, ShowMessage)]
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
            if isinstance(interaction, ShowMessage):
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
    # rank 4 -> no (s)/(t) prompt: the first PromptChoice is the gangster pick, and after
    # picking, it goes straight to the range cost (no second PromptChoice).
    st = _state(rank=4)
    seen = _observe(
        HANDLERS["waf.train"],
        st,
        _StubRng(),
        {PromptChoice: [0], Confirm: [False]},  # pick gangster 0, decline range
    )
    prompt_choices = [i for i in seen if isinstance(i, PromptChoice)]
    assert len(prompt_choices) == 1  # only the gangster pick, no venue choice


def test_venue_gate_rank_5_offers_choice():
    st = _state(rank=5)
    seen = _observe(
        HANDLERS["waf.train"],
        st,
        _StubRng(),
        {PromptChoice: [0, 0], Confirm: [False]},  # gangster 0, venue=range(0), decline
    )
    prompt_choices = [i for i in seen if isinstance(i, PromptChoice)]
    assert len(prompt_choices) == 2  # gangster pick + venue choice
    assert prompt_choices[1].key == "locations.waf.venue_prompt"


# --------------------------------------------------------------------------- #
# Range gains (R12)                                                           #
# --------------------------------------------------------------------------- #
def test_range_default_ln_gains():
    # ln=3 (default): kr+5, int+3, brut+2. Cost 800+200*1 = 1000. Score x=1.
    st = _state(ln=3, rank=1, ka=100000)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptChoice: [0], Confirm: [True]}),
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
        _source({PromptChoice: [0], Confirm: [True]}),
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
        _source({PromptChoice: [0], Confirm: [True]}),
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
        _source({PromptChoice: [0], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    assert result.state.players[0].roster[0].kraft == 99  # 98+5 clamped


def test_range_declined_no_effects():
    st = _state(ln=3, rank=1, ka=100000)
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptChoice: [0], Confirm: [False]}),
        state=st,
        rng=_StubRng(),
    )
    assert result.effects == []


def test_range_unaffordable_no_effects():
    st = _state(ln=3, rank=1, ka=100)  # need 1000
    result = run_pure(
        HANDLERS["waf.train"],
        _source({PromptChoice: [0], Confirm: [True]}),
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
        _source({PromptChoice: [0, 1], Confirm: [True]}),  # gangster 0, venue camp, confirm
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
        _source({PromptChoice: [0, 1], Confirm: [True]}),
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
        _source({PromptChoice: [0], Confirm: [True]}),
        state=st,
        rng=_StubRng(),
    )
    types = [type(e).__name__ for e in result.effects]
    assert types.index("ScoreAndRank") == len(types) - 1  # score is last
    assert types.count("StatChangeCapped") == 3

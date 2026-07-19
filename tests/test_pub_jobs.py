"""Tests for the pub job-accept handler + the shift flow — U10.

Proof-first: written and observed RED (JobSet/JobClear NotImplementedError from the
U2 groundwork stubs, no ``pub.job`` handler, and no ``job.shift`` handler) before
implementation.

Ports ``mf-prg.bas:12300-12335`` (take-job) + ``:25000-25560`` (the shift flow that
replaces an employed player's turn):

- take-job: INVERTED rank guard (ra(sp)<4 gets the offer, i.e. rank<=3); 1-in-5
  nobody has work; job type 1-4 uniform (bouncer/croupier/doorman/killer); per-type
  duration (fixed literal) + pay (500-wide uniform roll); accept stores the job via
  JobSet and force-ends the turn (ms=0).
- shift: bouncer/doorman share one flow (50% quiet, else a scripted brawler fight);
  croupier picks a trick 1-3, caught with probability 1/(6-trick), immediate bonus on
  success, a fight when caught; killer always fights. Lost fight -> job cleared
  unpaid, score -2. Successful shift decrements months_left; at 0 the full wage pays
  out once with the completion score (6 for croupier per the plan's Open Questions
  resolution, 3 for every other type).
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import EnergyChange, JobClear, JobSet, MoneyChange, MsChange
from engine.locations import HANDLERS
from engine.rng import Rng
from engine.state import Clock, Config, Gangster, GameState, Job, Player
from tests.helpers import run_pure, scripted as _scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PARAMS = {
    "rank_divisor": 11.1,
    "job_bouncer_duration": 3,
    "job_bouncer_pay_min": 2000,
    "job_bouncer_pay_max": 2999,
    "job_croupier_duration": 2,
    "job_croupier_pay_min": 1000,
    "job_croupier_pay_max": 1499,
    "job_doorman_duration": 2,
    "job_doorman_pay_min": 2000,
    "job_doorman_pay_max": 2499,
    "job_killer_duration": 1,
    "job_killer_pay_min": 2000,
    "job_killer_pay_max": 2499,
}


class _StubRng:
    """Scripted RNG: returns queued values, records every call (determinism gate)."""

    def __init__(self, *values):
        self._it = iter(values)
        self.calls = []

    def range(self, n):
        self.calls.append(("range", n))
        return next(self._it)

    def hit(self, a, b):
        self.calls.append(("hit", a, b))
        return next(self._it)


def _state(*, ka=100000, rank=1, ms=5, roster=None, jobs=None):
    active = Player(
        name="p0",
        ka=ka,
        rank=rank,
        ms=ms,
        roster=roster if roster is not None else (Gangster(name="g0", energie=5, kraft=30, brutalitaet=30),),
        jobs=jobs if jobs is not None else Job(),
    )
    return GameState(
        players=(active,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS),
    )




# --------------------------------------------------------------------------- #
# pub.job — rank guard (INVERTED vs. recruit)                                  #
# --------------------------------------------------------------------------- #
def test_rank_4_or_above_denied_no_rng_draw():
    for rank in (4, 5, 10):
        st = _state(rank=rank)
        rng = _StubRng()  # no draws expected -- the guard short-circuits before any roll
        result = run_pure(HANDLERS["pub.job"], _scripted(), state=st, rng=rng)
        assert result.status == "completed"
        assert result.effects == []
        assert rng.calls == []


def test_rank_3_or_below_passes_the_guard():
    for rank in (1, 2, 3):
        st = _state(rank=rank)
        rng = _StubRng(0)  # 1-in-5 nobody-has-work roll: 0 -> "nobody" branch
        result = run_pure(HANDLERS["pub.job"], _scripted(), state=st, rng=rng)
        assert result.status == "completed"
        assert rng.calls == [("range", 5)]


# --------------------------------------------------------------------------- #
# pub.job — 1-in-5 nobody-available branch                                     #
# --------------------------------------------------------------------------- #
def test_nobody_available_on_the_zero_roll():
    st = _state(rank=1)
    rng = _StubRng(0)
    result = run_pure(HANDLERS["pub.job"], _scripted(), state=st, rng=rng)
    assert result.effects == []
    assert result.state.players[0].jobs == Job()


# --------------------------------------------------------------------------- #
# pub.job — seeded type/pay rolls in the documented ranges                    #
# --------------------------------------------------------------------------- #
def test_job_type_uniform_1_to_4_dispatches_correct_duration():
    cases = [
        (0, 1, 3),  # bouncer: jd=3
        (1, 2, 2),  # croupier: jd=2
        (2, 3, 2),  # doorman: jd=2
        (3, 4, 1),  # killer: jd=1
    ]
    for type_roll, expected_type, expected_duration in cases:
        st = _state(rank=1, ka=100000)
        rng = _StubRng(1, type_roll, 2000)  # available; type roll; pay roll
        result = run_pure(HANDLERS["pub.job"], _scripted(True), state=st, rng=rng)
        job_sets = [e for e in result.effects if isinstance(e, JobSet)]
        assert len(job_sets) == 1
        assert job_sets[0].type == expected_type
        assert job_sets[0].months_left == expected_duration


def test_pay_rolled_within_documented_range_per_type():
    cases = [
        (0, 2000, 2999),  # bouncer
        (1, 1000, 1499),  # croupier
        (2, 2000, 2499),  # doorman
        (3, 2000, 2499),  # killer
    ]
    for type_roll, pay_min, pay_max in cases:
        for pay_roll in (pay_min, pay_max):
            st = _state(rank=1, ka=100000)
            rng = _StubRng(1, type_roll, pay_roll)
            result = run_pure(HANDLERS["pub.job"], _scripted(True), state=st, rng=rng)
            job_sets = [e for e in result.effects if isinstance(e, JobSet)]
            assert job_sets[0].pending_pay == pay_roll
            assert rng.calls[-1] == ("hit", pay_min, pay_max)


# --------------------------------------------------------------------------- #
# pub.job — decline / accept + ms=0 force-end                                 #
# --------------------------------------------------------------------------- #
def test_decline_pay_confirm_no_state_change():
    st = _state(rank=1, ka=100000, ms=5)
    rng = _StubRng(1, 0, 2000)  # available; bouncer; pay=2000
    result = run_pure(HANDLERS["pub.job"], _scripted(False), state=st, rng=rng)
    assert result.effects == []
    assert result.state.players[0].jobs == Job()
    assert result.state.players[0].ms == 5


def test_accept_stores_job_and_force_ends_turn_with_ms_zero():
    st = _state(rank=1, ka=100000, ms=7)
    rng = _StubRng(1, 1, 1200)  # available; croupier; pay=1200
    result = run_pure(HANDLERS["pub.job"], _scripted(True), state=st, rng=rng)
    assert result.effects == [
        JobSet(type=2, pending_pay=1200, months_left=2),
        MsChange(amount=-7),
    ]
    assert result.state.players[0].jobs == Job(type=2, pending_pay=1200, months_left=2)
    assert result.state.players[0].ms == 0


# --------------------------------------------------------------------------- #
# job.shift — bouncer/doorman shared flow                                     #
# --------------------------------------------------------------------------- #
def test_bouncer_quiet_day_ticks_down_no_fight():
    st = _state(jobs=Job(type=1, pending_pay=2500, months_left=3))
    rng = _StubRng(0)  # 50% quiet-day roll: 0 -> quiet
    result = run_pure(HANDLERS["job.shift"], _scripted(), state=st, rng=rng)
    assert result.effects == [JobSet(type=1, pending_pay=2500, months_left=2)]
    assert rng.calls == [("range", 2)]


def test_doorman_shares_the_bouncer_flow():
    st = _state(jobs=Job(type=3, pending_pay=2200, months_left=2))
    rng = _StubRng(0)
    result = run_pure(HANDLERS["job.shift"], _scripted(), state=st, rng=rng)
    assert result.effects == [JobSet(type=3, pending_pay=2200, months_left=1)]


def test_bouncer_trouble_picks_one_of_three_brawlers_and_fights():
    # trouble roll -> nonzero; brawler pick 0,1,2; then surrender immediately (loss).
    for brawler_roll in (0, 1, 2):
        st = _state(jobs=Job(type=1, pending_pay=2500, months_left=3))
        rng = _StubRng(1, brawler_roll)
        result = run_pure(HANDLERS["job.shift"], _scripted("surrender"), state=st, rng=rng)
        assert result.status == "completed"
        assert any(isinstance(e, JobClear) for e in result.effects)


# --------------------------------------------------------------------------- #
# job.shift — croupier: trick pick, catch probability 1/(6-trick)             #
# --------------------------------------------------------------------------- #
def test_croupier_success_pays_immediate_bonus_no_fight():
    for trick in (1, 2, 3):
        st = _state(jobs=Job(type=2, pending_pay=1200, months_left=2), ka=1000)
        # catch roll must be NONZERO (uncaught, value 1 always qualifies since
        # catch_bound = 6-trick is always >= 3); bonus roll at the low end of range.
        catch_bound = 6 - trick
        rng = _StubRng(1, 300)
        result = run_pure(HANDLERS["job.shift"], _scripted(trick), state=st, rng=rng)
        # Successful shift also ticks months_left down (2 -> 1), alongside the bonus.
        assert result.effects == [
            MoneyChange(300),
            JobSet(type=2, pending_pay=1200, months_left=1),
        ]
        assert rng.calls == [("range", catch_bound), ("hit", 300, 300 + 100 * trick - 1)]


def test_croupier_caught_probability_1_in_6_minus_trick():
    for trick in (1, 2, 3):
        st = _state(jobs=Job(type=2, pending_pay=1200, months_left=2))
        rng = _StubRng(0)  # caught roll == 0 -> caught, fight starts
        result = run_pure(HANDLERS["job.shift"], _scripted(trick, "surrender"), state=st, rng=rng)
        assert rng.calls[0] == ("range", 6 - trick)
        assert any(isinstance(e, JobClear) for e in result.effects)  # lost the fight


def test_croupier_bonus_uses_documented_range_per_trick():
    # p = int(rnd(1)*100*trick)+300 -> [300, 300+100*trick-1] inclusive.
    for trick, expected_max in [(1, 399), (2, 499), (3, 599)]:
        st = _state(jobs=Job(type=2, pending_pay=1200, months_left=2))
        catch_bound = 6 - trick
        rng = _StubRng(1, expected_max)
        result = run_pure(HANDLERS["job.shift"], _scripted(trick), state=st, rng=rng)
        assert rng.calls == [("range", catch_bound), ("hit", 300, expected_max)]
        bonus_effects = [e for e in result.effects if isinstance(e, MoneyChange)]
        assert bonus_effects == [MoneyChange(expected_max)]


# --------------------------------------------------------------------------- #
# job.shift — killer always fights                                            #
# --------------------------------------------------------------------------- #
def test_killer_always_fights_the_victim():
    st = _state(jobs=Job(type=4, pending_pay=2200, months_left=1))
    rng = _StubRng()
    result = run_pure(HANDLERS["job.shift"], _scripted("surrender"), state=st, rng=rng)
    # A surrender loses immediately -> job cleared unpaid, score -2.
    assert any(isinstance(e, JobClear) for e in result.effects)
    assert result.state.players[0].jobs == Job()


# --------------------------------------------------------------------------- #
# job.shift — failed fight: job cleared, no pay, score -2                     #
# --------------------------------------------------------------------------- #
def test_failed_shift_fight_clears_job_no_pay_score_minus_2():
    st = _state(jobs=Job(type=4, pending_pay=2200, months_left=1), ka=1000)
    rng = _StubRng()
    result = run_pure(HANDLERS["job.shift"], _scripted("surrender"), state=st, rng=rng)
    assert result.state.players[0].ka == 1000  # unpaid
    assert result.state.players[0].jobs == Job()  # cleared
    assert not any(isinstance(e, MoneyChange) for e in result.effects)


# --------------------------------------------------------------------------- #
# job.shift — full lifecycle: croupier, two shifts, lump sum paid, cleared    #
# --------------------------------------------------------------------------- #
def test_croupier_full_lifecycle_two_shifts_then_lump_sum():
    st = _state(jobs=Job(type=2, pending_pay=1200, months_left=2), ka=1000)
    # Shift 1: success, no fight (trick=1, catch roll nonzero).
    rng1 = _StubRng(1, 350)
    result1 = run_pure(HANDLERS["job.shift"], _scripted(1), state=st, rng=rng1)
    assert result1.state.players[0].jobs == Job(type=2, pending_pay=1200, months_left=1)
    assert result1.state.players[0].ka == 1350

    # Shift 2: success again -> months_left hits 0 -> full wage pays out.
    rng2 = _StubRng(1, 350)
    result2 = run_pure(HANDLERS["job.shift"], _scripted(1), state=result1.state, rng=rng2)
    assert result2.state.players[0].jobs == Job()  # cleared
    assert result2.state.players[0].ka == 1350 + 350 + 1200  # bonus + wage
    assert any(isinstance(e, MoneyChange) and e.amount == 1200 for e in result2.effects)


# --------------------------------------------------------------------------- #
# The croupier completion score: 0 (:25560 `x=3+3*(jo=2)`, relational true=-1)  #
# --------------------------------------------------------------------------- #
def test_croupier_completion_score_is_zero():
    # #47 audit: this asserted 6, from the research gloss and the since-reversed
    # true=+1 pin. The C64 evaluation is 3+3*(-1) = 0, which is also the reading
    # that makes design sense — the croupier is the one job that already paid an
    # immediate per-shift bonus (:25125-25126), so no completion award on top.
    from engine.effects import ScoreAndRank

    st = _state(jobs=Job(type=2, pending_pay=1200, months_left=1), ka=1000)
    rng = _StubRng(1, 300)
    result = run_pure(HANDLERS["job.shift"], _scripted(1), state=st, rng=rng)
    score_effects = [e for e in result.effects if isinstance(e, ScoreAndRank)]
    assert score_effects == [ScoreAndRank(amount=0.0, rank_divisor=11.1)]


def test_non_croupier_completion_score_is_three():
    from engine.effects import ScoreAndRank

    # Bouncer job, quiet-day path: reaches shift completion with NO fight at all, so
    # the assertion is fully deterministic (a fight's win/loss would need a real RNG
    # to drive to a specific outcome, which is exercised separately elsewhere).
    st = _state(jobs=Job(type=1, pending_pay=2500, months_left=1))
    rng = _StubRng(0)  # quiet day
    result = run_pure(HANDLERS["job.shift"], _scripted(), state=st, rng=rng)
    score_effects = [e for e in result.effects if isinstance(e, ScoreAndRank)]
    assert score_effects == [ScoreAndRank(amount=3.0, rank_divisor=11.1)]


# --------------------------------------------------------------------------- #
# #44 — post-fight roster energy persists through run_pure's replay check     #
# --------------------------------------------------------------------------- #
def test_post_fight_roster_energy_persists_on_loss():
    """A losing shift fight must leave the roster's energy at its ACTUAL post-fight
    value, not silently revert -- the #44 regression this unit closes. Uses the REAL
    engine RNG (not a stub) so the fight actually resolves through several activations
    before the scripted player surrenders, guaranteeing at least one real exchange."""
    roster = (Gangster(name="g0", energie=5, kraft=10, brutalitaet=10, weapon=0),)
    st = _state(jobs=Job(type=1, pending_pay=2500, months_left=3), roster=roster)
    # Seed 0 is confirmed (by direct trial) to produce a real hit before the scripted
    # surrender -- passing several times lets the AI close in and land a shot.
    rng = Rng(0)
    keys = ["pass"] * 40 + ["surrender"]
    result = run_pure(HANDLERS["job.shift"], _scripted(*keys), state=st, rng=rng)
    assert any(isinstance(e, EnergyChange) for e in result.effects)
    # run_pure already asserts state == replay(snapshot, effects); the roster energy
    # actually changed (not silently reverted) is the additional assertion #44 needs.
    final_energy = result.state.players[0].roster[0].energie
    assert final_energy != roster[0].energie
    energy_effects = [e for e in result.effects if isinstance(e, EnergyChange)]
    assert energy_effects[0].gangster == 0

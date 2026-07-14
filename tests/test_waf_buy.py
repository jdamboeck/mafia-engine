"""Tests for the waf.buy handler — U6, the core protocol stress test.

Proof-first. Ports mf-prg.bas:13010-13091 + the spec-sheet sub-state (13500-13525).

Setup seeds each player with exactly ONE gangster and there is no in-scope recruit
flow, so multi-gangster / armed-gangster fixtures are hand-constructed here (per the
plan's Test fixtures note).
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import AssignWeapon, MoneyChange, ScoreChange
from engine.interactions import (
    CANCEL,
    Confirm,
    LoadSubState,
    PromptChoice,
    PromptInt,
    ShowMessage,
)
from engine.locations import HANDLERS
from engine.state import Clock, Config, Gangster, GameState, Player
from tests.helpers import run_pure

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

# formula_params matching config.yaml (the waf/sph tunables the handler reads).
_PARAMS = {
    "stat_cap": 99,
    "rank_divisor": 11.1,
    "trade_in_divisor": 1.5,
    "grenade_roll": 3,
    "grenade_rank_gate": 5,
}


class _StubRng:
    """rng.range(n) returns scripted values; records the args."""

    def __init__(self, *values):
        self._it = iter(values)
        self.calls = []

    def range(self, n):
        self.calls.append(("range", n))
        return next(self._it)

    def hit(self, a, b):
        self.calls.append(("hit", a, b))
        return next(self._it)


def _state(*, ka=100000, ln=2, rank=1, gf=50.0, score_mult=1.0, roster=None):
    active = Player(
        name="p0",
        ka=ka,
        gf=gf,
        rank=rank,
        roster=roster if roster is not None else [Gangster(name="g0")],
    )
    active.last_location = ln
    return GameState(
        players=[active],
        clock=Clock(active_player=0, player_count=1),
        config=Config(score_mult=score_mult, formula_params=_PARAMS),
    )


def _observe(handler, state, rng, answers):
    """Step `handler` out-of-band and record EVERY yielded interaction, including the
    ShowMessage/LoadSubState the real driver auto-handles without consulting a source.

    Mirrors driver semantics: ShowMessage -> Ack; LoadSubState -> run its registered
    sub-state to completion (acking its own ShowMessages) and send its return value back;
    PromptInt/PromptChoice/Confirm -> the next scripted answer for that type. Returns the
    list of yielded interactions (observation only; use run_pure for effect assertions).
    """
    from engine.interactions import Ack, Cancelled, Ctx
    from engine.substates import SUBSTATES

    iters = {k: iter(v) for k, v in answers.items()}
    ctx = Ctx(state=state, rng=rng)
    gen = handler(ctx)
    seen = []

    def answer(interaction):
        for typ, it in iters.items():
            if isinstance(interaction, typ):
                return next(it)
        raise AssertionError(f"unscripted interaction {interaction!r}")

    interaction = next(gen)
    try:
        while True:
            seen.append(interaction)
            if isinstance(interaction, ShowMessage):
                interaction = gen.send(Ack)
            elif isinstance(interaction, LoadSubState):
                child = SUBSTATES[interaction.kind](ctx, interaction.params)
                next(child)
                cval = None
                try:
                    while True:
                        # child sub-state only yields ShowMessage here (display-only).
                        child.send(Ack)
                except StopIteration as stop:
                    cval = stop.value
                interaction = gen.send(cval)
            else:
                resp = answer(interaction)
                if resp is CANCEL:
                    # Mirror the driver: CANCEL at a cancellable prompt unwinds the handler.
                    gen.throw(Cancelled())
                    break
                interaction = gen.send(resp)
    except (StopIteration, Cancelled):
        pass
    return seen


def _by_type_source(answers):
    """An input_source that returns by interaction type from an answers dict-of-lists.

    ShowMessage/LoadSubState are driven by the real run() (LoadSubState runs its
    registered sub-state), so callers only script PromptInt/PromptChoice/Confirm.
    """
    iters = {k: iter(v) for k, v in answers.items()}

    def source(interaction):
        for typ, it in iters.items():
            if isinstance(interaction, typ):
                return next(it)
        raise AssertionError(f"unscripted interaction {interaction!r}")

    return source


def _by_type(handler, state, rng, answers):
    """Run `handler` through run_pure with a type-dispatched source (purity assertions)."""
    return run_pure(handler, _by_type_source(answers), state=state, rng=rng)


# --------------------------------------------------------------------------- #
# Stock by ln (R4)                                                             #
# --------------------------------------------------------------------------- #
def test_stock_range_by_ln():
    # For each ln, cancel at the weapon prompt but capture its (min,max).
    for ln, (lo, hi) in [(1, (3, 7)), (2, (1, 5)), (3, (1, 4))]:
        st = _state(ln=ln)
        seen = []

        def source(interaction):
            seen.append(interaction)
            return CANCEL  # cancel the weapon PromptInt (whole-buy abort)

        # ln=1 makes a grenade roll first; force it non-zero so stock stays [3,7].
        rng = _StubRng(1) if ln == 1 else _StubRng()
        result = run_pure(HANDLERS["waf.buy"], source, state=st, rng=rng)
        assert result.status == "cancelled"
        prompt = next(i for i in seen if isinstance(i, PromptInt))
        assert (prompt.min, prompt.max) == (lo, hi)


# --------------------------------------------------------------------------- #
# Grenade roll (R5)                                                           #
# --------------------------------------------------------------------------- #
def test_grenade_roll_extends_stock_when_hit_and_rank_gt_5():
    st = _state(ln=1, rank=6)
    seen = []

    def source(interaction):
        seen.append(interaction)
        return CANCEL

    rng = _StubRng(0)  # 1-in-3 hit
    run_pure(HANDLERS["waf.buy"], source, state=st, rng=rng)
    prompt = next(i for i in seen if isinstance(i, PromptInt))
    assert prompt.max == 8  # grenades in stock


def test_grenade_roll_excluded_when_missed():
    st = _state(ln=1, rank=6)
    seen = []

    def source(interaction):
        seen.append(interaction)
        return CANCEL

    rng = _StubRng(1)  # miss
    run_pure(HANDLERS["waf.buy"], source, state=st, rng=rng)
    prompt = next(i for i in seen if isinstance(i, PromptInt))
    assert prompt.max == 7


def test_grenade_roll_rerolls_on_loopback_at_ln1():
    # The BASIC re-enters at 13010 on afford-fail (13025 goto13010), which RE-EXECUTES the
    # grenade roll (13011). So an afford-fail at ln=1 must draw the grenade roll AGAIN
    # (matching the original's stock churn + RNG draw count), not reuse the first stock.
    st = _state(ln=1, rank=6, ka=100)  # can't afford grenades (10000) or much else
    seen = []

    def source(interaction):
        seen.append(interaction)
        if isinstance(interaction, PromptInt):
            # First loop: pick grenades (8) -> unaffordable -> loop. Second loop: cancel.
            return 8 if sum(isinstance(i, PromptInt) for i in seen) == 1 else CANCEL
        return CANCEL

    # Two grenade rolls: first hits (0 -> stock [3,8]), second misses (1 -> [3,7]).
    rng = _StubRng(0, 1)
    result = run_pure(HANDLERS["waf.buy"], source, state=st, rng=rng)
    assert result.status == "cancelled"
    # The grenade roll was drawn TWICE (once per loop entry).
    assert rng.calls == [("range", 3), ("range", 3)]
    prompts = [i for i in seen if isinstance(i, PromptInt)]
    assert prompts[0].max == 8  # first stock included grenades
    assert prompts[1].max == 7  # second stock re-rolled -> grenades gone


def test_grenade_never_offered_below_rank_6():
    st = _state(ln=1, rank=5)  # rank not > 5
    seen = []

    def source(interaction):
        seen.append(interaction)
        return CANCEL

    rng = _StubRng(0)  # even a hit shouldn't extend at rank 5
    run_pure(HANDLERS["waf.buy"], source, state=st, rng=rng)
    prompt = next(i for i in seen if isinstance(i, PromptInt))
    assert prompt.max == 7


# --------------------------------------------------------------------------- #
# Weapon select cancel (R6)                                                    #
# --------------------------------------------------------------------------- #
def test_weapon_zero_cancels_whole_buy():
    st = _state(ln=2)
    result = run_pure(HANDLERS["waf.buy"], lambda i: CANCEL, state=st, rng=_StubRng())
    assert result.status == "cancelled"
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Affordability (R6)                                                           #
# --------------------------------------------------------------------------- #
def test_unaffordable_weapon_shows_not_enough_and_reprompts():
    # ln=2 stock [1,5]; pick revolver (5, price 4000) with only 100 cash -> not enough,
    # then cancel.
    st = _state(ln=2, ka=100)
    seen = _observe(
        HANDLERS["waf.buy"], st, _StubRng(),
        {PromptInt: [5, CANCEL]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "system.not_enough_money" in keys
    # It re-prompted for the weapon (two PromptInt presented).
    assert sum(isinstance(i, PromptInt) for i in seen) == 2

    # Effect-level: nothing committed (cancelled).
    result = run_pure(HANDLERS["waf.buy"], lambda i: CANCEL, state=_state(ln=2, ka=100), rng=_StubRng())
    assert result.status == "cancelled"


# --------------------------------------------------------------------------- #
# Spec sheet (R7 / R14)                                                        #
# --------------------------------------------------------------------------- #
def test_spec_sheet_shown_then_continues_to_gangster_pick():
    st = _state(ln=2, ka=100000)
    # pick messer (1), then cancel the gangster pick.
    seen = _observe(
        HANDLERS["waf.buy"], st, _StubRng(),
        {PromptInt: [1], PromptChoice: [CANCEL]},
    )
    # The weapon-spec LoadSubState was yielded, then the parent continued to a gangster
    # PromptChoice (proving the sub-state threaded back into the parent).
    subs = [i for i in seen if isinstance(i, LoadSubState)]
    assert len(subs) == 1 and subs[0].kind == "weapon_spec"
    assert subs[0].params["index"] == 1  # messer
    assert any(isinstance(i, PromptChoice) for i in seen)

    # Effect-level: gangster-pick cancel discards the whole buy.
    result = run_pure(
        HANDLERS["waf.buy"],
        _by_type_source({PromptInt: [1], PromptChoice: [CANCEL]}),
        state=_state(ln=2, ka=100000),
        rng=_StubRng(),
    )
    assert result.status == "cancelled"


# --------------------------------------------------------------------------- #
# Stat gates (R8)                                                              #
# --------------------------------------------------------------------------- #
def test_stat_gate_intelligence_blocks_then_passes():
    # weapon 6 (gewehr) requires int>=40. Gangster int 39 fails, then a second gangster
    # int 40 passes (buys). Two-gangster hand-built fixture.
    roster = [
        Gangster(name="dumb", intelligenz=39),
        Gangster(name="smart", intelligenz=40),
    ]
    st = _state(ln=1, rank=6, roster=roster, ka=100000)
    # ln=1 grenade roll forced miss; weapon 6; gangster 0 (fails), gangster 1 (passes);
    # both unarmed so no trade-in confirm.
    seen = _observe(
        HANDLERS["waf.buy"], st, _StubRng(1),  # grenade miss
        {PromptInt: [6], PromptChoice: [0, 1]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "locations.waf.too_dumb" in keys

    # Effect-level: smart gangster (index 1) got the weapon.
    result = run_pure(
        HANDLERS["waf.buy"],
        _by_type_source({PromptInt: [6], PromptChoice: [0, 1]}),
        state=_state(ln=1, rank=6, ka=100000, roster=[
            Gangster(name="dumb", intelligenz=39),
            Gangster(name="smart", intelligenz=40),
        ]),
        rng=_StubRng(1),
    )
    assert result.status == "completed"
    assert AssignWeapon(weapon=6, gangster=1) in result.effects


def test_stat_gate_kraft_and_brutality():
    # weapon 3 (schlagkette) requires kraft>=20 AND brut>=40.
    roster = [Gangster(name="g", kraft=19, brutalitaet=40)]
    st = _state(ln=2, roster=roster, ka=100000)
    # weapon 3; gangster 0 fails kraft (19 < 20), then cancel the re-shown gangster pick.
    seen = _observe(
        HANDLERS["waf.buy"], st, _StubRng(),
        {PromptInt: [3], PromptChoice: [0, CANCEL]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "locations.waf.too_weak" in keys


# --------------------------------------------------------------------------- #
# Trade-in cash / assign (R9)                                                  #
# --------------------------------------------------------------------------- #
def test_first_weapon_unarmed_settles_cash_and_assigns():
    # Unarmed gangster buys messer (1, price 50): q=0, cash -= 50, assign weapon 1.
    st = _state(ln=2, ka=1000)
    answers = {PromptInt: iter([1]), PromptChoice: iter([0])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "completed"
    assert MoneyChange(-50) in result.effects
    assert AssignWeapon(weapon=1, gangster=0) in result.effects
    assert result.state.players[0].ka == 950
    assert result.state.players[0].roster[0].weapon == 1


def test_trade_in_offer_uses_old_weapon_price_and_settles():
    # Armed gangster (old weapon 4 = wurfsterne, price 3000). Buy revolver (5, price 4000).
    # q = int(3000/1.5) = 2000. Accept -> cash += 2000 - 4000 = -2000. Assign 5.
    roster = [Gangster(name="g", weapon=4, intelligenz=99, kraft=99, brutalitaet=99)]
    st = _state(ln=2, ka=10000, roster=roster)
    answers = {PromptInt: iter([5]), PromptChoice: iter([0]), Confirm: iter([True])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "completed"
    assert MoneyChange(2000 - 4000) in result.effects
    assert AssignWeapon(weapon=5, gangster=0) in result.effects
    assert result.state.players[0].ka == 10000 - 2000  # +2000 -4000


def test_trade_in_decline_returns_to_weapon_list():
    roster = [Gangster(name="g", weapon=4, intelligenz=99, kraft=99, brutalitaet=99)]
    st = _state(ln=2, ka=10000, roster=roster)
    # buy 5, pick gangster 0, DECLINE trade-in -> back to weapon list, then cancel.
    answers = {
        PromptInt: iter([5, CANCEL]),
        PromptChoice: iter([0]),
        Confirm: iter([False]),
    }
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "cancelled"  # ultimately cancelled at the re-shown weapon list
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Trade-in score signs (R9, KTD-9 true=+1)                                    #
# --------------------------------------------------------------------------- #
def test_score_first_weapon_down_by_x8():
    # Unarmed, gf<100 -> score DOWN by x8 (score_mult=1.0 -> -1).
    st = _state(ln=2, ka=1000, gf=50.0, score_mult=1.0)
    answers = {PromptInt: iter([1]), PromptChoice: iter([0])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert ScoreChange(-1.0) in result.effects


def test_score_new_index_higher_than_old_is_down():
    # old weapon 1 (messer), buy revolver 5 (x=5 > old=1) -> DOWN by x8 (gf<100).
    roster = [Gangster(name="g", weapon=1, intelligenz=99, kraft=99, brutalitaet=99)]
    st = _state(ln=2, ka=10000, gf=50.0, score_mult=1.0, roster=roster)
    answers = {PromptInt: iter([5]), PromptChoice: iter([0]), Confirm: iter([True])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert ScoreChange(-1.0) in result.effects


def test_score_new_index_not_higher_is_up_by_2x8():
    # old weapon 5 (revolver), buy messer 1 (x=1 <= old=5) -> UP by 2*x8 (gf>0).
    roster = [Gangster(name="g", weapon=5, intelligenz=99, kraft=99, brutalitaet=99)]
    st = _state(ln=2, ka=10000, gf=50.0, score_mult=1.0, roster=roster)
    answers = {PromptInt: iter([1]), PromptChoice: iter([0]), Confirm: iter([True])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert ScoreChange(2.0) in result.effects


def test_score_gate_false_no_change():
    # gf=100 -> (gf<100) false for first-weapon -> NO score change.
    st = _state(ln=2, ka=1000, gf=100.0, score_mult=1.0)
    answers = {PromptInt: iter([1]), PromptChoice: iter([0])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert not any(isinstance(e, ScoreChange) for e in result.effects)


# --------------------------------------------------------------------------- #
# Cancel atomicity                                                             #
# --------------------------------------------------------------------------- #
def test_empty_roster_loops_back_to_weapon_list_no_empty_picker():
    # The original picker (1130) returns y=0 on an empty roster, looping back to the weapon
    # list (13035 goto13010) — it never presents an empty gangster picker. So buying with an
    # empty roster must re-list weapons (not hang on an unanswerable PromptChoice), then a
    # weapon cancel ends the buy with no effects and no gangster PromptChoice ever shown.
    st = _state(ln=2, ka=1000, roster=[])
    seen = _observe(
        HANDLERS["waf.buy"], st, _StubRng(),
        {PromptInt: [1, CANCEL]},  # pick messer -> loops back (empty roster) -> cancel
    )
    assert not any(isinstance(i, PromptChoice) for i in seen)  # no empty picker presented
    assert sum(isinstance(i, PromptInt) for i in seen) == 2  # re-listed the weapons

    result = run_pure(
        HANDLERS["waf.buy"],
        _by_type_source({PromptInt: [1, CANCEL]}),
        state=_state(ln=2, ka=1000, roster=[]),
        rng=_StubRng(),
    )
    assert result.status == "cancelled"
    assert result.effects == []


def test_cancel_at_gangster_pick_commits_nothing():
    st = _state(ln=2, ka=1000)
    answers = {PromptInt: iter([1]), PromptChoice: iter([CANCEL])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "cancelled"
    assert result.effects == []
    assert result.state.players[0].roster[0].weapon == 0  # unchanged

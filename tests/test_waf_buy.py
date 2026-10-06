"""Tests for the waf.buy handler — U6, the core protocol stress test.

Proof-first. Ports mf-prg.bas:13010-13091 + the spec-sheet sub-state (13500-13525).

Setup seeds each player with exactly ONE gangster and there is no in-scope recruit
flow, so multi-gangster / armed-gangster fixtures are hand-constructed here (per the
plan's Test fixtures note).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config_loader import load_game_config
from engine.effects import AssignWeapon, MoneyChange, ScoreChange
from engine.interactions import (
    Acknowledge,
    CANCEL,
    Confirm,
    LoadSubState,
    PromptInt,
    ShowMessage,
)
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster
from engine.turns import KEY_WAIT_SCREEN
from tests.helpers import StubRng as _StubRng, run_pure, scripted

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


def _state(*, ka=100000, ln=2, rank=1, gf=50.0, score_mult=1.0, roster=None):
    active = Player(
        name="p0",
        ka=ka,
        gf=gf,
        rank=rank,
        roster=roster if roster is not None else (Gangster(name="g0"),),
        last_location=ln,
    )
    return GameState(
        players=(active,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params={**_PARAMS, "score_mult": score_mult}),
    )


#: The shared gangster picker's prompt (``setup.pick_gangster``, :1145 ``nummer:``):
#: answered 1-based, 0 for none. Scripts key it by this prompt key, not by its type,
#: since the weapon list is a ``PromptInt`` too.
PICK = "turn.picker.prompt"


def _is_pick(interaction) -> bool:
    return isinstance(interaction, PromptInt) and interaction.key == PICK


def _scripted_answer(iters, interaction):
    """The next scripted answer: by the prompt's key when scripted, else by its type."""
    key = getattr(interaction, "key", None)
    if key in iters:
        return next(iters[key])
    for typ, it in iters.items():
        if isinstance(typ, type) and isinstance(interaction, typ):
            return next(it)
    raise AssertionError(f"unscripted interaction {interaction!r}")


def _observe(handler, state, rng, answers):
    """Step `handler` out-of-band and record EVERY yielded interaction, including the
    ShowMessage/LoadSubState the real driver auto-handles without consulting a source.

    Mirrors driver semantics: ShowMessage -> Ack; LoadSubState -> run its registered
    sub-state to completion (acking its own ShowMessages) and send its return value back;
    PromptInt/PromptChoice/Confirm -> the next scripted answer for its key or type. Returns the
    list of yielded interactions (observation only; use run_pure for effect assertions).
    """
    from engine.interactions import Ack, Cancelled, Ctx
    from engine.substates import SUBSTATES

    iters = {k: iter(v) for k, v in answers.items()}
    ctx = Ctx(state=state, rng=rng)
    gen = handler(ctx)
    seen = []

    def answer(interaction):
        return _scripted_answer(iters, interaction)

    interaction = next(gen)
    try:
        while True:
            seen.append(interaction)
            if isinstance(interaction, (ShowMessage, Acknowledge)):
                # Narration and the :1100 key wait: delivered, not asked.
                interaction = gen.send(Ack)
            elif isinstance(interaction, LoadSubState):
                child = SUBSTATES[interaction.kind](ctx, interaction.params)
                next(child)
                cval = None
                try:
                    while True:
                        # the child sub-state only yields display-only interactions here
                        # (its ShowMessages and its :13525 key wait).
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
        if isinstance(interaction, (ShowMessage, Acknowledge)):
            # #43: narration is DELIVERED, not asked. It consumes no scripted answer,
            # and the driver acks regardless of what we return here. So does the
            # :1100 key wait (``Acknowledge(KEY_WAIT_SCREEN)``).
            return None
        return _scripted_answer(iters, interaction)

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


def _list_screen(state, rng) -> list[str]:
    """The rows ``waf.buy`` shows before its first ``ihre wahl:`` prompt, resolved."""
    from engine.strings import Resolver

    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    seen = _observe(HANDLERS["waf.buy"], state, rng, {PromptInt: [CANCEL]})
    shown = seen[: next(i for i, x in enumerate(seen) if isinstance(x, PromptInt))]
    assert all(isinstance(x, ShowMessage) for x in shown), shown
    return "\n".join(resolver.resolve(x.key, x.params) for x in shown).split("\n")


def test_the_weapon_list_shows_each_offered_weapon_with_its_price():
    """:13010 ``print"{clr}{down}ok, wir haben folgendes:":print``, then :13015
    ``fori=atob:printmid$(str$(i),2)" - "wa$(i);wp(i)"$":next`` -- the number without
    its sign position, the name, the price as PRINT prints a number (" 50 ")."""
    assert _list_screen(_state(ln=2), _StubRng()) == [
        "ok, wir haben folgendes:",
        "",
        "1 - messer 50 $",
        "2 - knueppel 100 $",
        "3 - schlagkette 500 $",
        "4 - wurfsterne 3000 $",
        "5 - revolver 4000 $",
    ]
    assert _list_screen(_state(ln=3), _StubRng())[-1] == "4 - wurfsterne 3000 $"
    # :13011 a=3:b=7 on tile 1; the grenade news (:13090) prints after :13010's
    # heading and before the list, which then runs to b=8.
    rows = _list_screen(_state(ln=1, rank=6), _StubRng(0))
    assert rows[:2] == ["ok, wir haben folgendes:", ""]
    assert rows[2].startswith("...brandheiss!")
    assert rows[-6:] == [
        "3 - schlagkette 500 $",
        "4 - wurfsterne 3000 $",
        "5 - revolver 4000 $",
        "6 - gewehr 4500 $",
        "7 - maschinenpistole 8000 $",
        "8 - handgranaten 10000 $",
    ]


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
        HANDLERS["waf.buy"],
        st,
        _StubRng(),
        {PromptInt: [5, CANCEL]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "system.not_enough_money" in keys
    # It re-prompted for the weapon (two PromptInt presented).
    assert sum(isinstance(i, PromptInt) for i in seen) == 2

    # Effect-level: nothing committed (cancelled).
    result = run_pure(
        HANDLERS["waf.buy"], lambda i: CANCEL, state=_state(ln=2, ka=100), rng=_StubRng()
    )
    assert result.status == "cancelled"


# --------------------------------------------------------------------------- #
# Spec sheet (R7 / R14)                                                        #
# --------------------------------------------------------------------------- #
def test_spec_sheet_shown_then_continues_to_gangster_pick():
    st = _state(ln=2, ka=100000)
    # pick messer (1), then cancel the gangster pick.
    seen = _observe(
        HANDLERS["waf.buy"],
        st,
        _StubRng(),
        {PromptInt: [1], PICK: [CANCEL]},
    )
    # The weapon-spec LoadSubState was yielded, then the parent continued to the gangster
    # picker (proving the sub-state threaded back into the parent).
    subs = [i for i in seen if isinstance(i, LoadSubState)]
    assert len(subs) == 1 and subs[0].kind == "weapon_spec"
    assert subs[0].params["index"] == 1  # messer
    assert any(_is_pick(i) for i in seen)

    # Effect-level: gangster-pick cancel discards the whole buy.
    result = run_pure(
        HANDLERS["waf.buy"],
        _by_type_source({PromptInt: [1], PICK: [CANCEL]}),
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
    roster = (
        Gangster(name="dumb", intelligenz=39),
        Gangster(name="smart", intelligenz=40),
    )
    st = _state(ln=1, rank=6, roster=roster, ka=100000)
    # ln=1 grenade roll forced miss; weapon 6; gangster 0 (fails), gangster 1 (passes);
    # both unarmed so no trade-in confirm.
    seen = _observe(
        HANDLERS["waf.buy"],
        st,
        _StubRng(1),  # grenade miss
        {PromptInt: [6], PICK: [1, 2]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "locations.waf.too_dumb" in keys

    # Effect-level: smart gangster (index 1) got the weapon.
    result = run_pure(
        HANDLERS["waf.buy"],
        _by_type_source({PromptInt: [6], PICK: [1, 2]}),
        state=_state(
            ln=1,
            rank=6,
            ka=100000,
            roster=(
                Gangster(name="dumb", intelligenz=39),
                Gangster(name="smart", intelligenz=40),
            ),
        ),
        rng=_StubRng(1),
    )
    assert result.status == "completed"
    assert AssignWeapon(weapon=6, gangster=1) in result.effects


def test_stat_gate_kraft_and_brutality():
    # weapon 3 (schlagkette) requires kraft>=20 AND brut>=40.
    roster = (Gangster(name="g", kraft=19, brutalitaet=40),)
    st = _state(ln=2, roster=roster, ka=100000)
    # weapon 3; gangster 0 fails kraft (19 < 20), then cancel the re-shown gangster pick.
    seen = _observe(
        HANDLERS["waf.buy"],
        st,
        _StubRng(),
        {PromptInt: [3], PICK: [1, CANCEL]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "locations.waf.too_weak" in keys


def test_stat_gates_run_in_source_order_when_several_fall_short():
    # schlagkette (3) requires kraft>=20 and brut>=40; this gangster fails BOTH. The
    # source tests kraft at 13055 before brutality at 13060 and jumps back on the first
    # failure, so the refusal is "zu wenig kraft", never "nicht brutal genug" — the
    # order is the handler's, not the order of the weapon's `requires` map.
    roster = (Gangster(name="g", kraft=19, brutalitaet=39),)
    st = _state(ln=2, roster=roster, ka=100000)
    seen = _observe(
        HANDLERS["waf.buy"],
        st,
        _StubRng(),
        {PromptInt: [3], PICK: [1, CANCEL]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "locations.waf.too_weak" in keys
    assert "locations.waf.not_brutal" not in keys


def test_stat_gate_works_on_a_reloaded_bare_combatant():
    # A loaded save rebuilds roster members as the engine's bare Combatant (layer rule),
    # which has no named stat properties. The gate must read stats load-safely (attrs).
    from engine import persistence

    roster = (Gangster(name="g", kraft=19, brutalitaet=40),)
    st = persistence.state_from_dict(persistence._state_to_dict(_state(ln=2, roster=roster)))
    assert type(st.players[0].roster[0]).__name__ == "Combatant"
    seen = _observe(
        HANDLERS["waf.buy"],
        st,
        _StubRng(),
        {PromptInt: [3], PICK: [1, CANCEL]},
    )
    keys = [getattr(i, "key", None) for i in seen if isinstance(i, ShowMessage)]
    assert "locations.waf.too_weak" in keys


# --------------------------------------------------------------------------- #
# Trade-in cash / assign (R9)                                                  #
# --------------------------------------------------------------------------- #
def test_first_weapon_unarmed_settles_cash_and_assigns():
    # Unarmed gangster buys messer (1, price 50): q=0, cash -= 50, assign weapon 1.
    st = _state(ln=2, ka=1000)
    answers = {PromptInt: iter([1]), PICK: iter([1])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "completed"
    assert MoneyChange(-50) in result.effects
    assert AssignWeapon(weapon=1, gangster=0) in result.effects
    assert result.state.players[0].ka == 950
    assert result.state.players[0].roster[0].weapon == 1


def test_trade_in_offer_uses_old_weapon_price_and_settles():
    # Armed gangster (old weapon 4 = wurfsterne, price 3000). Buy revolver (5, price 4000).
    # q = int(3000/1.5) = 2000. Accept -> cash += 2000 - 4000 = -2000. Assign 5.
    roster = (Gangster(name="g", weapon=4, intelligenz=99, kraft=99, brutalitaet=99),)
    st = _state(ln=2, ka=10000, roster=roster)
    answers = {PromptInt: iter([5]), PICK: iter([1]), Confirm: iter([True])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "completed"
    assert MoneyChange(2000 - 4000) in result.effects
    assert AssignWeapon(weapon=5, gangster=0) in result.effects
    assert result.state.players[0].ka == 10000 - 2000  # +2000 -4000


def test_trade_in_decline_returns_to_weapon_list():
    roster = (Gangster(name="g", weapon=4, intelligenz=99, kraft=99, brutalitaet=99),)
    st = _state(ln=2, ka=10000, roster=roster)
    # buy 5, pick gangster 0, DECLINE trade-in -> back to weapon list, then cancel.
    answers = {
        PromptInt: iter([5, CANCEL]),
        PICK: iter([1]),
        Confirm: iter([False]),
    }
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "cancelled"  # ultimately cancelled at the re-shown weapon list
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Trade-in score signs (R9) — C64 true=-1, corrected by the #47 fidelity audit  #
#                                                                              #
# These three tests previously asserted the exact opposite signs, encoding the  #
# since-reversed true=+1 pin: they claimed that arming a gangster and buying a  #
# BETTER weapon LOWERED the gang's notoriety, while DOWNGRADING raised it.      #
# Weapon indices ascend in power and price (DATA 50100-50115: 0 `haende` 0$ ..  #
# 7 `handgranaten` 10000$), and `gf` is notoriety — positive for successes      #
# (x=2 won fights/heists), negative for failures (x=-2/-5/-10). The corrected   #
# signs below are the C64 evaluation and the only ones consistent with that.    #
# --------------------------------------------------------------------------- #
def test_score_first_weapon_up_by_x8():
    # :13065 `gf(sp)=gf(sp)-x8*1*(gf(sp)<100)`; (gf<100) is true = -1 -> score UP by x8.
    # Arming a previously unarmed gangster raises notoriety.
    st = _state(ln=2, ka=1000, gf=50.0, score_mult=1.0)
    answers = {PromptInt: iter([1]), PICK: iter([1])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert ScoreChange(1.0, floor=None, cap=None) in result.effects


def test_score_upgrade_new_index_higher_than_old_is_up():
    # :13072 — old weapon 1 (messer), buy revolver 5. x=5 > old=1 is an UPGRADE
    # (higher index = better weapon), so `gf - x8*(gf<100)` -> UP by x8.
    roster = (Gangster(name="g", weapon=1, intelligenz=99, kraft=99, brutalitaet=99),)
    st = _state(ln=2, ka=10000, gf=50.0, score_mult=1.0, roster=roster)
    answers = {PromptInt: iter([5]), PICK: iter([1]), Confirm: iter([True])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert ScoreChange(1.0, floor=None, cap=None) in result.effects


def test_score_downgrade_new_index_not_higher_is_down_by_2x8():
    # :13073 — old weapon 5 (revolver), buy messer 1. x=1 <= old=5 is a DOWNGRADE,
    # so `gf + x8*2*(gf>0)` with (gf>0) true = -1 -> DOWN by 2*x8.
    roster = (Gangster(name="g", weapon=5, intelligenz=99, kraft=99, brutalitaet=99),)
    st = _state(ln=2, ka=10000, gf=50.0, score_mult=1.0, roster=roster)
    answers = {PromptInt: iter([1]), PICK: iter([1]), Confirm: iter([True])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert ScoreChange(-2.0, floor=None, cap=None) in result.effects


def test_score_gate_false_no_change():
    # gf=100 -> (gf<100) false for first-weapon -> NO score change.
    st = _state(ln=2, ka=1000, gf=100.0, score_mult=1.0)
    answers = {PromptInt: iter([1]), PICK: iter([1])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert not any(isinstance(e, ScoreChange) for e in result.effects)


# --------------------------------------------------------------------------- #
# Cancel atomicity                                                             #
# --------------------------------------------------------------------------- #
def test_empty_roster_loops_back_to_weapon_list_no_empty_picker():
    # The original picker (1130) returns y=0 on an empty roster, looping back to the weapon
    # list (13035 goto13010) — it never presents an empty gangster picker. So buying with an
    # empty roster must re-list weapons (not hang on an unanswerable prompt), then a
    # weapon cancel ends the buy with no effects and no gangster prompt ever shown.
    st = _state(ln=2, ka=1000, roster=())
    seen = _observe(
        HANDLERS["waf.buy"],
        st,
        _StubRng(),
        {PromptInt: [1, CANCEL]},  # pick messer -> loops back (empty roster) -> cancel
    )
    assert not any(_is_pick(i) for i in seen)  # no empty picker presented
    assert sum(isinstance(i, PromptInt) for i in seen) == 2  # re-listed the weapons

    result = run_pure(
        HANDLERS["waf.buy"],
        _by_type_source({PromptInt: [1, CANCEL]}),
        state=_state(ln=2, ka=1000, roster=()),
        rng=_StubRng(),
    )
    assert result.status == "cancelled"
    assert result.effects == []


def test_a_gangster_pick_of_0_goes_back_to_the_weapon_list():
    # :13035 ``gosub1130:ify=0goto13010``: 0 at the shared picker (:1150) re-lists the
    # weapons; the spec sheet and the question come again with the next pick (13035).
    seen = _observe(
        HANDLERS["waf.buy"],
        _state(ln=2, ka=1000),
        _StubRng(),
        {PromptInt: [1, 1], PICK: [0, 1]},
    )
    weapon_prompts = [i for i in seen if isinstance(i, PromptInt) and not _is_pick(i)]
    assert len(weapon_prompts) == 2
    assert sum(isinstance(i, LoadSubState) for i in seen) == 2

    result = run_pure(
        HANDLERS["waf.buy"],
        _by_type_source({PromptInt: [1, 1], PICK: [0, 1]}),
        state=_state(ln=2, ka=1000),
        rng=_StubRng(),
    )
    assert result.status == "completed"
    assert result.effects == [
        ScoreChange(1.0, floor=None, cap=None),
        MoneyChange(-50),
        AssignWeapon(weapon=1, gangster=0),
    ]


def test_a_failed_stat_gate_shows_the_spec_sheet_and_the_question_again():
    # :13055 ``gosub1100:goto13035`` re-runs 13035 whole: spec sheet, question, picker.
    roster = (Gangster(name="g", kraft=19, brutalitaet=40),)
    seen = _observe(
        HANDLERS["waf.buy"],
        _state(ln=2, roster=roster, ka=100000),
        _StubRng(),
        {PromptInt: [3], PICK: [1, CANCEL]},
    )
    assert sum(isinstance(i, LoadSubState) for i in seen) == 2
    keys = [i.key for i in seen if isinstance(i, ShowMessage)]
    assert keys.count("locations.waf.gangster_prompt") == 2


def test_cancel_at_gangster_pick_commits_nothing():
    st = _state(ln=2, ka=1000)
    answers = {PromptInt: iter([1]), PICK: iter([CANCEL])}
    result = _by_type(HANDLERS["waf.buy"], st, _StubRng(), answers)
    assert result.status == "cancelled"
    assert result.effects == []
    assert result.state.players[0].roster[0].weapon == 0  # unchanged


# --------------------------------------------------------------------------- #
# Spec-sheet labels (#146 item 4)                                              #
# --------------------------------------------------------------------------- #
#: Each weapon's sheet as the source prints it: :13515
#: ``printtab(8)"{down}treffgenauigkeit: "ts$(int(ts(x)/2))``, :13520
#: ``printtab(8)"{down}wirkung: "tg$(int(tg(x)/4)+1)``, with the labels :125
#: ``fori=1to3:readts$(i):next:fori=1to5:readtg$(i):next`` reads from :50500
#: ``"mies","ganz gut","todsicher","laecherlich","maessig","schlimm!"`` and :50505
#: ``"brutal","erschreckend!"``. Worked by hand from the (ts, tg) pairs of :50100-50115
#: (no weapon has ts < 2, so none reaches the never-assigned ts$(0)).
_SPEC_LABELS = {
    "haende": ("mies", "laecherlich"),  # ts 2 -> 1, tg 2 -> 1
    "messer": ("mies", "maessig"),  # ts 3 -> 1, tg 5 -> 2
    "knueppel": ("ganz gut", "laecherlich"),  # ts 4 -> 2, tg 3 -> 1
    "schlagkette": ("ganz gut", "maessig"),  # ts 4 -> 2, tg 4 -> 2
    "wurfsterne": ("mies", "maessig"),  # ts 2 -> 1, tg 7 -> 2
    "revolver": ("ganz gut", "schlimm!"),  # ts 5 -> 2, tg 10 -> 3
    "gewehr": ("ganz gut", "brutal"),  # ts 5 -> 2, tg 12 -> 4
    "maschinenpistole": ("todsicher", "brutal"),  # ts 6 -> 3, tg 15 -> 4
    "handgranaten": ("todsicher", "erschreckend!"),  # ts 7 -> 3, tg 18 -> 5
}


def _spec_screen(weapon) -> list[str]:
    """Run the spec-sheet sub-state for ``weapon`` and return its resolved screen rows."""
    from engine.interactions import Ack, Ctx
    from engine.strings import Resolver
    from engine.substates import SUBSTATES

    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    child = SUBSTATES["weapon_spec"](Ctx(state=_state(), rng=_StubRng()), {"weapon": weapon})
    text: list[str] = []
    interaction = next(child)
    try:
        while True:
            if isinstance(interaction, Acknowledge) and interaction.key == KEY_WAIT_SCREEN:
                # :13525's key wait: no row of the sheet.
                interaction = child.send(Ack)
                continue
            assert isinstance(interaction, ShowMessage), interaction
            text.append(resolver.resolve(interaction.key, interaction.params))
            interaction = child.send(Ack)
    except StopIteration:
        pass
    return "\n".join(text).split("\n")


def test_every_weapon_s_spec_sheet_prints_the_source_labels():
    import yaml

    table = yaml.safe_load((_CONFIG_DIR / "entities" / "weapons.yaml").read_text())["weapons"]
    assert [w["name"] for w in table] == list(_SPEC_LABELS)
    for weapon in table:
        accuracy, effect = _SPEC_LABELS[weapon["name"]]
        rows = _spec_screen(weapon)
        # :13500 ``print"{clr}{down}{rght}{rvon}{blk} waffe: "wa$(x)" {gry3}"``, :13510
        # ``"{home}{down}{down}{down}"tab(8)"{blk}preis:"wp(x)"$"``, then each label line
        # one row further down (its ``{down}``) at column 8.
        assert rows == [
            f"waffe: {weapon['name']}",
            "",
            f"        preis: {weapon['price']} $",
            "",
            f"        treffgenauigkeit: {accuracy}",
            "",
            f"        wirkung: {effect}",
        ], weapon["name"]


def test_an_accuracy_below_two_prints_the_empty_ts_label():
    """``int(ts/2)`` = 0 indexes ``ts$(0)``, which :125 never assigns: the C64 prints
    nothing after the colon."""
    rows = _spec_screen({"name": "x", "price": 1, "ts": 1, "tg": 2})
    assert rows[4] == "        treffgenauigkeit: "


# --------------------------------------------------------------------------- #
# The :1100 key wait at each exit (#160)                                       #
# --------------------------------------------------------------------------- #
def test_the_spec_sheet_ends_in_its_own_key_wait():
    """:13525 ``print"{down}{down} taste druecken!":poke198,0:wait198,1:...:return``."""
    from engine.interactions import Ack, Ctx
    from engine.substates import SUBSTATES

    weapon = {"name": "messer", "price": 50, "ts": 3, "tg": 5}
    child = SUBSTATES["weapon_spec"](Ctx(state=_state(), rng=_StubRng()), {"weapon": weapon})
    seen = [next(child)]
    try:
        while True:
            seen.append(child.send(Ack))
    except StopIteration:
        pass
    assert [type(i).__name__ for i in seen] == ["ShowMessage"] * 3 + ["Acknowledge"]
    assert seen[-1] == Acknowledge(KEY_WAIT_SCREEN)


_DUMB = Gangster(name="g0", kraft=10, intelligenz=50, brutalitaet=50)
_STRONG = Gangster(name="g0", kraft=30, intelligenz=50, brutalitaet=50)
_ARMED = Gangster(name="g0", kraft=30, intelligenz=50, brutalitaet=50, weapon=1)

_BUY_EXITS = [
    # id, state kwargs, answers, waits, ends in the wait
    # :13020 input"{down}ihre wahl:";x:ifx=0thenreturn
    ("13020-nothing", {}, (CANCEL,), 0, False),
    # :13025 ifka(sp)<wp(x)thengosub1125:goto13010 -- then nothing
    ("13025-too-poor", {"ka": 10}, (5, CANCEL), 1, False),
    # :13035 gosub13500 (:13525's wait) ...:gosub1130:ify=0goto13010 -- then nothing
    ("13035-pick-0", {}, (1, 0, CANCEL), 1, False),
    # :13035's sheet, :13055 ...:gosub1100:goto13035, the sheet again, 0, nothing
    ("13055-too-weak", {"roster": (_DUMB,)}, (2, 1, 0, CANCEL), 3, False),
    # :13035's sheet, :13071 ...gosub1110:ifx$="n"goto13010 -- then nothing
    ("13071-declined", {"roster": (_ARMED,)}, (2, 1, False, CANCEL), 1, False),
    # :13035's sheet, :13080 print"{down}du hast nun die neue waffe!":goto1100
    ("13080-bought", {"roster": (_STRONG,)}, (2, 1), 2, True),
]


@pytest.mark.parametrize(
    ("kwargs", "answers", "waits", "ends"),
    [case[1:] for case in _BUY_EXITS],
    ids=[case[0] for case in _BUY_EXITS],
)
def test_each_buy_exit_waits_for_a_key_where_the_source_does(kwargs, answers, waits, ends):
    src = scripted(*answers)
    run_pure(HANDLERS["waf.buy"], src, state=_state(**kwargs), rng=_StubRng())
    assert src.key_waits() == waits
    assert src.ends_in_key_wait() == ends

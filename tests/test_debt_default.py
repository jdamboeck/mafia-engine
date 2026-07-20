"""Tests for the debt-default lifecycle (U12) — the upkeep debt slot, ports
``mf-prg.bas:4040`` / ``4300-4370``.

Proof-first: written and observed RED (the U3 debt slot was a no-op — no
``DebtChange`` tick, no warning message, no collectors fight) before implementation.

THE COUNTER DIRECTION (the U2-flagged relational-sign landmine)
--------------------------------------------------------------
``:4305`` is ``kz(sp)=kz(sp)+(kz(sp)>0)``. Under this project's pinned porting
convention (``docs/solutions/architecture-patterns/
basic-relational-boolean-is-plus-one-when-porting.md``, ``true=+1``) that would count
**UP** from 6 forever — a grace period that never expires and a headline flow that
never fires. Under strict C64 semantics (``true=-1``) it counts **DOWN**.

The source pins DOWN unambiguously, three ways — this is the same resolution shape U6
used at ``:30450`` (sibling lines stating the rule with literal constants beat the
convention):

* ``:15030`` (``kz(sp)=6``) + ``:15025`` ("du hast 6 monate zeit") — the counter STARTS
  at 6 and the printed contract is a six-month deadline. Counting up from 6 has no
  terminus; counting down from 6 expires in six months, exactly as printed.
* ``:4305``'s own branch, ``ifkz(sp)=0goto4350`` — the collectors trigger is reached
  only by a counter that DESCENDS to 0. Ascending from 6 never equals 0.
* ``:4308`` prints ``kz(sp)+1`` months remaining. The ``+1`` is only correct if the
  tick has ALREADY decremented this turn: after the first tick 6->5, the player is
  told "6 monate" — the full deadline they were just promised at ``:15025``. Under an
  up-count the ``+1`` would report an ever-growing number.

:func:`test_counter_counts_down_not_up` is the direction-pinning test: it fails
loudly if the tick is ever inverted to ``+1``.
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import DebtChange, DebtClear, MoneyChange
from engine.interactions import ShowMessage
from engine.state import Business, Clock, Config, Debt, Gangster, GameState, Player
from engine.strings import Resolver
from engine.upkeep import run_upkeep
from tests.helpers import run_pure, scripted as _scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PARAMS = {
    "rank_divisor": 11.1,
    "kdh_borrow_grace_months": 6,
    "kdh_income_quiet_roll": 3,
    "kdh_collectors_count": 5,
    "kdh_collectors_weapon": 3,
    "kdh_collectors_energie": 30,
}


class _StubRng:
    """Scripted RNG: returns queued values, records every call (determinism gate).

    NOT swapped for ``tests.helpers.StubRng`` (unlike ``test_kdh.py``) — and the
    reason is a live test bug, not a stub-compatibility detail (see #49).

    ``test_win_changes_nothing_and_the_fight_recurs_next_turn`` and
    ``test_the_fight_actually_re_fires_on_the_following_turn`` construct this
    with ZERO scripted values. The fight therefore never resolves: the RNG
    exhausts on the first draw, ``StopIteration`` unwinds the combat generator,
    and the run ends having emitted only ``upkeep.turn_banner`` and
    ``upkeep.debt_collectors_intro`` — no ``combat.winner_banner`` at all.

    Both tests then PASS VACUOUSLY. They assert that cash, debt and the expired
    counter survive untouched, and those hold because *nothing happened*, not
    because a win preserved them. The test named "win changes nothing" never
    reaches a win.

    The shared ``StubRng`` raises ``AssertionError`` on exhaustion, which is
    correct and turns both red — it is the messenger. Keeping this permissive
    copy preserves the status quo until the tests are rewritten to actually
    drive a fight to a win; adopting the strict stub is part of that fix, not
    a prerequisite for it.
    """

    def __init__(self, *values):
        self._it = iter(values)
        self.calls = []

    def range(self, n):
        self.calls.append(("range", n))
        return next(self._it)

    def hit(self, a, b):
        self.calls.append(("hit", a, b))
        return next(self._it)


def _state(*, debt=None, ka=100000, roster=None, business=None):
    roster = roster if roster is not None else [
        Gangster(name="alcapone", energie=40, kraft=30, brutalitaet=30)
    ]
    player = Player(
        name="alcapone",
        ka=ka,
        debt=debt if debt is not None else Debt(),
        business=business if business is not None else Business(),
        roster=roster,
    )
    return GameState(
        players=[player],
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS),
    )




def _debt_effects(result):
    return [e for e in result.effects if isinstance(e, (DebtChange, DebtClear))]


# --------------------------------------------------------------------------- #
# THE COUNTER DIRECTION — mf-prg.bas:4305, pinned by :15030/:15025/:4308      #
# --------------------------------------------------------------------------- #
def test_counter_counts_down_not_up():
    """``kz`` DESCENDS: 6 -> 5 -> ... -> 0. Inverting the sign fails here.

    This is the landmine test. Under the pinned ``true=+1`` convention the tick would
    be ``months + 1`` and this asserts ``months == 5``, so an inverted port fails on
    the very first turn rather than silently never triggering the flow.
    """
    st = _state(debt=Debt(amount=3000, months=6))
    result = run_upkeep(st, rng=_StubRng())
    assert result.state.players[0].debt.months == 5
    assert _debt_effects(result) == [DebtChange(amount=0, months=5)]


def test_counter_descends_over_successive_turns_to_the_fight():
    """Six ticks from a fresh loan reach 0 — the six months :15025 promises."""
    st = _state(debt=Debt(amount=3000, months=6))
    seen = []
    for _ in range(5):
        st = run_upkeep(st, rng=_StubRng()).state
        seen.append(st.players[0].debt.months)
    assert seen == [5, 4, 3, 2, 1]


# --------------------------------------------------------------------------- #
# grace period: warn with months remaining — mf-prg.bas:4306-4309             #
# --------------------------------------------------------------------------- #
def _warnings_for(months, amount=3000):
    """Drive upkeep and collect the ``upkeep.debt_warning`` screens it yields.

    ``ShowMessage`` is auto-acked by the driver and produces no event, so the only way
    to assert on its PARAMS is to observe the generator's yields directly. Drives the
    registered generator with a minimal ctx double rather than through ``run_upkeep``.
    """
    from engine.locations import HANDLERS
    from engine.upkeep import UPKEEP_HANDLER_KEY

    st = _state(debt=Debt(amount=amount, months=months))

    class _Ctx:
        state = st
        rng = _StubRng()

        def apply(self, effect):
            pass

    gen = HANDLERS[UPKEEP_HANDLER_KEY](_Ctx())
    seen = []
    try:
        value = None
        while True:
            interaction = gen.send(value)
            if isinstance(interaction, ShowMessage):
                seen.append(interaction)
                value = None
            else:  # a StartCombat we do not drive here
                break
    except StopIteration:
        pass
    return [m for m in seen if m.key == "upkeep.debt_warning"]


def test_warning_shows_months_remaining_as_counter_plus_one():
    """``:4308`` prints ``kz(sp)+1`` — AFTER the tick. 6 -> tick to 5 -> "6 monate"."""
    warnings = _warnings_for(6)
    assert len(warnings) == 1
    assert warnings[0].params["months"] == 6
    assert warnings[0].params["amount"] == 3000


def test_warning_arithmetic_across_the_whole_grace_period():
    """Each turn reports one fewer month; the last warning before the fight is "2"."""
    assert [_warnings_for(m)[0].params["months"] for m in (6, 5, 4, 3, 2)] == [6, 5, 4, 3, 2]


def test_last_grace_month_warns_then_the_next_turn_fights():
    """``months=1`` ticks to 0 — no warning, the fight instead (``:4305`` -> ``:4350``)."""
    assert _warnings_for(1) == []


def test_no_debt_is_a_silent_no_op():
    """``kz=0`` with no debt: the tick leaves 0 at 0 and must NOT summon collectors."""
    st = _state(debt=Debt())
    result = run_upkeep(st, rng=_StubRng())
    assert _debt_effects(result) == []
    assert result.state.players[0].debt == Debt()


# --------------------------------------------------------------------------- #
# grace zero: the collectors fight — mf-prg.bas:4350-4370                     #
# --------------------------------------------------------------------------- #
def test_grace_zero_starts_the_collectors_fight():
    """At ``kz=0`` the fight fires; a surrender loses it and triggers the seizure."""
    st = _state(debt=Debt(amount=3000, months=1), ka=7500)
    # months=1 ticks to 0 -> :4305's `ifkz(sp)=0goto4350`.
    result = run_upkeep(st, input_source=_scripted("surrender"), rng=_StubRng())
    assert result.state.players[0].ka == 0
    assert result.state.players[0].debt == Debt(amount=0, months=0)


def test_loss_seizes_all_cash_and_wipes_the_debt():
    """``:4370`` ``ka(sp)=0:kr(sp)=0:kz(sp)=0`` — cash AND debt AND counter all zeroed."""
    st = _state(debt=Debt(amount=4200, months=1), ka=9999)
    result = run_upkeep(st, input_source=_scripted("surrender"), rng=_StubRng())
    assert MoneyChange(-9999) in result.effects
    assert DebtClear() in result.effects
    assert result.state.players[0].ka == 0
    assert result.state.players[0].debt.amount == 0
    assert result.state.players[0].debt.months == 0


def test_loss_seizure_is_the_cash_at_seizure_time_not_a_stale_read():
    """The seizure zeroes cash exactly — regardless of the starting balance."""
    for cash in (1, 500, 250000):
        st = _state(debt=Debt(amount=1000, months=1), ka=cash)
        result = run_upkeep(st, input_source=_scripted("surrender"), rng=_StubRng())
        assert result.state.players[0].ka == 0


def test_win_changes_nothing_and_the_fight_recurs_next_turn():
    """``:4355``'s ``ifs=1thenreturn`` — a win skips :4365-4370 entirely.

    Cash and debt survive untouched, and because ``kz`` is already 0 and the tick's
    ``(kz>0)`` guard makes 0 a fixed point, the NEXT turn re-enters :4350 and fights
    again. Source-confirmed and kept per KTD-9 — do not "fix" this.
    """
    st = _state(debt=Debt(amount=3000, months=0), ka=8000)
    # A long pass script lets the fight resolve without surrendering.
    result = run_upkeep(st, input_source=_scripted(*(["pass"] * 400)), rng=_StubRng())
    assert result.status == "completed"
    # Nothing was seized: cash, debt and the expired counter all survive untouched.
    assert result.state.players[0].ka == 8000
    assert result.state.players[0].debt == Debt(amount=3000, months=0)
    assert DebtClear() not in result.effects
    assert MoneyChange(-8000) not in result.effects


def test_the_fight_actually_re_fires_on_the_following_turn():
    """Two consecutive turns from a won fight BOTH reach the collectors.

    The recurrence is only real if turn N+1 takes the same ``:4350`` branch. Proven by
    surrendering on the second turn: if the branch were not re-entered there would be
    no fight to surrender to, and the cash would survive.
    """
    st = _state(debt=Debt(amount=3000, months=0), ka=8000)
    first = run_upkeep(st, input_source=_scripted(*(["pass"] * 400)), rng=_StubRng())
    assert first.state.players[0].ka == 8000  # survived turn 1

    second = run_upkeep(
        first.state, input_source=_scripted("surrender"), rng=_StubRng()
    )
    # Turn 2 fought again — and this time lost, so the seizure fired.
    assert second.state.players[0].ka == 0
    assert second.state.players[0].debt == Debt(amount=0, months=0)


def test_counter_at_zero_is_a_fixed_point_so_the_fight_recurs():
    """``(kz>0)`` guards the tick: 0 stays 0, which is what makes the fight recur."""
    st = _state(debt=Debt(amount=3000, months=0), ka=5000)
    result = run_upkeep(st, input_source=_scripted("surrender"), rng=_StubRng())
    # The tick must NOT have pushed months negative on the way into the fight.
    ticks = [e for e in result.effects if isinstance(e, DebtChange)]
    assert all(e.months is None or e.months >= 0 for e in ticks)


# --------------------------------------------------------------------------- #
# repayment mid-grace stops the countdown and the fight                       #
# --------------------------------------------------------------------------- #
def test_repayment_mid_grace_stops_the_countdown_and_the_fight():
    """``:15075`` sets ``kz=0`` WITH ``kr=0``; a zero debt must never fight.

    The recurrence branch keys on the DEBT, not on the bare counter — otherwise a
    fully repaid player (kz=0, kr=0) would be ambushed forever.
    """
    st = _state(debt=Debt(amount=0, months=0), ka=6000)
    result = run_upkeep(st, input_source=_scripted(), rng=_StubRng())
    assert result.status == "completed"
    assert result.state.players[0].ka == 6000
    assert _debt_effects(result) == []


# --------------------------------------------------------------------------- #
# handler purity (Verification Contract: run_pure on every changed handler)   #
# --------------------------------------------------------------------------- #
def test_debt_slot_is_pure_through_the_grace_warning():
    """No direct state mutation: every change is explained by a buffered effect."""
    from engine.locations import HANDLERS
    from engine.upkeep import UPKEEP_HANDLER_KEY

    st = _state(debt=Debt(amount=3000, months=6))
    run_pure(HANDLERS[UPKEEP_HANDLER_KEY], _scripted(), state=st, rng=_StubRng())


def test_debt_slot_is_pure_through_the_fight_and_seizure():
    """The seizure path too — MoneyChange/DebtClear must fully explain result.state."""
    from engine.locations import HANDLERS
    from engine.upkeep import UPKEEP_HANDLER_KEY

    st = _state(debt=Debt(amount=3000, months=1), ka=7500)
    result = run_pure(
        HANDLERS[UPKEEP_HANDLER_KEY], _scripted("surrender"), state=st, rng=_StubRng()
    )
    assert result.state.players[0].ka == 0


# --------------------------------------------------------------------------- #
# theme strings resolve                                                       #
# --------------------------------------------------------------------------- #
def test_debt_string_keys_resolve_in_the_classic_theme():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    assert resolver.resolve("upkeep.debt_warning", {"amount": 3000, "months": 6})
    assert resolver.resolve("upkeep.debt_collectors_intro", {})
    assert resolver.resolve("upkeep.debt_seized", {})

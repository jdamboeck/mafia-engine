"""Tests for the police capture and trial — ports ``mf-prg.bas:26000-26080``.

The module (``handlers/police.py``) registers no handler; its callers arrive in later
units. So every test drives its three entries the way a caller will: a caller-shaped
test handler that may change state first and then ends with ``yield from`` one of the
entries ``police_fight``, ``caught`` and ``sentence`` (the source's :26000, :26020 and
:26045). The turn-level
tests put such a handler behind a location door and run it through the engine's turn
runner.

The rng is the strict shared ``StubRng`` (raw values, raises when drawn past its
script). ``caught`` always draws the ``:26021`` chief-bribe roll first.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.effects import Jail, JobClear, ScoreAndRank, TipClear
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.handlers.police import (
    ACQUITTED,
    BRIBED,
    ESCAPED,
    FOUGHT_OFF,
    SENTENCED,
    Arrest,
    caught,
    police_fight,
    sentence,
)
from data.game_configs.mafia_1920s.state import SCHEMA, Job, Wanted, values_of
import data.game_configs.mafia_1920s.state as game
from engine.combat import STEP_RIGHT
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, SetMovementPoints, Teleport
from engine.interactions import (
    Acknowledge,
    Confirm,
    Ctx,
    Heading,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptChoice,
    PromptInt,
    ShowMessage,
    StartCombat,
)
from engine.locations import Location, Option
from engine.state import Clock, Config, GameState, Player
from engine.turns import TURN_OVER_SCREEN, UPKEEP_SCREEN, WALKING, TurnRunner
from tests.helpers import StubRng, run_pure, scripted
from tests.test_turn_runner import _approach, _door

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)
_PARAMS = {**load_config(_CONFIG_DIR / "config.yaml")["formula_params"], "score_mult": 1.0}

_BRIBE, _FLEE, _SURRENDER = 0, 1, 2
#: The map step's p after a roadblock at cell 500: :2030 ``p=br+po(sp)+x``, br=52224.
_ROADBLOCK_P = 52224 + 500


def _score(x: float) -> ScoreAndRank:
    return ScoreAndRank(amount=x, rank_divisor=11.1)


def _rules(**settings: str) -> dict[str, str]:
    rules = {
        "intelligence_or_30": "faithful",
        "shared_direction_memory": "faithful",
        "stale_bribe_price": "faithful",
        "flight_odds_by_seat": "faithful",
        "chief_bribe_negative_months": "faithful",
        "chief_bribe_empty_answer": "faithful",
    }
    rules.update(settings)
    return rules


def _player(name: str = "alcapone", **fields) -> Player:
    values = {
        **SCHEMA.player_defaults(),
        **values_of(
            Wanted(bribe_months=fields.pop("bribe_months", 0)),
            Job(**fields.pop("job", {})),
            tip_target=fields.pop("tip", 0),
        ),
    }
    fields.setdefault("ka", 10_000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("po", 400)
    fields.setdefault("ms", 20)
    fields.setdefault("roster", (Gangster(name=name, energie=40, kraft=30, brutalitaet=30),))
    return Player(name=name, values=values, **fields)


def _state(*, seats: int = 1, active: int = 0, rules=None, **fields) -> GameState:
    players = tuple(
        _player(**fields) if i == active else _player(name=f"p{i}") for i in range(seats)
    )
    return GameState(
        players=players,
        clock=Clock(active_player=active, player_count=seats),
        config=Config(formula_params=_PARAMS, house_rules=rules or _rules()),
    )


def _caller(entry, arrest: Arrest, *, before=(), **kw):
    """A caller-shaped handler: apply ``before``, then end in ``yield from`` the entry."""

    def handler(ctx):
        for effect in before:
            ctx.apply(effect)
        return (yield from entry(ctx, arrest, **kw))

    return handler


def _run(entry, arrest, answers=(), draws=(), **state_fields):
    rng = StubRng(*draws)
    source = scripted(*answers)
    handler = _caller(entry, arrest, before=state_fields.pop("before", ()))
    result = run_pure(handler, source, state=_state(**state_fields), rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _sentenced(months: int) -> list:
    """The effects of :26045 then :26080 with no lawyer."""
    return [
        TipClear(),
        _score(2),
        Jail(months=months),
        SetMovementPoints(0),
        JobClear(),
        _score(-10),
        Teleport(911),
    ]


def _asked(source, cls) -> list:
    return [i for i in source.seen if isinstance(i, cls)]


# --------------------------------------------------------------------------- #
# :26021 — the chief-bribe auto-pay and its stale p (house rule stale_bribe_price)
# --------------------------------------------------------------------------- #
def _ae7_game(setting: str) -> GameState:
    """A new game at the setup defaults (or the one switch set), the player at rank 4
    (the roadblock needs more than 3) with chief-bribe months left."""
    state = _CONFIG.module.new_game(
        seed=42,
        end_year=1930,
        score_weight=1.0,
        players=[("alcapone", "the outfit")],
        house_rules={} if setting == "faithful" else {"stale_bribe_price": setting},
    )
    player = state.players[0]
    values = {**player.values, **values_of(Wanted(bribe_months=2))}
    player = replace(player, rank=4, values=values)
    return replace(state, players=(player,))


def test_ae7_the_auto_bribe_asks_for_the_stale_p_after_a_roadblock_and_sentences():
    """AE7, faithful: the 1-in-2 roll hits, the menu is skipped, and :26037 compares
    the cash with the leftover map-step p (52224 plus the cell): unpayable, sentenced."""
    state = _ae7_game("faithful")
    assert state.players[0].ka < _ROADBLOCK_P
    rng = StubRng(1)  # :26021 int(rnd(1)*2)<>0
    source = scripted()
    result = run_pure(_caller(caught, Arrest(p=_ROADBLOCK_P)), source, state=state, rng=rng)

    assert result.payload.returned == SENTENCED
    assert _asked(source, PromptChoice) == []  # the menu never showed
    assert source.message_keys()[:2] == ["police.caught", "system.not_enough_money"]
    assert result.effects == _sentenced(2)  # rank 4: int(4/2+.5) = 2 months, no lawyer
    assert rng.calls == [("range", 2)]


def test_ae7_intent_the_auto_bribe_charges_the_ordinary_bribe():
    """AE7, intent: the skipped menu charges 500+500*rank (2500 at rank 4), paid and
    let go when the 1-in-5 roll misses."""
    state = _ae7_game("intent")
    rng = StubRng(1, 1)  # :26021 hits; :26038 int(rnd(1)*5) = 1, not 0
    source = scripted()
    result = run_pure(_caller(caught, Arrest(p=_ROADBLOCK_P)), source, state=state, rng=rng)

    assert result.payload.returned == BRIBED
    assert _asked(source, PromptChoice) == []
    assert result.effects == [MoneyChange(-2500)]
    assert source.message_keys() == ["police.caught", "police.let_go"]


def test_a_stale_p_of_zero_with_no_cash_walks_free_four_times_in_five():
    """Faithful: a p of 0 is affordable with no cash (:26037 ``ka(sp)<p`` is false),
    the 0 is paid and :26038's 1-in-5 roll alone sends the player to trial."""
    outcomes = []
    for roll in range(5):
        result, _ = _run(caught, Arrest(p=0), draws=(1, roll), ka=0, bribe_months=1)
        outcomes.append(result.payload.returned)
        assert result.state.players[0].ka == 0
    assert outcomes == [SENTENCED, BRIBED, BRIBED, BRIBED, BRIBED]


def test_without_chief_bribe_months_the_menu_shows_even_when_the_roll_hits():
    result, source = _run(caught, Arrest(p=0), answers=(_SURRENDER,), draws=(1,))
    assert len(_asked(source, PromptChoice)) == 1
    assert result.payload.returned == SENTENCED


# --------------------------------------------------------------------------- #
# :26035-26039 — the bribe
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rank", [1, 4, 10])
def test_a_bribe_accepted_at_the_right_price_lets_the_player_go_four_times_in_five(rank):
    price = 500 + 500 * rank
    outcomes = []
    for roll in range(5):
        result, source = _run(
            # (the trial's lawyer offer, at rank 5 and up, is declined)
            caught,
            Arrest(p=0),
            answers=(_BRIBE, True, False),
            draws=(0, roll),
            rank=rank,
        )
        outcomes.append(result.payload.returned)
        demand = [m for m in source.messages() if m.key == "police.bribe_demand"]
        assert [m.params for m in demand] == [{"price": price}]
        assert result.effects[0] == MoneyChange(-price)
        assert result.state.players[0].ka == 10_000 - price
    assert outcomes == [SENTENCED, BRIBED, BRIBED, BRIBED, BRIBED]


def test_a_declined_bribe_goes_to_the_sentence():
    """:26036 ``ifx$="n"thenx=2:gosub1160:goto26045``: +2, then the trial's +2."""
    result, source = _run(caught, Arrest(p=0), answers=(_BRIBE, False), draws=(0,))
    assert result.payload.returned == SENTENCED
    assert result.effects == [_score(2), *_sentenced(1)]


def test_an_unaffordable_bribe_goes_to_the_sentence():
    """:26037 ``ifka(sp)<pthengosub1125:goto26045``: nothing is paid."""
    result, source = _run(caught, Arrest(p=0), answers=(_BRIBE, True), draws=(0,), ka=999)
    assert result.payload.returned == SENTENCED
    assert "system.not_enough_money" in source.message_keys()
    assert result.effects == _sentenced(1)


# --------------------------------------------------------------------------- #
# :26040-26043 — the flight (house rule flight_odds_by_seat)
# --------------------------------------------------------------------------- #
#: Seat 1 is vehicle 1's range (35); the player drives the auburn (vehicle 4, 60).
_FLIGHT = dict(answers=(_FLEE,), vehicle=4)


def test_the_flight_odds_follow_the_seat_index_when_faithful():
    """:26040 reads ``tr(sp)``: seat 1's range, 35, whatever the player drives."""
    result, _ = _run(caught, Arrest(p=0), draws=(0, 10), **_FLIGHT)
    assert result.payload.returned == SENTENCED  # int(rnd*35) = 10 < 11: caught
    escaped, _ = _run(caught, Arrest(p=0), draws=(0, 11), **_FLIGHT)
    assert escaped.payload.returned == ESCAPED
    rng = StubRng(0, 11)
    run_pure(_caller(caught, Arrest(p=0)), scripted(_FLEE), state=_state(vehicle=4), rng=rng)
    assert rng.calls == [("range", 2), ("range", 35)]
    # Seat 4 is vehicle 4's range, 60.
    rng = StubRng(0, 11)
    st = _state(seats=4, active=3, vehicle=0)
    run_pure(_caller(caught, Arrest(p=0)), scripted(_FLEE), state=st, rng=rng)
    assert rng.calls == [("range", 2), ("range", 60)]


def test_the_flight_odds_follow_the_players_vehicle_under_intent():
    rng = StubRng(0, 11)
    st = _state(vehicle=4, rules=_rules(flight_odds_by_seat="intent"))
    result = run_pure(_caller(caught, Arrest(p=0)), scripted(_FLEE), state=st, rng=rng)
    assert rng.calls == [("range", 2), ("range", 60)]
    assert result.payload.returned == ESCAPED


def test_a_failed_flight_costs_five_and_goes_to_the_sentence():
    result, source = _run(caught, Arrest(p=0), draws=(0, 0), answers=(_FLEE,))
    assert result.effects == [_score(-5), *_sentenced(1)]
    assert "police.flight_failed" in source.message_keys()


# --------------------------------------------------------------------------- #
# :26050-26072 — the lawyer
# --------------------------------------------------------------------------- #
def test_the_lawyer_prompt_asks_again_only_above_the_cash_or_below_zero():
    """:26061 ``ifx>ka(sp)orx<0then`` asks again; a fee above the prompt's 10000 is
    taken (house rule lawyer_fee_uncapped). An empty answer after a refusal keeps the
    refused fee on the C64, so it is asked again too."""
    # rank 5: int(5/2+.5) = 3 months; 20000 cuts int(rnd*21)+1 months: draw 999 -> 1.
    result, source = _run(
        sentence,
        Arrest(),
        answers=(True, 40_000, -5, "", 20_000),
        draws=(999,),
        rank=5,
        ka=30_000,
    )
    assert len(_asked(source, PromptInt)) == 4
    assert result.payload.returned == SENTENCED
    assert result.effects[2] == MoneyChange(-20_000)
    assert game.wanted(result.state.players[0]).jail_months == 2
    assert [m.params for m in source.messages() if m.key == "police.lawyer_result"] == [
        {"months": 2}
    ]


@pytest.mark.parametrize("answer", [0, ""])
def test_a_lawyer_fee_of_zero_or_an_empty_answer_means_no_lawyer(answer):
    """:26060 ``x=val(x$):ifx=0goto26075``; an empty answer keeps the "j" in x$."""
    result, source = _run(sentence, Arrest(), answers=(True, answer), rank=5)
    assert len(_asked(source, PromptInt)) == 1
    assert result.effects == _sentenced(3)
    assert "police.sentenced" in source.message_keys()


def test_no_lawyer_is_offered_below_rank_five():
    result, source = _run(sentence, Arrest(), rank=4)
    assert source.seen and all(isinstance(i, ShowMessage) for i in source.seen)
    assert result.effects == _sentenced(2)


# --------------------------------------------------------------------------- #
# The outcomes
# --------------------------------------------------------------------------- #
def test_a_sentence_sets_the_months_ends_the_turn_and_jails_the_player():
    """:26045 then :26080: the months, ms=0, cell 911, no job, no tip, -10 score."""
    result, source = _run(
        sentence, Arrest(), rank=3, job={"type": 1, "pending_pay": 2000, "months_left": 2}, tip=4
    )
    player = result.state.players[0]
    assert result.payload.returned == SENTENCED
    assert game.wanted(player).jail_months == 2
    assert player.ms == 0
    assert player.po == 911
    assert game.job(player) == Job()
    assert game.tip_target(player) == 0
    assert player.gf == 50 + 2 - 10
    assert [m.params for m in source.messages() if m.key == "police.sentenced"] == [{"months": 2}]


def test_an_acquittal_clears_the_tip_and_keeps_the_job():
    """:26070 ``ms=0:goto1100`` skips :26080: the job, the cell and the score stay."""
    held = {"type": 1, "pending_pay": 2000, "months_left": 2}
    # rank 5: 3 months; a 2000 fee cuts int(rnd*3)+1: draw 2999 -> 3.
    result, source = _run(
        sentence, Arrest(), answers=(True, 2000), draws=(2999,), rank=5, job=held, tip=4
    )
    player = result.state.players[0]
    assert result.payload.returned == ACQUITTED
    assert result.effects == [
        TipClear(),
        _score(2),
        MoneyChange(-2000),
        Jail(months=0),
        SetMovementPoints(0),
    ]
    assert game.job(player) == Job(**held)
    assert game.tip_target(player) == 0
    assert (player.po, player.ms, player.gf) == (400, 0, 52)
    assert "police.acquitted" in source.message_keys()


def test_an_invalid_menu_key_is_ignored():
    """:26025 ``getx$:ifx$<"1"orx$>"3"goto26025``: the menu is asked again."""
    result, source = _run(caught, Arrest(p=0), answers=(3, "x", -1, _SURRENDER), draws=(0,))
    assert len(_asked(source, PromptChoice)) == 4
    assert result.payload.returned == SENTENCED
    assert source.message_keys().count("police.caught") == 1


def test_a_caller_that_changed_cash_before_capture_sees_its_own_change():
    """The caller spent 6600 of 7000 before the arrest; ctx.state still shows 7000, so
    only the entry record tells capture the 1000 bribe cannot be paid."""
    spent = MoneyChange(-6600)
    result, source = _run(
        caught,
        Arrest(p=0, cash=400),
        answers=(_BRIBE, True),
        draws=(0,),
        ka=7000,
        before=(spent,),
    )
    assert result.payload.returned == SENTENCED
    assert "system.not_enough_money" in source.message_keys()
    assert result.effects == [spent, *_sentenced(1)]


# --------------------------------------------------------------------------- #
# The police fight — :26000-26015
# --------------------------------------------------------------------------- #
def _squad(rank: int, count_draw: int, energy_draw: int):
    """The police side :26000-26010 builds, read off the fight's StartCombat."""
    state = _state(rank=rank)
    rng = StubRng(count_draw, energy_draw)
    gen = police_fight(Ctx(state=state, rng=rng), Arrest())
    start = next(gen)
    # :26000 int(rnd(1)*ra(sp)/2) as range(ra)//2; :26010 int(rnd(1)*21), 0..20.
    assert rng.calls == [("range", rank), ("range", 21)]
    assert isinstance(start, StartCombat)
    assert start.scenario is not None and start.scenario.sides is not None
    return start.scenario.sides[1]


@pytest.mark.parametrize("rank", range(1, 11))
def test_the_police_weapon_and_energy_follow_the_rank(rank):
    """:26010 ``w=5-2*(ra(sp)>5)``: the revolver (5) up to rank 5, the gewehr (7) above.
    ``e=20+2*(ra(sp)-1)-int(rnd(1)*21)`` stays within 20+2*(rank-1) minus 0..20, one
    value for the whole squad; :26000 ``gz(0)=5+int(rnd(1)*ra(sp)/2)`` police."""
    top = 20 + 2 * (rank - 1)
    low = _squad(rank, count_draw=0, energy_draw=20)
    high = _squad(rank, count_draw=rank - 1, energy_draw=0)
    assert {f.weapon for f in low + high} == {5 if rank <= 5 else 7}
    assert {f.vitality for f in low} == {top - 20}
    assert {f.vitality for f in high} == {top}
    assert (len(low), len(high)) == (5, 5 + (rank - 1) // 2)


#: Five gangsters that down one policeman per shot before the police act (the debt
#: collectors' winning gang: the police line up on the same ks rows).
_WINNING_GANG = tuple(
    Gangster(name=name, weapon=7, energie=40, kraft=30, brutalitaet=290)
    for name in ("alcapone", "luigi", "mario", "vito", "tony")
)
_ONE_SHOT_KILL = (1, 10, 0)


def _won_police_fight():
    return run_pure(
        _caller(police_fight, Arrest()),
        scripted(*([("shoot", STEP_RIGHT)] * 5)),
        state=_state(roster=_WINNING_GANG),
        rng=StubRng(0, 0, *(_ONE_SHOT_KILL * 5)),  # 5 police, energy 20
    )


def test_a_won_police_fight_scores_two_and_leaves_the_turn_running():
    result = _won_police_fight()
    assert result.payload.returned == FOUGHT_OFF
    assert result.effects == [_score(2)]
    assert result.state.players[0].ms == 20


def test_an_escape_leaves_the_turn_running():
    result, _ = _run(caught, Arrest(p=0), answers=(_FLEE,), draws=(0, 11))
    assert result.payload.returned == ESCAPED
    assert result.effects == [_score(2)]
    assert result.state.players[0].ms == 20


# --------------------------------------------------------------------------- #
# The score per path — one table
# --------------------------------------------------------------------------- #
_PATHS = {
    # path: (entry, arrest, answers, draws, state fields, outcome, net score)
    "fought off": None,
    "bribed": (caught, Arrest(p=0), (_BRIBE, True), (0, 1), {}, BRIBED, 0),
    "escaped": (caught, Arrest(p=0), (_FLEE,), (0, 11), {}, ESCAPED, 2),
    "surrendered": (caught, Arrest(p=0), (_SURRENDER,), (0,), {}, SENTENCED, 2 - 10),
    "bribe declined": (caught, Arrest(p=0), (_BRIBE, False), (0,), {}, SENTENCED, 2 + 2 - 10),
    "bribe unaffordable": (
        caught,
        Arrest(p=0),
        (_BRIBE, True),
        (0,),
        {"ka": 0},
        SENTENCED,
        2 - 10,
    ),
    "bribe paid, jailed": (caught, Arrest(p=0), (_BRIBE, True), (0, 0), {}, SENTENCED, 2 - 10),
    "flight failed": (caught, Arrest(p=0), (_FLEE,), (0, 0), {}, SENTENCED, -5 + 2 - 10),
    "acquitted": (sentence, Arrest(), (True, 2000), (2999,), {"rank": 5}, ACQUITTED, 2),
}


@pytest.mark.parametrize("path", list(_PATHS))
def test_the_score_change_per_path(path):
    if path == "fought off":
        result, outcome, net = _won_police_fight(), FOUGHT_OFF, 2
    else:
        entry, arrest, answers, draws, fields, outcome, net = _PATHS[path]
        result, _ = _run(entry, arrest, answers=answers, draws=draws, **fields)
    assert result.payload.returned == outcome
    assert result.state.players[0].gf == 50 + net


# --------------------------------------------------------------------------- #
# Through the turn runner: a location handler that calls capture
# --------------------------------------------------------------------------- #
def _drive_turn(entry, arrest, draws, answers=()):
    """Walk player 0 into a pub whose one option ends in ``entry``; return the screens
    from the option's end up to the next player's upkeep, or the next map prompt."""
    door = _door("pub")[0]
    start, into = _approach(door)
    state = _state(seats=2, po=start, rank=1)
    state = replace(state, clock=replace(state.clock, turn_phase=WALKING))
    shells = {"pub": Location("pub", [Option("act", handler=_caller(entry, arrest))])}
    runner = TurnRunner(
        state,
        StubRng(*draws),
        city=_CONFIG.city,
        shells=shells,
        turn_menu=_CONFIG.menus["turn"],
    )
    gen = runner.run()
    pending = list(answers)
    seen: list = []
    interaction = next(gen)
    moves = 0
    while True:
        seen.append(interaction)
        if isinstance(interaction, Heading) and interaction.key == UPKEEP_SCREEN:
            break
        if isinstance(interaction, MapMove):
            moves += 1
            if moves > 1:
                break
            response = into
        elif isinstance(interaction, LocationMenu):
            response = 0
        elif isinstance(interaction, (PromptChoice, PromptInt, Confirm)):
            response = pending.pop(0)
        else:
            response = None
        interaction = gen.send(response)
    gen.close()
    done = next(i for i, s in enumerate(seen) if isinstance(s, OptionDone))
    return seen[done + 1 :], runner


def test_after_a_sentence_the_next_screen_is_the_next_players():
    after, runner = _drive_turn(sentence, Arrest(), draws=())
    assert [type(s) for s in after] == [Acknowledge, Heading]
    assert (after[0].key, after[0].player) == (TURN_OVER_SCREEN, 0)
    assert (after[1].key, after[1].player) == (UPKEEP_SCREEN, 1)
    assert runner.state.players[0].po == 911


def test_after_an_escape_the_same_turn_goes_on_on_the_map():
    after, runner = _drive_turn(caught, Arrest(p=0), draws=(0, 11), answers=(_FLEE,))
    assert [type(s) for s in after] == [MapMove]
    assert after[0].player == 0
    assert runner.state.players[0].ms == 15  # the door's 5 after the visit (:2060)

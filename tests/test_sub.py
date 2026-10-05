"""Tests for the sub (Subway-Station) handlers — ports ``mf-prg.bas:18000-18052``.

::

    18010 onwgoto18035,18015
    18015 print"ein u-bahn-ticket kostet dich"
    18020 print"{down}50 $. ";:gosub1110:ifx$="n"thenreturn
    18025 ifka(sp)<50goto1125
    18030 ka(sp)=ka(sp)-50
    18035 print"{clr}{down}welchen spieler setzt du als dieb ein:":gosub1130:ify=0thenreturn
    18039 x=1:gosub1160:print"{clr}{down}du stiehlst...":fort=1to500:next
    18040 ifint(rnd(1)*15)=10goto18052
    18041 ifint(rnd(1)*(in/10))goto18045
    18042 print"{down}...nichts! denn du wirst erwischt!":gosub1100:goto26020
    18045 onint(rnd(1)*4)-(w=2)-(la<>9)goto18047,18048,18049,18050,18051
    18046-18051 handtasche 0, fotoapparat +50, perlenkette 0, armbanduhr +100,
                brieftasche +500, diamant +800
    18052 print"{down}...eine anleitung -der safeknacker- ??!":s9(sp)=5:goto1100

The draws, in order: the manual roll ``range(15)``, the catch roll ``range(in)`` (below
10 is caught), the loot roll ``range(4)``. The rng is the strict ``StubRng``; every
refusal leaves the state as it was.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.effects import (
    Jail,
    JobClear,
    SafeSkillSet,
    ScoreAndRank,
    TipClear,
)
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.state import SCHEMA, Wanted, safe_skill, values_of
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, SetMovementPoints, Teleport
from engine.interactions import Confirm, PromptInt
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from tests.helpers import StubRng, run_pure, scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)
_PARAMS = {**load_config(_CONFIG_DIR / "config.yaml")["formula_params"], "score_mult": 1.0}

_PICK = "turn.picker.prompt"
#: The capture menu's surrender key (``:26022-26023``, 0-based here).
_SURRENDER = 2
#: :18039 ``x=1:gosub1160``.
_SCORE = ScoreAndRank(amount=1, rank_divisor=11.1)
#: A manual roll that is not 10 (:18040), and a catch roll that is not caught (:18041).
_NO_MANUAL, _FREE = 0, 10

_THIEF = Gangster(name="alcapone", energie=40, kraft=30, intelligenz=40, brutalitaet=30)


def _rules() -> dict[str, str]:
    return {
        "intelligence_or_30": "faithful",
        "shared_direction_memory": "faithful",
        "stale_bribe_price": "faithful",
        "flight_odds_by_seat": "faithful",
        "c64_input_negatives": "faithful",
        "chief_bribe_empty_answer": "faithful",
        "gang_war_score_to_the_attacker": "faithful",
        "prison_brawl_zeroes_the_attackers_boss": "faithful",
        "c64_float_score": "faithful",
    }


def _state(*, bribe_months: int = 0, s9: int = 0, **fields) -> GameState:
    fields.setdefault("ka", 10_000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("ms", 20)
    fields.setdefault("rank", 1)
    fields.setdefault("last_la", 8)
    fields.setdefault("last_location", 1)
    fields.setdefault("roster", (_THIEF,))
    values = {
        **SCHEMA.player_defaults(),
        **values_of(Wanted(bribe_months=bribe_months), safe_skill=s9),
    }
    player = Player(name="alcapone", values=values, **fields)
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS, house_rules=_rules()),
    )


def _run(key: str, state: GameState, answers=(), draws=()):
    rng = StubRng(*draws)
    source = scripted(*answers)
    result = run_pure(HANDLERS[key], source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _asked(source, key: str) -> list:
    return [i for i in source.seen if getattr(i, "key", None) == key]


_SENTENCED = [
    TipClear(),
    ScoreAndRank(amount=2, rank_divisor=11.1),
    Jail(months=1),
    SetMovementPoints(0),
    JobClear(),
    ScoreAndRank(amount=-10, rank_divisor=11.1),
    Teleport(911),
]


# --------------------------------------------------------------------------- #
# The score before the outcome — :18039                                        #
# --------------------------------------------------------------------------- #
def test_a_caught_pickpocket_still_keeps_the_score():
    """:18039 pays ``x=1`` before :18041's catch roll; caught, :18042 goes to :26020
    (no fight) and nothing takes the point back. Surrender at rank 1: the trial."""
    result, source = _run(
        "sub.platform",
        _state(),
        answers=(1, _SURRENDER),
        draws=(_NO_MANUAL, 9, 0),  # no manual, caught (9 < 10), the :26021 roll
    )
    assert result.effects == [_SCORE, *_SENTENCED]
    keys = source.message_keys()
    assert keys.index("locations.sub.stealing") < keys.index("locations.sub.caught")
    assert keys.index("locations.sub.caught") < keys.index("police.caught")
    assert result.state.players[0].gf == 50.0 + 1 + 2 - 10


def test_the_arrest_gets_the_map_steps_p_and_the_cash_after_the_ticket():
    """Nothing from :2050 to :26020 sets ``p``, so the chief-bribe auto-pay (:26021 ->
    :26037, house rule ``stale_bribe_price``) charges the map step's ``p=br+po(sp)+x``:
    52224 plus the door cell (tile 2 is cell 167). With 52441 $ before the ticket the
    player can pay 52391 only because the ticket has not been counted: the cash the
    capture sees is 52391 after it, exactly enough."""
    st = _state(ka=52_441, bribe_months=1, last_location=2)
    result, source = _run(
        "sub.train",
        st,
        answers=(True, 1),
        # no manual, caught, the :26021 skip (1: to :26037), the :26038 bribe roll (let go)
        draws=(_NO_MANUAL, 0, 1, 1),
    )
    assert result.effects == [MoneyChange(-50), _SCORE, MoneyChange(-(52224 + 167))]
    assert source.message_keys()[-1] == "police.let_go"
    assert result.state.players[0].ka == 0

    # One dollar less and the stale price cannot be paid: the trial.
    result, source = _run(
        "sub.train",
        _state(ka=52_440, bribe_months=1, last_location=2),
        answers=(True, 1),
        draws=(_NO_MANUAL, 0, 1),
    )
    assert result.effects == [MoneyChange(-50), _SCORE, *_SENTENCED]
    assert "system.not_enough_money" in source.message_keys()


# --------------------------------------------------------------------------- #
# The ticket — :18015-18030                                                    #
# --------------------------------------------------------------------------- #
def test_cancelling_the_picker_loses_the_ticket():
    """:18030 pays before :18035's picker, and ``y=0`` returns: the 50 $ are gone. The
    picker is not cancellable after the ticket, so the client cannot undo the buy."""
    st = _state()
    result, source = _run("sub.train", st, answers=(True, 0))
    assert result.effects == [MoneyChange(-50)]
    assert result.state.players[0].ka == 9_950
    (prompt,) = _asked(source, _PICK)
    assert (prompt.min, prompt.max, prompt.cancellable) == (0, 1, False)
    assert "locations.sub.stealing" not in source.message_keys()


def test_the_platform_picker_is_cancellable_and_0_returns_quietly():
    st = _state()
    result, source = _run("sub.platform", st, answers=(0,))
    assert result.effects == []
    assert result.state == st
    (prompt,) = _asked(source, _PICK)
    assert (prompt.min, prompt.max, prompt.cancellable) == (0, 1, True)


def test_a_player_with_less_than_50_is_refused_with_no_ticket_charged():
    """:18015-18020 ask first; :18025 ``ifka(sp)<50goto1125`` then refuses before
    :18030 charges. No picker, no draw."""
    st = _state(ka=49)
    result, source = _run("sub.train", st, answers=(True,))
    assert result.effects == []
    assert result.state == st
    assert source.message_keys() == ["locations.sub.ticket", "system.not_enough_money"]
    assert _asked(source, _PICK) == []

    result, _ = _run("sub.train", _state(ka=50), answers=(True, 0))
    assert result.effects == [MoneyChange(-50)]


def test_declining_the_ticket_changes_nothing():
    """:18020 ``ifx$="n"thenreturn``."""
    st = _state()
    result, source = _run("sub.train", st, answers=(False,))
    assert result.effects == []
    assert result.state == st
    ticket = source.messages()[0]
    assert (ticket.key, ticket.params) == ("locations.sub.ticket", {"price": 50})
    assert any(isinstance(i, Confirm) for i in source.seen)


# --------------------------------------------------------------------------- #
# The safecracker's manual — :18040, :18052                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("before", [0, 3, 5])
def test_the_manual_sets_the_bonus_to_5_and_a_second_does_not_stack(before):
    """:18040 ``ifint(rnd(1)*15)=10goto18052``; :18052 ``s9(sp)=5`` sets it: whatever
    was left (a second manual included), it is 5. No catch roll is drawn."""
    result, source = _run("sub.platform", _state(s9=before), answers=(1,), draws=(10,))
    assert result.effects == [_SCORE, SafeSkillSet(5)]
    assert safe_skill(result.state.players[0]) == 5
    assert source.message_keys()[-1] == "locations.sub.loot_manual"
    assert result.state.players[0].ka == 10_000


@pytest.mark.parametrize("roll", [r for r in range(15) if r != 10])
def test_any_other_manual_roll_goes_on_to_the_catch_roll(roll):
    result, _ = _run("sub.platform", _state(), answers=(1,), draws=(roll, _FREE, 0))
    assert not any(isinstance(e, SafeSkillSet) for e in result.effects)


# --------------------------------------------------------------------------- #
# The catch roll — :18041                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("intelligenz", "roll"), [(40, 9), (40, 10), (11, 10), (99, 98)])
def test_the_thief_is_caught_below_10_of_his_intelligence(intelligenz, roll):
    """``int(rnd(1)*(in/10))`` is 0 iff ``int(rnd(1)*in) < 10``."""
    thief = Gangster(name="t", energie=40, kraft=30, intelligenz=intelligenz, brutalitaet=30)
    caught = roll < 10
    draws = (_NO_MANUAL, roll, 0)  # the third is :26021's roll, or the loot roll
    answers = (1, _SURRENDER) if caught else (1,)
    rng = StubRng(*draws)
    result = run_pure(
        HANDLERS["sub.platform"], scripted(*answers), state=_state(roster=(thief,)), rng=rng
    )
    assert rng.calls[:2] == [("range", 15), ("range", intelligenz)]
    assert (Teleport(911) in result.effects) == caught


@pytest.mark.parametrize("intelligenz", [0, 5, 10])
def test_a_thief_of_intelligence_10_or_less_is_always_caught(intelligenz):
    thief = Gangster(name="t", energie=40, kraft=30, intelligenz=intelligenz, brutalitaet=30)
    top = max(intelligenz, 1) - 1
    result, source = _run(
        "sub.platform",
        _state(roster=(thief,)),
        answers=(1, _SURRENDER),
        draws=(_NO_MANUAL, top, 0),
    )
    assert "locations.sub.caught" in source.message_keys()


# --------------------------------------------------------------------------- #
# The loot — :18045-18051                                                      #
# --------------------------------------------------------------------------- #
_LOOT = [
    ("handbag", 0),
    ("camera", 50),
    ("pearls", 0),
    ("watch", 100),
    ("wallet", 500),
    ("diamond", 800),
]


@pytest.mark.parametrize("roll", range(4))
@pytest.mark.parametrize(("key", "w", "ticket"), [("sub.platform", 1, []), ("sub.train", 2, [50])])
def test_the_loot_follows_the_menu_options_formula(roll, key, w, ticket):
    """:18045 ``onint(rnd(1)*4)-(w=2)-(la<>9)goto...``: C64 true is -1, so the index is
    the roll +1 in the subway (la=8) and +1 more on the train (w=2). The platform gives
    items 1-4, the train 2-5; the handbag (index 0) is only the railway station's."""
    answers = (True, 1) if w == 2 else (1,)
    result, source = _run(key, _state(), answers=answers, draws=(_NO_MANUAL, _FREE, roll))
    item, cash = _LOOT[roll + 1 + (w == 2)]
    expected = [MoneyChange(-p) for p in ticket] + [_SCORE]
    if cash:
        expected.append(MoneyChange(cash))
    assert result.effects == expected
    assert source.message_keys()[-1] == f"locations.sub.loot_{item}"
    assert result.state.players[0].ka == 10_000 - sum(ticket) + cash


def test_the_body_off_the_subway_reads_la_9_as_the_railway_station():
    """``-(la<>9)`` is 0 at the Bahnhof (``:19050`` jumps in with ``w=1``): a roll of 0
    is the handbag, index 0 (:18046)."""
    result, source = _run(
        "sub.platform", _state(last_la=9), answers=(1,), draws=(_NO_MANUAL, _FREE, 0)
    )
    assert result.effects == [_SCORE]
    assert source.message_keys()[-1] == "locations.sub.loot_handbag"


# --------------------------------------------------------------------------- #
# The gangster picker — :1130-1155                                             #
# --------------------------------------------------------------------------- #
def test_the_picker_with_an_empty_gang_returns():
    """:1130 ``ifgz(sp)=0theny=0:return`` and :18035 returns: no prompt, no score, no
    draw. On the train the ticket is already gone."""
    result, source = _run("sub.platform", _state(roster=()))
    assert result.effects == []
    assert _asked(source, _PICK) == []
    result, _ = _run("sub.train", _state(roster=()), answers=(True,))
    assert result.effects == [MoneyChange(-50)]


def test_a_number_above_the_gang_is_asked_again():
    """:1145 ``ify>gz(sp)thenprint"{up}{up}";:goto1145``: 3 of 2 is asked again, and the
    second gangster's intelligence (11) is the one the catch roll reads."""
    second = Gangster(name="b", energie=40, kraft=30, intelligenz=11, brutalitaet=30)
    rng = StubRng(_NO_MANUAL, _FREE, 0)
    source = scripted(3, 2)
    run_pure(HANDLERS["sub.platform"], source, state=_state(roster=(_THIEF, second)), rng=rng)
    assert len([i for i in source.seen if isinstance(i, PromptInt) and i.key == _PICK]) == 2
    assert rng.calls[1] == ("range", 11)

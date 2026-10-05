"""Tests for the ban (Bank/Postamt) handlers — ports ``mf-prg.bas:20000-20060``.

::

    20003 ifra(sp)<3thenprint"werde erst '"ra$(3)"'!":goto1100
    20004 ifll(sp)=20*la+lngoto17008
    20005 onwgoto20009,20100
    20009 ifgz(sp)=1thenprint"du brauchst einen begleiter!":goto1100
    20010 ifint(rnd(1)*3)=0goto20050
    20011-20012 leider hast du die drei wachmaenner am eingang uebersehen...:gz(0)=3-(ln=1)
    20015 bn$(0)="wachmaenner":e=30:w=6:kf$="kb":gosub5000:ifs=2goto26020
    20050 p=int(rnd(1)*3000)+4000-500*(la=10andln=1):x=tp(sp)
    20051 if(x=1andla=9)or(x=2andla=10andln=2)or(x=3andla=13)thentp(sp)=0:p=p+3000
    20055-20060 du hast es geschafft! deine beute betraegt p $!:ka(sp)=ka(sp)+p:x=4:gosub1160

    20100 a=sp:b=1:gosub1350:ifin>=40andkr>=15andbt>=20goto20102
    20101 print"{clr}{down}{gry2}du musst noch trainieren!":goto1100
    20104 print"...wer soll den kasten knacken:":gosub1130:ify=0thenreturn
    20110 sysbl,"trs1":pokesi+24,15:fori=0to2:rd(i)=1+i:cd(i)=int(rnd(1)*10):next
    20111 y=20+int(in/10)+3*(ln=1)+s9(sp):s9(sp)=s9(sp)-1:ifs9(sp)<0thens9(sp)=0
    20115 getx$:x=asc(x$+chr$(0)):ifx<133orx>135goto20115
    20116 x=x-133:rd(x)=rd(x)+1:ifrd(x)=10thenrd(x)=0
    20125 ifint(rnd(1)*(in/8))=0orrd(x)<>cd(x)thensysso,7:goto20135
    20130 sysso,8:fori=0to2:ifrd(i)=cd(i)thennext:gosub1190:goto20150
    20135 y=y-1:ify>0goto20115
    20141-20142 'teufel...! da ist was schiefgegangen! es kommt jemand!':kf$="kb":goto26000
    20150 sysbl,"trs2":poke198,0:wait198,1:poke198,0:x=-1:gosub1160:goto20050

Through ``run_pure`` with the strict ``StubRng``; every refusal leaves the state as it
was.
"""

from __future__ import annotations

import sys
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
from data.game_configs.mafia_1920s.setup import load_combat_backdrop
from data.game_configs.mafia_1920s.state import SCHEMA, tip_target, values_of
from engine.combat import CombatResult
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, SetMovementPoints, Teleport
from engine.interactions import (
    CANCEL,
    Acknowledge,
    CombatScreen,
    Ctx,
    PromptChoice,
    PromptInt,
    ShowMessage,
    StartCombat,
)
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from engine.turns import KEY_WAIT_SCREEN
from tests.helpers import StubRng, run_pure, scripted

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)
_PARAMS = {**load_config(_CONFIG_DIR / "config.yaml")["formula_params"], "score_mult": 1.0}

_LA = 10  # ban's location id (:3105)
_SGL_LA = 7
_SURRENDER_FIGHT = ("surrender", None)
#: The capture menu's keys (``:26022-26023``, 0-based here).
_BRIBE, _SURRENDER = 0, 2

_G = Gangster(name="alcapone", energie=40, kraft=30, intelligenz=40, brutalitaet=30)
_GANG2 = (_G, _G)


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


def _state(
    *, ln: int = 3, tip: int = 0, previous=(0, 0), safe_skill: int = 0, **fields
) -> GameState:
    fields.setdefault("ka", 10_000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("ms", 20)
    fields.setdefault("rank", 3)
    fields.setdefault("roster", _GANG2)
    player = Player(
        name="alcapone",
        values={
            **SCHEMA.player_defaults(),
            **values_of(tip_target=tip, safe_skill=safe_skill),
        },
        last_la=_LA,
        last_location=ln,
        previous_tile=previous,
        **fields,
    )
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS, house_rules=_rules()),
    )


def _run(key: str, state: GameState, answers=(), draws=()):
    rng = StubRng(*draws)
    source = scripted(*answers)
    result = run_pure(HANDLERS[f"ban.{key}"], source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source, rng


def _keys(source) -> list[str]:
    """The message keys shown, without the fight's own outcome screen (``:30500``)."""
    return [k for k in source.message_keys() if not k.startswith("combat.")]


def _score(x: int) -> ScoreAndRank:
    return ScoreAndRank(amount=x, rank_divisor=11.1)


def _fight_of(state: GameState, roll: int = 1):
    """The ``StartCombat`` the hold-up yields for ``state`` on the given fight roll
    (line 20010), or ``None`` when it pays without one."""
    gen = HANDLERS["ban.holdup"](Ctx(state=state, rng=StubRng(roll, 0)))
    try:
        interaction = next(gen)
        while not isinstance(interaction, StartCombat):
            interaction = gen.send(None)
    except StopIteration:
        return None
    finally:
        gen.close()
    return interaction


def _fights(monkeypatch, module: str, *, winner: int):
    """Stand in for the fights ``handlers/<module>.py`` starts, each ending with
    ``winner``. Returns the encounters fought, with their overrides."""
    fought = []

    def fight(ctx, encounter, **kwargs):
        fought.append((encounter.key, kwargs))
        return CombatResult(winner=winner, losses=(0, 3))
        yield  # a generator, as run_encounter is

    package = HANDLERS["ban.holdup"].__module__.rsplit(".", 1)[0]
    monkeypatch.setattr(sys.modules[f"{package}.{module}"], "run_encounter", fight)
    return fought


# --------------------------------------------------------------------------- #
# The prologue — :20003-20004, both options                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["holdup", "safe"])
@pytest.mark.parametrize("rank", [1, 2])
def test_below_rank_3_the_bank_names_the_rank_even_on_a_revisit(key, rank):
    """:20003 comes before the trap (:20004): a revisit below rank 3 shows the rank
    refusal with ``ra$(3)``, not the police, and changes nothing."""
    st = _state(rank=rank, ln=2, previous=(_LA, 2))
    result, source, _ = _run(key, st)
    assert result.effects == []
    assert result.state == st
    (msg,) = source.messages()
    assert (msg.key, msg.params) == ("locations.ban.rank_too_low", {"rank": "kleiner fisch"})


@pytest.mark.parametrize("key", ["holdup", "safe"])
def test_a_revisit_of_the_same_bank_tile_meets_the_police_on_ks(key):
    """:20004 ``goto17008``: the shop's text, then the police fight on "ks" (:17009
    ``kf$="ks":goto26000``), surrendered, then the trial (rank 3: 2 months)."""
    st = _state(ln=2, previous=(_LA, 2))
    # the squad's rank roll and energy roll (:26000-26010), the :26021 roll
    result, source, _ = _run(key, st, answers=(_SURRENDER_FIGHT, _SURRENDER), draws=(0, 0, 0))
    keys = _keys(source)
    assert keys[0] == "locations.sgl.police_waiting"
    assert "police.caught" in keys
    assert result.effects == [
        TipClear(),
        _score(2),
        Jail(months=2),
        SetMovementPoints(0),
        JobClear(),
        _score(-10),
        Teleport(911),
    ]


@pytest.mark.parametrize("key", ["holdup", "safe"])
def test_the_trap_fights_the_police_on_ks(monkeypatch, key):
    """:17009 ``kf$="ks":goto26000``: the police fight's backdrop is the shop's "ks"."""
    fought = _fights(monkeypatch, "police", winner=1)
    result, _, _ = _run(key, _state(ln=2, previous=(_LA, 2)), draws=(0, 0))
    assert [(k, kw["grid"]) for k, kw in fought] == [("police_fight", "ks")]
    assert result.effects == [_score(2)]  # :26015, the police fought off


def test_the_trap_reads_the_tile_not_the_location():
    """``20*la+ln`` is the tile: another bank tile, or tile 2 of the shop, is no trap.
    The boss alone is refused (:20009), which draws nothing."""
    for previous in ((_LA, 3), (_SGL_LA, 2), (0, 0)):
        st = _state(ln=2, previous=previous, roster=(_G,))
        result, source, _ = _run("holdup", st)
        assert source.message_keys() == ["locations.ban.alone"], previous
        assert result.effects == [] and result.state == st


# --------------------------------------------------------------------------- #
# The hold-up's refusal and the fight chance — :20009-20015                    #
# --------------------------------------------------------------------------- #
def test_the_boss_alone_needs_a_companion():
    """:20009 ``ifgz(sp)=1thenprint"du brauchst einen begleiter!"``."""
    st = _state(roster=(_G,))
    result, source, rng = _run("holdup", st)
    assert result.effects == [] and result.state == st
    assert source.message_keys() == ["locations.ban.alone"]
    assert rng.calls == []


def test_an_empty_gang_leaves_quietly():
    """The source's gang is never empty (:4651 sets gz to 1); a port state without a
    boss leaves with nothing shown or changed."""
    st = _state(roster=())
    result, source, _ = _run("holdup", st)
    assert result.effects == [] and result.state == st
    assert source.seen == []


@pytest.mark.parametrize(("roll", "fights"), [(0, False), (1, True), (2, True)])
def test_the_guards_fight_two_times_in_three(roll, fights):
    """:20010 ``ifint(rnd(1)*3)=0goto20050``: roll 0 of 3 skips the fight."""
    assert (_fight_of(_state(), roll) is not None) == fights


@pytest.mark.parametrize(("roll", "p"), [(0, 4000), (1234, 5234), (2999, 6999)])
def test_the_no_fight_third_pays_4000_to_6999_and_scores_4(roll, p):
    """:20010 roll 0, then :20050 ``p=int(rnd(1)*3000)+4000`` on tile 3, :20060
    ``ka(sp)=ka(sp)+p:x=4:gosub1160``. No guards' text, no fight."""
    st = _state()
    result, source, rng = _run("holdup", st, draws=(0, roll))
    assert rng.calls == [("range", 3), ("range", 3000)]
    assert result.effects == [MoneyChange(p), _score(4)]
    assert source.message_keys() == ["locations.ban.loot"]
    assert source.messages()[0].params == {"p": p}
    player = result.state.players[0]
    assert (player.ka, player.gf) == (10_000 + p, 54.0)


# --------------------------------------------------------------------------- #
# The tiles — :20012 gz(0)=3-(ln=1), :20050 +500 at ln=1, :20051 tip 2 at ln=2   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("ln", "men"), [(1, 4), (2, 3), (3, 3), (4, 3), (5, 3)])
def test_the_guards_are_wachmaenner_with_gewehre_on_kb(ln, men):
    """:20015 ``bn$(0)="wachmaenner":e=30:w=6:kf$="kb"``, ``gz(0)=3-(ln=1)``."""
    start = _fight_of(_state(ln=ln))
    assert start is not None
    scenario = start.scenario
    assert scenario is not None and scenario.sides is not None
    assert [(f.name, f.weapon, f.vitality) for f in scenario.sides[1]] == [
        ("wachmaenner", 6, 30)
    ] * men
    assert scenario.grid == load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / "kb.yaml")


def test_tile_1_sends_four_guards_while_the_text_says_three():
    """House rule ``bank_guards_text_says_three``: :20011 prints "drei" on every tile,
    and :20012 ``gz(0)=3-(ln=1)`` sends 4 on tile 1."""
    st = _state(ln=1)
    gen = HANDLERS["ban.holdup"](Ctx(state=st, rng=StubRng(1)))
    shown = next(gen)
    assert shown.key == "locations.ban.guards"
    assert gen.send(None).key == KEY_WAIT_SCREEN  # :20012 ``gosub1100``
    start = gen.send(None)
    gen.close()
    assert isinstance(start, StartCombat)
    assert start.scenario is not None and start.scenario.sides is not None
    assert len(start.scenario.sides[1]) == 4


@pytest.mark.parametrize(("ln", "p"), [(1, 5734), (2, 5234), (3, 5234), (4, 5234), (5, 5234)])
def test_tile_1_pays_500_more(ln, p):
    """:20050 ``-500*(la=10andln=1)``: C64 true is -1, so +500 on tile 1 only."""
    result, _, _ = _run("holdup", _state(ln=ln), draws=(0, 1234))
    assert result.effects == [MoneyChange(p), _score(4)]


@pytest.mark.parametrize(
    ("ln", "tip", "cashes"),
    [(2, 2, True), (1, 2, False), (3, 2, False), (4, 2, False), (5, 2, False)]
    + [(2, 0, False), (2, 1, False), (2, 3, False), (2, 4, False), (2, 5, False)],
)
def test_the_bank_tip_pays_3000_on_tile_2_only(ln, tip, cashes):
    """:20051 ``(x=2andla=10andln=2)thentp(sp)=0:p=p+3000``: tip 2 on tile 2 is cashed
    in; on any other bank tile, or another tip, the tip is kept and adds nothing."""
    result, source, _ = _run("holdup", _state(ln=ln, tip=tip), draws=(0, 1234))
    p = 1234 + 4000 + (500 if ln == 1 else 0) + (3000 if cashes else 0)
    head = [TipClear()] if cashes else []
    assert result.effects == [*head, MoneyChange(p), _score(4)]
    assert source.messages()[-1].params == {"p": p}
    assert tip_target(result.state.players[0]) == (0 if cashes else tip)


def test_a_won_fight_pays_the_same_payout(monkeypatch):
    """:20015 ``s=1`` falls through to :20050: the tile 2 tip still pays."""
    fought = _fights(monkeypatch, "ban", winner=1)
    result, source, rng = _run("holdup", _state(ln=2, tip=2), draws=(1, 2999))
    assert fought == [("ban_guards", {"variant": 0})]
    assert rng.calls == [("range", 3), ("range", 3000)]
    assert result.effects == [TipClear(), MoneyChange(9999), _score(4)]
    assert _keys(source) == ["locations.ban.guards", "locations.ban.loot"]


# --------------------------------------------------------------------------- #
# A lost fight — :20015 ifs=2goto26020                                         #
# --------------------------------------------------------------------------- #
def test_a_lost_hold_up_fight_leads_to_capture_without_a_police_fight(monkeypatch):
    """``goto26020``: the arrest menu, surrendered: the trial (rank 3, 2 months), and
    no loot. The tip is cleared by the trial (:26045)."""
    guards = _fights(monkeypatch, "ban", winner=2)
    police = _fights(monkeypatch, "police", winner=1)
    st = _state(ln=2, tip=2)
    result, source, _ = _run("holdup", st, answers=(_SURRENDER,), draws=(1, 0))
    assert [k for k, _ in guards] == ["ban_guards"]
    assert police == []
    assert result.effects == [
        TipClear(),
        _score(2),
        Jail(months=2),
        SetMovementPoints(0),
        JobClear(),
        _score(-10),
        Teleport(911),
    ]
    keys = _keys(source)
    assert keys[:2] == ["locations.ban.guards", "police.caught"]
    assert "locations.ban.loot" not in keys


@pytest.mark.parametrize(("ka", "paid"), [(2000, True), (1999, False)])
def test_the_capture_sees_the_cash_the_hold_up_left(ka, paid):
    """Nothing is taken before :20015's fight, so the bribe (:26035 ``p=500+500*ra``,
    2000 at rank 3) is checked against the cash as it was (:26037 ``ifka(sp)<p``)."""
    st = _state(ka=ka)
    # :20010's fight roll, :26021's roll, then (paid) the :26038 roll: let go
    draws = (1, 0, 1) if paid else (1, 0)
    answers = (_SURRENDER_FIGHT, _BRIBE, True)
    result, source, _ = _run("holdup", st, answers=answers, draws=draws)
    if paid:
        assert result.effects == [MoneyChange(-2000)]
        assert _keys(source)[-1] == "police.let_go"
    else:
        assert "system.not_enough_money" in _keys(source)
        assert result.effects[-1] == Teleport(911)
        assert not any(isinstance(e, MoneyChange) for e in result.effects)


# --------------------------------------------------------------------------- #
# The night safe-crack — :20100-20150                                          #
# --------------------------------------------------------------------------- #
#: A boss exactly at the gate (:20100 ``in>=40andkr>=15andbt>=20``).
_BOSS = Gangster(name="boss", energie=40, kraft=15, intelligenz=40, brutalitaet=20)
#: The code draws (:20110 ``cd(i)=int(rnd(1)*10)``): one press of F5 (dial 3, 3 -> 4)
#: matches all three dials, which start at 1, 2, 3.
_CODE = (1, 2, 4)
_F1, _F5 = 0, 2
#: The :20125 roll ``int(rnd(1)*(in/8))``, drawn as ``range(in)``: below 8 is a slip.
_SLIP, _NO_SLIP = 0, 8
_SAFE = "locations.ban.safe_"


def _safe_messages(source) -> list[tuple[str, dict]]:
    """The minigame's screens: the dials, and each press's feedback."""
    return [
        (m.key.removeprefix(_SAFE), m.params)
        for m in source.messages()
        if m.key in (f"{_SAFE}dials", f"{_SAFE}slip", f"{_SAFE}click")
    ]


def _dials(d1: int, d2: int, d3: int) -> dict:
    return {"d1": d1, "d2": d2, "d3": d3}


def _dial_prompts(source) -> list[PromptChoice]:
    return [i for i in source.seen if isinstance(i, PromptChoice)]


@pytest.mark.parametrize("stat", ["intelligenz", "kraft", "brutalitaet"])
def test_a_boss_short_of_any_safe_stat_must_train(stat):
    """:20100 tests the boss (``b=1``) on all three stats; :20101 "du musst noch
    trainieren!". Nothing is drawn, nobody is picked, nothing changes."""
    stats = {"kraft": 15, "intelligenz": 40, "brutalitaet": 20}
    stats[stat] -= 1
    boss = Gangster(name="boss", energie=40, **stats)
    ace = Gangster(name="ace", energie=40, kraft=99, intelligenz=99, brutalitaet=99)
    st = _state(roster=(boss, ace))
    result, source, rng = _run("safe", st)
    assert source.message_keys() == ["locations.ban.safe_untrained"]
    assert result.effects == [] and result.state == st
    assert rng.calls == []


def test_the_safe_crack_gate_reads_the_boss_and_the_minigame_the_cracker(monkeypatch):
    """House rule ``safe_gate_checks_the_boss``: a trained boss lets a cracker of
    intelligence 7 in (:20104 picks gangster 2), whose ``in`` sets the tries (:20111,
    20 + int(7/10) = 20) and always slips (:20125, int(rnd(1)*7/8) is 0): 20 presses,
    the matching one included, sound wrong, and the crack fails."""
    _fights(monkeypatch, "police", winner=1)
    weak = Gangster(name="weak", energie=40, kraft=99, intelligenz=7, brutalitaet=99)
    st = _state(roster=(_BOSS, weak))
    presses = (_F5,) * 20
    # the code, a slip roll per press, then the police squad's two rolls (:26000-26010)
    draws = (*_CODE, *(6,) * 20, 0, 0)
    result, source, rng = _run("safe", st, answers=(2, *presses), draws=draws)
    assert rng.calls[3:23] == [("range", 7)] * 20
    assert len(_dial_prompts(source)) == 20
    assert [k for k, _ in _safe_messages(source)] == ["dials", *["slip"] * 20]
    assert "locations.ban.safe_failed" in source.message_keys()
    assert result.effects == [SafeSkillSet(0), _score(2)]  # :26015, the police fought off


@pytest.mark.parametrize(
    ("intelligenz", "ln", "bonus", "tries"),
    [
        (40, 3, 0, 24),
        (49, 2, 0, 24),
        (50, 3, 0, 25),
        (40, 1, 0, 21),
        (99, 1, 5, 31),
        (40, 3, 1, 25),
    ],
)
def test_the_tries_are_20_plus_a_tenth_of_in_less_3_on_tile_1_plus_the_manual(
    monkeypatch, intelligenz, ln, bonus, tries
):
    """:20111 ``y=20+int(in/10)+3*(ln=1)+s9(sp)`` (C64 true is -1), then
    ``s9(sp)=s9(sp)-1:ifs9(sp)<0thens9(sp)=0``: the manual's bonus wears off by one per
    attempt. Turning only the first dial never opens a code of 0, 0, 0, clicks or not
    (:20130 checks all three), so every try is spent."""
    _fights(monkeypatch, "police", winner=1)
    cracker = Gangster(name="c", energie=40, kraft=15, intelligenz=intelligenz, brutalitaet=20)
    st = _state(ln=ln, safe_skill=bonus, roster=(cracker,))
    draws = (0, 0, 0, *(_NO_SLIP,) * tries, 0, 0)
    result, source, _ = _run("safe", st, answers=(1, *(_F1,) * tries), draws=draws)
    assert len(_dial_prompts(source)) == tries
    assert result.effects[0] == SafeSkillSet(max(bonus - 1, 0))
    feedback = [k for k, _ in _safe_messages(source)][1:]
    # dial 1 runs 2, 3, ..., 9, 0: the click sounds on the 9th press of each turn
    assert [i for i, k in enumerate(feedback, start=1) if k == "click"] == list(
        range(9, tries + 1, 10)
    )


def test_no_press_reveals_a_match_before_the_end():
    """:20125: a slip sounds as a wrong digit does, so the press that sets the last
    dial right can sound wrong, and the safe stays shut (:20130 checks only on a
    click). The feedback is the dials on screen and the sound; the code is never
    shown. The dials can even start on the code (:20110 ``rd(i)=1+i``) and nothing
    opens until a press clicks."""
    st = _state(roster=(_BOSS,))
    # press F5 eleven times: 4 (right, slips), 5..9, 0..3 (wrong), 4 (right, clicks)
    draws = (*_CODE, _SLIP, *(_NO_SLIP,) * 10, 1234)
    result, source, _ = _run("safe", st, answers=(1, *(_F5,) * 11, None), draws=draws)
    shown = _safe_messages(source)
    assert shown[0] == ("dials", _dials(1, 2, 3))
    assert shown[1] == ("slip", _dials(1, 2, 4))  # the right digit, sounding wrong
    assert [k for k, _ in shown[2:11]] == ["slip"] * 9
    assert shown[11] == ("click", _dials(1, 2, 4))
    assert len(shown) == 12
    assert all(set(params) == {"d1", "d2", "d3"} for _, params in shown)
    assert source.message_keys()[-1] == "locations.ban.loot"
    assert result.effects == [SafeSkillSet(0), _score(-1), MoneyChange(5234), _score(4)]

    # Dials that start on the code: the first press moves one off it, and the safe
    # opens only when a full turn of dial 3 brings it back with a click.
    draws = (1, 2, 3, *(_NO_SLIP,) * 10, 1234)
    result, source, _ = _run("safe", st, answers=(1, *(_F5,) * 10, None), draws=draws)
    shown = _safe_messages(source)
    assert shown[0] == ("dials", _dials(1, 2, 3))
    assert [k for k, _ in shown[1:]] == ["slip"] * 9 + ["click"]
    assert shown[-1] == ("click", _dials(1, 2, 3))


def test_aborting_or_a_wrong_input_mid_crack_follows_the_source():
    """:20115 ``ifx<133orx>135goto20115``: the minigame takes only F1, F3, F5; any other
    key, a cancel included, is ignored (the source has no way out once the dials
    turn). Before it, the picker's 0 leaves (:20104 ``ify=0thenreturn``) with nothing
    drawn or changed, as a cancel there does."""
    st = _state(roster=(_BOSS,))
    result, source, rng = _run("safe", st, answers=(0,))
    assert result.effects == [] and result.state == st and rng.calls == []
    assert source.message_keys()[0] == "locations.ban.safe_who"
    assert not _dial_prompts(source)
    picker = [i for i in source.seen if isinstance(i, PromptInt)]
    assert len(picker) == 1 and picker[0].cancellable

    result, _, rng = _run("safe", st, answers=(CANCEL,))
    assert result.status == "cancelled" and result.effects == [] and rng.calls == []

    answers = (1, CANCEL, 3, -1, "f5", None, _F5, None)
    result, source, _ = _run("safe", st, answers=answers, draws=(*_CODE, _NO_SLIP, 1234))
    prompts = _dial_prompts(source)
    assert len(prompts) == 6 and not any(p.cancellable for p in prompts)
    assert [k for k, _ in _safe_messages(source)] == ["dials", "click"]
    assert result.effects == [SafeSkillSet(0), _score(-1), MoneyChange(5234), _score(4)]


@pytest.mark.parametrize(("ln", "door"), [(1, 95), (2, 437), (3, 442), (4, 657), (5, 865)])
def test_a_failed_crack_reaches_the_police_fight_on_kb(monkeypatch, ln, door):
    """:20140-20142 "'teufel...! da ist was schiefgegangen! es kommt jemand!'", then
    ``kf$="kb":goto26000``. Option 2 sets no ``p``, so capture gets the map step's
    (:2030 ``p=br+po(sp)+x``): 52224 plus the tile's door cell."""
    module = sys.modules[HANDLERS["ban.safe"].__module__]
    entered = []

    def police_fight(ctx, arrest, *, grid=None):
        entered.append((arrest, grid))
        return "fought_off"
        yield  # a generator, as police_fight is

    monkeypatch.setattr(module, "police_fight", police_fight)
    weak = Gangster(name="weak", energie=40, kraft=15, intelligenz=0, brutalitaet=20)
    st = _state(ln=ln, roster=(_BOSS, weak))
    tries = 20 - (3 if ln == 1 else 0)
    draws = (*_CODE, *(0,) * tries)
    result, source, _ = _run("safe", st, answers=(2, *(_F5,) * tries), draws=draws)
    assert source.message_keys()[-1] == "locations.ban.safe_failed"
    assert [(a.p, a.cash, a.gang_size, grid) for a, grid in entered] == [
        (52224 + door, None, None, "kb")
    ]
    assert result.effects == [SafeSkillSet(0)]


def test_a_failed_crack_fights_the_police_on_kb(monkeypatch):
    """The real :26000 police fight runs on "kb" (:20142 ``kf$="kb"``)."""
    fought = _fights(monkeypatch, "police", winner=1)
    weak = Gangster(name="weak", energie=40, kraft=15, intelligenz=0, brutalitaet=20)
    st = _state(roster=(_BOSS, weak))
    draws = (*_CODE, *(0,) * 20, 0, 0)
    result, _, _ = _run("safe", st, answers=(2, *(_F5,) * 20), draws=draws)
    assert [(k, kw["grid"]) for k, kw in fought] == [("police_fight", "kb")]
    assert result.effects == [SafeSkillSet(0), _score(2)]


@pytest.mark.parametrize(
    ("gf", "after"),
    [(50.0, 53.0), (0.0, 4.0), (0.5, 4.0), (99.5, 100.0), (100.0, 100.0), (98.0, 100.0)],
)
def test_the_score_clamp_changes_the_net_as_the_two_effect_order_predicts(gf, after):
    """:20150 ``x=-1:gosub1160`` then :20060 ``x=4:gosub1160``, each clamped to 0..100
    (:1160-1161): from 0 the dip is lost and the net is +4, not +3; near 100 the
    payout's +4 is cut."""
    st = _state(gf=gf, roster=(_BOSS,))
    result, _, _ = _run("safe", st, answers=(1, _F5, None), draws=(*_CODE, _NO_SLIP, 1234))
    assert result.effects == [SafeSkillSet(0), _score(-1), MoneyChange(5234), _score(4)]
    assert result.state.players[0].gf == after


def test_a_cracked_safe_shows_the_open_safe_and_waits_for_a_key():
    """:20150 ``sysbl,"trs2":poke198,0:wait198,1``: the opened safe, then a key, then the loot."""
    st = _state(ln=1, tip=0, roster=(_BOSS,))
    result, source, _ = _run("safe", st, answers=(1, _F5, None), draws=(*_CODE, _NO_SLIP, 1234))
    shown = [i for i in source.seen if isinstance(i, (Acknowledge, ShowMessage))]
    keys = [i.key for i in shown]
    opened = keys.index("locations.ban.safe_opened")
    assert isinstance(shown[opened], Acknowledge)
    assert keys.index(f"{_SAFE}click") < opened < keys.index("locations.ban.loot")
    assert result.effects == [SafeSkillSet(0), _score(-1), MoneyChange(5734), _score(4)]


def test_a_cracked_safe_pays_the_banks_tile_terms():
    """:20150 ``goto20050``: the bank's payout, +500 on tile 1, tip 2 on tile 2."""
    for ln, tip, p, head in ((1, 0, 5734, []), (2, 2, 8234, [TipClear()])):
        st = _state(ln=ln, tip=tip, roster=(_BOSS,))
        result, _, _ = _run("safe", st, answers=(1, _F5, None), draws=(*_CODE, _NO_SLIP, 1234))
        assert result.effects == [SafeSkillSet(0), _score(-1), *head, MoneyChange(p), _score(4)]


# --------------------------------------------------------------------------- #
# The :1100 key wait at each exit (#160)                                       #
# --------------------------------------------------------------------------- #
_WEAK = Gangster(name="weak", energie=40, kraft=15, intelligenz=0, brutalitaet=20)

_EXITS = [
    # id, option, state fields, answers, rng draws, key waits, ends in the wait
    # :20003 ifra(sp)<3thenprint"werde erst '"ra$(3)"'!":goto1100
    ("20003-rank", "holdup", {"rank": 2}, (), (), 1, True),
    # :20009 ifgz(sp)=1thenprint"du brauchst einen begleiter!":goto1100
    ("20009-alone", "holdup", {"roster": (_G,)}, (), (), 1, True),
    # :20010 ...goto20050 -> :20060 ...:x=4:gosub1160:goto1100
    ("20060-no-guards", "holdup", {}, (), (0, 1234), 1, True),
    # :20012 gosub1100, the fight's :30520 wait, :20015 ifs=2goto26020 -> :26080's
    ("20015-guards-won", "holdup", {}, (_SURRENDER_FIGHT, _SURRENDER), (1, 0), 3, True),
    # :20101 print"{clr}{down}{gry2}du musst noch trainieren!":goto1100
    ("20101-untrained", "safe", {"roster": (_WEAK,)}, (), (), 1, True),
    # :20104 ...gosub1130:ify=0thenreturn
    ("20104-pick-0", "safe", {"roster": (_BOSS,)}, (0,), (), 0, False),
    # :20107 gosub1100; :20150's own wait198 (the opened safe, not a :1100 wait);
    # :20060's goto1100
    (
        "20150-cracked",
        "safe",
        {"ln": 1, "roster": (_BOSS,)},
        (1, _F5, None),
        (*_CODE, _NO_SLIP, 1234),
        2,
        True,
    ),
    # :20107's wait, :20142 gosub1100, the police fight's wait, :26080's
    (
        "20142-failed",
        "safe",
        {"roster": (_BOSS, _WEAK)},
        (2, *(_F5,) * 20, _SURRENDER_FIGHT, _SURRENDER),
        (*_CODE, *(0,) * 20, 0, 0, 0),
        4,
        True,
    ),
]


@pytest.mark.parametrize(
    ("key", "fields", "answers", "draws", "waits", "ends"),
    [case[1:] for case in _EXITS],
    ids=[case[0] for case in _EXITS],
)
def test_each_exit_waits_for_a_key_where_the_source_does(key, fields, answers, draws, waits, ends):
    _, source, _ = _run(key, _state(**fields), answers=answers, draws=draws)
    assert source.key_waits() == waits
    assert source.ends_in_key_wait() == ends


def test_the_safe_waits_before_the_dials_and_before_the_police():
    """:20107 ``...mit f1, f3 und f5!)":gosub1100`` before the dials (:20110), and
    :20142 ``...es kommt jemand!'":gosub1100`` before ``goto26000``."""
    st = _state(roster=(_BOSS, _WEAK))
    answers = (2, *(_F5,) * 20, _SURRENDER_FIGHT, _SURRENDER)
    _, source, _ = _run("safe", st, answers=answers, draws=(*_CODE, *(0,) * 20, 0, 0, 0))
    keys = [i.key for i in source.seen if isinstance(i, (Acknowledge, ShowMessage))]
    stethoscope = keys.index("locations.ban.safe_stethoscope")
    assert keys[stethoscope + 1 : stethoscope + 3] == [KEY_WAIT_SCREEN, f"{_SAFE}dials"]
    failed = keys.index("locations.ban.safe_failed")
    assert keys[failed + 1] == KEY_WAIT_SCREEN
    fight = next(i for i, x in enumerate(source.seen) if isinstance(x, CombatScreen))
    assert getattr(source.seen[fight - 1], "key", None) == KEY_WAIT_SCREEN

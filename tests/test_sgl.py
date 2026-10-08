"""Tests for the sgl (Einfacher Laden) handlers — ports ``mf-prg.bas:17000-17592``.

::

    2055 gosub3000:ll(sp)=20*la+ln
    3045 ifw=awthenms=ms-5:return
    17005 ifra(sp)>1goto17007
    17006 print"'verschwinde, du milchgesicht!'":goto1100
    17007 ifll(sp)<>20*la+lngoto17010
    17009 print"{down}poliyei!":gosub1100:kf$="ks":goto26000
    17016 ifbt<30thenprint"du schindest keinen eindruck...":print:goto17020
    17017 ifln=1orln=7orln=9goto17500
    17100 ifln=1orln=4orln=5goto17500
    17200 ifln=2orln=3orln=8goto17500
    17210 bn$(0)="jack's gang":gz(0)=3-2*(gz(sp)>5):w=7:e=30:kf$="ksgl":gosub5000
    17215 ifs=2thenreturn
    17300 a=sp:b=1:gosub1350:if(ln=1orln=3orln=6)andin>=30goto17500
    17500 x=2:gosub1160:ifint(rnd(1)*3)=0goto17530
    17505 p=int(rnd(1)*200)+800-300*(ln=2)-200*(ln=7)-200*(ln=9)+600*(w=2)
    17510 onwgoto17511,17515,17520,17525
    17530 p=int(rnd(1)*100)+100:print"{clr}{down}'ich habe leider nur"p"$!"
    17550 ka(sp)=ka(sp)+p: ...
    17570 ifln<>2andln<>6andln<>7andln<>8goto17575
    17573 bn$(0)="schlaeger":gz(0)=5:w=3:e=20:kf$="ksgl":gosub5000:ifs=2thenreturn
    17575 pokera,4:pokera+1,4:p=int(rnd(1)*100)+300
    17578 ka(sp)=ka(sp)+p:x=1:gosub1160:goto1100
    17580 ifln<>1andln<>4goto17590
    17587 bn$(0)="ladenbesitzer":gz(0)=1:w=7:e=30:kf$="ksgl":gosub5000
    17588 ifs=2thenreturn
    17590 p=int(rnd(1)*100)+200:pokera,4:pokera+1,4
    17592 print"{down}hatte er der tasche!":x=1:gosub1160:goto17578
    30215 r=2:w=gw(ks(s),f):ifw>3thenr=15

C64 true is -1: ``-300*(ln=2)`` adds 300, and ``+600*(w=2)`` takes 600 OFF. The rng is
the strict ``StubRng``; every refusal leaves the state as it was.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.effects import Jail, JobClear, ScoreAndRank, TipClear
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.state import SCHEMA
from engine.combat import STEP_RIGHT, CombatResult
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, SetMovementPoints, Teleport
from engine.interactions import (
    Acknowledge,
    CombatScreen,
    Confirm,
    Ctx,
    LocationMenu,
    MapMove,
    PromptChoice,
    PromptInt,
    ShowMessage,
    StartCombat,
    TurnMenu,
)
from engine.locations import HANDLERS
from engine.persistence import load_game, save_game
from engine.rng import Rng
from engine.state import Clock, Config, Fighter, GameState, Player
from engine.turns import KEY_WAIT_SCREEN, TURN_START, WALKING, TurnRunner
from tests.helpers import StubRng, is_effect, run_pure, scripted
from tests.test_turn_runner import _approach, _door

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)
_PARAMS = {**load_config(_CONFIG_DIR / "config.yaml")["formula_params"], "score_mult": 1.0}

_LA = 7  # sgl's location id (:3105)
_SURRENDER_FIGHT = ("surrender", None)
#: The capture menu's surrender key (``:26022-26023``, 0-based here).
_SURRENDER = 2
_BRIBE = 0
#: The after-payment menu (``:17550-17551``), 0-based.
_TAKE, _DEMOLISH, _KILL = 0, 1, 2

_BOSS = Gangster(name="alcapone", energie=40, kraft=30, intelligenz=40, brutalitaet=30)
#: A boss who kills a 30-energy enemy with one maschinenpistole shot (draws 1, 10, 0).
_KILLER = Gangster(name="alcapone", weapon=7, energie=40, kraft=30, intelligenz=40, brutalitaet=290)


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


def _state(*, ln: int = 1, previous=(0, 0), **fields) -> GameState:
    fields.setdefault("ka", 1000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("rank", 2)
    fields.setdefault("ms", 20)
    fields.setdefault("roster", (_BOSS,))
    player = Player(
        name="alcapone",
        values=SCHEMA.player_defaults(),
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
    result = run_pure(HANDLERS[f"sgl.{key}"], source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _keys(source) -> list[str]:
    """The message keys shown, without the fight's own outcome screen (``:30500``)."""
    return [k for k in source.message_keys() if not k.startswith("combat.")]


def _score(x: int) -> ScoreAndRank:
    return ScoreAndRank(amount=x, rank_divisor=11.1)


#: An option that pays at once on each tile (the tile table, :17017/:17100/:17200/:17300).
_PAYING = {1: "sob_story", 2: "protection", 3: "protection", 4: "sob_story", 5: "sob_story"}
_PAYING |= {6: "fake_police", 7: "threat", 8: "protection", 9: "threat"}
_W = {"threat": 1, "sob_story": 2, "protection": 3, "fake_police": 4}


# --------------------------------------------------------------------------- #
# The prologue — :17005-17009                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["threat", "sob_story", "protection", "fake_police"])
def test_a_rank_1_player_is_sent_away_even_on_a_revisit(key):
    """:17005 comes before the trap (:17007): a rank-1 revisit shows the rank refusal,
    not the police, and changes nothing."""
    st = _state(rank=1, ln=3, previous=(_LA, 3))
    result, source = _run(key, st)
    assert result.effects == []
    assert result.state == st
    assert source.message_keys() == ["locations.sgl.milksop"]


@pytest.mark.parametrize("key", ["threat", "sob_story", "protection", "fake_police"])
def test_a_revisit_of_the_same_tile_meets_the_police(key):
    """:17007-17009: the previous tile is this tile -> the police fight (:26000),
    here surrendered and then the trial (rank 2: no lawyer)."""
    st = _state(ln=3, previous=(_LA, 3))
    result, source = _run(
        key, st, answers=(_SURRENDER_FIGHT, _SURRENDER), draws=(0, 0, 0)
    )  # the squad's rank roll and energy roll (:26000-26010), the :26021 roll
    keys = source.message_keys()
    assert keys[0] == "locations.sgl.police_waiting"
    assert "police.caught" in keys
    assert result.effects[-7:] == [
        TipClear(),
        _score(2),
        Jail(months=1),
        SetMovementPoints(0),
        JobClear(),
        _score(-10),
        Teleport(911),
    ]
    assert not any(is_effect(e, MoneyChange) for e in result.effects)


def test_the_trap_reads_the_tile_not_the_location():
    """``20*la+ln`` is the tile: another sgl tile, or the same ``ln`` at another
    location, is no trap. Tile 2 refuses the sob story (:17105), which draws nothing."""
    for previous in ((_LA, 3), (1, 2), (0, 0)):
        st = _state(ln=2, previous=previous)
        result, source = _run("sob_story", st)
        assert source.message_keys() == ["locations.sgl.sob_refused"], previous
        assert result.effects == [] and result.state == st


def test_an_empty_gang_leaves_quietly():
    """The source's gang is never empty (gz(sp) only grows, and :4651 sets it to 1);
    a port state without a boss is refused with nothing shown or changed."""
    for key in ("threat", "sob_story", "protection", "fake_police"):
        st = _state(ln=1, roster=())
        result, source = _run(key, st)
        assert result.effects == [] and result.state == st
        assert source.message_keys() == []


# --------------------------------------------------------------------------- #
# The options — :17015-17306                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ln", range(1, 10))
def test_the_threat_works_on_tiles_1_7_9_with_a_brutal_boss(ln):
    """:17016-17020: brutality 30 and tile 1, 7 or 9 pay; else the police are called."""
    st = _state(ln=ln)
    if ln in (1, 7, 9):
        result, source = _run("threat", st, answers=(_TAKE,), draws=(0, 0))
        assert result.effects == [_score(2), MoneyChange(100)]
        assert "locations.sgl.calls_police" not in source.message_keys()
    else:
        result, source = _run("threat", st, answers=(_SURRENDER_FIGHT, _SURRENDER), draws=(0, 0, 0))
        assert _keys(source)[:2] == ["locations.sgl.calls_police", "police.caught"]


def test_a_boss_without_brutality_makes_no_impression_and_the_police_come():
    """:17016 ``ifbt<30`` -> the line, then :17020's police, on a paying tile too."""
    weak = replace(_BOSS, attrs={**_BOSS.attrs, "brutalitaet": 29})
    st = _state(ln=1, roster=(weak,))
    _, source = _run("threat", st, answers=(_SURRENDER_FIGHT, _SURRENDER), draws=(0, 0, 0))
    assert _keys(source)[:3] == [
        "locations.sgl.no_impression",
        "locations.sgl.calls_police",
        "police.caught",
    ]


@pytest.mark.parametrize("ln", range(1, 10))
def test_the_sob_story_works_on_tiles_1_4_5(ln):
    st = _state(ln=ln)
    if ln in (1, 4, 5):
        result, _ = _run("sob_story", st, answers=(_TAKE,), draws=(0, 0))
        assert result.effects == [_score(2), MoneyChange(100)]
    else:
        result, source = _run("sob_story", st)
        assert source.message_keys() == ["locations.sgl.sob_refused"]
        assert result.effects == [] and result.state == st


@pytest.mark.parametrize("ln", range(1, 10))
@pytest.mark.parametrize("intelligenz", [29, 30])
def test_the_fake_police_works_on_tiles_1_3_6_with_a_clever_boss(ln, intelligenz):
    boss = replace(_BOSS, attrs={**_BOSS.attrs, "intelligenz": intelligenz})
    st = _state(ln=ln, roster=(boss,))
    if ln in (1, 3, 6) and intelligenz >= 30:
        result, _ = _run("fake_police", st, answers=(_TAKE,), draws=(0, 0))
        assert result.effects == [_score(2), MoneyChange(100)]
    else:
        result, source = _run("fake_police", st)
        assert source.message_keys() == ["locations.sgl.fake_police_refused"]
        assert result.effects == [] and result.state == st


def test_the_boss_not_a_picked_gangster_is_tested():
    """:17015/:17300 ``a=sp:b=1:gosub1350``: gangster 1's stats, whoever else is there."""
    weak = replace(_BOSS, attrs={**_BOSS.attrs, "brutalitaet": 5, "intelligenz": 5})
    st = _state(ln=1, roster=(weak, _BOSS))
    _, source = _run("fake_police", st)
    assert source.message_keys() == ["locations.sgl.fake_police_refused"]


# --------------------------------------------------------------------------- #
# The payout — :17500-17551                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ln", range(1, 10))
def test_the_big_payout_and_its_reply_per_option_and_tile(ln):
    """:17505 800..999, +300 on tile 2, +200 on 7 and 9, and 600 less for ``w=2``;
    :17510's reply is the option's. The score comes first (:17500)."""
    key = _PAYING[ln]
    st = _state(ln=ln)
    result, source = _run(key, st, answers=(_TAKE,), draws=(1, 199))
    p = 999 + {2: 300, 7: 200, 9: 200}.get(ln, 0) - (600 if key == "sob_story" else 0)
    assert result.effects == [_score(2), MoneyChange(p)]
    reply = {1: "reply_pays", 2: "reply_sob", 3: "reply_enough", 4: "reply_forged"}
    (message,) = [m for m in source.messages() if m.key.startswith("locations.sgl.reply_")]
    assert message.key == f"locations.sgl.{reply[_W[key]]}"
    assert message.params == {"p": p}


@pytest.mark.parametrize("roll", [0, 1, 2])
def test_one_time_in_three_the_shopkeeper_has_only_100_to_199(roll):
    """:17500 ``ifint(rnd(1)*3)=0goto17530``: the small payment, the same reply for all."""
    st = _state(ln=2)
    result, source = _run("protection", st, answers=(_TAKE,), draws=(roll, 50))
    if roll == 0:
        assert result.effects == [_score(2), MoneyChange(150)]
        assert "locations.sgl.reply_small" in source.message_keys()
    else:
        assert result.effects == [_score(2), MoneyChange(850 + 300)]


def test_the_money_is_credited_before_the_choice_and_kept_after_a_lost_fight():
    """:17550 credits ``p`` before the menu; a lost thug fight returns (:17573) with it.
    The menu reads only 1..3 (:17555)."""
    st = _state(ln=2)
    result, source = _run("protection", st, answers=(_DEMOLISH, _SURRENDER_FIGHT), draws=(0, 0))
    assert result.effects == [_score(2), MoneyChange(100)]
    assert result.state.players[0].ka == 1100
    (menu,) = [i for i in source.seen if isinstance(i, PromptChoice)]
    assert menu.options == [
        "locations.sgl.after_take",
        "locations.sgl.after_demolish",
        "locations.sgl.after_kill",
    ]
    assert not menu.cancellable


# --------------------------------------------------------------------------- #
# Wrecking the shop and finishing the owner — :17570-17592                     #
# --------------------------------------------------------------------------- #
def _encounter_of(key: str, ln: int, choice: int):
    """The encounter the after-payment choice starts on tile ``ln``, or ``None``."""
    ctx = Ctx(state=_state(ln=ln), rng=StubRng(0, 0))
    gen = HANDLERS[f"sgl.{key}"](ctx)
    interaction = next(gen)
    try:
        while not isinstance(interaction, StartCombat):
            interaction = gen.send(choice if isinstance(interaction, PromptChoice) else None)
    except (StopIteration, AssertionError):
        return None
    finally:
        gen.close()
    return interaction.scenario


@pytest.mark.parametrize("ln", range(1, 10))
def test_the_thugs_come_only_on_tiles_2_6_7_8(ln):
    """:17570-17573: five schlaeger with a schlagkette and 20 energy."""
    scenario = _encounter_of(_PAYING[ln], ln, _DEMOLISH)
    if ln in (2, 6, 7, 8):
        assert scenario is not None and scenario.sides is not None
        assert [(f.weapon, f.vitality) for f in scenario.sides[1]] == [(3, 20)] * 5
    else:
        assert scenario is None


@pytest.mark.parametrize("ln", range(1, 10))
def test_the_owner_fights_only_on_tiles_1_and_4(ln):
    """:17580-17587: one ladenbesitzer with a maschinenpistole and 30 energy."""
    scenario = _encounter_of(_PAYING[ln], ln, _KILL)
    if ln in (1, 4):
        assert scenario is not None and scenario.sides is not None
        assert [(f.weapon, f.vitality) for f in scenario.sides[1]] == [(7, 30)]
    else:
        assert scenario is None


def test_wrecking_a_shop_without_thugs_pays_the_till_and_one_score():
    """:17575-17578: 300..399, ``ka+=p``, ``x=1``."""
    st = _state(ln=3)
    result, source = _run("protection", st, answers=(_DEMOLISH,), draws=(0, 0, 42))
    assert result.effects == [_score(2), MoneyChange(100), MoneyChange(342), _score(1)]
    (m,) = [m for m in source.messages() if m.key == "locations.sgl.demolished"]
    assert m.params == {"p": 342}


def test_finishing_an_owner_who_does_not_fight_scores_twice():
    """:17590-17592 ``x=1:gosub1160:goto17578``, then :17578's own ``x=1``: 200..299."""
    st = _state(ln=5)
    result, _ = _run("sob_story", st, answers=(_KILL,), draws=(0, 0, 99))
    assert result.effects == [
        _score(2),
        MoneyChange(100),
        _score(1),
        MoneyChange(299),
        _score(1),
    ]


def test_a_won_owner_fight_pays_his_pockets():
    """Tile 1: the owner arms himself with ``wa$(7)`` (:17586), loses one shot, and
    his 200..299 follow (:17590)."""
    st = _state(ln=1, roster=(_KILLER,))
    result, source = _run(
        "sob_story", st, answers=(_KILL, ("shoot", STEP_RIGHT)), draws=(0, 0, 1, 10, 0, 50)
    )
    (arms,) = [m for m in source.messages() if m.key == "locations.sgl.owner_arms"]
    assert arms.params == {"weapon": "maschinenpistole"}
    assert result.effects == [_score(2), MoneyChange(100), _score(1), MoneyChange(250), _score(1)]


def test_a_lost_owner_fight_returns():
    """:17588 ``ifs=2thenreturn``: the payment stays, nothing more."""
    st = _state(ln=4)
    result, source = _run("sob_story", st, answers=(_KILL, _SURRENDER_FIGHT), draws=(0, 0))
    assert result.effects == [_score(2), MoneyChange(100)]
    assert "locations.sgl.owner_dead" not in source.message_keys()
    assert "police.caught" not in source.message_keys()


# --------------------------------------------------------------------------- #
# Narben-Jack — :17205-17225                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("gang", "men"), [(1, 3), (5, 3), (6, 5), (10, 5)])
def test_jacks_gang_is_3_or_5_against_a_gang_of_more_than_5(gang, men):
    """:17210 ``gz(0)=3-2*(gz(sp)>5)``: maschinenpistole, 30 energy, jack's gang."""
    ctx = Ctx(state=_state(ln=1, roster=(_BOSS,) * gang), rng=StubRng())
    gen = HANDLERS["sgl.protection"](ctx)
    interaction = next(gen)
    while not isinstance(interaction, StartCombat):
        interaction = gen.send(None)
    gen.close()
    scenario = interaction.scenario
    assert scenario is not None and scenario.sides is not None
    assert [(f.name, f.weapon, f.vitality) for f in scenario.sides[1]] == [
        ("jack's gang", 7, 30)
    ] * men


def test_a_lost_jack_fight_returns_with_nothing():
    """:17215 ``ifs=2thenreturn``: no police, no money, no score."""
    st = _state(ln=1)
    result, source = _run("protection", st, answers=(_SURRENDER_FIGHT,))
    assert result.effects == []
    assert result.state == st
    keys = source.message_keys()
    assert keys[0] == "locations.sgl.jack_warning"
    assert "locations.sgl.jack_leaves" not in keys and "police.caught" not in keys


def _won_jack_fight(monkeypatch, shooter: Fighter | None):
    """Stand in for Jack's fight: won, with ``shooter`` firing the last shot."""
    fought = []

    def fight(ctx, encounter, **kwargs):
        fought.append(encounter.key)
        return CombatResult(winner=1, losses=(0, 3), last_shooter=shooter)
        yield  # a generator, as run_encounter is

    # The config's handlers live in the module load_game_config imported, not in a
    # plain ``import`` of the package: patch the one the handler reads.
    module = sys.modules[HANDLERS["sgl.protection"].__module__]
    monkeypatch.setattr(module, "run_encounter", fight)
    return fought


@pytest.mark.parametrize(
    ("weapon", "reply", "sob"),
    [
        (0, "reply_pays", False),
        (1, "reply_pays", False),
        (2, "reply_sob", True),
        (3, "reply_enough", False),
        (4, "reply_forged", False),
        (5, "reply_pays", False),
        (7, "reply_pays", False),
    ],
)
def test_after_a_won_jack_fight_the_killers_weapon_picks_the_reply(monkeypatch, weapon, reply, sob):
    """House rule ``jack_fight_reply_by_killing_weapon`` (faithful-only): :30215 left
    the killing shooter's weapon in ``w``, which :17505/:17510 read. A club kill (2)
    takes 600 off with the sob-story reply; outside 1..4 falls through to :17511."""
    fought = _won_jack_fight(monkeypatch, Fighter(name="alcapone", weapon=weapon))
    st = _state(ln=5)
    result, source = _run("protection", st, answers=(_TAKE,), draws=(1, 100))
    assert fought == ["sgl_jack"]
    assert result.effects == [_score(2), MoneyChange(900 - (600 if sob else 0))]
    keys = source.message_keys()
    assert keys.index("locations.sgl.jack_leaves") < keys.index(f"locations.sgl.{reply}")


def test_a_won_jack_fight_with_a_club_kill_pays_600_less():
    """The plan's scenario: a club kill after Jack's fight. ``+600*(w=2)`` is -600
    under C64 true = -1, so it pays 600 LESS than any other kill (:17505)."""
    with pytest.MonkeyPatch.context() as mp:
        _won_jack_fight(mp, Fighter(weapon=2))
        club, _ = _run("protection", _state(ln=7), answers=(_TAKE,), draws=(1, 0))
    with pytest.MonkeyPatch.context() as mp:
        _won_jack_fight(mp, Fighter(weapon=7))
        gun, _ = _run("protection", _state(ln=7), answers=(_TAKE,), draws=(1, 0))
    assert club.effects == [_score(2), MoneyChange(800 + 200 - 600)]
    assert gun.effects == [_score(2), MoneyChange(800 + 200)]


def test_a_won_jack_fight_with_no_shot_keeps_w_7(monkeypatch):
    """:17210 set ``w=7`` before the fight; with no shooter it stays (first reply)."""
    _won_jack_fight(monkeypatch, None)
    _, source = _run("protection", _state(ln=5), answers=(_TAKE,), draws=(1, 0))
    assert "locations.sgl.reply_pays" in source.message_keys()


@pytest.mark.parametrize(
    ("ln", "after", "second"), [(7, _DEMOLISH, "sgl_thugs"), (1, _KILL, "sgl_owner")]
)
def test_the_fight_after_jacks_starts_from_the_energy_jacks_fight_left(
    monkeypatch, ln, after, second
):
    """Each hit is stored in the gang's stats at once (:30260 ``gosub1350:en=en-y``,
    :30265 ``gosub1365``), so the thugs (:17573) or the owner (:17587) meet the gang as
    Jack's fight (:17210) left it. The first fight's write-back is buffered, so the
    second is built from its closing energies."""
    fought: list = []
    closing = [((0, 12), (1, 3)), ()]
    gang = (_BOSS, replace(_BOSS, name="g2", energie=20))

    def fight(ctx, encounter, **kwargs):
        fought.append((encounter.key, kwargs))
        return CombatResult(winner=1, losses=(0, 1), roster_vitality=closing.pop(0))
        yield  # a generator, as run_encounter is

    module = sys.modules[HANDLERS["sgl.protection"].__module__]
    monkeypatch.setattr(module, "run_encounter", fight)
    _run("protection", _state(ln=ln, roster=gang), answers=(after,), draws=(1, 0, 0))

    (jack, first), (key, then) = fought
    assert (jack, key) == ("sgl_jack", second)
    assert first.get("roster") is None
    assert [g.vitality for g in then["roster"]] == [12, 3]
    assert [g.name for g in then["roster"]] == ["alcapone", "g2"]


# --------------------------------------------------------------------------- #
# Through the turn runner: ll(sp) — :1012, :2055                               #
# --------------------------------------------------------------------------- #
def _walking(
    ln: int, *, rank: int = 2, ms: int = 60, state: GameState | None = None
) -> tuple[GameState, str]:
    """The one-player game on the map beside sgl's tile ``ln`` door."""
    door, la = _door("sgl", ln=ln)
    assert la == _LA
    start, into = _approach(door)
    if state is None:
        state = _CONFIG.new_game(
            seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
        )
    assert state is not None
    player = replace(state.players[0], po=start, ms=ms, rank=rank, ka=10_000)
    state = replace(state, players=(player,), clock=replace(state.clock, turn_phase=WALKING))
    return state, into


def _runner(state: GameState, rng) -> TurnRunner:
    return TurnRunner(
        state, rng, city=_CONFIG.city, shells=_CONFIG.shells, turn_menu=_CONFIG.menus["turn"]
    )


def _visit(runner: TurnRunner, gen, plan: list, *, first: bool = False):
    """Drive ``gen`` through ``plan`` (map moves, then the menu's option ids, then the
    answers to other prompts, in order); stop at the next map prompt once ``plan`` is
    spent. Returns ``(the message keys seen, gen)``."""
    keys: list[str] = []
    interaction = next(gen) if first else gen.send(plan.pop(0))
    while True:
        if isinstance(interaction, ShowMessage):
            keys.append(interaction.key)
            response = None
        elif isinstance(interaction, MapMove):
            if not plan:
                return keys
            response = plan.pop(0)
        elif isinstance(interaction, LocationMenu):
            response = interaction.options.index(plan.pop(0))
        elif isinstance(interaction, TurnMenu):
            response = "2"
        elif isinstance(interaction, CombatScreen):
            response = _SURRENDER_FIGHT
        elif isinstance(interaction, (PromptChoice, Confirm, PromptInt)):
            response = plan.pop(0)
        else:  # Heading, Acknowledge, StartCombat: nothing to answer
            response = None
        interaction = gen.send(response)


def _drive(runner: TurnRunner, plan: list) -> list[str]:
    gen = runner.run()
    keys = _visit(runner, gen, plan, first=True)
    gen.close()
    return keys


def test_entering_the_same_tile_twice_in_one_turn_meets_the_police():
    """A leave on tile 2, then the sob story on tile 2: ``ll(sp)`` is ``(7, 2)``."""
    state, into = _walking(2)
    rng = StubRng(0, 0)  # the squad's rolls (:26000-26010); the drive stops at the fight
    runner = _runner(state, rng)
    gen = runner.run()
    keys = _visit(runner, gen, [into, "leave"], first=True)
    assert keys == []
    assert runner.state.players[0].previous_tile == (_LA, 2)
    interaction = gen.send(into)
    assert isinstance(interaction, LocationMenu)
    interaction = gen.send(interaction.options.index("sob_story"))
    assert isinstance(interaction, ShowMessage)
    assert interaction.key == "locations.sgl.police_waiting"
    interaction = gen.send(None)
    assert isinstance(interaction, Acknowledge)  # :17009 ``gosub1100``
    assert interaction.key == KEY_WAIT_SCREEN
    assert isinstance(gen.send(None), CombatScreen)  # the police fight (:26000)
    gen.close()


def test_entering_a_then_b_then_a_is_safe():
    """``ll(sp)`` is one slot: the visit to tile 3 overwrites tile 2's."""
    state, into2 = _walking(2)
    runner = _runner(state, StubRng())
    _drive(runner, [into2, "leave"])
    state, into3 = _walking(3, state=runner.state)
    runner = _runner(state, StubRng())
    _drive(runner, [into3, "leave"])
    assert runner.state.players[0].previous_tile == (_LA, 3)
    state, into2 = _walking(2, state=runner.state)
    runner = _runner(state, StubRng())
    keys = _drive(runner, [into2, "sob_story"])
    assert keys == ["locations.sgl.sob_refused"]  # :17105, not the police


def test_choosing_leave_on_a_revisit_escapes_the_trap():
    """Leave is :3045, before :17000: no rank gate, no trap, just the 5 points."""
    state, into = _walking(2, ms=60)
    runner = _runner(state, StubRng())
    keys = _drive(runner, [into, "leave", into, "leave"])
    assert keys == []
    player = runner.state.players[0]
    assert player.previous_tile == (_LA, 2)
    assert player.ms == 60 - 2 * (5 + 5)  # :3045 and :2060, twice


def test_the_previous_tile_clears_at_the_next_turn_start():
    """:1012 ``ll(sp)=0``: last turn's visit to tile 2 is no trap this turn."""
    state, into = _walking(2)
    state = replace(
        state,
        players=(replace(state.players[0], previous_tile=(_LA, 2)),),
        clock=replace(state.clock, turn_phase=TURN_START),
    )
    runner = _runner(state, Rng(42))
    gen = runner.run(TURN_START)
    interaction = next(gen)
    while not (isinstance(interaction, MapMove) and interaction.outcome is None):
        if isinstance(interaction, TurnMenu):
            response = "2"
        elif isinstance(interaction, CombatScreen):
            response = _SURRENDER_FIGHT
        else:
            response = None
        interaction = gen.send(response)
    assert runner.state.players[0].previous_tile == (0, 0)
    keys = _visit(runner, gen, [into, "sob_story"])
    gen.close()
    assert keys == ["locations.sgl.sob_refused"]


def test_a_save_between_two_visits_still_springs_the_trap(tmp_path):
    """KTD-13: ``ll(sp)`` is saved, so a save and load between the visits keeps it."""
    state, into = _walking(2)
    runner = _runner(state, StubRng())
    _drive(runner, [into, "leave"])
    save = tmp_path / "sgl.jsonl"
    save_game(save, runner.state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=42)
    loaded = load_game(save, _CONFIG.registries).state
    assert loaded.players[0].previous_tile == (_LA, 2)
    runner = _runner(loaded, StubRng(0, 0))
    gen = runner.run()
    interaction = next(gen)
    assert isinstance(interaction, MapMove)
    menu = gen.send(into)
    assert isinstance(menu, LocationMenu)
    message = gen.send(menu.options.index("sob_story"))
    assert isinstance(message, ShowMessage) and message.key == "locations.sgl.police_waiting"
    gen.close()


def test_after_bribing_the_police_free_the_same_tile_springs_the_trap_again():
    """KTD-13: a capture from a location writes ``ll(sp)`` too (:2055). The police
    fight lost, the bribe paid and taken (:26038-26039), the turn goes on; the next
    entry to the same tile meets the police again."""
    state, into = _walking(2, ms=60)
    # :26000/:26010 the squad's rolls, :26021's roll, :26038's roll (1: taken).
    rng = StubRng(0, 0, 0, 1)
    runner = _runner(state, rng)
    gen = runner.run()
    keys = _visit(runner, gen, [into, "leave", into, "sob_story", _BRIBE, True], first=True)
    assert keys[0] == "locations.sgl.police_waiting"
    assert keys[-1] == "police.let_go"
    player = runner.state.players[0]
    assert player.previous_tile == (_LA, 2)
    assert player.ms > 0 and player.po != 911
    menu = gen.send(into)
    assert isinstance(menu, LocationMenu)
    message = gen.send(menu.options.index("sob_story"))
    assert isinstance(message, ShowMessage) and message.key == "locations.sgl.police_waiting"
    gen.close()


# --------------------------------------------------------------------------- #
# The :1100 key wait at each exit (#160)                                       #
# --------------------------------------------------------------------------- #
_POLICE_LOST = (_SURRENDER_FIGHT, _SURRENDER)

_EXITS = [
    # id, option, state fields, answers, rng draws, key waits, ends in the wait
    # :17006 print"'verschwinde, du milchgesicht!'":goto1100
    ("17006-milksop", "threat", {"rank": 1}, (), (), 1, True),
    # :17009 gosub1100, the police fight's :30520 wait, the capture's :26080 wait
    (
        "17009-revisit",
        "sob_story",
        {"ln": 3, "previous": (_LA, 3)},
        _POLICE_LOST,
        (0, 0, 0),
        3,
        True,
    ),
    # :17020 print"der kerl ruft die polizei!!!":gosub1100, then the same
    ("17020-police-called", "threat", {"ln": 2}, _POLICE_LOST, (0, 0, 0), 3, True),
    # :17105 print"{clr}{down}'sorge doch selbst fuer deine alte!'":goto1100
    ("17105-sob-refused", "sob_story", {"ln": 2}, (), (), 1, True),
    # :17306 print"{down}neppt ihr keinen mehr!'":goto1100
    ("17306-fake-refused", "fake_police", {"ln": 2}, (), (), 1, True),
    # :17565 return -- the money taken
    ("17565-taken", "sob_story", {"ln": 1}, (_TAKE,), (0, 0), 0, False),
    # :17572 gosub1100, the fight's wait, :17573 ifs=2thenreturn
    ("17573-thugs-won", "protection", {"ln": 2}, (_DEMOLISH, _SURRENDER_FIGHT), (0, 0), 2, True),
    # :17578 ka(sp)=ka(sp)+p:x=1:gosub1160:goto1100
    ("17578-wrecked", "protection", {"ln": 3}, (_DEMOLISH,), (0, 0, 42), 1, True),
    # :17586 gosub1100, the fight's wait, :17588 ifs=2thenreturn
    ("17588-owner-won", "sob_story", {"ln": 4}, (_KILL, _SURRENDER_FIGHT), (0, 0), 2, True),
    # :17592 ...:goto17578 -> :17578's goto1100
    ("17592-owner-dead", "sob_story", {"ln": 5}, (_KILL,), (0, 0, 99), 1, True),
    # :17586's wait, the fight's, :17592 -> :17578's goto1100
    (
        "17592-owner-shot",
        "sob_story",
        {"ln": 1, "roster": (_KILLER,)},
        (_KILL, ("shoot", STEP_RIGHT)),
        (0, 0, 1, 10, 0, 50),
        3,
        True,
    ),
    # :17206 gosub1100, the fight's wait, :17215 ifs=2thenreturn
    ("17215-jack-won", "protection", {"ln": 1}, (_SURRENDER_FIGHT,), (), 2, True),
]


@pytest.mark.parametrize(
    ("key", "fields", "answers", "draws", "waits", "ends"),
    [case[1:] for case in _EXITS],
    ids=[case[0] for case in _EXITS],
)
def test_each_exit_waits_for_a_key_where_the_source_does(key, fields, answers, draws, waits, ends):
    _, source = _run(key, _state(**fields), answers=answers, draws=draws)
    assert source.key_waits() == waits
    assert source.ends_in_key_wait() == ends


def test_jack_leaving_waits_before_the_payout(monkeypatch):
    """:17221 ``...gebiet...":gosub1100`` then :17225 ``goto17500``: the wait comes
    between Jack's leaving and the shopkeeper's reply; taking the money (:17565) adds
    none. (The stand-in fight shows no outcome screen.)"""
    _won_jack_fight(monkeypatch, None)
    _, source = _run("protection", _state(ln=5), answers=(_TAKE,), draws=(1, 0))
    keys = [i.key for i in source.seen if isinstance(i, (ShowMessage, Acknowledge))]
    assert keys == [
        "locations.sgl.jack_warning",
        KEY_WAIT_SCREEN,
        "locations.sgl.jack_leaves",
        KEY_WAIT_SCREEN,
        "locations.sgl.reply_pays",
    ]

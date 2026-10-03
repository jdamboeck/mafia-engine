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

Through ``run_pure`` with the strict ``StubRng``; every refusal leaves the state as it
was. Option 2 (``:20100``, the safe-crack) is the next unit's: only its prologue runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.effects import Jail, JobClear, ScoreAndRank, TipClear
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.setup import load_combat_backdrop
from data.game_configs.mafia_1920s.state import SCHEMA, tip_target, values_of
from engine.combat import CombatResult
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, SetMovementPoints, Teleport
from engine.interactions import Ctx, StartCombat
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
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
        "chief_bribe_negative_months": "faithful",
        "chief_bribe_empty_answer": "faithful",
    }


def _state(*, ln: int = 3, tip: int = 0, previous=(0, 0), **fields) -> GameState:
    fields.setdefault("ka", 10_000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("ms", 20)
    fields.setdefault("rank", 3)
    fields.setdefault("roster", _GANG2)
    player = Player(
        name="alcapone",
        values={**SCHEMA.player_defaults(), **values_of(tip_target=tip)},
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


def test_the_safe_crack_does_nothing_yet_past_the_prologue():
    """Option 2 (:20100) is the next unit's: past the rank and the trap, nothing."""
    st = _state()
    result, source, _ = _run("safe", st)
    assert result.effects == [] and result.state == st
    assert source.seen == []


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

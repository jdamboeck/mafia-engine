"""Tests for the pol (Polizei-Praesidium) handlers — ports ``mf-prg.bas:21000-21255``.

::

    21005 onwgoto26045,21010,21100
    21010 print"aha! fuer einen monat untaetigkeit"
    21011 input"{down}nehme ich 1000 $. wieviele monate:";x:p=1000*x:ifx=0thenreturn
    21015 ifka(sp)<pthenprint"{down}sie sind leider nich fluessig!":goto1100
    21020 ka(sp)=ka(sp)-p:pl(sp)=pl(sp)+x+1:x=2:gosub1160
    21030 print"{down}ausgang!":po(sp)=911:goto1100
    21100 x=1:fori=1tosz:ifgs(i)theng(x)=i:x=x+1
    21105 next:ifint(rnd(1)*3)=0andra(sp)>4theng(x)=5:x=x+1:sp$(5)="irgendjemanden"
    21125 g=val(x$):ifg=0thenreturn
    21130 p=500*int(rnd(1)*5)+3000
    21255 ka(x)=ka(x)-y:ka(sp)=ka(sp)+y:gs(x)=0:x=2:gosub1160:return
     4050 pl(sp)=pl(sp)+(pl(sp)>0)

Three options: surrender (the trial at ``:26045``), the chief bribe and freeing an
inmate. The rng is the strict ``StubRng``; every refusal leaves the state as it was.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import data.game_configs.mafia_1920s.state as game
from data.game_configs.mafia_1920s.effects import (
    BribeMonthsChange,
    Jail,
    JobClear,
    ScoreAndRank,
    TipClear,
)
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.handlers.turn import JAIL_SCREEN
from data.game_configs.mafia_1920s.state import SCHEMA, Wanted, values_of
from engine.config_loader import load_config, load_game_config
from engine.effects import MoneyChange, RosterAppend, SetMovementPoints, Teleport, commit
from engine.interactions import (
    Acknowledge,
    LocationMenu,
    MapMove,
    OptionDone,
    PromptInt,
    ShowMessage,
    TurnMenu,
)
from engine.locations import HANDLERS
from engine.persistence import load_game, replay, save_game
from engine.rng import Rng
from engine.state import Clock, Config, GameState, Player
from engine.turns import UPKEEP, TurnRunner
from engine.upkeep import UPKEEP_HANDLER_KEY
from tests.helpers import StubRng, run_pure, scripted, with_values

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)
_PARAMS = {**load_config(_CONFIG_DIR / "config.yaml")["formula_params"], "score_mult": 1.0}

_NEGATIVE = "c64_input_negatives"
_EMPTY = "chief_bribe_empty_answer"

#: The street cells next to the station's door (910), and the step each makes into it.
_FROM_LEFT, _FROM_RIGHT, _FROM_BELOW = 909, 911, 950


def _score(x: float) -> ScoreAndRank:
    return ScoreAndRank(amount=x, rank_divisor=11.1)


def _rules(**settings: str) -> dict[str, str]:
    rules = {
        "intelligence_or_30": "faithful",
        "shared_direction_memory": "faithful",
        "stale_bribe_price": "faithful",
        "flight_odds_by_seat": "faithful",
        _NEGATIVE: "faithful",
        _EMPTY: "faithful",
    }
    rules.update(settings)
    return rules


def _player(name: str, *, jail: int = 0, bribe: int = 0, **fields) -> Player:
    values = {
        **SCHEMA.player_defaults(),
        **values_of(Wanted(jail_months=jail, bribe_months=bribe)),
    }
    fields.setdefault("ka", 10_000)
    fields.setdefault("gf", 50.0)
    fields.setdefault("po", _FROM_LEFT)
    fields.setdefault("ms", 20)
    fields.setdefault("rank", 1)
    fields.setdefault("roster", (Gangster(name=name, energie=40, kraft=30, brutalitaet=30),))
    return Player(name=name, values=values, **fields)


def _state(*others: dict, rules=None, **active) -> GameState:
    """Player 0 is the active one (``active``); each of ``others`` is one more player."""
    players = (_player("alcapone", **active),) + tuple(
        _player(o.pop("name", f"p{i + 1}"), **o) for i, o in enumerate(others)
    )
    return GameState(
        players=players,
        clock=Clock(active_player=0, player_count=len(players)),
        config=Config(formula_params=_PARAMS, house_rules=rules or _rules()),
    )


def _run(key: str, state: GameState, answers=(), draws=()):
    rng = StubRng(*draws)
    source = scripted(*answers)
    result = run_pure(HANDLERS[key], source, state=state, rng=rng)
    assert rng._values == [], f"undrawn rng values left: {rng._values}"
    return result, source


def _prompts(source) -> list:
    return [i for i in source.seen if not isinstance(i, ShowMessage)]


def _bribe_months(state: GameState, idx: int = 0) -> int:
    return game.wanted(state.players[idx]).bribe_months


def _jail_months(state: GameState, idx: int) -> int:
    return game.wanted(state.players[idx]).jail_months


# --------------------------------------------------------------------------- #
# Surrender — :21005 -> :26045                                                 #
# --------------------------------------------------------------------------- #
def test_surrender_sentences_the_player():
    """Option 1 goes straight to the trial (``:26045``): no arrest menu, and a player
    no one is looking for may give himself up."""
    result, source = _run("pol.surrender", _state(rank=3))
    months = int(3 / 2 + 0.5)  # :26045 gs(sp)=int(ra(sp)/2+.5)
    assert result.effects == [
        TipClear(),
        _score(2),
        Jail(months=months),
        SetMovementPoints(0),
        JobClear(),
        _score(-10),
        Teleport(911),
    ]
    assert source.message_keys() == ["police.trial", "police.sentenced"]
    assert _jail_months(result.state, 0) == 2
    assert result.state.players[0].po == 911


# --------------------------------------------------------------------------- #
# The chief bribe — :21010-21030                                              #
# --------------------------------------------------------------------------- #
def _bought(months: int) -> list:
    return [
        MoneyChange(-1000 * months),
        BribeMonthsChange(months + 1),
        _score(2),
        Teleport(911),
    ]


def test_the_chief_bribe_buys_months_and_sends_the_player_out_the_back():
    """:21020 ``ka(sp)=ka(sp)-p:pl(sp)=pl(sp)+x+1``; :21030 ``po(sp)=911``."""
    result, source = _run("pol.bribe", _state(bribe=1), answers=(3,))
    assert result.effects == _bought(3)
    assert source.message_keys() == ["locations.pol.chief_offer", "locations.pol.chief_done"]
    player = result.state.players[0]
    assert (player.ka, player.po, player.ms) == (7000, 911, 20)
    assert _bribe_months(result.state) == 1 + 4


def test_no_months_is_a_quiet_return():
    """:21011 ``ifx=0thenreturn``."""
    result, source = _run("pol.bribe", _state(), answers=(0,))
    assert result.effects == []
    assert source.message_keys() == ["locations.pol.chief_offer"]


def test_an_unaffordable_chief_bribe_changes_nothing():
    """:21015 ``ifka(sp)<pthenprint"{down}sie sind leider nich fluessig!"``: strict, so
    cash equal to the price buys."""
    st = _state(ka=9999)
    result, source = _run("pol.bribe", st, answers=(10,))
    assert result.effects == []
    assert result.state == st
    assert source.message_keys()[-1] == "locations.pol.chief_broke"

    result, _ = _run("pol.bribe", _state(ka=10_000), answers=(10,))
    assert result.effects == _bought(10)


def test_a_negative_month_count_pays_out_when_faithful():
    """C64 ``INPUT`` hands :21011 a negative (checked in VICE): ``p=1000*x`` is below 0,
    so :21015's ``ka(sp)<p`` passes and :21020 pays the player and lowers ``pl``."""
    result, _ = _run("pol.bribe", _state(), answers=(-3,))
    assert result.effects == [
        MoneyChange(3000),
        BribeMonthsChange(-2),
        _score(2),
        Teleport(911),
    ]
    assert result.state.players[0].ka == 13_000
    assert _bribe_months(result.state) == -2


def test_a_negative_month_count_is_refused_under_intent():
    """Intent: a month count below 0 is asked again, so the player buys what he then
    types."""
    st = _state(rules=_rules(**{_NEGATIVE: "intent"}))
    result, source = _run("pol.bribe", st, answers=(-3, 2))
    assert result.effects == _bought(2)
    assert [p.min for p in _prompts(source)] == [0, 0]


@pytest.mark.parametrize(("po", "step"), [(_FROM_LEFT, 1), (_FROM_RIGHT, -1), (_FROM_BELOW, -40)])
def test_an_empty_month_answer_keeps_the_step_into_the_door_when_faithful(po, step):
    """An empty ``INPUT`` leaves ``x`` as it was (checked in VICE), and the last thing
    that wrote ``x`` was the map step into the door (:2015-2018 ``x=-1``/``1``/``-40``/
    ``40``): from 909 the player buys a month, from 911 or 950 he is paid."""
    result, _ = _run("pol.bribe", _state(po=po), answers=("",))
    assert result.effects == [
        MoneyChange(-1000 * step),
        BribeMonthsChange(step + 1),
        _score(2),
        Teleport(911),
    ]


def test_an_empty_month_answer_is_a_quiet_return_under_intent():
    st = _state(po=_FROM_RIGHT, rules=_rules(**{_EMPTY: "intent"}))
    result, source = _run("pol.bribe", st, answers=("",))
    assert result.effects == []
    assert source.message_keys() == ["locations.pol.chief_offer"]


def test_an_empty_answer_standing_for_a_negative_step_is_asked_again_under_negative_intent():
    """The two switches meet: the empty answer keeps -1 (faithful), which the intended
    month check then refuses, so the player is asked again."""
    st = _state(po=_FROM_RIGHT, rules=_rules(**{_NEGATIVE: "intent"}))
    result, source = _run("pol.bribe", st, answers=("", 1))
    assert result.effects == _bought(1)
    assert len(_prompts(source)) == 2


# --------------------------------------------------------------------------- #
# :4050 — the months age at upkeep                                             #
# --------------------------------------------------------------------------- #
def _upkeep(state: GameState) -> GameState:
    result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], scripted(), state=state, rng=StubRng())
    return result.state


def test_bought_months_age_by_one_each_upkeep_and_stop_at_zero():
    """:4050 ``pl(sp)=pl(sp)+(pl(sp)>0)``: true is -1 on the C64, so a positive count
    loses one a month and 0 stays 0."""
    state = _CONFIG.new_game(
        seed=3, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    state = with_values(state, Wanted(bribe_months=2))
    seen = []
    for _ in range(3):
        state = _upkeep(state)
        seen.append(_bribe_months(state))
    assert seen == [1, 0, 0]


def test_negative_months_never_age():
    """``(pl(sp)>0)`` is 0 for a negative count (checked in VICE: -2 stays -2)."""
    state = _CONFIG.new_game(
        seed=3, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    state = with_values(state, Wanted(bribe_months=-2))
    assert _bribe_months(_upkeep(_upkeep(state))) == -2


def test_the_plus_one_prepays_the_next_upkeep():
    """:21020 adds ``x+1`` so the first upkeep leaves exactly the months bought."""
    state = _CONFIG.new_game(
        seed=3, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    state = replace(state, players=(replace(state.players[0], po=_FROM_LEFT),))
    result, _ = _run("pol.bribe", state, answers=(3,))
    assert _bribe_months(_upkeep(result.state)) == 3


def test_the_chief_bribe_months_survive_a_save_and_a_load(tmp_path):
    state = _CONFIG.new_game(
        seed=3, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    log = [BribeMonthsChange(4)]
    live = commit(state, log).state

    snap = tmp_path / "snap.jsonl"
    save_game(snap, live, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=3)
    assert _bribe_months(load_game(snap, _CONFIG.registries).state) == 4

    logged = tmp_path / "log.jsonl"
    save_game(logged, state, registries=_CONFIG.registries, effect_log=log, rng_log=[], seed=3)
    loaded = load_game(logged, _CONFIG.registries)
    assert loaded.effect_log == log
    assert _bribe_months(replay(loaded, _CONFIG.registries)) == 4


# --------------------------------------------------------------------------- #
# Freeing an inmate — :21100-21255                                             #
# --------------------------------------------------------------------------- #
_NO_PHANTOM = 1  # :21105 int(rnd(1)*3)=0 missed
_PHANTOM = 0


def _price(k: int) -> int:
    """The release price for price draw ``k``: :21130 ``p=500*int(rnd(1)*5)+3000``."""
    return 500 * k + 3000


def test_freeing_a_jailed_player_clears_their_months_and_asks_them_for_thanks():
    st = _state({"name": "moran", "jail": 2, "ka": 800, "po": 911})
    result, source = _run("pol.free", st, answers=(1, True, 300), draws=(_NO_PHANTOM, 2))
    assert result.effects == [
        MoneyChange(-_price(2)),
        MoneyChange(-300, player=1),
        MoneyChange(300),
        Jail(months=0, player=1),
        _score(2),
    ]
    assert _jail_months(result.state, 1) == 0
    freed = result.state.players[1]
    assert (freed.ka, freed.po) == (500, 911)
    assert result.state.players[0].ka == 10_000 - 4000 + 300

    # :21250-21252 the screen names the freed player, and its prompt is theirs.
    thanks = [m for m in source.messages() if m.key == "locations.pol.thank_you"]
    assert [m.params for m in thanks] == [{"name": "moran", "rescuer": "alcapone", "cash": 800}]
    prompt = _prompts(source)[-1]
    assert isinstance(prompt, PromptInt) and prompt.player == 1


def test_only_jailed_players_are_listed():
    """:21100 ``ifgs(i)theng(x)=i``: the list numbers the jailed players 1..x."""
    st = _state({"name": "moran"}, {"name": "torrio", "jail": 1}, {"name": "capone2", "jail": 3})
    _result, source = _run("pol.free", st, answers=(0,), draws=(_NO_PHANTOM,))
    entries = [m.params for m in source.messages() if m.key == "locations.pol.free_entry"]
    assert entries == [{"index": 1, "name": "torrio"}, {"index": 2, "name": "capone2"}]
    pick = _prompts(source)[0]
    assert (pick.min, pick.max) == (0, 2)


def test_the_phantom_inmate_joins_the_gang_and_adds_no_score():
    """:21105 the phantom is listed when the 1-in-3 roll hits and ``ra(sp)>4``; freed,
    he joins as "knasti" (:21210 ``ge$(sp,x)="05100540"``), and no ``gosub1160`` runs."""
    result, source = _run("pol.free", _state(rank=5), answers=(1, True), draws=(_PHANTOM, 1))
    knasti = Gangster(name="knasti", weapon=0, energie=5, kraft=10, intelligenz=5, brutalitaet=40)
    assert result.effects == [MoneyChange(-_price(1)), RosterAppend(gangster=knasti)]
    assert not any(isinstance(e, ScoreAndRank) for e in result.effects)
    assert result.state.players[0].gf == 50.0
    assert "locations.pol.free_entry_phantom" in source.message_keys()
    assert source.message_keys()[-1] == "locations.pol.freed"


def test_the_phantom_thanks_a_full_gang_and_goes():
    """:21205 ``ifgz(sp)=10thenprint"{down}er bedankt sich und verschwindet..."``: the
    money is spent and nobody joins."""
    gang = tuple(Gangster(name=f"g{i}") for i in range(10))
    result, source = _run(
        "pol.free", _state(rank=6, roster=gang), answers=(1, True), draws=(_PHANTOM, 4)
    )
    assert result.effects == [MoneyChange(-_price(4))]
    assert source.message_keys()[-2:] == ["locations.pol.freed", "locations.pol.phantom_leaves"]


def test_the_phantom_needs_a_rank_above_four():
    """``ra(sp)>4``: at rank 4 a hit roll lists nobody."""
    result, source = _run("pol.free", _state(rank=4), draws=(_PHANTOM,))
    assert result.effects == []
    assert source.message_keys() == ["locations.pol.nobody_jailed"]


def test_with_no_inmates_and_the_phantom_roll_missed_nobody_is_jailed():
    """:21110 ``ifx=1thenprint"es ist niemand inhaftiert."``."""
    st = _state({"name": "moran"}, rank=9)
    result, source = _run("pol.free", st, draws=(_NO_PHANTOM,))
    assert result.effects == []
    assert result.state == st
    assert source.message_keys() == ["locations.pol.nobody_jailed"]
    assert _prompts(source) == []


@pytest.mark.parametrize("key", [0, ""])
def test_a_zero_or_non_digit_key_returns_with_no_pause(key):
    """:21125 ``g=val(x$):ifg=0thenreturn``: the key "0", or RETURN (``val`` of a
    non-digit is 0), leaves with nothing after the list and no price roll."""
    st = _state({"name": "moran", "jail": 2})
    result, source = _run("pol.free", st, answers=(key,), draws=(_NO_PHANTOM,))
    assert result.effects == []
    assert isinstance(source.seen[-1], PromptInt)


def test_an_unaffordable_release_changes_nothing():
    """:21135 ``ifka(sp)<pgoto1125``: "du hast zu wenig kies!"."""
    st = _state({"name": "moran", "jail": 2}, ka=3999)
    result, source = _run("pol.free", st, answers=(1, True), draws=(_NO_PHANTOM, 2))
    assert result.effects == []
    assert result.state == st
    assert source.message_keys()[-1] == "system.not_enough_money"


def test_a_declined_release_changes_nothing():
    """:21131 ``gosub1110:ifx$="n"thenreturn`` — before the cash check."""
    st = _state({"name": "moran", "jail": 2}, ka=0)
    result, source = _run("pol.free", st, answers=(1, False), draws=(_NO_PHANTOM, 0))
    assert result.effects == []
    assert "system.not_enough_money" not in source.message_keys()
    assert source.messages()[-1].params == {"price": 3000}


def _thanks_run(answers, *, cash=800):
    st = _state({"name": "moran", "jail": 2, "ka": cash})
    return _run("pol.free", st, answers=(1, True, *answers), draws=(_NO_PHANTOM, 0))


def test_a_thank_you_above_the_freed_players_cash_redraws_the_screen():
    """:21252 ``ify>ka(x)goto21250``: the whole screen again, then the prompt."""
    result, source = _thanks_run((900, 800))
    assert source.message_keys().count("locations.pol.thank_you") == 2
    assert MoneyChange(-800, player=1) in result.effects
    assert result.state.players[1].ka == 0


def test_a_negative_thank_you_is_asked_again_without_the_screen():
    """:21253 ``ify>ka(x)ory<0thenprint"{up}{up}";:goto21252``."""
    result, source = _thanks_run((-5, 100))
    assert source.message_keys().count("locations.pol.thank_you") == 1
    assert len(_prompts(source)) == 4  # pick, confirm, two thank-you asks
    assert MoneyChange(-100, player=1) in result.effects


def test_an_empty_thank_you_is_nothing():
    """``inputx$`` keeps ``x$`` on an empty answer: the "j" of the confirm, and
    ``val("j")`` is 0 (checked in VICE). The months are still cleared."""
    result, _ = _thanks_run(("",))
    assert result.effects == [MoneyChange(-3000), Jail(months=0, player=1), _score(2)]


def test_an_empty_thank_you_after_a_refusal_is_asked_again():
    """After :21252's ``goto21250`` ``x$`` holds the refused answer, so an empty answer
    is refused again."""
    result, source = _thanks_run((900, "", 50))
    assert MoneyChange(-50, player=1) in result.effects
    assert len(_prompts(source)) == 5


def test_a_thank_you_equal_to_the_cash_is_taken():
    result, _ = _thanks_run((800,))
    assert result.state.players[1].ka == 0


# --------------------------------------------------------------------------- #
# Through the turn runner                                                      #
# --------------------------------------------------------------------------- #
_TWO = [("alcapone", "the outfit"), ("moran", "north side")]


def test_the_freed_player_plays_their_next_turn_from_911():
    state = _CONFIG.new_game(seed=42, end_year=1930, score_weight=1.0, players=_TWO)
    moran = replace(state.players[1], po=911)
    state = replace(state, players=(state.players[0], moran))
    state = with_values(state, Wanted(jail_months=2), idx=1)

    result, _ = _run("pol.free", state, answers=(1, True, 0), draws=(_NO_PHANTOM, 0))
    freed = replace(result.state, clock=replace(result.state.clock, active_player=1))

    runner = TurnRunner(
        freed,
        StubRng(),
        city=_CONFIG.city,
        shells=_CONFIG.shells,
        turn_menu=_CONFIG.menus["turn"],
    )
    gen = runner.run(UPKEEP)
    seen = []
    interaction = next(gen)
    while not isinstance(interaction, TurnMenu):
        seen.append(interaction)
        interaction = gen.send(None)
    gen.close()
    assert not any(isinstance(i, Acknowledge) and i.key == JAIL_SCREEN for i in seen)
    assert runner.state.clock.active_player == 1
    assert runner.state.players[1].po == 911


def test_after_the_chief_bribe_the_next_map_step_starts_from_911():
    """:21030 ``po(sp)=911`` mid-turn: the door's 5 points are charged (:2060) and the
    map goes on from the back exit."""
    state = _CONFIG.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    from engine.turns import WALKING

    state = replace(
        state,
        players=(replace(state.players[0], po=_FROM_LEFT, ms=20, ka=5000),),
        clock=replace(state.clock, turn_phase=WALKING),
    )
    runner = TurnRunner(
        state,
        Rng(42),
        city=_CONFIG.city,
        shells=_CONFIG.shells,
        turn_menu=_CONFIG.menus["turn"],
    )
    gen = runner.run()
    answers = ["right", "bribe", 2, "down"]  # into 910, the bribe, 2 months, a step
    seen = []
    interaction = next(gen)
    try:
        while answers:
            seen.append(interaction)
            if isinstance(interaction, MapMove):
                response = answers.pop(0)
            elif isinstance(interaction, LocationMenu):
                response = interaction.options.index(answers.pop(0))
            elif isinstance(interaction, PromptInt):
                response = answers.pop(0)
            else:
                response = None
            interaction = gen.send(response)
    finally:
        gen.close()
    assert any(isinstance(i, OptionDone) for i in seen)
    player = runner.state.players[0]
    # A step down from 911 lands on 951; from 909 it would have landed on 949.
    assert player.po == 911 + 40
    assert player.ms == 20 - 5 - 1
    assert _bribe_months(runner.state) == 3

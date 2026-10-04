"""Tests for the ble (Blueten-Eddie) handlers — ports ``mf-prg.bas:22000-22120``.

Two playable options:

* ``ble.passport`` (``:22010-22020``) — a passport for the whole gang, ``1000*gz(sp)``,
  confirmed before the cash check; buying sets the passport mark (``ag(sp) or 1``) and
  scores 1.
* ``ble.counterfeit`` (``:22100-22120``) — invest ``q`` real dollars (``0-5000``, at most
  the cash) for ``p=int(rnd(1)*q/2)+q+100`` in counterfeit bills; the roll comes before
  the confirm; buying sets the counterfeit mark (``ag(sp) or 2``) and scores 1.

Every refusal leaves the state as it was (``run_pure`` + an empty effect list).
"""

from __future__ import annotations

from pathlib import Path

from engine.config_loader import load_game_config
from engine.effects import MoneyChange, commit
from engine.interactions import Confirm, PromptInt, ShowMessage
from engine.locations import HANDLERS
from engine.persistence import load_game, replay, save_game
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.effects import MarkSet, ScoreAndRank
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.state import Contraband
from tests.helpers import StubRng, run_pure, scripted
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)

_PARAMS = {
    "rank_divisor": 11.1,
    "score_mult": 1.0,
    "ble_passport_price": 1000,
    "ble_passport_score": 1,
    "ble_counterfeit_max": 5000,
    "ble_counterfeit_spread": 2,
    "ble_counterfeit_bonus": 100,
    "ble_counterfeit_score": 1,
}

_SCORE = ScoreAndRank(amount=1, rank_divisor=11.1)


def _state(*, ka=10_000, gang=1, papers=0, counterfeit=0) -> GameState:
    roster = tuple(Gangster(name=f"g{i}") for i in range(gang))
    player = Player(
        name="p0",
        ka=ka,
        roster=roster,
        last_location=1,
        values=game.values_of(Contraband(fake_papers=papers, counterfeit=counterfeit)),
    )
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params=_PARAMS),
    )


def _marks(state: GameState) -> tuple[int, int]:
    held = game.contraband(state.players[0])
    return (held.fake_papers, held.counterfeit)


# --------------------------------------------------------------------------- #
# ble.passport — :22010-22020                                                  #
# --------------------------------------------------------------------------- #
def test_passport_price_scales_with_gang_size():
    """:22010 ``x=gz(sp):p=1000*x`` — the boss counts, so a gang of 1 pays 1000."""
    for gang in (1, 2, 5, 10):
        src = scripted(True)
        result = run_pure(HANDLERS["ble.passport"], src, state=_state(gang=gang))
        price = 1000 * gang
        assert result.effects == [MoneyChange(-price), MarkSet(fake_papers=True), _SCORE]
        assert result.state.players[0].ka == 10_000 - price
        assert _marks(result.state) == (1, 0)


def test_passport_offer_names_one_pass_or_the_count():
    """:22011 one gangster: "fuer einen pass"; :22012 otherwise "fuer"x"paesse"."""
    src = scripted(False)
    run_pure(HANDLERS["ble.passport"], src, state=_state(gang=1))
    assert src.message_keys() == [
        "locations.ble.passport_for_one",
        "locations.ble.passport_price",
    ]
    assert src.messages()[1].params == {"price": 1000}

    src = scripted(False)
    run_pure(HANDLERS["ble.passport"], src, state=_state(gang=3))
    assert src.message_keys()[0] == "locations.ble.passport_for_many"
    assert src.messages()[0].params == {"count": 3}
    assert src.messages()[1].params == {"price": 3000}


def test_passport_success_says_the_source_line():
    src = scripted(True)
    run_pure(HANDLERS["ble.passport"], src, state=_state())
    assert src.message_keys()[-1] == "locations.ble.passport_done"


def test_passport_declined_changes_nothing():
    """:22013 ``gosub1110:ifx$="n"thenreturn`` — the confirm comes before the cash check."""
    src = scripted(False)
    result = run_pure(HANDLERS["ble.passport"], src, state=_state(ka=0))
    assert result.effects == []
    assert "system.not_enough_money" not in src.message_keys()
    assert [type(i) for i in src.seen if not isinstance(i, ShowMessage)] == [Confirm]


def test_passport_broke_player_is_refused_with_the_source_text():
    """:22014 ``ifka(sp)<pgoto1125`` — "du hast zu wenig kies!", nothing changes."""
    for ka in (0, 999):
        src = scripted(True)
        st = _state(ka=ka)
        result = run_pure(HANDLERS["ble.passport"], src, state=st)
        assert result.effects == []
        assert result.state == st
        assert src.message_keys()[-1] == "system.not_enough_money"


def test_passport_exact_cash_is_enough():
    """``ka(sp)<p`` is strict: cash equal to the price buys."""
    result = run_pure(HANDLERS["ble.passport"], scripted(True), state=_state(ka=2000, gang=2))
    assert result.state.players[0].ka == 0
    assert _marks(result.state) == (1, 0)


def test_passport_bought_again_while_held_is_charged_again():
    """House rule ``passport_rebuy_charged``: :22010-22020 never looks at ``ag(sp)``,
    so a holder pays the full price again and the mark stays as it was."""
    st = _state(papers=1, counterfeit=1, gang=2)
    result = run_pure(HANDLERS["ble.passport"], scripted(True), state=st)
    assert result.effects == [MoneyChange(-2000), MarkSet(fake_papers=True), _SCORE]
    assert result.state.players[0].ka == 8000
    assert _marks(result.state) == (1, 1)


def test_passport_draws_nothing():
    rng = StubRng()
    run_pure(HANDLERS["ble.passport"], scripted(True), state=_state(), rng=rng)
    assert rng.calls == []


# --------------------------------------------------------------------------- #
# ble.counterfeit — :22100-22120                                               #
# --------------------------------------------------------------------------- #
def test_counterfeit_purchase_follows_the_formula_over_the_input_range():
    """:22110 ``p=int(rnd(1)*q/2)+q+100``, :22120 ``ka(sp)=ka(sp)-q+p``.

    ``int(rnd(1)*q/2)`` is drawn as ``rng.range(q)//2`` — the same value for every
    ``rnd(1)``, since ``int(int(r*q)/2) == int(r*q/2)``. So the draw ``d`` of
    ``range(q)`` gives ``p = d//2 + q + 100``.
    """
    for q in (1, 2, 3, 99, 100, 2500, 4999, 5000):
        for d in sorted({0, 1, q // 2, q - 1} & set(range(q))):
            rng = StubRng(d)
            src = scripted(q, True)
            result = run_pure(HANDLERS["ble.counterfeit"], src, state=_state(), rng=rng)
            p = d // 2 + q + 100
            assert rng.calls == [("range", q)]
            assert src.messages()[-1].params == {"amount": p}
            assert result.effects == [MoneyChange(p - q), MarkSet(counterfeit=True), _SCORE]
            assert result.state.players[0].ka == 10_000 - q + p
            assert _marks(result.state) == (0, 1)


def test_counterfeit_messages_and_prompt_are_the_sources():
    rng = StubRng(0)
    src = scripted(100, True)
    run_pure(HANDLERS["ble.counterfeit"], src, state=_state(ka=3000), rng=rng)
    assert src.message_keys() == [
        "locations.ble.counterfeit_reluctant",
        "locations.ble.counterfeit_offer",
    ]
    prompt = next(i for i in src.seen if isinstance(i, PromptInt))
    assert prompt.key == "locations.ble.counterfeit_prompt"
    # :22106 ``ifq>5000orq>ka(sp)`` re-prompts: the prompt's ceiling is the lower of the two.
    assert prompt.max == 3000


def test_counterfeit_zero_is_a_quiet_abort():
    """:22105 ``ifq<=0thenreturn`` — no roll, no message, nothing changes."""
    rng = StubRng()
    src = scripted(0)
    st = _state()
    result = run_pure(HANDLERS["ble.counterfeit"], src, state=st, rng=rng)
    assert result.effects == []
    assert result.state == st
    assert rng.calls == []
    assert src.message_keys() == ["locations.ble.counterfeit_reluctant"]


def test_counterfeit_negative_is_a_quiet_abort_not_a_reprompt():
    """:22105 ``ifq<=0thenreturn`` covers a negative too: the C64 ``INPUT`` takes "-5"
    (checked in VICE) and the line returns. The port's prompt accepts it and aborts,
    rather than asking again."""
    for q in (-1, -5, -5000, -1_000_000):
        rng = StubRng()
        src = scripted(q)
        st = _state()
        result = run_pure(HANDLERS["ble.counterfeit"], src, state=st, rng=rng)
        assert result.effects == []
        assert result.state == st
        assert rng.calls == []
        assert sum(isinstance(i, PromptInt) for i in src.seen) == 1


def test_counterfeit_above_5000_asks_again():
    """:22106 ``ifq>5000...thenprint"{up}{up}";:goto22105`` — asked again, not refused."""
    rng = StubRng(0)
    src = scripted(5001, 99999, 5000, True)
    result = run_pure(HANDLERS["ble.counterfeit"], src, state=_state(ka=10_000), rng=rng)
    assert sum(isinstance(i, PromptInt) for i in src.seen) == 3
    assert result.effects == [MoneyChange(100), MarkSet(counterfeit=True), _SCORE]


def test_counterfeit_above_cash_asks_again_and_a_zero_then_leaves():
    """:22106 ``q>ka(sp)`` — above the cash is asked again; a 0 then leaves quietly."""
    rng = StubRng()
    src = scripted(501, 0)
    st = _state(ka=500)
    result = run_pure(HANDLERS["ble.counterfeit"], src, state=st, rng=rng)
    assert sum(isinstance(i, PromptInt) for i in src.seen) == 2
    assert result.effects == []
    assert result.state == st


def test_counterfeit_broke_player_can_only_leave():
    """With no cash every positive stake is above ``ka(sp)``: only ``q<=0`` gets out."""
    src = scripted(1, 0)
    st = _state(ka=0)
    result = run_pure(HANDLERS["ble.counterfeit"], src, state=st, rng=StubRng())
    assert result.effects == []
    assert sum(isinstance(i, PromptInt) for i in src.seen) == 2


def test_counterfeit_declined_changes_nothing_but_the_roll_is_drawn():
    """:22115 ``gosub1110:ifx$="n"thenreturn`` — after :22110's roll."""
    rng = StubRng(7)
    src = scripted(100, False)
    st = _state()
    result = run_pure(HANDLERS["ble.counterfeit"], src, state=st, rng=rng)
    assert result.effects == []
    assert result.state == st
    assert rng.calls == [("range", 100)]


def test_counterfeit_bought_again_keeps_the_mark_and_pays_again():
    rng = StubRng(0)
    st = _state(papers=1, counterfeit=1)
    result = run_pure(HANDLERS["ble.counterfeit"], scripted(10, True), state=st, rng=rng)
    assert result.state.players[0].ka == 10_100
    assert _marks(result.state) == (1, 1)


# --------------------------------------------------------------------------- #
# The marks are saved                                                          #
# --------------------------------------------------------------------------- #
def test_both_marks_survive_a_save_and_a_load(tmp_path):
    state = _CONFIG.new_game(
        seed=3, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    log = [MarkSet(fake_papers=True), MarkSet(counterfeit=True)]
    live = commit(state, log).state
    assert _marks(live) == (1, 1)

    # The marks in a snapshot...
    snap = tmp_path / "snap.jsonl"
    save_game(snap, live, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=3)
    assert _marks(load_game(snap, _CONFIG.registries).state) == (1, 1)

    # ...and the effects that set them in the log, replayed.
    logged = tmp_path / "log.jsonl"
    save_game(logged, state, registries=_CONFIG.registries, effect_log=log, rng_log=[], seed=3)
    loaded = load_game(logged, _CONFIG.registries)
    assert loaded.effect_log == log
    assert _marks(replay(loaded, _CONFIG.registries)) == (1, 1)


def test_mark_set_clears_one_mark_and_leaves_the_other():
    st = _state(papers=1, counterfeit=1)
    assert _marks(commit(st, [MarkSet(fake_papers=False)]).state) == (0, 1)
    assert _marks(commit(st, [MarkSet(counterfeit=False)]).state) == (1, 0)

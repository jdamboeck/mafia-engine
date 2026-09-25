"""U6 — standings and year-end runners (engine) + handlers (mafia_1920s config).

Ports ``mf-prg.bas:4500-4515`` (standings) and ``:40100-40166`` (year-end result).
The ranking rule (``:40105-40106``: strictly higher replaces the leader, equal joins the
tie list) lives in the config handler; the engine only runs it (KTD-1).
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.game_end import (
    STANDINGS_HANDLER_KEY,
    YEAR_END_HANDLER_KEY,
    run_standings,
    run_year_end,
)
from engine.interactions import PromptInt, ShowMessage
from engine.strings import Resolver
from tests.conftest import CONFIG_DIR

_NAMES = [("alcapone", "outfit"), ("bugsy", "syndikat"), ("dutch", "bande"), ("frank", "clan")]


def _state(mafia_module, scores, cash=None):
    state = mafia_module.new_game(
        seed=1, end_year=1930, score_weight=1.0, players=_NAMES[: len(scores)]
    )
    players = tuple(
        dataclasses.replace(p, gf=float(s), ka=(cash[i] if cash is not None else p.ka))
        for i, (p, s) in enumerate(zip(state.players, scores))
    )
    return dataclasses.replace(state, players=players)


def _collect(runner, state):
    seen: list[ShowMessage] = []

    def source(interaction):
        assert isinstance(interaction, ShowMessage), interaction
        seen.append(interaction)

    result = runner(state, input_source=source)
    return result, seen


def _keys(messages, key):
    return [m for m in messages if m.key == key]


def _winners(messages):
    return [m.params["name"] for m in _keys(messages, "game_end.winner")]


def _tied(messages):
    return [m.params["name"] for m in _keys(messages, "game_end.tie_name")]


# --- the ranking rule (:40100-40160) -----------------------------------------------


def test_ae1_higher_score_wins_alone(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [42, 37]))
    assert _winners(msgs) == ["alcapone"]
    assert _keys(msgs, "game_end.tie_header") == []
    assert _tied(msgs) == []


def test_ae1_winner_not_first_player(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [37, 42]))
    assert _winners(msgs) == ["bugsy"]


def test_ae2_tie_lists_both_top_scorers_in_player_order(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [50, 50, 20]))
    assert _tied(msgs) == ["alcapone", "bugsy"]
    assert len(_keys(msgs, "game_end.tie_header")) == 1
    assert len(_keys(msgs, "game_end.tie_footer")) == 1
    assert _winners(msgs) == []


def test_ae3_single_player_wins(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [0]))
    assert _winners(msgs) == ["alcapone"]
    assert _tied(msgs) == []


def test_four_players_all_zero_all_tie(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [0, 0, 0, 0]))
    assert _tied(msgs) == ["alcapone", "bugsy", "dutch", "frank"]
    assert _winners(msgs) == []


def test_strictly_higher_resets_the_tie_list(mafia_module):
    # :40105 — player 2's 40 beats player 1's 30 and resets the list; :40106 — player
    # 3's equal 40 joins it. Result: players 2 and 3 tie (not player 2 alone).
    _, msgs = _collect(run_year_end, _state(mafia_module, [30, 40, 40]))
    assert _tied(msgs) == ["bugsy", "dutch"]
    assert _winners(msgs) == []


def test_later_higher_score_discards_an_earlier_tie(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [40, 40, 45]))
    assert _winners(msgs) == ["dutch"]
    assert _tied(msgs) == []


# --- the standings table (:4500-4510) ----------------------------------------------


def _expected_rows(state):
    return [{"name": p.name, "cash": p.ka, "score": p.gf} for p in state.players]


@pytest.mark.parametrize("runner", [run_standings, run_year_end])
def test_standings_carry_date_and_every_player(mafia_module, runner):
    state = _state(mafia_module, [12.5, 3, 7], cash=[5100, 6200, 7300])
    state = dataclasses.replace(state, clock=dataclasses.replace(state.clock, year=1926, month=4))
    _, msgs = _collect(runner, state)
    assert msgs[0].key == "game_end.standings_header"
    assert msgs[0].params == {"year": 1926, "month": 5}  # 0-based month 4 -> shown 5
    rows = [m.params for m in _keys(msgs, "game_end.standings_row")]
    assert rows == [
        {"name": "alcapone", "cash": 5100, "score": 12.5},
        {"name": "bugsy", "cash": 6200, "score": 3.0},
        {"name": "dutch", "cash": 7300, "score": 7.0},
    ]
    # standings come first — the rows directly follow the header, in player order
    assert [m.key for m in msgs[1:4]] == ["game_end.standings_row"] * 3


def test_standings_runner_shows_no_result(mafia_module):
    _, msgs = _collect(run_standings, _state(mafia_module, [42, 37]))
    assert {m.key for m in msgs} == {"game_end.standings_header", "game_end.standings_row"}


# --- runner contract ---------------------------------------------------------------


@pytest.mark.parametrize("runner", [run_standings, run_year_end])
def test_runners_commit_no_effects(mafia_module, runner):
    state = _state(mafia_module, [50, 50, 20])
    result = runner(state)  # default input source swallows ShowMessage
    assert result.status == "completed"
    assert len(result.effects) == 0
    assert result.state == state


@pytest.mark.parametrize(
    ("runner", "key"),
    [(run_standings, STANDINGS_HANDLER_KEY), (run_year_end, YEAR_END_HANDLER_KEY)],
)
def test_runners_refuse_a_prompt(mafia_module, monkeypatch, runner, key):
    from engine import locations

    def asks(ctx):
        yield PromptInt("x", 0, 1)
        return []

    monkeypatch.setitem(locations.HANDLERS, key, asks)
    with pytest.raises(AssertionError, match="display-only"):
        runner(_state(mafia_module, [1]))


@pytest.mark.parametrize("runner", [run_standings, run_year_end])
def test_runners_require_a_registered_handler(runner):
    with pytest.raises(KeyError):
        runner(object(), handlers={})


# --- resolved text (verbatim German, classic theme) --------------------------------


def _resolved(msgs):
    r = Resolver.from_config(CONFIG_DIR, theme="classic")
    return "\n".join(r.resolve(m.key, m.params) for m in msgs)


def test_resolved_winner_screen_is_verbatim(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [42, 37], cash=[5000, 6000]))
    text = _resolved(msgs)
    assert "spielstand 1925-1" in text
    assert "spieler:        kapital: punkte:" in text
    assert "alcapone" in text and "5000$" in text and "42" in text
    assert "alcapone hat gewonnen!" in text
    assert "du warst von allen der brutalste," in text
    assert "gemeinste und schlaueste!" in text
    assert "diesmal haben mehrere" not in text


def test_resolved_tie_screen_is_verbatim(mafia_module):
    _, msgs = _collect(run_year_end, _state(mafia_module, [50, 50, 20]))
    text = _resolved(msgs)
    assert "diesmal haben mehrere die gleichen\npunkte:\nalcapone\nbugsy\n" in text
    assert text.endswith("alle waren gleich fiese gangster!")
    assert "hat gewonnen!" not in text

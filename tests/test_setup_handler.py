"""The new-game setup handler (``mf-prg.bas:170-176``, ``:205-220``, ``:290-292``,
``:300-316``, ``:350-360``) and the ``new_game`` paths it feeds.

The characterization test at the top pins the starting stats ``new_game`` rolls for a
fixed seed. It was written and run green against the keyword ``new_game`` before the
setup handler existed, and stays here unchanged: the handler's rolls stopped on their
first frame must draw exactly what ``new_game`` drew before (KTD-2).
"""

from __future__ import annotations

import functools
from collections import defaultdict
from pathlib import Path

import pytest

from engine.config_loader import load_game_config
from engine.interactions import Acknowledge, Heading, PromptText, RollFrame, ShowMessage, run
from engine.locations import HANDLERS
from engine.persistence import load_game, save_game
from engine.rng import Rng
from engine.strings import Resolver
from engine.turns import SETUP_HANDLER_KEY

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(CONFIG_ROOT)
_MAFIA = _CONFIG.module
new_game = _MAFIA.new_game

#: ``(kraft, intelligenz, brutalitaet, cash)`` per player of a four-player game, by
#: seed and the ``intelligence_or_30`` setting, as ``new_game`` rolled them before the
#: setup handler existed. A game of fewer players rolls the first rows.
_BASELINE = {
    (3, "faithful"): [
        (25, 62, 20, 6000),
        (45, 31, 10, 6500),
        (30, 62, 25, 5500),
        (45, 62, 50, 6500),
    ],
    (3, "intent"): [(25, 50, 20, 6000), (45, 15, 10, 6500), (30, 50, 25, 5500), (45, 50, 50, 6500)],
    (1986, "faithful"): [
        (30, 31, 40, 6500),
        (15, 62, 20, 6000),
        (15, 31, 20, 6000),
        (50, 30, 50, 7000),
    ],
    (1986, "intent"): [
        (30, 15, 40, 6500),
        (15, 40, 20, 6000),
        (15, 15, 20, 6000),
        (50, 10, 50, 7000),
    ],
}

#: Every switch of the catalogue at faithful: the map a game set up without choices holds.
_ALL_FAITHFUL = {
    "intelligence_or_30": "faithful",
    "shared_direction_memory": "faithful",
    "stale_bribe_price": "faithful",
    "flight_odds_by_seat": "faithful",
    "chief_bribe_negative_months": "faithful",
    "chief_bribe_empty_answer": "faithful",
    "gang_war_score_to_the_attacker": "faithful",
    "prison_brawl_zeroes_the_attackers_boss": "faithful",
}


def _players(count: int) -> list[tuple[str, str]]:
    return [(f"p{i}", f"g{i}") for i in range(count)]


def _stats(state) -> list[tuple[int, int, int, int]]:
    return [
        (p.roster[0].kraft, p.roster[0].intelligenz, p.roster[0].brutalitaet, p.ka)
        for p in state.players
    ]


@pytest.mark.parametrize("count", [2, 3, 4])
@pytest.mark.parametrize(("seed", "setting"), list(_BASELINE))
def test_new_game_rolls_the_pinned_starting_stats(seed, setting, count):
    state = new_game(
        seed=seed,
        end_year=1930,
        score_weight=1.0,
        players=_players(count),
        house_rules={"intelligence_or_30": setting},
    )
    assert _stats(state) == _BASELINE[seed, setting][:count]
    assert dict(state.config.house_rules) == {**_ALL_FAITHFUL, "intelligence_or_30": setting}


# --------------------------------------------------------------------------- #
# The setup handler                                                           #
# --------------------------------------------------------------------------- #
#: The house-rules offer's change key (config data the theme receives as a param).
_CHANGE_KEY = _MAFIA.house_rules.CHANGE_KEY


class Answers:
    """An input source for the setup handler, answering by interaction.

    ``texts`` maps a ``PromptText`` key to the answers it gets, in order (a key asked
    more often than it has answers fails). ``stop_at`` maps ``(roll key, player)`` to
    the frame (1-based) a roll stops on; every other roll stops on its first frame.
    ``seen`` keeps every interaction delivered, in order.
    """

    def __init__(self, texts=None, stop_at=None):
        self._texts = {key: list(answers) for key, answers in (texts or {}).items()}
        self._stop_at = dict(stop_at or {})
        self._frames: defaultdict = defaultdict(int)
        self.seen: list = []

    def __call__(self, interaction):
        self.seen.append(interaction)
        if isinstance(interaction, PromptText):
            queue = self._texts.get(interaction.key)
            assert queue, f"no answer left for {interaction!r}"
            return queue.pop(0)
        if isinstance(interaction, RollFrame):
            roll = (interaction.key, interaction.player)
            self._frames[roll] += 1
            return self._frames[roll] >= self._stop_at.get(roll, 1)
        return None  # Heading / ShowMessage / Acknowledge: delivered, not asked

    def of(self, kind, key=None):
        return [i for i in self.seen if isinstance(i, kind) and (key is None or i.key == key)]


def _names(count):
    """Answers for ``count`` players' name and gang-name prompts."""
    return {
        "setup.player_name_prompt": [f"p{i}" for i in range(count)],
        "setup.gang_name_prompt": [f"g{i}" for i in range(count)],
    }


def _run_setup(answers, seed=1, **prefill):
    handler = functools.partial(HANDLERS[SETUP_HANDLER_KEY], **prefill)
    result = run(handler, answers, state=None, rng=Rng(seed))
    assert result.status == "completed"
    assert result.effects == []  # setup applies no effects: there is no state yet
    return result.payload.returned


def _full_answers(count, setting="faithful", **extra):
    """Answers for a whole unfilled setup of ``count`` players."""
    texts = {
        "setup.end_year_prompt": ["1930"],
        "setup.score_weight_prompt": ["1"],
        "setup.player_count_prompt": [str(count)],
        **_names(count),
    }
    if setting == "intent":
        texts["setup.house_rules.offer"] = [_CHANGE_KEY]
        texts["setup.house_rules.prompt"] = ["1", ""]  # intelligence_or_30 is entry 1
    else:
        texts["setup.house_rules.offer"] = [""]
    texts.update(extra)
    return texts


def test_the_handler_is_registered_under_the_setup_key():
    # Each load of the config registers its own copy: compare by where it is defined.
    handler = HANDLERS[SETUP_HANDLER_KEY]
    assert (handler.__module__.rsplit(".", 2)[-2:], handler.__qualname__) == (
        ["handlers", "new_game"],
        "setup",
    )


@pytest.mark.parametrize("count", [1, 2, 3, 4])
@pytest.mark.parametrize(("seed", "setting"), [*_BASELINE, (7, "faithful"), (7, "intent")])
def test_rolls_stopped_on_their_first_frame_set_up_todays_game(seed, setting, count):
    """AE2: the handler, every roll stopped at once, == the keyword ``new_game``."""
    record = _run_setup(Answers(_full_answers(count, setting)), seed=seed)
    from_handler = new_game(record)
    today = new_game(
        seed=seed,
        end_year=1930,
        score_weight=1.0,
        players=_players(count),
        house_rules={"intelligence_or_30": setting},
    )
    assert from_handler == today


def test_the_record_holds_what_was_answered_and_rolled():
    record = _run_setup(Answers(_full_answers(2)), seed=3)
    assert record.end_year == 1930
    assert record.score_weight == 1.0
    assert dict(record.house_rules) == _ALL_FAITHFUL
    assert [(p.name, p.gang_name) for p in record.players] == _players(2)
    # The raw :350 intelligenz roll: 62 and 31 in the state are 50 and 15 OR 30.
    assert [(p.kraft, p.intelligenz, p.brutalitaet, p.cash) for p in record.players] == [
        (25, 50, 20, 6000),
        (45, 15, 10, 6500),
    ]


@pytest.mark.parametrize(
    ("answers", "count"),
    [
        (["0", "5", "x", "", "-1", "4.5", "1"], 1),  # :206 ifsz<1orsz>4goto205
        (["4"], 4),
        (["2.5"], 2),  # val("2.5")=2.5 passes :206; fori=1tosz runs i=1,2
        (["1.9"], 1),
    ],
)
def test_the_player_count_is_asked_again_until_it_is_1_to_4(answers, count):
    texts = _full_answers(count, **{"setup.player_count_prompt": answers})
    source = Answers(texts)
    record = _run_setup(source)
    assert len(record.players) == count
    assert len(source.of(PromptText, "setup.player_count_prompt")) == len(answers)


@pytest.mark.parametrize("bad", ["", "   ", "A" * 14, "  " + "A" * 14])
def test_an_empty_name_or_one_over_13_characters_is_asked_again(bad):
    texts = _full_answers(1)
    texts["setup.player_name_prompt"] = [bad, "A" * 13]
    texts["setup.gang_name_prompt"] = [bad, "B" * 13]
    source = Answers(texts)
    record = _run_setup(source)
    assert (record.players[0].name, record.players[0].gang_name) == ("A" * 13, "B" * 13)
    assert len(source.of(PromptText, "setup.player_name_prompt")) == 2
    assert len(source.of(PromptText, "setup.gang_name_prompt")) == 2


def test_a_name_loses_its_leading_and_trailing_spaces_as_input_drops_them():
    # C64 INPUT returns "  ab  " as "ab" (VICE capture); 13 letters inside spaces fit.
    texts = _full_answers(1)
    texts["setup.player_name_prompt"] = ["  " + "A" * 13 + "  "]
    texts["setup.gang_name_prompt"] = [" the outfit "]
    record = _run_setup(Answers(texts))
    assert (record.players[0].name, record.players[0].gang_name) == ("A" * 13, "the outfit")


def test_the_name_prompt_numbers_the_player():
    source = Answers(_full_answers(3))
    _run_setup(source)
    prompts = source.of(PromptText, "setup.player_name_prompt")
    assert [p.params["number"] for p in prompts] == [1, 2, 3]


def test_a_roll_stopped_on_its_third_frame_keeps_the_third_draw():
    source = Answers(_full_answers(1), stop_at={("setup.roll.kraft", 0): 3})
    record = _run_setup(source, seed=4)
    frames = source.of(RollFrame, "setup.roll.kraft")
    assert len(frames) == 3
    rng = Rng(4)
    draws = [rng.range(9) * 5 + 10 for _ in range(3)]
    assert len(set(draws)) == 3  # a seed whose kept draw tells the frames apart
    assert [f.params["value"] for f in frames] == draws
    assert record.players[0].kraft == draws[2] == frames[-1].params["value"]
    # The next roll draws on from there: intelligenz is the fourth draw.
    assert source.of(RollFrame, "setup.roll.intelligenz")[0].params["value"] == (
        rng.range(9) * 5 + 10
    )


@pytest.mark.parametrize("setting", ["faithful", "intent"])
def test_the_intelligenz_frame_shows_the_roll_and_the_state_follows_the_house_rule(setting):
    for seed in range(40):
        source = Answers(_full_answers(1, setting))
        record = _run_setup(source, seed=seed)
        shown = source.of(RollFrame, "setup.roll.intelligenz")[0].params["value"]
        assert record.players[0].intelligenz == shown
        stored = new_game(record).players[0].roster[0].intelligenz
        assert stored == (shown | 30 if setting == "faithful" else shown)


def test_the_eigenschaften_screen_in_source_order():
    source = Answers(_full_answers(1))
    record = _run_setup(source, seed=3)
    screen = [
        (type(i).__name__, i.key)
        for i in source.seen
        if isinstance(i, (Heading, RollFrame, ShowMessage, Acknowledge))
    ]
    assert screen == [
        ("Heading", "setup.eigenschaften"),  # :300
        ("RollFrame", "setup.roll.kraft"),  # :310
        ("RollFrame", "setup.roll.intelligenz"),  # :311
        ("RollFrame", "setup.roll.brutalitaet"),  # :312
        ("ShowMessage", "setup.energie"),  # :313
        ("ShowMessage", "setup.kapital"),  # :316
        ("Acknowledge", "setup.eigenschaften"),  # :316 goto1100
    ]
    assert source.of(ShowMessage, "setup.energie")[0].params == {"energy": 5}
    assert source.of(ShowMessage, "setup.kapital")[0].params == {"cash": record.players[0].cash}


def test_each_players_screens_come_after_their_names():
    source = Answers(_full_answers(2))
    _run_setup(source)
    order = [
        i.key
        for i in source.seen
        if i.key in {"setup.player_name_prompt", "setup.gang_name_prompt"} or isinstance(i, Heading)
    ]
    assert (
        order
        == [
            "setup.player_name_prompt",
            "setup.gang_name_prompt",
            "setup.eigenschaften",
        ]
        * 2
    )


def test_per_player_interactions_carry_that_players_index():
    source = Answers(_full_answers(3))
    _run_setup(source)
    per_player = {
        "setup.player_name_prompt",
        "setup.gang_name_prompt",
        "setup.eigenschaften",
        "setup.roll.kraft",
        "setup.roll.intelligenz",
        "setup.roll.brutalitaet",
        "setup.energie",
        "setup.kapital",
    }
    for interaction in source.seen:
        if interaction.key in per_player:
            assert interaction.player is not None, interaction
        else:
            assert interaction.player is None, interaction
    headings = source.of(Heading, "setup.eigenschaften")
    assert [h.player for h in headings] == [0, 1, 2]
    names = source.of(PromptText, "setup.player_name_prompt")
    assert [n.player for n in names] == [0, 1, 2]


def test_setup_asks_in_source_order():
    source = Answers(_full_answers(1))
    _run_setup(source)
    asked = [i.key for i in source.seen if isinstance(i, PromptText)]
    assert asked == [
        "setup.end_year_prompt",  # :170
        "setup.score_weight_prompt",  # :175
        "setup.house_rules.offer",  # KTD-5: after the score weight
        "setup.player_count_prompt",  # :205
        "setup.player_name_prompt",  # :210
        "setup.gang_name_prompt",  # :215
    ]


@pytest.mark.parametrize(
    ("key", "answers", "value"),
    [
        ("setup.end_year_prompt", ["1927", "1979", "abc", "", "nan", "inf", "1950.9"], 1950),
        ("setup.score_weight_prompt", ["0.05", "2.1", "x", "0.5"], 0.5),
    ],
)
def test_end_year_and_score_weight_are_asked_again_until_in_range(key, answers, value):
    source = Answers(_full_answers(1, **{key: answers}))
    record = _run_setup(source)
    field = "end_year" if key == "setup.end_year_prompt" else "score_weight"
    assert getattr(record, field) == value
    assert len(source.of(PromptText, key)) == len(answers)


def test_prefilled_players_skip_count_and_names_but_keep_the_rolls():
    source = Answers({})
    record = _run_setup(
        source,
        seed=3,
        end_year=1930,
        score_weight=1.0,
        players=_players(2),
        house_rules={},
    )
    assert source.of(PromptText) == []
    assert len(source.of(RollFrame)) == 6
    assert len(source.of(Acknowledge)) == 2
    assert new_game(record) == new_game(
        seed=3, end_year=1930, score_weight=1.0, players=_players(2)
    )


def test_prefilled_house_rules_skip_the_house_rules_step():
    texts = _full_answers(1)
    del texts["setup.house_rules.offer"]
    record = _run_setup(Answers(texts), house_rules={"intelligence_or_30": "intent"})
    assert record.house_rules["intelligence_or_30"] == "intent"


@pytest.mark.parametrize(
    "prefill",
    [
        {"end_year": 1927},
        {"score_weight": 2.5},
        {"players": []},
        {"players": _players(5)},
        {"players": [("", "g")]},
        {"players": [("p", "B" * 14)]},
        {"house_rules": {"no_such_rule": "intent"}},
        {"house_rules": {"intelligence_or_30": "sometimes"}},
    ],
)
def test_a_bad_prefill_raises(prefill):
    with pytest.raises(ValueError):
        _run_setup(Answers(_full_answers(1)), **prefill)


def test_the_house_rules_step_toggles_and_shows_the_list_again():
    texts = _full_answers(1)
    texts["setup.house_rules.offer"] = [_CHANGE_KEY.upper()]
    texts["setup.house_rules.prompt"] = ["1", "x", "99", "2", "1", ""]
    source = Answers(texts)
    record = _run_setup(source)
    assert record.house_rules["intelligence_or_30"] == "faithful"  # toggled twice
    assert record.house_rules["shared_direction_memory"] == "intent"
    lists = source.of(PromptText, "setup.house_rules.prompt")
    assert len(lists) == 6
    first, last = lists[0].params["rules"], lists[-1].params["rules"]
    assert first[0] == {"number": 1, "id": "intelligence_or_30", "setting": "faithful"}
    assert last[1] == {"number": 2, "id": "shared_direction_memory", "setting": "intent"}
    assert source.of(PromptText, "setup.house_rules.offer")[0].params == {"key": _CHANGE_KEY}


def test_a_game_set_up_by_the_handler_saves_and_loads(tmp_path):
    record = _run_setup(Answers(_full_answers(2)), seed=5)
    state = new_game(record)
    path = tmp_path / "game.jsonl"
    save_game(path, state, registries=_CONFIG.registries, effect_log=[], rng_log=[], seed=5)
    assert load_game(path, _CONFIG.registries).state == state


def test_every_key_the_handler_yields_resolves_in_the_classic_theme():
    resolver = Resolver.from_directory(CONFIG_ROOT / "themes" / "classic")
    texts = _full_answers(2)
    texts["setup.house_rules.offer"] = [_CHANGE_KEY]
    texts["setup.house_rules.prompt"] = [""]
    source = Answers(texts)
    _run_setup(source)
    keys = {i.key for i in source.seen}
    assert keys >= {"setup.roll.kraft", "setup.kapital", "setup.house_rules.prompt"}
    for interaction in source.seen:
        resolver.resolve(interaction.key, dict(interaction.params))


@pytest.mark.parametrize(
    ("key", "params", "text"),
    [
        ("setup.player_count_prompt", {}, "spieleranzahl:"),  # :205
        ("setup.player_name_prompt", {"number": 2}, "name spieler 2:"),  # :210 "i"{left}:
        ("setup.gang_name_prompt", {}, "bandenname:"),  # :215
        ("setup.eigenschaften", {}, "eigenschaften:"),  # :300
        ("setup.roll.kraft", {"value": 25}, "kraft: 25 "),  # :310 + :350 print x;
        ("setup.roll.intelligenz", {"value": 10}, "intelligenz: 10 "),  # :311
        ("setup.roll.brutalitaet", {"value": 50}, "brutalitaet: 50 "),  # :312
        ("setup.energie", {"energy": 5}, "energie: 5"),  # :313
        ("setup.kapital", {"cash": 6500}, "kapital: 6500 $"),  # :316
    ],
)
def test_the_classic_theme_prints_the_screen_as_the_source(key, params, text):
    resolver = Resolver.from_directory(CONFIG_ROOT / "themes" / "classic")
    assert resolver.resolve(key, params) == text

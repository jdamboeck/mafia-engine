"""Tests for the sgl shell + classic-theme strings (``mf-prg.bas:17000-17592``).

The checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, the keys the handlers emit in their behavioural runs
(``tests/test_sgl.py``) resolve in the classic theme and reach every sgl string,
and the text is verbatim from the research corpus (location-dialogue.yaml, sgl; the
lines the corpus misses, :17006, :17008-17009 and :17016, from the listing).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.locations import load_location
from engine.strings import Resolver
from tests.helpers import assert_captured_keys_resolve, location_strings
from tests.test_sgl import (
    _BOSS,
    _EXITS,
    _SURRENDER,
    _SURRENDER_FIGHT,
    _TAKE,
    _run,
    _state,
    _won_jack_fight,
)
from tests.text_fold import fixture_value

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

_CONFIG = load_game_config(_CONFIG_DIR)
_SHELL = _CONFIG_DIR / "content" / "locations" / "sgl.yaml"
_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "sgl.yaml"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"
_OPTIONS = ("threat", "sob_story", "protection", "fake_police")


def _get(data, dotted):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _shell():
    return load_location(yaml.safe_load(_SHELL.read_text(encoding="utf-8")))


def test_sgl_shell_resolves_four_handlers_and_a_guardless_leave():
    loc = _shell()
    assert loc.key == "sgl"
    assert [o.id for o in loc.options] == [*_OPTIONS, "leave"]
    by_id = {o.id: o for o in loc.options}
    for opt_id in _OPTIONS:
        assert by_id[opt_id].handler is _CONFIG.handlers[f"sgl.{opt_id}"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_sgl_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sgl.entry_prompt")
    for opt_id in (*_OPTIONS, "leave"):
        assert _get(data, f"locations.sgl.menu.{opt_id}")


_WEAK_BOSS = replace(_BOSS, attrs={**_BOSS.attrs, "brutalitaet": 29})

#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_sgl.py`` (id, option, state fields, answers, rng draws, whether Jack's
#: fight is a stand-in win), the big payout's reply for each option (:17510), the boss
#: who makes no impression (:17016) and Jack leaving after a won fight (:17221).
_RUNS = [(*case[:5], False) for case in _EXITS] + [
    ("17510-threat-pays", "threat", {"ln": 7}, (_TAKE,), (1, 199), False),
    ("17510-sob-story", "sob_story", {"ln": 1}, (_TAKE,), (1, 199), False),
    ("17510-protection-enough", "protection", {"ln": 2}, (_TAKE,), (1, 199), False),
    ("17510-fake-police-forged", "fake_police", {"ln": 6}, (_TAKE,), (1, 199), False),
    (
        "17016-no-impression",
        "threat",
        {"ln": 1, "roster": (_WEAK_BOSS,)},
        (_SURRENDER_FIGHT, _SURRENDER),
        (0, 0, 0),
        False,
    ),
    ("17221-jack-leaves", "protection", {"ln": 5}, (_TAKE,), (1, 0), True),
]


def _captured(monkeypatch, key, fields, answers, draws, jack_won) -> list:
    with monkeypatch.context() as patch:
        if jack_won:
            _won_jack_fight(patch, None)
        return _run(key, _state(**fields), answers=answers, draws=draws)[1].seen


@pytest.mark.parametrize(
    ("key", "fields", "answers", "draws", "jack_won"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_sgl_run_emits_resolve_in_the_classic_theme(
    monkeypatch, key, fields, answers, draws, jack_won
):
    assert_captured_keys_resolve(_captured(monkeypatch, key, fields, answers, draws, jack_won))


def test_the_sgl_runs_reach_every_sgl_string(monkeypatch):
    reached = set().union(
        *(assert_captured_keys_resolve(_captured(monkeypatch, *r[1:])) for r in _RUNS)
    )
    assert location_strings("sgl") - reached == set()


def test_the_classic_theme_prints_each_sgl_line_as_the_source():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.sgl.milksop": ({}, "'Verschwinde, du Milchgesicht!'"),
        "locations.sgl.police_waiting": ({}, "Vor dem Laden erwartet dich die Polizei!"),
        "locations.sgl.no_impression": ({}, "Du schindest keinen Eindruck..."),
        "locations.sgl.calls_police": ({}, "Der Kerl ruft die Polizei!!!"),
        "locations.sgl.sob_refused": ({}, "'Sorge doch selbst für deine Alte!'"),
        "locations.sgl.jack_warning": ({}, "'Das wird aber Narben-Jack interessieren!'"),
        "locations.sgl.jack_leaves": ({}, "Jack verzieht sich erstmal aus diesem Gebiet..."),
        "locations.sgl.fake_police_refused": (
            {},
            "'Ach, die alte Bullen-Masche! Damit neppt ihr keinen mehr!'",
        ),
        "locations.sgl.reply_pays": (
            {"p": 912},
            "'I..i..ich z..zahle ja schon! Hi..hier sind 912 $!'",
        ),
        "locations.sgl.reply_sob": (
            {"p": 312},
            "'Deine arme Ma (schnief)! Gib ihr die 312 $ hier!'",
        ),
        "locations.sgl.reply_enough": ({"p": 850}, "'Ich habe 850 Dollar, reicht das?'"),
        "locations.sgl.reply_forged": (
            {"p": 999},
            "'Verdammt! Wer hat mir nur Blüten für 999 $ angedreht...?'",
        ),
        "locations.sgl.reply_small": ({"p": 150}, "'Ich habe leider nur 150 $!'"),
        "locations.sgl.after_menu": ({}, "Was machst du:"),
        "locations.sgl.after_take": ({}, "1 Angebotenes Geld nehmen"),
        "locations.sgl.after_demolish": ({}, "2 Laden demolieren"),
        "locations.sgl.after_kill": ({}, "3 Besitzer fertigmachen"),
        "locations.sgl.thugs_called": (
            {},
            "'Saubande! Das werdet ihr büßen!' (Der Kerl winkt ein paar Schläger heran!)",
        ),
        "locations.sgl.demolished": (
            {"p": 342},
            "Du hast Kleinholz aus dem Laden gemacht. In der Kasse waren 342 $!",
        ),
        "locations.sgl.owner_arms": (
            {"weapon": "Maschinenpistole"},
            "Er verspricht dir einen schönen Grabstein und lädt seine Maschinenpistole!",
        ),
        "locations.sgl.owner_dead": (
            {"p": 250},
            "Der aufmüpfige Kerl ist hin. 250 $ hatte er in der Tasche!",
        ),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_sgl_strings_verbatim():
    """The C64-text fixture\'s menu and entry text, byte for byte from
    location-dialogue.yaml (sgl), the 40-column wrap padding included."""
    assert fixture_value("locations.sgl.entry_prompt") == "'TAG, MISTER. WAS DARF'S DENN SEIN?'"
    assert fixture_value("locations.sgl.menu.threat") == (
        "'DEN ZASTER HER, ABER SCHNELL! SONST    GEHT DER BOELLER HIER LOS!'"
    )
    assert fixture_value("locations.sgl.menu.sob_story") == (
        "'BITTE, BITTE EIN PAAR DOLLARS FUER     MEINE KRANKE MA! SIE IST SOOOO ARM!'"
    )
    assert fixture_value("locations.sgl.menu.protection") == (
        "'WIR SCHUETZEN DICH FUER EINE KLEINE    SPENDE VOR DEN BOESEN GANGSTERN!'"
    )
    assert fixture_value("locations.sgl.menu.fake_police") == (
        "'POLIZEI! IN IHRER KASSE IST FALSCH-    GELD VERSTECKT, DAS IST KONFISZIERT!'"
    )
    assert fixture_value("locations.sgl.menu.leave") == "HEUTE MAL NICHT KASSIEREN"


def test_sgl_screen_text_in_today_s_spelling():
    """The live classic theme's sgl title, entry and menu text: the disk file's
    words in today's spelling, the wrap padding one space (the fold check ties
    them to the C64-text fixture above)."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sgl.title") == (
        "Einfacher Laden (wie geschaffen zum Schutzgeld eintreiben!)"
    )
    assert _get(data, "locations.sgl.entry_prompt") == "'Tag, Mister. Was darf's denn sein?'"
    assert _get(data, "locations.sgl.menu.threat") == (
        "'Den Zaster her, aber schnell! Sonst geht der Böller hier los!'"
    )
    assert _get(data, "locations.sgl.menu.sob_story") == (
        "'Bitte, bitte ein paar Dollars für meine kranke Ma! Sie ist soooo arm!'"
    )
    assert _get(data, "locations.sgl.menu.protection") == (
        "'Wir schützen dich für eine kleine Spende vor den bösen Gangstern!'"
    )
    assert _get(data, "locations.sgl.menu.fake_police") == (
        "'Polizei! In Ihrer Kasse ist Falschgeld versteckt, das ist konfisziert!'"
    )
    assert _get(data, "locations.sgl.menu.leave") == "Heute mal nicht kassieren"


def test_the_sgl_doors_open_the_sgl_shell_one_per_tile():
    """Cells 49, 145, 340, 443, 538, 772, 790, 874 and 905 are sgl's doors (``la=7``,
    ``ln`` 1..9)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "sgl"]
    cells = (49, 145, 340, 443, 538, 772, 790, 874, 905)
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [
        (cell, 7, ln) for ln, cell in enumerate(cells, start=1)
    ]

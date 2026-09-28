"""Tests for the sgl shell + classic-theme strings (``mf-prg.bas:17000-17592``).

The four checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, every key the handlers emit resolves with its params,
and the text is verbatim from the research corpus (location-dialogue.yaml, sgl; the
lines the corpus misses, :17006, :17008-17009 and :17016, from the listing).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location
from engine.strings import Resolver

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

load_game_config(_CONFIG_DIR)
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
        assert by_id[opt_id].handler is HANDLERS[f"sgl.{opt_id}"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_sgl_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sgl.entry_prompt")
    for opt_id in (*_OPTIONS, "leave"):
        assert _get(data, f"locations.sgl.menu.{opt_id}")


def test_every_key_the_handlers_emit_resolves_with_its_params():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.sgl.milksop": ({}, "'verschwinde, du milchgesicht!'"),
        "locations.sgl.police_waiting": ({}, "vor dem laden erwartet dich die\npoliyei!"),
        "locations.sgl.no_impression": ({}, "du schindest keinen eindruck..."),
        "locations.sgl.calls_police": ({}, "der kerl ruft die polizei!!!"),
        "locations.sgl.sob_refused": ({}, "'sorge doch selbst fuer deine alte!'"),
        "locations.sgl.jack_warning": ({}, "'das wird aber narben-jack interes-\nsieren!'"),
        "locations.sgl.jack_leaves": ({}, "jack verzieht sich erstmal aus diesem\ngebiet..."),
        "locations.sgl.fake_police_refused": (
            {},
            "'ach, die alte bullen-masche! damit\nneppt ihr keinen mehr!'",
        ),
        "locations.sgl.reply_pays": (
            {"p": 912},
            "'i..i..ich z..zahle ja schon! hi..hier\nsind 912$!'",
        ),
        "locations.sgl.reply_sob": (
            {"p": 312},
            "'deine arme ma (schnief)! gib ihr die\n 312$ hier!'",
        ),
        "locations.sgl.reply_enough": ({"p": 850}, "'ich habe 850 dollar, reicht das?'"),
        "locations.sgl.reply_forged": (
            {"p": 999},
            "'verdammt! wer hat mir nur blueten\nfuer 999$ angedreht...?'",
        ),
        "locations.sgl.reply_small": ({"p": 150}, "'ich habe leider nur 150$!"),
        "locations.sgl.after_menu": ({}, "was machst du:"),
        "locations.sgl.after_take": ({}, "1 angebotenes geld nehmen"),
        "locations.sgl.after_demolish": ({}, "2 laden demolieren"),
        "locations.sgl.after_kill": ({}, "3 besitzer fertigmachen"),
        "locations.sgl.thugs_called": (
            {},
            "'saubande! das werdet ihr buessen!'\n(der kerl winkt ein paar schlaeger\nheran!)",
        ),
        "locations.sgl.demolished": (
            {"p": 342},
            "du hast kleinholz aus dem laden\ngemacht. in der kasse waren 342$!",
        ),
        "locations.sgl.owner_arms": (
            {"weapon": "maschinenpistole"},
            "er verspricht dir einen schoenen grab-\nstein und laedt seine maschinenpistole!",
        ),
        "locations.sgl.owner_dead": (
            {"p": 250},
            "der aufmuepfige kerl ist hin. 250$\nhatte er der tasche!",
        ),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_sgl_strings_verbatim():
    """Menu and entry text, byte for byte from location-dialogue.yaml (sgl), the
    40-column wrap padding included."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sgl.entry_prompt") == "'TAG, MISTER. WAS DARF'S DENN SEIN?'"
    assert _get(data, "locations.sgl.menu.threat") == (
        "'DEN ZASTER HER, ABER SCHNELL! SONST    GEHT DER BOELLER HIER LOS!'"
    )
    assert _get(data, "locations.sgl.menu.sob_story") == (
        "'BITTE, BITTE EIN PAAR DOLLARS FUER     MEINE KRANKE MA! SIE IST SOOOO ARM!'"
    )
    assert _get(data, "locations.sgl.menu.protection") == (
        "'WIR SCHUETZEN DICH FUER EINE KLEINE    SPENDE VOR DEN BOESEN GANGSTERN!'"
    )
    assert _get(data, "locations.sgl.menu.fake_police") == (
        "'POLIZEI! IN IHRER KASSE IST FALSCH-    GELD VERSTECKT, DAS IST KONFISZIERT!'"
    )
    assert _get(data, "locations.sgl.menu.leave") == "HEUTE MAL NICHT KASSIEREN"


def test_the_sgl_doors_open_the_sgl_shell_one_per_tile():
    """Cells 49, 145, 340, 443, 538, 772, 790, 874 and 905 are sgl's doors (``la=7``,
    ``ln`` 1..9)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "sgl"]
    cells = (49, 145, 340, 443, 538, 772, 790, 874, 905)
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [
        (cell, 7, ln) for ln, cell in enumerate(cells, start=1)
    ]

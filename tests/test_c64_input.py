"""What the C64 ``INPUT`` stores for a number typed at a numeric prompt.

The oracle is a capture from the real C64 ROM: ``tests/fixtures/c64_input/input_cases.bas``
was run in VICE 3.10 (``x64sc``) and wrote ``tests/fixtures/c64_input/vice_capture.txt``.
For each answer in its ``DATA`` the program pokes the answer and a RETURN into the
keyboard buffer (631-640, count at 198) and runs ``input y`` -- the same statement as
``mf-prg.bas:12027``/``:12060``'s ``inputy`` -- then writes ``[answer]:str$(y)``.

Then it plays the pub's buy (``:12028-12035``) and sell (``:12061-12075``) lines, as
written, on an ``INPUT`` of ``-5``: with ``ka(sp)=1000``, ``ta(sp)=0``, offer ``x=50``
and price ``p=7`` the buy writes ``BUY:str$(ka(sp))str$(ta(sp))``; with ``ka(sp)=1000``,
``ta(sp)=0`` and price ``x=20`` the sell writes ``SELL:...``. Letters came out uppercased.
CI never runs VICE; these tests read the committed capture.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import data.game_configs.mafia_1920s.state as game
from data.game_configs.mafia_1920s.effects import BarrelChange
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.state import Contraband
from engine.config_loader import load_game_config
from engine.effects import MoneyChange
from engine.interactions import PromptInt
from engine.locations import HANDLERS
from engine.state import Clock, Config, GameState, Player
from tests.helpers import StubRng, run_pure, scripted

FIXTURE = Path(__file__).parent / "fixtures" / "c64_input"
CAPTURE = FIXTURE / "vice_capture.txt"
PROGRAM = FIXTURE / "input_cases.bas"

load_game_config(Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s")


def _read_capture() -> tuple[dict[str, str], dict[str, str]]:
    lines = CAPTURE.read_text(encoding="ascii").splitlines()
    assert lines[-1] == "END", "the capture is cut short"
    stored: dict[str, str] = {}
    trades: dict[str, str] = {}
    for line in lines[:-1]:
        match = re.fullmatch(r"\[(.*)\]:(.*)", line)
        if match:
            stored[match[1]] = match[2]
        else:
            name, value = line.split(":", 1)
            trades[name] = value
    return stored, trades


STORED, TRADES = _read_capture()

#: What ``y`` holds after each answer, as ``str$(y)`` printed it (a leading space for
#: a value of at least 0, the sign otherwise).
EXPECTED = {
    "-3": "-3",
    "-0": " 0",
    "- 3": "-3",
    "-3.5": "-3.5",
    "-99999": "-99999",
    "3": " 3",
    " -3": "-3",
    "+3": " 3",
    "-5": "-5",
    "0": " 0",
    "-99999999": "-99999999",
}


def test_the_capture_answers_exactly_the_programs_data():
    data = next(ln for ln in PROGRAM.read_text("ascii").splitlines() if ln.startswith("1000 "))
    answers = re.findall(r'"([^"]*)"', data)
    assert answers[-1] == "end"
    assert list(STORED) == answers[:-1]


@pytest.mark.parametrize(("answer", "value"), EXPECTED.items())
def test_input_stores_the_number_typed(answer, value):
    """A negative answer is stored as typed: INPUT refuses no sign."""
    assert STORED[answer] == value


def test_the_pubs_buy_lines_sell_barrels_on_a_negative_count():
    """:12028-12035 on y=-5: ka 1000-7*(-5)=1035, ta 0+(-5)=-5."""
    assert TRADES["BUY"] == " 1035-5"


def test_the_pubs_sell_lines_buy_barrels_on_a_negative_count():
    """:12061-12075 on y=-5: ka 1000+(-5)*20=900, ta 0-(-5)=5."""
    assert TRADES["SELL"] == " 900 5"


#: The answers the port's number prompt reads differently from the C64: a fraction
#: (asked again; whole numbers only, a departure noted in content/house_rules.yaml) and a
#: sign set apart from its digits by a space (asked again; the C64 reads it as -3).
_READ_DIFFERENTLY = {"-3.5", "- 3"}


def _sell(*answers: str):
    """The pub's sell (faithful ``c64_input_negatives``, 20 barrels, 15 $ a barrel),
    answered with the typed strings, through the real driver."""
    st = _pub_state()
    source = scripted(*answers)
    result = run_pure(HANDLERS["pub.drink"], source, state=st, rng=StubRng(1, 15))
    return result, [i for i in source.seen if isinstance(i, PromptInt)]


def _pub_state() -> GameState:
    player = Player(
        name="p0",
        ka=1000,
        roster=(Gangster(name="g0"),),
        last_location=2,
        values=game.values_of(Contraband(alcohol_barrels=20)),
    )
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(
            formula_params={"pub_alcohol_sell_price_min": 10, "pub_alcohol_sell_price_max": 29},
            house_rules={"c64_input_negatives": "faithful"},
        ),
    )


@pytest.mark.parametrize("answer", sorted(set(EXPECTED) - _READ_DIFFERENTLY))
def test_the_pub_sells_the_count_the_c64_input_stored(answer):
    """Typed at the pub's sell prompt (:12060), each whole number trades the count the
    C64 stored for it."""
    y = int(STORED[answer])
    result, prompts = _sell(answer)
    assert len(prompts) == 1
    assert result.effects == ([] if y == 0 else [MoneyChange(15 * y), BarrelChange(-y)])


@pytest.mark.parametrize("answer", sorted(_READ_DIFFERENTLY))
def test_the_port_asks_again_where_the_c64_reads_a_number(answer):
    result, prompts = _sell(answer, "0")
    assert len(prompts) == 2
    assert result.effects == []

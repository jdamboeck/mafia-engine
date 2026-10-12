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

import ast
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


#: The answer the port's number prompt reads differently from the C64: a fraction
#: (asked again; whole numbers only, a departure noted in content/house_rules.yaml).
_READ_DIFFERENTLY = {"-3.5"}


def _sell(*answers: str, rule: str = "faithful"):
    """The pub's sell (``c64_input_negatives`` set to ``rule``, 20 barrels, 15 $ a
    barrel), answered with the typed strings, through the real driver."""
    st = _pub_state(rule)
    source = scripted(*answers)
    result = run_pure(HANDLERS["pub.drink"], source, state=st, rng=StubRng(1, 15))
    return result, [i for i in source.seen if isinstance(i, PromptInt)]


def _pub_state(rule: str = "faithful") -> GameState:
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
            house_rules={"c64_input_negatives": rule},
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


def test_the_pub_sells_the_count_the_c64_reads_for_a_spaced_sign():
    """The capture's ``[- 3]:-3`` row: the C64 ``INPUT`` reads a sign set apart from
    its digits as the signed number, and the faithful sell (:12060 ``inputy``) trades
    -3 on it (:12075 ``ka(sp)=ka(sp)+y*x:ta(sp)=ta(sp)-y``)."""
    assert STORED["- 3"] == "-3"
    result, prompts = _sell("- 3")
    assert len(prompts) == 1
    assert result.effects == [MoneyChange(-45), BarrelChange(3)]


@pytest.mark.parametrize("answer", ["- 3", "-3"])
def test_intent_asks_a_negative_count_again_spaced_or_not(answer):
    result, prompts = _sell(answer, "0", rule="intent")
    assert len(prompts) == 2
    assert result.effects == []


def _capital(answer: str, rule: str):
    """kdh's capital screen (:15208 ``input"{down}gern";x$:x=val(x$)``), 1000 $ in the
    shop, answered with ``answer`` then 0."""
    from data.game_configs.mafia_1920s.state import Business

    player = Player(
        name="p0",
        ka=0,
        roster=(Gangster(name="g0"),),
        last_location=1,
        values=game.values_of(Business(shop_tile=1, shop_capital=1000)),
    )
    st = GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(
            formula_params={"kdh_capital_max": 5000},
            house_rules={"c64_input_negatives": rule},
        ),
    )
    source = scripted(answer, "0")
    result = run_pure(HANDLERS["kdh.capital"], source, state=st, rng=StubRng())
    return result, [i for i in source.seen if isinstance(i, PromptInt)]


def test_a_spaced_sign_withdraws_capital_when_faithful():
    """A prompt taking negatives either way reads ``- 3`` as -3 under faithful."""
    from data.game_configs.mafia_1920s.effects import ShopChange

    result, prompts = _capital("- 300", "faithful")
    assert len(prompts) == 1
    assert result.effects == [MoneyChange(300), ShopChange(capital_delta=-300)]


def test_a_spaced_sign_is_asked_again_under_intent():
    """Intent reads numbers as Python does: ``- 300`` is asked again, and the 0 after
    it leaves (:15208 ``ifx=0thenreturn``)."""
    result, prompts = _capital("- 300", "intent")
    assert len(prompts) == 2
    assert result.effects == []


def _calls(name: str) -> list[tuple[str, int, ast.Call]]:
    """Every call of the bare name ``name`` in the config's Python files."""
    config = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
    calls = []
    for path in sorted(config.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
            ):
                calls.append((path.relative_to(config).as_posix(), node.lineno, node))
    return calls


def test_every_line_read_number_prompt_is_built_by_the_number_prompt_helper():
    """A C64 ``INPUT`` reads every number prompt the config asks as a line, so each
    one takes the spaced sign under faithful: only ``house_rules.number_prompt``
    builds a ``PromptInt`` read as a line; a one-key prompt (``getx$``) cannot
    receive a sign and is built directly."""
    calls = _calls("PromptInt")
    # 15 helper calls, the helper's own PromptInt and 4 one-key prompts today.
    assert len(_calls("number_prompt")) + len(calls) >= 20, "the scan is blind"
    direct = [
        f"{path}:{line}"
        for path, line, call in calls
        if path != "house_rules.py"
        and not any(
            kw.arg == "read" and isinstance(kw.value, ast.Name) and kw.value.id == "READ_KEY"
            for kw in call.keywords
        )
    ]
    assert direct == []

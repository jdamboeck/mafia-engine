"""The C64's 5-byte float and the ``:1013`` score truncation it drives.

The oracle is a capture from the real C64 ROM: ``tests/fixtures/c64_float/trunc_cases.bas``
was run in VICE 3.10 (``x64sc``) and wrote ``tests/fixtures/c64_float/vice_capture.txt``.
For each ``g`` the program stores ``t=int(g*100)/100`` -- ``mf-prg.bas:1013``'s
``gf(sp)=int(gf(sp)*100)/100`` -- and writes ``label:G:T:str$(g):str$(t)``, where ``G``
and ``T`` are the 5 bytes of ``g`` and ``t`` as hex, peeked from the variable
table (``g`` and ``t`` are its first two variables). Labels came out uppercased. A zero
holds only its exponent byte (``00``); the C64 leaves stale mantissa bytes behind it.

The rows come in four groups:

- **literals** (``25.4``, ``-.125``, ...): ``g=val(label)``.
- **sums** (``S<i>-<n>``): ``:1160``'s ``gf(sp)=gf(sp)+(x*x8)``, on ``g``, with the cap at 100 and floor at
  0, for the ``x8`` and ``x`` lists in the program's ``DATA``, each row then truncated
  and carried on (``g=t``), as a score grows over turns.
- **repeats** (``D<i>-<n>``): ``:1013`` applied again to its own result until it holds.
- **the sweep**: one line per ``k`` in ``0..10000``, ``g=k/100`` -- every whole-cent
  score from 0 to 100 -- with ``=`` where ``t=g`` and ``T`` where the C64 changed it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.handlers.turn import truncated_score
from engine.c64_numbers import (
    c64_add,
    c64_add_product,
    c64_bytes,
    c64_divide,
    c64_float,
    c64_int_divide,
    c64_multiply,
    c64_str,
    c64_val,
)

FIXTURE = Path(__file__).parent / "fixtures" / "c64_float"
CAPTURE = FIXTURE / "vice_capture.txt"
PROGRAM = FIXTURE / "trunc_cases.bas"


@dataclass(frozen=True)
class Row:
    label: str
    g: bytes
    t: bytes
    g_str: str
    t_str: str


def _decode(raw: bytes) -> float:
    """The value a C64 variable's 5 bytes hold (the test's own reading of the format)."""
    if raw[0] == 0:
        return 0.0
    mantissa = int.from_bytes(bytes([raw[1] | 0x80]) + raw[2:], "big")
    value = mantissa * 2.0 ** (raw[0] - 129 - 31)
    return -value if raw[1] & 0x80 else value


def _same(got: bytes, captured: bytes) -> bool:
    """Byte equality, with a zero compared by its exponent byte alone."""
    return got == captured or (got[0] == 0 and captured[0] == 0)


def _read_capture() -> tuple[dict[str, Row], list[bytes | None]]:
    rows: dict[str, Row] = {}
    sweep: list[bytes | None] = []
    lines = CAPTURE.read_text(encoding="ascii").splitlines()
    assert lines[-1] == "END", "the capture is cut short"
    for line in lines[:-1]:
        if ":" in line:
            label, g, t, g_str, t_str = line.split(":")
            rows[label] = Row(label, bytes.fromhex(g), bytes.fromhex(t), g_str, t_str)
        else:
            sweep.append(None if line == "=" else bytes.fromhex(line))
    return rows, sweep


ROWS, SWEEP = _read_capture()
LITERALS = [r for r in ROWS.values() if not re.fullmatch(r"[SD]\d+-\d+", r.label)]


def _data_line(number: int) -> list[float]:
    """The numbers of the program's ``DATA`` line ``number``."""
    line = next(ln for ln in PROGRAM.read_text("ascii").splitlines() if ln.startswith(f"{number} "))
    return [float(x) for x in line.split("data", 1)[1].split(",")]


X8S = _data_line(1100)  # the score weights the sums use, x8
STEPS = _data_line(1110)  # the score changes they add, x


def test_the_capture_is_complete():
    assert len(SWEEP) == 10001
    assert len(LITERALS) == 39
    assert sum(1 for r in ROWS if r.startswith("S")) == len(X8S) * len(STEPS) == 264


def test_25_4_truncates_to_25_39_as_captured():
    row = ROWS["25.4"]
    assert c64_bytes(25.4) == row.g == bytes.fromhex("854B333333")
    got = truncated_score(25.4)
    assert c64_bytes(got) == row.t == bytes.fromhex("854B1EB852")
    assert c64_str(got) == row.t_str == " 25.39"
    # As a double, int(25.4*100)/100 keeps 25.4: the C64 holds 25.4 a little low.
    assert int(25.4 * 100) / 100 == 25.4


@pytest.mark.parametrize("row", ROWS.values(), ids=lambda r: r.label)
def test_every_captured_row_truncates_as_the_c64_did(row: Row):
    """From the C64's own ``g``, the helper lands on the captured ``t``, byte for byte."""
    g = _decode(row.g)
    assert row.g[0] == 0 or c64_bytes(g) == row.g, "the test's decoding is off"
    got = truncated_score(g)
    assert _same(c64_bytes(got), row.t)
    assert c64_float(got) == got, "the result is not a 5-byte value"
    assert c64_str(got) == row.t_str


#: Literals the C64's decimal parser reads a unit or two off the nearest 5-byte value, so a
#: double literal (which :func:`c64_float` rounds to nearest) starts elsewhere. ``.57``
#: and ``.01`` truncate differently for it: the parser's ``.57`` lies below .57, the
#: nearest lies above; the parser's ``.01`` lies above .01, the nearest (which is also
#: the C64's ``1/100``) below, so ``int(1/100*100)/100`` is 0 on the C64 too (sweep k=1).
PARSER_OFF = {".57": " .57", "25.199999": " 25.19", ".01": " 0"}


@pytest.mark.parametrize("row", LITERALS, ids=lambda r: r.label)
def test_a_literal_score_as_a_double_truncates_as_captured(row: Row):
    value = float(row.label)
    nearest = c64_bytes(value)
    if row.label in PARSER_OFF:
        off = int.from_bytes(nearest[1:], "big") - int.from_bytes(row.g[1:], "big")
        assert nearest[0] == row.g[0] and abs(off) <= 2, "not a parser slip"
        assert c64_str(truncated_score(value)) == PARSER_OFF[row.label]
        return
    assert nearest == row.g
    assert _same(c64_bytes(truncated_score(value)), row.t)


def test_every_whole_cent_score_truncates_as_the_sweep_captured():
    """``k/100`` for every k in 0..10000: kept or cut exactly as the C64 did."""
    moved = []
    for k, captured in enumerate(SWEEP):
        c64_g = c64_divide(k, 100)  # the program's g=k/100
        assert c64_float(k / 100) == c64_g, f"the double {k}/100 is not the C64's"
        got = truncated_score(k / 100)
        expected = c64_bytes(c64_g) if captured is None else captured
        assert _same(c64_bytes(got), expected), k
        if captured is not None:
            moved.append(k)
    # 4803 of the 10001 whole cents drop a cent, each to the cent below it.
    assert len(moved) == 4803
    assert all(c64_str(truncated_score(k / 100)) == c64_str((k - 1) / 100) for k in moved)


def test_whole_scores_are_unchanged():
    for n in range(0, 101):
        assert SWEEP[100 * n] is None, n
        assert truncated_score(float(n)) == n
        assert truncated_score(n) == n
    for n in (-1, -50, -100):
        assert truncated_score(float(n)) == n


def test_a_cut_score_can_lose_a_cent_again():
    """``12.34`` drops to ``12.33`` and, on the next turn, to ``12.32``, then holds."""
    chain = [ROWS[f"D8-{i}"] for i in (1, 2, 3)]
    assert [r.t_str for r in chain] == [" 12.33", " 12.32", " 12.32"]
    assert "D8-4" not in ROWS
    gf = 12.34
    for row in chain:
        gf = truncated_score(gf)
        assert c64_bytes(gf) == row.t


def test_the_sums_pinned_before_the_port():
    """Characterization: the capture's ``:1160`` rows the faithful port must reach."""
    assert ROWS["S3-3"].g == bytes.fromhex("8166666667")  # .9+3*.3 above 1.8 on the C64
    assert ROWS["S3-3"].t_str == " 1.8"
    assert [ROWS[f"S{i}-1"].g.hex().upper() for i in (1, 6, 9)] == [
        "7D4CCCCCCD",
        "810CCCCCCD",
        "815999999A",
    ]


#: Where replaying the captured sums in doubles first parts from the C64: series index
#: (``S<i>``) -> step. The port adds ``:1160``'s ``x*x8`` in doubles, the C64 in its own
#: arithmetic, and a sum one ends a hair under a cent the other ends a hair over it
#: (``S3-3``: the C64's 0.9+0.9 is above 1.8, the double's below), so ``:1013`` keeps
#: or drops that cent differently and the series runs a cent apart from there on.
#: Only ``:1013`` is emulated; this pins how far that reaches.
FIRST_DIVERGENCE = {3: 3, 6: 2, 9: 2}


def test_a_double_replay_of_the_sums_agrees_until_a_known_step():
    divergent = {}
    for i, x8 in enumerate(X8S, start=1):
        gf = 0.0
        for n, x in enumerate(STEPS, start=1):
            gf = min(max(gf + x * x8, 0.0), 100.0)  # :1160 and its cap and floor
            gf = truncated_score(gf)
            if not _same(c64_bytes(gf), ROWS[f"S{i}-{n}"].t) and i not in divergent:
                divergent[i] = n
    assert divergent == FIRST_DIVERGENCE


def test_the_format_helpers():
    assert c64_bytes(1) == bytes.fromhex("8100000000")
    assert c64_bytes(-25.4) == bytes.fromhex("85CB333333")
    assert c64_bytes(0.0) == c64_bytes(-0.0) == bytes(5)
    assert c64_float(c64_float(0.1)) == c64_float(0.1) != 0.1
    assert c64_divide(1, 100) == _decode(bytes.fromhex("7A23D70A3D"))
    assert c64_float(2.0**-140) == 0.0  # below the exponent byte: the ROM's underflow
    with pytest.raises(ValueError):
        c64_float(1e39)
    with pytest.raises(ValueError):
        c64_float(float("nan"))
    with pytest.raises(TypeError):
        c64_float(True)
    with pytest.raises(ZeroDivisionError):
        c64_divide(1, 0)


# --------------------------------------------------------------------------- #
# FADD and FMULT: the 40-bit window (#147)                                     #
# --------------------------------------------------------------------------- #
#: ``ops_cases.bas`` pokes 5-byte operands into ``a``, ``b``, ``x`` and ``y`` and writes
#: the 5 bytes of ``r=a+b`` (``A<n>``), ``r=a+(x*y)`` (``M<n>``) and ``r=x*y``
#: (``P<n>``). The operands were drawn at random and mostly kept where a model that
#: rounds the exact result, or one that drops the product's rounding byte before the
#: addition, lands elsewhere than this port; VICE 3.10 (``x64sc``) wrote ``ops_capture.txt``.
OPS_PROGRAM = FIXTURE / "ops_cases.bas"
OPS_CAPTURE = FIXTURE / "ops_capture.txt"


def _ops_cases() -> tuple[list[tuple[bytes, ...]], ...]:
    """The program's operands: ``r=a+b`` pairs, ``r=a+(x*y)`` triples, ``r=x*y`` pairs."""
    numbers: list[int] = []
    for line in OPS_PROGRAM.read_text("ascii").splitlines():
        if " data " in line:
            numbers += [int(n) for n in line.split(" data ", 1)[1].split(",")]
    rest = iter(numbers[3:])

    def cases(count: int, width: int) -> list[tuple[bytes, ...]]:
        return [
            tuple(bytes(next(rest) for _ in range(5)) for _ in range(width)) for _ in range(count)
        ]

    groups = (cases(numbers[0], 2), cases(numbers[1], 3), cases(numbers[2], 2))
    assert next(rest, None) is None, "unread DATA"
    return groups


def _ops_capture() -> dict[str, bytes]:
    lines = OPS_CAPTURE.read_text("ascii").splitlines()
    assert lines[-1] == "END", "the capture is cut short"
    return {label: bytes.fromhex(raw) for label, raw in (ln.split(":") for ln in lines[:-1])}


OP_ADDS, OP_SUMS, OP_PRODUCTS = _ops_cases()
OPS = _ops_capture()


def _held(raw: bytes) -> float:
    value = _decode(raw)
    assert raw[0] == 0 or c64_bytes(value) == raw, "the test's decoding is off"
    return value


def test_the_ops_capture_is_complete():
    assert (len(OP_ADDS), len(OP_SUMS), len(OP_PRODUCTS)) == (60, 80, 30)
    assert len(OPS) == 170


@pytest.mark.parametrize("n", range(1, 61))
def test_fadd_matches_the_c64(n: int):
    a, b = OP_ADDS[n - 1]
    assert _same(c64_bytes(c64_add(_held(a), _held(b))), OPS[f"A{n}"])


@pytest.mark.parametrize("n", range(1, 81))
def test_a_plus_x_times_y_matches_the_c64(n: int):
    a, x, y = OP_SUMS[n - 1]
    assert _same(c64_bytes(c64_add_product(_held(a), _held(x), _held(y))), OPS[f"M{n}"])


@pytest.mark.parametrize("n", range(1, 31))
def test_fmult_matches_the_c64(n: int):
    x, y = OP_PRODUCTS[n - 1]
    assert _same(c64_bytes(c64_multiply(_held(x), _held(y))), OPS[f"P{n}"])


def test_the_ops_capture_tells_the_window_from_plain_rounding():
    """The capture is no rubber stamp: rounding the exact result misses rows of it."""
    from fractions import Fraction

    def exact(a: float, b: float) -> bytes:
        return c64_bytes(float(Fraction(a) + Fraction(b)))

    missed = [n for n, (a, b) in enumerate(OP_ADDS, 1) if exact(_held(a), _held(b)) != OPS[f"A{n}"]]
    assert len(missed) >= 30


# --------------------------------------------------------------------------- #
# :1160 under both settings of the c64_float_score house rule                  #
# --------------------------------------------------------------------------- #
#: The ``DATA`` text of the weights (``read e(i)`` parses it as ``val`` does).
X8_TEXTS = [
    t.strip()
    for t in next(ln for ln in PROGRAM.read_text("ascii").splitlines() if ln.startswith("1100 "))
    .split("data", 1)[1]
    .split(",")
]


def _score_state(gf: float, weight: float, setting: str):
    from engine.state import Clock, Config, GameState, Player

    from data.game_configs.mafia_1920s.gangster import Gangster

    return GameState(
        players=(Player(name="p0", gf=gf, roster=(Gangster(name="g0"),)),),
        clock=Clock(active_player=0),
        config=Config(
            formula_params={"score_mult": weight}, house_rules={"c64_float_score": setting}
        ),
    )


def _award(gf: float, x: int, weight: float, setting: str) -> tuple[float, int]:
    """One ``gosub 1160`` through the config's real effect: the new score and ``nr``."""
    import data.game_configs.mafia_1920s.state as game
    from data.game_configs.mafia_1920s.effects import ScoreAndRank
    from engine.effects import apply

    out = apply(_score_state(gf, weight, setting), ScoreAndRank(amount=x, rank_divisor=11.1))
    return out.players[0].gf, game.next_rank(out.players[0])


def test_the_weights_parse_as_the_c64_read_them():
    assert len(X8_TEXTS) == len(X8S) == 11
    for i, text in enumerate(X8_TEXTS, start=1):
        assert c64_bytes(c64_val(text)) == ROWS[f"S{i}-1"].g, text


@pytest.mark.parametrize("i", range(1, 12))
def test_faithful_sums_match_every_captured_series(i: int):
    """AE4: the faithful effect lands on the capture's ``g`` and ``:1013``'s ``t``,
    byte for byte, at every step -- series 3, 6 and 9 included."""
    weight = c64_val(X8_TEXTS[i - 1])
    gf = 0.0
    for n, x in enumerate(STEPS, start=1):
        row = ROWS[f"S{i}-{n}"]
        gf, _ = _award(gf, int(x), weight, "faithful")
        assert c64_float(gf) == gf, f"S{i}-{n}: the stored score is no 5-byte value"
        assert _same(c64_bytes(gf), row.g), f"S{i}-{n}: the sum"
        gf = truncated_score(gf)
        assert _same(c64_bytes(gf), row.t), f"S{i}-{n}: the cut"
        assert c64_str(gf) == row.t_str


@pytest.mark.parametrize("i", range(1, 12))
def test_intent_sums_are_the_exact_decimal_sums(i: int):
    """AE4: under intent each step is the exact decimal sum, capped, floored and cut."""
    from decimal import ROUND_FLOOR, Decimal

    weight = float(X8_TEXTS[i - 1])
    expected = Decimal(0)
    gf = 0.0
    for x in STEPS:
        expected = min(max(expected + int(x) * Decimal(X8_TEXTS[i - 1]), Decimal(0)), Decimal(100))
        gf, _ = _award(gf, int(x), weight, "intent")
        assert gf == float(expected)
        gf = truncated_score(gf, decimal=True)
        expected = expected.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        assert gf == float(expected)


def test_the_settings_part_where_the_capture_says():
    """Series 3 (x8=.3): both settings reach 1.8 at step 3 (the C64's sum lies above it,
    so :1013 keeps it); at step 4 the C64's 2.1 lies below and is cut to 2.09 (``S3-4``),
    where the exact decimal keeps 2.1."""
    faithful = [ROWS[f"S3-{n}"].t_str for n in range(1, 25)]
    intent_gf, intent = 0.0, []
    for x in STEPS:
        intent_gf, _ = _award(intent_gf, int(x), 0.3, "intent")
        intent_gf = truncated_score(intent_gf, decimal=True)
        intent.append(c64_str(intent_gf))
    assert faithful[:3] == intent[:3] == [" .3", " .9", " 1.8"]
    assert (faithful[3], intent[3]) == (" 2.09", " 2.1")


@pytest.mark.parametrize("setting", ["faithful", "intent"])
def test_the_cap_and_floor_come_after_the_sum(setting: str):
    assert _award(99.0, 3, 1.0, setting) == (100.0, 10)
    assert _award(1.0, -5, 1.0, setting) == (0.0, 1)
    assert _award(98.5, 1, 1.5, setting)[0] == 100.0
    assert _award(0.5, -1, 0.5, setting)[0] == 0.0


# --------------------------------------------------------------------------- #
# The decimal parser (``val``, ``input``, literals)                            #
# --------------------------------------------------------------------------- #
#: ``parse_cases.bas`` writes, per text, the 5 bytes ``val`` gives it and the 5 bytes
#: ``:175``'s own path gives it (``input`` then ``val``, typed through the keyboard
#: buffer; ``-`` for text too long for the buffer). VICE 3.10 wrote ``parse_capture.txt``.
PARSE_CAPTURE = FIXTURE / "parse_capture.txt"


def _parse_rows() -> list[tuple[str, bytes, bytes | None]]:
    lines = PARSE_CAPTURE.read_text("ascii").splitlines()
    assert lines[-1] == "END", "the capture is cut short"
    rows = []
    for line in lines[:-1]:
        text, rest = line[1:].split("]:")
        by_val, by_input = rest.split(":")
        rows.append(
            (text, bytes.fromhex(by_val), None if by_input == "-" else bytes.fromhex(by_input))
        )
    return rows


PARSE_ROWS = _parse_rows()


@pytest.mark.parametrize(("text", "by_val", "by_input"), PARSE_ROWS, ids=lambda v: str(v))
def test_c64_val_reads_text_as_the_c64_did(text: str, by_val: bytes, by_input: bytes | None):
    assert by_input in (None, by_val), "input and val disagree"
    assert c64_bytes(c64_val(text)) == by_val


def test_the_parse_capture_tells_the_parser_from_rounding():
    """Some weights setup takes parse a unit off the nearest value, so ``float`` misses."""
    off = [text for text, by_val, _ in PARSE_ROWS if c64_bytes(float(text)) != by_val]
    assert {".01", "1.99", "0.11", "1.98"} <= set(off)
    assert len(PARSE_ROWS) == 57


@pytest.mark.parametrize("row", LITERALS, ids=lambda r: r.label)
def test_c64_val_reads_every_literal_of_the_truncation_capture(row: Row):
    """``g=val(label)``: the parser lands on the captured ``g``, :data:`PARSER_OFF` too."""
    assert _same(c64_bytes(c64_val(row.label)), row.g)


@pytest.mark.parametrize("text", ["1.5x", "", ".", "e5", "1..5", "1_0", "0x1", "inf", "nan"])
def test_c64_val_reads_clean_text_only(text: str):
    with pytest.raises(ValueError):
        c64_val(text)


def test_c64_val_edges():
    assert c64_val(" -0.5 ") == -0.5
    assert c64_val("1e-99999") == 0.0  # underflows to zero, without 99999 divisions
    with pytest.raises(ValueError):
        c64_val("1e40")  # ?overflow error


# --------------------------------------------------------------------------- #
# :1165 nr(sp)=int(gf(sp)/11.1)+1                                              #
# --------------------------------------------------------------------------- #
#: ``rank_cases.bas`` stores ``g=k/100`` for every k in 0..10000 and writes ``k:r`` each
#: time ``r=int(g/11.1)+1`` changes, after the 5 bytes of the literal ``11.1``.
RANK_CAPTURE = FIXTURE / "rank_capture.txt"


def _rank_steps() -> tuple[bytes, list[tuple[int, int]]]:
    lines = RANK_CAPTURE.read_text("ascii").splitlines()
    assert lines[-1] == "END", "the capture is cut short"
    label, literal = lines[0].split(":")
    assert label == "11.1"
    return bytes.fromhex(literal), [
        (int(k), int(r)) for k, r in (ln.split(":") for ln in lines[1:-1])
    ]


ELEVEN_ONE, RANK_STEPS = _rank_steps()


def _steps(rank_of) -> list[tuple[int, int]]:
    steps, previous = [], None
    for k in range(10001):
        rank = rank_of(k)
        if rank != previous:
            steps.append((k, rank))
            previous = rank
    return steps


def test_the_rank_divisor_is_the_parsed_literal():
    assert c64_bytes(c64_val("11.1")) == ELEVEN_ONE


def test_faithful_rank_steps_where_the_c64_does():
    """55.50 is rank 5 and 99.90 rank 9 on the C64; doubles say 6 and 10."""
    eleven_one = c64_val("11.1")
    assert _steps(lambda k: c64_int_divide(c64_divide(k, 100), eleven_one) + 1) == RANK_STEPS
    doubles = _steps(lambda k: int(c64_divide(k, 100) / 11.1) + 1)
    assert [step for step in doubles if step not in RANK_STEPS] == [(5550, 6), (9990, 10)]


@pytest.mark.parametrize(
    ("gf", "faithful", "intent"), [(55.5, 5, 6), (99.9, 9, 10), (55.51, 6, 6), (55.49, 5, 5)]
)
def test_the_effect_ranks_by_the_setting(gf: float, faithful: int, intent: int):
    stored = c64_divide(round(gf * 100), 100)  # the score as :1013 leaves it on the C64
    assert _award(stored, 0, 1.0, "faithful")[1] == faithful
    assert _award(gf, 0, 1.0, "intent")[1] == intent

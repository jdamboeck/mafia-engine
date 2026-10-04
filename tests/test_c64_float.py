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
from engine.c64_numbers import c64_bytes, c64_divide, c64_float, c64_str

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

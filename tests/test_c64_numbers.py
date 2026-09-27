"""Tests for the C64 BASIC V2 ``str$`` formatter.

The oracle is a capture from the real C64 ROM, not the rules table in the plan:
``tests/fixtures/c64_str/str_cases.bas`` was run in VICE 3.10 (``x64sc``) and wrote
``tests/fixtures/c64_str/vice_capture.txt``, one row per case in the form
``input\\str$ output\\``. PETSCII ``|`` came out as ``\\``, and labels came out
uppercased (``1E9``, ``ACC01``). The leading space of each output is the sign position
and is part of the expected text.

Literal rows (the ``DATA`` lines) are fed back through ``float``/``int`` of their label.
Computed rows (``1/3``, ``.1+.2-.3``, the ``ACC*`` sums, ``RND``) are recomputed here in
Python exactly as the ``.bas`` program computes them. Where the double lands on the same
printed value as the C64's 40-bit float, the capture is the expected text; where it does
not, the test pins the formatter printing the double faithfully (see
:data:`DIVERGENT_ROWS`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.c64_numbers import c64_str

CAPTURE = Path(__file__).parent / "fixtures" / "c64_str" / "vice_capture.txt"


def _read_capture() -> dict[str, str]:
    """Parse the VICE capture into ``{input label: str$ output}``."""
    rows: dict[str, str] = {}
    for line in CAPTURE.read_text(encoding="ascii").splitlines():
        if not line:
            continue
        label, output, tail = line.split("\\")
        assert tail == "", line
        rows[label] = output
    return rows


VICE = _read_capture()


def _replay_sum(start: float, steps: list[float]) -> float:
    """Add ``steps`` to ``start`` one at a time, as the ``.bas`` loops do."""
    g = start
    for step in steps:
        g = g + step
    return g


def _acc07() -> float:
    """``x8=.7:g=12.5`` then three times ``g=g+x8*2*-(g>0)``."""
    x8 = 0.7
    g = 12.5
    for _ in range(3):
        g = g + x8 * 2 * (1 if g > 0 else 0)
    return g


# Computed rows whose double prints the same as the C64's 40-bit result.
AGREEING_ROWS: dict[str, float] = {
    "1/3": 1 / 3,
    "2/3": 2 / 3,
    "ACC07": _acc07(),
    "ACC25": _replay_sum(0.0, [1 * 0.1] * 25),
    "ACC253": _replay_sum(25.0, [0.3 * -1] * 2),
}

# Computed rows where the double and the 40-bit float end on different values:
# label -> (Python-computed input, what the formatter prints for that double).
# Leftovers differ in both directions: the C64 keeps a leftover the double does
# not share (ACC01, ACC03), and cancels exactly where the double does not (.1+.2-.3).
DIVERGENT_ROWS: dict[str, tuple[float, str]] = {
    ".1+.2-.3": (0.1 + 0.2 - 0.3, " 5.55111512E-17"),
    "ACC01": (_replay_sum(0.0, [1 * 0.1] * 3 + [-1 * 0.1] * 3), " 2.77555756E-17"),
    "ACC03": (_replay_sum(0.0, [2 * 0.3] * 7 + [-2 * 0.3] * 7), "-2.22044605E-16"),
    # int(25.4*100)/100: the C64 gets 2539.99.. and truncates, the double gets 2540.
    "RND": (int(25.4 * 100) / 100, " 25.4"),
}

LITERAL_ROWS = sorted(set(VICE) - set(AGREEING_ROWS) - set(DIVERGENT_ROWS))


def _literal(label: str) -> int | float:
    """The number a ``DATA`` label denotes, as an int when it is written as one."""
    try:
        return int(label)
    except ValueError:
        return float(label)


def test_every_capture_row_is_covered() -> None:
    """No capture row is silently skipped: each literal label parses as a number."""
    for label in LITERAL_ROWS:
        _literal(label)
    assert set(AGREEING_ROWS) | set(DIVERGENT_ROWS) <= set(VICE)


@pytest.mark.parametrize("label", LITERAL_ROWS)
def test_literal_rows_match_vice(label: str) -> None:
    assert c64_str(_literal(label)) == VICE[label]


@pytest.mark.parametrize("label", LITERAL_ROWS)
def test_literal_rows_as_float_match_vice(label: str) -> None:
    """An integer-valued float prints like the int: ``22.0`` is `` 22``, no ``.0``."""
    assert c64_str(float(label)) == VICE[label]


@pytest.mark.parametrize("label", sorted(AGREEING_ROWS))
def test_computed_rows_match_vice(label: str) -> None:
    assert c64_str(AGREEING_ROWS[label]) == VICE[label]


@pytest.mark.parametrize("label", sorted(DIVERGENT_ROWS))
def test_double_leftovers_print_faithfully_not_snapped(label: str) -> None:
    """The formatter prints the double it is given; it does not emulate 40-bit math.

    The capture shows the C64 itself leaves leftovers (ACC01 prints
    `` 5.82076609E-11``), so snapping near-zero values to 0 would not match it
    either. The divergence is accepted and documented on :func:`c64_str`.
    """
    value, printed = DIVERGENT_ROWS[label]
    assert VICE[label] != printed
    assert c64_str(value) == printed


def test_negative_zero_prints_as_zero() -> None:
    assert c64_str(-0.0) == VICE["-0"] == " 0"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.001, " 1E-03"),  # below 0.01 -> exponent form
        (0.0099, " 9.9E-03"),
        (0.01, " .01"),  # smallest fixed magnitude
        (-0.9, "-.9"),  # no leading zero, sign position holds the minus
        (123456789, " 123456789"),  # largest 9-digit integer stays fixed
        (999999999.6, " 1E+09"),  # rounds up across the 1e9 boundary
        (1234567890, " 1.23456789E+09"),  # 10 digits -> exponent form
        (123.456789123, " 123.456789"),  # 9 significant digits
        (2 / 3, " .666666667"),  # rounded, not truncated
    ],
)
def test_rule_edges(value: float, expected: str) -> None:
    """The edges each rule turns on, spelled out (each is also a capture row)."""
    assert c64_str(value) == expected


@pytest.mark.parametrize("value", [True, False, "22", None, [1], complex(1, 0)])
def test_non_numbers_raise_type_error(value: object) -> None:
    with pytest.raises(TypeError):
        c64_str(value)  # pyright: ignore[reportArgumentType]  # the wrong type is the test


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_raises_value_error(value: float) -> None:
    """The C64 has no NaN or infinity to print; refuse rather than invent text."""
    with pytest.raises(ValueError):
        c64_str(value)

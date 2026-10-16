"""The ``x8`` score-weight helpers of the ``mafia_1920s`` setup, and its number parsing.

Setup asks the score weight at ``mf-prg.bas:175-176`` (``x8=val(x$)``, asked again
outside ``input_ranges.score_weight``). This module parses a numeric setup answer
(:func:`parse_setup_number`, :func:`in_range`) and decides whether a typed weight is
taken, under the C64's decimal parser and under Python's ``float``. :mod:`.setup`, the
setup handler (``handlers/new_game.py``) and the package (the terminal client's
``--score-weight`` check) use them; :mod:`.setup` re-exports them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Context, Decimal

from engine.c64_numbers import c64_val

__all__ = [
    "parse_setup_number",
    "in_range",
    "check_score_weight",
    "parse_score_weight",
    "score_weight_accepted",
    "score_weight_text",
]


def parse_setup_number(text: str, *, integer: bool) -> float | None:
    """Parse one numeric setup answer; ``None`` for non-numeric input (asked again).

    The end year is ``int(val(x$))`` in the source (mf-prg.bas:170) -- truncated to an
    integer; the score weight is ``val(x$)`` (:175), a decimal such as ``0.5``, and so
    is the player count (:205 ``sz=val(x$)``). Only a finite number is a number here.
    """
    try:
        value = float(text.strip())
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if integer else value


def in_range(value: float, bounds: Mapping[str, float]) -> bool:
    """``bounds`` is one ``input_ranges`` entry (``{min, max}``) from config.yaml."""
    return bounds["min"] <= value <= bounds["max"]


def score_weight_text(weight: float | str) -> str:
    """The text an ``x8`` score weight is parsed from.

    Typed text is kept as typed, without the spaces around it (C64 ``INPUT`` drops
    them); a number -- the keyword :func:`new_game`'s ``score_weight=0.1`` -- becomes its
    shortest text (``repr``), so it reaches the weight typing that text reaches.
    """
    if isinstance(weight, str):
        return weight.strip()
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        raise TypeError(f"score_weight must be a number or its text, got {weight!r}")
    return repr(weight)


def _exact_weight_text(text: str) -> str:
    """The typed ``x8`` weight as its exact decimal, in one spelling per value.

    ``"1"``, ``"1.0"`` and ``" 1e0"`` are the same weight, so they store the same text
    (``"1"``); every digit typed is kept (``"0.33333333333333333"`` is not its float,
    and a weight longer than ``Decimal``'s default 28 digits is not rounded to them).
    """
    exact = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
    return format(Decimal(text.strip()).normalize(exact), "f")


def parse_score_weight(text: str, *, c64: bool) -> float | None:
    """``:175`` ``x8=val(x$)`` for a clean number; ``None`` for other text (asked again).

    The clean-number check is :func:`parse_setup_number`'s. With ``c64`` the value is
    the one the C64's decimal parser gives the text
    (:func:`~engine.c64_numbers.c64_val`: ``0.01`` lies a unit above ``1/100``),
    else Python's ``float``. Text the C64 parser does not read as a whole number --
    ``val("1.5x")`` is 1.5 on the C64 -- is no number under either reading.
    """
    value = parse_setup_number(text, integer=False)
    if value is None or not c64:
        return value
    try:
        return c64_val(text)
    except ValueError:
        return None


def _score_weight_in_range(value: float, bounds: Mapping[str, float], *, c64: bool) -> bool:
    """``:176`` ``ifx8<0.1orx8>2goto175``: on the C64 the bounds are parsed literals."""
    if not c64:
        return in_range(value, bounds)
    low, high = (c64_val(repr(float(bounds[end]))) for end in ("min", "max"))
    return low <= value <= high


def score_weight_accepted(weight: float | str, ranges: Mapping) -> bool:
    """Whether setup takes ``weight`` as the ``x8`` score weight (``:175-176``).

    Setup asks the weight before the house rules, so it cannot know yet whether the
    C64's parser or ``float`` will read it (:data:`..effects.C64_FLOAT_SCORE`): the
    weight must be a clean number inside ``input_ranges.score_weight`` under both.
    """
    text = score_weight_text(weight)
    for c64 in (True, False):
        value = parse_score_weight(text, c64=c64)
        if value is None or not _score_weight_in_range(value, ranges["score_weight"], c64=c64):
            return False
    return True


def check_score_weight(score_weight: float | str, ranges: Mapping) -> None:
    """Refuse an ``x8`` score weight setup would ask again for (:176)."""
    if not score_weight_accepted(score_weight, ranges):
        sw = ranges["score_weight"]
        raise ValueError(
            f"score_weight must be a number in [{sw['min']}, {sw['max']}], got {score_weight!r}"
        )

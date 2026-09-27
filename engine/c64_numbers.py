"""Numbers as C64 BASIC V2 prints them (``str$``).

The original game prints every number through the C64 ROM's float-to-text routine,
so a theme that wants the original's look needs the same text: a sign position
(a space for zero and positives, ``-`` for negatives), no leading zero before the
point (``-.9``), no trailing ``.0``, 9 significant digits, and exponent form outside
``0.01 <= |x| < 1e9``.

Authority
---------
The research corpus does not document these rules, so the authority is the ROM
itself: ``tests/fixtures/c64_str/str_cases.bas`` was run on the real C64 ROM in
VICE 3.10 (``x64sc``) and its output is ``tests/fixtures/c64_str/vice_capture.txt``,
which ``tests/test_c64_numbers.py`` uses as the oracle. The rules below are read off
that capture:

- **Rounding.** The value is rounded half up to 9 significant digits first; the notation is
  chosen on the rounded value (``999999999.6`` prints `` 1E+09``).
- **Fixed notation** for ``0.01 <= |x| < 1e9`` after rounding: digits with trailing
  zeros dropped, no ``0`` before the point (`` .01``, `` 123.456789``).
- **Exponent form** otherwise: one digit, then ``.`` and the remaining digits if any
  are non-zero, then ``E``, the exponent's sign and two digits (`` 1E-03``,
  `` 9.9E-03``, `` 1.23456789E+09``, `` 1E+38``).
- **Zero**, including ``-0``, prints `` 0``.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

SIGNIFICANT_DIGITS = 9
"""Decimal digits the C64 prints of its 40-bit float's mantissa."""

_FIXED_MIN_EXPONENT = -2
"""Smallest decimal exponent printed in fixed notation (``0.01``)."""

_FIXED_MAX_EXPONENT = SIGNIFICANT_DIGITS - 1
"""Largest decimal exponent printed in fixed notation (``999999999``)."""


def c64_str(value: int | float) -> str:
    """Return ``value`` as C64 BASIC V2's ``str$`` would, sign position included.

    ``c64_str(22)`` and ``c64_str(22.0)`` are `` 22``; ``c64_str(-0.9)`` is ``-.9``;
    ``c64_str(0.001)`` is `` 1E-03``. A ``bool`` or a non-number raises
    ``TypeError``; NaN and infinity, which the C64 cannot hold, raise ``ValueError``.

    Double versus 40-bit float
    --------------------------
    The formatter prints the double it is given; it does not emulate the C64's
    40-bit arithmetic, so a sum of fractions can end on a different leftover than
    the original's. The VICE capture shows this goes both ways, so snapping
    near-zero values to 0 would not match the C64 either:

    - ``.1+.2-.3`` is exactly 0 on the C64 (`` 0``) but ``5.551115123125783e-17``
      as a double, which prints `` 5.55111512E-17``.
    - ``x8=.1``, adding ``1*x8`` three times then ``-1*x8`` three times, leaves
      `` 5.82076609E-11`` on the C64 but ``2.7755575615628914e-17`` as a double.
    - ``int(25.4*100)/100`` is `` 25.39`` on the C64 but ``25.4`` as a double.

    The difference is accepted: callers that need the original's value must compute
    it, and the formatter only reproduces how that value prints.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"c64_str needs an int or float, got {type(value).__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"the C64 cannot print {value!r}")
    if value == 0:
        return " 0"

    sign = "-" if value < 0 else " "
    # The ROM rounds the 9th digit half up (a tie such as 100000000.5 prints
    # `` 100000001``). A float is rounded from its shortest decimal repr, the number
    # the port meant, not from the binary double's exact expansion.
    exact = Decimal(abs(value)) if isinstance(value, int) else Decimal(repr(abs(value)))
    rounded = exact.quantize(
        Decimal(1).scaleb(exact.adjusted() - SIGNIFICANT_DIGITS + 1), rounding=ROUND_HALF_UP
    )
    exponent = rounded.adjusted()
    digits = "".join(map(str, rounded.as_tuple().digits)).rstrip("0")

    if _FIXED_MIN_EXPONENT <= exponent <= _FIXED_MAX_EXPONENT:
        return sign + _fixed(digits, exponent)
    return sign + _exponent_form(digits, exponent)


def _fixed(digits: str, exponent: int) -> str:
    """Place the point in ``digits`` (first digit worth ``10**exponent``)."""
    if exponent < 0:
        return "." + "0" * (-exponent - 1) + digits
    whole = digits[: exponent + 1].ljust(exponent + 1, "0")
    fraction = digits[exponent + 1 :]
    return whole + ("." + fraction if fraction else "")


def _exponent_form(digits: str, exponent: int) -> str:
    """``d.ddd`` then ``E``, the exponent's sign and two exponent digits."""
    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{mantissa}E{'-' if exponent < 0 else '+'}{abs(exponent):02d}"

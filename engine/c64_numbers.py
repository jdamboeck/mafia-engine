"""Numbers as C64 BASIC V2 holds and prints them: the 5-byte float, and ``str$``.

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

The 5-byte float
----------------
:func:`c64_float`, :func:`c64_divide` and :func:`c64_bytes` hold a number as the C64
does: a variable is 5 bytes, an exponent byte (bias 129: ``1`` is ``81 00 00 00 00``;
``0`` means zero) and a 32-bit mantissa whose top bit, always set, is replaced by the
sign. Their authority is ``tests/fixtures/c64_float/trunc_cases.bas``, run in VICE 3.10
with its output in ``tests/fixtures/c64_float/vice_capture.txt``; each row peeks
the 5 bytes of ``g`` and of ``int(g*100)/100``, which ``tests/test_c64_float.py`` checks
byte for byte. Read off the capture and the ROM (``$BC1B`` ROUND, ``$BB0F`` FDIV):

- **Storing rounds half up.** The ROM keeps a guard byte below the mantissa and rounds
  on its top bit when a value is stored (``$BBD4`` calls ``$BC1B``), on the magnitude.
- **Division is exact, then that rounding.** FDIV builds 34 quotient bits and drops the
  remainder, so the stored quotient is the exact quotient rounded half up to 32 bits
  (``1/100`` is ``7A 23 D7 0A 3D``, the nearest 5-byte value).
- **INT reads the unrounded result.** ``int(g*100)`` floors the product still in the
  accumulator, before any store rounds it. ``g*100`` needs at most 39 bits, which the
  multiply's 40-bit window holds exactly, so ``int(g*100)`` is the exact floor: the
  ``25.4`` the C64 holds is a little below 25.4, and ``int(25.4*100)`` is 2539.
- **Not modelled:** addition and multiplication in general (they truncate below a
  40-bit window before the store rounds, so they are not plain round-to-nearest).
- **Not every source rounds to nearest.** The decimal parser (literals, ``val``,
  ``input``) can land a unit or two off: the literal ``.01`` is ``7A 23 D7 0A 3E``. These
  helpers model the rounding above, not the parser.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

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
    it (:func:`c64_float`, :func:`c64_divide`), and the formatter only reproduces how
    that value prints.
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


def c64_print(value: int | float) -> str:
    """Return ``value`` as C64 BASIC V2's ``PRINT`` writes it: ``str$`` then one space.

    ``PRINT`` follows every number with a cursor-right (a space when the output is a
    file), so ``print"a"p"b"`` with ``p=5`` shows ``a 5 b`` and with ``p=-500`` shows
    ``a-500 b``. ``c64_print(5)`` is `` 5 ``; ``c64_print(-0.9)`` is ``-.9 ``. The
    authority is ``tests/fixtures/c64_print/print_cases.bas``, run in VICE 3.10, whose
    capture ``tests/fixtures/c64_print/vice_capture.txt`` holds each value's ``PRINT``,
    ``str$`` and ``mid$(str$(..),2)`` forms. On screen the cursor-right moves over
    whatever is there rather than writing a blank; on the cleared screens the game
    prints to, the two look the same.
    """
    return c64_str(value) + " "


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


MANTISSA_BITS = 32
"""Bits in the C64 float's mantissa (its 4 low bytes, top bit implied)."""

_EXPONENT_BIAS = 129
"""The exponent byte of a value in ``[1, 2)``; the byte ``0`` means zero."""


def c64_float(value: int | float) -> float:
    """Return ``value`` as a C64 variable would hold it: rounded to the 5-byte float.

    The magnitude is rounded half up to a 32-bit mantissa, as the ROM rounds a value
    when it stores it (``$BC1B``). The result is exact as a Python float, so
    ``c64_float(c64_float(x)) == c64_float(x)``. ``c64_float(25.4)`` is
    ``25.399999998509884`` (``85 4B 33 33 33``). A value too small for the exponent
    byte becomes 0, as the ROM's underflow does; a ``bool``, a non-number, NaN,
    infinity and a value too large (``?overflow error``) raise.
    """
    return float(_rounded(_exact(value, "c64_float")))


def c64_divide(dividend: int | float, divisor: int | float) -> float:
    """Return ``dividend / divisor`` as C64 BASIC computes and stores it.

    Both operands are first held as C64 floats (:func:`c64_float`); the exact quotient
    is then rounded half up to the 5-byte float, which is what FDIV (``$BB0F``) and the
    store's rounding give together. ``c64_divide(2539, 100)`` is ``25.390000000596046``
    (``85 4B 1E B8 52``), which prints `` 25.39``. A zero divisor raises
    ``ZeroDivisionError`` (``?division by zero error``).
    """
    top = _rounded(_exact(dividend, "c64_divide"))
    bottom = _rounded(_exact(divisor, "c64_divide"))
    if bottom == 0:
        raise ZeroDivisionError("?division by zero error")
    return float(_rounded(top / bottom))


def c64_bytes(value: int | float) -> bytes:
    """Return the 5 bytes a C64 variable holding ``value`` has in memory.

    The exponent byte, then the mantissa high byte first with the sign in its top bit
    (``c64_bytes(-25.4)`` is ``85 CB 33 33 33``). The value is rounded first, as
    :func:`c64_float` does. Zero is all zero bytes: the C64 checks only the exponent
    byte of a zero and leaves whatever the mantissa held, so compare a zero by that
    byte alone.
    """
    exact = _rounded(_exact(value, "c64_bytes"))
    if exact == 0:
        return bytes(5)
    exponent, mantissa = _split(abs(exact))
    sign = 0x80 if exact < 0 else 0
    return bytes([exponent + _EXPONENT_BIAS, (mantissa >> 24) & 0x7F | sign]) + (
        mantissa & 0xFFFFFF
    ).to_bytes(3, "big")


def _exact(value: int | float, name: str) -> Fraction:
    """``value`` as an exact fraction, rejecting what the C64 cannot hold."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} needs an int or float, got {type(value).__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"the C64 cannot hold {value!r}")
    return Fraction(value)


def _split(magnitude: Fraction) -> tuple[int, int]:
    """``(e, m)`` with ``magnitude == m * 2**(e - 31)`` and ``m`` a 32-bit mantissa,
    rounded half up. ``e`` is the binary exponent of the value's leading bit."""
    exponent = magnitude.numerator.bit_length() - magnitude.denominator.bit_length()
    if magnitude < Fraction(2) ** exponent:
        exponent -= 1
    scale = Fraction(2) ** (MANTISSA_BITS - 1 - exponent)
    mantissa = math.floor(magnitude * scale + Fraction(1, 2))
    if mantissa >> MANTISSA_BITS:  # rounded up to the next power of two
        return exponent + 1, mantissa >> 1
    return exponent, mantissa


def _rounded(exact: Fraction) -> Fraction:
    """``exact`` rounded half up, on the magnitude, to the 5-byte float."""
    if exact == 0:
        return Fraction(0)
    exponent, mantissa = _split(abs(exact))
    if exponent + _EXPONENT_BIAS < 1:
        return Fraction(0)  # underflow: the ROM stores zero
    if exponent + _EXPONENT_BIAS > 0xFF:
        raise ValueError(f"?overflow error: {float(exact)!r} is too large for the C64")
    value = mantissa * Fraction(2) ** (exponent - (MANTISSA_BITS - 1))
    return -value if exact < 0 else value

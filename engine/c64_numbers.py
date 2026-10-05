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
- **Add and multiply keep a 40-bit window.** The floating accumulator holds the 32-bit
  mantissa and a rounding byte below it (``FACOV``). FMULT (``$BA2B``) shifts and adds
  the multiplicand once per bit of the multiplier, the rounding byte's bits first, so
  the product is the exact one cut (not rounded) to 40 bits. FADD (``$B86A``) shifts the
  operand with the smaller exponent right, its bits falling into its rounding byte and
  below it out of the sum, then adds or subtracts the 40-bit mantissas and normalizes.
  Nothing rounds until the result is stored, so in ``a+(x*y)`` the product reaches the
  addition with its rounding byte (:func:`c64_add_product`). The authority is
  ``tests/fixtures/c64_float/ops_cases.bas`` (``r=a+b``, ``r=a+(x*y)`` and ``r=x*y`` on
  poked 5-byte operands, chosen where the window shows), held byte for byte by
  ``tests/test_c64_float.py``.
- **INT reads FDIV's quotient before the store.** FDIV builds 34 quotient bits, so
  ``int(a/b)`` is the floor of the exact quotient (:func:`c64_int_divide`): the C64's
  ``55.5`` over its ``11.1`` lies a hair under 5, and ``int(55.5/11.1)`` is 4.
- **The decimal parser rounds at every step.** Literals, ``val`` and ``input`` go
  through FIN (``$BCF3``): each digit multiplies the value so far by 10 (MUL10, which
  rounds it first, then adds four times it to it) and adds the digit; then one DIV10
  per digit after the point divides by 10, rounding the value before each division.
  So the parser can land a unit or two off the nearest value: the literal ``.01`` is
  ``7A 23 D7 0A 3E``, ``1/100`` is ``... 3D``. :func:`c64_val` ports it for clean decimal
  text, held to ``tests/fixtures/c64_float/parse_cases.bas``'s capture.
"""

from __future__ import annotations

import math
import re
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


# --- the floating accumulator: FMULT, FADD, INT of FDIV, the decimal parser ----------

_WINDOW_BITS = MANTISSA_BITS + 8
"""Bits the accumulator holds: the 32-bit mantissa and its rounding byte (``FACOV``)."""

_QUOTIENT_BITS = 34
"""Bits FDIV builds before it stops: the mantissa's 32 and two in the rounding byte."""


class _Fac:
    """The floating accumulator: sign, exponent byte and a 40-bit mantissa.

    ``mantissa`` is the 32-bit mantissa then the rounding byte, top bit set unless the
    value is zero (``exponent`` 0); the value is ``mantissa * 2**(exponent - 129 - 39)``.
    """

    __slots__ = ("negative", "exponent", "mantissa")

    def __init__(self, negative: bool, exponent: int, mantissa: int) -> None:
        self.negative = negative
        self.exponent = exponent
        self.mantissa = mantissa


_ZERO = _Fac(False, 0, 0)


def _load(value: Fraction) -> _Fac:
    """A stored 5-byte value in the accumulator, its rounding byte clear (MOVFM)."""
    if value == 0:
        return _ZERO
    exponent, mantissa = _split(abs(value))
    return _Fac(value < 0, exponent + _EXPONENT_BIAS, mantissa << 8)


def _normalized(negative: bool, exponent: int, mantissa: int) -> _Fac:
    """Shift ``mantissa`` until its top bit is bit 39 (NORMAL); a right shift truncates."""
    if mantissa == 0:
        return _ZERO
    while mantissa >> _WINDOW_BITS:
        mantissa >>= 1
        exponent += 1
    while not mantissa >> (_WINDOW_BITS - 1):
        mantissa <<= 1
        exponent -= 1
    return _Fac(negative, exponent, mantissa)


def _store(fac: _Fac) -> Fraction:
    """The accumulator stored to a variable: rounded half up on its rounding byte's top
    bit, on the magnitude (``$BC1B``)."""
    if fac.exponent == 0 or fac.mantissa == 0:
        return Fraction(0)
    exponent = fac.exponent
    mantissa = (fac.mantissa >> 8) + ((fac.mantissa >> 7) & 1)
    if mantissa >> MANTISSA_BITS:  # rounded up to the next power of two
        mantissa >>= 1
        exponent += 1
    if exponent < 1:
        return Fraction(0)  # underflow: the ROM stores zero
    if exponent > 0xFF:
        raise ValueError("?overflow error: the result is too large for the C64")
    value = mantissa * Fraction(2) ** (exponent - _EXPONENT_BIAS - (MANTISSA_BITS - 1))
    return -value if fac.negative else value


def _fmult(multiplier: _Fac, multiplicand: _Fac) -> _Fac:
    """FMULT: ``multiplicand * multiplier``, the product cut to the 40-bit window.

    The ROM adds the 32-bit multiplicand into the accumulator's top for each set bit of
    the multiplier's 40 (its rounding byte first) and shifts the accumulator right one
    bit after each, so the bits that leave its rounding byte are dropped: the result is
    the exact product truncated to 40 bits.
    """
    if multiplier.exponent == 0 or multiplicand.exponent == 0:
        return _ZERO
    product = ((multiplicand.mantissa >> 8) * multiplier.mantissa) >> MANTISSA_BITS
    return _normalized(
        multiplier.negative != multiplicand.negative,
        multiplier.exponent + multiplicand.exponent - 128,
        product,
    )


def _fadd(fac: _Fac, arg: _Fac) -> _Fac:
    """FADD: ``arg + fac``, aligned and summed in the 40-bit window.

    The operand with the smaller exponent (``arg`` when they are equal) is shifted right
    by the difference with its own rounding byte; bits below that byte are lost. Equal
    signs add (a carry shifts right one bit, truncating); unequal signs subtract the
    shifted operand, a negative difference flips the sign, and the result is normalized.
    """
    if fac.exponent == 0:
        return _Fac(arg.negative, arg.exponent, arg.mantissa)
    if arg.exponent == 0:
        return fac
    big, small = (arg, fac) if arg.exponent > fac.exponent else (fac, arg)
    shifted = small.mantissa >> (big.exponent - small.exponent)
    if big.negative == small.negative:
        return _normalized(big.negative, big.exponent, big.mantissa + shifted)
    difference = big.mantissa - shifted
    negative = big.negative != (difference < 0)
    return _normalized(negative, big.exponent, abs(difference))


def _operand(value: int | float, name: str) -> _Fac:
    return _load(_rounded(_exact(value, name)))


def c64_multiply(a: int | float, b: int | float) -> float:
    """Return ``a*b`` as C64 BASIC computes and stores it (FMULT, then the store).

    Both operands are first held as C64 floats (:func:`c64_float`). The product is cut
    to the 40-bit window and then rounded half up, which lands where rounding the exact
    product would, save on an exact tie below the window.
    """
    return float(_store(_fmult(_operand(b, "c64_multiply"), _operand(a, "c64_multiply"))))


def c64_add(a: int | float, b: int | float) -> float:
    """Return ``a+b`` as C64 BASIC computes and stores it (FADD, then the store).

    Both operands are first held as C64 floats (:func:`c64_float`). Bits of the smaller
    operand shifted out of the 40-bit window are lost before the sum rounds, so a
    difference can round to the other neighbour than the exact one: ``c64_add`` is not
    ``c64_float(a + b)``.
    """
    return float(_store(_fadd(_operand(b, "c64_add"), _operand(a, "c64_add"))))


def c64_add_product(a: int | float, x: int | float, y: int | float) -> float:
    """Return ``a+(x*y)`` as C64 BASIC computes and stores it -- ``:1160``'s sum.

    ``x*y`` stays in the accumulator with its rounding byte when it reaches the
    addition (nothing stores it in between), so this is not
    ``c64_add(a, c64_multiply(x, y))``. ``y`` is the multiplier, as BASIC evaluates the
    right operand into the accumulator. All three are first held as C64 floats.
    """
    name = "c64_add_product"
    product = _fmult(_operand(y, name), _operand(x, name))
    return float(_store(_fadd(product, _operand(a, name))))


def c64_int_divide(dividend: int | float, divisor: int | float) -> int:
    """Return ``int(dividend/divisor)`` as C64 BASIC computes it -- ``:1165``'s rank.

    Both operands are first held as C64 floats. INT floors FDIV's quotient as it lies in
    the accumulator, 34 bits cut from the exact quotient and never rounded, so the
    result is the floor of that cut quotient: ``c64_int_divide(55.5, 11.1)`` is 4, the
    C64's 55.5 lying a hair under five of its 11.1 (``int(55.5/11.1)`` in doubles is 5).
    A zero divisor raises ``ZeroDivisionError``.
    """
    top = _rounded(_exact(dividend, "c64_int_divide"))
    bottom = _rounded(_exact(divisor, "c64_int_divide"))
    if bottom == 0:
        raise ZeroDivisionError("?division by zero error")
    quotient = top / bottom
    if quotient == 0:
        return 0
    exponent, _ = _split(abs(quotient))
    scale = Fraction(2) ** (_QUOTIENT_BITS - 1 - exponent)
    cut = Fraction(math.floor(abs(quotient) * scale)) / scale
    return math.floor(-cut if quotient < 0 else cut)


def _mul10(fac: _Fac) -> _Fac:
    """MUL10 (``$BAE2``): round the accumulator, then ``(4v + v) * 2`` in FADD."""
    value = _load(_store(fac))
    if value.exponent == 0:
        return value
    total = _fadd(_Fac(value.negative, value.exponent + 2, value.mantissa), value)
    return _Fac(total.negative, total.exponent + 1, total.mantissa)


def _div10(fac: _Fac) -> _Fac:
    """DIV10 (``$BAFE``): round the accumulator, then FDIV by 10 (34 quotient bits)."""
    value = _store(fac)
    if value == 0:
        return _ZERO
    quotient = abs(value) / 10
    exponent, _ = _split(quotient)
    cut = math.floor(quotient * Fraction(2) ** (_QUOTIENT_BITS - 1 - exponent))
    return _Fac(value < 0, exponent + _EXPONENT_BIAS, cut << (_WINDOW_BITS - _QUOTIENT_BITS))


#: The text :func:`c64_val` reads: a sign, digits with at most one point, an exponent.
_CLEAN_NUMBER = re.compile(r"\s*([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?\s*")


def c64_val(text: str) -> float:
    """Return the number C64 BASIC's decimal parser (FIN, ``$BCF3``) reads from ``text``.

    ``val``, ``input`` and a program's literals all parse through FIN. Each digit
    multiplies the value so far by ten (MUL10, rounding it first) and adds the digit;
    then the value is divided by ten once per digit after the point (DIV10, rounding it
    before each division) -- or multiplied, for a positive exponent -- and stored. Each
    step rounds, so the result can be a unit or two off the nearest 5-byte value:
    ``c64_val(".01")`` is ``7A 23 D7 0A 3E``, one above :func:`c64_divide`'s ``1/100``.

    Only clean text is read: an optional sign, digits with at most one point, an
    optional ``E`` exponent, and spaces around it. Other text raises ``ValueError``;
    this is not ``val``'s reading of a prefix (``val("1.5x")`` is 1.5 on the C64). A
    value too large raises ``ValueError`` (``?overflow error``).
    """
    match = _CLEAN_NUMBER.fullmatch(text)
    if match is None or not (match.group(2) or match.group(3)):
        raise ValueError(f"c64_val reads clean decimal text only, got {text!r}")
    sign, whole, fraction, power = match.groups()
    fraction = fraction or ""
    fac = _ZERO
    for digit in whole + fraction:
        accumulated = _load(_store(_mul10(fac)))  # FINLOG: MOVAF rounds the value so far
        fac = _fadd(_load(Fraction(int(digit))), accumulated)
    scale = (int(power) if power else 0) - len(fraction)
    for _ in range(-scale):
        if fac.exponent == 0:
            break  # zero, or underflowed to it: the rest of the divisions keep it there
        fac = _div10(fac)
    for _ in range(scale):
        if fac.exponent == 0:
            break
        fac = _mul10(fac)  # a value too large raises ?overflow error within 40 steps
    value = _store(fac)
    return float(-value if sign == "-" else value)

"""Evaluate a quoted Commodore BASIC V2 expression the way a C64 does.

A test helper, never engine code: port tests feed it the verbatim text of a line
from ``mf-prg.bas`` and compare the engine's port against its result.

API
---
``eval_expr(expr, values)``
    Evaluate a bare expression, e.g. ``50-50*(ln=3orln=4)-100*(ln=1)``.
``eval_assignment(stmt, values)``
    Evaluate the right-hand side of one assignment, e.g. ``z=3+3*(jo(sp)=2)``
    (an optional leading ``let`` is accepted). Two entry points, because
    ``ln=1`` is both a valid assignment and a valid comparison.
``BasicEvalError``
    Raised for anything outside the supported subset, instead of guessing.

``values`` maps variable names to numbers:

- A scalar is keyed by its name: ``{"ln": 3}``. An integer variable keeps its
  ``%`` (``"x5%"``), and is a different variable from ``x5``, as on the C64.
- An array element is keyed by the name and its *evaluated* integer subscripts:
  ``jo(sp)`` with ``sp=1`` reads ``"jo(1)"``; ``a(i,j)`` reads ``"a(1,2)"``.
- ``rnd(1)`` is a named input keyed ``"rnd(1)"``. Its value is either one number
  (every ``rnd`` call in the expression reads it) or a sequence consumed one call
  at a time in left-to-right evaluation order.
- Keys are case-insensitive and normalized like source names (two characters),
  so ``"money"`` and ``"mo"`` name the same variable; two keys that collide must
  carry the same value.

Grammar and precedence (tightest first), as in the C64 ROM's operator table:
unary minus/plus, ``* /``, ``+ -``, relational (``= < > <> >< <= =< >= =>``),
``not``, ``and``, ``or``. Binary operators are left-associative, relational ones
included: ``a=b=c`` is ``(a=b)=c``. ``not`` takes a relational-level operand, so
``not a=b`` is ``not (a=b)``. Primaries: numbers (``12``, ``.5``, ``1e2``),
variables, array references, parentheses, ``int(x)`` and ``rnd(x)``.

C64 rules a conventional parser gets wrong, followed here:

- The tokenizer matches keywords before identifiers, at every letter, so
  ``ln=3orln=4`` is ``ln = 3 or ln = 4`` and ``xandy`` is ``x and y``. The full
  V2 keyword table is used, so a name hiding an unsupported keyword (``fort``
  contains ``for``) is rejected rather than read as a variable.
- Variable names are significant to two characters: ``money`` is ``mo``.
- ``int`` is floor: ``int(-2.5)`` is -3.
- Comparisons yield -1 (true) or 0 (false).
- ``and``/``or``/``not`` are 16-bit two's-complement bitwise: ``-1and5`` is 5,
  ``not 5`` is -6. An operand outside -32768..32767 is an error (the C64's
  ``?ILLEGAL QUANTITY``).

Assumptions and deliberate deviations (stated, not hidden):

- Arithmetic uses Python floats; the C64's 40-bit floats are not emulated, so
  results can differ in the last bits. ``/`` is true division (``7/2`` is 3.5).
- A non-integer operand of ``and``/``or``/``not`` raises. (The ROM would
  truncate it; the game only feeds these operators comparison results.)
- Array subscripts truncate toward zero; a negative subscript raises.
- An unknown variable raises. (The C64 reads an unset variable as 0; a test
  helper treats that as a typo in the test instead.)
- ``rnd`` with an argument <= 0 (reseed / repeat) is not modeled and raises.
- Spaces between tokens are ignored, but never join tokens: ``1 2`` raises,
  although the C64 would read it as 12.
- Strings, ``^``, ``:``-separated statements and every function except ``int``
  and ``rnd`` raise ``BasicEvalError``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["BasicEvalError", "eval_assignment", "eval_expr"]


class BasicEvalError(ValueError):
    """The text is outside the supported BASIC subset, or cannot be evaluated."""


# The BASIC V2 keyword table, in ROM order (the tokenizer takes the first match).
_KEYWORDS: tuple[str, ...] = (
    "end", "for", "next", "data", "input#", "input", "dim", "read", "let", "goto",
    "run", "if", "restore", "gosub", "return", "rem", "stop", "on", "wait", "load",
    "save", "verify", "def", "poke", "print#", "print", "cont", "list", "clr", "cmd",
    "sys", "open", "close", "get", "new", "tab(", "to", "fn", "spc(", "then", "not",
    "step", "and", "or", "sgn", "int", "abs", "usr", "fre", "pos", "sqr", "rnd",
    "log", "exp", "cos", "sin", "tan", "atn", "peek", "len", "str$", "val", "asc",
    "chr$", "left$", "right$", "mid$", "go",
)  # fmt: skip
_SUPPORTED_KEYWORDS = frozenset({"and", "or", "not", "int", "rnd", "let"})
_SYMBOLS = frozenset("+-*/()=<>,")
_NUMBER = re.compile(r"(\d+\.?\d*|\.\d+)(e[+-]?\d+)?")
_KEY = re.compile(r"([a-z][a-z0-9]*)([%$]?)(?:\((.*)\))?")


@dataclass(frozen=True)
class _Tok:
    kind: str  # "num", "name", "kw", "sym", "end"
    text: str
    value: float = 0.0


def _keyword_at(src: str, pos: int) -> str | None:
    for kw in _KEYWORDS:
        if src.startswith(kw, pos):
            return kw
    return None


def _lex(src: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pos = 0
    while pos < len(src):
        ch = src[pos]
        if ch.isspace():
            pos += 1
        elif ch.isdigit() or ch == ".":
            m = _NUMBER.match(src, pos)
            if m is None:
                raise BasicEvalError(f"malformed number at {src[pos:]!r}")
            toks.append(_Tok("num", m.group(0), float(m.group(0))))
            pos = m.end()
        elif "a" <= ch <= "z":
            kw = _keyword_at(src, pos)
            if kw is not None:
                if kw not in _SUPPORTED_KEYWORDS:
                    raise BasicEvalError(f"unsupported BASIC keyword {kw!r} in {src!r}")
                toks.append(_Tok("kw", kw))
                pos += len(kw)
                continue
            start = pos
            pos += 1
            while pos < len(src) and src[pos].isalnum() and src[pos].isascii():
                if src[pos].isalpha() and _keyword_at(src, pos) is not None:
                    break
                pos += 1
            if pos < len(src) and src[pos] in "%$":
                if src[pos] == "$":
                    raise BasicEvalError(f"string variables are unsupported: {src!r}")
                pos += 1
            toks.append(_Tok("name", _normalize_name(src[start:pos])))
        elif ch in _SYMBOLS:
            toks.append(_Tok("sym", ch))
            pos += 1
        elif ch == '"':
            raise BasicEvalError(f"string expressions are unsupported: {src!r}")
        elif ch == ":":
            raise BasicEvalError(f"only one statement is supported: {src!r}")
        else:
            raise BasicEvalError(f"unsupported character {ch!r} in {src!r}")
    toks.append(_Tok("end", ""))
    return toks


def _normalize_name(name: str) -> str:
    """Two significant characters, keeping a ``%`` type suffix."""
    suffix = name[-1] if name[-1] in "%$" else ""
    return name[: len(name) - len(suffix)][:2] + suffix


def _normalize_values(values: Mapping[str, float | Sequence[float]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for raw_key, value in values.items():
        key = raw_key.replace(" ", "").lower()
        m = _KEY.fullmatch(key)
        if m is None:
            raise BasicEvalError(f"cannot read value key {raw_key!r}")
        name, suffix, subs = m.groups()
        if key == "rnd(1)":
            norm = key
        else:
            if isinstance(value, (str, bytes)) or not isinstance(value, (int, float)):
                raise BasicEvalError(f"value for {raw_key!r} must be a number")
            norm = _normalize_name(name + suffix)
            if subs is not None:
                try:
                    norm += "(" + ",".join(str(int(s)) for s in subs.split(",")) + ")"
                except ValueError:
                    raise BasicEvalError(
                        f"array key {raw_key!r} needs integer subscripts, e.g. 'jo(1)'"
                    ) from None
        if norm in out and out[norm] != value:
            raise BasicEvalError(
                f"keys naming the same variable {norm!r} disagree: {raw_key!r}={value!r}"
            )
        out[norm] = value
    return out


def _int16(x: float) -> int:
    if x != int(x):
        raise BasicEvalError(f"bitwise operand {x} is not an integer")
    if not -32768 <= x <= 32767:
        raise BasicEvalError(f"bitwise operand {x} is outside 16 bits (?ILLEGAL QUANTITY)")
    return int(x)


def _truth(flag: bool) -> float:
    return -1.0 if flag else 0.0


class _Evaluator:
    def __init__(self, src: str, values: Mapping[str, float | Sequence[float]]) -> None:
        self.src = src
        self.toks = _lex(src.lower())
        self.pos = 0
        self.values = _normalize_values(values)
        self.rnd_calls = 0

    # -- token helpers --
    def peek(self) -> _Tok:
        return self.toks[self.pos]

    def take(self) -> _Tok:
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def at(self, kind: str, text: str) -> bool:
        tok = self.peek()
        return tok.kind == kind and tok.text == text

    def expect(self, kind: str, text: str) -> None:
        if not self.at(kind, text):
            got = self.peek().text or "end of text"
            raise BasicEvalError(f"expected {text!r}, got {got!r} in {self.src!r}")
        self.pos += 1

    def finish(self) -> None:
        if self.peek().kind != "end":
            raise BasicEvalError(f"unexpected {self.peek().text!r} in {self.src!r}")

    # -- grammar, loosest first --
    def expr(self) -> float:
        left = self.and_expr()
        while self.at("kw", "or"):
            self.pos += 1
            left = float(_int16(left) | _int16(self.and_expr()))
        return left

    def and_expr(self) -> float:
        left = self.relational()
        while self.at("kw", "and"):
            self.pos += 1
            left = float(_int16(left) & _int16(self.relational()))
        return left

    def relational(self) -> float:
        left = self.additive()
        while self.peek().kind == "sym" and self.peek().text in "<=>":
            ops = ""
            while self.peek().kind == "sym" and self.peek().text in "<=>":
                op = self.take().text
                if op in ops:
                    raise BasicEvalError(f"malformed comparison in {self.src!r}")
                ops += op
            right = self.additive()
            left = _truth(
                ("<" in ops and left < right)
                or ("=" in ops and left == right)
                or (">" in ops and left > right)
            )
        return left

    def additive(self) -> float:
        left = self.term()
        while self.peek().kind == "sym" and self.peek().text in "+-":
            op = self.take().text
            right = self.term()
            left = left + right if op == "+" else left - right
        return left

    def term(self) -> float:
        left = self.unary()
        while self.peek().kind == "sym" and self.peek().text in "*/":
            op = self.take().text
            right = self.unary()
            if op == "*":
                left = left * right
            elif right == 0:
                raise BasicEvalError(f"division by zero in {self.src!r}")
            else:
                left = left / right
        return left

    def unary(self) -> float:
        if self.at("sym", "-"):
            self.pos += 1
            return -self.unary()
        if self.at("sym", "+"):
            self.pos += 1
            return self.unary()
        if self.at("kw", "not"):
            self.pos += 1
            return float(~_int16(self.relational()))
        return self.primary()

    def primary(self) -> float:
        tok = self.take()
        if tok.kind == "num":
            return tok.value
        if tok.kind == "sym" and tok.text == "(":
            value = self.expr()
            self.expect("sym", ")")
            return value
        if tok.kind == "kw" and tok.text == "int":
            return float(math.floor(self.paren_arg()))
        if tok.kind == "kw" and tok.text == "rnd":
            return self.rnd(self.paren_arg())
        if tok.kind == "name":
            if self.at("sym", "("):
                return self.lookup(tok.text + self.subscripts())
            return self.lookup(tok.text)
        got = tok.text or "end of text"
        raise BasicEvalError(f"expected a value, got {got!r} in {self.src!r}")

    def paren_arg(self) -> float:
        self.expect("sym", "(")
        value = self.expr()
        self.expect("sym", ")")
        return value

    def subscripts(self) -> str:
        self.expect("sym", "(")
        subs = [self.expr()]
        while self.at("sym", ","):
            self.pos += 1
            subs.append(self.expr())
        self.expect("sym", ")")
        if any(s < 0 for s in subs):
            raise BasicEvalError(f"negative array subscript in {self.src!r}")
        return "(" + ",".join(str(int(s)) for s in subs) + ")"

    def lookup(self, key: str) -> float:
        if key not in self.values:
            raise BasicEvalError(f"no value for variable {key!r} in {self.src!r}")
        value = self.values[key]
        assert isinstance(value, (int, float))
        return float(value)

    def rnd(self, arg: float) -> float:
        if arg <= 0:
            raise BasicEvalError(f"rnd({arg:g}) (reseed/repeat) is unsupported in {self.src!r}")
        if "rnd(1)" not in self.values:
            raise BasicEvalError(f"no value for 'rnd(1)' in {self.src!r}")
        value = self.values["rnd(1)"]
        if isinstance(value, (int, float)):
            return float(value)
        assert isinstance(value, Sequence)
        if self.rnd_calls >= len(value):
            raise BasicEvalError(f"rnd(1) sequence exhausted after {len(value)} calls")
        result = value[self.rnd_calls]
        self.rnd_calls += 1
        assert isinstance(result, (int, float))
        return float(result)

    def skip_target(self) -> None:
        """Consume an assignment target (``z``, ``jo(sp)``) without evaluating it."""
        if self.peek().kind != "name":
            raise BasicEvalError(f"not an assignment: {self.src!r}")
        self.pos += 1
        if self.at("sym", "("):
            depth = 0
            while True:
                tok = self.take()
                if tok.kind == "end":
                    raise BasicEvalError(f"unbalanced parentheses in {self.src!r}")
                if tok.text == "(":
                    depth += 1
                elif tok.text == ")":
                    depth -= 1
                    if depth == 0:
                        break


def eval_expr(expr: str, values: Mapping[str, float | Sequence[float]]) -> float:
    """Evaluate a bare BASIC expression; see the module docstring."""
    ev = _Evaluator(expr, values)
    result = ev.expr()
    ev.finish()
    return result


def eval_assignment(stmt: str, values: Mapping[str, float | Sequence[float]]) -> float:
    """Evaluate the right-hand side of one BASIC assignment; see the module docstring."""
    ev = _Evaluator(stmt, values)
    if ev.at("kw", "let"):
        ev.pos += 1
    ev.skip_target()
    if not ev.at("sym", "=") or ev.toks[ev.pos + 1].text in ("<", ">", "="):
        raise BasicEvalError(f"not an assignment: {stmt!r}")
    ev.pos += 1
    result = ev.expr()
    ev.finish()
    return result

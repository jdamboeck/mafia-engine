"""Tests for the C64 BASIC expression evaluator used by port tests.

Every expected value here was computed by hand from Commodore BASIC V2 rules:
comparisons yield -1 (true) or 0 (false), ``int`` is floor, ``and``/``or``/``not``
are 16-bit two's-complement bitwise operations, variable names are significant to
two characters, and the tokenizer matches keywords before identifiers.
"""

from __future__ import annotations

import pytest

from tests.basic_eval import BasicEvalError, eval_assignment, eval_expr

# --- The plan's scenarios, on formulas quoted from the game ---------------------


@pytest.mark.parametrize(("jo", "expected"), [(1, 3), (2, 0), (3, 3), (4, 3)])
def test_completion_score_assignment(jo: int, expected: int) -> None:
    assert eval_assignment("z=3+3*(jo(sp)=2)", {"sp": 1, "jo(1)": jo}) == expected


@pytest.mark.parametrize(("ln", "expected"), [(1, 150), (2, 50), (3, 100), (4, 100), (5, 50)])
def test_crunched_or_splits_on_keyword(ln: int, expected: int) -> None:
    assert eval_expr("50-50*(ln=3orln=4)-100*(ln=1)", {"ln": ln}) == expected


def test_true_comparison_times_negative_constant_is_positive() -> None:
    assert eval_expr("-20*(i=2)", {"i": 2}) == 20
    assert eval_expr("-20*(i=2)", {"i": 3}) == 0
    assert eval_expr("2-4*(i=2)", {"i": 2}) == 6
    assert eval_expr("2-4*(i=2)", {"i": 1}) == 2


@pytest.mark.parametrize(("s", "expected"), [(1, 2), (2, 1)])
def test_side_toggle(s: int, expected: int) -> None:
    assert eval_assignment("s=1-(s=1)", {"s": s}) == expected
    assert eval_expr("1-(s=1)", {"s": s}) == expected


def test_int_rnd_with_arrays_and_division() -> None:
    values = {"rnd(1)": 0.5, "w": 3, "tg(3)": 10, "bt": 35}
    # int(0.5*10 + 35/10) + 1 = int(8.5) + 1 = 9
    assert eval_expr("int(rnd(1)*tg(w)+bt/10)+1", values) == 9


@pytest.mark.parametrize(("la", "ln", "expected"), [(10, 1, -1), (10, 2, 0), (9, 1, 0), (9, 2, 0)])
def test_crunched_and_of_comparisons(la: int, ln: int, expected: int) -> None:
    assert eval_expr("(la=10andln=1)", {"la": la, "ln": ln}) == expected


def test_int_is_floor_not_truncation() -> None:
    assert eval_expr("int(-2.5)", {}) == -3
    assert eval_expr("int(2.5)", {}) == 2
    assert eval_expr("int(-3)", {}) == -3


def test_bitwise_and_or_not() -> None:
    assert eval_expr("-1and5", {}) == 5
    assert eval_expr("12and10", {}) == 8
    assert eval_expr("5or2", {}) == 7
    assert eval_expr("-1or0", {}) == -1
    assert eval_expr("not0", {}) == -1
    assert eval_expr("not-1", {}) == 0
    assert eval_expr("not5", {}) == -6
    assert eval_expr("xandy", {"x": 12, "y": 10}) == 8


def test_names_are_significant_to_two_characters() -> None:
    assert eval_expr("mole+mo", {"mo": 5}) == 10
    assert eval_expr("ln", {"lnx": 3}) == 3
    assert eval_expr("joker(1)", {"jo(1)": 4}) == 4


def test_mapping_keys_colliding_on_two_characters_must_agree() -> None:
    with pytest.raises(BasicEvalError, match="same variable"):
        eval_expr("mo", {"money": 1, "mo": 2})
    assert eval_expr("mo", {"money": 2, "mo": 2}) == 2


# --- Comparisons ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("1=1", -1),
        ("1=2", 0),
        ("1<>2", -1),
        ("1><2", -1),
        ("2<>2", 0),
        ("1<2", -1),
        ("2<1", 0),
        ("2>1", -1),
        ("1>2", 0),
        ("2<=2", -1),
        ("2=<2", -1),
        ("3<=2", 0),
        ("2>=2", -1),
        ("2=>2", -1),
        ("1>=2", 0),
    ],
)
def test_comparisons_yield_minus_one_or_zero(expr: str, expected: int) -> None:
    assert eval_expr(expr, {}) == expected


def test_relational_chain_is_left_associative() -> None:
    # (3=3)=-1 -> -1=-1 -> -1 ; (1<2)<3 -> -1<3 -> -1 ; (3>2)>1 -> -1>1 -> 0
    assert eval_expr("3=3=-1", {}) == -1
    assert eval_expr("1<2<3", {}) == -1
    assert eval_expr("3>2>1", {}) == 0


# --- Precedence -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("2+3*4", 14),
        ("6/2*3", 9),
        ("2-3-1", -2),
        ("-2*3+1", -5),
        ("2*-3+1", -5),
        ("--2", 2),
        ("+2", 2),
        ("1+2=3", -1),  # relational is looser than +
        ("not2=3", -1),  # not(2=3), not (not 2)=3
        ("1or2and0", 1),  # and binds tighter than or
        ("notland0", 0),  # (not l) and 0: not is tighter than and
        ("7/2", 3.5),
        ("1.5*2", 3),
        (".5*4", 2),
        ("1e2+1", 101),
        (" 1 + 2 ", 3),
    ],
)
def test_precedence_and_numbers(expr: str, expected: float) -> None:
    assert eval_expr(expr, {"l": 0}) == expected


# --- Arrays, integer variables and rnd ---------------------------------------------


def test_array_index_is_an_expression() -> None:
    values = {"sp": 1, "jo(2)": 7, "a(1,2)": 9}
    assert eval_expr("jo(sp+1)", values) == 7
    assert eval_expr("a(sp,sp*2)", values) == 9
    assert eval_expr("jo(2.9)", values) == 7  # subscripts truncate


def test_assignment_to_array_element_and_let() -> None:
    assert eval_assignment("jo(sp)=5+sp", {"sp": 1}) == 6
    assert eval_assignment("let z=4", {}) == 4


def test_integer_variable_is_distinct_name() -> None:
    assert eval_expr("x5%+x5", {"x5%": 1, "x5": 10}) == 11


def test_rnd_sequence_is_consumed_left_to_right() -> None:
    assert eval_expr("rnd(1)+rnd(1)*10", {"rnd(1)": [0.1, 0.2]}) == pytest.approx(2.1)


# --- Errors: never guess ------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        '"abc"',
        "a$",
        "peek(53280)",
        "sin(1)",
        "2^3",
        "a:b=1",
        "(1+2",
        "1+",
        "1 2",
        "rnd(0)",
        "fort",
        "money",  # m on ey: the tokenizer finds the keyword `on`
    ],
)
def test_unsupported_syntax_raises(expr: str) -> None:
    with pytest.raises(BasicEvalError):
        eval_expr(expr, {"a": 1, "b": 1, "t": 1})


def test_unknown_variable_raises() -> None:
    with pytest.raises(BasicEvalError, match="zz"):
        eval_expr("zz+1", {})


def test_bitwise_operands_must_be_16_bit_integers() -> None:
    with pytest.raises(BasicEvalError):
        eval_expr("40000and1", {})
    with pytest.raises(BasicEvalError):
        eval_expr("1.5and1", {})


def test_rnd_sequence_exhausted_raises() -> None:
    with pytest.raises(BasicEvalError, match="rnd"):
        eval_expr("rnd(1)+rnd(1)", {"rnd(1)": [0.1]})


def test_assignment_requires_an_assignment() -> None:
    with pytest.raises(BasicEvalError):
        eval_assignment("3+4", {})

"""Tests for the guard DSL evaluator (U6, PART 1).

Proof-first: these tests were written and observed RED (module missing) before
``engine/conditions.py`` existed.

The guard DSL is the declarative precondition layer for menu options
(docs/design/config-and-content-contract.md). Constraints under test: seven operators, and/or connectives,
nesting depth ≤ 2, NO NOT, a fixed resolvable-variable set, and the three real
guards (slw rent, slw lease, pub recruit) from the research.
"""

import pytest

from engine.conditions import build_context, evaluate, validate
from engine.state import Clock, Gangster, GameState, Player


def _state(*, rank=1, ka=0, gf=0.0, ms=0, po=18, roster=0, active=0, players=1):
    """Build a GameState with one active player carrying the given stats."""
    plist = [Player() for _ in range(players)]
    p = plist[active]
    p.rank = rank
    p.ka = ka
    p.gf = gf
    p.ms = ms
    p.po = po
    p.roster = [Gangster() for _ in range(roster)]
    return GameState(players=plist, clock=Clock(active_player=active, player_count=players))


# --- Each operator at depth 1: passing + failing ---------------------------

def test_op_eq():
    ctx = build_context(_state(rank=3))
    assert evaluate({"var": "rank", "op": "=", "value": 3}, ctx) is True
    assert evaluate({"var": "rank", "op": "=", "value": 4}, ctx) is False


def test_op_neq():
    ctx = build_context(_state(rank=3))
    assert evaluate({"var": "rank", "op": "!=", "value": 4}, ctx) is True
    assert evaluate({"var": "rank", "op": "!=", "value": 3}, ctx) is False


def test_op_gte():
    ctx = build_context(_state(rank=5))
    assert evaluate({"var": "rank", "op": ">=", "value": 5}, ctx) is True
    assert evaluate({"var": "rank", "op": ">=", "value": 6}, ctx) is False


def test_op_lte():
    ctx = build_context(_state(rank=5))
    assert evaluate({"var": "rank", "op": "<=", "value": 5}, ctx) is True
    assert evaluate({"var": "rank", "op": "<=", "value": 4}, ctx) is False


def test_op_gt():
    ctx = build_context(_state(rank=5))
    assert evaluate({"var": "rank", "op": ">", "value": 4}, ctx) is True
    assert evaluate({"var": "rank", "op": ">", "value": 5}, ctx) is False


def test_op_lt():
    ctx = build_context(_state(rank=5))
    assert evaluate({"var": "rank", "op": "<", "value": 6}, ctx) is True
    assert evaluate({"var": "rank", "op": "<", "value": 5}, ctx) is False


def test_op_in():
    ctx = build_context(_state(rank=3))
    assert evaluate({"var": "rank", "op": "in", "value": [1, 2, 3]}, ctx) is True
    assert evaluate({"var": "rank", "op": "in", "value": [4, 5]}, ctx) is False


# --- Connectives at depth 1 and depth 2 ------------------------------------

def test_and_depth1():
    ctx = build_context(_state(rank=5, ka=100))
    guard_true = {"and": [
        {"var": "rank", "op": ">", "value": 4},
        {"var": "ka", "op": ">=", "value": 100},
    ]}
    guard_false = {"and": [
        {"var": "rank", "op": ">", "value": 4},
        {"var": "ka", "op": ">=", "value": 200},
    ]}
    assert evaluate(guard_true, ctx) is True
    assert evaluate(guard_false, ctx) is False


def test_or_depth1():
    ctx = build_context(_state(rank=2, ka=100))
    guard_true = {"or": [
        {"var": "rank", "op": ">", "value": 4},   # false
        {"var": "ka", "op": ">=", "value": 100},  # true
    ]}
    guard_false = {"or": [
        {"var": "rank", "op": ">", "value": 4},    # false
        {"var": "ka", "op": ">=", "value": 200},   # false
    ]}
    assert evaluate(guard_true, ctx) is True
    assert evaluate(guard_false, ctx) is False


def test_connective_of_connectives_depth2():
    ctx = build_context(_state(rank=5, ka=100, ms=3))
    # depth 2: outer or, children are connectives-of-leaves
    guard = {"or": [
        {"and": [
            {"var": "rank", "op": ">", "value": 4},
            {"var": "ka", "op": ">=", "value": 100},
        ]},
        {"and": [
            {"var": "ms", "op": ">", "value": 5},   # false
            {"var": "ka", "op": ">=", "value": 100},
        ]},
    ]}
    assert evaluate(guard, ctx) is True


# --- Structural rejections -------------------------------------------------

def test_depth3_rejected():
    ctx = build_context(_state())
    guard = {"or": [
        {"and": [
            {"or": [{"var": "rank", "op": "=", "value": 1}]},  # depth-3 connective
        ]},
    ]}
    with pytest.raises(ValueError):
        evaluate(guard, ctx)


def test_not_rejected():
    ctx = build_context(_state())
    with pytest.raises(ValueError):
        evaluate({"not": {"var": "rank", "op": "=", "value": 1}}, ctx)


def test_unknown_operator_rejected():
    ctx = build_context(_state())
    with pytest.raises(ValueError):
        evaluate({"var": "rank", "op": "~=", "value": 1}, ctx)


def test_leaf_with_stray_connective_key_rejected():
    # A leaf that also carries an `and` must be a hard error, not a silently
    # dropped branch — otherwise a hidden branch (incl. a NOT inside it) slips
    # past the depth/no-NOT contract.
    ctx = build_context(_state())
    sneaky = {
        "var": "rank", "op": "=", "value": 1,
        "and": [{"not": {"var": "rank", "op": "=", "value": 1}}],
    }
    with pytest.raises(ValueError):
        evaluate(sneaky, ctx)
    with pytest.raises(ValueError):
        validate(sneaky)


def test_leaf_with_unknown_extra_key_rejected():
    ctx = build_context(_state())
    with pytest.raises(ValueError):
        evaluate({"var": "rank", "op": "=", "value": 1, "typo": 3}, ctx)


def test_type_mismatch_comparison_raises_value_error_not_typeerror():
    # rank (int) compared with '>' to a str: must surface as the module's
    # ValueError contract, not a raw TypeError available_options can't reason about.
    ctx = build_context(_state())
    with pytest.raises(ValueError):
        evaluate({"var": "rank", "op": ">", "value": "x"}, ctx)


def test_unknown_variable_rejected():
    ctx = build_context(_state())
    with pytest.raises(ValueError):
        evaluate({"var": "nonesuch", "op": "=", "value": 1}, ctx)


def test_empty_guard_is_true():
    ctx = build_context(_state())
    assert evaluate(None, ctx) is True
    assert evaluate({}, ctx) is True


# --- Resolvable-variable set -----------------------------------------------

def test_all_named_variables_resolve():
    st = _state(rank=7, ka=500, gf=42.0, ms=4, po=20, roster=2, active=1, players=3)
    ctx = build_context(st, ln=5)
    assert evaluate({"var": "rank", "op": "=", "value": 7}, ctx) is True
    assert evaluate({"var": "ka", "op": "=", "value": 500}, ctx) is True
    assert evaluate({"var": "gf", "op": "=", "value": 42.0}, ctx) is True
    assert evaluate({"var": "ms", "op": "=", "value": 4}, ctx) is True
    assert evaluate({"var": "po", "op": "=", "value": 20}, ctx) is True
    assert evaluate({"var": "gang_size", "op": "=", "value": 2}, ctx) is True
    assert evaluate({"var": "sp", "op": "=", "value": 1}, ctx) is True


def test_tenancy_requires_ln():
    st = _state()
    st.map.tenancy[5] = 2
    ctx = build_context(st, ln=None)
    with pytest.raises(ValueError):
        evaluate({"var": "tenancy", "op": "=", "value": 0}, ctx)


# --- The three REAL guards (docs/design/engine-architecture.md § Testing and verification) --------

def test_slw_rent_guard():
    """slw option 1 (rent): available when unit is free — tenancy==0.

    Research: mf-prg.bas:10010, denied when uk(ln)<>0 -> "nichts mehr frei".
    """
    guard = {"var": "tenancy", "op": "=", "value": 0}
    free = _state()
    ctx_free = build_context(free, ln=3)  # tenancy.get(3,0) == 0
    assert evaluate(guard, ctx_free) is True

    occupied = _state()
    occupied.map.tenancy[3] = 2
    ctx_occ = build_context(occupied, ln=3)
    assert evaluate(guard, ctx_occ) is False


def test_slw_lease_guard_variable_rhs():
    """slw option 2 (manage lease): available when you are the tenant — tenancy==sp.

    Uses the RHS variable extension: value = {"var": "sp"}.
    Research: mf-prg.bas:10100, denied when uk(ln)<>sp -> "du wohnst hier nicht".
    """
    guard = {"var": "tenancy", "op": "=", "value": {"var": "sp"}}

    # active player is index 1 and is the tenant of tile 3 -> passes
    st = _state(active=1, players=3)
    st.map.tenancy[3] = 1
    ctx = build_context(st, ln=3)
    assert evaluate(guard, ctx) is True

    # another player is the tenant -> fails
    st2 = _state(active=1, players=3)
    st2.map.tenancy[3] = 2
    ctx2 = build_context(st2, ln=3)
    assert evaluate(guard, ctx2) is False


def test_pub_recruit_guard():
    """pub recruit: available when rank>4 AND gang has room (<10)."""
    guard = {"and": [
        {"var": "rank", "op": ">", "value": 4},
        {"var": "gang_size", "op": "<", "value": 10},
    ]}
    ok = build_context(_state(rank=5, roster=3))
    assert evaluate(guard, ok) is True

    bad_rank = build_context(_state(rank=3, roster=3))
    assert evaluate(guard, bad_rank) is False

    full_gang = build_context(_state(rank=5, roster=10))
    assert evaluate(guard, full_gang) is False

"""Tests for the seedable, logged RNG (U2).

The RNG is the single nondeterminism source for the engine. Every public draw
(``range``/``hit``) appends exactly one record to an in-memory log so that
event-sourced replay is additive later (KTD-6). Determinism follows from
seeding a stdlib ``random.Random`` (KTD-4 — we match probabilities, not the
C64 bitstream).
"""

import pytest

from engine.rng import Rng


# ---------------------------------------------------------------------------
# Determinism (happy path)
# ---------------------------------------------------------------------------


def _script(rng: Rng) -> list[int]:
    """Run a fixed script of mixed draws, returning the drawn values."""
    out: list[int] = []
    for _ in range(200):
        out.append(rng.range(9))
        out.append(rng.hit(10, 50))
        out.append(rng.hit(-5, 5))
    return out


def test_same_seed_identical_sequences():
    a = Rng(1234)
    b = Rng(1234)
    assert _script(a) == _script(b)


def test_different_seeds_diverge():
    a = _script(Rng(1))
    b = _script(Rng(2))
    # Statistically all-but-certain to differ over 600 mixed draws.
    assert a != b


# ---------------------------------------------------------------------------
# Bounds (happy path + edges)
# ---------------------------------------------------------------------------


def test_range_bounds():
    rng = Rng(7)
    seen = set()
    for _ in range(1000):
        v = rng.range(9)
        assert 0 <= v <= 8
        seen.add(v)
    # Extremes reachable over enough draws.
    assert 0 in seen
    assert 8 in seen


def test_hit_bounds_inclusive():
    rng = Rng(7)
    seen = set()
    for _ in range(1000):
        v = rng.hit(10, 50)
        assert 10 <= v <= 50
        seen.add(v)
    assert 10 in seen
    assert 50 in seen


def test_hit_negative_range():
    rng = Rng(3)
    for _ in range(1000):
        v = rng.hit(-5, 5)
        assert -5 <= v <= 5


def test_hit_single_value():
    rng = Rng(99)
    for _ in range(50):
        assert rng.hit(7, 7) == 7


# ---------------------------------------------------------------------------
# Log correctness (the KTD-6 seam)
# ---------------------------------------------------------------------------


def test_log_length_matches_public_calls():
    rng = Rng(42)
    rng.range(9)
    rng.hit(10, 50)
    rng.hit(1, 1)
    # hit must NOT double-count even though it may draw via range internally.
    assert len(rng.log) == 3


def test_log_preserves_order_and_values():
    rng = Rng(42)
    returned: list[int] = []
    returned.append(rng.range(9))
    returned.append(rng.hit(10, 50))
    returned.append(rng.range(3))
    returned.append(rng.hit(-5, 5))

    assert len(rng.log) == len(returned)
    # Each record's recorded value equals the value actually returned, in order.
    logged_values = [rec[-1] for rec in rng.log]
    assert logged_values == returned


def test_log_records_method_and_args():
    rng = Rng(42)
    r = rng.range(9)
    h = rng.hit(10, 50)

    range_rec = rng.log[0]
    hit_rec = rng.log[1]

    assert range_rec[0] == "range"
    assert tuple(range_rec[1]) == (9,)
    assert range_rec[-1] == r

    assert hit_rec[0] == "hit"
    assert tuple(hit_rec[1]) == (10, 50)
    assert hit_rec[-1] == h


# ---------------------------------------------------------------------------
# Error paths (must NOT append to the log)
# ---------------------------------------------------------------------------


def test_range_zero_raises_and_does_not_log():
    rng = Rng(1)
    with pytest.raises(ValueError):
        rng.range(0)
    assert rng.log == []


def test_range_negative_raises_and_does_not_log():
    rng = Rng(1)
    with pytest.raises(ValueError):
        rng.range(-1)
    assert rng.log == []


def test_hit_inverted_bounds_raises_and_does_not_log():
    rng = Rng(1)
    with pytest.raises(ValueError):
        rng.hit(5, 3)
    assert rng.log == []

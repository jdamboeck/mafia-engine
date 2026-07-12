"""The engine's single nondeterminism source.

One seedable RNG. Every public draw is appended to an in-memory log so that
event-sourced replay can be layered on additively later without touching the
draw sites (KTD-6).

Design notes
------------
- Backed by stdlib ``random.Random(seed)``. We match the original game's
  *probabilities*, not its C64 ``rnd()`` bitstream (KTD-4), so a standard PRNG
  seeded with an ``int`` is the correct, fully-deterministic backend.
- The original's core idiom is ``int(rnd(1)*N)`` -> a uniform integer in
  ``[0, N-1]`` (e.g. ``mf-prg.bas:350`` rolls ``int(rnd(1)*9)*5+10`` for the
  10..50 stat). ``range(n)`` is that primitive; ``hit(a, b)`` is expressed on
  top of it so both public methods flow through one draw path.

Log record shape
----------------
Each record is a 3-tuple ``(method_name: str, args: tuple, value: int)`` in
call order, e.g. ``("range", (9,), 4)`` or ``("hit", (10, 50), 37)``.
"""

from __future__ import annotations

import random


class Rng:
    """Seedable, logged uniform integer generator.

    Public draws:

    - ``range(n)`` -> uniform int in ``[0, n-1]``.
    - ``hit(a, b)`` -> uniform int in ``[a, b]`` inclusive.

    Every public draw appends exactly one record to :attr:`log`. Invalid
    arguments raise ``ValueError`` and append nothing.
    """

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)
        self._log: list[tuple[str, tuple, int]] = []

    @property
    def log(self) -> list[tuple[str, tuple, int]]:
        """Ordered record of every public draw. See module docstring for shape."""
        return self._log

    def range(self, n: int) -> int:
        """Uniform integer in ``[0, n-1]``. Raises ``ValueError`` if ``n <= 0``."""
        if n <= 0:
            raise ValueError(f"range(n) requires n > 0, got {n!r}")
        value = self._draw(n)
        self._log.append(("range", (n,), value))
        return value

    def hit(self, a: int, b: int) -> int:
        """Uniform integer in ``[a, b]`` inclusive. Raises ``ValueError`` if ``b < a``."""
        if b < a:
            raise ValueError(f"hit(a, b) requires b >= a, got a={a!r}, b={b!r}")
        # Draw via the shared primitive so hit and range share one code path,
        # but log exactly once (for this public call) below.
        value = a + self._draw(b - a + 1)
        self._log.append(("hit", (a, b), value))
        return value

    def _draw(self, n: int) -> int:
        """Uniform int in ``[0, n-1]``. The single, un-logged RNG touch point."""
        return self._random.randrange(n)

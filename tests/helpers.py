"""Shared, importable test helpers (distinct from ``conftest.py`` fixtures).

``conftest.py`` holds pytest *fixtures* (injected by name); this module holds
plain callables that any test module can ``from tests.helpers import ...``. Kept
here — rather than as a fixture — precisely so both ``test_slw.py`` and
``test_driver.py`` can import the same function.

The centerpiece is :func:`run_pure`, a **handler purity harness**. Handlers must
NEVER mutate ``GameState`` directly — they only buffer effects via ``ctx.apply``
(see ``engine.interactions``/``engine.effects``). This harness enforces that
contract by comparison (no read-only proxy is required):

1. deep-copy the input state BEFORE running,
2. run the handler through :func:`engine.interactions.run`,
3. independently replay ``result.effects`` onto the pre-run snapshot via
   :func:`engine.effects.commit`,
4. assert (a) the caller's input state was NOT mutated and (b) the driver's
   returned state is FULLY EXPLAINED by the committed effects (no hidden direct
   mutation).

A handler that sneaks a direct ``ctx.state.<...>`` mutation past the effect
buffer is caught by assertion (b): its returned state diverges from the
independent effect replay.
"""

from __future__ import annotations

import copy
from typing import Any

from engine.effects import commit
from engine.interactions import run


def run_pure(handler, input_source, *, state, rng=None):
    """Run ``handler`` through the driver and assert it did not mutate state directly.

    Args mirror :func:`engine.interactions.run` but ``state`` is REQUIRED (the
    harness has nothing to compare against without one).

    Asserts:
        * the caller's input ``state`` is unchanged (equals a pre-run snapshot);
        * ``result.state`` equals the state obtained by independently committing
          ``result.effects`` onto that snapshot — i.e. every observable state
          change is explained by a buffered effect, none by direct mutation.

    On cancel (``result.status == "cancelled"``) the driver returns the ORIGINAL
    state object with empty effects; both assertions still hold trivially (empty
    replay == snapshot == unchanged input), and this additionally proves the
    identity contract below.

    Returns:
        The :class:`~engine.actions.EngineResult` so callers keep asserting on it.
    """
    snapshot = copy.deepcopy(state)

    result = run(handler, input_source, state=state, rng=rng)

    # (a) The handler must not have mutated the caller's state in place.
    assert state == snapshot, (
        "handler (or driver) mutated the INPUT state in place; the input GameState "
        "must be left untouched — all changes belong on result.state via effects"
    )

    # (b) The returned state must be fully explained by the committed effects:
    # replaying them onto the untouched snapshot must reproduce result.state exactly.
    expected: Any = commit(snapshot, list(result.effects)).state
    assert result.state == expected, (
        "result.state is NOT explained by result.effects — the handler mutated "
        "ctx.state directly instead of buffering an effect via ctx.apply(). "
        "Independently committing result.effects onto the pre-run snapshot yields "
        "a DIFFERENT state than the driver returned."
    )

    # On cancel the driver contract is stronger: the SAME object is handed back.
    if result.status == "cancelled":
        assert result.state is state, (
            "on cancel the driver must return the ORIGINAL state object unchanged"
        )

    return result

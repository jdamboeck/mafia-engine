"""Shared, importable test helpers (distinct from ``conftest.py`` fixtures).

``conftest.py`` holds pytest *fixtures* (injected by name); this module holds
plain callables that any test module can ``from tests.helpers import ...``. Kept
here — rather than as a fixture — precisely so both ``test_slw.py`` and
``test_driver.py`` can import the same function.

The centerpiece is :func:`run_pure`, a **handler purity harness**. Handlers must
NEVER mutate ``GameState`` directly — they only buffer effects via ``ctx.apply``
(see ``engine.interactions``/``engine.effects``). The harness:

1. snapshots the input state's VALUES (not its identity — see below),
2. runs the handler through :func:`engine.interactions.run`,
3. independently replays ``result.effects`` onto a state rebuilt from that
   snapshot via :func:`engine.effects.commit`,
4. asserts the input is unchanged and the driver's returned state is FULLY
   EXPLAINED by the committed effects (no hidden direct mutation).

**Why the snapshot must be by value.** Freezing the graph makes a plain
``ctx.state.<...>`` write raise at the offending line, which covers the common
case. But ``object.__setattr__`` bypasses frozen-ness, and that is exactly what
this harness is the compensating control for. A by-identity snapshot
(``snapshot = state``) would make step 4 vacuous twice over: the baseline mutates
along with the state it is compared against, and replaying from the same object
the driver already committed against compares ``commit``'s output with itself.
``tests/test_driver.py`` has a negative self-test pinning that this harness fails
on a genuinely-mutating handler — without it, a weakened harness stays green.

The ``with_*`` helpers below are the frozen-graph replacement for the old
``state.players[0].field = x`` arrange idiom.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any

from engine.effects import commit
from engine.interactions import run
from engine.persistence import _json_safe, _state_from_dict
from engine.state import tuple_replace


def with_player(state, idx: int = 0, **field_changes):
    """Return ``state`` with ``players[idx]`` field-updated — the test-side setup idiom.

    The state graph is frozen (R1), so a test can no longer arrange a scenario with
    ``state.players[0].po = 141``. This is the construction-shaped replacement, kept
    here so the arrange step stays one readable line.
    """
    new_player = dataclasses.replace(state.players[idx], **field_changes)
    return dataclasses.replace(
        state, players=tuple_replace(state.players, idx, new_player)
    )


def with_clock(state, **field_changes):
    """Return ``state`` with ``clock`` field-updated (frozen-graph test setup idiom)."""
    return dataclasses.replace(state, clock=dataclasses.replace(state.clock, **field_changes))


def with_tenancy(state, tenancy):
    """Return ``state`` with ``map.tenancy`` replaced by ``tenancy`` (read-only).

    Wraps in a proxy so a fixture cannot hand the engine a mutable mapping and
    quietly reopen the write path the freeze exists to close.
    """
    return dataclasses.replace(
        state, map=dataclasses.replace(state.map, tenancy=MappingProxyType(dict(tenancy)))
    )


def with_config(state, **field_changes):
    """Return ``state`` with ``config`` field-updated (frozen-graph test setup idiom)."""
    return dataclasses.replace(
        state, config=dataclasses.replace(state.config, **field_changes)
    )


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
    # Snapshot the VALUES, not the object. `snapshot = state` would be worthless: the
    # graph is frozen, so the object cannot change — but `object.__setattr__` bypasses
    # frozen-ness, and a by-identity snapshot makes both assertions below vacuous (the
    # baseline mutates along with the state it is meant to be compared against).
    # deepcopy is unavailable here — mappingproxy fields are unpicklable — so walk the
    # graph into plain containers instead.
    snapshot = _json_safe(state)

    result = run(handler, input_source, state=state, rng=rng)

    # (a) The handler must not have mutated the caller's state in place. Frozen
    # dataclasses already make a plain attribute write raise at the offending line;
    # this catches the one escape freezing cannot close (object.__setattr__).
    assert _json_safe(state) == snapshot, (
        "handler (or driver) mutated the INPUT state in place; the input GameState "
        "must be left untouched — all changes belong on result.state via effects"
    )

    # (b) The returned state must be fully explained by the committed effects:
    # replaying them onto the untouched snapshot must reproduce result.state exactly.
    # Replay from the SNAPSHOT VALUES, not from `state` — the driver computed
    # result.state as commit(state, buffer), so recommitting against that same object
    # would compare commit's output with itself and could never fail.
    expected: Any = commit(_state_from_dict(snapshot), list(result.effects)).state
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

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
import io
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from dataclasses import replace

from data.game_configs.mafia_1920s.combat_rules import build_rules, equipper
from engine.combat import CombatFight
from engine.effects import commit
from engine.interactions import run
from engine.persistence import state_from_dict
from engine.state import CombatState, Fighter, json_safe, tuple_replace


def make_walk_script(keys: list[str]) -> io.StringIO:
    """Build the piped-stdin body for ``play()``: two blank acks + one key/line.

    ``play()`` reads one line for "press a key" on the title screen, THEN (U3, KTD-3)
    one more for the turn-start upkeep screen's "press any key..." ack — BOTH before
    the map loop starts — so every scripted stdin must account for both. See
    ``docs/solutions/developer-experience/driving-terminal-play-loop-over-piped-stdin.md``.
    Every ``advance_turn`` rotation triggers a THIRD such ack for the new active
    player's upkeep — callers scripting a multi-turn session add one blank/any-key
    line per rotation on top of the two this builder prepends.
    """
    return io.StringIO("\n".join(["", ""] + keys) + "\n")


def scripted(*answers):
    """An ``input_source`` answering PROMPTS in order, while RECEIVING narration.

    Since #43 the driver hands every ``ShowMessage`` to the input source too — for
    delivery, not for an answer (it discards the return value and acks regardless).
    A scripted source must therefore distinguish the two: a ``ShowMessage`` is
    *recorded and ignored*, and only a real prompt consumes the next scripted answer.
    Doing it the other way round — letting narration eat a scripted answer — would
    silently desynchronize every script whenever a handler's flavour text changes.

    Strictness is preserved, and that is the point: running past the end of the script
    still raises, so a handler that yields an unexpected *prompt* fails loudly rather
    than being answered with a fabricated value.

    The returned callable exposes:

    ``seen``
        Every interaction the driver presented, in order (prompts and messages).
    ``messages()``
        Callable returning just the ``ShowMessage`` interactions delivered so far —
        so a test whose subject IS the narration can assert on what a client would
        have rendered. ``message_keys()`` returns their keys.
    """
    from engine.interactions import ShowMessage

    it = iter(answers)
    seen: list = []

    def source(interaction):
        seen.append(interaction)
        if isinstance(interaction, ShowMessage):
            # Delivered, not asked. Consumes no scripted answer; the driver acks.
            return None
        try:
            return next(it)
        except StopIteration:
            raise AssertionError(
                f"input_source exhausted; driver asked again for {interaction!r}"
            ) from None

    source.seen = seen
    source.messages = lambda: [i for i in seen if isinstance(i, ShowMessage)]
    source.message_keys = lambda: [i.key for i in seen if isinstance(i, ShowMessage)]
    return source


def with_player(state, idx: int = 0, **field_changes):
    """Return ``state`` with ``players[idx]`` field-updated — the test-side setup idiom.

    The state graph is frozen (R1), so a test can no longer arrange a scenario with
    ``state.players[0].po = 141``. This is the construction-shaped replacement, kept
    here so the arrange step stays one readable line.
    """
    new_player = dataclasses.replace(state.players[idx], **field_changes)
    return dataclasses.replace(state, players=tuple_replace(state.players, idx, new_player))


def with_clock(state, **field_changes):
    """Return ``state`` with ``clock`` field-updated (frozen-graph test setup idiom)."""
    return dataclasses.replace(state, clock=dataclasses.replace(state.clock, **field_changes))


def with_tenancy(state, tenancy=None, *, ln=None, owner=None):
    """Return ``state`` with ``map.tenancy`` arranged for a test.

    Two shapes, since fixtures need both: pass a whole ``tenancy`` mapping to
    replace it outright, or ``ln=``/``owner=`` to set one tile on top of what is
    already there.

    Wraps the result in a proxy so a fixture cannot hand the engine a mutable
    mapping and quietly reopen the write path the freeze exists to close.
    """
    if (tenancy is None) == (ln is None):
        raise TypeError(
            "with_tenancy takes either a tenancy mapping or ln=/owner=, not both or neither"
        )
    if ln is not None:
        tenancy = {**state.map.tenancy, ln: owner}
    return dataclasses.replace(
        state, map=dataclasses.replace(state.map, tenancy=MappingProxyType(dict(tenancy)))
    )


def with_config(state, **field_changes):
    """Return ``state`` with ``config`` field-updated (frozen-graph test setup idiom)."""
    return dataclasses.replace(state, config=dataclasses.replace(state.config, **field_changes))


def _shape(value):
    """Return a type fingerprint of ``value``'s whole graph, ignoring its contents.

    :func:`~engine.state.json_safe` exists to ERASE types — proxy to dict, tuple to list — so a
    value comparison built on it cannot see type drift. That blind spot is not
    cosmetic: swapping ``map.tenancy``'s ``MappingProxyType`` for a plain ``dict``
    reopens the exact R2 false floor the frozen graph closes, and both sides
    flatten to the same JSON. Python's own coercions hide more (``0 == False``,
    ``5000 == 5000.0``), so an int silently becoming a float — the corruption that
    surfaces only on load — would compare equal too.

    Pairing this fingerprint with the value check closes both.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (
            type(value).__name__,
            {f.name: _shape(getattr(value, f.name)) for f in dataclasses.fields(value)},
        )
    if isinstance(value, Mapping):
        return (type(value).__name__, {k: _shape(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, [_shape(v) for v in value])
    return type(value).__name__


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
    snapshot = json_safe(state)
    shape = _shape(state)

    result = run(handler, input_source, state=state, rng=rng)

    # (a) The handler must not have mutated the caller's state in place. Frozen
    # dataclasses already make a plain attribute write raise at the offending line;
    # this catches the one escape freezing cannot close (object.__setattr__).
    assert json_safe(state) == snapshot, (
        "handler (or driver) mutated the INPUT state in place; the input GameState "
        "must be left untouched — all changes belong on result.state via effects"
    )

    # (a2) Types too, not just values — see :func:`_shape`. Catches a read-only
    # collection downgraded to a mutable one (reopening the R2 false floor) and
    # scalar drift that Python's == would equate (0/False, 5000/5000.0).
    assert _shape(state) == shape, (
        "handler (or driver) changed the TYPE of a field on the input GameState. "
        "A read-only collection replaced by a mutable one reopens the write path "
        "the frozen graph exists to close; scalar type drift corrupts save/replay."
    )

    # (b) The returned state must be fully explained by the committed effects:
    # replaying them onto the untouched snapshot must reproduce result.state exactly.
    # Replay from the SNAPSHOT VALUES, not from `state` — the driver computed
    # result.state as commit(state, buffer), so recommitting against that same object
    # would compare commit's output with itself and could never fail.
    expected: Any = commit(state_from_dict(snapshot), list(result.effects)).state
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


# --------------------------------------------------------------------------- #
# Combat fixtures — shared by tests/test_combat_loop.py and tests/test_combat_ai.py #
# --------------------------------------------------------------------------- #
#: The weapon table for the reference title — ``(id, ts, tg, range)``. ``ts``/``tg``
#: are verbatim from mf-prg.bas:50100-50115; ``range`` is derived from 30215-30216
#: (2 for ids 0-3, 15 for ids > 3, then 20 for ids 6/7 — so id 8 lands on 15, not 20).
#: Both combat test modules exercised every one of these rows independently before
#: this hoist; kept as one table so the two files can never silently drift apart.
WEAPON_TABLE: tuple[tuple[int, int, int, int], ...] = (
    (0, 2, 2, 2),  # haende
    (1, 3, 5, 2),  # messer
    (2, 4, 3, 2),  # knueppel
    (3, 4, 4, 2),  # schlagkette
    (4, 2, 7, 15),  # wurfsterne
    (5, 5, 10, 15),  # revolver
    (6, 5, 12, 20),  # gewehr
    (7, 6, 15, 20),  # maschinenpistole
    (8, 7, 18, 15),  # handgranaten
)

WEAPON_STATS: dict[int, tuple[int, int, int]] = {
    w: (ts, tg, rng) for w, ts, tg, rng in WEAPON_TABLE
}


class StubRng:
    """Scripted RNG: returns queued values, records every call (determinism gate)."""

    def __init__(self, *values):
        self._values = list(values)
        self.calls = []

    def range(self, n):
        self.calls.append(("range", n))
        if not self._values:
            raise AssertionError(f"stub rng exhausted at range({n}); calls={self.calls}")
        return self._values.pop(0)

    def hit(self, a, b):
        self.calls.append(("hit", a, b))
        if not self._values:
            raise AssertionError(f"stub rng exhausted at hit({a},{b}); calls={self.calls}")
        return self._values.pop(0)


def combat_fighter(**kw) -> Fighter:
    """A :class:`~engine.state.Fighter` with sane combat-test defaults, overridable.

    Equips from the reference title's weapon table by default (amendment A1: every
    combatant enters a fight already carrying its equipment), so a fighter built here
    and dropped straight into a ``StartCombat`` fights correctly. Pass ``equipment=``
    to override, or a ``weapon`` id the default table knows. ``build_fight`` leaves an
    already-equipped fighter untouched, so this does not double-resolve.
    """
    base = dict(name="f", weapon=5, energie=20, kraft=30, brutalitaet=30, position=100)
    base.update(kw)
    if "equipment" not in base and base["weapon"] in WEAPON_STATS:
        base["equipment"] = equipper(WEAPON_STATS)(base["weapon"])
    return Fighter(**base)


def build_fight(
    *,
    side1,
    side2,
    grid=(),
    rng=None,
    dir_memory=None,
    weapon_stats=None,
    rules=None,
    active: tuple[int, int],
) -> CombatFight:
    """Build a :class:`~engine.combat.CombatFight` for a scripted test.

    ``active`` (``(active_side, active_fighter)``) is REQUIRED rather than defaulted
    here: ``tests/test_combat_loop.py`` exercises side 1 acting (``CombatState``'s own
    default) while ``tests/test_combat_ai.py`` exercises side 2/the CPU acting
    (``(2, 1)``) at nearly every one of its 33 call sites. A shared default would
    silently pick one file's convention for the other — each module's own thin
    ``_fight`` wrapper supplies its own default explicitly instead of relying on this
    one, so neither file's behavior moved when this builder was hoisted out of both.

    ``weapon_stats`` defaults to the reference title's table (:data:`WEAPON_STATS`);
    pass a different mapping to fight with weapons this game never defined, which is
    how the attribute-agnostic paths (reach, melee) are tested without game data.

    The table is used ONCE, here, to equip each fighter (amendment A1) — it is not
    handed to the fight. Callers keep writing ``weapon=<id>``; this resolves the id
    while the table is still in scope, so the fight itself never holds a lookup that
    could disagree with the roster.

    A fighter that ALREADY carries an ``equipment`` mapping is left as-is: a test that
    hand-builds equipment (e.g. one deliberately missing ``range``) is describing the
    combatant directly, and re-resolving its id from the table would overwrite that.
    """
    table = WEAPON_STATS if weapon_stats is None else weapon_stats
    equip = equipper(table)

    def equipped(f):
        return f if f.equipment else replace(f, equipment=equip(f.weapon))

    return CombatFight(
        CombatState(
            sides=(
                tuple(equipped(f) for f in side1),
                tuple(equipped(f) for f in side2),
            ),
            grid=tuple(grid),
            dir_memory=dict(dir_memory or {}),
            active_side=active[0],
            active_fighter=active[1],
        ),
        rng=rng,
        rules=build_rules() if rules is None else rules,
    )

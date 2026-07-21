"""Recording and replay of a fight — the observable, replayable transcript (U7, R12).

A **recording** is an ordered list of :class:`Event` sharing one monotonic ``index``
(which doubles as the seek key). It is a TAGGED UNION on ``kind``: an
:class:`ActivationEvent` per fighter activation, a :class:`HandoffEvent` when a side's
driver is reassigned between activations. Every variant carries ``index``, ``snapshot``,
``snapshot_shape_version`` and ``parent_index`` (a shared base), so a seek never
special-cases which kind sits at an index and a future third variant (an environmental
actor, reaction fire) is a new ``kind`` case and nothing else (plan §U7).

**Recording is "replay with snapshotting on."** The one function :func:`_replay_and_snapshot`
both *records* a live fight (driving it, capturing each activation's decision/draws/result,
snapshotting via :func:`engine.state.json_safe`) and *rebuilds* one whose stored snapshots
are stale (shape drift). There is no second serializer and no second replay engine.

**Replay is the fidelity detector.** :func:`replay` rebuilds a fresh fight from the
recorded scenario, then for each event feeds the recorded ``draws`` back through a
replay RNG (structurally :class:`~tests.helpers.StubRng`, sourced from the recording)
while calling the **live** formula code. A changed formula turns the same draw into a
different hit/damage, so the recomputed ``result`` no longer matches the recorded one and
:func:`replay` returns ``ReplayReport(diverged=True, at_index=…)``.

**Serialization is JSON via** :func:`engine.state.json_safe` — the SAME path save/load
uses for every state graph (never a second snapshot serializer). Recordings are RUN
ARTIFACTS, not versioned game content, so :func:`save`/:func:`load` are directory-agnostic
(U8 picks the directory). Shape versioning reuses :data:`engine.effects.SCHEMA_VERSION`;
on a load whose ``snapshot_shape_version`` mismatches, every snapshot is discarded and the
recording is rebuilt from index 0 at the current version.

``engine/`` imports nothing from ``server``/``clients``/config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from engine.effects import SCHEMA_VERSION
from engine.state import json_safe


def _json_normalize(value: Any) -> Any:
    """Round a json_safe value through JSON's own key/tuple normalization.

    :func:`json_safe` unwraps the frozen graph to dicts/lists but leaves INT dict keys
    (``dir_memory`` is keyed by fighter index) — which JSON silently stringifies on
    ``dump``. A snapshot kept in memory as a raw json_safe dict would then have int keys
    while the same snapshot after ``save``/``load`` has string keys, so an
    in-memory-vs-serialized comparison spuriously differs. Passing every snapshot through
    ``json.loads(json.dumps(...))`` makes the in-memory form identical to the serialized
    one — the snapshot IS a JSON document, so JSON's normalization is the canonical shape.
    A single normalizer, not a second serializer: it wraps the same :func:`json_safe` output.
    """
    return json.loads(json.dumps(value))


__all__ = [
    "Event",
    "ActivationEvent",
    "HandoffEvent",
    "Recording",
    "ReplayReport",
    "record_fight",
    "replay",
    "save",
    "load",
]


# --------------------------------------------------------------------------- #
# The event tagged union                                                      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Event:
    """Shared fields every recorded event carries — the seek/snapshot base (plan §U7).

    ``index`` is monotonic from 0 and doubles as the seek key. ``snapshot`` (a
    :func:`json_safe`-ed :class:`~engine.state.CombatState`) and its
    ``snapshot_shape_version`` live on EVERY variant so seeking to any index finds a
    board state without branching on ``kind``. ``parent_index`` reserves room for
    out-of-turn events (reaction fire, interrupts); the source's activation loop is
    strictly sequential (``mf-prg.bas:30105-30110``), so nothing in this arc sets it —
    it stays ``None`` on every recorded event.
    """

    index: int
    side: int
    snapshot: Any = None
    snapshot_shape_version: int | None = None
    parent_index: int | None = None


@dataclass(frozen=True)
class ActivationEvent(Event):
    """One fighter's activation: who acted, what they chose, every draw, the result.

    ``draws`` is the exact slice of ``rng.log`` this activation consumed, in order, as
    ``[(method, args, value), …]`` — replay feeds these back so the live formula sees the
    same randomness. ``calc_inputs`` names what the hit/damage formulas *read* (the
    accuracy/damage attribute values, the weapon stats, both draw bounds) so U8's
    ``--debug`` can print the arithmetic WITHOUT recomputing. ``result`` is the
    :meth:`~engine.combat.CombatFight.shoot` dict for a shot (empty for move/pass);
    replay recomputes and compares against it.
    """

    kind: str = "activation"
    fighter_index: int = 0  # 0-based index of the acting fighter within its side
    driver_kind: str = "human"
    decision: dict = field(default_factory=dict)  # {"action": str, "argument": Any}
    draws: list = field(default_factory=list)  # this activation's rng.log slice, in order
    #: How many of ``draws`` the DECISION consumed (the AI's 30415 coin flip) before the
    #: action applied. Replay uses the RECORDED decision, so it must skip past these to
    #: align the ACTION's formula with its own draws — an off-by-one here silently
    #: desyncs the shot's hit/damage rolls. 0 for a human/policy/deterministic decision.
    decision_draw_count: int = 0
    calc_inputs: dict = field(default_factory=dict)  # named inputs each formula read
    result: dict = field(default_factory=dict)  # what happened — replay compares this


@dataclass(frozen=True)
class HandoffEvent(Event):
    """A side's driver was reassigned between activations (U6's mid-fight handoff, R11).

    Carries the same seek/snapshot base as every other variant; the handoff itself is
    the ``from_driver_kind`` -> ``to_driver_kind`` transition observed on ``drivers[side]``.
    No decision, no draws — a handoff advances no fighter.
    """

    kind: str = "handoff"
    from_driver_kind: str = ""
    to_driver_kind: str = ""


#: The tagged-union dispatch: ``kind`` -> the dataclass that rebuilds it from a dict.
#: A future variant adds one entry here and nothing else changes.
_EVENT_KINDS: dict[str, Any] = {
    "activation": ActivationEvent,
    "handoff": HandoffEvent,
}


def _event_from_dict(raw: dict) -> Event:
    """Rebuild one :class:`Event` from its :func:`json_safe` dict, dispatching on ``kind``."""
    cls = _EVENT_KINDS.get(raw.get("kind"))
    if cls is None:
        raise ValueError(f"unknown recording event kind {raw.get('kind')!r}")
    return cls(**raw)


# --------------------------------------------------------------------------- #
# The recording container                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class Recording:
    """A fight's full transcript: the ordered events + the scenario that produced them.

    ``scenario`` is kept live (its :class:`~engine.combat.RulesBundle` carries the LIVE
    formula functions) so :func:`replay` can rebuild a fresh fight and re-run the real
    code — that live code IS the fidelity detector. ``winner`` / ``losses`` are the
    :class:`~engine.combat.CombatResult` the recorded fight produced, so a replay can
    assert it reproduced the same outcome. ``schema_version`` stamps the snapshot shape
    (reused :data:`engine.effects.SCHEMA_VERSION`).

    JSON serialization (:func:`save`) emits ``events`` + ``schema_version`` + the
    scenario's serializable fields (sides/grid/dir_memory/seed — NOT its formula
    functions, which JSON cannot carry). :func:`load` rebuilds the scenario shell; a
    caller re-attaches live ``rules`` at :func:`replay` time.
    """

    events: list = field(default_factory=list)
    scenario: Any = None
    winner: int | None = None
    losses: tuple[int, int] | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ReplayReport:
    """The verdict of a :func:`replay`: did the live formulas reproduce the recording?

    ``diverged`` is True iff some activation's recomputed ``result`` (hit/damage/downed)
    differs from the recorded one — the fidelity detector firing. ``at_index`` names that
    activation (a valid seek target, so U8 can jump to the board just before the change);
    ``expected`` is the recorded result, ``got`` the freshly recomputed one. On a faithful
    replay ``diverged`` is False and the other fields are ``None``.
    """

    diverged: bool = False
    at_index: int | None = None
    expected: dict | None = None
    got: dict | None = None


# --------------------------------------------------------------------------- #
# The recorder — observes the shared drive loop, one call per activation       #
# --------------------------------------------------------------------------- #
class _Recorder:
    """Observes :func:`engine.interactions._drive_fight`, appending one event per step.

    The drive loop is one-iteration-per-activation. Before an activation applies, the
    loop tells the recorder the acting side/fighter, the driver kind, and the rng-log
    high-water mark; after it applies, the recorder is handed the decision and result
    and slices ``rng.log[start:]`` for that activation's draws. A driver reassignment
    observed between activations becomes a :class:`HandoffEvent`.

    The recorder is deliberately dumb about *rules*: it captures whatever the fight and
    the shoot-result already expose (draws, attribute values, weapon stats). If a value
    U8 needs is not exposed, the fix is to expose it at record time here — never to
    recompute it in U8 off a second path.
    """

    def __init__(self, fight: Any, rng: Any) -> None:
        self._fight = fight
        self._rng = rng
        self.events: list = []
        self._index = 0
        # The driver kind currently assigned to each side, so a reassignment is visible.
        self._current_driver: dict[int, str] = {}

    def _snapshot(self) -> tuple[Any, int]:
        """A json_safe CombatState of the fight NOW + the shape version stamped on it.

        Normalized through JSON so the in-memory snapshot's dict-key shape matches the
        serialized one (int dir_memory keys stringify on JSON dump — see
        :func:`_json_normalize`).
        """
        return _json_normalize(json_safe(self._fight.snapshot())), SCHEMA_VERSION

    def observe_drivers(self, drivers: Any) -> None:
        """Emit a :class:`HandoffEvent` for any side whose driver kind changed.

        Called each loop iteration before the activation runs. The FIRST time a side is
        seen is initial assignment, not a handoff, so it is only recorded (no event).
        """
        for side, driver in drivers.items():
            kind = getattr(driver, "kind", None)
            previous = self._current_driver.get(side)
            if previous is not None and previous != kind:
                snapshot, version = self._snapshot()
                self.events.append(
                    HandoffEvent(
                        index=self._index,
                        side=side,
                        from_driver_kind=previous,
                        to_driver_kind=kind,
                        snapshot=snapshot,
                        snapshot_shape_version=version,
                    )
                )
                self._index += 1
            self._current_driver[side] = kind

    def draw_mark(self) -> int:
        """The rng-log length at the start of an activation (the draws-slice lower bound)."""
        return len(self._rng.log) if self._rng is not None else 0

    def record_activation(
        self,
        *,
        side: int,
        fighter_index: int,
        driver_kind: str,
        action: str,
        argument: Any,
        result: Any,
        calc_inputs: dict,
        draw_start: int,
        decision_draw_count: int = 0,
    ) -> None:
        """Append one :class:`ActivationEvent` for an activation that just applied."""
        draws = []
        if self._rng is not None:
            # The rng.log records are (method, args, value) tuples; json_safe turns the
            # args tuple into a list, so store the same list-shape a loaded recording has.
            draws = [list(rec) for rec in self._rng.log[draw_start:]]
        snapshot, version = self._snapshot()
        self.events.append(
            ActivationEvent(
                index=self._index,
                side=side,
                fighter_index=fighter_index,
                driver_kind=driver_kind,
                decision={"action": action, "argument": argument},
                draws=draws,
                decision_draw_count=decision_draw_count,
                calc_inputs=calc_inputs,
                result=dict(result) if isinstance(result, dict) else {},
                snapshot=snapshot,
                snapshot_shape_version=version,
            )
        )
        self._index += 1


def _shoot_calc_inputs(fight: Any, direction: int) -> dict:
    """The named inputs the hit/damage formulas read for a shot — captured at record time.

    Enough for U8's ``--debug`` to print the arithmetic WITHOUT recomputing (plan lines
    1647-1662): both draw bounds, the accuracy attribute value (this game: kraft), the
    damage attribute value (brutalitaet), and the weapon stats. Read off the fight's own
    surface (the rules bundle's role maps + the attacker's equipment), so it names no game
    word itself — a second game's roles flow through unchanged.
    """
    attacker = fight.active
    rules = fight._rules
    equipment = dict(fight.equipment_stats(attacker))
    inputs: dict[str, Any] = {
        "direction": direction,
        "weapon": attacker.weapon,
        "equipment": equipment,
        "range": fight.equipment_range(attacker),
    }
    # The accuracy/damage attribute values, keyed by the role->attr map the config declared
    # (so U8 can print "accuracy attr (kraft) -> 34" without knowing the game's vocabulary).
    for capability, roles in (("hit", rules.hit_roles), ("damage", rules.damage_roles)):
        for role, attr in roles.items():
            inputs[f"{capability}.{role}.attr"] = attr
            inputs[f"{capability}.{role}.value"] = attacker.attrs.get(attr)
    return inputs


# --------------------------------------------------------------------------- #
# Recording: drive a live fight capturing everything                          #
# --------------------------------------------------------------------------- #
def record_fight(
    scenario: Any,
    drivers: Any,
    *,
    rng: Any = None,
    input_source: Any = None,
) -> tuple[Any, "Recording"]:
    """Drive ``scenario`` to a result while recording every activation.

    The recording sibling of :func:`engine.interactions.simulate`: same fight, same
    shared drive loop, but with a :class:`_Recorder` threaded in. Returns
    ``(CombatResult, Recording)``. The recording holds the live ``scenario`` (its rules
    carry the live formulas, so :func:`replay` re-runs real code) and every
    :class:`ActivationEvent` / :class:`HandoffEvent`.

    Headless by default (``input_source is None``) — a :class:`~engine.interactions.HumanDriver`
    is rejected exactly as :func:`simulate` rejects it, because a human side needs a
    client to suspend to. Pass an ``input_source`` (an ``(interaction) -> response``
    callable) to record a client-driven fight: the recorded transcript is byte-identical
    to the headless one EXCEPT each event's ``driver_kind`` (the point of the
    same-fight/same-seed equality test).
    """
    from engine.combat import CombatResult
    from engine.interactions import StartCombat, _build_fight, _drive_fight, _no_input_source
    from engine.rng import Rng

    if input_source is None:
        human_sides = [s for s, d in drivers.items() if getattr(d, "kind", None) == "human"]
        if human_sides:
            raise ValueError(
                f"record_fight() cannot run a HumanDriver headlessly (sides "
                f"{sorted(human_sides)}); pass an input_source to drive a human side"
            )
        input_source = _no_input_source

    if rng is None and getattr(scenario, "seed", None) is not None:
        rng = Rng(seed=scenario.seed)

    fight = _build_fight(StartCombat(scenario=scenario), rng=rng)
    recorder = _Recorder(fight, rng)
    # Drive the CALLER's drivers mapping directly (not a copy), so a policy that
    # reassigns ``drivers[side]`` between activations (U6's mid-fight handoff, R11) is
    # visible to both the loop and the recorder's HandoffEvent detection.
    winner = _drive_fight(fight, drivers, input_source, recorder=recorder)
    recording = Recording(
        events=recorder.events,
        scenario=scenario,
        winner=winner,
        losses=fight.losses,
    )
    return CombatResult(winner=winner, losses=fight.losses), recording


# --------------------------------------------------------------------------- #
# Replay: re-run the live formulas against the recorded draws                  #
# --------------------------------------------------------------------------- #
class _ReplayRng:
    """Plays back a recording's draws in order rather than rolling fresh.

    Structurally :class:`~tests.helpers.StubRng`, but sourced from the recording: each
    activation's ``draws`` are loaded before that activation replays, and each
    ``range``/``hit`` call pops the next recorded value. The formula being fed these is
    the LIVE code, so a changed formula turns the same draw into a different result —
    which is exactly how :func:`replay` detects a fidelity break.

    It also re-logs each call (like the real :class:`~engine.rng.Rng`), so a replay's own
    draw slice can be compared against the recorded one if a caller wants to.
    """

    def __init__(self) -> None:
        self._queue: list = []
        self._log: list = []

    @property
    def log(self) -> list:
        return self._log

    def load(self, draws: list, *, skip: int = 0) -> None:
        """Queue one activation's recorded draws (values only) before it replays.

        ``skip`` drops the first ``skip`` draws — the ones the DECISION consumed (the
        AI's coin flip). Replay uses the RECORDED decision rather than re-deciding, so
        those draws must not be fed to the ACTION's formula, or the shot's hit/damage
        rolls read the wrong values.
        """
        self._queue = [rec[2] for rec in draws[skip:]]

    def range(self, n: int) -> int:
        value = self._pop("range", (n,))
        return value

    def hit(self, a: int, b: int) -> int:
        return self._pop("hit", (a, b))

    def _pop(self, method: str, args: tuple) -> int:
        if not self._queue:
            raise AssertionError(
                f"replay rng exhausted at {method}{args}: the recorded draw slice ran out — "
                f"the live formula drew MORE than was recorded (an off-by-one desync)"
            )
        value = self._queue.pop(0)
        self._log.append((method, args, value))
        return value


def _rebuild_fight(scenario: Any, rng: Any, *, rules: Any = None) -> Any:
    """A fresh :class:`~engine.combat.CombatFight` from a recording's scenario.

    ``rules`` overrides the scenario's bundle — used both to inject a DELIBERATELY altered
    formula (the fidelity-detector test) and to re-attach live formulas to a scenario
    shell rebuilt by :func:`load` (JSON cannot carry the formula functions).
    """
    from engine.combat import CombatFight
    from engine.state import CombatState

    return CombatFight(
        CombatState(
            sides=scenario.sides,
            grid=tuple(scenario.grid or ()),
            dir_memory=dict(scenario.dir_memory or {}),
        ),
        rng=rng,
        rules=rules if rules is not None else scenario.rules,
    )


def replay(recording: "Recording", *, rules: Any = None) -> "ReplayReport":
    """Re-run the recorded decisions against the LIVE formulas; report any divergence.

    Rebuilds a fresh fight from ``recording.scenario`` and, for each
    :class:`ActivationEvent`, applies its recorded decision while the replay RNG feeds
    back that activation's recorded draws. The formula is live code, so a changed formula
    produces a different hit/damage than the recorded ``result`` — returned as
    ``ReplayReport(diverged=True, at_index=…, expected=…, got=…)``. A :class:`HandoffEvent`
    advances no fighter, so it is skipped. On a faithful replay, returns a non-diverged
    report.

    ``rules`` re-attaches live formulas when replaying a recording loaded from disk (whose
    scenario shell lost its formula functions), or injects an altered formula to prove the
    detector. "Diverged" means the recomputed hit/damage/downed differs — NOT that draws
    differ (impossible: they are fed back verbatim) and NOT that the winner differs (a
    formula change may not flip the outcome; the per-activation check catches it anyway).
    """
    rng = _ReplayRng()
    fight = _rebuild_fight(recording.scenario, rng, rules=rules)

    for event in recording.events:
        if event.kind != "activation":
            continue
        rng.load(event.draws, skip=event.decision_draw_count)
        action = event.decision["action"]
        argument = event.decision["argument"]

        if action == "shoot":
            got = fight.apply_action("shoot", argument)
            expected = event.result
            if _shoot_diverged(expected, got):
                return ReplayReport(
                    diverged=True,
                    at_index=event.index,
                    expected=dict(expected),
                    got=dict(got),
                )
            winner = fight.winner()
            if winner is not None:
                fight.finish(winner)
                continue
            fight.advance_activation()
        elif action == "move":
            fight.apply_action("move", argument, record_dir_memory=event.driver_kind != "human")
            fight.advance_activation()
        elif action == "pass":
            fight.advance_activation()
        elif action == "surrender":
            fight.surrender()

    return ReplayReport(diverged=False)


def _shoot_diverged(expected: dict, got: dict) -> bool:
    """True iff a replayed shot's hit/damage/downed differs from the recorded result.

    Only the outcome fields matter for the fidelity check: ``hit``, ``damage``,
    ``downed`` (and, when hit, WHO was struck). ``target_side``/``target_index`` are
    deterministic geometry, but a formula change that flips a hit to a miss changes them
    too, so comparing the full outcome set catches every real divergence.
    """
    fields = ("hit", "damage", "downed", "target_side", "target_index")
    return any(expected.get(f) != got.get(f) for f in fields)


# --------------------------------------------------------------------------- #
# Serialization — JSON via json_safe, the SAME path save/load uses            #
# --------------------------------------------------------------------------- #
def _recording_to_dict(recording: "Recording") -> dict:
    """The JSON-safe dict form of a recording — events + version + scenario shell.

    Every nested state graph (each event's ``snapshot``) goes through the SAME
    :func:`json_safe` the state save path uses — never a second serializer. The
    scenario's formula functions (``rules``) are NOT emitted (JSON cannot carry them);
    :func:`load` rebuilds the scenario shell and a caller re-attaches live rules at
    :func:`replay` time.
    """
    return {
        "schema_version": recording.schema_version,
        "winner": recording.winner,
        "losses": json_safe(recording.losses) if recording.losses is not None else None,
        "scenario": _scenario_to_dict(recording.scenario),
        "events": [json_safe(event) for event in recording.events],
    }


def _scenario_to_dict(scenario: Any) -> Any:
    """A scenario's serializable fields — sides/grid/dir_memory/seed, NOT its rules."""
    if scenario is None:
        return None
    return {
        "sides": json_safe(scenario.sides),
        "grid": json_safe(scenario.grid),
        "dir_memory": {str(k): v for k, v in (scenario.dir_memory or {}).items()},
        "seed": scenario.seed,
    }


def _scenario_from_dict(raw: Any) -> Any:
    """Rebuild a rules-less :class:`~engine.scenario.Scenario` shell from its dict."""
    if raw is None:
        return None
    from engine.scenario import Scenario
    from engine.state import Fighter

    sides = tuple(tuple(Fighter(**f) for f in side) for side in raw["sides"])
    dir_memory = {int(k): v for k, v in raw.get("dir_memory", {}).items()}
    return Scenario(
        sides=sides,
        grid=tuple(raw.get("grid") or ()),
        rules=None,  # formulas cannot survive JSON — re-attached at replay time
        dir_memory=dir_memory,
        seed=raw.get("seed"),
    )


def _recording_from_dict(raw: dict) -> "Recording":
    """Rebuild a :class:`Recording` from its JSON dict, dispatching each event on ``kind``.

    If the stored ``schema_version`` does not match :data:`engine.effects.SCHEMA_VERSION`,
    this returns the recording with its events intact but their snapshots stripped — the
    caller's :func:`load` then rebuilds snapshots via :func:`_replay_and_snapshot`. When
    versions match, snapshots are kept as-is.
    """
    events = [_event_from_dict(dict(e)) for e in raw["events"]]
    losses = tuple(raw["losses"]) if raw.get("losses") is not None else None
    return Recording(
        events=events,
        scenario=_scenario_from_dict(raw.get("scenario")),
        winner=raw.get("winner"),
        losses=losses,
        schema_version=raw.get("schema_version", SCHEMA_VERSION),
    )


def save(recording: "Recording", path: Any) -> None:
    """Write ``recording`` to ``path`` as JSON (directory-agnostic — U8 picks the dir)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_recording_to_dict(recording), fh, indent=2)


def load(path: Any, *, rules: Any = None) -> "Recording":
    """Read a recording from ``path``, rebuilding stale-shape snapshots if needed.

    On load, if the recording's ``snapshot_shape_version`` does not match the current
    :data:`engine.effects.SCHEMA_VERSION`, EVERY snapshot is discarded and the recording
    is rebuilt from index 0 via :func:`_replay_and_snapshot` — re-snapshotting at the
    current version from the decision log. That rebuild path is the SAME function live
    recording uses ("replay with snapshotting on").

    ``rules`` re-attaches the live formula bundle to the scenario shell (JSON dropped the
    formula functions). Needed whenever the loaded recording will be replayed OR rebuilt.
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    recording = _recording_from_dict(raw)
    if rules is not None and recording.scenario is not None:
        recording.scenario = replace(recording.scenario, rules=rules)

    if not _snapshots_current(recording):
        # Shape drift (KTD-8): the stored snapshots are an old shape. Discard them all and
        # rebuild from the decision log at the current version.
        recording = _replay_and_snapshot(recording)
    return recording


def _snapshots_current(recording: "Recording") -> bool:
    """True iff every event's snapshot is stamped with the current SCHEMA_VERSION."""
    versions = [e.snapshot_shape_version for e in recording.events if e.snapshot is not None]
    return all(v == SCHEMA_VERSION for v in versions)


# --------------------------------------------------------------------------- #
# The shared rebuild — "replay with snapshotting on"                          #
# --------------------------------------------------------------------------- #
def _replay_and_snapshot(recording: "Recording") -> "Recording":
    """Re-drive the recorded decisions, re-snapshotting each event at the current version.

    The ONE function both live recording and the shape-drift rebuild use. It rebuilds a
    fresh fight from the recording's scenario, replays each event's decision consuming its
    recorded draws (via :class:`_ReplayRng`), and re-stamps every event with a fresh
    :func:`json_safe` snapshot at :data:`SCHEMA_VERSION`. The events' decisions/draws/
    results are untouched — only the (discarded) snapshots are rebuilt. Requires the
    scenario to carry live rules (a caller re-attaches them via :func:`load`'s ``rules``).
    """
    rng = _ReplayRng()
    fight = _rebuild_fight(recording.scenario, rng)

    rebuilt: list = []
    for event in recording.events:
        if event.kind == "handoff":
            # A handoff advances no fighter — re-snapshot the board as it stands.
            snapshot = _json_normalize(json_safe(fight.snapshot()))
            rebuilt.append(replace(event, snapshot=snapshot, snapshot_shape_version=SCHEMA_VERSION))
            continue

        action = event.decision["action"]
        argument = event.decision["argument"]
        rng.load(event.draws, skip=event.decision_draw_count)
        terminal = False
        if action == "shoot":
            fight.apply_action("shoot", argument)
            winner = fight.winner()
            if winner is not None:
                # The live recorder snapshots the winning shot BEFORE calling finish()
                # (result_flag still 0), so mirror that: snapshot here, then finish.
                terminal = True
            else:
                fight.advance_activation()
        elif action == "move":
            fight.apply_action("move", argument, record_dir_memory=event.driver_kind != "human")
            fight.advance_activation()
        elif action == "pass":
            fight.advance_activation()
        elif action == "surrender":
            fight.surrender()

        snapshot = _json_normalize(json_safe(fight.snapshot()))
        rebuilt.append(replace(event, snapshot=snapshot, snapshot_shape_version=SCHEMA_VERSION))
        if terminal:
            fight.finish(fight.winner())

    return Recording(
        events=rebuilt,
        scenario=recording.scenario,
        winner=recording.winner,
        losses=recording.losses,
        schema_version=SCHEMA_VERSION,
    )

"""Recording and replay — the observable, replayable fight transcript (U7, R12).

Every test here shares ONE fixture: the **kdh ambush** (a 1v1 loan-shark ambush,
``mf-prg.bas:15200-15260`` — one player gangster vs. one gewehr-armed ambusher) at
``seed=42``, recorded once and reused. A single named fixture keeps every scenario
comparing like with like rather than inventing its own fight.

The transcript is a tagged union sharing one monotonic ``index``: an
:class:`~engine.recording.ActivationEvent` per activation, a
:class:`~engine.recording.HandoffEvent` when a side's driver is reassigned. Replay
feeds each activation's recorded draws back through the LIVE formula code, so a changed
formula turns the same draw into a different hit/damage and :func:`~engine.recording.replay`
reports the divergence — the fidelity detector. Serialization is JSON via
:func:`engine.state.json_safe`, the same path the state save/load uses.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from data.game_configs.mafia_1920s.combat_rules import (
    DAMAGE_ROLES,
    HIT_ROLES,
    build_rules,
    is_hit,
)
from engine.combat import RulesBundle
from engine.effects import SCHEMA_VERSION
from engine.interactions import AiDriver, PolicyDriver, simulate
from engine.recording import (
    _recording_from_dict,
    _recording_to_dict,
    load,
    record_fight,
    replay,
    save,
)
from engine.rng import Rng
from engine.scenario import Scenario
from tests.helpers import combat_fighter as _f


def _cell(row: int, col: int) -> int:
    """Linear combat-grid cell from (row, col) — the grid is 40 wide (CLAUDE.md)."""
    return row * 40 + col


def _kdh_ambush_scenario() -> Scenario:
    """The shared fixture: the kdh ambush 1v1 at ``seed=42``.

    Side 1 is the player's gangster (revolver, this game's own kraft/brutalitaet); side 2
    is the single loan-shark ambusher (gewehr, the fixed CPU 30/30 of ``mf-prg.bas:30245``,
    ``energie=35`` from ``kdh_ambush_energie``). Positioned a few cells apart on an open
    arena so the fight actually plays out (moves + shots + a downing).
    """
    sides = (
        (
            _f(
                name="hero",
                weapon=5,  # revolver
                energie=20,
                kraft=34,
                brutalitaet=28,
                position=_cell(6, 15),
                roster_id=0,
            ),
        ),
        (
            _f(
                name="ambusher",
                weapon=6,  # gewehr (kdh_ambush_weapon)
                energie=35,  # kdh_ambush_energie
                kraft=30,
                brutalitaet=30,
                position=_cell(6, 22),
                roster_id=None,
            ),
        ),
    )
    return Scenario(sides=sides, grid=(), rules=build_rules(), dir_memory={0: -1}, seed=42)


@pytest.fixture
def ambush_recording() -> tuple:
    """Record the shared kdh ambush ONCE (AI vs AI) and hand back result + recording."""
    scenario = _kdh_ambush_scenario()
    result, recording = record_fight(scenario, {1: AiDriver(), 2: AiDriver()})
    return result, recording


# --------------------------------------------------------------------------- #
# A recorded fight replays to an identical CombatResult                        #
# --------------------------------------------------------------------------- #
def test_a_recorded_fight_records_the_same_result_a_bare_simulate_produces(ambush_recording):
    """The recorded fight's winner + losses are exactly what a non-recording
    ``simulate()`` of the same scenario/seed produces — recording changes no behaviour."""
    result, recording = ambush_recording
    bare = simulate(_kdh_ambush_scenario(), {1: AiDriver(), 2: AiDriver()})
    assert (result.winner, result.losses) == (bare.winner, bare.losses)
    # The recording carries that same outcome for a replay to check against.
    assert recording.winner == result.winner
    assert tuple(recording.losses) == tuple(result.losses)


def test_replay_reproduces_the_same_winner_and_losses(ambush_recording):
    """Replaying the recording against the live formulas does not diverge — the fight
    reproduces to the identical outcome."""
    _, recording = ambush_recording
    report = replay(recording)
    assert report.diverged is False
    assert report.at_index is None


# --------------------------------------------------------------------------- #
# Replay reproduces EVERY intermediate state, not just the outcome            #
# --------------------------------------------------------------------------- #
def test_replay_reproduces_every_intermediate_vitality_not_only_the_last(ambush_recording):
    """Rebuilding from the decision log yields the SAME board — every fighter's vitality
    — at EVERY activation index, not merely the final one."""
    from engine.recording import _rebuild_fight, _ReplayRng

    _, recording = ambush_recording

    # Drive a fresh fight through the recorded decisions, comparing the live board to the
    # snapshot each event captured — at every index.
    rng = _ReplayRng()
    fight = _rebuild_fight(recording.scenario, rng)

    def vitalities(state_dict):
        return [f["vitality"] for side in state_dict["sides"] for f in side]

    checked = 0
    for event in recording.events:
        if event.kind != "activation":
            continue
        rng.load(event.draws, skip=event.decision_draw_count)
        action = event.decision["action"]
        argument = event.decision["argument"]
        if action == "shoot":
            fight.apply_action("shoot", argument)
            winner = fight.winner()
            if winner is None:
                fight.advance_activation()
        elif action == "move":
            fight.apply_action("move", argument, record_dir_memory=event.driver_kind != "human")
            fight.advance_activation()
        elif action == "pass":
            fight.advance_activation()
        from engine.state import json_safe

        live = json_safe(fight.snapshot())
        assert vitalities(live) == vitalities(event.snapshot), (
            f"vitalities diverged at activation index {event.index}"
        )
        checked += 1
    assert checked >= 3, "the shared fixture must exercise several activations"


# --------------------------------------------------------------------------- #
# Every RNG draw is carried; replay consumes them in order                     #
# --------------------------------------------------------------------------- #
def test_recorded_draw_count_equals_the_live_fights_draw_count(ambush_recording):
    """The recorded draws total exactly the live fight's ``rng.log`` length — an
    off-by-one here is exactly what silently desynchronises a replay."""
    from engine.interactions import StartCombat, _build_fight, _drive_fight, _no_input_source

    _, recording = ambush_recording

    # Re-run the same fight WITHOUT recording, counting the raw rng.log.
    rng = Rng(seed=42)
    fight = _build_fight(StartCombat(scenario=_kdh_ambush_scenario()), rng=rng)
    _drive_fight(fight, {1: AiDriver(), 2: AiDriver()}, _no_input_source)
    live_draw_count = len(rng.log)

    recorded_draw_count = sum(len(e.draws) for e in recording.events if e.kind == "activation")
    assert recorded_draw_count == live_draw_count
    assert recorded_draw_count > 0


def test_every_draw_is_carried_in_order_as_method_args_value(ambush_recording):
    """Each recorded draw is a ``[method, args, value]`` record, in call order."""
    _, recording = ambush_recording
    seen_a_draw = False
    for event in recording.events:
        if event.kind != "activation":
            continue
        for record in event.draws:
            method, args, value = record
            assert method in ("range", "hit")
            assert isinstance(value, int)
            seen_a_draw = True
    assert seen_a_draw


# --------------------------------------------------------------------------- #
# calc_inputs carries the debug values WITHOUT a second computation            #
# --------------------------------------------------------------------------- #
def test_a_shoot_activation_carries_the_attribute_values_the_formulas_read(ambush_recording):
    """A recorded shot names the accuracy/damage attribute VALUES (kraft/brutalitaet) and
    the weapon stats, so U8's --debug can print them without recomputing."""
    _, recording = ambush_recording
    shots = [
        e for e in recording.events if e.kind == "activation" and e.decision["action"] == "shoot"
    ]
    assert shots, "the shared fixture must include at least one shot"
    shot = shots[0]
    ci = shot.calc_inputs
    # The role->attr map is named (so U8 prints "accuracy attr (kraft) -> 34") and the
    # attribute VALUE is captured, not recomputed.
    assert ci["hit.attacker.attr"] == "kraft"
    assert ci["damage.attacker.attr"] == "brutalitaet"
    assert isinstance(ci["hit.attacker.value"], int)
    assert isinstance(ci["damage.attacker.value"], int)
    # Both draw bounds are recoverable from equipment (ts) and the attribute (kraft//10+1),
    # and the weapon stats + range are present.
    assert "ts" in ci["equipment"] and "tg" in ci["equipment"]
    assert isinstance(ci["range"], int)


# --------------------------------------------------------------------------- #
# A mid-fight handoff appears as a HandoffEvent and replays correctly          #
# --------------------------------------------------------------------------- #
def test_a_mid_fight_handoff_is_recorded_and_replays():
    """Reassigning ``drivers[side]`` between activations (U6's handoff, R11) surfaces as a
    HandoffEvent carrying from/to driver kinds — and the recording still replays faithfully."""
    scenario = _kdh_ambush_scenario()
    drivers: dict = {1: AiDriver(), 2: AiDriver()}

    # A policy that behaves exactly like the AI, then hands side 1 back to a plain
    # AiDriver after its first activation — the discrete, observable control-plane change
    # U6 requires. It closes over the SAME drivers dict record_fight drives.
    handed_off = {"done": False}

    def hand_off_after_first(view):
        decision = view._fight.ai_decide(view)
        if not handed_off["done"]:
            drivers[1] = AiDriver()
            handed_off["done"] = True
        return decision

    drivers[1] = PolicyDriver(policy=hand_off_after_first)
    result, recording = record_fight(scenario, drivers)

    handoffs = [e for e in recording.events if e.kind == "handoff"]
    assert len(handoffs) == 1, "exactly one driver reassignment should be recorded"
    handoff = handoffs[0]
    assert handoff.side == 1
    assert handoff.from_driver_kind == "policy"
    assert handoff.to_driver_kind == "ai"
    # A handoff shares the same seek/snapshot base as every other variant.
    assert handoff.snapshot is not None
    assert handoff.snapshot_shape_version == SCHEMA_VERSION

    # The recording still replays without divergence (a handoff advances no fighter).
    report = replay(recording)
    assert report.diverged is False


# --------------------------------------------------------------------------- #
# Fidelity detector: an altered formula diverges, at_index names it            #
# --------------------------------------------------------------------------- #
def test_replaying_against_an_altered_damage_formula_diverges(ambush_recording):
    """Replaying the SAME recorded draws through a DELIBERATELY altered damage formula
    produces a different damage, so replay reports diverged=True and names the activation."""
    _, recording = ambush_recording

    def altered_damage(attacker, equipment, rng):
        # The faithful port is int(draw + bt/10) + 1; this adds 1000 instead of 1.
        tg = equipment["tg"]
        draw = rng.range(tg) if tg > 0 else 0
        return int(draw + attacker / 10) + 1000

    altered = RulesBundle(
        hit_roles=HIT_ROLES,
        hit_fn=is_hit,
        damage_roles=DAMAGE_ROLES,
        damage_fn=altered_damage,
    )
    report = replay(recording, rules=altered)
    assert report.diverged is True
    # at_index names the first activation whose damage changed (a valid seek target).
    first_hit = next(e for e in recording.events if e.kind == "activation" and e.result.get("hit"))
    assert report.at_index == first_hit.index
    assert report.expected["damage"] != report.got["damage"]
    assert report.got["damage"] >= report.expected["damage"] + 999


def test_a_faithful_replay_against_the_live_rules_does_not_diverge(ambush_recording):
    """The control for the detector: replaying against the UNCHANGED live rules is clean."""
    _, recording = ambush_recording
    assert replay(recording, rules=build_rules()).diverged is False


# --------------------------------------------------------------------------- #
# Shape drift (KTD-8): a stale-version recording loads, rebuilds, matches       #
# --------------------------------------------------------------------------- #
def test_shape_drift_discards_snapshots_rebuilds_and_matches_every_state(
    ambush_recording, tmp_path
):
    """A recording whose snapshot_shape_version != SCHEMA_VERSION loads successfully,
    discards its (garbled) snapshots, rebuilds from the decision log, and yields identical
    state at every activation index."""
    _, recording = ambush_recording
    original_snapshots = [e.snapshot for e in recording.events]

    # Serialize, then corrupt: bump every snapshot to a stale shape version and garble the
    # snapshot payload so a naive load that trusted it would be visibly wrong.
    raw = _recording_to_dict(recording)
    for event in raw["events"]:
        event["snapshot_shape_version"] = SCHEMA_VERSION + 99
        event["snapshot"] = {"garbled": True}
    import json

    path = tmp_path / "drifted.json"
    path.write_text(json.dumps(raw))

    loaded = load(path, rules=build_rules())

    # Every snapshot is re-stamped at the current version and rebuilt from the log.
    assert all(
        e.snapshot_shape_version == SCHEMA_VERSION for e in loaded.events if e.snapshot is not None
    )
    rebuilt_snapshots = [e.snapshot for e in loaded.events]
    assert rebuilt_snapshots == original_snapshots, (
        "rebuilt snapshots must match the originals at every index"
    )
    # And it still replays cleanly.
    assert replay(loaded).diverged is False


# --------------------------------------------------------------------------- #
# A recording round-trips through serialization unchanged                       #
# --------------------------------------------------------------------------- #
def test_a_recording_round_trips_through_serialization_unchanged(ambush_recording, tmp_path):
    """save(path) then load(path) yields a recording whose JSON dict form is identical to
    the original's — no field is dropped or reshaped by the round-trip."""
    _, recording = ambush_recording
    before = _recording_to_dict(recording)

    path = tmp_path / "roundtrip.json"
    save(recording, path)
    reloaded = load(path, rules=build_rules())
    after = _recording_to_dict(reloaded)

    assert before == after


def test_the_dict_round_trip_alone_is_stable(ambush_recording):
    """_recording_to_dict -> _recording_from_dict -> _recording_to_dict is a fixed point
    (the in-memory half of the serialization round-trip, no disk)."""
    _, recording = ambush_recording
    once = _recording_to_dict(recording)
    twice = _recording_to_dict(_recording_from_dict(once))
    assert once == twice


# --------------------------------------------------------------------------- #
# Two recordings of the SAME fight are byte-identical except driver_kind        #
# --------------------------------------------------------------------------- #
def test_two_recordings_of_the_same_fight_differ_only_in_driver_kind():
    """The same fight (same scenario, same seed) recorded two ways — once with side 1 as a
    plain AiDriver, once with side 1 as a PolicyDriver whose callable returns the AI's OWN
    decision — is byte-identical EXCEPT ``driver_kind`` ("ai" vs "policy").

    A human driver is deliberately NOT used for this equality: the source keys direction
    memory by fighter index (``ri(f)``, ``mf-prg.bas:30492``), and side 1's fighter 0 and
    side 2's fighter 0 share key 0. An AI move RECORDS that key while a human move does
    not, so a human-driven side-1 genuinely takes a different trajectory — it is not the
    "same fight". A policy that echoes ``ai_decide`` reproduces the AI's every draw and
    dir-memory write, so the ONLY observable difference is how the side was driven.
    """
    # 1) Side 1 driven by the built-in AI.
    _, ai_recording = record_fight(_kdh_ambush_scenario(), {1: AiDriver(), 2: AiDriver()})

    # 2) Side 1 driven by a policy that simply returns the AI's own choice — same decision,
    #    same draws, same dir-memory writes (non-human, so record_dir_memory stays on).
    def echo_ai(view):
        return view._fight.ai_decide(view)

    _, policy_recording = record_fight(
        _kdh_ambush_scenario(), {1: PolicyDriver(policy=echo_ai), 2: AiDriver()}
    )

    ai_events = [e for e in ai_recording.events if e.kind == "activation"]
    policy_events = [e for e in policy_recording.events if e.kind == "activation"]
    assert len(ai_events) == len(policy_events)
    assert len(ai_events) >= 3, "the shared fixture must exercise several activations"

    for ai_event, policy_event in zip(ai_events, policy_events):
        # driver_kind is the ONLY permitted difference, and only on side 1.
        if ai_event.side == 1:
            assert ai_event.driver_kind == "ai"
            assert policy_event.driver_kind == "policy"
        else:
            assert ai_event.driver_kind == policy_event.driver_kind == "ai"
        normalized_ai = replace(ai_event, driver_kind="X")
        normalized_policy = replace(policy_event, driver_kind="X")
        assert normalized_ai == normalized_policy, (
            f"events at index {ai_event.index} differ beyond driver_kind"
        )

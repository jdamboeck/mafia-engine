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

import json
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
from engine.fight_loop import AiDriver, PolicyDriver, simulate
from engine.recording import (
    HouseRulesError,
    load,
    record_fight,
    ReplayReport,
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
    return Scenario(sides=sides, grid=(), rules=build_rules({}), dir_memory={0: -1}, seed=42)


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
    """Replay checks the board after EVERY activation, not merely the final result: the
    genuine recording replays clean, and a recording whose snapshot is off at one
    intermediate activation diverges at exactly that index."""
    _, recording = ambush_recording
    assert replay(recording) == ReplayReport(diverged=False)

    activations = [e for e in recording.events if e.kind == "activation"]
    assert len(activations) >= 3, "the shared fixture must exercise several activations"
    middle = activations[len(activations) // 2]
    board = json.loads(json.dumps(middle.snapshot))
    board["sides"][0][0]["vitality"] += 1
    tampered = replace(
        recording,
        events=tuple(replace(e, snapshot=board) if e is middle else e for e in recording.events),
    )

    report = replay(tampered)
    assert report.diverged
    assert report.at_index == middle.index


# --------------------------------------------------------------------------- #
# Every RNG draw is carried; replay consumes them in order                     #
# --------------------------------------------------------------------------- #
def test_recorded_draw_count_equals_the_live_fights_draw_count(ambush_recording):
    """The recorded draws total exactly the live fight's ``rng.log`` length — an
    off-by-one here is exactly what silently desynchronises a replay."""
    _, recording = ambush_recording

    # Re-run the same fight WITHOUT recording, counting the raw rng.log.
    rng = Rng(seed=42)
    simulate(_kdh_ambush_scenario(), {1: AiDriver(), 2: AiDriver()}, rng=rng)
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
    assert report.expected is not None and report.got is not None
    assert report.expected["damage"] != report.got["damage"]
    assert report.got["damage"] >= report.expected["damage"] + 999


def test_a_faithful_replay_against_the_live_rules_does_not_diverge(ambush_recording):
    """The control for the detector: replaying against the UNCHANGED live rules is clean."""
    _, recording = ambush_recording
    assert replay(recording, rules=build_rules({})).diverged is False


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

    # Save, then corrupt the file: bump every snapshot to a stale shape version and garble
    # the snapshot payload so a naive load that trusted it would be visibly wrong.
    path = tmp_path / "drifted.json"
    save(recording, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    for event in raw["events"]:
        event["snapshot_shape_version"] = SCHEMA_VERSION + 99
        event["snapshot"] = {"garbled": True}
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load(path, rules=build_rules({}))

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
# KTD-14: a fighter's owner — absent from a recording made before owners        #
# --------------------------------------------------------------------------- #
def _drop_owner(value):
    """``value`` with every fighter's ``owner`` key removed, as an older file stores it."""
    if isinstance(value, dict):
        return {k: _drop_owner(v) for k, v in value.items() if k != "owner"}
    if isinstance(value, list):
        return [_drop_owner(v) for v in value]
    return value


def test_a_recording_without_owners_still_replays_unchanged(ambush_recording, tmp_path):
    """Every existing recording is the active player against NPCs and stores no
    ``owner``: it loads with every fighter unowned, replays without diverging, and its
    snapshots compare equal to the ones the live fight makes now."""
    _, recording = ambush_recording
    path = tmp_path / "before_owners.json"
    save(recording, path)
    raw = _drop_owner(json.loads(path.read_text(encoding="utf-8")))
    assert "owner" not in json.dumps(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load(path, rules=build_rules({}))

    assert loaded.scenario is not None and loaded.scenario.sides is not None
    assert [f.owner for side in loaded.scenario.sides for f in side] == [None, None]
    assert [e.snapshot for e in loaded.events] == [e.snapshot for e in recording.events]
    assert replay(loaded) == ReplayReport(diverged=False)


def test_a_fighters_owner_round_trips_through_a_recording(tmp_path):
    """A recorded fight with a player-owned side stores each fighter's ``owner``."""
    scenario = _kdh_ambush_scenario()
    assert scenario.sides is not None
    hero, ambusher = scenario.sides[0][0], scenario.sides[1][0]
    owned = replace(
        scenario,
        sides=((replace(hero, owner=0),), (replace(ambusher, roster_id=2, owner=1),)),
    )
    _, recording = record_fight(owned, {1: AiDriver(), 2: AiDriver()})
    path = tmp_path / "owned.json"
    save(recording, path)

    loaded = load(path, rules=build_rules({}))
    assert loaded.scenario is not None and loaded.scenario.sides is not None
    assert [f.owner for side in loaded.scenario.sides for f in side] == [0, 1]
    assert replay(loaded).diverged is False


# --------------------------------------------------------------------------- #
# A recording round-trips through serialization unchanged                       #
# --------------------------------------------------------------------------- #
def test_a_recording_round_trips_through_serialization_unchanged(ambush_recording, tmp_path):
    """save(path) then load(path) yields a recording that saves to the identical file —
    no field is dropped or reshaped by the round-trip."""
    _, recording = ambush_recording
    first, second = tmp_path / "roundtrip.json", tmp_path / "resaved.json"

    save(recording, first)
    reloaded = load(first, rules=build_rules({}))
    save(reloaded, second)

    assert second.read_text(encoding="utf-8") == first.read_text(encoding="utf-8")


def test_a_load_without_rules_resaves_to_the_same_file(ambush_recording, tmp_path):
    """Loading a current-version recording needs no rebuild (and so no rules): the file
    parses back into a recording that saves to the identical file."""
    _, recording = ambush_recording
    first, second = tmp_path / "saved.json", tmp_path / "resaved.json"

    save(recording, first)
    reloaded = load(first)  # no rules: nothing is replayed
    assert reloaded.scenario is not None and reloaded.scenario.rules is None
    save(reloaded, second)

    assert second.read_text(encoding="utf-8") == first.read_text(encoding="utf-8")


@pytest.mark.parametrize("side_count", [1, 3])
def test_a_recording_whose_scenario_is_not_two_sided_is_rejected_on_load(
    ambush_recording, side_count, tmp_path
):
    """A fight has exactly two sides. A (hand-edited or corrupt) recording with any other
    count must fail at load, not load and replay "without divergence" while silently
    ignoring the extra side (three) or crash deep inside the fight (one)."""
    _, recording = ambush_recording
    path = tmp_path / "sides.json"
    save(recording, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    first, second = raw["scenario"]["sides"]
    raw["scenario"]["sides"] = [first, second, second][:side_count]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load(path)


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


# --------------------------------------------------------------------------- #
# #45 — an opted-in (observed) fight records and replays exactly like a plain one
# --------------------------------------------------------------------------- #
def test_an_observed_fight_records_the_same_transcript_and_replays_without_divergence():
    """Observation frames are display-only: a human-vs-AI fight whose client opted in
    (``observes_ai``) records event-for-event what the same fight records without the
    opt-in, and that recording replays with no divergence."""
    from engine.fight_loop import HumanDriver

    class Client:
        def __init__(self, observes_ai: bool) -> None:
            self.observes_ai = observes_ai
            self.frames = 0

        def __call__(self, interaction):
            if interaction.prompt == "observe":
                self.frames += 1
                return ("surrender", None)  # ignored by the loop
            return ("shoot", +1)

    watched_client = Client(observes_ai=True)
    _, watched = record_fight(
        _kdh_ambush_scenario(), {1: HumanDriver(), 2: AiDriver()}, input_source=watched_client
    )
    _, plain = record_fight(
        _kdh_ambush_scenario(), {1: HumanDriver(), 2: AiDriver()}, input_source=Client(False)
    )

    ai_activations = [e for e in watched.events if e.kind == "activation" and e.side == 2]
    assert len(ai_activations) >= 3
    assert watched_client.frames == len(ai_activations)
    assert watched.events == plain.events
    assert watched.losses is not None and plain.losses is not None
    assert (watched.winner, tuple(watched.losses)) == (plain.winner, tuple(plain.losses))
    report = replay(watched)
    assert report.diverged is False
    assert report.at_index is None


# --------------------------------------------------------------------------- #
# House rules (U38, R21, KTD-9): a recording stores its map; replay compares it  #
# --------------------------------------------------------------------------- #
#: A made-up house-rules map. The engine carries the map as opaque data and never
#: reads a switch by its id, so the ids need not be real catalogue entries here.
_UNDER = {"alpha": "faithful", "beta": "intent"}


def _recorded_under(house_rules: dict, tmp_path) -> tuple:
    """The shared ambush recorded under ``house_rules`` and saved; (path, recording)."""
    scenario = replace(_kdh_ambush_scenario(), rules=build_rules(house_rules))
    _, recording = record_fight(scenario, {1: AiDriver(), 2: AiDriver()})
    path = tmp_path / "under.json"
    save(recording, path)
    return path, recording


def test_a_recording_stores_the_map_it_was_made_under_and_replays_under_it(tmp_path):
    path, recording = _recorded_under(_UNDER, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["house_rules"] == _UNDER
    loaded = load(path, rules=build_rules(dict(_UNDER)))
    assert loaded.house_rules is not None and dict(loaded.house_rules) == _UNDER
    assert replay(loaded) == ReplayReport(diverged=False)
    assert replay(recording, rules=build_rules(dict(_UNDER))) == ReplayReport(diverged=False)


def test_a_recording_made_under_one_map_refuses_to_load_under_another(tmp_path):
    path, _ = _recorded_under(_UNDER, tmp_path)
    with pytest.raises(HouseRulesError) as exc:
        load(path, rules=build_rules({"alpha": "faithful", "beta": "faithful"}))
    assert str(exc.value) == (
        "house rule 'beta' differs: the recording was made with 'intent', "
        "the supplied rules have 'faithful'"
    )


def test_a_recording_refuses_to_replay_under_another_map(tmp_path):
    _, recording = _recorded_under(_UNDER, tmp_path)
    with pytest.raises(HouseRulesError, match="house rule 'alpha' differs"):
        replay(recording, rules=build_rules({"alpha": "intent", "beta": "intent"}))


def test_an_entry_one_map_lacks_is_named_as_unset(tmp_path):
    path, _ = _recorded_under(_UNDER, tmp_path)
    with pytest.raises(HouseRulesError) as exc:
        load(path, rules=build_rules({"alpha": "faithful"}))
    assert str(exc.value) == (
        "house rule 'beta' differs: the recording was made with 'intent', "
        "the supplied rules have it unset"
    )


@pytest.mark.parametrize("with_rules", [True, False])
def test_a_recording_with_no_map_is_refused(ambush_recording, tmp_path, with_rules):
    _, recording = ambush_recording
    path = tmp_path / "no-map.json"
    save(recording, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["house_rules"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HouseRulesError) as exc:
        load(path, rules=build_rules({}) if with_rules else None)
    assert str(exc.value) == "the recording stores no house-rules map"

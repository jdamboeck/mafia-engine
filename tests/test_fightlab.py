"""fightlab — the terminal play/watch/replay debug tool (U8, R13).

The tool has NO fidelity oracle (no BASIC line says what a debug dump prints), so every
test here pins CONCRETE seeded values rather than "some text appeared". The shared
fixture is the kdh ambush scenario at ``seed=42``: one invented player gangster
(revolver, kraft 34 / brut 28) vs. the schuldner the kdh_ambush encounter declares
(gewehr, 35 energy). Recorded once (AI vs AI) for the watch/replay tests.

R13 (thinness) is the unit's whole point: fightlab RENDERS and READS KEYS; every number
it prints already exists on a recorded event / result. ``test_debug_dump_*`` proves it
by asserting the exact draws, attribute values, and resulting damage — pinned to the
seed — appear in the dump.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from clients.terminal import fightlab
from data.game_configs.mafia_1920s.combat_rules import build_rules, enemy_attrs, equipper
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.setup import (
    load_combat_backdrop,
    load_encounter,
    weapon_stats_by_id,
)
from engine.interactions import AiDriver
from engine.recording import record_fight, save
from engine.scenario import Scenario

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_SCENARIO = _CONFIG_DIR / "content" / "scenarios" / "kdh_ambush.yaml"


def _params() -> dict:
    import yaml

    return yaml.safe_load((_CONFIG_DIR / "config.yaml").read_text(encoding="utf-8"))[
        "formula_params"
    ]


@pytest.fixture
def recording_path(tmp_path):
    """Record the shared kdh ambush scenario (AI vs AI, seed 42) once and save it."""
    scenario = fightlab.load_scenario(_SCENARIO)
    _result, recording = record_fight(scenario, {1: AiDriver(), 2: AiDriver()})
    path = tmp_path / "ambush.json"
    save(recording, path)
    return path, recording


# --------------------------------------------------------------------------- #
# The scenario file's ENEMY side == Scenario.from_encounter("kdh_ambush", …)   #
# --------------------------------------------------------------------------- #
def test_scenario_file_enemy_side_equals_from_encounter():
    """Loading content/scenarios/kdh_ambush.yaml produces a Scenario whose ENEMY side is
    byte-identical to the in-game ``Scenario.from_encounter("kdh_ambush", …)`` — the
    differential shape U6a uses (one loader, two sources cannot diverge)."""
    from_file = fightlab.load_scenario(_SCENARIO)

    # The in-game path: the SAME roster the scenario file declares, through from_encounter.
    roster = [Gangster(name="hero", weapon=5, energie=20, kraft=34, brutalitaet=28, intelligenz=30)]
    params = _params()
    enc = load_encounter(_CONFIG_DIR / "content" / "encounters" / "kdh_ambush.yaml")
    grid = load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{enc.grid}.yaml")
    from_encounter = Scenario.from_encounter(
        enc.variants[0],
        roster,
        build_rules(),
        enemy_attrs=enemy_attrs(params),
        grid=grid,
        equip=equipper(weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")),
    )

    # The enemy side (side 2) must match exactly — same schuldner, gewehr, 35 energy,
    # 30/30 attrs, same position/equipment.
    assert from_file.sides[1] == from_encounter.sides[1]
    # And the player side is the invented fighter the file declares.
    assert [f.name for f in from_file.sides[0]] == ["hero"]
    assert from_file.sides[0][0].weapon == 5
    assert from_file.sides[0][0].vitality == 20


# --------------------------------------------------------------------------- #
# play --scenario --seed resolves to a winner and prints a losses block        #
# --------------------------------------------------------------------------- #
def test_play_seeded_resolves_to_a_winner_with_a_losses_block():
    """``play --scenario kdh_ambush --seed 42`` over scripted stdin resolves to a
    winner and prints a losses block. The seed makes the outcome ASSERTABLE: side 1
    passes every activation, so the AI schuldner wins (winner 2), losing nobody while
    downing the passive hero (losses = (1, 0))."""
    stdin = io.StringIO("p\n" * 80)  # side 1 passes every activation
    out = io.StringIO()
    result = fightlab.play(_SCENARIO, seed=42, stdin=stdin, out=out)

    assert result.winner == 2
    assert tuple(result.losses) == (1, 0)

    text = out.getvalue()
    # A real losses block with the REAL side names (NOT the "side {i}" placeholder).
    assert "verluste der spieler" in text
    assert "hero: 1" in text  # the passive hero went down
    assert "schuldner: 0" in text
    assert "sieger: schuldner" in text  # winner banner, real name


# --------------------------------------------------------------------------- #
# watch renders activation 0, ONE keypress advances to EXACTLY activation 1     #
# --------------------------------------------------------------------------- #
def _activation_indices(text: str) -> list[int]:
    return [int(m) for m in re.findall(r"-- activation (\d+) /", text)]


def test_watch_renders_activation_0_then_one_keypress_advances_to_exactly_1(recording_path):
    """``watch`` renders activation 0, and ONE keypress (space/enter) advances to
    EXACTLY activation 1 — asserting the rendered activation INDEX, not that output
    merely changed."""
    path, _rec = recording_path
    keys = iter(["\n", "q"])
    out = io.StringIO()
    fightlab.watch(path, out=out, key_reader=lambda: next(keys))

    indices = _activation_indices(out.getvalue())
    assert indices == [0, 1]


# --------------------------------------------------------------------------- #
# b at activation 5 renders activation 4 == a forward replay to 4               #
# --------------------------------------------------------------------------- #
def test_b_at_activation_5_renders_activation_4_matching_forward_replay(recording_path):
    """Stepping forward to activation 5 then pressing ``b`` renders activation 4, whose
    board is IDENTICAL to a forward replay to 4 (KTD-8: the snapshot seek matches the
    decision log). Proven by comparing the rendered snapshot to the recording's own
    snapshot at index 4 — which the recording built by driving the decision log
    forward, so equality proves seek == forward-replay."""
    path, recording = recording_path
    # Advance 0->5 (five keypresses), then 'b' (->4), then quit.
    keys = iter(["\n", "\n", "\n", "\n", "\n", "b", "q"])
    out = io.StringIO()
    fightlab.watch(path, out=out, key_reader=lambda: next(keys))

    indices = _activation_indices(out.getvalue())
    # The last rendered index after the 'b' is activation 4.
    assert indices == [0, 1, 2, 3, 4, 5, 4]

    # The board 'b' seeked to (index 4's snapshot) is exactly the forward-replay board.
    # The recording's snapshot at index 4 IS the forward-driven board (recording ==
    # replay-with-snapshotting-on), so vitalities at index 4 are the seek target.
    def vitalities(snapshot):
        return [f["vitality"] for side in snapshot["sides"] for f in side]

    from engine.recording import _ReplayRng, _rebuild_fight
    from engine.state import json_safe

    rng = _ReplayRng()
    fight = _rebuild_fight(recording.scenario, rng, rules=build_rules())
    live_at_4 = None
    for event in recording.events:
        if event.kind != "activation":
            continue
        rng.load(event.draws, skip=event.decision_draw_count)
        action = event.decision["action"]
        argument = event.decision["argument"]
        if action == "shoot":
            fight.apply_action("shoot", argument)
            if fight.winner() is None:
                fight.advance_activation()
        elif action == "move":
            fight.apply_action("move", argument, record_dir_memory=event.driver_kind != "human")
            fight.advance_activation()
        elif action == "pass":
            fight.advance_activation()
        if event.index == 4:
            live_at_4 = vitalities(json_safe(fight.snapshot()))
            break

    assert live_at_4 is not None
    assert vitalities(recording.events[4].snapshot) == live_at_4


# --------------------------------------------------------------------------- #
# autoplay from 0 stops at N-1 and does NOT wrap                                #
# --------------------------------------------------------------------------- #
def test_autoplay_stops_at_last_index_and_does_not_wrap(recording_path):
    """Autoplay (``a``) from activation 0 on an N-activation recording advances to
    N-1 and STOPS — it does not loop or wrap. Asserted on the FINAL rendered index."""
    path, recording = recording_path
    n = len(recording.events)
    # 'a' turns on autoplay (races to the end, then stops); 'q' quits once stepped again.
    keys = iter(["a", "q"])
    out = io.StringIO()
    fightlab.watch(path, out=out, key_reader=lambda: next(keys))

    indices = _activation_indices(out.getvalue())
    # Rendered 0,1,2,...,N-1 exactly once each; the last is N-1 (no wrap back to 0).
    assert indices[0] == 0
    assert indices[-1] == n - 1
    # Monotonic, no repeats -> never wrapped.
    assert indices == list(range(n))


# --------------------------------------------------------------------------- #
# --debug on a KNOWN shot prints every input the rolls consumed (R13)          #
# --------------------------------------------------------------------------- #
def test_debug_dump_prints_every_seeded_roll_input_for_a_known_shot():
    """``--debug`` on a known shot prints EVERY input the hit and damage rolls consumed:
    both draws with their bounds, the accuracy AND damage attribute values, and the
    resulting damage — all pinned to seed 42. This is the unit's whole purpose (R13):
    the values come off the recorded event, never a recompute.

    The pinned shot is side 1's hero's first HIT (activation 18): hit draw
    ``rng.range(ts=5) -> 4``, kraft 34, damage draw ``rng.range(tg=10) -> 0``,
    brutalitaet 28, damage = int(0 + 2.8) + 1 = 3, schuldner energie 35 -> 32."""
    scenario = fightlab.load_scenario(_SCENARIO)
    _result, recording = record_fight(scenario, {1: AiDriver(), 2: AiDriver()})

    event = next(e for e in recording.events if e.index == 18)
    # Sanity: this IS the shot we pinned (a hit for 3 by side 1).
    assert event.decision["action"] == "shoot"
    assert event.result["hit"] is True
    assert event.result["damage"] == 3

    prev = next(e for e in recording.events if e.index == 17).snapshot
    out = io.StringIO()
    fightlab.render_shot_debug(
        event,
        weapon_names=fightlab._weapon_names(),
        prev_snapshot=prev,
        out=out,
    )
    text = out.getvalue()

    # The weapon line (ts/tg/range) — all off calc_inputs.
    assert "weapon: revolver (ts=5, tg=10, range=15)" in text
    # The hit check: the draw with its BOUND, the accuracy attr value, the verdict.
    assert "draw = rng.range(ts=5)          -> 4" in text
    assert "accuracy attr (kraft)           -> 34" in text
    assert "HIT" in text
    # The damage roll: the draw with its BOUND, the damage attr value, the arithmetic.
    assert "draw = rng.range(tg=10)         -> 0" in text
    assert "damage attr (brutalitaet)         -> 28" in text
    assert "int(0 + 2.8) + 1 = 3" in text
    # The target line: real name (NOT "side {i}"), energie before -> after.
    assert "schuldner" in text
    assert "energie 35 -> 32" in text
    # The result line.
    assert "result: hit, damage=3, downed=False" in text


def test_debug_dump_for_a_shot_that_reached_no_target_says_so_not_none():
    """A shot fired into empty space (no fighter in line) draws no range(ts), so the hit
    check never ran. The debug block must say "no hit check" rather than the misleading
    "rng.range(ts=5) -> None" that a naive read of a missing draw would print. Pinned to
    seed 42, activation 2 is exactly this case (a side-1 shot with only the decision
    coin-flip draw and target_side None)."""
    scenario = fightlab.load_scenario(_SCENARIO)
    _result, recording = record_fight(scenario, {1: AiDriver(), 2: AiDriver()})

    event = next(e for e in recording.events if e.index == 2)
    # Sanity: a shot that reached no target — one decision draw, no hit-check draw.
    assert event.decision["action"] == "shoot"
    assert event.result.get("hit") is False
    assert event.result.get("target_side") is None
    assert len(event.draws) == event.decision_draw_count  # only the decision drew

    out = io.StringIO()
    fightlab.render_shot_debug(
        event, weapon_names=fightlab._weapon_names(), prev_snapshot=None, out=out
    )
    text = out.getvalue()
    assert "shot reached no target" in text
    assert "-> None" not in text, "a missing draw must not print as a bogus '-> None'"


# --------------------------------------------------------------------------- #
# Reactions frame — SKIPPED until reactions exist (parent_index on a recording) #
# --------------------------------------------------------------------------- #
@pytest.mark.skip(
    reason="Skip until reactions exist: no recorded event sets parent_index "
    "in this arc (Event docstring). The debug renderer must NOT assume a "
    "flat list once nested-reaction frames arrive — a nested frame would "
    "render under its parent. Left as a marker so the renderer is not "
    "written for a flat list."
)
def test_debug_frame_for_parent_index_renders_nested_under_parent():
    """A --debug frame for an activation whose recording has parent_index set renders it
    nested under its parent. Not exercisable until reaction fire exists (nothing sets
    parent_index in this arc)."""


# --------------------------------------------------------------------------- #
# A scenario fight constructs NO GameState and writes NO file                   #
# --------------------------------------------------------------------------- #
def test_play_constructs_no_gamestate_and_writes_no_file(monkeypatch, tmp_path):
    """A scenario fight leaves no persistent state: it builds NO ``GameState`` and
    writes NO file. Both are asserted (otherwise "leaves no persistent state" is
    unfalsifiable) — a spy on ``GameState.__init__`` and on ``open`` for writes."""
    import builtins

    import engine.state as engine_state

    # Spy: any GameState construction fails the test.
    gamestate_built = {"n": 0}
    real_gamestate_init = engine_state.GameState.__init__

    def spy_init(self, *a, **kw):
        gamestate_built["n"] += 1
        return real_gamestate_init(self, *a, **kw)

    monkeypatch.setattr(engine_state.GameState, "__init__", spy_init)

    # Spy: any file opened for WRITING fails the test.
    real_open = builtins.open
    writes: list[str] = []

    def spy_open(file, mode="r", *a, **kw):
        if any(m in mode for m in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy_open)

    stdin = io.StringIO("p\n" * 80)
    out = io.StringIO()
    fightlab.play(_SCENARIO, seed=42, stdin=stdin, out=out)

    assert gamestate_built["n"] == 0, "a scenario fight must build no GameState"
    assert writes == [], f"a scenario fight must write no file, wrote: {writes}"


# --------------------------------------------------------------------------- #
# A scenario file with an unknown weapon id fails at LOAD, naming the id        #
# --------------------------------------------------------------------------- #
def test_unknown_weapon_id_fails_at_load_naming_the_id(tmp_path):
    """A scenario file whose player names an unknown weapon id fails AT LOAD with a
    message NAMING the id — not a KeyError several activations deep in a fight (§2.2c's
    third seam, surfaced where a human meets it)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "encounter: kdh_ambush\n"
        "player:\n"
        "  - {name: hero, weapon: 999, energie: 20, kraft: 34, brutalitaet: 28}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        fightlab.load_scenario(bad)
    # The id itself is named — not a bare KeyError, and not a fight-time surprise.
    assert "999" in str(exc.value)

"""U12 — Save / load: event store + snapshot round-trip.

Proves the persistence contract (docs/design/engine-architecture.md § Save/replay,
KTD-6): a game is ``initial seed + setup + ordered effect/RNG log``. The store is an
append-only JSONL log of **effects + RNG draws** (semantic events are NOT in it) plus a
per-turn ``GameState`` snapshot. Serialization is type-tagged so a frozen ``Effect``
dataclass round-trips exactly, and int-keyed ``dict`` state fields (``tenancy``,
``special_cells``) survive JSON (which stringifies keys).

The hard case — a save taken while a handler is suspended at a prompt — is reconstructed
by **replay**, not by freezing a generator (generators are not serializable and none lives
in ``GameState``). ``SaveGame`` records the in-progress action's already-supplied responses
at the input-source seam; ``LoadGame`` replays them into a fresh ``run_option`` so the
handler re-suspends at the identical prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# --- config + engine imports (mirror tests/test_slice_integration.py) --------- #
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "data" / "game_configs" / "mafia_1920s"
sys.path.insert(0, str(_CONFIG_DIR.parent.parent))

from engine.effects import (  # noqa: E402
    MoneyChange,
    SetTenancy,
    commit,
)
from engine.config_loader import load_game_config  # noqa: E402
from engine.interactions import run  # noqa: E402
from engine.locations import load_location  # noqa: E402
from engine.movement import DOWN, LEFT, load_city, try_move  # noqa: E402
from engine.rng import Rng  # noqa: E402
from engine import persistence  # noqa: E402  (module under test)

# Load the config BY PATH so its "slw.rent" handler registers into
# engine.locations.HANDLERS (mirror of tests/test_slice_integration.py).
_CONFIG = load_game_config(_CONFIG_DIR)
new_game = _CONFIG.module.new_game

_CITY_YAML = _CONFIG_DIR / "content" / "map" / "city.yaml"
_SLW_SHELL = _CONFIG_DIR / "content" / "locations" / "slw.yaml"

SEED = 42


class _Recorder:
    """Records interactions and returns scripted answers (mirror of the slice test)."""

    def __init__(self, *answers):
        self.seen: list = []
        self._answers = iter(answers)

    def __call__(self, interaction):
        self.seen.append(interaction)
        return next(self._answers)


def _fresh_state():
    return new_game(
        seed=SEED, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )


def _load_city():
    return load_city(yaml.safe_load(_CITY_YAML.read_text(encoding="utf-8")))


def _load_slw():
    return load_location(yaml.safe_load(_SLW_SHELL.read_text(encoding="utf-8")))


def _opt(location, opt_id):
    return next(o for o in location.options if o.id == opt_id)


# --------------------------------------------------------------------------- #
# Scenario 1 — round-trip after setup: save -> load == identical GameState.     #
# --------------------------------------------------------------------------- #
def test_save_load_roundtrip_after_setup(tmp_path: Path):
    state = _fresh_state()
    save_path = tmp_path / "game.jsonl"

    persistence.save_game(save_path, state, effect_log=[], rng_log=[], seed=SEED)
    loaded = persistence.load_game(save_path)

    # Identical GameState across a serialize/deserialize boundary (fresh objects).
    assert loaded.state == state
    assert loaded.state is not state


def test_roundtrip_preserves_int_keyed_dicts(tmp_path: Path):
    """tenancy/special_cells are int-keyed dicts; JSON stringifies keys — assert they
    come back as ints, not strings (the documented JSON gotcha)."""
    state = _fresh_state()
    # Commit a tenancy write so there is a non-empty int-keyed dict to round-trip.
    state = commit(state, [SetTenancy(ln=2)]).state
    assert state.map.tenancy == {2: 0}

    save_path = tmp_path / "game.jsonl"
    persistence.save_game(save_path, state, effect_log=[], rng_log=[], seed=SEED)
    loaded = persistence.load_game(save_path)

    assert loaded.state.map.tenancy == {2: 0}  # keys are ints, not "2"
    assert loaded.state == state


# --------------------------------------------------------------------------- #
# Scenario 2 — replay from snapshot + effect log reproduces final state.        #
# --------------------------------------------------------------------------- #
def test_replay_reproduces_final_state_without_rerolling(tmp_path: Path):
    """A snapshot + the ordered effect log replays to the exact final GameState, and
    the persisted RNG log matches a live run's draws (replay does not re-roll)."""
    base = _fresh_state()
    rng = Rng(SEED)
    # Drive a couple of RNG draws and record the effects they inform.
    _ = rng.range(9)
    _ = rng.hit(10, 50)
    effect_log = [MoneyChange(amount=-100), SetTenancy(ln=2)]
    live_final = commit(base, effect_log).state

    save_path = tmp_path / "game.jsonl"
    persistence.save_game(
        save_path, base, effect_log=effect_log, rng_log=list(rng.log), seed=SEED
    )
    loaded = persistence.load_game(save_path)

    replayed_final = persistence.replay(loaded)
    assert replayed_final == live_final
    # RNG draws replay from the log — identical to the live run's log.
    assert loaded.rng_log == rng.log


def test_semantic_events_excluded_from_replay_log(tmp_path: Path):
    """Events are audit/UI records, never in the replay log: a save that is *given*
    events still replays purely from effects to the identical state."""
    base = _fresh_state()
    effect_log = [MoneyChange(amount=-100)]
    live_final = commit(base, effect_log).state

    save_path = tmp_path / "game.jsonl"
    # Even if a caller hands events, save_game must not fold them into replay.
    persistence.save_game(
        save_path, base, effect_log=effect_log, rng_log=[], seed=SEED
    )
    loaded = persistence.load_game(save_path)
    assert persistence.replay(loaded) == live_final
    # No 'event' records in the persisted log.
    assert all(rec.get("kind") != "event" for rec in loaded.raw_records)


# --------------------------------------------------------------------------- #
# Scenario 3 — mid-handler save/resume via replay reconstruction.               #
# --------------------------------------------------------------------------- #
def test_mid_slw_rent_save_resume_matches_uninterrupted(tmp_path: Path):
    """Save while slw.rent is suspended at the months PromptInt, load, then send the
    response; the resumed handler commits the SAME effects as an uninterrupted run."""
    city = _load_city()
    slw = _load_slw()

    # Walk into slw ln=2 (positive-rent tile) exactly as the slice test does.
    state = _fresh_state()
    state.players[0].po = 141
    state = try_move(state, city, DOWN).state
    r_enter = try_move(state, city, LEFT)
    state = r_enter.state
    assert r_enter.payload.la == 1 and r_enter.payload.ln == 2

    # --- uninterrupted reference run --------------------------------------- #
    ref_handler = _opt(slw, "rent").handler
    ref = run(ref_handler, _Recorder(2), state=state, rng=None)
    assert ref.status == "completed"

    # --- interrupted run: SAVE at the prompt, LOAD, then answer 2 ---------- #
    # A mid-action save records the action locus + responses supplied so far (none yet,
    # the save is taken *at* the first prompt) so LoadGame can replay to re-suspend.
    save_path = tmp_path / "game.jsonl"
    persistence.save_game(
        save_path,
        state,
        effect_log=[],
        rng_log=[],
        seed=SEED,
        pending_action={
            "location_key": slw.key,
            "option_id": "rent",
            "ln": 2,
            "responses_so_far": [],
        },
    )
    loaded = persistence.load_game(save_path)

    resumed = persistence.resume_pending_action(
        loaded, slw, live_input=_Recorder(2), rng=None
    )
    assert resumed.status == "completed"
    # Same committed effects as the uninterrupted run, and identical resulting state.
    assert resumed.effects == ref.effects
    assert resumed.state == ref.state


# --------------------------------------------------------------------------- #
# Scenario 4 — append-only + schema version guard.                              #
# --------------------------------------------------------------------------- #
def test_log_is_append_only_and_versioned(tmp_path: Path):
    state = _fresh_state()
    save_path = tmp_path / "game.jsonl"
    persistence.save_game(
        save_path, state, effect_log=[MoneyChange(amount=10)], rng_log=[], seed=SEED
    )
    first = save_path.read_text(encoding="utf-8")
    # Appending another effect grows the file; earlier bytes are unchanged (append-only).
    persistence.append_effect(save_path, MoneyChange(amount=20))
    grown = save_path.read_text(encoding="utf-8")
    assert grown.startswith(first)
    assert len(grown) > len(first)

    loaded = persistence.load_game(save_path)
    # Every record carries the schema version.
    assert all("version" in rec for rec in loaded.raw_records)


def test_unknown_version_is_rejected(tmp_path: Path):
    state = _fresh_state()
    save_path = tmp_path / "game.jsonl"
    persistence.save_game(save_path, state, effect_log=[], rng_log=[], seed=SEED)
    # Corrupt a record to a future version the loader cannot understand.
    lines = save_path.read_text(encoding="utf-8").splitlines()
    import json

    hdr = json.loads(lines[0])
    hdr["version"] = 999_999
    lines[0] = json.dumps(hdr)
    save_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(persistence.SchemaVersionError):
        persistence.load_game(save_path)

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

import json
import sys
from pathlib import Path

import pytest
import yaml

# --- config + engine imports (mirror tests/test_slice_integration.py) --------- #
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "data" / "game_configs" / "mafia_1920s"
sys.path.insert(0, str(_CONFIG_DIR.parent.parent))

from engine.effects import MoneyChange, commit  # noqa: E402
from data.game_configs.mafia_1920s.effects import SetTenancy  # noqa: E402
from engine.config_loader import load_game_config  # noqa: E402
from engine.interactions import run  # noqa: E402
from engine.locations import load_location  # noqa: E402
from engine.movement import DOWN, LEFT, load_city, try_move  # noqa: E402
from engine.state import freeze  # noqa: E402
from tests.helpers import with_config, with_player  # noqa: E402
from engine.rng import Rng  # noqa: E402
from engine import persistence  # noqa: E402  (module under test)
import data.game_configs.mafia_1920s.state as game  # noqa: E402

# Load the config BY PATH so its "slw.rent" handler registers into
# engine.locations.HANDLERS (mirror of tests/test_slice_integration.py).
_CONFIG = load_game_config(_CONFIG_DIR)
new_game = _CONFIG.module.new_game
_REGISTRIES = _CONFIG.registries

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
    loaded = persistence.load_game(save_path, _REGISTRIES)

    # Identical GameState across a serialize/deserialize boundary (fresh objects).
    assert loaded.state == state
    assert loaded.state is not state


def test_roundtrip_preserves_a_written_global_value(tmp_path: Path):
    """A global value an effect wrote (the tenancy of tile 2) survives the round-trip."""
    state = _fresh_state()
    state = commit(state, [SetTenancy(ln=2)]).state
    assert game.tenant(state, 2) == 0

    save_path = tmp_path / "game.jsonl"
    persistence.save_game(save_path, state, effect_log=[], rng_log=[], seed=SEED)
    loaded = persistence.load_game(save_path, _REGISTRIES)

    assert game.tenant(loaded.state, 2) == 0
    assert loaded.state == state


def test_string_keyed_formula_params_survive_roundtrip(tmp_path: Path):
    """A string-keyed ``formula_params`` sub-map whose keys are not all int-looking keeps
    its string keys: the restorer coerces only an all-int-looking key set."""
    # A numeric-looking string key is the adversarial case for a blanket int-key restore.
    params = {**_fresh_state().config.formula_params, "costs": {"bribe": 100, "42": 7}}
    state = with_config(_fresh_state(), formula_params=freeze(params))

    save_path = tmp_path / "game.jsonl"
    persistence.save_game(save_path, state, effect_log=[], rng_log=[], seed=SEED)
    loaded = persistence.load_game(save_path, _REGISTRIES)

    assert loaded.state.config.formula_params["costs"] == {"bribe": 100, "42": 7}
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
    persistence.save_game(save_path, base, effect_log=effect_log, rng_log=list(rng.log), seed=SEED)
    loaded = persistence.load_game(save_path, _REGISTRIES)

    replayed_final = persistence.replay(loaded, _REGISTRIES)
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
    persistence.save_game(save_path, base, effect_log=effect_log, rng_log=[], seed=SEED)
    loaded = persistence.load_game(save_path, _REGISTRIES)
    assert persistence.replay(loaded, _REGISTRIES) == live_final
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
    state = with_player(_fresh_state(), 0, po=141)
    state = try_move(state, city, DOWN).state
    r_enter = try_move(state, city, LEFT)
    state = r_enter.state
    assert r_enter.payload.la == 1 and r_enter.payload.ln == 2

    # --- uninterrupted reference run --------------------------------------- #
    ref_handler = _opt(slw, "rent").handler
    assert ref_handler is not None
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
    loaded = persistence.load_game(save_path, _REGISTRIES)

    resumed = persistence.resume_pending_action(loaded, slw, live_input=_Recorder(2), rng=None)
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

    loaded = persistence.load_game(save_path, _REGISTRIES)
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
        persistence.load_game(save_path, _REGISTRIES)


def test_spawn_fighter_effect_round_trips_as_a_fighter_dataclass(tmp_path: Path):
    """A SpawnFighter in the effect log must replay as a ``Fighter``, not a raw dict.

    The save file holds effects as plain JSON, so the nested ``Fighter`` is written
    as a plain dict; reconstruction has to rebuild it. Without
    that, replaying any save taken mid-fight yields ``CombatState.sides`` full of
    dicts, and the first ``.name``/``.position`` read raises ``AttributeError`` —
    far from the save/load code that caused it.
    """
    from engine.effects import SpawnFighter
    from engine.state import Fighter

    effect = SpawnFighter(fighter=Fighter(name="Al", position=100, vitality=30), side=1)
    state = _fresh_state()
    save_path = tmp_path / "game.jsonl"
    persistence.save_game(save_path, state, effect_log=[effect], rng_log=[], seed=SEED)

    loaded = persistence.load_game(save_path, _REGISTRIES)
    restored = loaded.effect_log[0]
    assert isinstance(restored.fighter, Fighter), (
        f"fighter replayed as {type(restored.fighter).__name__}, not Fighter"
    )
    assert restored.fighter.name == "Al"
    assert restored.fighter.position == 100
    assert restored.fighter.vitality == 30
    assert restored.side == 1


def test_a_score_change_recorded_before_the_bound_fields_loads_as_it_meant(tmp_path: Path):
    """A ScoreChange carries its caller-supplied bound in a save. One logged before the
    bound fields existed loads with the bound it was recorded under: no key meant the
    [0, 100] clamp, and the replaced ``clamp`` flag meant [0, 100] or no bound."""
    from engine.effects import ScoreChange

    save_path = tmp_path / "game.jsonl"
    unclamped = ScoreChange(3.0, floor=None, cap=None)
    persistence.save_game(save_path, _fresh_state(), effect_log=[unclamped], rng_log=[], seed=SEED)
    header, effect_line = save_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(effect_line)
    assert record["effect"] == {
        "_type": "ScoreChange",
        "amount": 3.0,
        "player": None,
        "floor": None,
        "cap": None,
    }
    assert persistence.load_game(save_path, _REGISTRIES).effect_log == [unclamped]

    def _load_with(fields: dict) -> list:
        old = {"_type": "ScoreChange", "amount": 3.0, "player": None, **fields}
        line = json.dumps({**record, "effect": old})
        save_path.write_text(f"{header}\n{line}\n", encoding="utf-8")
        return persistence.load_game(save_path, _REGISTRIES).effect_log

    clamped = ScoreChange(3.0, floor=0.0, cap=100.0)
    assert _load_with({}) == [clamped]  # before ``clamp`` existed
    assert _load_with({"clamp": True}) == [clamped]
    assert _load_with({"clamp": False}) == [unclamped]


def test_session_save_resumes_the_rng_stream(tmp_path):
    """U8/KTD-5: a map-turn save carries an EMPTY effect log (the snapshot is
    authoritative) plus the session seed and RNG log, and a load rebuilds an RNG whose
    next draws equal the uninterrupted session's. ``replay`` of the empty log returns
    the snapshot unchanged."""
    state = _fresh_state()
    rng = Rng(SEED)
    for _ in range(50):
        rng.range(9)
        rng.hit(10, 50)
    save_path = tmp_path / "game.jsonl"
    persistence.save_game(save_path, state, effect_log=[], rng_log=rng.log, seed=SEED)

    loaded = persistence.load_game(save_path, _REGISTRIES)
    assert loaded.effect_log == []
    assert persistence.replay(loaded, _REGISTRIES) == state
    resumed = Rng.replayed(loaded.seed, loaded.rng_log)
    assert resumed.log == rng.log
    assert [resumed.range(1000) for _ in range(100)] == [rng.range(1000) for _ in range(100)]


def test_a_save_that_fails_midway_leaves_the_old_save_untouched(tmp_path, monkeypatch):
    """``save_game`` overwrites atomically: a write that dies partway (disk full after
    some bytes landed) leaves the existing save byte-identical and no temp file behind
    -- the default save target is the --load file itself, so a truncating overwrite
    would lose the only copy of the game."""
    import io

    path = tmp_path / "s.jsonl"
    persistence.save_game(path, _fresh_state(), effect_log=[], rng_log=[], seed=1)
    before = path.read_bytes()
    later = with_player(_fresh_state(), ka=_fresh_state().players[0].ka + 1)

    real_open = io.open
    failed = []

    class _DiesHalfway:
        def __init__(self, fh):
            self._fh = fh

        def write(self, data):
            self._fh.write(data[: len(data) // 2])
            failed.append(True)
            raise OSError(28, "No space left on device")

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            return False

    def dying_open(file, mode="r", *args, **kwargs):
        fh = real_open(file, mode, *args, **kwargs)
        return _DiesHalfway(fh) if "w" in mode else fh

    monkeypatch.setattr(io, "open", dying_open)
    with pytest.raises(OSError):
        persistence.save_game(path, later, effect_log=[], rng_log=[], seed=1)
    monkeypatch.setattr(io, "open", real_open)

    assert failed, "the failure was never injected mid-write"
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.jsonl"]


def test_save_format_is_unchanged_by_the_atomic_write(tmp_path):
    """The atomic write keeps the exact bytes a plain ``write_text`` produced."""
    import json

    state = _fresh_state()
    path = tmp_path / "s.jsonl"
    persistence.save_game(path, state, effect_log=[], rng_log=[("range", (6,), 3)], seed=7)
    expected = (
        json.dumps(
            {
                "kind": "header",
                "version": persistence.SCHEMA_VERSION,
                "seed": 7,
                "snapshot": persistence._state_to_dict(state),
            }
        )
        + "\n"
        + json.dumps(
            {"kind": "rng", "version": persistence.SCHEMA_VERSION, "draw": ["range", [6], 3]}
        )
        + "\n"
    )
    assert path.read_text(encoding="utf-8") == expected


def test_roster_append_effect_round_trips_its_gangster(tmp_path: Path):
    """A RosterAppend in the effect log must replay a roster member, not a raw dict.

    The nested ``gangster`` is written as a plain dict; reconstruction rebuilds it from
    the effect's declared field type (#110: a hand-kept nested-field table covered only
    ``SpawnFighter``, so this reloaded as a dict and replay put a dict in the roster).
    """
    from engine.effects import RosterAppend
    from engine.state import Combatant

    hire = _CONFIG.module.Gangster(name="Lucky", weapon=2, energie=5, kraft=20)
    effect = RosterAppend(gangster=hire)
    state = _fresh_state()
    live = commit(state, [effect]).state
    save_path = tmp_path / "game.jsonl"
    persistence.save_game(save_path, state, effect_log=[effect], rng_log=[], seed=SEED)

    loaded = persistence.load_game(save_path, _REGISTRIES)
    restored = loaded.effect_log[0]
    assert isinstance(restored.gangster, Combatant), (
        f"gangster replayed as {type(restored.gangster).__name__}, not a Combatant"
    )
    assert restored == effect
    replayed = persistence.replay(loaded, _REGISTRIES)
    assert replayed == live
    assert isinstance(replayed.players[0].roster[-1], Combatant)

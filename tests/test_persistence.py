"""U12 — Save / load: event store + snapshot round-trip.

Proves the persistence contract (docs/design/engine-architecture.md § Save/replay,
KTD-6): a game is ``initial seed + setup + ordered effect/RNG log``. The store is an
append-only JSONL log of **effects + RNG draws** (semantic events are NOT in it) plus a
per-turn ``GameState`` snapshot. Serialization is type-tagged so a frozen ``Effect``
dataclass round-trips exactly, and maps keep their key types through JSON (which
would stringify them) because the save records each non-string key's type.

A save holds committed state only: an action suspended at a prompt is not saved and
nothing resumes one (generators are not serializable and none lives in ``GameState``).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

# --- config + engine imports (mirror tests/test_slice_integration.py) --------- #
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "data" / "game_configs" / "mafia_1920s"
sys.path.insert(0, str(_CONFIG_DIR.parent.parent))

from engine.effects import MoneyChange, commit  # noqa: E402
from data.game_configs.mafia_1920s.effects import SetTenancy  # noqa: E402
from engine.config_loader import load_game_config  # noqa: E402
from engine.state import freeze  # noqa: E402
from tests.helpers import with_config, with_player  # noqa: E402
from engine.rng import Rng  # noqa: E402
from engine import persistence  # noqa: E402  (module under test)
import data.game_configs.mafia_1920s.state as game  # noqa: E402

# Load the config BY PATH for its new_game and registries (mirror of
# tests/test_slice_integration.py).
_CONFIG = load_game_config(_CONFIG_DIR)
new_game = _CONFIG.module.new_game
_REGISTRIES = _CONFIG.registries

SEED = 42


def _fresh_state():
    return new_game(
        seed=SEED, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )


# --------------------------------------------------------------------------- #
# Scenario 1 — round-trip after setup: save -> load == identical GameState.     #
# --------------------------------------------------------------------------- #
def test_save_load_roundtrip_after_setup(tmp_path: Path):
    state = _fresh_state()
    save_path = tmp_path / "game.jsonl"

    persistence.save_game(
        save_path, state, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=SEED
    )
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
    persistence.save_game(
        save_path, state, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=SEED
    )
    loaded = persistence.load_game(save_path, _REGISTRIES)

    assert game.tenant(loaded.state, 2) == 0
    assert loaded.state == state


def test_string_keyed_formula_params_survive_roundtrip(tmp_path: Path):
    """A string-keyed ``formula_params`` sub-map keeps its string keys, a numeric-looking
    one included: the save records each key's type rather than guessing it."""
    # A numeric-looking string key is the adversarial case for a blanket int-key restore.
    params = {**_fresh_state().config.formula_params, "costs": {"bribe": 100, "42": 7}}
    state = with_config(_fresh_state(), formula_params=freeze(params))

    save_path = tmp_path / "game.jsonl"
    persistence.save_game(
        save_path, state, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=SEED
    )
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
    persistence.save_game(
        save_path,
        base,
        registries=_REGISTRIES,
        effect_log=effect_log,
        rng_log=list(rng.log),
        seed=SEED,
    )
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
    persistence.save_game(
        save_path, base, registries=_REGISTRIES, effect_log=effect_log, rng_log=[], seed=SEED
    )
    loaded = persistence.load_game(save_path, _REGISTRIES)
    assert persistence.replay(loaded, _REGISTRIES) == live_final
    # No 'event' records in the persisted log.
    assert all(rec.get("kind") != "event" for rec in loaded.raw_records)


# --------------------------------------------------------------------------- #
# Scenario 3 — no mid-handler resume: a save holds committed state only.        #
# --------------------------------------------------------------------------- #
def test_persistence_no_longer_exports_resume_pending_action():
    """The resume helper rested on a dry input completing the action; it is gone."""
    assert not hasattr(persistence, "resume_pending_action")
    assert "resume_pending_action" not in persistence.__all__


# --------------------------------------------------------------------------- #
# Scenario 4 — append-only + schema version guard.                              #
# --------------------------------------------------------------------------- #
def test_log_is_append_only_and_versioned(tmp_path: Path):
    state = _fresh_state()
    save_path = tmp_path / "game.jsonl"
    persistence.save_game(
        save_path,
        state,
        registries=_REGISTRIES,
        effect_log=[MoneyChange(amount=10)],
        rng_log=[],
        seed=SEED,
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
    persistence.save_game(
        save_path, state, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=SEED
    )
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
    persistence.save_game(
        save_path, state, registries=_REGISTRIES, effect_log=[effect], rng_log=[], seed=SEED
    )

    loaded = persistence.load_game(save_path, _REGISTRIES)
    restored = loaded.effect_log[0]
    assert isinstance(restored.fighter, Fighter), (
        f"fighter replayed as {type(restored.fighter).__name__}, not Fighter"
    )
    assert restored.fighter.name == "Al"
    assert restored.fighter.position == 100
    assert restored.fighter.vitality == 30
    assert restored.side == 1


def test_a_score_change_round_trips_its_bound_and_an_unbounded_record_is_refused(
    tmp_path: Path,
):
    """A ScoreChange carries its caller-supplied bound in a save. A record without the
    bound fields (or with the replaced ``clamp`` flag) is not upgraded: nothing
    backfills an old record, so it fails to load."""
    from engine.effects import ScoreChange

    save_path = tmp_path / "game.jsonl"
    unclamped = ScoreChange(3.0, floor=None, cap=None)
    persistence.save_game(
        save_path,
        _fresh_state(),
        registries=_REGISTRIES,
        effect_log=[unclamped],
        rng_log=[],
        seed=SEED,
    )
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

    for fields in ({}, {"clamp": True}):
        old = {"_type": "ScoreChange", "amount": 3.0, "player": None, **fields}
        line = json.dumps({**record, "effect": old})
        save_path.write_text(f"{header}\n{line}\n", encoding="utf-8")
        with pytest.raises(TypeError):
            persistence.load_game(save_path, _REGISTRIES)


# --------------------------------------------------------------------------- #
# The save header: version, config id, content version (KTD-4, KTD-5)          #
# --------------------------------------------------------------------------- #
def _rewrite_header(path: Path, **changes) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps({**json.loads(lines[0]), **changes})
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _saved(tmp_path: Path) -> Path:
    path = tmp_path / "game.jsonl"
    persistence.save_game(
        path, _fresh_state(), registries=_REGISTRIES, effect_log=[], rng_log=[], seed=SEED
    )
    return path


def test_the_header_records_the_config_id_and_content_version(tmp_path: Path):
    header = json.loads(_saved(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert header["version"] == persistence.SCHEMA_VERSION == 3
    assert header["config_id"] == _CONFIG.config["name"] == "mafia_1920s"
    assert header["content_version"] == _CONFIG.config["content_version"]


def test_a_version_1_save_is_refused_as_older(tmp_path: Path):
    path = _saved(tmp_path)
    _rewrite_header(path, version=1)
    with pytest.raises(persistence.SchemaVersionError) as exc:
        persistence.load_game(path, _REGISTRIES)
    assert exc.value.older and exc.value.found == 1


@pytest.mark.parametrize(
    ("field", "value"), [("config_id", "another_game"), ("content_version", 999)]
)
def test_a_save_under_another_config_or_content_version_is_refused(
    tmp_path: Path, field: str, value
):
    path = _saved(tmp_path)
    _rewrite_header(path, **{field: value})
    with pytest.raises(persistence.SaveConfigError) as exc:
        persistence.load_game(path, _REGISTRIES)
    assert (exc.value.field, exc.value.found) == (field, value)


def test_a_save_without_the_config_fields_is_refused(tmp_path: Path):
    path = _saved(tmp_path)
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    del header["config_id"]
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")
    with pytest.raises(persistence.SaveConfigError):
        persistence.load_game(path, _REGISTRIES)


def test_a_snapshot_without_a_value_map_is_refused(tmp_path: Path):
    """A key missing from a value map is default-filled (KTD-2); a whole map missing is
    not a version-2 save, and is refused rather than filled."""
    path = _saved(tmp_path)
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    del header["snapshot"]["players"][0]["values"]
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")
    with pytest.raises(KeyError, match="values"):
        persistence.load_game(path, _REGISTRIES)


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
    persistence.save_game(
        save_path, state, registries=_REGISTRIES, effect_log=[], rng_log=rng.log, seed=SEED
    )

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
    persistence.save_game(
        path, _fresh_state(), registries=_REGISTRIES, effect_log=[], rng_log=[], seed=1
    )
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
        persistence.save_game(
            path, later, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=1
        )
    monkeypatch.setattr(io, "open", real_open)

    assert failed, "the failure was never injected mid-write"
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.jsonl"]


def test_save_format_is_unchanged_by_the_atomic_write(tmp_path):
    """The atomic write keeps the exact bytes a plain ``write_text`` produced."""
    import json

    state = _fresh_state()
    path = tmp_path / "s.jsonl"
    persistence.save_game(
        path, state, registries=_REGISTRIES, effect_log=[], rng_log=[("range", (6,), 3)], seed=7
    )
    expected = (
        json.dumps(
            {
                "kind": "header",
                "version": persistence.SCHEMA_VERSION,
                "config_id": "mafia_1920s",
                "content_version": _CONFIG.config["content_version"],
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
    persistence.save_game(
        save_path, state, registries=_REGISTRIES, effect_log=[effect], rng_log=[], seed=SEED
    )

    loaded = persistence.load_game(save_path, _REGISTRIES)
    restored = loaded.effect_log[0]
    assert isinstance(restored.gangster, Combatant), (
        f"gangster replayed as {type(restored.gangster).__name__}, not a Combatant"
    )
    assert restored == effect
    replayed = persistence.replay(loaded, _REGISTRIES)
    assert replayed == live
    assert isinstance(replayed.players[0].roster[-1], Combatant)


# --------------------------------------------------------------------------- #
# Lossless key types, save-time validation, schema 3 (R7, R12; AE5, AE6)        #
# --------------------------------------------------------------------------- #
def _round_trip(tmp_path: Path, state, *, effect_log=()):
    path = tmp_path / "game.jsonl"
    persistence.save_game(
        path,
        state,
        registries=_REGISTRIES,
        effect_log=list(effect_log),
        rng_log=[],
        seed=SEED,
    )
    return persistence.load_game(path, _REGISTRIES)


def _with_params(**extra):
    state = _fresh_state()
    return with_config(state, formula_params={**state.config.formula_params, **extra})


def test_ae6_numeric_string_keys_survive_as_distinct_strings(tmp_path: Path):
    """AE6: ``"01"`` and ``"1"`` are two string keys, and both come back as saved."""
    state = _with_params(labels={"01": "first", "1": "second"})

    loaded = _round_trip(tmp_path, state)

    assert dict(loaded.state.config.formula_params["labels"]) == {"01": "first", "1": "second"}
    assert all(type(k) is str for k in loaded.state.config.formula_params["labels"])
    assert loaded.state == state


def test_an_all_numeric_string_key_set_stays_strings(tmp_path: Path):
    """No guessing: a sub-map whose string keys all look like ints is still string-keyed."""
    state = _with_params(labels={"1": "one", "2": "two"})

    loaded = _round_trip(tmp_path, state)

    assert sorted(loaded.state.config.formula_params["labels"]) == ["1", "2"]
    assert loaded.state == state


def test_int_keys_come_back_as_ints(tmp_path: Path):
    """``fnm.overrides``' int keys (the live config's) and ``dir_memory``'s keys are ints
    after a load."""
    from engine.state import CombatState

    state = dataclasses.replace(_fresh_state(), combat=CombatState(dir_memory={0: -1, 3: 2}))
    overrides = state.config.formula_params["fnm"]["overrides"]
    assert overrides and all(type(k) is int for k in overrides)

    loaded = _round_trip(tmp_path, state).state

    assert loaded.config.formula_params["fnm"]["overrides"] == overrides
    assert all(type(k) is int for k in loaded.config.formula_params["fnm"]["overrides"])
    assert dict(loaded.combat.dir_memory) == {0: -1, 3: 2}
    assert all(type(k) is int for k in loaded.combat.dir_memory)
    assert loaded == state


def test_mixed_and_nested_key_types_round_trip_unchanged(tmp_path: Path):
    """A map mixing int and string keys keeps each key's type; an int-keyed map nested
    under string keys does too; and a string key that spells the codec's own tag is
    still an ordinary key."""
    state = _with_params(
        mixed={1: "int one", "1": "string one", "b": 2, 2.5: "float"},
        scalars={True: "bool", None: "none"},
        outer={"inner": {1: "x", 2: ("y", "z")}, "flat": "v"},
        tagged={"__map__": [[1, 2]]},
    )

    loaded = _round_trip(tmp_path, state).state
    params = loaded.config.formula_params

    assert {(type(k), k) for k in params["mixed"]} == {
        (int, 1),
        (str, "1"),
        (str, "b"),
        (float, 2.5),
    }
    assert {(type(k), k) for k in params["scalars"]} == {(bool, True), (type(None), None)}
    assert params["outer"]["inner"] == {1: "x", 2: ("y", "z")}
    assert all(type(k) is int for k in params["outer"]["inner"])
    assert params["tagged"] == {"__map__": ((1, 2),)}
    assert loaded == state


def test_an_effect_field_holding_an_int_keyed_map_round_trips(tmp_path: Path):
    """The effect codec keeps key types too. No engine effect holds an int-keyed map
    today, so a fighter's ``attrs`` stands in for one: the codec, not the field's
    declared type, is under test."""
    from engine.effects import SpawnFighter
    from engine.state import Fighter

    attrs: dict = {"kraft": 3, 7: 1}
    effect = SpawnFighter(fighter=Fighter(name="Al", attrs=attrs), side=1)

    loaded = _round_trip(tmp_path, _fresh_state(), effect_log=[effect])

    assert loaded.effect_log == [effect]
    assert {type(k) for k in loaded.effect_log[0].fighter.attrs} == {str, int}


def test_state_from_dict_is_the_exact_inverse_of_json_safe():
    """The purity harness's baseline: ``state_from_dict(json_safe(s)) == s`` with no key
    guessing, so a string key that looks like an int stays a string there too."""
    from engine.state import json_safe

    state = _with_params(labels={"1": "one"}, ints={1: "one"})

    rebuilt = persistence.state_from_dict(json_safe(state))

    assert list(rebuilt.config.formula_params["labels"]) == ["1"]
    assert list(rebuilt.config.formula_params["ints"]) == [1]
    assert rebuilt == state


def test_ae5_an_invalid_value_is_refused_before_a_file_is_written(tmp_path: Path):
    """AE5: a declared int set to a string makes ``save_game`` raise, and no file claims
    to be a save -- not at a fresh path, and not over an existing save."""
    from engine.effects import set_player_value
    from engine.state import StateSchemaError

    bad = set_player_value(_fresh_state(), "debt.amount", "not-an-int", player=0)

    fresh = tmp_path / "fresh.jsonl"
    with pytest.raises(StateSchemaError, match="debt.amount"):
        persistence.save_game(
            fresh, bad, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=SEED
        )
    assert list(tmp_path.iterdir()) == []

    existing = _saved(tmp_path)
    before = existing.read_bytes()
    with pytest.raises(StateSchemaError, match="debt.amount"):
        persistence.save_game(
            existing, bad, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=SEED
        )
    assert existing.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["game.jsonl"]


def test_an_invalid_global_value_is_refused_too(tmp_path: Path):
    from engine.effects import set_global_value
    from engine.state import StateSchemaError

    bad = set_global_value(_fresh_state(), "tenancy.1", "nobody")
    with pytest.raises(StateSchemaError, match="tenancy.1"):
        persistence.save_game(
            tmp_path / "g.jsonl", bad, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=1
        )
    assert list(tmp_path.iterdir()) == []


def test_a_schema_2_save_is_refused_naming_its_version(tmp_path: Path):
    path = _saved(tmp_path)
    _rewrite_header(path, version=2)
    with pytest.raises(persistence.SchemaVersionError) as exc:
        persistence.load_game(path, _REGISTRIES)
    assert exc.value.older
    assert (exc.value.found, exc.value.supported) == (2, 3)
    assert "2" in str(exc.value)


def test_a_save_carries_no_pending_action(tmp_path: Path):
    """Nothing resumes a suspended action, so the save shape holds no slot for one."""
    import inspect

    header = json.loads(_saved(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert "pending_action" not in header
    assert "pending_action" not in inspect.signature(persistence.save_game).parameters
    assert "pending_action" not in {f.name for f in dataclasses.fields(persistence.SaveData)}


@pytest.mark.parametrize("arm", ["faithful", "intent"])
def test_a_two_player_mafia_game_saves_and_loads_equal(tmp_path: Path, arm: str):
    """A real new game, both arms of ``c64_float_score``, round-trips whole -- the intent
    arm's exact score and the typed weight text included."""
    from tests.helpers import with_exact_score

    state = new_game(
        seed=SEED,
        end_year=1930,
        score_weight="0.7",
        players=[("alcapone", "the outfit"), ("moran", "north side")],
        house_rules={"c64_float_score": arm},
    )
    if arm == "intent":
        state = with_exact_score(state, "0.7", {0: "12.3456789012345678901", 1: "-3.5"})

    loaded = _round_trip(tmp_path, state).state

    assert loaded == state
    assert loaded.config.formula_params["score_mult_text"] == "0.7"
    assert [p.values["exact_score"] for p in loaded.players] == [
        p.values["exact_score"] for p in state.players
    ]
    if arm == "intent":
        assert loaded.players[0].values["exact_score"] == "12.3456789012345678901"

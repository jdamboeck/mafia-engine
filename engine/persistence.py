"""U12 — Save / load: append-only event store + snapshot round-trip (KTD-6).

A game is ``initial seed + setup + ordered effect/RNG log`` (docs/design/engine-architecture.md
§ Save/replay). This module persists exactly that and reconstructs a ``GameState`` from it,
purely additively — it changes nothing in the frozen ``engine.state``/``engine.interactions``/
``engine.effects`` contracts and imports nothing from ``server``/``clients``.

Store shape (append-only JSONL, one JSON object per line)
--------------------------------------------------------
- **line 0 — header**: ``{"kind":"header", "version", "seed", "snapshot", "pending_action"?}``
  where ``snapshot`` is a serialized per-turn ``GameState`` and ``pending_action`` (optional)
  is the mid-handler locus ``{location_key, option_id, ln, responses_so_far}``.
- **line 1..n — effect / rng records**: ``{"kind":"effect"|"rng", "version", ...}`` in commit
  order. **Semantic events are never written** — they are audit/UI records, not replay input
  (binding replay-semantics note in docs/plans/current-action-plan.md).

Replay = restore the snapshot, then ``commit`` the ordered effects. RNG draws are carried in
the log so a replay does not re-roll. A **mid-handler** save is restored by replaying the
recorded input responses into a fresh ``run_option`` so the handler re-suspends at the same
prompt — generators are not serializable and none lives in ``GameState`` (see U12 in the plan).

Serialization is **type-tagged**: each effect is written as ``{"_type": "<ClassName>", ...}``
and reconstructed by looking the tag up in :data:`_EFFECT_TYPES`. ``GameState`` is nested
dataclasses; it round-trips via :func:`~engine.state.json_safe` + typed reconstruction, with the
int-keyed mapping fields (``map.tenancy``, ``map.special_cells``) restored to int keys (JSON
stringifies dict keys). The graph's READ-ONLY collections are unwrapped to plain dict/list on
save and rebuilt as read-only on load, so a restored state is as immutable as a built one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from engine import effects as _effects
from engine.effects import SCHEMA_VERSION, commit
from engine.state import (
    Business,
    Clock,
    CombatState,
    Config,
    Contraband,
    Debt,
    Combatant,
    Fighter,
    Flags,
    GameState,
    Job,
    MapState,
    Player,
    Wanted,
    freeze,
    json_safe,
)

__all__ = [
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "SaveData",
    "save_game",
    "load_game",
    "append_effect",
    "replay",
    "resume_pending_action",
    "state_from_dict",
]


class SchemaVersionError(Exception):
    """Raised on load when a record carries a schema ``version`` this build cannot read."""


# --------------------------------------------------------------------------- #
# Effect (de)serialization — type-tagged                                      #
# --------------------------------------------------------------------------- #
#: Every concrete Effect class, keyed by its tag (the class name). Built from the
#: effects module's ``__all__`` so a newly added effect is covered automatically.
def _build_effect_types() -> dict[str, type]:
    out: dict[str, type] = {}
    for name in _effects.__all__:
        obj = getattr(_effects, name)
        if isinstance(obj, type) and is_dataclass(obj) and obj is not _effects.CommitResult:
            out[name] = obj
    return out


_EFFECT_TYPES: dict[str, type] = _build_effect_types()


def _effect_to_dict(effect: Any) -> dict:
    """Serialize a frozen Effect dataclass to a type-tagged plain dict.

    Walks with :func:`~engine.state.json_safe`, NOT ``dataclasses.asdict``. Since U2
    a nested :class:`~engine.state.Fighter` carries an ``attrs`` mapping held as a
    read-only ``mappingproxy``, and ``asdict`` deepcopies internally — ``mappingproxy``
    is not picklable, so it cannot walk a frozen graph at all. ``json_safe`` is the
    engine's declared inverse of that frozen form and unwraps it correctly.
    """
    tag = type(effect).__name__
    if tag not in _EFFECT_TYPES:
        raise TypeError(f"cannot serialize unknown effect type {tag!r}")
    return {"_type": tag, **json_safe(effect)}


#: Effect fields holding a nested dataclass, by effect type and field name.
#: ``dataclasses.asdict`` flattens these on the way out, so reconstruction has to
#: rebuild them — otherwise the effect replays carrying a plain dict and the first
#: attribute read fails far from the save/load code that caused it.
_NESTED_EFFECT_FIELDS: dict[str, dict[str, type]] = {
    "SpawnFighter": {"fighter": Fighter},
}


def _effect_from_dict(raw: dict) -> Any:
    """Reconstruct an Effect dataclass from its type-tagged dict."""
    tag = raw.get("_type")
    cls = _EFFECT_TYPES.get(tag) if isinstance(tag, str) else None
    if cls is None:
        raise TypeError(f"cannot deserialize unknown effect type {tag!r}")
    kwargs = {k: v for k, v in raw.items() if k != "_type"}
    for field, nested_cls in _NESTED_EFFECT_FIELDS.get(tag, {}).items():
        value = kwargs.get(field)
        if isinstance(value, dict):
            kwargs[field] = nested_cls(**value)
    return cls(**kwargs)


# --------------------------------------------------------------------------- #
# GameState (de)serialization — nested dataclasses + int-key restoration      #
# --------------------------------------------------------------------------- #
def _state_to_dict(state: GameState) -> dict:
    """Serialize a ``GameState`` to a JSON-safe nested dict."""
    return json_safe(state)


def _restore_int_keys(d: dict) -> Mapping[int, int]:
    """Return ``d`` with keys coerced back to int (JSON stringified them).

    Yields a READ-ONLY mapping: a restored graph must satisfy the same deep-immutability
    invariant as a freshly built one (R2/KTD-6).
    """
    return MappingProxyType({int(k): v for k, v in d.items()})


def _restore_numeric_keys(value: Any) -> Any:
    """Recursively restore int dict keys JSON stringified (general — for opaque
    config sub-dicts like ``formula_params['fnm']['overrides']`` whose keys are ints).

    A dict whose keys are ALL int-looking strings has them coerced back to int; mixed or
    non-numeric key sets are left untouched (real string keys must survive). Recurses into
    nested dicts and lists so arbitrarily deep config data round-trips losslessly.
    """
    if isinstance(value, Mapping):
        restored = {k: _restore_numeric_keys(v) for k, v in value.items()}
        if restored and all(isinstance(k, str) and _is_int_literal(k) for k in restored):
            restored = {int(k): v for k, v in restored.items()}
        return MappingProxyType(restored)  # read-only: R2 holds after load too
    if isinstance(value, list):
        return tuple(_restore_numeric_keys(v) for v in value)
    return value


def _is_int_literal(s: str) -> bool:
    body = s[1:] if s[:1] in "+-" else s
    return body.isascii() and body.isdigit()


#: The roster-member blueprint fields the engine names; every other saved key is a
#: game stat that folds into ``attrs`` (U2, amendment A4). ``vitality`` is a slot,
#: not an attr — this game's ``energie`` was mapped onto it at construction.
_ROSTER_BLUEPRINT_FIELDS = ("name", "weapon", "vitality")


def _roster_member_from_dict(raw: dict) -> Combatant:
    """Rebuild a saved roster member as a bare :class:`~engine.state.Combatant`.

    The engine does not know the config's ``Gangster`` type (layer rule), so it never
    reconstructs the named-field form. ``name``/``weapon`` are read by name; every
    other saved key is a game stat and is folded into ``attrs`` — which is exactly
    where the engine reads stats from, so the round-trip is lossless. A config that
    needs the named-field form reads it through ``attrs`` (amendment A4).
    """
    blueprint = {k: raw[k] for k in _ROSTER_BLUEPRINT_FIELDS if k in raw}
    stats = {k: v for k, v in raw.items() if k not in _ROSTER_BLUEPRINT_FIELDS and k != "attrs"}
    # Prefer an explicit saved attrs; fall back to the named stat keys.
    attrs = raw.get("attrs") or stats
    return Combatant(**blueprint, attrs=dict(attrs))


def _player_from_dict(raw: dict) -> Player:
    return Player(
        **{
            **raw,
            "roster": tuple(_roster_member_from_dict(g) for g in raw["roster"]),
            "jobs": Job(**raw["jobs"]),
            "debt": Debt(**raw["debt"]),
            "business": Business(**raw["business"]),
            "contraband": Contraband(**raw["contraband"]),
            "wanted": Wanted(**raw["wanted"]),
        }
    )


def _map_from_dict(raw: dict) -> MapState:
    return MapState(
        grid=freeze(raw["grid"]),
        tenancy=_restore_int_keys(raw["tenancy"]),
        special_cells=_restore_int_keys(raw["special_cells"]),
    )


def _config_from_dict(raw: dict) -> Config:
    """Reconstruct ``Config``, restoring int dict keys only where they legitimately occur.

    ``formula_params`` holds opaque nested game data whose sub-dicts may be int-keyed
    (e.g. ``fnm.overrides``) — restore those. ``action_costs`` is declared ``dict[str, int]``
    with *semantic string* keys, so it is left untouched: a blanket numeric-key restore
    would corrupt a legitimately string-keyed dict whose keys happened to look numeric.
    """
    restored = dict(raw)
    if "formula_params" in restored:
        restored["formula_params"] = _restore_numeric_keys(restored["formula_params"])
    return Config(**restored)


def _combat_from_dict(raw: dict) -> CombatState:
    """Reconstruct ``CombatState``, restoring the nested ``Fighter`` dataclasses (U4).

    ``sides`` is a 2-tuple of fighter-dict lists (JSON-safed by :func:`~engine.state.json_safe`
    into plain lists of plain dicts) — each dict rebuilds into a :class:`~engine.state.Fighter`.
    ``dir_memory`` is keyed by enemy fighter index (int), JSON-stringified on save like
    ``map.tenancy``, so it goes through the same :func:`_restore_int_keys` restoration.
    """
    restored = dict(raw)
    restored["sides"] = tuple(tuple(Fighter(**f) for f in side) for side in raw["sides"])
    restored["dir_memory"] = _restore_int_keys(raw["dir_memory"])
    return CombatState(**restored)


def state_from_dict(raw: dict) -> GameState:
    """Reconstruct a ``GameState`` from its serialized nested dict.

    Public because it is the declared inverse of :func:`~engine.state.json_safe`
    for the WHOLE state graph: it owns the typed per-dataclass reconstruction
    (and int-key restoration) that a generic walker cannot do. The test purity
    harness rebuilds its replay baseline with it, so this is a supported entry
    point, not persistence-internal — changing its shape breaks that harness.
    """
    return GameState(
        players=tuple(_player_from_dict(p) for p in raw["players"]),
        map=_map_from_dict(raw["map"]),
        combat=_combat_from_dict(raw["combat"]),
        clock=Clock(**raw["clock"]),
        config=_config_from_dict(raw["config"]),
        flags=Flags(**raw["flags"]),
    )


# --------------------------------------------------------------------------- #
# Loaded save bundle                                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SaveData:
    """A loaded save: the snapshot state, ordered effect/RNG logs, and metadata."""

    state: GameState
    effect_log: list
    rng_log: list
    seed: int
    pending_action: dict | None
    raw_records: list[dict]


# --------------------------------------------------------------------------- #
# Write                                                                       #
# --------------------------------------------------------------------------- #
def _check_version(rec: dict) -> None:
    if rec.get("version") != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"record schema version {rec.get('version')!r} != supported {SCHEMA_VERSION}"
        )


def save_game(
    path: str | Path,
    state: GameState,
    *,
    effect_log: list,
    rng_log: list,
    seed: int,
    pending_action: dict | None = None,
) -> None:
    """Write the append-only JSONL save: header (snapshot) then effect/RNG records.

    ``effect_log`` is the ordered committed-effect stream; ``rng_log`` is the ordered
    RNG draw records (``engine.rng.Rng.log`` tuples). Semantic events, if a caller has
    any, are intentionally not accepted here — they are never part of replay.
    """
    path = Path(path)
    header = {
        "kind": "header",
        "version": SCHEMA_VERSION,
        "seed": seed,
        "snapshot": _state_to_dict(state),
    }
    if pending_action is not None:
        header["pending_action"] = pending_action

    lines = [json.dumps(header)]
    for effect in effect_log:
        lines.append(
            json.dumps(
                {"kind": "effect", "version": SCHEMA_VERSION, "effect": _effect_to_dict(effect)}
            )
        )
    for draw in rng_log:
        # rng.log tuples are (method, args, value); JSON turns the tuple into a list.
        lines.append(json.dumps({"kind": "rng", "version": SCHEMA_VERSION, "draw": list(draw)}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_effect(path: str | Path, effect: Any) -> None:
    """Append one effect record to an existing save (append-only — never rewrites)."""
    path = Path(path)
    rec = {"kind": "effect", "version": SCHEMA_VERSION, "effect": _effect_to_dict(effect)}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #
def load_game(path: str | Path) -> SaveData:
    """Load a save: parse the JSONL, verify versions, reconstruct snapshot + logs."""
    path = Path(path)
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not records or records[0].get("kind") != "header":
        raise ValueError(f"{path}: missing header record")

    for rec in records:
        _check_version(rec)

    header = records[0]
    state = state_from_dict(header["snapshot"])
    effect_log = [
        _effect_from_dict(rec["effect"]) for rec in records if rec.get("kind") == "effect"
    ]
    # rng draws round-trip as [method, [args...], value]; normalize to the log tuple shape.
    rng_log = [
        (rec["draw"][0], tuple(rec["draw"][1]), rec["draw"][2])
        for rec in records
        if rec.get("kind") == "rng"
    ]
    return SaveData(
        state=state,
        effect_log=effect_log,
        rng_log=rng_log,
        seed=header["seed"],
        pending_action=header.get("pending_action"),
        raw_records=records,
    )


# --------------------------------------------------------------------------- #
# Replay + resume                                                             #
# --------------------------------------------------------------------------- #
def replay(save: SaveData) -> GameState:
    """Reproduce the final ``GameState`` = snapshot + ordered effect log.

    RNG draws are carried in ``save.rng_log`` for verification/auditing; replay itself
    does not re-roll because the committed effects already encode every outcome.
    """
    return commit(save.state, save.effect_log).state


def resume_pending_action(save: SaveData, location, *, live_input, rng=None):
    """Resume a mid-handler save by replaying recorded responses, then live input.

    Rebuilds the suspended handler by re-running its option through the real driver:
    a chained ``input_source`` first returns ``pending_action.responses_so_far`` (so the
    generator deterministically re-suspends at the same prompt), then hands off to
    ``live_input`` for the remaining interactions. Returns the driver's ``EngineResult``.
    """
    from engine.actions import run_option

    pending = save.pending_action
    if pending is None:
        raise ValueError("save has no pending_action to resume")
    if pending["location_key"] != location.key:
        raise ValueError(f"pending action location {pending['location_key']!r} != {location.key!r}")

    replayed = iter(pending["responses_so_far"])

    def _chained_input(interaction):
        try:
            return next(replayed)
        except StopIteration:
            return live_input(interaction)

    return run_option(
        location,
        pending["option_id"],
        save.state,
        ln=pending["ln"],
        input_source=_chained_input,
        rng=rng,
    )

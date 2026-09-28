"""Save / load: append-only event store + snapshot round-trip.

A game is ``initial seed + setup + ordered effect/RNG log`` (docs/design/engine-architecture.md
§ Save/replay). This module persists exactly that and reconstructs a ``GameState`` from it,
purely additively — it changes nothing in the frozen ``engine.state``/``engine.interactions``/
``engine.effects`` contracts and imports nothing from ``server``/``clients``.

Store shape (append-only JSONL, one JSON object per line)
--------------------------------------------------------
- **line 0 — header**: ``{"kind":"header", "version", "config_id", "content_version", "seed",
  "snapshot", "pending_action"?}`` where ``config_id``/``content_version`` name the config
  (and its content version) the save was written under, ``snapshot`` is a serialized
  per-turn ``GameState`` and ``pending_action`` (optional) is the mid-handler locus
  ``{location_key, option_id, ln, responses_so_far}``.
- **line 1..n — effect / rng records**: ``{"kind":"effect"|"rng", "version", ...}`` in commit
  order. **Semantic events are never written** — they are audit/UI records, not replay input
  (binding replay-semantics note in docs/plans/2026-07-13-001-refactor-state-event-foundation-plan.md).

Replay = restore the snapshot, then ``commit`` the ordered effects. RNG draws are carried in
the log so a replay does not re-roll. A **mid-handler** save is restored by replaying the
recorded input responses into a fresh ``run_option`` so the handler re-suspends at the same
prompt — generators are not serializable and none lives in ``GameState``.

Serialization is **type-tagged**: each effect is written as ``{"_type": "<tag>", ...}``
(the tag it registered under, :func:`engine.effects.register_effect`) and reconstructed by
looking the tag up in the loaded config's effect registry. ``GameState`` is nested
dataclasses; it round-trips via :func:`~engine.state.json_safe` + typed reconstruction, with the
int-keyed mapping fields (``combat.dir_memory``, int-keyed ``formula_params`` sub-maps) restored
to int keys (JSON stringifies dict keys). Game state is never a class here: it lives in the
declared value maps, which hold only scalars. The graph's READ-ONLY collections are unwrapped to plain dict/list on
save and rebuilt as read-only on load, so a restored state is as immutable as a built one.

**Loading needs the loaded config.** The engine cannot name what a config declares, so
:func:`load_game` and :func:`replay` take the config's :class:`Registries`: the effect
registry (effects are rebuilt by tag) and the state schema (value maps are rebuilt, with a
missing key default-filled and an unknown key refused). :func:`save_game` takes them too,
for the config's id and content version it stamps in the header; a save loaded under
another config or content version is refused (:class:`SaveConfigError`), as is a save of
another :data:`SCHEMA_VERSION` (:class:`SchemaVersionError`) -- nothing upgrades an old
save. Recordings (``engine.recording``) hold no effects or player state and do not take
them.
"""

from __future__ import annotations

import dataclasses
import json
import os
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from engine.effects import SCHEMA_VERSION, commit, effect_tag
from engine.state import (
    Clock,
    CombatState,
    Combatant,
    Config,
    Fighter,
    GameState,
    Player,
    StateSchema,
    json_safe,
)

__all__ = [
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "SaveConfigError",
    "UnknownEffectError",
    "Registries",
    "SaveData",
    "save_game",
    "load_game",
    "append_effect",
    "replay",
    "resume_pending_action",
    "state_from_dict",
]


class SchemaVersionError(Exception):
    """Raised on load when a record carries a schema ``version`` this build cannot read.

    ``found`` is the record's version as saved (any JSON value, or ``None`` when it has
    none) and ``supported`` this build's :data:`SCHEMA_VERSION`. :attr:`older` tells a
    save written by an older build from a newer or malformed one.
    """

    def __init__(self, found: Any, supported: int = SCHEMA_VERSION) -> None:
        self.found = found
        self.supported = supported
        super().__init__(f"record schema version {found!r} != supported {supported}")

    @property
    def older(self) -> bool:
        """Whether the record was written by an older build (a lower integer version)."""
        found = self.found
        return isinstance(found, int) and not isinstance(found, bool) and found < self.supported


class SaveConfigError(Exception):
    """Raised on load when a save was written under another config or content version.

    ``field`` is ``"config_id"`` or ``"content_version"``; ``found`` is the save's value
    and ``expected`` the loaded config's.
    """

    def __init__(self, field: str, found: Any, expected: Any) -> None:
        self.field = field
        self.found = found
        self.expected = expected
        super().__init__(f"save {field} {found!r} != the loaded config's {expected!r}")


class UnknownEffectError(TypeError):
    """Raised when an effect's tag is not in the effect registry it is resolved against."""


@dataclass(frozen=True)
class Registries:
    """What save loading and replay need from the loaded config.

    ``effects`` is the effect registry (tag -> class, :data:`engine.effects.EFFECTS`);
    ``state_schema`` declares the per-player and global value maps; ``config_id`` and
    ``content_version`` are the config's ``name`` and ``content_version`` from its
    ``config.yaml``, which a save's header records and a load checks. All come from
    :class:`~engine.config_loader.LoadedConfig` (``loaded.registries``).
    """

    effects: Mapping[str, type]
    state_schema: StateSchema
    config_id: str
    content_version: int


# --------------------------------------------------------------------------- #
# Effect (de)serialization — type-tagged                                      #
# --------------------------------------------------------------------------- #
def _effect_to_dict(effect: Any) -> dict:
    """Serialize a frozen Effect dataclass to a type-tagged plain dict.

    Walks with :func:`~engine.state.json_safe`, NOT ``dataclasses.asdict``. A nested
    :class:`~engine.state.Fighter` carries an ``attrs`` mapping held as a
    read-only ``mappingproxy``, and ``asdict`` deepcopies internally — ``mappingproxy``
    is not picklable, so it cannot walk a frozen graph at all. ``json_safe`` is the
    engine's declared inverse of that frozen form and unwraps it correctly.
    """
    tag = effect_tag(effect)
    if tag is None:
        raise UnknownEffectError(
            f"cannot serialize {type(effect).__name__!r}: it is not a registered effect"
        )
    return {"_type": tag, **json_safe(effect)}


def _dataclass_in(hint: Any) -> type | None:
    """The dataclass a field annotation names, directly or as one arm of a union."""
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return hint
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        found = [a for a in typing.get_args(hint) if isinstance(a, type)]
        classes = [a for a in found if dataclasses.is_dataclass(a)]
        if len(classes) == 1:
            return classes[0]
    return None


def _rebuild(cls: type, raw: dict) -> Any:
    """Rebuild dataclass ``cls`` from its JSON dict, recursing into nested dataclasses.

    Nested fields are found from the class's own field annotations, so an effect (the
    engine's or a config's) with a dataclass-typed field needs no table entry: a
    ``RosterAppend.gangster`` declared ``Combatant`` comes back a ``Combatant``, a
    ``SpawnFighter.fighter`` declared ``Fighter`` a ``Fighter``.
    """
    hints = typing.get_type_hints(cls)
    kwargs = dict(raw)
    for f in dataclasses.fields(cls):
        value = kwargs.get(f.name)
        nested = _dataclass_in(hints.get(f.name))
        if nested is not None and isinstance(value, dict):
            kwargs[f.name] = _rebuild(nested, value)
    return cls(**kwargs)


def _effect_from_dict(raw: dict, effects: Mapping[str, type]) -> Any:
    """Reconstruct an Effect dataclass from its type-tagged dict, by the registry."""
    tag = raw.get("_type")
    if not isinstance(tag, str) or tag not in effects:
        raise UnknownEffectError(
            f"the save names effect {tag!r}, which the loaded config does not register"
        )
    return _rebuild(effects[tag], {k: v for k, v in raw.items() if k != "_type"})


# --------------------------------------------------------------------------- #
# GameState (de)serialization — nested dataclasses + int-key restoration      #
# --------------------------------------------------------------------------- #
def _state_to_dict(state: GameState) -> dict:
    """Serialize a ``GameState`` to a JSON-safe nested dict."""
    return json_safe(state)


def _restore_int_keys(d: dict) -> Mapping[int, int]:
    """Return ``d`` with keys coerced back to int (JSON stringified them).

    Yields a READ-ONLY mapping: a restored graph must satisfy the same deep-immutability
    invariant as a freshly built one.
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
        return MappingProxyType(restored)  # read-only: the graph stays immutable after load
    if isinstance(value, list):
        return tuple(_restore_numeric_keys(v) for v in value)
    return value


def _is_int_literal(s: str) -> bool:
    body = s[1:] if s[:1] in "+-" else s
    return body.isascii() and body.isdigit()


def _roster_member_from_dict(raw: dict) -> Combatant:
    """Rebuild a saved roster member as a bare :class:`~engine.state.Combatant`.

    The engine does not know the config's ``Gangster`` type (layer rule), so it never
    reconstructs the named-field form: a member is saved as its blueprint slots
    (``name``, ``weapon``, ``vitality``) and its game stats under ``attrs`` -- exactly
    where the engine reads stats from, so the round-trip is lossless. A config that
    needs the named-field form reads it through ``attrs``.
    """
    return Combatant(**raw)


def _player_from_dict(raw: dict, schema: StateSchema | None, index: int) -> Player:
    values = raw["values"]
    if schema is None:
        # No schema: the exact inverse of json_safe (see state_from_dict).
        values = MappingProxyType(dict(values))
    else:
        # Default-fill a missing key; refuse an unknown or mistyped one.
        values = schema.load_player_values(values, where=f"players[{index}]")
    return Player(
        **{
            **raw,
            "values": values,
            "roster": tuple(_roster_member_from_dict(g) for g in raw["roster"]),
        }
    )


def _config_from_dict(raw: dict) -> Config:
    """Reconstruct ``Config``, restoring the int dict keys JSON stringified.

    ``formula_params`` holds opaque nested game data whose sub-dicts may be int-keyed
    (e.g. ``fnm.overrides``) — restore those.
    """
    restored = dict(raw)
    if "formula_params" in restored:
        restored["formula_params"] = _restore_numeric_keys(restored["formula_params"])
    return Config(**restored)


def _combat_from_dict(raw: dict) -> CombatState:
    """Reconstruct ``CombatState``, restoring the nested ``Fighter`` dataclasses.

    ``sides`` is a 2-tuple of fighter-dict lists (JSON-safed by :func:`~engine.state.json_safe`
    into plain lists of plain dicts) — each dict rebuilds into a :class:`~engine.state.Fighter`.
    ``dir_memory`` is keyed by enemy fighter index (int), JSON-stringified on save, so it
    goes through :func:`_restore_int_keys`.
    """
    restored = dict(raw)
    restored["sides"] = tuple(tuple(Fighter(**f) for f in side) for side in raw["sides"])
    restored["dir_memory"] = _restore_int_keys(raw["dir_memory"])
    return CombatState(**restored)


def state_from_dict(raw: dict, schema: StateSchema | None = None) -> GameState:
    """Reconstruct a ``GameState`` from its serialized nested dict.

    Public because it is the declared inverse of :func:`~engine.state.json_safe`
    for the WHOLE state graph: it owns the typed per-dataclass reconstruction
    (and int-key restoration) that a generic walker cannot do. The test purity
    harness rebuilds its replay baseline with it, so this is a supported entry
    point, not persistence-internal — changing its shape breaks that harness.

    With a ``schema`` the value maps load through it (default-fill, refuse unknown
    keys) — :func:`load_game` always passes the loaded config's. Without one they come
    back exactly as saved, which is what the exact-inverse use needs.
    """
    global_values = raw["values"]
    if schema is None:
        global_values = MappingProxyType(dict(global_values))
    else:
        global_values = schema.load_global_values(global_values)
    return GameState(
        players=tuple(_player_from_dict(p, schema, i) for i, p in enumerate(raw["players"])),
        combat=_combat_from_dict(raw["combat"]),
        clock=Clock(**raw["clock"]),
        config=_config_from_dict(raw["config"]),
        values=global_values,
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
        raise SchemaVersionError(rec.get("version"))


def _check_config(header: dict, registries: Registries) -> None:
    """Refuse a save written under another config, or another content version of it."""
    for field, expected in (
        ("config_id", registries.config_id),
        ("content_version", registries.content_version),
    ):
        found = header.get(field)
        if found != expected:
            raise SaveConfigError(field, found, expected)


def save_game(
    path: str | Path,
    state: GameState,
    *,
    registries: Registries,
    effect_log: list,
    rng_log: list,
    seed: int,
    pending_action: dict | None = None,
) -> None:
    """Write the append-only JSONL save: header (snapshot) then effect/RNG records.

    ``registries`` is the loaded config's (``loaded.registries``): the header records
    its config id and content version, which :func:`load_game` checks. ``effect_log``
    is the ordered committed-effect stream; ``rng_log`` is the ordered RNG draw records
    (``engine.rng.Rng.log`` tuples). Semantic events, if a caller has any, are
    intentionally not accepted here — they are never part of replay.
    """
    path = Path(path)
    header = {
        "kind": "header",
        "version": SCHEMA_VERSION,
        "config_id": registries.config_id,
        "content_version": registries.content_version,
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
    # Atomic overwrite: the save target is often the only copy of the game (the
    # --load file itself), so write a sibling temp file and swap it in with one
    # os.replace -- an interrupted write leaves the old save intact, never truncated.
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def append_effect(path: str | Path, effect: Any) -> None:
    """Append one effect record to an existing save (append-only — never rewrites)."""
    path = Path(path)
    rec = {"kind": "effect", "version": SCHEMA_VERSION, "effect": _effect_to_dict(effect)}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #
def load_game(path: str | Path, registries: Registries) -> SaveData:
    """Load a save: parse the JSONL, verify versions, reconstruct snapshot + logs.

    ``registries`` is the loaded config's (``loaded.registries``): effects are rebuilt
    by tag through its effect registry — an unregistered tag raises
    :class:`UnknownEffectError` naming it — and the value maps through its state schema.
    A record of another :data:`SCHEMA_VERSION` raises :class:`SchemaVersionError`, and
    a save written under another config id or content version :class:`SaveConfigError`.
    """
    path = Path(path)
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not records or records[0].get("kind") != "header":
        raise ValueError(f"{path}: missing header record")

    for rec in records:
        _check_version(rec)

    header = records[0]
    _check_config(header, registries)
    state = state_from_dict(header["snapshot"], registries.state_schema)
    effect_log = [
        _effect_from_dict(rec["effect"], registries.effects)
        for rec in records
        if rec.get("kind") == "effect"
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
def replay(save: SaveData, registries: Registries) -> GameState:
    """Reproduce the final ``GameState`` = snapshot + ordered effect log.

    RNG draws are carried in ``save.rng_log`` for verification/auditing; replay itself
    does not re-roll because the committed effects already encode every outcome. Each
    effect applies itself; ``registries`` is checked first, so a log holding an effect
    the loaded config does not register fails naming its tag rather than replaying it.
    """
    for effect in save.effect_log:
        tag = effect_tag(effect)
        if tag is None or tag not in registries.effects:
            raise UnknownEffectError(
                f"the log holds effect {tag or type(effect).__name__!r}, "
                "which the loaded config does not register"
            )
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

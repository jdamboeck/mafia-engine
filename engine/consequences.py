"""Strict conversion of YAML **consequence dicts** into typed effects (T6).

A game config's declarative shell may express an option's outcome as a flat list of
pure-data ``consequences`` — raw dicts loaded from YAML (see an option's
``resolve.consequences`` in :mod:`engine.locations`). This module is the one authority
that converts those raw dicts into the typed effect dataclasses of :mod:`engine.effects`.

**Config errors RAISE.** A consequence dict is authored at build time, so a malformed one
is a *config/programmer bug*, not a recoverable runtime condition: every violation raises
``ValueError`` and is never funnelled into an :class:`~engine.actions.EngineResult`
(mirrors the error semantics of :mod:`engine.actions`). Strict on all four axes: an
unknown ``type``, a missing ``type`` key, a missing required field, and an unknown extra
field each raise.

**Pure.** Conversion never touches game state and never mutates its input dicts — it only
reads the raw dicts and constructs frozen effect dataclasses. Field requiredness is
derived declaratively from each effect's :func:`dataclasses.fields` (a field with no
default is required; one with a default is optional), so the type registry below is the
only thing to extend when a new consequence type is added.

``engine/`` imports nothing from ``server``/``clients``/transport; this module holds no
display text.
"""

from __future__ import annotations

from dataclasses import MISSING, fields

from engine.effects import (
    AssignWeapon,
    MoneyChange,
    MsChange,
    ScoreAndRank,
    ScoreChange,
    SetEntryContext,
    SetPosition,
    StatChangeCapped,
    Teleport,
)

__all__ = ["effect_from_dict", "effects_from_dicts", "EFFECT_TYPES"]

#: Registry mapping a consequence ``type`` string (as authored in YAML) to its effect
#: dataclass. This is the sole extension point for new consequence types.
EFFECT_TYPES: dict[str, type] = {
    "ms_change": MsChange,
    "money_change": MoneyChange,
    "score_change": ScoreChange,
    "teleport": Teleport,
    "set_position": SetPosition,
    "set_entry_context": SetEntryContext,
    "stat_change_capped": StatChangeCapped,
    "assign_weapon": AssignWeapon,
    "score_and_rank": ScoreAndRank,
}


def _field_sets(effect_cls: type) -> tuple[set[str], set[str]]:
    """Return ``(required, optional)`` field-name sets derived from ``effect_cls``.

    A dataclass field with no default (and no default factory) is *required*; one with
    either is *optional*. Class-level constants like ``SCHEMA_VERSION`` are not dataclass
    fields and so never appear here.
    """
    required: set[str] = set()
    optional: set[str] = set()
    for f in fields(effect_cls):
        if f.default is MISSING and f.default_factory is MISSING:  # type: ignore[misc]
            required.add(f.name)
        else:
            optional.add(f.name)
    return required, optional


def effect_from_dict(raw: dict) -> object:
    """Convert one raw consequence dict into its typed effect dataclass.

    The ``type`` key selects the effect class (see :data:`EFFECT_TYPES`); the remaining
    keys become constructor keyword arguments. Strict: raises ``ValueError`` for an unknown
    or missing ``type``, a missing required field, or an unknown extra field. Absent
    optional fields fall back to the dataclass default. Never mutates ``raw`` and never
    touches game state.
    """
    if "type" not in raw:
        raise ValueError(
            "consequence dict is missing the required 'type' key; expected one of "
            f"{sorted(EFFECT_TYPES)}"
        )

    type_name = raw["type"]
    effect_cls = EFFECT_TYPES.get(type_name)
    if effect_cls is None:
        raise ValueError(
            f"unknown consequence type {type_name!r}; expected one of {sorted(EFFECT_TYPES)}"
        )

    required, optional = _field_sets(effect_cls)
    allowed = required | optional
    given = {k: v for k, v in raw.items() if k != "type"}
    given_keys = set(given)

    unknown = given_keys - allowed
    if unknown:
        raise ValueError(
            f"consequence type {type_name!r} has unknown field(s) {sorted(unknown)}; "
            f"allowed fields are {sorted(allowed)}"
        )

    missing = required - given_keys
    if missing:
        raise ValueError(
            f"consequence type {type_name!r} is missing required field(s) {sorted(missing)}"
        )

    return effect_cls(**given)


def effects_from_dicts(raws: list[dict]) -> list[object]:
    """Convert a list of raw consequence dicts into typed effects, preserving order.

    Applies :func:`effect_from_dict` to each dict in turn; any conversion error raises at
    the offending dict. Never mutates the input list or its dicts.
    """
    return [effect_from_dict(raw) for raw in raws]

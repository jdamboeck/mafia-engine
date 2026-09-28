"""This game's house rules: the quirk catalogue, its schema check, and the switch reader.

The catalogue (``content/house_rules.yaml``) records each rule quirk of the original:
its id, the BASIC line it cites, the faithful behaviour and -- where the intent is
clear -- the intended one, and whether setup offers a switch for it
(docs/design/config-and-content-contract.md § House rules). The file is checked when
the config loads (:data:`CATALOGUE`), so a bad entry fails the load, not a game.

The switches a game was set up with live on ``state.config.house_rules`` (id ->
``"faithful"``/``"intent"``), fixed for the game. A handler reads one through the state
with :func:`intent`; the engine never reads a switch by its id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from engine.state import HOUSE_RULE_SETTINGS, INTENT, GameState
from engine.types import ConfigValidationError

__all__ = [
    "CATALOGUE",
    "CATALOGUE_FILE",
    "HouseRule",
    "check_stored_map",
    "intent",
    "load_house_rules",
    "switchable",
]

#: Where the catalogue lives, relative to the config directory.
CATALOGUE_FILE = Path("content") / "house_rules.yaml"

_ID = re.compile(r"[a-z][a-z0-9_]*")
#: A citation of ``mf-prg.bas``: one line (``:26020``) or a line range (``:1015-1050``).
_CITATION = re.compile(r":\d+(-\d+)?")
_FIELDS = {"id", "citation", "faithful", "intent", "no_intent", "switch"}


@dataclass(frozen=True)
class HouseRule:
    """One catalogue entry.

    ``intent`` is the intended behaviour, or ``None`` when the intent is not clear --
    then ``no_intent`` says why. Only an entry with an intent text may have a
    ``switch``.
    """

    id: str
    citation: str
    faithful: str
    intent: str | None
    no_intent: str | None
    switch: bool


def _text(entry: dict, name: str, where: str) -> str:
    value = entry.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"{where} field {name!r} must be a non-empty string")
    return value


def _check_entry(entry: Any, index: int) -> HouseRule:
    where = f"house_rules[{index}]"
    if not isinstance(entry, dict):
        raise ConfigValidationError(f"{where} must be a mapping")
    unknown = set(entry) - _FIELDS
    if unknown:
        raise ConfigValidationError(f"{where} has unknown field(s) {sorted(unknown)}")
    rule_id = entry.get("id")
    if not isinstance(rule_id, str) or not _ID.fullmatch(rule_id):
        raise ConfigValidationError(
            f"{where} field 'id' must be a lower-case identifier, got {rule_id!r}"
        )
    where = f"house rule {rule_id!r}"
    citation = entry.get("citation")
    if not isinstance(citation, str) or not _CITATION.fullmatch(citation):
        raise ConfigValidationError(
            f"{where} field 'citation' must cite a BASIC line like ':26020', got {citation!r}"
        )
    faithful = _text(entry, "faithful", where)
    switch = entry.get("switch")
    if not isinstance(switch, bool):
        raise ConfigValidationError(f"{where} field 'switch' must be true or false")
    if ("intent" in entry) == ("no_intent" in entry):
        raise ConfigValidationError(
            f"{where} must give exactly one of 'intent' (the intended behaviour) "
            "and 'no_intent' (why there is none)"
        )
    if switch and "intent" not in entry:
        raise ConfigValidationError(f"{where} has a switch but no intent text")
    return HouseRule(
        id=rule_id,
        citation=citation,
        faithful=faithful,
        intent=_text(entry, "intent", where) if "intent" in entry else None,
        no_intent=_text(entry, "no_intent", where) if "no_intent" in entry else None,
        switch=switch,
    )


def load_house_rules(path: str | Path) -> tuple[HouseRule, ...]:
    """Load and check a quirk catalogue; a bad entry raises ``ConfigValidationError``."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("house_rules") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ConfigValidationError(f"{path}: 'house_rules' must be a list of entries")
    rules: list[HouseRule] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            rule = _check_entry(entry, index)
        except ConfigValidationError as exc:
            raise ConfigValidationError(f"{path}: {exc}") from None
        if rule.id in seen:
            raise ConfigValidationError(f"{path}: duplicate house rule id {rule.id!r}")
        seen.add(rule.id)
        rules.append(rule)
    return tuple(rules)


def switchable(rules: tuple[HouseRule, ...]) -> tuple[HouseRule, ...]:
    """The entries setup offers a switch for, in catalogue order."""
    return tuple(rule for rule in rules if rule.switch)


def check_stored_map(stored: Any, rules: tuple[HouseRule, ...]) -> dict[str, str]:
    """A house-rules map an artifact stores, checked against ``rules``' switches.

    An artifact that replays rules (a fight-lab scenario file) stores the map it runs
    under. It must hold a setting for exactly the catalogue's switches: a missing map,
    a missing switch, a switch the catalogue does not offer or an unknown setting
    raises ``ValueError`` naming it -- never a default (R21).
    """
    if not isinstance(stored, dict):
        raise ValueError("stores no house-rules map")
    offered = [rule.id for rule in switchable(rules)]
    for rule_id, setting in stored.items():
        if rule_id not in offered:
            raise ValueError(f"the catalogue offers no switch for house rule {rule_id!r}")
        if setting not in HOUSE_RULE_SETTINGS:
            raise ValueError(
                f"house rule {rule_id!r} is set to {setting!r}; "
                f"expected one of {list(HOUSE_RULE_SETTINGS)}"
            )
    for rule_id in offered:
        if rule_id not in stored:
            raise ValueError(f"stores no setting for house rule {rule_id!r}")
    return dict(stored)


#: This config's catalogue, checked on import (the config's load).
CATALOGUE = load_house_rules(Path(__file__).resolve().parent / CATALOGUE_FILE)

_SWITCH_IDS = frozenset(rule.id for rule in switchable(CATALOGUE))


def intent(state: GameState, rule_id: str) -> bool:
    """Whether the game plays house rule ``rule_id`` as intended (else faithfully).

    Reads ``state.config.house_rules``. An id the catalogue has no switch for raises
    ``KeyError`` -- a misspelt id is a bug, not a default. A switchable id the map does
    not hold reads faithful: only a hand-built state lacks it, since setup fills every
    switch and a save without the map is refused.
    """
    if rule_id not in _SWITCH_IDS:
        raise KeyError(f"no switchable house rule {rule_id!r} in the catalogue")
    return state.config.house_rules.get(rule_id) == INTENT

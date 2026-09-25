"""The engine spells no game stat — stated as executable assertions (U2, A4/A5).

CLAUDE.md's spine says the ``engine/`` package is a GENRE engine: it must name no
game vocabulary. A4 removed the roster ``Combatant``'s named stat fields; A5 does the
same for the on-grid ``Fighter`` and the combat depletion path. This file is that
goal's greppable proof.

Two complementary guards:

* **Structural** (AST/dataclass introspection) — the durable contract. ``Combatant``
  and ``Fighter`` carry the engine's blueprint SLOTS and an opaque ``attrs`` map, and
  NO dataclass field named after one of this game's stats (``energie``/``kraft``/
  ``brutalitaet``/``intelligenz``). ``vitality`` is the engine's word for the one
  depleting resource and IS a slot; ``energie`` is the game's word for that role and
  never appears in the engine.
* **Source grep** — the acceptance test the plan names (NEXT-STEPS §IMMEDIATE): a
  scan of ``engine/**/*.py`` finds a game-stat name only inside a docstring/comment
  citing ``mf-prg.bas`` (a source reference), or in the two documented data sites that
  legitimately name attrs KEYS as strings (``effects._STAT_NAMES``, the weapon
  requirement fields on ``engine.types``) — never as a field the engine addresses.

Break either guard and this file goes red, catching a future edit that reintroduces
a game word into the engine.
"""

from __future__ import annotations

import ast
import re
from dataclasses import fields
from pathlib import Path

import engine
from engine.combat import RulesBundle
from engine.state import Combatant, Fighter

#: This game's four stat names. The engine must spell none of them AS A FIELD.
#: ``vitality`` (the engine's word for the depleting-resource role) is NOT here — it
#: is exactly the engine-named slot that REPLACES the game's ``energie`` field.
_GAME_STAT_FIELDS = ("energie", "kraft", "brutalitaet", "intelligenz")

_ENGINE_DIR = Path(engine.__file__).parent


# --------------------------------------------------------------------------- #
# Structural — the durable contract (introspection, not text)                 #
# --------------------------------------------------------------------------- #
def test_combatant_names_no_game_stat_field():
    names = {f.name for f in fields(Combatant)}
    assert not (names & set(_GAME_STAT_FIELDS)), (
        f"engine Combatant names a game stat as a field: {names & set(_GAME_STAT_FIELDS)}"
    )
    # The engine's own word for the depleting resource is a slot (A4).
    assert "vitality" in names


def test_fighter_names_no_game_stat_field():
    names = {f.name for f in fields(Fighter)}
    assert not (names & set(_GAME_STAT_FIELDS)), (
        f"engine Fighter names a game stat as a field: {names & set(_GAME_STAT_FIELDS)}"
    )
    # A5: Fighter gets the engine's ``vitality`` slot, like Combatant.
    assert "vitality" in names


def test_rules_bundle_holds_no_vitality_attr_key():
    # A5: with vitality a slot the engine reads directly, the bundle no longer names
    # which attrs key holds it. A lingering ``vitality`` field would be a second name
    # for one thing (the "energie" string it used to carry drove the lookup).
    assert "vitality" not in {f.name for f in fields(RulesBundle)}


def test_combat_module_defines_no_fixed_enemy_stat_constant():
    # A5 / Finding 4: the fixed enemy 30/30 (mf-prg.bas:30245) is CONFIG data now.
    # The engine names neither the stat nor its value.
    import engine.combat as combat

    for dead in ("ENEMY_KRAFT", "ENEMY_BRUTALITAET"):
        assert not hasattr(combat, dead), f"engine.combat still defines {dead}"


# --------------------------------------------------------------------------- #
# Source grep — the acceptance test named by the plan                         #
# --------------------------------------------------------------------------- #
def _code_lines_naming_a_game_stat(path: Path) -> list[tuple[int, str]]:
    """Lines in ``path`` that name a game stat OUTSIDE a docstring or comment.

    Uses ``ast`` to find every string-literal (docstring) span and drops those lines,
    then drops the ``#``-comment tail of each remaining line, then greps the code that
    survives. A source citation (``# mf-prg.bas:30245`` or a docstring) is thus never a
    hit; only executable code that spells a game stat is.
    """
    source = path.read_text()
    tree = ast.parse(source)
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            end = node.end_lineno or node.lineno
            docstring_lines.update(range(node.lineno, end + 1))

    pattern = re.compile(r"\b(" + "|".join(_GAME_STAT_FIELDS) + r")\b")
    hits: list[tuple[int, str]] = []
    for lineno, raw in enumerate(source.splitlines(), start=1):
        if lineno in docstring_lines:
            continue
        code = raw.split("#", 1)[0]
        if pattern.search(code):
            hits.append((lineno, raw.strip()))
    return hits


#: The two documented sites where the engine legitimately names a game stat AS A
#: STRING KEY (data), not as a field it addresses. Both are called out in their own
#: source comments; neither makes the engine depend on the name being present.
#:  * ``effects._STAT_NAMES`` — the config-facing ``StatChange`` target set (attrs
#:    keys as data; documented to move to config in Step 8).
#:  * ``engine/types`` — the weapon-requirement fields (``req_kraft``/etc.), a
#:    WEAPON's stat gate, not a combatant stat the engine reads off a fighter.
_LEGITIMATE_DATA_SITES = {
    "effects.py",
    "types/__init__.py",
}


def test_engine_source_names_no_game_stat_in_code():
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(_ENGINE_DIR.rglob("*.py")):
        rel = path.relative_to(_ENGINE_DIR).as_posix()
        if rel in _LEGITIMATE_DATA_SITES:
            continue
        hits = _code_lines_naming_a_game_stat(path)
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "engine code names a game stat outside a docstring/comment:\n"
        + "\n".join(
            f"  engine/{rel}:{n}: {line}" for rel, hits in offenders.items() for n, line in hits
        )
    )

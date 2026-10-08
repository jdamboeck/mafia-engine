"""Typed accessors over this game's declared value maps, and its guard variables.

The engine keeps only genre-level player fields; this game's state lives in the
value maps that ``state_schema.yaml`` declares (``Player.values``, ``GameState.values``),
as flat scalar keys. This module is the one place that spells those keys, so handler
and effect code stays readable:

* **Entity views.** :class:`Debt`, :class:`Job`, :class:`Business`,
  :class:`Contraband` and :class:`Wanted` are small frozen views. Each owns the keys
  ``"<prefix>.<field>"`` (``Debt.amount`` is ``"debt.amount"``). :func:`read` builds one
  from a player; :func:`write` writes one back into a state (effect side);
  :func:`values_of` flattens views into a value map (construction side).
* **Scalars.** :func:`gang_name`, :func:`next_rank`, :func:`tip_target`,
  :func:`rented_months`, :func:`safe_skill` read one key each.
* **Globals.** :func:`tenant` reads ``uk(ln)`` (``"tenancy.<ln>"``, ``-1`` = vacant);
  :func:`hired_ids` reads the ``sg()`` set (``"hired.<id>"`` bools).

A read falls back to the declared default for a key the map does not hold, so a state
built without a full map (a test fixture) reads like a fresh game. A key the schema
does not declare raises ``KeyError`` on read.

The guard variables this game's shells use register at the bottom of the module.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from engine.conditions import register_guard_variable
from engine.config_loader import STATE_SCHEMA_FILE, load_state_schema
from engine.effects import set_global_value, set_player_value, target_index
from engine.state import GameState, Player

__all__ = [
    "SCHEMA",
    "Business",
    "Contraband",
    "Debt",
    "Job",
    "Wanted",
    "business",
    "contraband",
    "debt",
    "gang_name",
    "hired_ids",
    "job",
    "mark_hired",
    "next_rank",
    "read",
    "rented_months",
    "safe_skill",
    "set_tenant",
    "tenant",
    "tenancy_values",
    "hired_values",
    "tip_target",
    "values_of",
    "wanted",
    "write",
]

#: This game's declared value maps (``state_schema.yaml``), read once on import.
SCHEMA = load_state_schema(Path(__file__).resolve().with_name(STATE_SCHEMA_FILE))

#: Tenancy value meaning "no tenant" (``uk(ln)=0`` in the source, whose players are
#: 1-based; this port's player indices are 0-based, so 0 is a real tenant).
VACANT = -1


def _player_value(player: Player, key: str) -> Any:
    if key in player.values:
        return player.values[key]
    return SCHEMA.player[key].default


def _global_value(state: GameState, key: str) -> Any:
    if key in state.values:
        return state.values[key]
    return SCHEMA.global_[key].default


# --------------------------------------------------------------------------- #
# Entity views                                                                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Debt:
    """Loan-shark debt: ``amount`` is ``kr(sp)``, ``months`` the ``kz(sp)`` grace counter.

    Renamed from ``kr`` to avoid the collision with the gangster stat ``kraft``.
    ``months`` is set to 6 on borrowing (``mf-prg.bas:15030``) and 0 on full repayment
    (``:15075``). The upkeep tick (``kz(sp)=kz(sp)+(kz(sp)>0)``, ``:4305``) DECREMENTS it
    once per month while positive (C64 true is -1), and 0 triggers the debt collectors
    (``:4350``); see ``handlers/upkeep.py``'s COUNTER DIRECTION section.
    """

    PREFIX: ClassVar[str] = "debt"
    amount: int = 0
    months: int = 0


@dataclass(frozen=True)
class Job:
    """The accepted job (``mf-prg.bas:12308-12335``, ``:25550-25560``).

    ``type`` is ``jo(sp)`` (0 = no job), ``pending_pay`` is ``jl(sp)`` (a lump sum paid
    when the job completes, not a monthly wage), ``months_left`` is ``jd(sp)``
    (decremented once per month; the job completes at 0, ``:25550``).
    """

    PREFIX: ClassVar[str] = "job"
    type: int = 0
    pending_pay: int = 0
    months_left: int = 0


@dataclass(frozen=True)
class Business:
    """The shop: ``shop_tile`` is ``kg(sp)``, the owned kdh tile's ``ln`` (0 = none),
    ``shop_capital`` is ``kk(sp)``.

    A tile, not an ownership flag: shop income and sale logic need the tile.
    """

    PREFIX: ClassVar[str] = "business"
    shop_tile: int = 0
    shop_capital: int = 0


@dataclass(frozen=True)
class Contraband:
    """Contraband holdings; ``alcohol_barrels`` is ``ta(sp)`` (``mf-prg.bas:12035,12075``)."""

    PREFIX: ClassVar[str] = "contraband"
    fake_papers: int = 0
    counterfeit: int = 0
    alcohol_barrels: int = 0


@dataclass(frozen=True)
class Wanted:
    """Jail months (``gs(sp)``), chief-bribe months and the two win flags ``x5``/``x6``."""

    PREFIX: ClassVar[str] = "wanted"
    jail_months: int = 0
    bribe_months: int = 0
    x5: bool = False  # win flag — cash-transport event
    x6: bool = False  # win flag — mayor-hit event


_View = TypeVar("_View", Debt, Job, Business, Contraband, Wanted)


def _keys(view: Any) -> dict[str, Any]:
    return {f"{view.PREFIX}.{f.name}": getattr(view, f.name) for f in dataclasses.fields(view)}


def read(player: Player, cls: type[_View]) -> _View:
    """The ``cls`` view of ``player``'s value map."""
    return cls(
        **{f.name: _player_value(player, f"{cls.PREFIX}.{f.name}") for f in dataclasses.fields(cls)}
    )


def write(state: GameState, view: Any, *, player: int | None = None) -> GameState:
    """Return ``state`` with every key of ``view`` written into the target player's map.

    The effect-side setter: a config effect's ``apply`` builds the new view (usually
    ``dataclasses.replace`` of the one :func:`read` returned) and writes it here.
    """
    idx = target_index(state, player)
    for key, value in _keys(view).items():
        state = set_player_value(state, key, value, player=idx)
    return state


def values_of(*views: Any, **scalars: Any) -> dict[str, Any]:
    """A value map holding ``views`` flattened to their keys, plus ``scalars`` as given.

    The construction-side helper: ``Player(values=values_of(Debt(amount=500)))``.
    """
    out: dict[str, Any] = {}
    for view in views:
        out.update(_keys(view))
    out.update(scalars)
    return out


def debt(player: Player) -> Debt:
    return read(player, Debt)


def job(player: Player) -> Job:
    return read(player, Job)


def business(player: Player) -> Business:
    return read(player, Business)


def contraband(player: Player) -> Contraband:
    return read(player, Contraband)


def wanted(player: Player) -> Wanted:
    return read(player, Wanted)


# --------------------------------------------------------------------------- #
# Scalars                                                                      #
# --------------------------------------------------------------------------- #
def gang_name(player: Player) -> str:
    return _player_value(player, "gang_name")


def next_rank(player: Player) -> int:
    """``nr(sp)``: the pending rank :class:`~.effects.ScoreAndRank` recomputes (``:1165``)."""
    return _player_value(player, "nr")


def tip_target(player: Player) -> int:
    """``tp(sp)``: the held heist tip type 1..5, or 0 for none (``:12225``)."""
    return _player_value(player, "tip_target")


def rented_months(player: Player) -> int:
    """``um(sp)``: prepaid rent months (``:10040``, counted down at ``:4046``)."""
    return _player_value(player, "rented_months")


def safe_skill(player: Player) -> int:
    """``s9(sp)``: the safecracker-manual bonus (set at ``:18052``, read at ``:20111``)."""
    return _player_value(player, "safe_skill")


# --------------------------------------------------------------------------- #
# Globals                                                                      #
# --------------------------------------------------------------------------- #
def _tenancy_key(ln: int) -> str:
    return f"tenancy.{ln}"


def _hired_key(candidate_id: int) -> str:
    return f"hired.{candidate_id}"


def tenant(state: GameState, ln: int) -> int | None:
    """``uk(ln)``: the index of the player renting motel tile ``ln``, or ``None`` if vacant."""
    value = _global_value(state, _tenancy_key(ln))
    return None if value == VACANT else value


def set_tenant(state: GameState, ln: int, player: int) -> GameState:
    """Return ``state`` with motel tile ``ln`` rented by player index ``player``."""
    return set_global_value(state, _tenancy_key(ln), player)


def hired_ids(state: GameState) -> tuple[int, ...]:
    """The ``sg()`` set: every candidate id any player has hired, ascending."""
    prefix = "hired."
    return tuple(
        int(key[len(prefix) :])
        for key in SCHEMA.global_
        if key.startswith(prefix) and _global_value(state, key)
    )


def mark_hired(state: GameState, candidate_id: int) -> GameState:
    """Return ``state`` with candidate ``candidate_id`` marked hired (``sg(i)=1``)."""
    return set_global_value(state, _hired_key(candidate_id), True)


def tenancy_values(tenancy: Mapping[int, int]) -> dict[str, Any]:
    """A global value map holding ``tenancy`` (``{ln: player index}``) — construction side."""
    return {_tenancy_key(ln): owner for ln, owner in tenancy.items()}


def hired_values(candidate_ids: Iterable[int]) -> dict[str, Any]:
    """A global value map marking ``candidate_ids`` hired — construction side."""
    return {_hired_key(i): True for i in candidate_ids}


# --------------------------------------------------------------------------- #
# Guard variables — the names this game's shells use in a guard's ``var``       #
# --------------------------------------------------------------------------- #
def _active(state: GameState) -> Player:
    """The active player, ``state.players[sp]``."""
    return state.players[state.clock.active_player]


@register_guard_variable("rank")
def _rank(state: GameState, ln: int | None) -> int:
    return _active(state).rank  # ra(sp)


@register_guard_variable("gang_size")
def _gang_size(state: GameState, ln: int | None) -> int:
    return len(_active(state).roster)  # gz(sp): the roster counts the boss


@register_guard_variable("ka")
def _ka(state: GameState, ln: int | None) -> int:
    return _active(state).ka


@register_guard_variable("ms")
def _ms(state: GameState, ln: int | None) -> int:
    return _active(state).ms


@register_guard_variable("po")
def _po(state: GameState, ln: int | None) -> int:
    return _active(state).po


@register_guard_variable("gf")
def _gf(state: GameState, ln: int | None) -> float:
    return _active(state).gf


@register_guard_variable("sp")
def _sp(state: GameState, ln: int | None) -> int:
    return state.clock.active_player


@register_guard_variable("tenancy")
def _tenancy(state: GameState, ln: int | None) -> int:
    """``uk(ln)`` for the current tile: the tenant's 0-based index, -1 when vacant.

    The source's vacant room is ``uk(ln)=0`` with 1-based players; the port's players
    are 0-based, so a vacant room reads :data:`VACANT` (-1) and player 0's own room
    reads 0 -- a ``tenancy = -1`` guard is the source's ``uk(ln)=0`` (#146). No shell
    of this config guards on it: slw checks the tenancy inside its handlers
    (``handlers/slw.py``, #122). A tenancy guard is meaningless without a tile, so
    ``ln is None`` raises ``ValueError``.
    """
    if ln is None:
        raise ValueError("guard variable 'tenancy' requires a tile context (ln), but ln is None")
    owner = tenant(state, ln)
    return VACANT if owner is None else owner


@register_guard_variable("tip")
def _tip(state: GameState, ln: int | None) -> int:
    """``tp(sp)``, the held heist tip (0 = none): the guard the event cells are armed
    under (``content/map/city.yaml``, ``:2002``/``:2003``)."""
    return tip_target(_active(state))

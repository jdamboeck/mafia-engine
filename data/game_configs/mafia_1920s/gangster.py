"""This game's roster member — ``Gangster`` (U2, amendment A4).

The engine's :class:`engine.state.Combatant` is the blueprint: ``name``, ``weapon``,
the ``vitality`` slot (the one depleting resource), and an opaque ``attrs`` map.
``Gangster`` is this reference title's *filling* of it — it accepts the game's stat
names as construction kwargs and exposes them as read-only **properties**, but stores
each stat in exactly ONE place:

* ``energie`` -> the engine's ``vitality`` slot (this game's name for the role);
* ``kraft``/``intelligenz``/``brutalitaet`` -> ``attrs``.

Because the named stats are properties, not dataclass fields, they are **not
serialized** — ``json_safe`` sees only ``name``/``weapon``/``vitality``/``attrs``,
exactly what a bare ``Combatant`` serializes. So a freshly-built ``Gangster`` and its
save-then-reload ``Combatant`` round-trip to the same shape, and no stat is ever
double-stored.

Lives in the config, not the engine, so the engine names none of this game's stats.
The engine never imports this class: a *loaded* roster member is reconstructed as a
bare ``Combatant`` (``engine.persistence``), which keeps the layer rule intact.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from engine.state import Combatant

#: The non-vitality stat names this game carries in ``attrs`` (mf-prg.bas:311-312).
GANGSTER_ATTR_NAMES = ("kraft", "intelligenz", "brutalitaet")


# eq=False so this INHERITS Combatant's cross-class __eq__ (compare by blueprint
# fields, not exact class) instead of generating a Gangster-only one — otherwise a
# live Gangster would never equal a reloaded bare Combatant, breaking every purity
# and replay check (U2, amendment A4).
@dataclass(frozen=True, eq=False)
class Gangster(Combatant):
    """The engine's ``Combatant`` filled with this game's stats.

    Construct with ``Gangster(name=…, weapon=…, energie=…, kraft=…, intelligenz=…,
    brutalitaet=…)``. ``energie`` lands in the ``vitality`` slot; the other three land
    in ``attrs``. Read them back via the like-named properties — or, load-safe, via
    ``.vitality`` / ``.attrs[…]`` which work for a reloaded bare ``Combatant`` too.
    """

    def __init__(
        self,
        *,
        name: str = "",
        weapon: int = 0,
        energie: int = 5,  # starting energy fixed at 5 (mf-prg.bas:313, en=5)
        kraft: int = 0,  # strength; rolled 10-50 at setup
        intelligenz: int = 0,  # mf-prg.bas:311 quirk "in = x OR 30"
        brutalitaet: int = 0,  # brutality (mf-prg.bas:312); a combat damage bonus
        attrs: Any = None,
        vitality: int | None = None,
    ) -> None:
        # Fold the game's stat kwargs into the blueprint's single-source storage:
        # energie -> the vitality slot, the rest -> attrs. A NON-DEFAULT named kwarg
        # wins over the same key in a supplied attrs (the named stat is authoritative);
        # a supplied attrs still carries any EXTRA keys the class has no kwarg for (the
        # genre-engine case). A default-valued kwarg does not clobber a supplied attrs,
        # so a dataclasses.replace rebuild (which passes attrs, not the stat kwargs)
        # keeps the rebuilt values.
        merged = dict(attrs or {})
        for key, val, default in (
            ("kraft", kraft, 0),
            ("intelligenz", intelligenz, 0),
            ("brutalitaet", brutalitaet, 0),
        ):
            if val != default or key not in merged:
                merged[key] = val
        super().__init__(
            name=name,
            weapon=weapon,
            vitality=energie if vitality is None else vitality,
            attrs=MappingProxyType(merged),
        )

    @property
    def energie(self) -> int:
        """This game's name for the ``vitality`` slot."""
        return self.vitality

    @property
    def kraft(self) -> int:
        return self.attrs["kraft"]

    @property
    def intelligenz(self) -> int:
        return self.attrs["intelligenz"]

    @property
    def brutalitaet(self) -> int:
        return self.attrs["brutalitaet"]

    def with_attr(self, name: str, value: int) -> "Gangster":
        """Set a stat by key, single-sourced: ``energie``->vitality, else ``attrs``."""
        if name == "energie":
            return replace(self, vitality=value)
        merged = dict(self.attrs)
        merged[name] = value
        return replace(self, attrs=MappingProxyType(merged))

"""A test-only game config: one config effect over one declared value map.

``tests/test_effect_registry.py`` loads it by path. Importing it registers
:class:`CounterBump`, a game effect the engine does not name, under the tag
``CounterBump``. Its state lives in the ``counter`` key of the per-player value map
that ``config.yaml`` declares.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.effects import SCHEMA_VERSION, player_values, register_effect, set_player_value
from engine.state import GameState, Player


@register_effect("CounterBump")
@dataclass(frozen=True)
class CounterBump:
    """Add ``amount`` to the target player's declared ``counter`` value."""

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        current = player_values(state, player=self.player)["counter"]
        return set_player_value(state, "counter", current + self.amount, player=self.player)


def new_game(**_kwargs) -> GameState:
    """One player with the declared defaults, plus a non-default label."""
    return GameState(
        players=(Player(name="tester", values={"counter": 0, "label": "boss"}),),
        values={"round_bonus": 0.0},
    )

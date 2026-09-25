"""The ``SUBSTATES`` registry — nested sub-state handlers for ``LoadSubState`` (KTD-1).

A **sub-state handler** is a factory ``(ctx, params) -> Generator[Interaction, Response, result]``
— the same generator/interaction protocol a location handler uses, one nesting level
down. When a parent handler yields :class:`~engine.interactions.LoadSubState(kind, params)`,
the driver (:func:`engine.interactions.run`) looks up the factory registered under
``kind`` here, drives the child generator to completion **sharing the parent's**
``Ctx`` (so the child's ``ctx.apply``/``ctx.record`` append into the parent's buffers —
one atomic action across the nesting boundary, KTD-1), and ``.send()``s the child's
return value back into the parent as the ``LoadSubState`` response.

This mirrors the ``HANDLERS`` / :func:`engine.locations.register` registry shape exactly:
the decorator and registry are the engine's generic mechanism; the sub-state generator
functions themselves are **config-owned game code** (e.g. the ``waf`` weapon-spec sheet).

``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

from typing import Callable

__all__ = [
    "SUBSTATES",
    "register_substate",
]

#: Module-level registry mapping a sub-state ``kind`` string (e.g. ``"weapon_spec"``)
#: to its sub-state handler factory ``(ctx, params) -> generator``. A game config
#: populates this via :func:`register_substate`. An unknown ``kind`` yielded at
#: runtime is a config bug and raises :class:`ValueError` in the driver.
SUBSTATES: dict[str, Callable] = {}


def register_substate(kind: str) -> Callable[[Callable], Callable]:
    """Decorator registering a sub-state handler factory under ``kind`` in :data:`SUBSTATES`."""

    def _decorator(func: Callable) -> Callable:
        SUBSTATES[kind] = func
        return func

    return _decorator

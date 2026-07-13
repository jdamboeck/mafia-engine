"""The YAML **location-shell loader** + ``HANDLERS`` registry (PLAN.md §5.2).

This is the *declarative shell layer*: it parses a location's menu structure
(pure data) and resolves each option's handler id against a registry. It owns
menu structure and guard evaluation/denial — it does **not** run handlers (the
driver does that, a later unit). Handlers themselves (the procedural
generator coroutines) are registered by a game config; U7 registers slw's.

**KTD-8 (validation ownership).** The shell owns guard evaluation and denial:
a denied option is never entered and commits no effects — the caller emits the
option's ``on_denied`` key instead. :func:`available_options` returns exactly the
options whose guard passes; a denied option's denial key is read from
``Option.on_denied``.

**KTD-5 (no display text).** This module emits *keys* (``on_denied``), never
text. Themes resolve keys to strings elsewhere.

An option carries an ``id`` and **exactly one** of:
* ``handler`` — a string id resolved to a callable from :data:`HANDLERS`, or
* ``resolve.consequences`` — a flat list of pure-data effect dicts (no handler).

Neither, or both, is a load-time :class:`ValueError`.

``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from engine.conditions import build_context, evaluate, validate

__all__ = [
    "HANDLERS",
    "register",
    "Option",
    "Location",
    "load_location",
    "available_options",
    "enter_location",
]

#: Module-level registry mapping a handler-id string (e.g. ``"pub.recruit"``) to
#: its handler factory callable. A game config populates this — directly or via
#: :func:`register`. The loader resolves an option's ``handler`` string against
#: it; an unregistered id is a load-time :class:`ValueError`. No real handlers
#: live here.
#:
#: **Handlers are registered by game configs at load time** (see
#: :func:`engine.config_loader.load_game_config`), **not defined in** ``engine/``.
#: The ``@register`` decorator and this registry are the engine's generic
#: registration mechanism; the handler generator functions themselves are
#: config-owned code (e.g. ``data/game_configs/mafia_1920s/handlers/``).
HANDLERS: dict[str, Callable] = {}


def register(handler_id: str) -> Callable[[Callable], Callable]:
    """Decorator registering a handler factory under ``handler_id`` in :data:`HANDLERS`."""

    def _decorator(func: Callable) -> Callable:
        HANDLERS[handler_id] = func
        return func

    return _decorator


@dataclass
class Option:
    """One menu option in a location shell.

    Exactly one of ``handler`` (resolved callable) / ``consequences`` (flat list
    of effect dicts) is set; the other is ``None``. ``guard`` is the (validated)
    guard dict or ``None`` (always-available). ``on_denied`` is the message key
    emitted when the guard fails, or ``None``.
    """

    id: str
    guard: dict | None = None
    on_denied: str | None = None
    handler: Callable | None = None
    consequences: list[dict] | None = None


@dataclass
class Location:
    """A parsed location shell: a ``key`` and its ordered list of options."""

    key: str
    options: list[Option]


def _parse_option(raw: dict) -> Option:
    if "id" not in raw:
        raise ValueError(f"location option missing 'id': {raw!r}")
    opt_id = raw["id"]

    guard = raw.get("guard")
    # Validate the guard eagerly so a malformed shell fails at load time.
    validate(guard)

    has_handler = "handler" in raw
    has_resolve = "resolve" in raw
    if has_handler and has_resolve:
        raise ValueError(
            f"option {opt_id!r} has BOTH a handler and consequences; exactly one is allowed"
        )
    if not has_handler and not has_resolve:
        raise ValueError(
            f"option {opt_id!r} has NEITHER a handler nor consequences; exactly one is required"
        )

    handler: Callable | None = None
    consequences: list[dict] | None = None
    if has_handler:
        handler_id = raw["handler"]
        if handler_id not in HANDLERS:
            raise ValueError(
                f"option {opt_id!r} references unregistered handler {handler_id!r}; "
                f"registered: {sorted(HANDLERS)}"
            )
        handler = HANDLERS[handler_id]
    else:
        resolve = raw["resolve"]
        if not isinstance(resolve, dict) or "consequences" not in resolve:
            raise ValueError(
                f"option {opt_id!r} 'resolve' must be a dict with a 'consequences' list: {resolve!r}"
            )
        consequences = resolve["consequences"]
        if not isinstance(consequences, list):
            raise ValueError(
                f"option {opt_id!r} consequences must be a list: {consequences!r}"
            )

    return Option(
        id=opt_id,
        guard=guard,
        on_denied=raw.get("on_denied"),
        handler=handler,
        consequences=consequences,
    )


def load_location(raw: dict) -> Location:
    """Parse a location dict (from ``yaml.safe_load``) into a :class:`Location`.

    Validates each option: guard structure, handler resolution, and the
    exactly-one-of handler/consequences rule. Any violation raises
    :class:`ValueError` at load time.
    """
    if "key" not in raw:
        raise ValueError(f"location shell missing 'key': {raw!r}")
    options_raw = raw.get("options", [])
    if not isinstance(options_raw, list):
        raise ValueError(f"location 'options' must be a list: {options_raw!r}")
    options = [_parse_option(o) for o in options_raw]
    return Location(key=raw["key"], options=options)


def enter_location(state, la: int, ln: int) -> None:
    """Set the entry context for a location the active player just entered — the **ln seam**.

    This is the formalized seam U7 stubbed. When movement (:func:`engine.movement.try_move`)
    resolves a door to ``(la, ln)``, it calls this **before** the location's handler runs,
    so the handler reads the correct within-location tile index. A handler keys on ``ln``
    via the active player's ``last_location`` (U7's slw handler reads ``fnm(ln)`` off it),
    so this writes ``last_location = ln`` — preserving U7's contract exactly.

    ``la`` (the location id) is also recorded on the player (``last_la``) for callers that
    want it, without disturbing the ``last_location``-reads U7 depends on.
    """
    active = state.players[state.clock.active_player]
    active.last_location = ln  # the U7-read seam — set on real entry (U9)
    # Record la too, for callers that want the resolved location id (does not
    # affect the last_location contract U7 reads).
    setattr(active, "last_la", la)


def available_options(location: Location, state, ln: int | None = None) -> list[Option]:
    """Return the options whose guard passes for ``(state, ln)`` — KTD-8.

    Denied options are excluded (never entered, commit no effects); the caller
    reads a denied option's :attr:`Option.on_denied` key to emit its message.
    A guardless option is always available.
    """
    context = build_context(state, ln)
    return [opt for opt in location.options if evaluate(opt.guard, context)]

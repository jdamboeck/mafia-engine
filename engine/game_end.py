"""The standings and year-end runners — ENGINE mechanism, config-driven body (U6, KTD-1).

Mirrors :mod:`engine.upkeep` exactly. The two end-of-round / end-of-game screens of the
reference title (the between-rounds standings table, ``mf-prg.bas:4500-4515``, and the
year-end result, ``:40100-40166``) run through the SAME generator/interaction/effect
protocol as any location handler. The generators themselves are **config-owned game
code** — registered by the game config under :data:`STANDINGS_HANDLER_KEY` and
:data:`YEAR_END_HANDLER_KEY` in the SAME :data:`engine.locations.HANDLERS` registry a
location option's ``handler`` string resolves against. In particular the RANKING RULE
(who wins, how ties are shared) lives in the config handler, not here: this module only
supplies the generic runners that look a generator up, drive it via
:func:`engine.interactions.run`, and return the :class:`~engine.actions.EngineResult`
for the caller to adopt.

WHEN these run is the engine's decision, not the client's (KTD-2): the client calls
:func:`run_standings` on a round wrap and :func:`run_year_end` whenever
``advance_turn`` reports ``game_over``. Both flows are display-only — they ask the
player nothing and commit no effects — so each runner's default input source swallows
``ShowMessage`` and raises on anything that asks a question (the same contract as
:func:`engine.upkeep._refuse_input`), catching a future handler that adds a prompt
without updating this contract.

``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

from typing import Any

from engine.actions import EngineResult
from engine.interactions import ShowMessage, run

__all__ = [
    "STANDINGS_HANDLER_KEY",
    "YEAR_END_HANDLER_KEY",
    "run_standings",
    "run_year_end",
]

#: Registry key of the config's standings generator (the between-rounds scoreboard).
STANDINGS_HANDLER_KEY = "game_end.standings"

#: Registry key of the config's year-end generator (standings + winner/tie screen).
YEAR_END_HANDLER_KEY = "game_end.year_end"


def _refuse_input(interaction: Any) -> Any:
    """The FALLBACK ``input_source`` for both runners: discard narration, refuse questions.

    Standings and the year-end result are pure display. ``ShowMessage`` is delivered
    to the input source (the driver acks it regardless), so this swallows it; a caller
    that wants to RENDER the screens passes a real source. Anything that actually asks
    a question (``PromptInt``/``PromptChoice``/``Confirm``/combat) raises, surfacing a
    handler that broke the display-only contract at the call site.
    """
    if isinstance(interaction, ShowMessage):
        return None
    raise AssertionError(
        f"a game-end flow asked the input source for a response to {interaction!r}; "
        "standings and year-end are display-only — only ShowMessage may reach here"
    )


def _run(key: str, state: Any, input_source: Any, rng: Any, handlers: dict | None):
    if handlers is None:
        from engine.locations import HANDLERS as handlers  # noqa: N811 - local alias

    factory = handlers.get(key)
    if factory is None:
        raise KeyError(
            f"no handler registered under {key!r}; every game config must register "
            "its standings and year-end generators (engine.game_end)"
        )
    return run(factory, input_source or _refuse_input, state=state, rng=rng)


def run_standings(
    state: Any,
    *,
    input_source: Any = None,
    rng: Any = None,
    handlers: dict | None = None,
) -> EngineResult:
    """Run the config's standings generator against ``state`` and return its result.

    Per KTD-2 the caller passes the state from BEFORE ``advance_turn`` so the date
    shown is the round just finished. The flow is display-only: ``result.state`` equals
    ``state``. ``handlers`` is a test seam (defaults to :data:`engine.locations.HANDLERS`).

    Raises:
        KeyError: if no config registered :data:`STANDINGS_HANDLER_KEY`.
        AssertionError: (default input source) if the handler asks a question.
    """
    return _run(STANDINGS_HANDLER_KEY, state, input_source, rng, handlers)


def run_year_end(
    state: Any,
    *,
    input_source: Any = None,
    rng: Any = None,
    handlers: dict | None = None,
) -> EngineResult:
    """Run the config's year-end generator (standings, then the result) and return it.

    Per KTD-2 the caller passes the post-``advance_turn`` state, as ``:40100`` does.
    Display-only: ``result.state`` equals ``state``. ``handlers`` is a test seam.

    Raises:
        KeyError: if no config registered :data:`YEAR_END_HANDLER_KEY`.
        AssertionError: (default input source) if the handler asks a question.
    """
    return _run(YEAR_END_HANDLER_KEY, state, input_source, rng, handlers)

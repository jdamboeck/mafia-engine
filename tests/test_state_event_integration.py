"""T9 — State/Event Foundation integration acceptance test.

The State/Event Foundation plan refactored the action/result spine: movement and
option execution now flow through a single :class:`~engine.actions.EngineResult`
that carries FOUR distinct things — the new ``state``, the **semantic events**
emitted (audit/UI records, never applied), the **primitive effects** committed
(the sole state mutations), and a ``status``. This file is the acceptance test
that the whole spine holds together against the REAL loaded ``mafia_1920s`` config
and map — the same config/city/locations the vertical-slice trajectory uses.

Where ``test_slice_integration.py`` proves the end-to-end *play-through* (setup ->
real walk into slw -> rent paths -> denials -> deterministic final state) by
driving handlers through the bare ``engine.interactions.run`` driver and adopting
``result.state``, THIS file proves the *result/event/effect architecture* the
refactor introduced, one seam at a time:

* movement (``try_move``) returns an ``EngineResult`` whose events name the move
  (``MoveStep`` / ``EnterLocation``) and whose effects are the sole mutations
  (``SetPosition`` + ``MsChange`` on a step; ``SetEntryContext`` + ``MsChange`` on
  an entry) — and is PURE (new state out, input untouched);
* a guard-denied option through the location-aware ``run_option`` dispatcher yields
  ``status="blocked"`` with an ``OptionDenied`` event, a ``DeniedResult`` payload,
  ZERO effects, and the UNCHANGED input state object;
* a handler option through ``run_option`` commits its effects and returns a new
  state reflecting them, leaving the input state untouched.

HEADLESSNESS still holds: this file imports NOTHING from ``clients/``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.actions import DeniedResult, EngineResult, run_option
from engine.config_loader import load_game_config
from engine.effects import MoneyChange, MsChange, SetEntryContext, SetPosition
from engine.events import EnterLocation, MoveStep, OptionDenied
from engine.locations import load_location
from engine.movement import DOWN, LEFT, load_city, try_move
from tests.helpers import run_pure

_CONFIG_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
)

# Load mafia_1920s BY PATH so its slw.rent / pub.recruit handlers register into
# engine.locations.HANDLERS (same load mechanism as test_slice_integration.py).
_CONFIG = load_game_config(_CONFIG_DIR)
new_game = _CONFIG.module.new_game

_CITY_YAML = _CONFIG_DIR / "content" / "map" / "city.yaml"
_SLW_SHELL = _CONFIG_DIR / "content" / "locations" / "slw.yaml"
_PUB_SHELL = _CONFIG_DIR / "content" / "locations" / "pub.yaml"

# The same fixed seed the slice trajectory uses (setup -> cash 5500, rank 1, ms 25).
SEED = 42


def _load_city():
    return load_city(yaml.safe_load(_CITY_YAML.read_text(encoding="utf-8")))


def _load_shell(path: Path):
    return load_location(yaml.safe_load(path.read_text(encoding="utf-8")))


def _fresh_state():
    """A fresh single-player game at SEED (alcapone / the outfit)."""
    return new_game(
        seed=SEED,
        end_year=1930,
        score_weight=1.0,
        players=[("alcapone", "the outfit")],
    )


def _scripted(*answers):
    """An input_source returning scripted answers in order (ShowMessage auto-acked)."""
    it = iter(answers)

    def source(interaction):
        return next(it)

    return source


def _types(items):
    return [type(i) for i in items]


# --------------------------------------------------------------------------- #
# 1. Movement step: EngineResult / MoveStep event / SetPosition+MsChange /     #
#    pure (new state out, input untouched).                                    #
# --------------------------------------------------------------------------- #
def test_movement_step_result_events_effects_and_purity():
    city = _load_city()
    state = _fresh_state()
    # po=141 --DOWN--> 181 is a real street step on the loaded map.
    state.players[0].po = 141
    ms_before = state.players[0].ms

    result = try_move(state, city, DOWN)

    # The spine: try_move returns exactly one EngineResult.
    assert isinstance(result, EngineResult)
    assert result.status == "completed"
    assert result.payload.kind == "step"

    # Events name the move (semantic, audit/UI); MoveStep is present.
    assert MoveStep in _types(result.events)

    # Effects are the SOLE mutations: SetPosition AND MsChange.
    effect_types = _types(result.effects)
    assert SetPosition in effect_types
    assert MsChange in effect_types

    # A NEW state object with the move applied.
    assert result.state is not state
    assert result.state.players[0].po == 181
    assert result.state.players[0].ms == ms_before - 1  # STEP_COST

    # Purity: the input state was NOT mutated.
    assert state.players[0].po == 141
    assert state.players[0].ms == ms_before


# --------------------------------------------------------------------------- #
# 2. Location entry: EnterLocation event / SetEntryContext+MsChange effects /  #
#    resulting state has last_location + last_la set to the door's (ln, la).   #
# --------------------------------------------------------------------------- #
def test_location_entry_result_events_effects_and_entry_context():
    city = _load_city()
    state = _fresh_state()
    # po=181 --LEFT--> door 180 enters slw at la=1, ln=2 on the loaded map.
    state.players[0].po = 181
    ms_before = state.players[0].ms
    last_location_before = state.players[0].last_location
    last_la_before = state.players[0].last_la

    result = try_move(state, city, LEFT)

    assert isinstance(result, EngineResult)
    assert result.status == "completed"
    assert result.payload.kind == "enter"
    assert result.payload.la == 1 and result.payload.ln == 2

    # The entry is a semantic EnterLocation event.
    assert EnterLocation in _types(result.events)

    # Effects: SetEntryContext (the ln seam) AND MsChange (the -5 entry cost).
    effect_types = _types(result.effects)
    assert SetEntryContext in effect_types
    assert MsChange in effect_types

    # The resulting state carries the door's (ln, la) on the active player.
    entered = result.state.players[0]
    assert entered.last_location == 2  # ln
    assert entered.last_la == 1  # la
    assert entered.po == 181  # po does NOT move onto the door
    assert entered.ms == ms_before - 5  # ENTER_COST

    # Purity: the input state is untouched (ms and entry context all unchanged).
    assert state.players[0].ms == ms_before
    assert state.players[0].last_location == last_location_before
    assert state.players[0].last_la == last_la_before


# --------------------------------------------------------------------------- #
# 3. Guard denial through run_option: blocked / OptionDenied event /           #
#    DeniedResult payload / zero effects / SAME state object.                  #
# --------------------------------------------------------------------------- #
def test_guard_denial_through_run_option_is_blocked_and_pure():
    pub = _load_shell(_PUB_SHELL)
    state = _fresh_state()  # fresh game -> rank 1; pub.recruit guard needs rank>4.
    assert state.players[0].rank == 1

    result = run_option(pub, "recruit", state, ln=1, input_source=None)

    assert isinstance(result, EngineResult)
    assert result.status == "blocked"

    # The denial is a semantic OptionDenied event with the shell's on_denied key.
    assert result.events == [
        OptionDenied(
            location_key="pub",
            option_id="recruit",
            reason_key="locations.pub.rank_too_low",
        )
    ]

    # The payload carries the guard diagnostics (DeniedResult), not the event.
    assert isinstance(result.payload, DeniedResult)
    assert result.payload.location_key == "pub"
    assert result.payload.option_id == "recruit"
    assert result.payload.reason_key == "locations.pub.rank_too_low"

    # Denial mutates NOTHING: zero effects, the UNCHANGED input state object.
    assert result.effects == []
    assert result.state is state  # exact same object (KTD-8: handler never entered)


# --------------------------------------------------------------------------- #
# 4. Handler option through run_option: EngineResult / effects committed /     #
#    returned state reflects them / input state untouched (purity harness).    #
# --------------------------------------------------------------------------- #
def test_handler_option_through_run_option_commits_and_is_pure():
    slw = _load_shell(_SLW_SHELL)
    state = _fresh_state()
    state.players[0].last_location = 2  # tile 2: fnm(2) == base 50 (a paying tile)
    ka_before = state.players[0].ka

    result = run_option(slw, "rent", state, ln=2, input_source=_scripted(2))

    assert isinstance(result, EngineResult)
    assert result.status == "completed"

    # Effects were committed and the returned state reflects them.
    assert MoneyChange in _types(result.effects)
    assert result.state is not state
    # fnm(2) == 50 -> 2 months cost 100; cash drops by exactly 100.
    assert result.state.players[0].ka == ka_before - 100
    assert result.state.map.tenancy[2] == 0  # tenancy set to active player index 0
    assert result.state.players[0].rented_months == 2

    # Purity: the input state was NOT mutated.
    assert state.players[0].ka == ka_before
    assert 2 not in state.map.tenancy


def test_handler_option_purity_via_run_pure_harness():
    """Cross-check the handler purity contract with the run_pure harness.

    run_option delegates to the same driver run_pure wraps; driving slw.rent's
    resolved callable directly through the harness proves every observable change on
    result.state is explained by a buffered effect (no direct ctx.state mutation).
    """
    slw = _load_shell(_SLW_SHELL)
    state = _fresh_state()
    state.players[0].last_location = 2
    handler = next(o for o in slw.options if o.id == "rent").handler

    result = run_pure(handler, _scripted(2), state=state, rng=None)

    assert result.status == "completed"
    assert result.state.players[0].ka == state.players[0].ka - 100
    assert result.state.map.tenancy[2] == 0

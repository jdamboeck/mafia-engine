"""Tests for the slw (Schlupfwinkel / motel) location — U7, the FIRST real handler.

Proof-first: written and observed RED (module + effects + state field missing)
before implementation.

This exercises the whole spine end-to-end: a generator handler (``slw.rent``)
driven by the real :func:`engine.interactions.run` driver, yielding interactions,
applying effects through the buffer, sitting behind the U6 guard shell. Ports
``mf-prg.bas:10020-10045`` (the shared rent block) verbatim, including:

* the positive-rent path (deduct ``x*p``, set tenancy, accrue months),
* the premium tile (``fnm(1)`` is 150 under C64 true=-1, the dearest rent),
* the ``x<=0`` quiet-cancel path (:10030 — zero effects committed),
* the affordability guard (:10035 — no deduction on insufficient cash),
* the two refusals inside the handlers (room taken, ``:10010``; not the tenant,
  ``:10100``) behind a menu that never changes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.effects import MoneyChange
from data.game_configs.mafia_1920s.effects import RentAccrue, SetTenancy
from engine.locations import HANDLERS, available_options, load_location
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster
from tests.helpers import run_pure, scripted as _scripted
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# Load the mafia_1920s config BY PATH so its "slw.rent" handler registers (the
# config is not a pip-installed package — see engine.config_loader).
load_game_config(_CONFIG_DIR)
_SLW_SHELL = _CONFIG_DIR / "content" / "locations" / "slw.yaml"
_SLW_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "slw.yaml"

# fnm params read from config.yaml itself (one source): base 50, ln=1 -> 150,
# ln=3/4 -> 100 (mf-prg.bas:115 under C64 true=-1).
_FNM_PARAMS = yaml.safe_load((_CONFIG_DIR / "config.yaml").read_text(encoding="utf-8"))[
    "formula_params"
]["fnm"]


def _state(*, ka=5000, ln=2, active=0, players=1, tenancy=None):
    """A GameState with the fnm params wired in and the active player at tile ``ln``.

    ``ln`` is delivered to the handler via the active player's ``last_location``
    field (the U7 seam; U9 will formalize how the turn system populates it).
    """
    # The graph is frozen (R1/R2): the active player is constructed WITH its ln
    # rather than having it assigned afterwards.
    plist = tuple(
        Player(ka=ka, roster=(Gangster(),), last_location=ln if i == active else 0)
        for i in range(players)
    )
    return GameState(
        players=plist,
        clock=Clock(active_player=active, player_count=players),
        config=Config(formula_params={"fnm": _FNM_PARAMS}),
        values=game.tenancy_values(tenancy or {}),
    )


def _load_shell():
    return load_location(yaml.safe_load(_SLW_SHELL.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Handler: rent success at a positive-rent tile                               #
# --------------------------------------------------------------------------- #
def test_rent_two_months_positive_tile():
    # ln=2 -> fnm(2) == base == 50; rent 2 months -> cost 100.
    st = _state(ka=5000, ln=2, active=0)
    # run_pure additionally asserts the handler mutated NO state directly (purity harness).
    result = run_pure(HANDLERS["slw.rent"], _scripted(2), state=st, rng=None)

    assert result.status == "completed"
    # Three effects: deduct 100, set tenancy[2]=0 (sp), accrue 2 months.
    assert result.effects == [
        MoneyChange(-100),
        SetTenancy(2),
        RentAccrue(2),
    ]
    # Post-commit state reflects all three.
    assert result.state.players[0].ka == 4900
    assert game.tenant(result.state, 2) == 0
    assert game.rented_months(result.state.players[0]) == 2


# --------------------------------------------------------------------------- #
# Handler: premium tile — fnm(1) == 150, the dearest rent                     #
# --------------------------------------------------------------------------- #
def test_premium_tile_rent_is_150_a_month():
    # ln=1 -> fnm(1) == 50-0-100*(-1) == 150 (:115). Renting 2 months costs 300.
    # (A true=+1 reading would make this -50, a tile that PAYS the tenant.)
    st = _state(ka=5000, ln=1, active=0)
    result = run_pure(HANDLERS["slw.rent"], _scripted(2), state=st, rng=None)

    assert result.status == "completed"
    assert result.effects == [MoneyChange(-300), SetTenancy(1), RentAccrue(2)]
    assert result.state.players[0].ka == 4700
    assert game.tenant(result.state, 1) == 0
    assert game.rented_months(result.state.players[0]) == 2


# --------------------------------------------------------------------------- #
# Handler: 0-month quiet cancel (:10030) — ZERO effects committed             #
# --------------------------------------------------------------------------- #
def test_zero_months_cancels_with_no_effects():
    st = _state(ka=5000, ln=2, active=0)
    result = run_pure(HANDLERS["slw.rent"], _scripted(0), state=st, rng=None)

    assert result.status == "completed"  # quiet return, not a driver-cancel
    assert result.effects == []  # atomic: nothing applied
    assert result.state.players[0].ka == 5000  # unchanged
    assert game.tenant(result.state, 2) is None  # no tenancy set
    assert game.rented_months(result.state.players[0]) == 0


# --------------------------------------------------------------------------- #
# Handler: insufficient cash (:10035) — NO deduction, not-enough-money msg    #
# --------------------------------------------------------------------------- #
def test_insufficient_cash_no_deduction():
    # ka=10, rent 5 months at p=50 -> need 250 > 10.
    st = _state(ka=10, ln=2, active=0)
    src = _scripted(5)

    result = run_pure(HANDLERS["slw.rent"], src, state=st, rng=None)

    assert result.status == "completed"
    assert result.effects == []  # nothing deducted / no tenancy
    assert result.state.players[0].ka == 10
    assert game.tenant(result.state, 2) is None


def test_insufficient_cash_emits_not_enough_money():
    """The refusal NARRATION reaches the client (#43).

    Before #43 the driver acked ShowMessage without delivering it, so this test had
    to hand-drive the generator out-of-band to see the message at all — proving the
    handler yielded it, but NOT that any client could ever render it. Now the message
    is delivered to the input source like any other interaction, so the assertion is
    on what a real client would actually display.
    """
    st = _state(ka=10, ln=2, active=0)
    src = _scripted(5)  # PromptInt for months -> 5 (needs 250, has 10)

    result = run_pure(HANDLERS["slw.rent"], src, state=st, rng=None)

    assert "system.not_enough_money" in src.message_keys(), (
        "the refusal message never reached the input source; a client cannot render it"
    )
    assert result.effects == []  # no effects buffered on the insufficient path


# --------------------------------------------------------------------------- #
# The menu is fixed; each option refuses inside its handler (:10010, :10100)  #
# --------------------------------------------------------------------------- #
def test_menu_offers_all_three_options_whoever_holds_the_room():
    """The source prints the location's ``aw`` options from its file (``:3030``) with no
    precondition, so the menu never changes and no option moves up a place."""
    loc = _load_shell()
    for tenancy, active in (({}, 0), ({2: 0}, 0), ({2: 1}, 0), ({2: 1}, 1)):
        st = _state(ln=2, active=active, players=2, tenancy=tenancy)
        assert [o.id for o in available_options(loc, st, ln=2)] == ["rent", "pay_rent", "leave"]


def test_rent_refused_when_the_room_is_taken():
    # :10010 ``ifuk(ln)<>0thenprint"'nichts mehr frei!'":goto1100`` -- no rent quote.
    st = _state(ln=2, active=0, players=2, tenancy={2: 1})
    src = _scripted()
    result = run_pure(HANDLERS["slw.rent"], src, state=st, rng=None)

    assert result.effects == []
    assert src.message_keys() == ["locations.slw.no_room"]


def test_player_zero_cannot_rent_their_own_room_again():
    """The first player's own room is not vacant (#122).

    The source's players are 1-based, so ``uk(ln)=0`` means vacant and the first
    player's room holds 1. This port's first player is index 0; the room they hold
    is still taken, so renting it refuses like any taken room (``:10010``).
    """
    st = _state(ln=2, active=0, players=1, tenancy={2: 0})
    src = _scripted()
    result = run_pure(HANDLERS["slw.rent"], src, state=st, rng=None)

    assert result.effects == []
    assert src.message_keys() == ["locations.slw.no_room"]


def test_pay_rent_refused_when_not_the_tenant():
    # :10100 ``ifuk(ln)<>spthenprint"du wohnst hier nicht!":nm=1:goto1100``.
    for tenancy in ({}, {2: 1}):
        st = _state(ln=2, active=0, players=2, tenancy=tenancy)
        src = _scripted()
        result = run_pure(HANDLERS["slw.pay_rent"], src, state=st, rng=None)

        assert result.effects == []
        assert src.message_keys() == ["locations.slw.not_resident"]


def test_player_zero_pays_rent_on_their_own_room():
    # :10105 ``goto10020`` -- the tenant re-enters the rent block and adds months.
    st = _state(ka=5000, ln=2, active=0, players=1, tenancy={2: 0})
    result = run_pure(HANDLERS["slw.pay_rent"], _scripted(3), state=st, rng=None)

    assert result.effects == [MoneyChange(-150), SetTenancy(2), RentAccrue(3)]
    assert game.tenant(result.state, 2) == 0


# --------------------------------------------------------------------------- #
# Strings: the classic theme's slw strings load verbatim                      #
# --------------------------------------------------------------------------- #
def test_strings_load_verbatim():
    data = yaml.safe_load(_SLW_STRINGS.read_text(encoding="utf-8"))

    def get(dotted):
        # Support either nested dicts or flat dotted keys.
        if dotted in data:
            return data[dotted]
        node = data
        for part in dotted.split("."):
            node = node[part]
        return node

    assert get("locations.slw.entry_prompt") == "'AH, EIN KUNDE! WAS WUENSCHT DER HERR?'"
    assert get("locations.slw.menu.rent") == (
        "'EINE UNTERKUNFT, ABER ZACK, ZACK! UND  ICH MOECHTE NICHT GESTOERT WERDEN!'"
    )
    assert get("locations.slw.menu.pay_rent") == "'ICH MOECHTE MEINE MIETE BEZAHLEN!'"
    assert get("locations.slw.menu.leave") == "'ICH WUENSCHE NICHTS. SIE VIELLEICHT?'"
    assert get("locations.slw.no_room") == "'nichts mehr frei!'"
    assert get("locations.slw.rent_quote") == "'gut. pro monat kostet das{price}$ miete.'"
    assert "{price}" in get("locations.slw.rent_quote")
    assert get("locations.slw.months_prompt") == "wieviele monate willst du mieten"
    assert get("locations.slw.success") == "'guten tag, der herr!'"
    assert get("locations.slw.not_resident") == "du wohnst hier nicht!"
    assert get("system.not_enough_money") == "du hast zu wenig kies!"

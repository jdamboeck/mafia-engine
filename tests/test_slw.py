"""Tests for the slw (Schlupfwinkel / motel) location — U7, the FIRST real handler.

Proof-first: written and observed RED (module + effects + state field missing)
before implementation.

This exercises the whole spine end-to-end: a generator handler (``slw.rent``)
driven by the real :func:`engine.interactions.run` driver, yielding interactions,
applying effects through the buffer, sitting behind the U6 guard shell. Ports
``mf-prg.bas:10020-10045`` (the shared rent block) verbatim, including:

* the positive-rent path (deduct ``x*p``, set tenancy, accrue months),
* the negative-rent QUIRK (``fnm(1) == -50`` CREDITS the player),
* the ``x<=0`` quiet-cancel path (:10030 — zero effects committed),
* the affordability guard (:10035 — no deduction on insufficient cash),
* the two SHELL guards (room-free for rent, you-are-the-tenant for pay-rent).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.effects import MoneyChange, RentAccrue, SetTenancy
from engine.interactions import PromptInt, ShowMessage, run
from engine.locations import HANDLERS, available_options, load_location
from engine.state import Clock, Config, Gangster, GameState, MapState, Player

_CONFIG_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "game_configs"
    / "mafia_1920s"
)

# Load the mafia_1920s config BY PATH so its "slw.rent" handler registers (the
# config is not a pip-installed package — see engine.config_loader).
load_game_config(_CONFIG_DIR)
_SLW_SHELL = _CONFIG_DIR / "content" / "locations" / "slw.yaml"
_SLW_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "slw.yaml"

# fnm params matching config.yaml: base 50, ln=1 -> -50 (quirk), ln=3/4 -> 0.
_FNM_PARAMS = {"base": 50, "overrides": {1: -50, 3: 0, 4: 0}}


def _state(*, ka=5000, ln=2, active=0, players=1, tenancy=None):
    """A GameState with the fnm params wired in and the active player at tile ``ln``.

    ``ln`` is delivered to the handler via the active player's ``last_location``
    field (the U7 seam; U9 will formalize how the turn system populates it).
    """
    plist = [Player(ka=ka, roster=[Gangster()]) for _ in range(players)]
    plist[active].last_location = ln
    st = GameState(
        players=plist,
        clock=Clock(active_player=active, player_count=players),
        config=Config(formula_params={"fnm": _FNM_PARAMS}),
        map=MapState(tenancy=dict(tenancy or {})),
    )
    return st


def _scripted(*answers):
    """An input_source returning the given answers in order for each prompt."""
    it = iter(answers)

    def source(interaction):
        return next(it)

    return source


def _load_shell():
    return load_location(yaml.safe_load(_SLW_SHELL.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Handler: rent success at a positive-rent tile                               #
# --------------------------------------------------------------------------- #
def test_rent_two_months_positive_tile():
    # ln=2 -> fnm(2) == base == 50; rent 2 months -> cost 100.
    st = _state(ka=5000, ln=2, active=0)
    result = run(HANDLERS["slw.rent"], _scripted(2), state=st, rng=None)

    assert not result.cancelled
    # Three effects: deduct 100, set tenancy[2]=0 (sp), accrue 2 months.
    assert result.committed_effects == [
        MoneyChange(-100),
        SetTenancy(2),
        RentAccrue(2),
    ]
    # Post-commit state reflects all three.
    assert result.state.players[0].ka == 4900
    assert result.state.map.tenancy[2] == 0
    assert result.state.players[0].rented_months == 2


# --------------------------------------------------------------------------- #
# Handler: negative-rent QUIRK — fnm(1) == -50 CREDITS the player             #
# --------------------------------------------------------------------------- #
def test_negative_rent_tile_credits_player():
    # ln=1 -> fnm(1) == -50 (premium unit that PAYS you). Renting 2 months:
    # MoneyChange(-x*p) = -(2*-50) = +100 -> ka INCREASES. Faithful quirk.
    st = _state(ka=5000, ln=1, active=0)
    result = run(HANDLERS["slw.rent"], _scripted(2), state=st, rng=None)

    assert not result.cancelled
    assert MoneyChange(+100) in result.committed_effects  # credit, not debit
    assert result.state.players[0].ka == 5100  # ka went UP by 100
    assert result.state.map.tenancy[1] == 0
    assert result.state.players[0].rented_months == 2


# --------------------------------------------------------------------------- #
# Handler: 0-month quiet cancel (:10030) — ZERO effects committed             #
# --------------------------------------------------------------------------- #
def test_zero_months_cancels_with_no_effects():
    st = _state(ka=5000, ln=2, active=0)
    result = run(HANDLERS["slw.rent"], _scripted(0), state=st, rng=None)

    assert not result.cancelled  # quiet return, not a driver-cancel
    assert result.committed_effects == []  # atomic: nothing applied
    assert result.state.players[0].ka == 5000  # unchanged
    assert 2 not in result.state.map.tenancy  # no tenancy set
    assert result.state.players[0].rented_months == 0


# --------------------------------------------------------------------------- #
# Handler: insufficient cash (:10035) — NO deduction, not-enough-money msg    #
# --------------------------------------------------------------------------- #
def test_insufficient_cash_no_deduction():
    # ka=10, rent 5 months at p=50 -> need 250 > 10.
    st = _state(ka=10, ln=2, active=0)
    seen = []

    def source(interaction):
        seen.append(interaction)
        return 5

    result = run(HANDLERS["slw.rent"], source, state=st, rng=None)

    assert not result.cancelled
    assert result.committed_effects == []  # nothing deducted / no tenancy
    assert result.state.players[0].ka == 10
    assert 2 not in result.state.map.tenancy
    # The not-enough-money message is emitted (auto-acked ShowMessage).
    # (It is not passed to the input_source, so assert via the returned handler run
    # having produced no effects; confirm the key by driving keys the handler emits.)


def test_insufficient_cash_emits_not_enough_money():
    # Assert the interaction sequence directly by capturing yields via a wrapping run.
    st = _state(ka=10, ln=2, active=0)
    emitted: list = []

    def source(interaction):
        # PromptInt for months -> answer 5.
        return 5

    # Re-run the handler manually to observe the ShowMessage it yields on the
    # insufficient path (the driver auto-acks ShowMessage without consulting source).
    from engine.interactions import Ctx

    ctx = Ctx(state=st, rng=None)
    gen = HANDLERS["slw.rent"](ctx)
    interaction = next(gen)  # rent_quote ShowMessage
    emitted.append(interaction)
    interaction = gen.send(None)  # months_prompt PromptInt
    emitted.append(interaction)
    try:
        interaction = gen.send(5)  # insufficient -> ShowMessage(not_enough_money)
        emitted.append(interaction)
        gen.send(None)  # let it return
    except StopIteration:
        pass

    keys = [getattr(i, "key", None) for i in emitted]
    assert "system.not_enough_money" in keys
    assert ctx._buffer == []  # no effects buffered on the insufficient path


# --------------------------------------------------------------------------- #
# Shell guard: room occupied by someone else -> rent option denied            #
# --------------------------------------------------------------------------- #
def test_room_occupied_rent_denied_by_shell():
    loc = _load_shell()
    st = _state(ln=2, active=0, players=1, tenancy={2: 2})  # tile 2 taken by player 2
    avail = available_options(loc, st, ln=2)
    ids = [o.id for o in avail]
    assert "rent" not in ids  # guard uk(ln)=0 fails -> excluded
    rent_opt = next(o for o in loc.options if o.id == "rent")
    assert rent_opt.on_denied == "locations.slw.no_room"


# --------------------------------------------------------------------------- #
# Shell guard: not resident -> pay_rent option denied                         #
# --------------------------------------------------------------------------- #
def test_not_resident_pay_rent_denied_by_shell():
    loc = _load_shell()
    st = _state(ln=2, active=0, players=1, tenancy={2: 99})  # tile taken by someone else
    avail = available_options(loc, st, ln=2)
    ids = [o.id for o in avail]
    assert "pay_rent" not in ids  # guard uk(ln)=sp fails -> excluded
    pay_opt = next(o for o in loc.options if o.id == "pay_rent")
    assert pay_opt.on_denied == "locations.slw.not_resident"


def test_shell_guards_pass_when_appropriate():
    loc = _load_shell()
    # Use active player index 1 so sp=1 (!=0), cleanly separating the "free room"
    # (uk=0) guard from the "you are the tenant" (uk=sp) guard. (For sp=0 the two
    # guards intentionally coincide on a free room — a faithful property of the
    # original guards uk(ln)=0 and uk(ln)=sp.)

    # Free room -> rent available, pay_rent not (player 1 is not the tenant).
    free = _state(ln=2, active=1, players=2, tenancy={})
    ids_free = [o.id for o in available_options(loc, free, ln=2)]
    assert "rent" in ids_free
    assert "pay_rent" not in ids_free
    assert "leave" in ids_free  # guardless

    # You (player 1) are the tenant -> pay_rent available, rent not.
    mine = _state(ln=2, active=1, players=2, tenancy={2: 1})
    ids_mine = [o.id for o in available_options(loc, mine, ln=2)]
    assert "pay_rent" in ids_mine
    assert "rent" not in ids_mine


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
    assert get("locations.slw.rent_quote") == "'gut. pro monat kostet das {price}$ miete.'"
    assert "{price}" in get("locations.slw.rent_quote")
    assert get("locations.slw.months_prompt") == "wieviele monate willst du mieten"
    assert get("locations.slw.success") == "'guten tag, der herr!'"
    assert get("locations.slw.not_resident") == "du wohnst hier nicht!"
    assert get("system.not_enough_money") == "du hast zu wenig kies!"

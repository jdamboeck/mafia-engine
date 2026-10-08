"""U1 — client-loop test harness: drives the REAL ``clients.terminal.play()``
input loop over piped stdin, per
``docs/solutions/developer-experience/driving-terminal-play-loop-over-piped-stdin.md``.

This is the harness later units' client tests import (``run_play`` and the walk
builders below; ``make_walk_script`` lives in ``tests.helpers`` alongside the other
cross-module test callables) — kept small and documented on purpose.

Why a harness at all: the driver-level tests (``test_sph.py`` etc.) call handlers
directly with an explicit ``Rng`` and never touch ``clients/terminal/__main__.py``.
That left the client's own wiring unverified — in particular ``_run_location`` passed
``rng=None`` into ``run_option``, so ANY handler that draws (sph gamble, waf buy's
grenade roll, waf train's stat-gain roll) crashed with ``AttributeError`` the moment a
real human (or this harness) played it through the terminal client. Driver-level tests
stayed green throughout because they never exercised that wire.

Harness conventions (from the solution doc):

1. A leading blank line dismisses the title screen (``play()`` reads one line there
   before the map loop) — ``make_walk_script`` always prepends it.
2. Walks are derived from the LIVE engine via ``try_move`` (BFS over street cells to a
   target door), never a hardcoded key sequence, so they survive map edits.
3. EOF is a quit on both the map loop and any sub-prompt (``_read_key`` docstring).
4. The turn-over prompt eats a key too when it fires; harness callers that expect to
   land INSIDE a location before turn-over should stop their script right after the
   location's own answers (walking never spends an extra step post-entry).
"""

from __future__ import annotations

import io
import re
import sys
from collections import deque
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.terminal import CLEAR, CONFIG_DIR, TerminalInput, main, play
from clients.terminal.palette import ColorSupport, Colors, load_palette
from engine.c64_numbers import c64_divide, c64_float
from engine.config_loader import load_game_config
from engine.locations import load_location
from engine.movement import DOWN, LEFT, RIGHT, UP, load_city
from engine.rng import Rng
from engine.upkeep import run_upkeep
from tests.helpers import (
    MENU_WALK_KEY,
    NEW_GAME_ACKS,
    SOLO,
    deadline,
    make_walk_script,
    next_turn_by_hand,
    with_values,
)
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = CONFIG_DIR

#: The classic palette in truecolor, for tests that build a TerminalInput directly.
_COLORS = Colors(load_palette(CONFIG_DIR), ColorSupport.TRUECOLOR)
#: The map's walking keys, as a player presses them (the client's W/A/S/D binding).
MOVE_KEYS = {"w": UP, "s": DOWN, "a": LEFT, "d": RIGHT}
_MOVE_KEYS = MOVE_KEYS
_DELTA_TO_KEY = {v: k for k, v in _MOVE_KEYS.items()}

# Load the config HERE, at import (#46). Loading it registers this config's handlers
# into engine.locations.HANDLERS as a side effect; without it these tests only pass
# when some earlier module in the same collection run happened to load it first, so a
# filtered run (`pytest -k ...`) failed on an "unregistered handler" that was never
# the real problem. Mirrors tests/test_persistence.py and tests/test_slice_integration.py.
# Its registries are what the saves below load through.
_REGISTRIES = load_game_config(_CONFIG_DIR).registries


# --------------------------------------------------------------------------- #
# Public harness surface (importable by later units' client tests)            #
# --------------------------------------------------------------------------- #


def find_door_cell(city_raw: dict, location_key: str, *, ln: int | None = None) -> int:
    """Return a door cell for ``location_key`` (optionally a specific ``ln`` tile)."""
    for door in city_raw["doors"]:
        if door.get("location") == location_key and (ln is None or door["ln"] == ln):
            return door["cell"]
    raise AssertionError(f"no door for {location_key!r} (ln={ln!r}) in city.yaml")


def walk_keys_to_cell(state, city, target_cell: int) -> list[str]:
    """BFS the shortest street-only path from the active player's ``po`` to a cell.

    Returns the movement-key sequence (one per step). The path only crosses
    walkable-street cells except for the FINAL step, which may land on the target door
    cell itself (a door cell is not itself walkable street, but is a valid BFS
    destination — entering IS the last move). Mirrors the "walk the engine, don't
    hardcode" rule from the solution doc, generalized to an arbitrary destination
    instead of only "the next stepping direction".
    """
    from engine.movement import STREET_CODE

    start = state.players[state.clock.active_player].po
    if start == target_cell:
        return []

    prev: dict[int, tuple[int, int]] = {}  # cell -> (prev_cell, delta)
    visited = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == target_cell:
            break
        for delta in (UP, DOWN, LEFT, RIGHT):
            nxt = cur + delta
            if nxt < 0 or nxt > 999 or nxt in visited:
                continue
            # A cell is enterable if it's walkable street OR the target door itself
            # (we never walk THROUGH a door cell, only ever end on one).
            if nxt == target_cell or city.code(nxt) == STREET_CODE:
                visited.add(nxt)
                prev[nxt] = (cur, delta)
                q.append(nxt)
    if target_cell not in prev and start != target_cell:
        raise AssertionError(f"no walkable path from {start} to {target_cell}")

    # Reconstruct the path backward from target to start.
    path_deltas: list[int] = []
    cur = target_cell
    while cur != start:
        p, d = prev[cur]
        path_deltas.append(d)
        cur = p
    path_deltas.reverse()
    return [_DELTA_TO_KEY[d] for d in path_deltas]


def walk_keys_across_turns(state, city, target_cell: int) -> list[str]:
    """Like :func:`walk_keys_to_cell`, but simulates the ``play()`` map loop faithfully
    enough to cross a turn-over if the walk needs more ``ms`` than one turn provides.

    Only meaningful for a SINGLE-player session (the rotation wraps back to the
    same player and replenishes ``ms``, :func:`tests.helpers.next_turn_by_hand`); a target unreachable within one turn's
    movement budget (e.g. waf's ``ln=1`` grenade-roll door, 39 steps from the default
    start) still needs a real key sequence a piped-stdin script can drive. Returns the
    full key stream INCLUDING the turn-over "press any key..." acknowledgment (any
    non-quit key) AND the U3 turn-start upkeep screen's own "press any key..." ack that
    immediately follows it (KTD-3: upkeep runs right after the rotation,
    before the map loop's next render) — wherever ``ms`` would hit 0 mid-walk, the real
    ``play()`` loop emits BOTH prompts in sequence and reads one key for each. On a
    round wrap (the new active player is 0 -- every turn-over in a single-player
    session) the standings screen sits between them and reads a key of its own (U7).
    The new turn then opens at the turn menu, answered with its walk key
    (:data:`tests.helpers.MENU_WALK_KEY`, ``mf-prg.bas:1030``).
    """
    from engine.movement import try_move

    full_path = walk_keys_to_cell(state, city, target_cell)
    out: list[str] = []
    for key in full_path:
        result = try_move(state, city, _MOVE_KEYS[key])
        state = result.state
        out.append(key)
        if getattr(result.payload, "turn_over", False):
            out.append("x")  # ack the turn-over prompt (any non-quit key advances)
            state, _game_over = next_turn_by_hand(state)
            if state.clock.active_player == 0:
                out.append("x")  # ack the round-standings screen (a wrap, U7/KTD-2)
            out.append("x")  # ack the U3 upkeep screen for the newly-active player
            out.append(MENU_WALK_KEY)  # the turn menu: walk (:1021 "2")
    return out


def load_shell(location_key: str):
    """A location's shell, loaded from the config the client plays."""
    path = _CONFIG_DIR / "content" / "locations" / f"{location_key}.yaml"
    return load_location(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_city_raw(config_dir: Path = _CONFIG_DIR) -> dict:
    return yaml.safe_load(
        (config_dir / "content" / "map" / "city.yaml").read_text(encoding="utf-8")
    )


def new_state(seed: int, players: list[tuple[str, str]] | None = None):
    """A fresh ``GameState`` for walk-planning (mirrors ``play()``'s own construction)."""
    cfg = load_game_config(_CONFIG_DIR)
    return cfg.module.new_game(
        seed=seed,
        end_year=1930,
        score_weight=1.0,
        players=players or [("alcapone", "the outfit")],
    )


def run_play(
    monkeypatch,
    *,
    seed: int,
    stdin_keys: list[str],
    players: list[tuple[str, str]] | None = None,
) -> str:
    """Drive the real ``play()`` loop over a scripted key sequence; return captured stdout.

    ``stdin_keys`` is the RAW per-line body (NOT yet title-prefixed) — callers build it
    with plain movement/menu-answer keys; this wraps it with :func:`make_walk_script`.
    ``players`` forwards straight to ``play()``'s own ``players`` parameter (default:
    :data:`~tests.helpers.SOLO`, the single "alcapone" player) — see
    ``TestTwoPlayerAlternation`` for a scripted multi-player session.
    """
    players = players or SOLO
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", make_walk_script(stdin_keys, players=len(players)))
    monkeypatch.setattr(sys, "stdout", out)
    # KTD-4: supply both setup answers (the pre-U5 hardcoded values) so play() skips
    # the end-year / score-weight prompts and every existing key script stays valid.
    play(seed=seed, players=players, end_year=1930, score_weight=1.0)
    return out.getvalue()


class _Deadline(Exception):
    """Raised by :func:`_run_play_with_deadline`'s SIGALRM handler."""


def _run_play_with_deadline(monkeypatch, *, seconds: float = 20.0, **kwargs) -> str:
    """:func:`run_play`, but a hang (e.g. an EOF re-prompt spin) FAILS instead of
    hanging the suite. SIGALRM interrupts the main thread and unwinds ``play()``;
    pytest-timeout is not a dependency, and a watchdog thread could not stop a
    spinning ``play()`` that shares the monkeypatched ``sys.stdin``/``sys.stdout``."""
    with deadline(
        seconds, f"play() did not return within {seconds}s (EOF spin?)", exc_type=_Deadline
    ):
        return run_play(monkeypatch, **kwargs)


# --------------------------------------------------------------------------- #
# Regression: sph gamble through the real client loop (the rng=None crash)    #
# --------------------------------------------------------------------------- #


class TestSphGambleThroughClient:
    """sph's ``play`` handler draws ``ctx.rng.range(...)`` — U1's root-cause regression."""

    def _walk_and_play_keys(self, seed=42):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = new_state(seed)
        sph_cell = find_door_cell(city_raw, "sph")
        walk = walk_keys_to_cell(state, city, sph_cell)
        # Entering a location with ASCII art shows a splash that eats one extra
        # "ENTER druecken" line (_run_location) BEFORE the menu is shown.
        # Inside sph: "1" play -> game menu choice "0" (poker) -> wager "100" -> back to map.
        return walk + ["", "1", "0", "100"]

    def test_gamble_completes_and_pays_out(self, monkeypatch):
        """Playing sph's gamble through the client must not crash and must show a payout.

        Asserts the RESOLVED outcome text and the exact cash values, not merely that
        a "$" appears somewhere: the status bar renders ``cash {N}$`` on virtually
        every screen, so a "$"-anywhere assertion passes even when the gamble's
        win/loss narration is deleted outright (verified — it kept the whole suite
        green). Seed 42 wins 50$ on a 100$ poker wager: 5500$ -> 5550$.
        """
        keys = self._walk_and_play_keys()
        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        low = output.lower()
        # The win narration itself (themes/classic/strings/sph.yaml: "du hast
        # {amount}$ gewonnen!"). Deleting the ShowMessage now fails this test.
        assert "gewonnen" in low, "sph's win narration never reached the client"
        assert "verloren" not in low, "seed 42 wins; a loss string means the seed drifted"
        # The payout actually landed: cash before and after the wager.
        assert "cash 5500$" in low
        assert "cash 5550$" in low

    def test_same_seed_twice_is_deterministic(self, monkeypatch):
        keys = self._walk_and_play_keys()
        out1 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        out2 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert out1 == out2

    def test_ae6_a_hand_waits_for_one_key_under_its_result(self, monkeypatch):
        """:16040 ``print"{down}du hast"p"$ gewonnen!":...:goto1100``: the result stays on
        screen with the pause line under it -- no clear between -- until ONE key; the
        next key reaches the map, where ``x`` is not a move and says so."""
        keys = [*self._walk_and_play_keys(), "", "x"]
        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        shown = output.index("gewonnen")
        paused = output.index("press any key", shown)
        assert CLEAR not in output[shown:paused], "the result was cleared before the wait"
        cleared = output.find(CLEAR, paused)
        assert cleared != -1, "the map never came back"
        # The wait took the blank line; the map read "x" (one note), not both (two).
        assert output.count("(use W/A/S/D, M, P or Q)", cleared) == 1


# --------------------------------------------------------------------------- #
# waf buy (grenade roll) and waf train through the client — same root cause    #
# --------------------------------------------------------------------------- #


class TestWafBuyThroughClient:
    """``waf.buy`` draws twice: the ``ln==1`` grenade-stock roll + none on a plain buy.

    Cash delta is the ground truth here (not string matching) — the "bought" message
    key resolves to a short confirmation that scrolls past before the map redraws, but
    the committed ``MoneyChange`` is unambiguous proof the handler ran to completion
    without a crash.
    """

    def test_buy_at_ln1_rolls_the_grenade_check_and_completes(self, monkeypatch):
        """ln=1 stock triggers the grenade roll (13011) — the exact draw site rng=None
        used to crash on. Walking there needs > 1 turn's ms budget (39 steps from the
        default start), so this drives a cross-turn walk via the harness."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        cell = find_door_cell(city_raw, "waf", ln=1)
        walk = walk_keys_across_turns(state, city, cell)
        # buy (option 1) -> weapon idx 5 (revolver, in [3,7] stock range, no stat gates,
        # 4000$ affordable against the 5500$ starting cash) -> the spec sheet's key wait
        # (:13525) -> gangster 1 (:1145, 1-based) -> :13080 goto1100's key wait.
        keys = walk + ["", "1", "5", "", "1", ""]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # The buy committed: cash dropped by the revolver's price (5500$ - 4000$).
        assert "cash 1500$" in output

    def test_buy_at_ln2_no_grenade_roll_still_completes(self, monkeypatch):
        """A plain buy with no grenade roll (ln != 1) — same wiring, simpler walk."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = new_state(42)
        cell = find_door_cell(city_raw, "waf", ln=2)
        walk = walk_keys_to_cell(state, city, cell)
        # buy (option 1) -> weapon idx 1 (messer, in [1,5] stock range, 50$, no gates)
        # -> the spec sheet's key wait (:13525) -> gangster 1 -> :13080's key wait.
        keys = walk + ["", "1", "1", "", "1", ""]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert "cash 5450$" in output


class TestWafTrainThroughClient:
    """``waf.train``'s camp path draws THREE ``rng.hit`` calls (13170-13172) — same
    ``rng=None`` root cause, previously untested through the client."""

    def test_train_at_range_completes(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = new_state(42)
        cell = find_door_cell(city_raw, "waf", ln=2)
        walk = walk_keys_to_cell(state, city, cell)
        # train (option 2) -> gangster 1 -> (rank 0 < 5, so no venue choice) -> confirm "y".
        keys = walk + ["", "2", "1", "y"]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # Range training at rank 0 costs range_base (1000$): 5500$ -> 4500$.
        assert "cash 4500$" in output


# --------------------------------------------------------------------------- #
# The location screen: title, options from 1, one key (:3025-3040, #159)      #
# --------------------------------------------------------------------------- #

_ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


class TestLocationScreen:
    """``:3025`` prints the location's title in reverse video, then its description;
    ``:3030`` numbers the options from 1 (``mid$(str$(i),2)" "x$``); ``:3040``
    ``getx$:w=val(x$):ifw<1orw>awgoto3040`` picks on one key of 1 to the option count
    and ignores every other key. slw (ln=2) has three options: rent, pay the rent, leave.
    """

    @staticmethod
    def _slw_walk() -> list[str]:
        city_raw = load_city_raw()
        load_game_config(_CONFIG_DIR)
        cell = find_door_cell(city_raw, "slw", ln=2)
        return walk_keys_across_turns(new_state(42), load_city(city_raw), cell)

    def test_the_screen_shows_the_title_and_numbers_the_options_from_1(self, monkeypatch):
        output, _ = run_play_returning(
            monkeypatch, seed=42, stdin_keys=[*self._slw_walk(), "", "q"], seconds=20
        )
        plain = _ANSI.sub("", output)
        rows = plain.splitlines()
        # :3025 print"{clr}{down}{rvon} "x$": " -- the title, framed, reverse video on.
        title = " SCHLUPFWINKEL (MOTEL, MIETSKASERNE): "
        assert title in rows
        raw_title_row = next(r for r in output.splitlines() if title in _ANSI.sub("", r))
        assert "\033[7m" in raw_title_row, "the title is not in reverse video"
        # :3030 print"{down}"mid$(str$(i),2)" "x$ -- "1 ...", no ")" and no leading space.
        options = [
            "1 'EINE UNTERKUNFT, ABER ZACK, ZACK! UND  ICH MOECHTE NICHT GESTOERT WERDEN!'",
            "2 'ICH MOECHTE MEINE MIETE BEZAHLEN!'",
            "3 'ICH WUENSCHE NICHTS. SIE VIELLEICHT?'",
        ]
        for option in options:
            assert option in rows, f"{option!r} is not a row of the screen"
        # Title, then the description (:3025's second input#1), then the options.
        at = rows.index(title)
        assert rows.index("'AH, EIN KUNDE! WAS WUENSCHT DER HERR?'", at) < rows.index(
            options[0], at
        )
        assert rows.index(options[0], at) < rows.index(options[1], at) < rows.index(options[2])

    def test_ae5_a_key_from_1_to_the_count_picks_every_other_key_is_ignored(self, monkeypatch):
        """``0`` and ``4`` (out of 1..3), a letter and a blank line are ignored and the menu
        waits; ``2`` then runs the second option, pay the rent, which refuses a player who
        rents nothing (:10100 ``du wohnst hier nicht!``)."""
        keys = [*self._slw_walk(), "", "0", "4", "x", "", "2", "", "q"]
        output, _ = run_play_returning(monkeypatch, seed=42, stdin_keys=keys, seconds=20)
        assert output.count("du wohnst hier nicht!") == 1, "2 did not run the second option"
        # 0 is not the first option (rent, :10020's quote), 4 is not the last (leave).
        assert "pro monat kostet das" not in output
        assert "nichts mehr frei" not in output

    def test_q_at_the_menu_ends_the_session(self, monkeypatch):
        """``q`` leaves the game from the menu: the keys after it pick nothing (``1`` would
        rent, :10020's quote; the old 0-based ``1`` was pay the rent, :10100's refusal)."""
        keys = [*self._slw_walk(), "", "q", "1", "", ""]
        output, _ = run_play_returning(monkeypatch, seed=42, stdin_keys=keys, seconds=20)
        assert "SCHLUPFWINKEL (MOTEL, MIETSKASERNE)" in output
        assert "pro monat kostet das" not in output, "the menu went on after q"
        assert "du wohnst hier nicht!" not in output, "the menu went on after q"

    def test_eof_at_the_menu_ends_the_session(self, monkeypatch):
        output, _ = run_play_returning(
            monkeypatch, seed=42, stdin_keys=[*self._slw_walk(), ""], seconds=20
        )
        assert "SCHLUPFWINKEL (MOTEL, MIETSKASERNE)" in output


# --------------------------------------------------------------------------- #
# slw rent flow through the client                                            #
# --------------------------------------------------------------------------- #


class TestSlwRentThroughClient:
    def test_rent_deducts_and_sets_tenancy(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        # ln=2 has a positive base rent (50$/month) per config.yaml's fnm overrides
        # (ln=1 is the negative-rent quirk tile, ln=3/4 are rent-free).
        cell = find_door_cell(city_raw, "slw", ln=2)
        walk = walk_keys_across_turns(state, city, cell)
        # rent (option 1) -> 3 months.
        keys = walk + ["", "1", "3"]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # 3 months * 50$/month = 150$ deducted from the 5500$ starting cash.
        assert "cash 5350$" in output

    def test_picking_an_option_clears_the_menu_before_its_handler(self, monkeypatch):
        """:3050 ``print"{clr}"``: the pick clears the location screen; the rent quote
        (:10020) opens a fresh one instead of printing under the menu."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)
        state = new_state(42)
        cell = find_door_cell(city_raw, "slw", ln=2)
        keys = walk_keys_across_turns(state, city, cell) + ["", "1", "3"]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        quote = output.index("pro monat kostet das")
        menu = output.rindex("ICH MOECHTE MEINE MIETE BEZAHLEN", 0, quote)
        assert CLEAR in output[menu:quote], "the handler printed under the location menu"

    @pytest.mark.parametrize(
        ("months", "result"),
        [("3", "guten tag, der herr!"), ("999", "du hast zu wenig kies!")],
        ids=["10045-success", "1125-refusal"],
    )
    def test_a_location_result_waits_for_a_key(self, monkeypatch, months, result):
        """:10045 ``...:goto1100`` and :1125 ``print"{down}du hast zu wenig kies!":goto1100``:
        the result stays on screen until a key, before the map comes back."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)
        state = new_state(42)
        cell = find_door_cell(city_raw, "slw", ln=2)
        keys = walk_keys_across_turns(state, city, cell) + ["", "1", months, ""]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        shown = output.index(result)
        cleared = output.find(CLEAR, shown)
        assert cleared != -1, "the map never came back"
        assert "press any key" in output[shown:cleared], "the result was cleared without a key"

    def test_the_client_adds_no_key_wait_of_its_own_after_an_option(self):
        """#160: ``:1100``'s wait comes only where a handler yields it
        (``Acknowledge(KEY_WAIT_SCREEN)``), path by path as the source reaches it. The
        client keeps no generic rule that pauses when an option ends with a message on
        screen: no unread-message flag, and no key read in the dispatch that sees an
        option end (``OptionDone``)."""
        import inspect

        from clients.terminal import session

        dispatch = inspect.getsource(session.TerminalSession.render)
        assert "_read_key" not in dispatch and "_write_press_any_key" not in dispatch
        assert "_unread" not in inspect.getsource(session)


# --------------------------------------------------------------------------- #
# pub drink (alcohol trade) + tip through the client — U8                     #
# --------------------------------------------------------------------------- #


class TestPubDrinkThroughClient:
    """``pub.drink`` at tile ln=4 draws TWO ``rng.hit`` calls (stock, price) before
    the client can even show the quantity prompt — same rng=None root cause U1 fixed
    for sph/waf, now exercised for pub's buy path."""

    def test_buy_one_barrel_at_ln4_completes(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        cell = find_door_cell(city_raw, "pub", ln=4)
        walk = walk_keys_across_turns(state, city, cell)
        # option 1 = "drink"; buy 1 barrel.
        keys = walk + ["", "1", "1"]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # Seed 42's rolled buy price for this walk is 5$/barrel -- 1 barrel costs 5$.
        assert "cash 5495$" in output

    def test_same_seed_twice_is_deterministic(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        cell = find_door_cell(city_raw, "pub", ln=4)
        walk = walk_keys_across_turns(state, city, cell)
        keys = walk + ["", "1", "1"]

        out1 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        out2 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert out1 == out2


class TestPubTipThroughClient:
    """``pub.tip`` requires rank>=4 (mf-prg.bas:12200), which a fresh rank-1 ``play()``
    session cannot reach without a full progression session -- ``play()`` has no state-
    injection hook. This drives the SAME real protocol one level down: a hand-built
    rank-4 ``GameState`` through ``engine.actions.run_option`` with a genuine
    ``TerminalInput`` reading piped stdin (the identical class/wire ``play()`` uses,
    per ``TestInteractiveCombatThroughTerminalInput``'s precedent for the same need).
    """

    def _state(self, *, rank=4, ka=100000):
        from engine.state import Clock, Config, GameState, Player
        from data.game_configs.mafia_1920s.gangster import Gangster

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    ka=ka,
                    rank=rank,
                    roster=(Gangster(name="alcapone"),),
                    values={"gang_name": "the outfit"},
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(
                formula_params={
                    "rank_divisor": 11.1,
                    "score_mult": 1.0,
                    "pub_tip_price_base": 1000,
                    "pub_tip_price_step": 500,
                    "pub_alcohol_stock_min": 100,
                    "pub_alcohol_stock_max": 299,
                    "pub_alcohol_buy_price_min": 5,
                    "pub_alcohol_buy_price_max": 9,
                    "pub_alcohol_sell_price_min": 10,
                    "pub_alcohol_sell_price_max": 29,
                    "pub_arms_deal_payout_min": 5500,
                    "pub_arms_deal_payout_max": 14999,
                }
            ),
        )

    def test_buy_a_tip_via_the_real_input_loop(self, monkeypatch):
        from engine.actions import run_option
        from engine.rng import Rng
        from engine.strings import Resolver

        state = self._state(rank=4, ka=100000)
        shell = load_shell("pub")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        out = io.StringIO()
        # seed=1: available(0), price roll 2 -> 2000$, tip id roll -> type 1 (no stake
        # sub-flow). "j" confirms the price; the tip's :12231 goto1100 waits for a key.
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("j\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "tip", state, ln=2, input_source=inp, rng=Rng(1))

        assert result.status == "completed"
        assert result.state.players[0].ka == 98000  # 100000 - 2000$ tip price
        assert game.tip_target(result.state.players[0]) == 1
        # The Confirm prompt genuinely reached the real TerminalInput wire.
        assert "ok (j/n)?" in out.getvalue()


class TestPubRecruitThroughClient:
    """``pub.recruit`` requires rank>=5 AND at least one rented apartment slot
    (mf-prg.bas:12100-12104), neither reachable from a fresh rank-1 ``play()``
    session without a full progression session -- same rationale and same pattern
    as ``TestPubTipThroughClient``: a hand-built rank-5, housed ``GameState`` driven
    through the real ``run_option``/``TerminalInput`` wire.
    """

    def _state(self, *, rank=5, ka=100000, housed=True):
        from engine.state import Clock, Config, GameState, Player
        from data.game_configs.mafia_1920s.gangster import Gangster

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    ka=ka,
                    rank=rank,
                    roster=(Gangster(name="alcapone"),),
                    values={"gang_name": "the outfit"},
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(formula_params={}),
            values=game.tenancy_values({1: 0}) if housed else {},
        )

    def test_recruit_one_gangster_via_the_real_input_loop(self, monkeypatch):
        from engine.actions import run_option
        from engine.rng import Rng
        from engine.strings import Resolver

        state = self._state(rank=5, ka=100000, housed=True)
        shell = load_shell("pub")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        out = io.StringIO()
        # seed=15: offer pool rolls offered=1, candidate id 0 ("killer-jack",
        # price 3000$) -- "j" accepts the single offer; :12170 gosub1100 waits for a key.
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("j\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "recruit", state, ln=1, input_source=inp, rng=Rng(15))

        assert result.status == "completed"
        assert result.state.players[0].ka == 97000  # 100000 - 3000$ price
        assert len(result.state.players[0].roster) == 2  # boss + killer-jack
        assert result.state.players[0].roster[1].name == "killer-jack"
        assert result.state.players[0].roster[1].vitality == 5
        assert game.hired_ids(result.state) == (0,)
        # The Confirm prompt genuinely reached the real TerminalInput wire.
        assert "ok (j/n)?" in out.getvalue()


# --------------------------------------------------------------------------- #
# U10 — pub.job (take a job) via the real input loop                          #
# --------------------------------------------------------------------------- #
class TestPubJobThroughClient:
    """``pub.job``'s guard (rank<=3) is satisfied by a FRESH rank-1 ``play()`` session
    -- unlike tip/recruit, no hand-built high-rank state is needed here. Same
    ``run_option``/``TerminalInput`` pattern as the sibling pub tests, one level
    below full ``play()``, since the exact RNG draw order (type/pay rolls) is easier
    to pin against a specific seed this way."""

    def test_accept_a_job_via_the_real_input_loop(self, monkeypatch):
        from engine.actions import run_option
        from engine.rng import Rng
        from engine.strings import Resolver

        state = new_state(1)
        shell = load_shell("pub")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        out = io.StringIO()
        # seed=1: available (nonzero roll), job type 1 (bouncer), pay=2261$.
        # "j" accepts the pay confirm; :12335 ...goto1100 waits for a key.
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("j\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "job", state, ln=2, input_source=inp, rng=Rng(1))

        assert result.status == "completed"
        assert game.job(result.state.players[0]).type == 1
        assert game.job(result.state.players[0]).pending_pay == 2261
        assert result.state.players[0].ms == 0  # force-ended (mf-prg.bas:12335 ms=0)
        # The Confirm prompt genuinely reached the real TerminalInput wire.
        assert "ok (j/n)?" in out.getvalue()


# --------------------------------------------------------------------------- #
# U10 — job.shift takes over an employed player's turn (the U3 seam)          #
# --------------------------------------------------------------------------- #
class TestJobShiftThroughClient:
    """Walks to the pub, accepts a job, then proves the NEXT turn runs the shift
    flow instead of the map/menu -- via the real ``play()`` input loop end to end,
    on the rendered ``job`` header + combat-screen protocol (same wire U7 proved
    for its throwaway combat trigger, now exercised by a REAL one)."""

    def test_employed_turn_shows_job_screen_not_the_map(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = new_state(5)
        pub_cell = find_door_cell(city_raw, "pub", ln=2)
        walk = walk_keys_to_cell(state, city, pub_cell)
        # Splash ack; option 4 (job, after drink/recruit/tip); accept ("j"); :12335's
        # key wait; one more move key forces
        # turn_over immediately (ms already 0 from the accept) -- ack turn_over,
        # ack the round standings (single player: every turn-over wraps, U7), ack the
        # next player's upkeep screen. seed=5's bouncer job then rolls a
        # shift-fight this turn (verified by direct trace), after :25030's key wait;
        # a scripted stdin that
        # runs out mid-fight surrenders via CANCEL (KTD-2), which is enough to
        # prove the "job" screen -- not the map -- is what renders next.
        keys = walk + ["", "4", "j", "", "w", "x", "x", "x", ""]
        output = run_play(monkeypatch, seed=5, stdin_keys=keys)

        # The job-shift screen rendered (its own header), not a second map draw
        # between the two turn_over screens.
        assert "job" in output
        # :25000's header (the player's name, "job als") and :25015's job name reach
        # the player before the shift's narration (:25020).
        opened = output.index(": job als")
        assert output.index("rausschmeisser", opened) < output.index("du wartest", opened)
        assert "deine aktion:" in output  # the combat-screen action prompt (U7 wire)
        # No third "move: W/A/S/D" prompt appears between the two turn-over screens
        # -- the employed turn never reached the map loop at all.
        turn_over_positions = [i for i in range(len(output)) if output.startswith("turn_over", i)]
        assert len(turn_over_positions) >= 2
        between = output[turn_over_positions[0] : turn_over_positions[1]]
        assert "move: W/A/S/D" not in between

    def test_croupier_full_lifecycle_two_shifts_lump_sum_via_client_input(self):
        """The job-lifecycle acceptance case (croupier: two shifts, lump sum paid,
        job cleared) driven via the REAL ``TerminalInput``/``run`` wire -- the
        driver-level equivalent lives in ``tests/test_pub_jobs.py``; this is the
        SAME scenario one level up, on the real terminal input protocol."""
        from engine.interactions import run as run_handler
        from engine.rng import Rng
        from engine.state import Clock, Config, GameState, Player
        from data.game_configs.mafia_1920s.state import Job
        from data.game_configs.mafia_1920s.gangster import Gangster
        from engine.strings import Resolver

        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        params = {"rank_divisor": 11.1, "score_mult": 1.0}
        state = GameState(
            players=(
                Player(
                    name="alcapone",
                    ka=1000,
                    roster=(Gangster(name="alcapone", energie=10, kraft=30, brutalitaet=30),),
                    values=game.values_of(Job(type=2, pending_pay=1200, months_left=2)),
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(formula_params=params),
        )
        from engine.locations import HANDLERS

        # Shift 1: trick 1, seed=1 -> success (bonus 372$, :25126 gosub1100's key
        # wait), months_left 2 -> 1.
        out1 = io.StringIO()
        inp1 = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("1\n\n"),
            stdout=out1,
            weapon_names=[],
        )
        result1 = run_handler(HANDLERS["job.shift"], inp1, state=state, rng=Rng(1))
        assert game.job(result1.state.players[0]) == Job(type=2, pending_pay=1200, months_left=1)
        assert result1.state.players[0].ka == 1372
        assert "welchen trick" in out1.getvalue()

        # Shift 2: trick 1, seed=1 again -> success again, months_left hits 0 ->
        # full wage (1200$) pays out once, job cleared (:25126's and :25560's key waits).
        out2 = io.StringIO()
        inp2 = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("1\n\n\n"),
            stdout=out2,
            weapon_names=[],
        )
        result2 = run_handler(HANDLERS["job.shift"], inp2, state=result1.state, rng=Rng(1))
        assert game.job(result2.state.players[0]) == Job()  # cleared
        assert result2.state.players[0].ka == 1372 + 372 + 1200  # bonus + lump sum


# --------------------------------------------------------------------------- #
# Determinism: same seed twice -> identical transcripts                       #
# --------------------------------------------------------------------------- #


class TestSessionRngDeterminism:
    """The session RNG is seeded once per ``play()`` call — replaying the identical
    script against the identical seed must reproduce the identical transcript,
    including every resolved RNG-dependent outcome (sph payout, waf stat gains)."""

    def test_waf_train_camp_same_seed_twice(self, monkeypatch):
        """Exercises the camp path (3 rng.hit draws) specifically, since it is the
        richest rng consumer in-slice — determinism here is the strongest signal."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = new_state(42)
        cell = find_door_cell(city_raw, "waf", ln=2)
        walk = walk_keys_to_cell(state, city, cell)
        keys = walk + ["", "2", "1", "y"]  # train (option 2)

        out1 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        out2 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert out1 == out2


# --------------------------------------------------------------------------- #
# EOF mid-handler exits cleanly instead of spinning                           #
# --------------------------------------------------------------------------- #


class TestEofMidHandlerExitsCleanly:
    """An exhausted script at ANY prompt must end the session cleanly (R11, #51).

    ``TerminalInput`` distinguishes real EOF (``readline()`` returns ``""``) from a
    blank line (``"\\n"``). A blank line keeps its meaning (cancel at a cancellable
    prompt, re-ask at a non-cancellable one), but real EOF at a non-combat prompt
    raises ``EndOfInput``, which ``play()`` catches and exits on (``bye.``) WITHOUT
    adopting the in-flight handler's result -- so its effects are never committed.
    Before this, EOF at a non-cancellable prompt (sph's wager) read as a blank line
    forever: the driver re-asked, starved again, and spun without end.
    """

    def _sph_keys(self, *answers):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = new_state(42)
        sph_cell = find_door_cell(city_raw, "sph")
        return walk_keys_to_cell(state, city, sph_cell) + list(answers)

    def test_eof_during_sph_wager_prompt_exits_without_committing(self, monkeypatch):
        """Walk in, ack the art splash, pick "play" (location option 1), pick poker
        (game 0) -- then stdin RUNS OUT at the non-cancellable wager prompt."""
        keys = self._sph_keys("", "1", "0")
        output = _run_play_with_deadline(monkeypatch, seed=42, stdin_keys=keys)
        low = output.lower()
        # The run genuinely reached the wager prompt (not the map, not the game menu).
        assert "dein einsatz" in low, "the script never reached sph's wager prompt"
        # "bye." is play()'s clean-exit line (a show-cursor escape follows it).
        assert "bye." in low, "the client did not exit cleanly on EOF"
        assert "gewonnen" not in low and "verloren" not in low, (
            "EOF must abandon the gamble, not resolve it"
        )
        # Cash untouched: the pre-entry balance shows, the seed-42 win balance never does.
        assert "cash 5500$" in low
        assert "cash 5550$" not in low

    def test_eof_at_cancellable_game_choice_prompt_exits_cleanly(self, monkeypatch):
        """Walk in, ack the splash, pick "play" (location option 1) -- then stdin runs
        out at sph's cancellable GAME-choice prompt (before any wager)."""
        keys = self._sph_keys("", "1")
        output = _run_play_with_deadline(monkeypatch, seed=42, stdin_keys=keys)
        low = output.lower()
        assert "bye." in low, "the client did not exit cleanly on EOF"
        assert "dein einsatz" not in low, "EOF at the game choice must not reach the wager"
        assert "gewonnen" not in low and "verloren" not in low, (
            "EOF must abandon the gamble, not resolve it"
        )

    def test_eof_at_map_loop_quits_cleanly(self, monkeypatch):
        """No keys at all after the title dismiss: the map loop's very first
        ``_read_key()`` hits EOF and must quit via ``_is_quit`` rather than spin."""
        output = run_play(monkeypatch, seed=42, stdin_keys=[])
        assert "bye." in output


# --------------------------------------------------------------------------- #
# Walking into a door whose shell does not exist yet: graceful denial         #
# --------------------------------------------------------------------------- #


class TestUnimplementedDoorGracefulDenial:
    """Walking into a door whose location has no shell denies gracefully and returns to
    the map, rather than raising out of ``play()`` (the runner's closed-door screen,
    ``engine/turns.py``, rendered by the client). Every location of this config has a
    shell now (U21 built the last, ``ban``), so the test removes one: ``play()`` loads
    a config whose shells lack ``ban`` (cells 95/437/442/657/865 in city.yaml), and the
    bank's door stays on the map with nothing behind it.
    """

    def test_walking_into_an_unimplemented_door_denies_gracefully(self, monkeypatch):
        import dataclasses

        import clients.terminal.session as session

        real = session.load_game_config

        def without_ban(config_dir):
            cfg = real(config_dir)
            return dataclasses.replace(
                cfg, shells={k: v for k, v in cfg.shells.items() if k != "ban"}
            )

        monkeypatch.setattr(session, "load_game_config", without_ban)
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        ban_cell = find_door_cell(city_raw, "ban")
        walk = walk_keys_across_turns(state, city, ban_cell)
        # No follow-up keys needed: denial is immediate and returns straight to the map.
        output = run_play(monkeypatch, seed=42, stdin_keys=walk)
        # Assert the denial text itself, NOT `or "ban" in output`: the map's status-bar
        # location legend renders "ban" on every map screen, so that disjunct was
        # satisfied regardless of what the guard printed (verified — replacing the
        # whole message with unrelated text kept this test green).
        assert "closed for renovations" in output, (
            "the unimplemented-door guard printed no denial message"
        )

    def test_with_its_shell_the_bank_door_opens_its_menu(self, monkeypatch):
        """The same walk without the removal reaches the bank's menu, not the denial:
        the denial above comes from the removed shell, not the walk."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)
        state = new_state(42)
        walk = walk_keys_across_turns(state, city, find_door_cell(city_raw, "ban"))
        # Splash ack, then leave (option 3 of 3).
        output = run_play(monkeypatch, seed=42, stdin_keys=walk + ["", "3"])
        assert "closed for renovations" not in output
        assert "GUTEN TAG, MEIN HERR!" in output


class TestBleReachableByWalking:
    """ble's one door (cell 371) opens Blueten-Eddie's menu, and leave goes back."""

    def test_ble_reachable_by_walking(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        walk = walk_keys_across_turns(state, city, find_door_cell(city_raw, "ble"))
        # Splash ack, then leave (option 3).
        output = run_play(monkeypatch, seed=42, stdin_keys=walk + ["", "3"])
        assert "closed for renovations" not in output
        assert "WO DRUECKT DER SCHUH?" in output


class TestAutReachableByWalking:
    """aut's first door (cell 147, ``ln=1``) opens the car dealer's menu, and leave goes
    back."""

    def test_aut_reachable_by_walking(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        walk = walk_keys_across_turns(state, city, find_door_cell(city_raw, "aut"))
        # Splash ack, then leave (option 3).
        output = run_play(monkeypatch, seed=42, stdin_keys=walk + ["", "3"])
        assert "closed for renovations" not in output
        assert "FLOTTESTEN SCHLITTEN." in output


class TestPolReachableByWalking:
    """pol's one door (cell 910) opens the police station's menu, and leave goes back."""

    def test_pol_reachable_by_walking(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        walk = walk_keys_across_turns(state, city, find_door_cell(city_raw, "pol"))
        # Splash ack, then leave (option 4).
        output = run_play(monkeypatch, seed=42, stdin_keys=walk + ["", "4"])
        assert "closed for renovations" not in output
        assert "WAS HABEN SIE HIER ZU SUCHEN?" in output


# --------------------------------------------------------------------------- #
# U11 — kdh reachable + a full play-through via the real input loop           #
# --------------------------------------------------------------------------- #
class TestKdhLocationThroughClient:
    """kdh is reachable by walking (the doors at cells 221/753 already existed, U1
    audit finding; U11 lands the shell). This drives a full play-through — borrow,
    repay, buy the shop, deposit capital, collect debts into the ambush fight — one
    ``run_option``/``TerminalInput`` call per step (same one-level-down pattern as
    ``TestPubJobThroughClient``/``TestPubRecruitThroughClient``, chaining the returned
    state across calls), since a hand-built shop-owning state cannot be reached by
    ``play()`` walking alone within one session.
    """

    def test_kdh_reachable_by_walking(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        kdh_cell = find_door_cell(city_raw, "kdh")
        walk = walk_keys_across_turns(state, city, kdh_cell)
        # kdh has no art splash: the blank line is ignored at the menu (:3040), then
        # leave (option 6).
        keys = walk + ["", "6"]
        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert "closed for renovations" not in output

    def _params(self):
        return {
            "rank_divisor": 11.1,
            "score_mult": 1.0,
            "kdh_borrow_min": 0,
            "kdh_borrow_max": 5000,
            "kdh_borrow_grace_months": 6,
            "kdh_buy_price_choices": 11,
            "kdh_buy_price_step": 100,
            "kdh_buy_price_base": 5000,
            "kdh_sell_price_choices": 11,
            "kdh_sell_price_step": 100,
            "kdh_sell_price_base": 4500,
            "kdh_capital_max": 5000,
            "kdh_ambush_roll": 3,
            "kdh_ambush_energie": 35,
            "kdh_ambush_weapon": 6,
            "kdh_ambush_loot_min": 500,
            "kdh_ambush_loot_max": 1499,
            "kdh_ambush_score": 2.0,
            "kdh_income_quiet_roll": 3,
            # A5/Finding 4: the fixed CPU-enemy stats are config data now.
            "enemy_kraft": 30,
            "enemy_brutalitaet": 30,
        }

    def _state(self, **overrides):
        from engine.state import Clock, Config, GameState, Player
        from data.game_configs.mafia_1920s.gangster import Gangster
        from data.game_configs.mafia_1920s.state import Business, Debt

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    ka=overrides.pop("ka", 100000),
                    last_location=1,
                    values=game.values_of(
                        overrides.pop("debt", Debt()),
                        overrides.pop("business", Business()),
                        gang_name="the outfit",
                    ),
                    roster=(
                        Gangster(name="alcapone", energie=50, kraft=50, brutalitaet=50, weapon=8),
                    ),
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(formula_params=self._params()),
        )

    def test_borrow_repay_buy_deposit_and_collect_into_the_ambush_fight(self, monkeypatch):
        from engine.actions import run_option
        from engine.rng import Rng
        from data.game_configs.mafia_1920s.state import Debt
        from engine.strings import Resolver

        shell = load_shell("kdh")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")

        # 1. Borrow 2000$ (seed=1: no rng draw needed, borrow has none).
        state = self._state()
        out = io.StringIO()
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            # the amount, then :15030 goto1100's key wait
            stdin=io.StringIO("2000\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "borrow", state, ln=1, input_source=inp, rng=Rng(1))
        assert result.status == "completed"
        assert game.debt(result.state.players[0]) == Debt(amount=2000, months=6)
        state = result.state

        # 2. Repay in full -> grace counter clears too.
        out = io.StringIO()
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            # the amount, then :15075 goto1100's key wait
            stdin=io.StringIO("2000\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "repay", state, ln=1, input_source=inp, rng=Rng(2))
        assert game.debt(result.state.players[0]) == Debt()
        state = result.state

        # 3. Buy the shop at this tile (seed=3: price rolls 5300$; "j" confirms), then
        # :15120 gosub1100's key wait and :15125 goto15200's capital screen (0 leaves).
        out = io.StringIO()
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("j\n\n0\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "trade", state, ln=1, input_source=inp, rng=Rng(3))
        assert game.business(result.state.players[0]).shop_tile == 1
        assert result.state.players[0].ka == 100000 - 5300
        state = result.state

        # 4. Deposit 1000$ capital.
        out = io.StringIO()
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("1000\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "capital", state, ln=1, input_source=inp, rng=Rng(4))
        assert game.business(result.state.players[0]).shop_capital == 1000
        state = result.state

        # 5. Collect debts -- seed=5 draws range(3)==2 (nonzero -> the KTD-9 2/3
        # ambush fires). Reaching the combat screen (not the "paid on time" message)
        # proves the collect flow drove a real fight through the real input loop.
        out = io.StringIO()
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            # :15312 gosub1100's key wait before the fight, the fight, :30520's wait
            stdin=io.StringIO("\nsurrender\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "collect", state, ln=1, input_source=inp, rng=Rng(5))
        assert result.status == "completed"
        rendered = out.getvalue()
        assert "deine aktion:" in rendered  # the combat-screen action prompt (U7 wire)
        # The fighter panel (:1300-1320's stat line) rendered, i.e. combat ran.
        assert re.search(r"e\d\d k\d\d i\d\d b\d\d", rendered)
        # A surrender loses -- no loot, no state change beyond the fight itself
        # (cash reflects the 5300$ purchase + the 1000$ capital deposit from step 4).
        assert result.state.players[0].ka == 100000 - 5300 - 1000


# --------------------------------------------------------------------------- #
# A scripted two-player session alternates turns through the client           #
# --------------------------------------------------------------------------- #


class TestTwoPlayerAlternation:
    """``play()`` accepts a ``players`` roster (this unit's client knob) so the harness
    can construct multi-player sessions; the turn runner rotates ``clock.active_player``
    through them in order. This drives two players each one step, then confirms the
    SECOND player (not the first) is active after the first's turn winds down."""

    def test_two_players_alternate_active_player(self, monkeypatch):
        players = [("alcapone", "the outfit"), ("moran", "north side")]
        cfg = load_game_config(_CONFIG_DIR)
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = cfg.module.new_game(seed=7, end_year=1930, score_weight=1.0, players=players)
        assert state.clock.active_player == 0

        # Walk player 0 all the way to turn_over (BFS one stepping move at a time,
        # mirroring the solution doc's "ask the engine which direction steps" rule),
        # then ack the turn-over prompt.
        from engine.movement import try_move

        keys: list[str] = []
        for _ in range(60):
            stepped = None
            for key, delta in _MOVE_KEYS.items():
                result = try_move(state, city, delta)
                if getattr(result.payload, "kind", None) == "step":
                    state = result.state
                    keys.append(key)
                    stepped = result
                    break
            if stepped is None:
                raise AssertionError("no stepping move available")
            if getattr(stepped.payload, "turn_over", False):
                break
        assert state.clock.active_player == 0  # still player 0 until the ack below

        keys.append("x")  # ack turn-over -> the rotation reaches player 1
        keys.append("x")  # ack player 1's U3 upkeep screen (KTD-3, right after rotation)
        output = run_play(monkeypatch, seed=7, stdin_keys=keys, players=players)
        assert "moran" in output


# --------------------------------------------------------------------------- #
# U7 — combat playable through the real input_source protocol (piped stdin)   #
# --------------------------------------------------------------------------- #
#
# NOTE (U12): when this block was written no in-slice handler yielded StartCombat.
# Real triggers have landed since -- kdh's collect ambush, the pub job shifts, and
# the debt-default collectors (see TestDebtDefaultThroughClient at the end of this
# file, which walks the headline flow through the real loop). This class is kept
# because it still isolates the protocol wire itself with a throwaway handler, free
# of any one trigger's guards. What IS real and already load-bearing is the
# input_source protocol itself: TerminalInput driven
# by engine.interactions.run() over piped stdin is the exact wire the eventual
# combat trigger will use unchanged (KTD-1/KTD-2) -- this is the same pattern
# tests/test_terminal_client.py already uses to test TerminalInput directly. A
# throwaway handler that yields StartCombat stands in for the not-yet-built
# trigger, exercising the SAME driver/client protocol a real trigger will.


class TestInteractiveCombatThroughTerminalInput:
    def _fight_handler(self, sides, *, cpu_sides=()):
        from dataclasses import replace

        from data.game_configs.mafia_1920s.combat_rules import build_rules, equipper
        from engine.interactions import StartCombat

        # Equip every combatant from this config's own table before the fight starts
        # (amendment A1): a StartCombat's fighters carry their constructed equipment,
        # the engine holds no weapon table to resolve an id against.
        equip = equipper(self._weapon_stats())
        equipped = tuple(
            tuple(replace(f, equipment=equip(f.weapon)) for f in side) for side in sides
        )

        def handler(ctx):
            result = yield StartCombat(
                sides=equipped,
                grid=(),
                rules=build_rules({}),
                cpu_sides=cpu_sides,
            )
            return result.winner

        return handler

    def _weapon_stats(self):
        # Must stay a (ts, tg, range) triple: dropping `range` does not fail loudly —
        # the engine falls back to DEFAULT_RANGE and every weapon silently becomes
        # melee, so a ranged shot in these tests would never reach its target.
        cfg = load_game_config(_CONFIG_DIR)
        weapons = cfg.module.load_weapons(_CONFIG_DIR / cfg.config["entities"]["weapons"])
        return {i: (w["ts"], w["tg"], w["range"]) for i, w in enumerate(weapons)}

    def _weapon_names(self):
        cfg = load_game_config(_CONFIG_DIR)
        weapons = cfg.module.load_weapons(_CONFIG_DIR / cfg.config["entities"]["weapons"])
        return [w["name"] for w in weapons]

    def _client(self, keys, seed=42):
        from engine.rng import Rng
        from engine.strings import Resolver

        out = io.StringIO()
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            stdin=io.StringIO("\n".join(keys) + "\n"),
            stdout=out,
            weapon_names=self._weapon_names(),
        )
        return inp, out, Rng(seed)

    def test_scripted_full_fight_reaches_a_winner_with_losses(self):
        """A hot-seat 1v1 (both sides client-driven, cpu_sides=()) walked entirely by
        WASD/f+aim keys: side 1 (position 100) moves right onto the wall-free grid
        toward side 2 (position 101, adjacent already) and shoots. This is the
        interactive-combat acceptance case for U7: a real fight, driven only by
        piped-stdin keys through TerminalInput, resolves to a winner."""
        from engine.interactions import run
        from engine.state import Fighter

        sides = (
            (
                Fighter(
                    name="hero",
                    weapon=5,
                    vitality=20,
                    attrs={"kraft": 30, "brutalitaet": 30},
                    position=100,
                ),
            ),
            (
                Fighter(
                    name="thug",
                    weapon=0,
                    vitality=1,
                    attrs={"kraft": 10, "brutalitaet": 10},
                    position=101,
                ),
            ),
        )
        handler = self._fight_handler(sides, cpu_sides=())
        # side 1 shoots right (thug is immediately adjacent at 101); thug (1 energy,
        # any positive damage roll) surrenders on its own turn if still standing --
        # but revolver tg=10 against a 1-energy target is a guaranteed kill on a hit.
        # A handful of retries covers a miss (ts=5 -> ~4/5 hit chance): shoot, shoot...
        keys = ["f", "d"] * 6
        inp, out, rng = self._client(keys)
        result = run(handler, inp, state=None, rng=rng)

        assert result.status == "completed"
        assert result.payload.returned in (1, 2)
        transcript = out.getvalue()
        # Per-side losses line rendered from the LAST screen shown before the winning
        # shot (mf-prg.bas:30510-30515's vocabulary, ported via combat.losses_*).
        assert "verluste" in transcript.lower() or "\nw:" in transcript  # the panel's weapon

    def test_grid_renders_exactly_40_columns_no_wide_chars(self):
        """Mirrors tests/test_terminal_integration.py::TestMapDisplayWidth, but for
        the 40x13 COMBAT grid -- a different coordinate space (CLAUDE.md)."""
        import unicodedata

        from clients.terminal.renderers import render_combat_grid

        payload = {
            "grid": [],
            "sides": [
                [{"position": 100, "down": False}],
                [{"position": 101, "down": False}],
            ],
            "active_side": 1,
            "active_fighter": 1,
        }
        buf = io.StringIO()
        render_combat_grid(payload, buf, _COLORS)
        import re

        ansi_re = re.compile(r"\033\[[0-9;]*m")
        lines = buf.getvalue().rstrip("\n").split("\n")
        assert len(lines) == 13
        for i, line in enumerate(lines):
            clean = ansi_re.sub("", line)
            assert len(clean) == 40, f"row {i}: width {len(clean)} != 40 ({clean!r})"
            for ch in clean:
                assert unicodedata.east_asian_width(ch) != "W", f"row {i}: wide char {ch!r}"

    def test_illegal_move_reprompts_without_state_change(self):
        """Moving onto the occupied enemy cell is illegal (mf-prg.bas:30145) -- the
        driver re-prompts the SAME activation. The client must not desync: it reads
        a second key and the fight proceeds from the same (unmoved) position."""
        from engine.interactions import run
        from engine.state import Fighter

        sides = (
            (
                Fighter(
                    name="hero",
                    weapon=0,
                    vitality=20,
                    attrs={"kraft": 30, "brutalitaet": 30},
                    position=100,
                ),
            ),
            (
                Fighter(
                    name="thug",
                    weapon=0,
                    vitality=20,
                    attrs={"kraft": 10, "brutalitaet": 10},
                    position=101,
                ),
            ),
        )
        handler = self._fight_handler(sides, cpu_sides=())
        # 'd' (move right onto the occupied cell 101) is illegal -> re-prompt; then
        # surrender to end the fight deterministically.
        keys = ["d", "surrender"]
        inp, out, rng = self._client(keys)
        result = run(handler, inp, state=None, rng=rng)
        assert result.status == "completed"
        # side 1 surrendered -> side 2 (thug) wins.
        assert result.payload.returned == 2
        assert "das geht nicht" in out.getvalue() or "geht nicht" in out.getvalue().lower()

    def test_surrender_ends_the_fight_from_the_client(self):
        from engine.interactions import run
        from engine.state import Fighter

        sides = (
            (
                Fighter(
                    name="hero",
                    weapon=0,
                    vitality=20,
                    attrs={"kraft": 30, "brutalitaet": 30},
                    position=100,
                ),
            ),
            (
                Fighter(
                    name="thug",
                    weapon=0,
                    vitality=20,
                    attrs={"kraft": 10, "brutalitaet": 10},
                    position=200,
                ),
            ),
        )
        handler = self._fight_handler(sides, cpu_sides=())
        inp, out, rng = self._client(["surrender"])
        result = run(handler, inp, state=None, rng=rng)
        assert result.status == "completed"
        assert result.payload.returned == 2  # side 1 gave up -> side 2 wins

    def test_eof_mid_fight_surrenders_and_exits_cleanly(self):
        """Mirrors U1's EOF regression: an exhausted script at a CombatScreen prompt
        must not hang or crash -- it maps to a surrender (KTD-2), same as CANCEL."""
        from engine.interactions import run
        from engine.state import Fighter

        sides = (
            (
                Fighter(
                    name="hero",
                    weapon=0,
                    vitality=20,
                    attrs={"kraft": 30, "brutalitaet": 30},
                    position=100,
                ),
            ),
            (
                Fighter(
                    name="thug",
                    weapon=0,
                    vitality=20,
                    attrs={"kraft": 10, "brutalitaet": 10},
                    position=200,
                ),
            ),
        )
        handler = self._fight_handler(sides, cpu_sides=())
        inp, out, rng = self._client([])  # no keys at all -> immediate EOF
        result = run(handler, inp, state=None, rng=rng)
        assert result.status == "completed"
        assert result.payload.returned == 2

    def test_determinism_same_seed_and_script_identical_transcript(self):
        from engine.interactions import run
        from engine.state import Fighter

        def _sides():
            return (
                (
                    Fighter(
                        name="hero",
                        weapon=5,
                        vitality=20,
                        attrs={"kraft": 30, "brutalitaet": 30},
                        position=100,
                    ),
                ),
                (
                    Fighter(
                        name="thug",
                        weapon=0,
                        vitality=15,
                        attrs={"kraft": 10, "brutalitaet": 10},
                        position=101,
                    ),
                ),
            )

        keys = ["f", "d"] * 8
        outs = []
        for _ in range(2):
            handler = self._fight_handler(_sides(), cpu_sides=())
            inp, out, rng = self._client(keys, seed=99)
            run(handler, inp, state=None, rng=rng)
            outs.append(out.getvalue())
        assert outs[0] == outs[1]


# --------------------------------------------------------------------------- #
# U13 — whole-session determinism: fixed seed, multi-turn, two players         #
# --------------------------------------------------------------------------- #
class TestWholeSessionDeterminism:
    """A fixed seed + a fixed script must reproduce the WHOLE session byte for byte.

    The existing determinism tests each pin ONE handler's draws (sph's payout, waf's
    three camp rolls, one combat). This pins the session as a unit: several turns, two
    players rotating, upkeep running at every turn start, and a location visited along
    the way -- i.e. every RNG consumer in the slice drawing from the SAME session Rng in
    a fixed order. Per-handler determinism does not imply this: a handler that drew from
    a fresh Rng, or upkeep drawing a variable number of times per turn, would leave the
    individual tests green while the session's draw ORDER silently diverged.

    Byte equality is deliberate (not a state comparison): the transcript is the
    observable output, so it catches divergence in what the player SEES -- narration
    included -- not merely in the final state.
    """

    def _script(self, seed, players):
        """A multi-turn walk: player 0 walks into the pub and trades, then both
        players' turns roll over (upkeep runs for each rotation)."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(seed, players=players)
        cell = find_door_cell(city_raw, "pub", ln=4)
        walk = walk_keys_across_turns(state, city, cell)
        # splash ack, drink (option 1), buy 1 barrel -- then walk the rest of the turn
        # out so the rotation to player 1 (and its upkeep screen) is part of the
        # transcript.
        return walk + ["", "1", "1"] + ["w"] * 12 + ["x", "x"] + ["s"] * 3

    def test_same_seed_and_script_reproduce_the_session_byte_for_byte(self, monkeypatch):
        players = [("alcapone", "the outfit"), ("moran", "north side")]
        keys = self._script(11, players)

        out1 = run_play(monkeypatch, seed=11, stdin_keys=keys, players=players)
        out2 = run_play(monkeypatch, seed=11, stdin_keys=keys, players=players)

        assert out1 == out2
        # The session really was multi-turn and multi-player (otherwise byte-equality
        # would be a vacuous pass over a session that ended on turn 1).
        assert "moran" in out1, "the session never rotated to the second player"
        assert out1.count("turn_over") >= 1

    def test_a_different_seed_diverges(self, monkeypatch):
        """The equality above must be the SEED's doing, not a script that renders the
        same bytes regardless -- otherwise the determinism test proves nothing."""
        players = [("alcapone", "the outfit"), ("moran", "north side")]
        keys = self._script(11, players)

        out_a = run_play(monkeypatch, seed=11, stdin_keys=keys, players=players)
        out_b = run_play(monkeypatch, seed=12, stdin_keys=keys, players=players)
        assert out_a != out_b


# --------------------------------------------------------------------------- #
# U13 — regressions the defining session surfaced / the R9 sweep left unpinned #
# --------------------------------------------------------------------------- #
class TestTurnOverScreenRendersNoRawDataclassRepr:
    """The turn-over summary must render VALUES, not a Python repr (U13 session find).

    ``play()``'s turn-over block interpolates player fields into its summary. ``wanted``
    was a scalar when that f-string was written and later became the structured
    :class:`Wanted` dataclass, so ``f"wanted: {p.wanted}"`` silently began
    printing ``Wanted(jail_months=0, bribe_months=0, x5=False, x6=False)`` at every
    turn boundary -- engine internals (including the two win FLAGS, which are meant to
    be secret) leaking straight onto the player's screen.

    No test covered the turn-over screen's text, so the drift was invisible: the field
    still "rendered", just as a repr. This pins the general contract -- no ``Name(...=``
    dataclass repr anywhere in the transcript -- rather than the one field, so the next
    scalar-to-dataclass migration fails here instead of shipping.
    """

    def test_turn_over_summary_has_no_dataclass_repr(self, monkeypatch):
        import re

        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)
        # Walk until the movement budget runs out -- that IS the turn-over screen.
        cell = find_door_cell(city_raw, "kdh", ln=1)
        walk = walk_keys_across_turns(state, city, cell)
        output = run_play(monkeypatch, seed=42, stdin_keys=walk)

        assert "turn_over" in output, "the turn-over screen never rendered"
        assert "Wanted(" not in output, (
            "the turn-over screen leaked a raw Wanted dataclass repr to the player"
        )
        # The general form: no `Identifier(field=` repr anywhere in what a human sees.
        leaked = re.findall(r"\b[A-Z]\w+\([a-z_]+=", output)
        assert not leaked, f"raw dataclass reprs rendered to the player: {leaked}"


class TestTurnOverScreenRendersCorrectValues:
    """The turn-over summary must show the RIGHT values, not just non-repr ones (#48).

    ``TestTurnOverScreenRendersNoRawDataclassRepr`` (above) pins the class of bug where a
    field renders as a Python repr. It does NOT pin the field being the right one: a
    renamed attribute, a swapped field (``p.ka`` for ``p.po``), or a unit change would
    still render a plausible-looking number and pass that test. This computes the
    expected cash/position/movement/rank/jail values INDEPENDENTLY of ``play()`` --
    by replaying the same upkeep + walk through the bare engine functions
    (``run_upkeep``, ``try_move``) the harness already uses to script the walk -- and
    asserts the rendered screen matches them exactly.
    """

    def test_turn_over_summary_matches_independently_computed_state(self, monkeypatch):
        from engine.movement import try_move
        from engine.rng import Rng

        city_raw = load_city_raw()
        city = load_city(city_raw)
        load_game_config(_CONFIG_DIR)  # registers the turn hooks
        state = new_state(42)

        # Walk until the movement budget runs out -- that IS the turn-over screen.
        cell = find_door_cell(city_raw, "kdh", ln=1)
        walk = walk_keys_across_turns(state, city, cell)
        output = run_play(monkeypatch, seed=42, stdin_keys=walk)
        assert "turn_over" in output, "the turn-over screen never rendered"

        # Independently derive the expected values: run the SAME turn-start upkeep
        # (KTD-3 -- play() runs it before the map loop's first render, with the same
        # session seed) and then replay the SAME walk keys through the bare engine's
        # try_move, stopping at the first turn-over -- exactly what play()'s map loop
        # does internally, but computed here without going through play() at all.
        rng = Rng(42)
        upkept = run_upkeep(state, input_source=lambda i: None, rng=rng).state
        expected_state = upkept
        for key in walk:
            delta = MOVE_KEYS.get(key)
            if delta is None:
                continue
            result = try_move(expected_state, city, delta)
            expected_state = result.state
            if getattr(result.payload, "turn_over", False):
                break
        p = expected_state.players[expected_state.clock.active_player]

        assert f"cash: {p.ka}$" in output
        assert f"position: {p.po}" in output
        assert f"movement: {p.ms}" in output
        assert f"rank: {p.rank}" in output
        assert f"jail: {game.wanted(p).jail_months} months" in output
        # Sanity: pin the concrete numbers too, so a coincidental match between a
        # wrong field and the right one (e.g. ka and po both landing on the same
        # value) can't slip through unnoticed.
        assert p.ka == 5500
        assert p.po == 306
        assert p.ms == 0
        assert p.rank == 1
        assert game.wanted(p).jail_months == 0


class TestHandlerRegistrationIsSelfSufficientPerModule:
    """Every test module that resolves a handler must register them itself (#46).

    The #46 defect: ``HANDLERS`` is a process-global registry populated as a SIDE EFFECT
    of ``load_game_config``. A module that resolves a handler without loading the config
    passed only when some EARLIER module in the same collection run happened to load it
    first -- so the full suite was green while ``pytest -k`` on that module alone failed
    with "unregistered handler", an error pointing nowhere near the real cause.

    The fix (importing ``load_game_config(_CONFIG_DIR)`` at module import) was applied
    but never PINNED: nothing failed if a future module reintroduced the ordering
    dependency. This asserts the invariant directly -- a registry-clearing run of this
    module's own imports still resolves the handlers it uses -- so the leak cannot come
    back silently.
    """

    def test_config_load_populates_the_handler_registry_from_empty(self):
        from engine.locations import HANDLERS

        # The handlers this module's tests resolve by name.
        used = ["pub.job", "job.shift", "pub.recruit", "pub.tip"]
        saved = dict(HANDLERS)
        try:
            HANDLERS.clear()
            assert not HANDLERS, "registry did not clear -- test cannot prove anything"
            # Exactly what this module does at import: loading the config must be
            # SUFFICIENT on its own to register every handler used here.
            load_game_config(_CONFIG_DIR)
            missing = [h for h in used if h not in HANDLERS]
            assert not missing, (
                f"load_game_config did not register {missing}; this module would "
                f"depend on another module having loaded the config first (#46)"
            )
        finally:
            HANDLERS.clear()
            HANDLERS.update(saved)


# --------------------------------------------------------------------------- #
# U12 — the debt-default headline flow through the real client loop           #
# --------------------------------------------------------------------------- #
class TestDebtDefaultThroughClient:
    """The armed closure's headline flow, driven by the real input loop.

    Walks the acceptance case the plan names: borrow at kdh, let the six-month grace
    expire turn by turn, and fight the collectors on the rendered 40x13 grid through
    ``TerminalInput`` over piped stdin -- the same wire a network client would use
    unchanged (KTD-1/KTD-2).

    Uses the one-``run_upkeep``-call-per-turn pattern (chaining the returned state)
    rather than ``play()`` walking, for the same reason ``TestKdhLocationThroughClient``
    does: a debt six months into its grace period cannot be reached by walking within
    one scripted session.
    """

    def _params(self):
        return {
            "rank_divisor": 11.1,
            "score_mult": 1.0,
            "kdh_borrow_min": 0,
            "kdh_borrow_max": 5000,
            "kdh_borrow_grace_months": 6,
            "kdh_capital_max": 5000,
            "kdh_income_quiet_roll": 3,
            "kdh_collectors_count": 5,
            "kdh_collectors_weapon": 3,
            "kdh_collectors_energie": 30,
            # A5/Finding 4: the fixed CPU-enemy stats are config data now.
            "enemy_kraft": 30,
            "enemy_brutalitaet": 30,
        }

    def _state(self, **overrides):
        from engine.state import Clock, Config, GameState, Player
        from data.game_configs.mafia_1920s.gangster import Gangster
        from data.game_configs.mafia_1920s.state import Business, Debt

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    ka=overrides.pop("ka", 20000),
                    last_location=1,
                    values=game.values_of(
                        overrides.pop("debt", Debt()), Business(), gang_name="the outfit"
                    ),
                    roster=(
                        Gangster(name="alcapone", energie=50, kraft=50, brutalitaet=50, weapon=8),
                    ),
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(formula_params=self._params()),
        )

    def _weapon_names(self):
        cfg = load_game_config(_CONFIG_DIR)
        weapons = cfg.module.load_weapons(_CONFIG_DIR / cfg.config["entities"]["weapons"])
        return [w["name"] for w in weapons]

    def _input(self, keys):
        from engine.strings import Resolver

        out = io.StringIO()
        inp = TerminalInput(
            resolver=Resolver.from_config(_CONFIG_DIR, theme="classic"),
            colors=_COLORS,
            stdin=io.StringIO("\n".join(keys) + "\n"),
            stdout=out,
            weapon_names=self._weapon_names(),
        )
        return inp, out

    def test_borrow_then_grace_expires_into_the_collectors_fight(self, monkeypatch):
        """The full F1 acceptance flow: borrow at kdh, six turns of upkeep, then fight.

        Turns 1-5 warn with a descending months-remaining count and consume no combat
        input; turn 6 (kz ticks 1 -> 0) opens the fight on the rendered grid, which the
        scripted keys surrender -- losing it, so the seizure fires.
        """
        from engine.actions import run_option
        from engine.rng import Rng
        from data.game_configs.mafia_1920s.state import Debt
        from engine.strings import Resolver

        shell = load_shell("kdh")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")

        # --- borrow 3000$ at kdh through the real input loop -------------------
        state = self._state(ka=20000)
        out = io.StringIO()
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            # the amount, then :15030 goto1100's key wait
            stdin=io.StringIO("3000\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "borrow", state, ln=1, input_source=inp, rng=Rng(1))
        assert game.debt(result.state.players[0]) == Debt(amount=3000, months=6)
        assert result.state.players[0].ka == 23000
        state = result.state

        # --- turns 1-5: the grace period counts DOWN, warning each turn --------
        for expected_months in (5, 4, 3, 2, 1):
            inp, _out = self._input([])  # no combat input consumed while in grace
            state = run_upkeep(state, input_source=inp, rng=Rng(7)).state
            assert game.debt(state.players[0]).months == expected_months
            assert state.players[0].ka == 23000  # nothing seized during grace

        # --- turn 6: kz ticks 1 -> 0, the collectors attack --------------------
        # :4350 gosub1100's key wait, the fight (a mandatory fight is lost, not escaped),
        # then the outcome screen's :30520 key wait.
        inp, out = self._input(["", "surrender", ""])
        result = run_upkeep(state, input_source=inp, rng=Rng(7))
        assert result.status == "completed"

        # The fight really rendered on the 40x13 grid through the client: the combat
        # screen's own action prompt (rendered per activation) proves the client-side
        # combat protocol ran, not just the engine-side branch.
        rendered = out.getvalue()
        assert "deine aktion:" in rendered
        assert "aufgeben" in rendered

        # Lost -> :4365-4370: all cash seized, debt and counter wiped.
        assert result.state.players[0].ka == 0
        assert game.debt(result.state.players[0]) == Debt(amount=0, months=0)

    def test_repaying_mid_grace_stops_the_countdown_and_no_fight_ever_comes(self):
        """Repay at kdh during the grace period -> upkeep never summons collectors."""
        from engine.actions import run_option
        from engine.rng import Rng
        from data.game_configs.mafia_1920s.state import Debt
        from engine.strings import Resolver

        shell = load_shell("kdh")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")

        state = self._state(ka=20000, debt=Debt(amount=3000, months=3))

        # Repay in full -> :15075 clears kr AND kz.
        out = io.StringIO()
        inp = TerminalInput(
            resolver=resolver,
            colors=_COLORS,
            # the amount, then :15075 goto1100's key wait
            stdin=io.StringIO("3000\n\n"),
            stdout=out,
            weapon_names=[],
        )
        result = run_option(shell, "repay", state, ln=1, input_source=inp, rng=Rng(2))
        assert game.debt(result.state.players[0]) == Debt()
        state = result.state
        cash = state.players[0].ka

        # Several further turns: no fight, no seizure, cash untouched. If the debt
        # branch keyed on the bare counter instead of the debt, kz=0 would ambush a
        # fully repaid player here -- run_upkeep would consume combat input it does
        # not have and raise.
        for _ in range(3):
            inp, _out = self._input([])
            state = run_upkeep(state, input_source=inp, rng=Rng(7)).state
            assert state.players[0].ka == cash
            assert game.debt(state.players[0]) == Debt()


# --------------------------------------------------------------------------- #
# U7 (vertical-slice completion) — round standings and the ending              #
# --------------------------------------------------------------------------- #


def burn_turn_keys(
    seed: int,
    players: list[tuple[str, str]] | None = None,
    *,
    end_year: int = 1930,
    turns: int | None = None,
    state=None,
) -> list[str]:
    """The key stream that walks every turn to its turn-over, mirroring ``play()``.

    ``state`` starts the walk from a given position (a saved state resumed with
    ``play(load=...)``) instead of a new game from ``seed``.

    Per the piped-stdin learning, the walk is asked of the engine (the first
    direction that STEPS from the current state, so no move enters a location), not
    hardcoded. For each turn it emits the movement keys, the turn-over ack, then --
    exactly as ``play()`` reads them -- the standings ack on a round wrap, then
    either the result-screen ack (``game_over``) or the next turn's upkeep ack and its
    turn menu's walk key (:data:`tests.helpers.MENU_WALK_KEY`). Stops after ``turns`` turn-overs, or at ``game_over`` when ``turns`` is None.

    Upkeep is not simulated here: for an idle player (no debt, no rent, no job) it
    moves nobody and changes no ``ms`` (checked by
    ``TestRoundStandingsAndEnding.test_idle_upkeep_never_asks_across_the_game``).
    """
    from engine.movement import try_move

    cfg = load_game_config(_CONFIG_DIR)
    city = load_city(load_city_raw())
    if state is None:
        state = cfg.module.new_game(
            seed=seed,
            end_year=end_year,
            score_weight=1.0,
            players=players or [("alcapone", "the outfit")],
        )
    keys: list[str] = []
    done = 0
    while turns is None or done < turns:
        for _ in range(200):
            for key, delta in _MOVE_KEYS.items():
                result = try_move(state, city, delta)
                if getattr(result.payload, "kind", None) == "step":
                    break
            else:
                raise AssertionError("no stepping move available")
            state = result.state
            keys.append(key)
            if getattr(result.payload, "turn_over", False):
                break
        else:
            raise AssertionError("turn never ended within 200 steps")
        keys.append("x")  # ack the turn-over screen
        state, game_over = next_turn_by_hand(state)
        done += 1
        if state.clock.active_player == 0:
            keys.append("x")  # ack the round-standings screen (wrap)
        if game_over:
            keys.append("x")  # ack the result screen
            break
        keys.append("x")  # ack the next turn's upkeep screen
        keys.append(MENU_WALK_KEY)  # the next turn's menu: walk (:1021 "2")
    return keys


def run_play_returning(
    monkeypatch,
    *,
    seed: int,
    stdin_keys: list[str],
    players: list[tuple[str, str]] | None = None,
    end_year: int = 1930,
    seconds: float = 60.0,
):
    """Drive ``play()`` like :func:`run_play` under a SIGALRM deadline, but return
    ``(stdout, play()'s return value)`` so KTD-12's ``(state, rng)`` is observable."""
    players = players or SOLO
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", make_walk_script(stdin_keys, players=len(players)))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(seconds, f"play() did not return within {seconds}s (spin?)", exc_type=_Deadline):
        ret = play(seed=seed, players=players, end_year=end_year, score_weight=1.0)
    return out.getvalue(), ret


class TestRoundStandingsAndEnding:
    """``play()`` shows the standings after every round (``mf-prg.bas:1010``'s
    ``gosub4500``) and ends the game at the end year with the result screen
    (``:40100``), KTD-2; it returns its final ``(state, rng)`` on every exit (KTD-12)."""

    _TWO = [("alcapone", "the outfit"), ("moran", "north side")]

    def test_standings_after_the_second_players_turn_over_with_the_played_date(self, monkeypatch):
        # Two turns = one full round; the script then runs dry on player 0's map.
        keys = burn_turn_keys(7, self._TWO, turns=2)
        output, _ = run_play_returning(monkeypatch, seed=7, stdin_keys=keys, players=self._TWO)

        assert output.count("spielstand") == 1, "standings must show once per round"
        turn_overs = [i for i in range(len(output)) if output.startswith("turn_over", i)]
        assert len(turn_overs) == 2, "the script must reach both players' turn-overs"
        standings_at = output.index("spielstand")
        assert standings_at > turn_overs[1], (
            "the standings appeared before the second player's turn-over (not a wrap)"
        )
        # KTD-2: the round just played (1925-01, month unpadded + 1-based, :4501),
        # not the advanced clock (1925-2).
        assert "spielstand 1925-1\n" in output
        assert "spielstand 1925-2" not in output
        # One row per player, in player order.
        assert output.index("alcapone", standings_at) < output.index("moran", standings_at)

    def test_ae5_game_ends_after_36_rounds_with_the_winner(self, monkeypatch):
        import time

        keys = burn_turn_keys(42, end_year=1928)
        t0 = time.monotonic()
        output, ret = run_play_returning(monkeypatch, seed=42, stdin_keys=keys, end_year=1928)
        elapsed = time.monotonic() - t0

        # One turn-over screen per advance: 36 turns, and no 37th.
        turn_overs = output.count("  turn_over  ")
        assert turn_overs == 36, f"{turn_overs} turn-over screens, expected 36"
        # Turns started = the first turn + one per non-final advance = 1 + 35 = 36.
        # The 36th advance reports game_over, so no 37th turn (and no upkeep) starts.
        upkeeps = output.count("  upkeep  ")
        assert upkeeps == 36, f"{upkeeps} upkeep screens, expected 36"
        winner_at = output.rindex("alcapone hat gewonnen!")
        tail = output[winner_at:]
        for later in ("turn_over", "upkeep", "spielstand", "bye."):
            assert later not in tail, f"{later!r} rendered after the result"
        # :1010 standings on the wrap, then :40100's own gosub4500 = twice for the
        # last round (1927-12), both showing the post-/pre-advance dates per KTD-2.
        assert "spielstand 1927-12\n" in output
        assert "spielstand 1928-1\n" in output
        state, rng = ret
        assert state.clock.year == 1928 and state.clock.month == 0
        assert isinstance(rng, Rng)
        assert elapsed < 30, f"AE5 run took {elapsed:.1f}s"

    def test_eof_at_the_result_screen_exits_cleanly(self, monkeypatch):
        # 1928 is the smallest end year input_ranges allows.
        keys = burn_turn_keys(42, end_year=1928)
        assert keys[-1] == "x"
        output, ret = run_play_returning(monkeypatch, seed=42, stdin_keys=keys[:-1], end_year=1928)
        assert "hat gewonnen!" in output
        state, _rng = ret
        assert state.clock.year == 1928

    def test_eof_at_the_standings_screen_ends_the_session(self, monkeypatch):
        keys = burn_turn_keys(42, turns=1)
        # [..., turn-over ack, standings ack, upkeep ack, walk] -> stop before the
        # standings ack.
        output, ret = run_play_returning(monkeypatch, seed=42, stdin_keys=keys[:-3])
        assert "spielstand 1925-1\n" in output
        assert output.rstrip().endswith("bye.") or "bye." in output[output.index("spielstand") :]
        assert "upkeep" not in output[output.index("spielstand") :], (
            "EOF at the standings must end the session, not start the next turn"
        )
        state, _ = ret
        assert state.clock.month == 1  # the advance happened before the standings

    def test_play_returns_state_and_rng_on_quit(self, monkeypatch):
        from engine.state import GameState

        output, ret = run_play_returning(monkeypatch, seed=42, stdin_keys=["q"])
        assert "bye." in output
        state, rng = ret
        assert isinstance(state, GameState) and isinstance(rng, Rng)
        assert state.clock.year == 1925 and state.clock.month == 0

    def test_play_returns_state_and_rng_on_eof_mid_handler(self, monkeypatch):
        from engine.state import GameState

        city_raw = load_city_raw()
        city = load_city(city_raw)
        walk = walk_keys_to_cell(new_state(42), city, find_door_cell(city_raw, "sph"))
        # splash ack, "play", poker -- then EOF at the wager prompt (EndOfInput path).
        output, ret = run_play_returning(monkeypatch, seed=42, stdin_keys=walk + ["", "1", "0"])
        assert "dein einsatz" in output.lower(), "never reached the wager prompt"
        assert "bye." in output
        state, rng = ret
        assert isinstance(state, GameState) and isinstance(rng, Rng)

    def test_idle_upkeep_never_asks_across_the_game(self):
        """The finding :func:`burn_turn_keys` relies on: over all 36 rounds to 1928, an
        idle player's upkeep yields only its turn banner -- no prompt, no fight -- and
        moves no position/``ms``/cash, so one ack per turn start is the whole script."""
        from engine.interactions import ShowMessage
        from engine.rng import Rng

        cfg = load_game_config(_CONFIG_DIR)
        state = cfg.module.new_game(
            seed=42, end_year=1928, score_weight=1.0, players=[("alcapone", "the outfit")]
        )
        rng = Rng(42)
        keys: list[str] = []

        def source(interaction):
            assert isinstance(interaction, ShowMessage), f"upkeep asked {interaction!r}"
            keys.append(interaction.key)

        game_over = False
        turns = 0
        while not game_over:
            before = state.players[0]
            state = run_upkeep(state, input_source=source, rng=rng).state
            after = state.players[0]
            assert (after.po, after.ms, after.ka) == (before.po, before.ms, before.ka)
            state, game_over = next_turn_by_hand(state)
            turns += 1
        assert turns == 36
        assert keys == ["upkeep.turn_banner"] * 36


# --------------------------------------------------------------------------- #
# U8 — the save key and --load (KTD-5, KTD-6, KTD-7)                           #
# --------------------------------------------------------------------------- #
def _run_session(monkeypatch, lines: list[str], *, seconds: float = 60.0, **play_kwargs):
    """Drive ``play()`` over EXACT stdin lines (no title/upkeep acks prepended -- a
    loaded game shows neither) under a SIGALRM deadline; return ``(stdout, (state, rng))``.
    """
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(seconds, f"play() did not return within {seconds}s (spin?)", exc_type=_Deadline):
        ret = play(**play_kwargs)
    return out.getvalue(), ret


def _new_game_lines(keys: list[str]) -> list[str]:
    """A new game's stdin (one player): the title ack, the house-rules offer, the
    eigenschaften key, the first upkeep ack (:data:`tests.helpers.NEW_GAME_ACKS`) and
    the turn menu's walk key
    (:data:`tests.helpers.MENU_WALK_KEY`), then ``keys`` on the map."""
    return [*NEW_GAME_ACKS, MENU_WALK_KEY] + keys


def _two_steps(seed: int = 42) -> tuple[list[str], list[int]]:
    """Two street STEPS from the start (asked of the engine, never hardcoded) and the
    ``po`` after each -- plain moves, so no key is spent inside a location."""
    from engine.movement import try_move

    city = load_city(load_city_raw())
    state = new_state(seed)
    keys, cells = [], []
    for _ in range(2):
        for key, delta in _MOVE_KEYS.items():
            result = try_move(state, city, delta)
            if getattr(result.payload, "kind", None) == "step":
                state = result.state
                keys.append(key)
                cells.append(state.players[0].po)
                break
    assert len(keys) == 2
    return keys, cells


class TestSaveAndLoad:
    """``p`` on the map saves; ``--load`` resumes that exact game (U8)."""

    _NEW = {"seed": 42, "end_year": 1930, "score_weight": 1.0, "players": SOLO}

    def _k1(self):
        """Walk into sph and play two poker hands (entering leaves ``po`` unchanged, so
        the last walk key re-enters), back on the map at the door. Seed 42's first
        three ``range(2)`` draws are 0, 0, 1: the save holds two draws, and the hand
        after the save draws the 1 -- a load that restarted the stream would draw a 0
        there instead, so the STATE diverges too, not only the log."""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        walk = walk_keys_to_cell(new_state(42), city, find_door_cell(city_raw, "sph"))
        # each hand's result waits for a key (:16035/:16040 ``goto1100``)
        return walk, walk + ["", "1", "0", "100", "", walk[-1], "", "1", "0", "100", ""]

    def test_loaded_game_continues_exactly_like_uninterrupted_play(self, monkeypatch, tmp_path):
        from engine.persistence import load_game

        walk, k1 = self._k1()
        # K2: enter sph again (po is unchanged by an entry, so the last walk key
        # re-enters) and gamble again -- RNG draws AFTER the save point -- then quit.
        k2 = [walk[-1], "", "1", "0", "200", "", "q"]
        save = tmp_path / "a.jsonl"

        out_a, (state_a, rng_a) = _run_session(
            monkeypatch, _new_game_lines(k1 + ["p"] + k2), save=str(save), **self._NEW
        )
        saved = load_game(save, _REGISTRIES)
        out_b, (state_b, rng_b) = _run_session(monkeypatch, k2, load=str(save))

        # The runs really played K2 after the save: three hands in A, one in B.
        wagers_b = out_b.lower().count("dein einsatz")
        assert wagers_b > 0 and out_a.lower().count("dein einsatz") == 3 * wagers_b
        assert len(saved.rng_log) > 0, "no draws before the save: the RNG half is vacuous"
        assert len(rng_a.log) > len(saved.rng_log), "no draws after the save"
        assert state_b == state_a
        assert rng_b.log == rng_a.log
        assert rng_b.log[: len(saved.rng_log)] == saved.rng_log
        # And the snapshot is not simply the end state (K2 changed cash).
        assert saved.state != state_a

    def test_taking_a_job_ends_the_turn_at_once_so_no_save_falls_before_the_shift(
        self, monkeypatch, tmp_path
    ):
        """Accepting a job zeroes ``ms`` (``:12335 ...ms=0:goto1100``); the visit
        returns to ``:2060 ms=ms-5``, which is not above 0, and ``:2065`` returns to
        the turn loop. No map prompt follows, so no save can fall between the job and
        its first shift, which belongs to the NEXT turn start. (Until U8 the port showed
        one more map prompt here, and this test saved on it.)"""
        city_raw = load_city_raw()
        city = load_city(city_raw)
        walk = walk_keys_to_cell(new_state(5), city, find_door_cell(city_raw, "pub", ln=2))
        # Splash ack, option 4 (job), accept; then "p" meets the turn-over screen (any
        # key goes on), and "q" quits at the standings.
        save = tmp_path / "job.jsonl"
        out, (state, _) = _run_session(
            monkeypatch,
            _new_game_lines(walk + ["", "4", "j", "p", "q"]),
            save=str(save),
            seed=5,
            end_year=1930,
            score_weight=1.0,
            players=SOLO,
        )
        assert game.job(state.players[0]).type != 0, "no job taken: vacuous"
        assert state.players[0].ms == -5
        assert "move: W/A/S/D" not in out.split("du hast den job!")[-1]
        assert not save.exists(), "a save fell between the job and its shift"

    def test_load_skips_title_setup_and_upkeep(self, monkeypatch, tmp_path):
        """AE3: a loaded game opens on the map -- no title, no setup, no upkeep banner --
        and, quit at once, returns exactly the state that was saved."""
        from engine.persistence import load_game

        save = tmp_path / "s.jsonl"
        (step, _), (cell, _) = _two_steps()
        new_out, _ = _run_session(
            monkeypatch, _new_game_lines([step, "p", "q"]), save=str(save), **self._NEW
        )
        assert "ist an der reihe" in new_out, "a new game's upkeep banner is the contrast"

        out, (state, _rng) = _run_session(monkeypatch, ["q"], load=str(save))
        first_screen = out.split(CLEAR)[1]
        assert "║" in first_screen and "move: W/A/S/D" in first_screen, "not the map first"
        assert "ist an der reihe" not in out, "the upkeep banner was printed"
        assert "  upkeep  " not in out
        assert "spielende" not in out.lower() and "punktewertigkeit" not in out.lower()
        assert state == load_game(save, _REGISTRIES).state
        assert state.players[0].po == cell  # resumed where the save was taken

    def test_map_turn_save_holds_no_combat_state(self, monkeypatch, tmp_path):
        import json

        from engine.state import CombatState, json_safe

        save = tmp_path / "s.jsonl"
        _run_session(monkeypatch, _new_game_lines(["p", "q"]), save=str(save), **self._NEW)
        header = json.loads(save.read_text(encoding="utf-8").splitlines()[0])
        combat = header["snapshot"]["combat"]
        assert combat == json_safe(CombatState())
        assert combat["sides"] == [[], []] and combat["dir_memory"] == {}

    def test_second_save_overwrites_with_the_later_position(self, monkeypatch, tmp_path):
        from engine.persistence import load_game

        save = tmp_path / "s.jsonl"
        (k1, k2), (c1, c2) = _two_steps()

        class _SnoopingStdin(io.StringIO):
            """Before answering each read, look at the save file as it is on disk."""

            seen: list[int | None] = []

            def readline(self, *args) -> str:
                self.seen.append(
                    load_game(save, _REGISTRIES).state.players[0].po if save.exists() else None
                )
                return super().readline(*args)

        lines = _new_game_lines([k1, "p", k2, "p", "q"])
        stdin = _SnoopingStdin("\n".join(lines) + "\n")
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(60, "play() did not return", exc_type=_Deadline):
            play(save=str(save), **self._NEW)

        # One read per line: title, house rules, eigenschaften, upkeep, walk, k1, p, k2,
        # p, q. The file read before k2 holds the first save; the one read before q
        # holds the second.
        assert c1 != c2
        assert stdin.seen[:7] == [None] * 7, "a save existed before the first p"
        assert stdin.seen[7:] == [c1, c1, c2]
        assert list(tmp_path.iterdir()) == [save]
        assert load_game(save, _REGISTRIES).state.players[0].po == c2
        # Confirmed in the map's note line, with the target path.
        assert str(save) in out.getvalue()

    def test_save_defaults_to_the_loaded_file(self, monkeypatch, tmp_path):
        from engine.persistence import load_game

        save = tmp_path / "s.jsonl"
        (k1, k2), (_c1, c2) = _two_steps()
        _run_session(monkeypatch, _new_game_lines([k1, "p", "q"]), save=str(save), **self._NEW)
        _run_session(monkeypatch, [k2, "p", "q"], load=str(save))
        assert list(tmp_path.iterdir()) == [save]
        assert load_game(save, _REGISTRIES).state.players[0].po == c2

    def test_a_failed_save_is_noted_and_the_game_goes_on(self, monkeypatch, tmp_path):
        """``p`` into a directory that does not exist must not end the session: the
        map's note line says the save failed, and the next key still plays (``q``)."""
        save = tmp_path / "no-such-dir" / "s.jsonl"
        (step, _), (cell, _) = _two_steps()
        out, (state, _rng) = _run_session(
            monkeypatch, _new_game_lines(["p", step, "q"]), save=str(save), **self._NEW
        )
        assert "speichern fehlgeschlagen" in out
        assert "Traceback" not in out
        assert state.players[0].po == cell, "the move after the failed save never played"
        assert "bye." in out
        assert not save.parent.exists()

    def test_save_defaults_to_mafia_save_in_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        _run_session(monkeypatch, _new_game_lines(["p", "q"]), **self._NEW)
        assert (tmp_path / "mafia-save.jsonl").is_file()


class TestLoadFlagConflicts:
    @pytest.mark.parametrize(
        "extra",
        [
            ["--end-year", "1950"],
            ["--seed", "7"],
            ["--score-weight", "1.5"],
            ["--player", "a:b"],
        ],
    )
    def test_load_with_a_setup_flag_is_rejected_before_any_screen(
        self, monkeypatch, capsys, tmp_path, extra
    ):
        from engine.persistence import save_game

        # A loadable save, so a main() that let the clash through would resume it and
        # draw the map -- the empty stdout below then fails the test.
        save = tmp_path / "x.jsonl"
        save_game(save, new_state(42), registries=_REGISTRIES, effect_log=[], rng_log=[], seed=42)
        monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
        with pytest.raises(SystemExit) as exc:
            main(["--load", str(save), *extra])
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "unrecognized arguments" not in captured.err
        assert extra[0] in captured.err and "--load" in captured.err

    def test_load_alone_resumes_the_saved_game(self, monkeypatch, capsys, tmp_path):
        """``--load`` alone resumes the save with ITS seed (no new-game seed is forced
        on it), on the map; ``p`` then writes to ``--save``."""
        from engine.persistence import load_game, save_game

        loaded, written = tmp_path / "x.jsonl", tmp_path / "y.jsonl"
        save_game(loaded, new_state(7), registries=_REGISTRIES, effect_log=[], rng_log=[], seed=7)
        monkeypatch.setattr(sys, "stdin", io.StringIO("p\nq\n"))
        main(["--load", str(loaded), "--save", str(written)])

        out = capsys.readouterr().out
        assert "move: W/A/S/D" in out.split(CLEAR)[1], "the first screen is not the map"
        resumed = load_game(written, _REGISTRIES)
        assert resumed.seed == 7, "the save's own seed was replaced"
        assert resumed.state == new_state(7)
        assert load_game(loaded, _REGISTRIES).state == new_state(7), (
            "the loaded file was overwritten"
        )


# --------------------------------------------------------------------------- #
# The turn phases, entered through a loaded save                               #
# --------------------------------------------------------------------------- #
def _resume_at(monkeypatch, tmp_path, state, phase: str, lines: list[str], *, seed: int = 42):
    """Save ``state`` (RNG ``seed``) to re-enter the turn runner at ``phase`` and resume
    it with ``play(load=...)`` over EXACT stdin ``lines``; returns
    ``(stdout, (state, rng))``.

    A save re-enters the phase its ``clock.turn_phase`` records, so a test can start
    ``play()`` at any point of a turn from a state it built.
    """
    from dataclasses import replace

    from engine.persistence import save_game

    save = tmp_path / f"{phase}.jsonl"
    state = replace(state, clock=replace(state.clock, turn_phase=phase))
    save_game(save, state, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=seed)
    return _run_session(monkeypatch, lines, load=str(save))


class TestTurnPhases:
    def test_a_loaded_save_starts_on_the_map_without_upkeep(self, monkeypatch, tmp_path):
        output, (state, _rng) = _resume_at(monkeypatch, tmp_path, new_state(42), "walking", ["q"])

        screens = output.split(CLEAR)[1:]
        assert len(screens) == 1, "the session showed more than the map"
        assert "║" in screens[0] and "move: W/A/S/D" in screens[0], "the screen is not the map"
        assert "ist an der reihe" not in output, "the upkeep banner was printed"
        assert state == new_state(42)
        assert "bye.\n" in output

    def test_the_round_standings_show_the_round_just_played(self, monkeypatch, tmp_path):
        # One player: the rotation wraps, so the standings show before the upkeep.
        output, (state, _rng) = _resume_at(
            monkeypatch, tmp_path, new_state(42), "next_player", ["x", "x", "q"]
        )
        assert (state.clock.month, state.clock.active_player) == (1, 0), "not a round wrap"
        assert "spielstand 1925-1\n" in output
        assert "spielstand 1925-2" not in output

    # :1013 `gf(sp)=int(gf(sp)*100)/100`, placed after :1011's upkeep and :1012's job
    # dispatch, before the free turn. Player 1 is the one the rotation reaches.
    @staticmethod
    def _second_player(*views, nr=None, **fields):
        from dataclasses import replace

        state = new_state(42, [("alcapone", "the outfit"), ("moran", "north side")])
        moran = state.players[1]
        scalars = {} if nr is None else {"nr": nr}
        values = {**moran.values, **game.values_of(*views, **scalars)}
        players = (state.players[0], replace(moran, values=values, **fields))
        return replace(state, players=players)

    def test_the_free_turn_truncates_the_score_after_upkeep_shows_it(self, monkeypatch, tmp_path):
        # rank 1 with nr 3 pending: :4030's promotion screen prints gf(sp) (:4215).
        state = self._second_player(gf=25.199999, nr=3)
        output, (state, _rng) = _resume_at(
            monkeypatch, tmp_path, state, "next_player", ["x", MENU_WALK_KEY, "q"]
        )

        before_map = output.split("move: W/A/S/D")[0]
        assert "25.199999 p." in before_map, "upkeep did not show the untruncated score"
        assert "move: W/A/S/D" in output, "the free turn never opened"
        assert state.players[1].gf == c64_float(25.19)  # :1013 as the C64 holds it
        assert state.players[0].gf == 0, "a player not on turn was touched"

    def test_upkeep_shows_the_promotion_only_when_the_committed_rank_moves(
        self, monkeypatch, tmp_path
    ):
        """:4030 `ifra(sp)<>nr(sp)thenra(sp)=nr(sp):gosub4200`: the wanted poster shows
        when upkeep commits a new rank, read through the state, and not otherwise."""
        state = self._second_player(gf=25.0, nr=3)
        output, (state, _rng) = _resume_at(
            monkeypatch, tmp_path, state, "next_player", ["x", MENU_WALK_KEY, "q"]
        )
        assert state.players[1].rank == 3
        before_map = output.split("move: W/A/S/D")[0]
        assert "north side\nmoran.\n25 p." in before_map, "no promotion screen"

        state = self._second_player(gf=25.0, nr=1)
        output, _ret = _resume_at(
            monkeypatch, tmp_path, state, "next_player", ["x", MENU_WALK_KEY, "q"]
        )
        before_map = output.split("move: W/A/S/D")[0]
        assert "ist an der reihe" in before_map, "not the upkeep screen"
        assert "north side\nmoran.\n" not in output, "a promotion screen without a promotion"

    def test_upkeep_prints_the_promotion_once_and_keeps_it_on_screen(self, monkeypatch, tmp_path):
        """:4200-4220 print the wanted poster once and wait for a key (#122).

        Upkeep's own messages -- the banner, the poster, a debt warning -- are the
        upkeep screen: printed once under its heading, with no screen clear between
        them and the key that ends it.
        """
        from data.game_configs.mafia_1920s.state import Debt

        state = self._second_player(Debt(amount=1000, months=4), gf=25.0, nr=3)
        output, _ret = _resume_at(
            monkeypatch, tmp_path, state, "next_player", ["x", MENU_WALK_KEY, "q"]
        )
        before_map = output.split("move: W/A/S/D")[0]
        poster = "north side\nmoran.\n25 p."
        assert before_map.count(poster) == 1, "the promotion printed more than once"
        assert before_map.count("ist an der reihe") == 1, "the banner printed more than once"
        from engine.strings import Resolver

        press = Resolver.from_config(_CONFIG_DIR, theme="classic").resolve("client.press_any_key")
        upkeep_screen = before_map[before_map.index("ist an der reihe") :]
        upkeep_screen = upkeep_screen[: upkeep_screen.index(press)]
        assert CLEAR not in upkeep_screen, "a screen clear wiped upkeep's messages"
        assert poster in upkeep_screen
        assert "1000 $ schulden" in upkeep_screen, "the debt warning is not on the upkeep screen"

    def test_an_employed_players_turn_skips_the_truncation(self, monkeypatch, tmp_path):
        from data.game_configs.mafia_1920s.state import Job

        # Croupier, trick 1, then quit at the turn-over. seed=1 is not caught, and with
        # months_left=2 a successful shift only ticks the contract: no score moves.
        state = self._second_player(Job(type=2, pending_pay=1200, months_left=2), gf=25.199999)
        output, (state, _rng) = _resume_at(
            monkeypatch, tmp_path, state, "next_player", ["x", "1", "q"], seed=1
        )

        assert "welchen trick" in output, "the job shift did not run"
        assert "move: W/A/S/D" not in output, "the employed player reached the map"
        assert game.job(state.players[1]).months_left == 1, "the shift did not complete"
        assert state.players[1].gf == 25.199999

    @pytest.mark.parametrize(
        ("setting", "tie", "scores"),
        [
            ("intent", True, (25.2, 25.2)),
            # tests/fixtures/c64_float/tie_capture.txt: the C64's twelve 3*.7 end on
            # 85 49 99 99 9B and truncate to 25.2, its four 9*.7 on ... 99 99 and
            # truncate to 25.19.
            ("faithful", False, (c64_float(25.2), c64_divide(2519, 100))),
        ],
    )
    def test_the_same_score_by_different_steps_ties_at_the_year_end(
        self, monkeypatch, tmp_path, setting, tie, scores
    ):
        """:40105/:40106 compare gf with `>` and `=`: 36 points at x8=0.7.

        Twelve awards of 3 and four awards of 9 (:1160 `gf(sp)=gf(sp)+(x*x8)`) are both
        25.2 in exact decimals, and each player's final-round free turn truncates them
        (:1013), so the year end sees a tie under the intent ``c64_float_score``. The
        C64 sums them in its 5-byte float: the first a hair above 25.2, the second a hair
        below, so :1013 keeps 25.2 and cuts 25.19, and the first player wins outright.
        """
        from dataclasses import replace

        from data.game_configs.mafia_1920s.setup import score_and_rank
        from engine.effects import commit

        state = new_state(42, [("alcapone", "the outfit"), ("moran", "north side")])
        state = replace(
            state,
            clock=replace(state.clock, year=1927, month=11, end_year=1928),
            config=replace(
                state.config,
                formula_params={**state.config.formula_params, "score_mult": 0.7},
                house_rules={**state.config.house_rules, "c64_float_score": setting},
            ),
        )
        params = state.config.formula_params
        awards = [score_and_rank(3, params)] * 12 + [
            replace(score_and_rank(9, params), player=1)
        ] * 4
        state = commit(state, awards).state
        if setting == "faithful":
            assert state.players[0].gf != state.players[1].gf, "no float drift: vacuous"

        keys = burn_turn_keys(42, state=state, end_year=1928)
        # The turn start opens the turn menu first: walk.
        output, (state, _rng) = _resume_at(
            monkeypatch, tmp_path, state, "turn_start", [MENU_WALK_KEY] + keys
        )

        assert ("diesmal haben mehrere die gleichen" in output) is tie, "tie or not"
        assert ("hat gewonnen!" in output) is not tie
        assert (state.players[0].gf, state.players[1].gf) == scores


# --------------------------------------------------------------------------- #
# The classic theme prints numbers as the C64 does (#98)                       #
# --------------------------------------------------------------------------- #
class TestC64NumbersOnScreen:
    """A loaded two-player game played through ``play()``: the screens a player sees
    print numbers with the C64's ``str$``, and ``mid$(str$(..),2)`` where the source
    uses it."""

    _TWO = [("alcapone", "the outfit"), ("moran", "north side")]

    def _play_one_turn(self, monkeypatch, tmp_path, state) -> str:
        """Save ``state``, resume it with ``play(load=...)``, play the active player's
        turn to its turn-over (and past the next player's upkeep), then quit."""
        from engine.persistence import save_game

        save = tmp_path / "c64.jsonl"
        save_game(save, state, registries=_REGISTRIES, effect_log=[], rng_log=[], seed=42)
        keys = burn_turn_keys(42, self._TWO, turns=1, state=state) + ["q"]
        output, _ret = _run_session(monkeypatch, keys, load=str(save))
        return output

    # :4030 shows :4200's poster when ra(sp)<>nr(sp); :4215 prints the score as
    # `mid$(str$(gf(sp)),2)" p."`, so a negative gf loses its minus (str$(-3.5) is
    # "-3.5", mid$ from 2 is "3.5"), and str$ has no ".0" and no leading "0.".
    @pytest.mark.parametrize(("gf", "shown"), [(-3.5, "3.5 p."), (22.0, "22 p."), (0.5, ".5 p.")])
    def test_ae1_rank_screen_prints_the_score_as_mid_str(self, monkeypatch, tmp_path, gf, shown):
        from dataclasses import replace

        state = new_state(42, self._TWO)
        moran = replace(
            state.players[1], gf=gf, rank=1, values={**state.players[1].values, "nr": 3}
        )
        state = replace(state, players=(state.players[0], moran))

        output = self._play_one_turn(monkeypatch, tmp_path, state)

        upkeep_at = output.index("spieler moran\nist an der reihe")
        screen = output[upkeep_at:]
        assert f"moran.\n{shown}\n" in screen, "the rank screen did not show the score"
        assert f"\n{gf!r} p." not in output, "the score printed as Python's str"

    # :4510 `print"{down}"sp$(i);tab(15);ka(i)"$";tab(26);gf(i)` — a plain PRINT of
    # gf(i): str$ keeps the minus and drops ".0" and the leading "0.", and PRINT adds
    # the sign space and the trailing space; tab(26) puts the score's sign at column 26.
    def test_ae2_standings_row_prints_the_score_as_str(self, monkeypatch, tmp_path):
        from dataclasses import replace

        state = new_state(42, self._TWO)
        players = (replace(state.players[0], gf=22.0), replace(state.players[1], gf=-0.9))
        state = replace(state, players=players, clock=replace(state.clock, active_player=1))

        output = self._play_one_turn(monkeypatch, tmp_path, state)

        standings = output[output.index("spielstand 1925-1\n") :]
        rows = {line.split()[0]: line for line in standings.splitlines()[:6] if "$" in line}
        assert re.search(r"^alcapone {7} 5500 \$ {5}22 (?![.\d])", rows["alcapone"]), rows
        assert re.search(r"^moran {10} 7000 \$ {4}-\.9 (?!\d)", rows["moran"]), rows


# --------------------------------------------------------------------------- #
# Who answers: a prompt for another player is announced (KTD-8)                #
# --------------------------------------------------------------------------- #


class TestWhoseTurnLine:
    """A prompt answered by a player other than the active one is announced first.

    The mechanism is proved with the game's upkeep hook swapped, for this session only,
    for one that asks a single question (the swap is made right after ``play()`` loads
    the config, since loading re-registers the config's own handlers). ``pol``'s freed
    player answers the first shipped prompt meant for another player.
    """

    _TWO = [("alcapone", "the outfit"), ("moran", "north side")]
    _WHOSE_TURN = "spieler moran\nist an der reihe..."

    def _play_with_upkeep_asking(self, monkeypatch, player):
        import clients.terminal.session as session_module
        from engine.interactions import PromptInt
        from engine.locations import HANDLERS
        from engine.upkeep import UPKEEP_HANDLER_KEY

        def asking_upkeep(ctx):
            yield PromptInt("locations.slw.months_prompt", min=0, max=9, player=player)
            return None

        real_load = session_module.load_game_config

        def load_then_swap(config_dir):
            cfg = real_load(config_dir)
            monkeypatch.setitem(HANDLERS, UPKEEP_HANDLER_KEY, asking_upkeep)
            return cfg

        monkeypatch.setattr(session_module, "load_game_config", load_then_swap)
        return run_play(monkeypatch, seed=42, stdin_keys=["3"], players=self._TWO)

    def test_a_prompt_for_another_player_names_that_player_first(self, monkeypatch):
        output = self._play_with_upkeep_asking(monkeypatch, player=1)

        prompt_at = output.index("wieviele monate willst du mieten")
        assert self._WHOSE_TURN in output[:prompt_at], "no whose-turn line before the prompt"

    def test_the_freed_player_at_pol_is_named_before_their_thank_you(self, monkeypatch, tmp_path):
        """:21250-21252: alcapone frees moran at pol; moran types the thank-you."""
        from dataclasses import replace

        state = new_state(42, self._TWO)
        alcapone = replace(state.players[0], po=950, ka=10_000, ms=20)  # below the door
        moran = replace(state.players[1], po=911, ka=800)
        state = replace(state, players=(alcapone, moran))
        state = with_values(state, game.Wanted(jail_months=2), idx=1)

        # Up into 910, the splash, "free" (option 3), inmate 1, yes, :21200 gosub1100's
        # key wait, 100 $, quit.
        output, (after, _rng) = _resume_at(
            monkeypatch, tmp_path, state, "walking", ["w", "", "3", "1", "j", "", "100", "q"]
        )

        screen_at = output.index("ihm zum dank (0 - 800):")
        whose_at = output.index(self._WHOSE_TURN, screen_at)
        prompt_at = output.index("?", whose_at)
        assert screen_at < whose_at < prompt_at
        assert self._WHOSE_TURN not in output[:screen_at], "announced before the screen"
        assert game.wanted(after.players[1]).jail_months == 0
        assert after.players[1].ka == 700

    def test_a_prompt_for_the_active_player_names_nobody(self, monkeypatch):
        output = self._play_with_upkeep_asking(monkeypatch, player=None)

        prompt_at = output.index("wieviele monate willst du mieten")
        # The upkeep banner comes after the upkeep's prompt, so nothing before it names a
        # player.
        assert "ist an der reihe" not in output[:prompt_at], "a whose-turn line was printed"

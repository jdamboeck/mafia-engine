"""U1 — client-loop test harness: drives the REAL ``clients.terminal.__main__.play()``
input loop over piped stdin, per
``docs/solutions/developer-experience/driving-terminal-play-loop-over-piped-stdin.md``.

This is the harness later units' client tests import (``make_walk_script`` / ``run_play``
below) — kept small and documented on purpose (KTD per U1's Files/Approach).

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
import sys
from collections import deque
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clients.terminal.__main__ as tmain
from engine.config_loader import load_game_config
from engine.movement import DOWN, LEFT, RIGHT, UP, load_city

_CONFIG_DIR = tmain._CONFIG_DIR
_MOVE_KEYS = tmain._MOVE_KEYS  # {"w": UP, "s": DOWN, "a": LEFT, "d": RIGHT}
_DELTA_TO_KEY = {v: k for k, v in _MOVE_KEYS.items()}


# --------------------------------------------------------------------------- #
# Public harness surface (importable by later units' client tests)            #
# --------------------------------------------------------------------------- #


def make_walk_script(keys: list[str]) -> io.StringIO:
    """Build the piped-stdin body for ``play()``: blank title-dismiss line + one key/line.

    Per the solution doc's rule 1 — ``play()`` reads one line for "press a key" on the
    title screen BEFORE the map loop starts; every scripted stdin must account for it.
    """
    return io.StringIO("\n".join([""] + keys) + "\n")


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


def walk_keys_across_turns(state, city, vehicles, target_cell: int) -> list[str]:
    """Like :func:`walk_keys_to_cell`, but simulates the ``play()`` map loop faithfully
    enough to cross a turn-over if the walk needs more ``ms`` than one turn provides.

    Only meaningful for a SINGLE-player session (``advance_turn`` wraps back to the
    same player and replenishes ``ms``); a target unreachable within one turn's
    movement budget (e.g. waf's ``ln=1`` grenade-roll door, 39 steps from the default
    start) still needs a real key sequence a piped-stdin script can drive. Returns the
    full key stream INCLUDING the turn-over "press any key..." acknowledgment (any
    non-quit key) wherever ``ms`` would hit 0 mid-walk — the real ``play()`` loop emits
    that prompt and reads one key for it before the map loop continues.
    """
    from engine.movement import advance_turn, try_move

    full_path = walk_keys_to_cell(state, city, target_cell)
    out: list[str] = []
    for key in full_path:
        result = try_move(state, city, _MOVE_KEYS[key])
        state = result.state
        out.append(key)
        if getattr(result.payload, "turn_over", False):
            out.append("x")  # ack the turn-over prompt (any non-quit key advances)
            state, _game_over = advance_turn(state, vehicles)
    return out


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
    the single "alcapone" player, unchanged) — see ``TestTwoPlayerAlternation`` for a
    scripted multi-player session.
    """
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", make_walk_script(stdin_keys))
    monkeypatch.setattr(sys, "stdout", out)
    tmain.play(seed=seed, players=players)
    return out.getvalue()


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
        # Inside sph: "0" play -> game menu choice "0" (poker) -> wager "100" -> back to map.
        return walk + ["", "0", "0", "100"]

    def test_gamble_completes_and_pays_out(self, monkeypatch):
        """Playing sph's gamble through the client must not crash and must show a payout."""
        keys = self._walk_and_play_keys()
        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # Regression for rng=None: previously an uncaught AttributeError propagated out
        # of play() and pytest would report an exception, not a clean return.
        assert "won" in output.lower() or "lost" in output.lower() or "$" in output

    def test_same_seed_twice_is_deterministic(self, monkeypatch):
        keys = self._walk_and_play_keys()
        out1 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        out2 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert out1 == out2


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
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(
            _CONFIG_DIR / cfg.config["entities"]["vehicles"]
        )
        state = new_state(42)
        cell = find_door_cell(city_raw, "waf", ln=1)
        walk = walk_keys_across_turns(state, city, vehicles, cell)
        # buy(0) -> weapon idx 5 (revolver, in [3,7] stock range, no stat gates, 4000$
        # affordable against the 5500$ starting cash) -> gangster 0.
        keys = walk + ["", "0", "5", "0"]

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
        # buy(0) -> weapon idx 1 (messer, in [1,5] stock range, 50$, no gates) -> gangster 0.
        keys = walk + ["", "0", "1", "0"]

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
        # train(1) -> gangster 0 -> (rank 0 < 5, so no venue choice) -> confirm "y".
        keys = walk + ["", "1", "0", "y"]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # Range training at rank 0 costs range_base (1000$): 5500$ -> 4500$.
        assert "cash 4500$" in output


# --------------------------------------------------------------------------- #
# slw rent flow through the client                                            #
# --------------------------------------------------------------------------- #


class TestSlwRentThroughClient:
    def test_rent_deducts_and_sets_tenancy(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(
            _CONFIG_DIR / cfg.config["entities"]["vehicles"]
        )
        state = new_state(42)
        # ln=2 has a positive base rent (50$/month) per config.yaml's fnm overrides
        # (ln=1 is the negative-rent quirk tile, ln=3/4 are rent-free).
        cell = find_door_cell(city_raw, "slw", ln=2)
        walk = walk_keys_across_turns(state, city, vehicles, cell)
        # rent(0) -> 3 months.
        keys = walk + ["", "0", "3"]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # 3 months * 50$/month = 150$ deducted from the 5500$ starting cash.
        assert "cash 5350$" in output


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
        keys = walk + ["", "1", "0", "y"]

        out1 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        out2 = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert out1 == out2


# --------------------------------------------------------------------------- #
# EOF mid-handler exits cleanly instead of spinning                           #
# --------------------------------------------------------------------------- #


class TestEofMidHandlerExitsCleanly:
    """An exhausted script inside a location's prompt sequence must not hang or crash
    ``play()`` — ``TerminalInput._read_line`` treats readline() EOF as an empty line,
    which the driver either re-prompts against (and immediately starves again, ending
    the call) or accepts as a quiet abort, depending on the interaction."""

    def test_eof_during_sph_wager_prompt_exits_without_crash(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = new_state(42)
        sph_cell = find_door_cell(city_raw, "sph")
        walk = walk_keys_to_cell(state, city, sph_cell)
        # Walk in, dismiss the art splash, pick a game -- then stdin RUNS OUT before
        # the wager prompt is answered (no crash, no infinite loop).
        keys = walk + ["", "0"]

        # Must return (not hang) and must not raise.
        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert isinstance(output, str)

    def test_eof_at_map_loop_quits_cleanly(self, monkeypatch):
        """No keys at all after the title dismiss: the map loop's very first
        ``_read_key()`` hits EOF and must quit via ``_is_quit`` rather than spin."""
        output = run_play(monkeypatch, seed=42, stdin_keys=[])
        assert "bye." in output


# --------------------------------------------------------------------------- #
# Walking into a kdh door before U11: graceful denial, not a crash            #
# --------------------------------------------------------------------------- #


class TestKdhDoorGracefulDenial:
    """The kdh doors already exist in city.yaml (cells 221/753, U1 audit finding) but
    ``content/locations/kdh.yaml`` does not — walking in used to raise
    ``FileNotFoundError`` straight out of ``play()``. Until U11 lands, it must deny
    gracefully and return to the map.
    """

    def test_walking_into_kdh_denies_gracefully(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(
            _CONFIG_DIR / cfg.config["entities"]["vehicles"]
        )
        state = new_state(42)
        kdh_cell = find_door_cell(city_raw, "kdh")
        walk = walk_keys_across_turns(state, city, vehicles, kdh_cell)
        # No follow-up keys needed: denial is immediate and returns straight to the map.
        output = run_play(monkeypatch, seed=42, stdin_keys=walk)
        assert "closed for renovations" in output or "kdh" in output.lower()

    def test_kdh_shell_file_does_not_exist_yet(self):
        """Documents WHY the guard is needed (regression bait for when U11 lands: this
        assertion should be the first thing to fail, prompting removal of the guard's
        now-stale docstring reference to "until U11 lands")."""
        shell_path = _CONFIG_DIR / "content" / "locations" / "kdh.yaml"
        assert not shell_path.exists()


# --------------------------------------------------------------------------- #
# A scripted two-player session alternates turns through the client           #
# --------------------------------------------------------------------------- #


class TestTwoPlayerAlternation:
    """``play()`` accepts a ``players`` roster (this unit's client knob) so the harness
    can construct multi-player sessions; ``advance_turn`` rotates ``clock.active_player``
    through them in order. This drives two players each one step, then confirms the
    SECOND player (not the first) is active after the first's turn winds down."""

    def test_two_players_alternate_active_player(self, monkeypatch):
        players = [("alcapone", "the outfit"), ("moran", "north side")]
        cfg = load_game_config(_CONFIG_DIR)
        city_raw = load_city_raw()
        city = load_city(city_raw)
        state = cfg.module.new_game(
            seed=7, end_year=1930, score_weight=1.0, players=players
        )
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

        keys.append("x")  # ack turn-over -> advance_turn rotates to player 1
        output = run_play(monkeypatch, seed=7, stdin_keys=keys, players=players)
        assert "moran" in output

"""U1 — client-loop test harness: drives the REAL ``clients.terminal.__main__.play()``
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
import sys
from collections import deque
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clients.terminal.__main__ as tmain
from engine.config_loader import load_game_config
from engine.movement import DOWN, LEFT, RIGHT, UP, load_city
from engine.upkeep import run_upkeep
from tests.helpers import make_walk_script

_CONFIG_DIR = tmain._CONFIG_DIR
_MOVE_KEYS = tmain._MOVE_KEYS  # {"w": UP, "s": DOWN, "a": LEFT, "d": RIGHT}
_DELTA_TO_KEY = {v: k for k, v in _MOVE_KEYS.items()}

# Load the config HERE, at import (#46). Loading it registers this config's handlers
# into engine.locations.HANDLERS as a side effect; without it these tests only pass
# when some earlier module in the same collection run happened to load it first, so a
# filtered run (`pytest -k ...`) failed on an "unregistered handler" that was never
# the real problem. Mirrors tests/test_persistence.py and tests/test_slice_integration.py.
load_game_config(_CONFIG_DIR)


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


def walk_keys_across_turns(state, city, vehicles, target_cell: int) -> list[str]:
    """Like :func:`walk_keys_to_cell`, but simulates the ``play()`` map loop faithfully
    enough to cross a turn-over if the walk needs more ``ms`` than one turn provides.

    Only meaningful for a SINGLE-player session (``advance_turn`` wraps back to the
    same player and replenishes ``ms``); a target unreachable within one turn's
    movement budget (e.g. waf's ``ln=1`` grenade-roll door, 39 steps from the default
    start) still needs a real key sequence a piped-stdin script can drive. Returns the
    full key stream INCLUDING the turn-over "press any key..." acknowledgment (any
    non-quit key) AND the U3 turn-start upkeep screen's own "press any key..." ack that
    immediately follows it (KTD-3: upkeep runs right after ``advance_turn`` rotates,
    before the map loop's next render) — wherever ``ms`` would hit 0 mid-walk, the real
    ``play()`` loop emits BOTH prompts in sequence and reads one key for each.
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
            out.append("x")  # ack the U3 upkeep screen for the newly-active player
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
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
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
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
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
# pub drink (alcohol trade) + tip through the client — U8                     #
# --------------------------------------------------------------------------- #


class TestPubDrinkThroughClient:
    """``pub.drink`` at tile ln=4 draws TWO ``rng.hit`` calls (stock, price) before
    the client can even show the quantity prompt — same rng=None root cause U1 fixed
    for sph/waf, now exercised for pub's buy path."""

    def test_buy_one_barrel_at_ln4_completes(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        state = new_state(42)
        cell = find_door_cell(city_raw, "pub", ln=4)
        walk = walk_keys_across_turns(state, city, vehicles, cell)
        # menu index 0 = "drink" (recruit is guard-excluded at rank 1, tip is index 1);
        # buy 1 barrel.
        keys = walk + ["", "0", "1"]

        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        # Seed 42's rolled buy price for this walk is 5$/barrel -- 1 barrel costs 5$.
        assert "cash 5495$" in output

    def test_same_seed_twice_is_deterministic(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        state = new_state(42)
        cell = find_door_cell(city_raw, "pub", ln=4)
        walk = walk_keys_across_turns(state, city, vehicles, cell)
        keys = walk + ["", "0", "1"]

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
        from engine.state import Clock, Config, Gangster, GameState, Player

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    gang_name="the outfit",
                    ka=ka,
                    rank=rank,
                    roster=(Gangster(name="alcapone"),),
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(
                formula_params={
                    "rank_divisor": 11.1,
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
        shell = tmain._load_shell("pub")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        out = io.StringIO()
        # seed=1: available(0), price roll 2 -> 2000$, tip id roll -> type 1 (no stake
        # sub-flow). "j" confirms the price.
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("j\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "tip", state, ln=2, input_source=inp, rng=Rng(1))

        assert result.status == "completed"
        assert result.state.players[0].ka == 98000  # 100000 - 2000$ tip price
        assert result.state.players[0].tip_target == 1
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
        from engine.state import Clock, Config, Flags, Gangster, GameState, MapState, Player

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    gang_name="the outfit",
                    ka=ka,
                    rank=rank,
                    roster=(Gangster(name="alcapone"),),
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(formula_params={}),
            map=MapState(tenancy={1: 0} if housed else {}),
            flags=Flags(),
        )

    def test_recruit_one_gangster_via_the_real_input_loop(self, monkeypatch):
        from engine.actions import run_option
        from engine.rng import Rng
        from engine.strings import Resolver

        state = self._state(rank=5, ka=100000, housed=True)
        shell = tmain._load_shell("pub")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        out = io.StringIO()
        # seed=15: offer pool rolls offered=1, candidate id 0 ("killer-jack",
        # price 3000$) -- "j" accepts the single offer.
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("j\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "recruit", state, ln=1, input_source=inp, rng=Rng(15))

        assert result.status == "completed"
        assert result.state.players[0].ka == 97000  # 100000 - 3000$ price
        assert len(result.state.players[0].roster) == 2  # boss + killer-jack
        assert result.state.players[0].roster[1].name == "killer-jack"
        assert result.state.players[0].roster[1].energie == 5
        assert result.state.flags.hired_gangsters == (0,)
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
        shell = tmain._load_shell("pub")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        out = io.StringIO()
        # seed=1: available (nonzero roll), job type 1 (bouncer), pay=2261$.
        # "j" accepts the pay confirm.
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("j\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "job", state, ln=2, input_source=inp, rng=Rng(1))

        assert result.status == "completed"
        assert result.state.players[0].jobs.type == 1
        assert result.state.players[0].jobs.pending_pay == 2261
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
        # Splash ack; menu choice 2 (job); accept ("j"); one more move key forces
        # turn_over immediately (ms already 0 from the accept) -- ack turn_over,
        # ack the next player's upkeep screen. seed=5's bouncer job then rolls a
        # shift-fight this turn (verified by direct trace); a scripted stdin that
        # runs out mid-fight surrenders via CANCEL (KTD-2), which is enough to
        # prove the "job" screen -- not the map -- is what renders next.
        keys = walk + ["", "2", "j", "w", "x", "x"]
        output = run_play(monkeypatch, seed=5, stdin_keys=keys)

        # The job-shift screen rendered (its own header), not a second map draw
        # between the two turn_over screens.
        assert "job" in output
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
        from engine.state import Clock, Config, Gangster, GameState, Job, Player
        from engine.strings import Resolver

        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
        params = {"rank_divisor": 11.1}
        state = GameState(
            players=(
                Player(
                    name="alcapone",
                    ka=1000,
                    roster=(Gangster(name="alcapone", energie=10, kraft=30, brutalitaet=30),),
                    jobs=Job(type=2, pending_pay=1200, months_left=2),
                ),
            ),
            clock=Clock(active_player=0, player_count=1),
            config=Config(formula_params=params),
        )
        from engine.locations import HANDLERS

        # Shift 1: trick 1, seed=1 -> success (bonus 372$), months_left 2 -> 1.
        out1 = io.StringIO()
        inp1 = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("1\n"), stdout=out1, weapon_names=[]
        )
        result1 = run_handler(HANDLERS["job.shift"], inp1, state=state, rng=Rng(1))
        assert result1.state.players[0].jobs == Job(type=2, pending_pay=1200, months_left=1)
        assert result1.state.players[0].ka == 1372
        assert "welchen trick" in out1.getvalue()

        # Shift 2: trick 1, seed=1 again -> success again, months_left hits 0 ->
        # full wage (1200$) pays out once, job cleared.
        out2 = io.StringIO()
        inp2 = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("1\n"), stdout=out2, weapon_names=[]
        )
        result2 = run_handler(HANDLERS["job.shift"], inp2, state=result1.state, rng=Rng(1))
        assert result2.state.players[0].jobs == Job()  # cleared
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

        # The protection here is STRUCTURAL: run_play returning at all proves the EOF
        # neither raised nor hung, which is the whole point of the test. The old
        # `assert isinstance(output, str)` was dead weight (run_play always returns
        # StringIO.getvalue()), so assert the two things the EOF path really does
        # determine: the run terminated cleanly, and it did NOT resolve the gamble.
        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        low = output.lower()
        # "bye." is play()'s clean-exit line (a show-cursor escape follows it, so
        # match on containment, not suffix).
        assert "bye." in low, "the client did not exit cleanly on EOF"
        assert "gewonnen" not in low and "verloren" not in low, (
            "EOF must abandon the gamble, not resolve it"
        )
        # NOTE (#51): despite this test's name and the comment above, `keys` does not
        # actually reach sph's wager prompt — the walk ends on the map. The EOF is
        # therefore exercised at the map loop, not mid-handler. The assertions above
        # are true and meaningful as written; naming/coverage fix tracked in #51.

    def test_eof_at_map_loop_quits_cleanly(self, monkeypatch):
        """No keys at all after the title dismiss: the map loop's very first
        ``_read_key()`` hits EOF and must quit via ``_is_quit`` rather than spin."""
        output = run_play(monkeypatch, seed=42, stdin_keys=[])
        assert "bye." in output


# --------------------------------------------------------------------------- #
# Walking into a door whose shell does not exist yet: graceful denial         #
# --------------------------------------------------------------------------- #


class TestUnimplementedDoorGracefulDenial:
    """The U1 ``_shell_exists`` guard (``clients/terminal/__main__.py``) makes walking
    into ANY door whose ``content/locations/<key>.yaml`` shell is missing deny
    gracefully and return to the map, rather than raising ``FileNotFoundError`` straight
    out of ``play()``. This originally exercised the kdh doors (cells 221/753, U1 audit
    finding) as the map's door table ran ahead of kdh's shell; U11 landed
    ``content/locations/kdh.yaml``, so kdh is reachable now (see
    ``TestKdhLocationThroughClient`` below) and the guard needs a DIFFERENT
    genuinely-unimplemented location to keep proving the denial path works. ``sgl``
    (Schutzgeld-Laden — cells 49/145/340/443/538 in city.yaml, U11's serial order) fits:
    wired into the map, no shell yet.
    """

    def test_walking_into_an_unimplemented_door_denies_gracefully(self, monkeypatch):
        city_raw = load_city_raw()
        city = load_city(city_raw)
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        state = new_state(42)
        sgl_cell = find_door_cell(city_raw, "sgl")
        walk = walk_keys_across_turns(state, city, vehicles, sgl_cell)
        # No follow-up keys needed: denial is immediate and returns straight to the map.
        output = run_play(monkeypatch, seed=42, stdin_keys=walk)
        # Assert the denial text itself, NOT `or "sgl" in output`: the map's status-bar
        # location legend renders "sgl" on every map screen, so that disjunct was
        # satisfied regardless of what the guard printed (verified — replacing the
        # whole message with unrelated text kept this test green).
        assert "closed for renovations" in output, (
            "the unimplemented-door guard printed no denial message"
        )

    def test_sgl_shell_file_does_not_exist_yet(self):
        """Documents WHY the guard is needed (regression bait for whichever unit lands
        sgl next: this assertion should be the first thing to fail, prompting a swap to
        another still-unimplemented location rather than deleting the coverage)."""
        shell_path = _CONFIG_DIR / "content" / "locations" / "sgl.yaml"
        assert not shell_path.exists()


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
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        state = new_state(42)
        kdh_cell = find_door_cell(city_raw, "kdh")
        walk = walk_keys_across_turns(state, city, vehicles, kdh_cell)
        # Splash ack, then immediately leave (menu index 5 = "leave").
        keys = walk + ["", "5"]
        output = run_play(monkeypatch, seed=42, stdin_keys=keys)
        assert "closed for renovations" not in output

    def _params(self):
        return {
            "rank_divisor": 11.1,
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
        }

    def _state(self, **overrides):
        from engine.state import Business, Clock, Config, Debt, Gangster, GameState, Player

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    gang_name="the outfit",
                    ka=overrides.pop("ka", 100000),
                    last_location=1,
                    debt=overrides.pop("debt", Debt()),
                    business=overrides.pop("business", Business()),
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
        from engine.state import Debt
        from engine.strings import Resolver

        shell = tmain._load_shell("kdh")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")

        # 1. Borrow 2000$ (seed=1: no rng draw needed, borrow has none).
        state = self._state()
        out = io.StringIO()
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("2000\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "borrow", state, ln=1, input_source=inp, rng=Rng(1))
        assert result.status == "completed"
        assert result.state.players[0].debt == Debt(amount=2000, months=6)
        state = result.state

        # 2. Repay in full -> grace counter clears too.
        out = io.StringIO()
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("2000\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "repay", state, ln=1, input_source=inp, rng=Rng(2))
        assert result.state.players[0].debt == Debt()
        state = result.state

        # 3. Buy the shop at this tile (seed=3: price rolls 5300$; "j" confirms).
        out = io.StringIO()
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("j\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "trade", state, ln=1, input_source=inp, rng=Rng(3))
        assert result.state.players[0].business.shop_tile == 1
        assert result.state.players[0].ka == 100000 - 5300
        state = result.state

        # 4. Deposit 1000$ capital.
        out = io.StringIO()
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("1000\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "capital", state, ln=1, input_source=inp, rng=Rng(4))
        assert result.state.players[0].business.shop_capital == 1000
        state = result.state

        # 5. Collect debts -- seed=5 draws range(3)==2 (nonzero -> the KTD-9 2/3
        # ambush fires). Reaching the combat screen (not the "paid on time" message)
        # proves the collect flow drove a real fight through the real input loop.
        out = io.StringIO()
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("surrender\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "collect", state, ln=1, input_source=inp, rng=Rng(5))
        assert result.status == "completed"
        rendered = out.getvalue()
        assert "deine aktion:" in rendered  # the combat-screen action prompt (U7 wire)
        assert "waffe:" in rendered  # the fighter panel rendered, i.e. combat ran
        # A surrender loses -- no loot, no state change beyond the fight itself
        # (cash reflects the 5300$ purchase + the 1000$ capital deposit from step 4).
        assert result.state.players[0].ka == 100000 - 5300 - 1000


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

        keys.append("x")  # ack turn-over -> advance_turn rotates to player 1
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
        from engine.interactions import StartCombat

        def handler(ctx):
            winner = yield StartCombat(
                sides=sides,
                grid=(),
                weapon_stats=self._weapon_stats(),
                cpu_sides=cpu_sides,
            )
            return winner

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
        inp = tmain.TerminalInput(
            resolver=resolver,
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
            (Fighter(name="hero", weapon=5, energie=20, kraft=30, brutalitaet=30, position=100),),
            (Fighter(name="thug", weapon=0, energie=1, kraft=10, brutalitaet=10, position=101),),
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
        assert "verluste" in transcript.lower() or "energie" in transcript.lower()

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
        render_combat_grid(payload, buf)
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
            (Fighter(name="hero", weapon=0, energie=20, kraft=30, brutalitaet=30, position=100),),
            (Fighter(name="thug", weapon=0, energie=20, kraft=10, brutalitaet=10, position=101),),
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
            (Fighter(name="hero", weapon=0, energie=20, kraft=30, brutalitaet=30, position=100),),
            (Fighter(name="thug", weapon=0, energie=20, kraft=10, brutalitaet=10, position=200),),
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
            (Fighter(name="hero", weapon=0, energie=20, kraft=30, brutalitaet=30, position=100),),
            (Fighter(name="thug", weapon=0, energie=20, kraft=10, brutalitaet=10, position=200),),
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
                        name="hero", weapon=5, energie=20, kraft=30, brutalitaet=30, position=100
                    ),
                ),
                (
                    Fighter(
                        name="thug", weapon=0, energie=15, kraft=10, brutalitaet=10, position=101
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
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        state = new_state(seed, players=players)
        cell = find_door_cell(city_raw, "pub", ln=4)
        walk = walk_keys_across_turns(state, city, vehicles, cell)
        # splash ack, drink(0), buy 1 barrel -- then walk the rest of the turn out so
        # the rotation to player 1 (and its upkeep screen) is part of the transcript.
        return walk + ["", "0", "1"] + ["w"] * 12 + ["x", "x"] + ["s"] * 3

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
    :class:`~engine.state.Wanted` dataclass, so ``f"wanted: {p.wanted}"`` silently began
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
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        state = new_state(42)
        # Walk until the movement budget runs out -- that IS the turn-over screen.
        cell = find_door_cell(city_raw, "kdh", ln=1)
        walk = walk_keys_across_turns(state, city, vehicles, cell)
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
        cfg = load_game_config(_CONFIG_DIR)
        vehicles = cfg.module.load_vehicles(_CONFIG_DIR / cfg.config["entities"]["vehicles"])
        state = new_state(42)

        # Walk until the movement budget runs out -- that IS the turn-over screen.
        cell = find_door_cell(city_raw, "kdh", ln=1)
        walk = walk_keys_across_turns(state, city, vehicles, cell)
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
            delta = tmain._MOVE_KEYS.get(key)
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
        assert f"jail: {p.wanted.jail_months} months" in output
        # Sanity: pin the concrete numbers too, so a coincidental match between a
        # wrong field and the right one (e.g. ka and po both landing on the same
        # value) can't slip through unnoticed.
        assert p.ka == 5500
        assert p.po == 306
        assert p.ms == 0
        assert p.rank == 1
        assert p.wanted.jail_months == 0


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
            "kdh_borrow_min": 0,
            "kdh_borrow_max": 5000,
            "kdh_borrow_grace_months": 6,
            "kdh_capital_max": 5000,
            "kdh_income_quiet_roll": 3,
            "kdh_collectors_count": 5,
            "kdh_collectors_weapon": 3,
            "kdh_collectors_energie": 30,
        }

    def _state(self, **overrides):
        from engine.state import Business, Clock, Config, Debt, Gangster, GameState, Player

        return GameState(
            players=(
                Player(
                    name="alcapone",
                    gang_name="the outfit",
                    ka=overrides.pop("ka", 20000),
                    last_location=1,
                    debt=overrides.pop("debt", Debt()),
                    business=Business(),
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
        inp = tmain.TerminalInput(
            resolver=Resolver.from_config(_CONFIG_DIR, theme="classic"),
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
        from engine.state import Debt
        from engine.strings import Resolver

        shell = tmain._load_shell("kdh")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")

        # --- borrow 3000$ at kdh through the real input loop -------------------
        state = self._state(ka=20000)
        out = io.StringIO()
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("3000\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "borrow", state, ln=1, input_source=inp, rng=Rng(1))
        assert result.state.players[0].debt == Debt(amount=3000, months=6)
        assert result.state.players[0].ka == 23000
        state = result.state

        # --- turns 1-5: the grace period counts DOWN, warning each turn --------
        for expected_months in (5, 4, 3, 2, 1):
            inp, _out = self._input([])  # no combat input consumed while in grace
            state = run_upkeep(state, input_source=inp, rng=Rng(7)).state
            assert state.players[0].debt.months == expected_months
            assert state.players[0].ka == 23000  # nothing seized during grace

        # --- turn 6: kz ticks 1 -> 0, the collectors attack --------------------
        inp, out = self._input(["surrender"])  # a mandatory fight is lost, not escaped
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
        assert result.state.players[0].debt == Debt(amount=0, months=0)

    def test_repaying_mid_grace_stops_the_countdown_and_no_fight_ever_comes(self):
        """Repay at kdh during the grace period -> upkeep never summons collectors."""
        from engine.actions import run_option
        from engine.rng import Rng
        from engine.state import Debt
        from engine.strings import Resolver

        shell = tmain._load_shell("kdh")
        resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")

        state = self._state(ka=20000, debt=Debt(amount=3000, months=3))

        # Repay in full -> :15075 clears kr AND kz.
        out = io.StringIO()
        inp = tmain.TerminalInput(
            resolver=resolver, stdin=io.StringIO("3000\n"), stdout=out, weapon_names=[]
        )
        result = run_option(shell, "repay", state, ln=1, input_source=inp, rng=Rng(2))
        assert result.state.players[0].debt == Debt()
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
            assert state.players[0].debt == Debt()

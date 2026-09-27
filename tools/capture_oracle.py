"""Capture the frozen behavior oracle: ``tests/oracle/<name>.stdin`` and ``<name>.txt``.

Run ONCE, before the engine/config seam refactor begins (full-game plan, U1):

    python tools/capture_oracle.py [--force]

For each session in :data:`tests.test_oracle.SESSIONS` it plans the key stream from
the live engine (walks are found by BFS over the city map, never hardcoded), writes
it as ``<name>.stdin``, drives ``clients.terminal.main()`` over it with the oracle's
pinned environment in a scratch directory, and writes the compacted stdout as
``<name>.txt``. ``tests/test_oracle.py`` then only compares against these files.

Re-capturing after the refactor has started would rewrite the oracle with the
refactored behavior and prove nothing, so existing files are kept unless ``--force``
is given. The oracle is retired in U7.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.config_loader import load_game_config  # noqa: E402
from engine.movement import advance_turn, load_city, try_move  # noqa: E402
from engine.state import tuple_replace  # noqa: E402
from tests.test_client_loop import (  # noqa: E402
    MOVE_KEYS,
    find_door_cell,
    load_city_raw,
    walk_keys_to_cell,
)
from tests.test_oracle import FIGHTS_DIR, ORACLE_DIR, SESSIONS, run_session  # noqa: E402

CONFIG_DIR = ROOT / "data" / "game_configs" / "mafia_1920s"

#: Any non-quit key: acknowledges a "press any key" screen.
ACK = "x"


class Script:
    """The stdin of one ``main()`` phase, planned against a movement-only mirror.

    The mirror tracks what decides which key the client reads next: position,
    movement points, the active player, the calendar. Handlers never move a player,
    so their outcomes do not matter here -- except the two things a caller states
    explicitly: a handler that ends the turn (``ends_turn``), and the prompts a
    fight or shift reads (``upkeep_keys``/``shift``).
    """

    def __init__(self, *, seed: int, players: list[tuple[str, str]], end_year: int) -> None:
        cfg = load_game_config(CONFIG_DIR)
        self.vehicles = cfg.module.load_vehicles(CONFIG_DIR / cfg.config["entities"]["vehicles"])
        self.city_raw = load_city_raw()
        self.city = load_city(self.city_raw)
        self.state = cfg.module.new_game(
            seed=seed, end_year=end_year, score_weight=1.0, players=players
        )
        self.keys: list[str] = ["", ""]  # the title ack, the first upkeep ack
        self.game_over = False
        self.turns = 0
        #: keys the upkeep of turn N reads before its own ack (a collectors' fight)
        self.upkeep_keys: dict[int, list[str]] = {}

    @property
    def active(self) -> int:
        return self.state.clock.active_player

    @property
    def ms(self) -> int:
        return self.state.players[self.active].ms

    def door(self, location: str, ln: int) -> int:
        return find_door_cell(self.city_raw, location, ln=ln)

    def _turn_over(self) -> None:
        self.keys.append(ACK)  # the turn-over screen
        self.state, game_over = advance_turn(self.state, self.vehicles)
        self.turns += 1
        if self.active == 0:
            self.keys.append(ACK)  # the round standings
        if game_over:
            self.keys.append(ACK)  # the year-end result
            self.game_over = True
            return
        self.keys += self.upkeep_keys.pop(self.turns, [])
        self.keys.append(ACK)  # the new turn's upkeep screen

    def move(self, key: str, answers: Sequence[str] = (), *, ends_turn: bool = False) -> str:
        """Press one map key; ``answers`` are read if it enters a location."""
        result = try_move(self.state, self.city, MOVE_KEYS[key])
        self.state = result.state
        self.keys.append(key)
        payload = result.payload
        kind = payload.kind
        if kind == "enter":
            self.keys += list(answers)
            if ends_turn:
                self._zero_ms()
        turn_over = payload.turn_over or (kind == "enter" and ends_turn)
        if turn_over:
            if kind == "enter" and ends_turn and not payload.turn_over:
                self.keys.append("w")  # the map reads one more key before the turn ends
            self._turn_over()
        return kind

    def _zero_ms(self) -> None:
        player = replace(self.state.players[self.active], ms=0)
        self.state = replace(
            self.state, players=tuple_replace(self.state.players, self.active, player)
        )

    def visit(self, cell: int, answers: list[str], *, ends_turn: bool = False) -> bool:
        """Walk to ``cell`` and enter it; ``False`` if the turn ended before arriving.

        Call again on the player's next turn to continue the walk.
        """
        player = self.active
        while self.active == player and not self.game_over:
            path = walk_keys_to_cell(self.state, self.city, cell)
            before = self.turns
            kind = self.move(path[0], answers, ends_turn=ends_turn)
            if kind == "enter":
                return True
            if self.turns != before:
                return False
        return False

    def step_out_turn(self) -> None:
        """Take stepping moves (never entering anything) until the turn ends."""
        start = self.turns
        while self.turns == start:
            for key, delta in MOVE_KEYS.items():
                if getattr(try_move(self.state, self.city, delta).payload, "kind", "") == "step":
                    self.move(key)
                    break
            else:
                raise AssertionError("no stepping move available")

    def reenter_until_turn_over(self, cell: int, answers: list[str]) -> None:
        """Enter the adjacent ``cell`` again and again until the turn ends."""
        start = self.turns
        while self.turns == start:
            path = walk_keys_to_cell(self.state, self.city, cell)
            assert len(path) == 1, f"{cell} is not adjacent to {self.state.players[self.active].po}"
            self.move(path[0], answers)

    def shift(self, keys: list[str]) -> None:
        """A job shift replaces the free turn: its prompts, then the turn ends."""
        self.keys += keys
        self._turn_over()

    def quit(self) -> None:
        self.keys.append("q")

    def text(self) -> str:
        return "\n".join(self.keys) + "\n"


# --------------------------------------------------------------------------- #
# The sessions' key streams                                                    #
# --------------------------------------------------------------------------- #

SPLASH = ""  # the location's art waits for ENTER before its menu
LEAVE = ""  # an empty menu choice leaves without running anything
SPH_POKER_100 = [SPLASH, "0", "0", "100"]


def plan_tour() -> list[str]:
    """Two players; between them they use all five locations."""
    s = Script(seed=42, players=[("a", "x"), ("b", "y")], end_year=1930)
    todo = {
        0: [
            (s.door("sph", 1), SPH_POKER_100),
            (s.door("waf", 2), [SPLASH, "0", "1", "0"]),  # buy a knife for gangster 0
            (s.door("slw", 3), [SPLASH, "0", "3"]),  # rent the flat for 3 months
        ],
        1: [
            (s.door("pub", 2), [SPLASH, "0"]),  # drink: this tile serves nothing
            (s.door("kdh", 2), ["0", "1000"]),  # borrow 1000$ (kdh has no splash)
        ],
    }
    while any(todo.values()):
        queue = todo[s.active]
        if not queue:
            s.step_out_turn()
            continue
        cell, answers = queue[0]
        if s.visit(cell, answers):
            queue.pop(0)
    # One more round, so each player's next upkeep shows the rent and the debt.
    s.step_out_turn()
    s.step_out_turn()
    s.quit()
    return [s.text()]


def plan_debt_default() -> list[str]:
    """Borrow at kdh, let the six-month grace run out, fight the collectors."""
    s = Script(seed=7, players=[("alcapone", "the outfit")], end_year=1930)
    kdh = s.door("kdh", 2)
    while not s.visit(kdh, ["0", "3000"]):  # kdh has no splash
        pass
    borrowed_at = s.turns
    s.upkeep_keys[borrowed_at + 6] = ["d"] * 8 + ["f", "d"] * 3  # then downed
    while s.turns < borrowed_at + 6:
        s.reenter_until_turn_over(kdh, [LEAVE])
    s.quit()
    return [s.text()]


def plan_job_shift() -> list[str]:
    """Take a pub job and work its shifts."""
    s = Script(seed=5, players=[("alcapone", "the outfit")], end_year=1930)
    while not s.visit(s.door("pub", 2), [SPLASH, "2", "j"], ends_turn=True):
        pass
    s.shift(["d"] * 6 + ["f", "d"] * 8 + ["surrender"])
    s.quit()
    return [s.text()]


def plan_save_resume() -> list[str]:
    """Gamble, save, quit; then load the save and play on from it."""
    s = Script(seed=11, players=[("alcapone", "the outfit")], end_year=1930)
    sph = s.door("sph", 1)
    assert s.visit(sph, SPH_POKER_100)
    s.keys.append("p")  # save
    s.quit()
    first = s.text()
    s.keys = []  # a load shows no title and no upkeep
    s.move(walk_keys_to_cell(s.state, s.city, sph)[0], SPH_POKER_100)
    while not s.visit(s.door("waf", 2), [SPLASH, "0", "1", "0"]):
        pass
    s.quit()
    return [first, s.text()]


def plan_year_end() -> list[str]:
    """A 1928 game played to its year-end result: one poker hand a month."""
    s = Script(seed=3, players=[("alcapone", "the outfit")], end_year=1928)
    sph = s.door("sph", 1)
    while not s.game_over:
        start = s.turns
        s.visit(sph, SPH_POKER_100)
        while s.turns == start and not s.game_over:
            s.reenter_until_turn_over(sph, [SPLASH, LEAVE])
    return [s.text()]


#: The fights recorded for the oracle: the fightlab's documented example (the kdh
#: ambush at its file's seed, 42), both sides driven by the AI.
FIGHTS = {"kdh_ambush": CONFIG_DIR / "content" / "scenarios" / "kdh_ambush.yaml"}


def capture_fight(name: str, scenario_path: Path) -> Path:
    """Record ``scenario_path``'s fight and write it as ``fights/<name>.json``.

    Written without indentation (``engine.recording.save`` pretty-prints, which
    quadruples the size); ``engine.recording.load`` reads either.
    """
    from clients.terminal.fightlab import load_scenario
    from engine.fight_loop import AiDriver
    from engine.recording import record_fight, save

    _result, recording = record_fight(load_scenario(scenario_path), {1: AiDriver(), 2: AiDriver()})
    path = FIGHTS_DIR / f"{name}.json"
    FIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    save(recording, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


PLANS = {
    "tour": plan_tour,
    "debt_default": plan_debt_default,
    "job_shift": plan_job_shift,
    "save_resume": plan_save_resume,
    "year_end": plan_year_end,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Capture the frozen behavior oracle.")
    parser.add_argument("--force", action="store_true", help="overwrite existing oracle files")
    parser.add_argument("names", nargs="*", help="sessions to capture (default: all)")
    args = parser.parse_args(argv)
    ORACLE_DIR.mkdir(exist_ok=True)
    for session in SESSIONS:
        if args.names and session.name not in args.names:
            continue
        paths = [session.transcript_path] + [
            session.stdin_path(i) for i in range(len(session.phases))
        ]
        if not args.force and any(p.exists() for p in paths):
            print(f"{session.name}: exists, kept (--force overwrites)")
            continue
        stdins = PLANS[session.name]()
        assert len(stdins) == len(session.phases), session.name
        for index, text in enumerate(stdins):
            session.stdin_path(index).write_text(text, encoding="utf-8")
        with tempfile.TemporaryDirectory() as workdir:
            transcript = run_session(session, Path(workdir))
        with open(session.transcript_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(transcript)
        print(f"{session.name}: {len(transcript)} chars, {transcript.count(chr(10))} lines")
    for name, scenario_path in FIGHTS.items():
        if args.names and name not in args.names:
            continue
        if not args.force and (FIGHTS_DIR / f"{name}.json").exists():
            print(f"fight {name}: exists, kept (--force overwrites)")
            continue
        path = capture_fight(name, scenario_path)
        print(f"fight {name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()

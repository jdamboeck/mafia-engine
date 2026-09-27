"""The frozen behavior oracle: today's game, recorded as screen transcripts.

The seam refactor that follows (the full-game plan's first phase) must change no
behavior. This module is the proof it did not. Each session below drives the real
terminal client -- ``clients.terminal.main(argv)`` over piped stdin, seeded -- and its
stdout is compared, exactly, with a transcript captured before the refactor began
(``tests/oracle/<name>.txt``; the keys it was driven with sit beside it as
``<name>.stdin``). Fight recordings are replayed too (``tests/oracle/fights/``).

The oracle imports no effect class and nothing from the engine's internals beyond
what drives ``main()`` and replays a recording, so a refactor of the engine/config
seam cannot rewrite what it checks. The transcripts are frozen data: this test only
compares them, it never writes them. They were written once by
``tools/capture_oracle.py``; re-capturing after the refactor started would defeat
the point. The oracle is retired in U7, after its last pass.

A transcript is the session's stdout with one lossless compaction: a long line that
already appeared earlier (a map or combat-grid row, a separator) is written as a
back-reference to the transcript line(s) it repeats (:func:`compact`). Every screen
is still there, byte for byte; the city map just is not stored hundreds of times.

Terminal size and colour support are pinned (:data:`PINNED_ENV`) -- the client reads
``COLUMNS``/``LINES`` (``shutil.get_terminal_size``) and ``COLORTERM``/``TERM``
(``clients/terminal/palette.py``) and nothing else from the environment -- so a
transcript matches on every machine and on every supported Python.
"""

from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.helpers import deadline

ORACLE_DIR = Path(__file__).resolve().parent / "oracle"
#: The oracle's own fight recordings. ``recordings/`` (the fightlab's example) is
#: gitignored run output -- absent in CI, and stale against today's rules on a machine
#: that has an old one -- so the oracle records and keeps its own.
FIGHTS_DIR = ORACLE_DIR / "fights"

#: The environment every session runs under. ``TERM=dumb`` with no ``COLORTERM``
#: selects the 8-colour palette, so no host terminal leaks into a transcript.
PINNED_ENV = {"COLUMNS": "80", "LINES": "24", "TERM": "dumb"}
UNSET_ENV = ("COLORTERM",)

#: The save file of the save-and-resume session, relative to the session's working
#: directory (a temporary one), so no machine-specific path reaches a transcript.
SAVE_FILE = "oracle-save.jsonl"

#: Lines at least this long are compacted when they repeat (see :func:`compact`).
_REPEAT_MIN = 60
_SESSION_DEADLINE = 120.0

#: The setup answers every 1930 session passes as flags, so no setup prompt is asked.
_SETUP = ["--end-year", "1930", "--score-weight", "1"]


@dataclass(frozen=True)
class Session:
    """One scripted session: ``main()``'s argv per phase, and what it must reach.

    A session with several phases runs them in order in ONE working directory (the
    save-and-resume session saves in its first phase and ``--load``s in its second).
    ``markers`` are texts the transcript must contain, proving the script reached what
    the session is for rather than silently derailing somewhere earlier.
    """

    name: str
    phases: tuple[tuple[str, ...], ...]
    markers: tuple[str, ...]

    def stdin_path(self, phase: int) -> Path:
        suffix = ".stdin" if len(self.phases) == 1 else f".{phase + 1}.stdin"
        return ORACLE_DIR / f"{self.name}{suffix}"

    @property
    def transcript_path(self) -> Path:
        return ORACLE_DIR / f"{self.name}.txt"


SESSIONS: tuple[Session, ...] = (
    Session(
        # Two players; between them they use all five locations, then play one more
        # round so each one's next upkeep runs over what they did.
        name="tour",
        phases=(("--seed", "42", *_SETUP, "--player", "a:x", "--player", "b:y"),),
        markers=(
            "du begibst dich an den spieltisch...",  # sph: a hand is dealt
            "du hast nun die neue waffe!",  # waf: a knife bought
            "wieviele monate willst du mieten",  # slw: a flat rented
            "'was? alkohol? ist doch verboten!'",  # pub: a drink refused
            "'du hast 6 monate zeit, die schulden zurueck zu zahlen!'",  # kdh: a loan
            "spieler b\nist an der reihe...",  # the second player's turns
            "spielstand 1925-3",  # the extra round
        ),
    ),
    Session(
        # Borrow at kdh, sit out the six-month grace, lose the collectors' fight.
        name="debt_default",
        phases=(("--seed", "7", *_SETUP),),
        markers=(
            "dir bleiben noch 2 monat(e), sie abzuzahlen!",  # the last warning
            "der kredithai hetzt seine eintreiber auf dich...",
            "treffer!",
            "sieger: eintreiber!!!",
            "die kerle nehmen dein ganzes geld mit und verschwinden...",
        ),
    ),
    Session(
        # Take a pub job; the next turn is the bouncer's shift and its fight.
        name="job_shift",
        phases=(("--seed", "5", *_SETUP),),
        markers=(
            "'du hast den job!'",
            "'ein bursche faengt an, zu randalieren! tu etwas!'",
            "treffer!",
            "'du hast deinen auftrag nicht erledigt!'",
        ),
    ),
    Session(
        # Gamble and save; then --load the save and play on from it, across a turn.
        name="save_resume",
        phases=(("--seed", "11", *_SETUP, "--save", SAVE_FILE), ("--load", SAVE_FILE)),
        markers=(
            f"spielstand gespeichert: {SAVE_FILE}",
            f"#### phase 2: main(['--load', '{SAVE_FILE}'])",
            "du hast 6400$. dein einsatz:",  # the resumed game kept the lost hand
            "spielstand 1925-1",  # the resumed game crossed a turn
            "du hast nun die neue waffe!",
        ),
    ),
    Session(
        # A 1928 game, one poker hand a month, to its year-end result.
        name="year_end",
        phases=(("--seed", "3", "--end-year", "1928", "--score-weight", "1"),),
        markers=("spielstand 1927-12", "spielstand 1928-1", "alcapone hat gewonnen!"),
    ),
)


def compact(text: str) -> str:
    """``text`` with every repeated long line written as a back-reference.

    Lossless: a line of at least ``_REPEAT_MIN`` characters that already occurred is
    replaced by ``<<repeats line N>>`` -- or ``<<repeats lines N-M>>`` for a run of
    them that repeats consecutive transcript lines -- naming the 1-based line of the
    RESULT where it was first written out in full. Expanding every reference gives
    back ``text`` exactly.
    """
    out: list[str] = []
    first_at: dict[str, int] = {}
    run: tuple[int, int] | None = None  # (first, last) transcript lines of an open run

    def close_run() -> None:
        nonlocal run
        if run is not None:
            first, last = run
            ref = f"line {first}" if first == last else f"lines {first}-{last}"
            out.append(f"<<repeats {ref}>>\n")
            run = None

    for line in text.splitlines(keepends=True):
        seen = first_at.get(line) if len(line) >= _REPEAT_MIN else None
        if seen is not None:
            if run is not None and seen == run[1] + 1:
                run = (run[0], seen)
            else:
                close_run()
                run = (seen, seen)
            continue
        close_run()
        out.append(line)
        if len(line) >= _REPEAT_MIN:
            first_at[line] = len(out)
    close_run()
    return "".join(out)


def read_stdin(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _ScriptStdin(io.StringIO):
    """Piped stdin that remembers whether the client ever read past its end."""

    hit_eof = False

    def readline(self, size: int | None = -1, /) -> str:
        line = super().readline(-1 if size is None else size)
        if line == "":
            self.hit_eof = True
        return line


def run_phase(argv: tuple[str, ...], stdin_text: str) -> str:
    """``clients.terminal.main(argv)`` over ``stdin_text``; returns its stdout.

    Runs under :data:`PINNED_ENV` in the CURRENT working directory. The script must
    end the session itself (``q``, or the year-end result's key): running out of
    input, or leaving keys unread, fails -- a derailed script cannot pass as a short
    one. A ``SystemExit`` (a flag ``main()`` rejects) propagates and fails too.
    """
    from clients.terminal import main

    out = io.StringIO()
    stdin = _ScriptStdin(stdin_text)
    with pytest.MonkeyPatch.context() as mp:
        for key, value in PINNED_ENV.items():
            mp.setenv(key, value)
        for key in UNSET_ENV:
            mp.delenv(key, raising=False)
        mp.setattr(sys, "stdin", stdin)
        mp.setattr(sys, "stdout", out)
        with deadline(
            _SESSION_DEADLINE,
            f"main({list(argv)}) did not return within {_SESSION_DEADLINE}s (spin?)",
            exc_type=AssertionError,
        ):
            main(list(argv))
    unread = stdin.read()
    assert not stdin.hit_eof, f"main({list(argv)}) ran out of scripted input"
    assert not unread, f"main({list(argv)}) ended with keys unread: {unread!r}"
    return out.getvalue()


def run_session(session: Session, workdir: Path) -> str:
    """Every phase of ``session`` in ``workdir``, as one compacted transcript."""
    parts: list[str] = []
    previous = Path.cwd()
    os.chdir(workdir)
    try:
        for index, argv in enumerate(session.phases):
            stdin_text = read_stdin(session.stdin_path(index))
            parts.append(f"#### phase {index + 1}: main({list(argv)})\n")
            parts.append(run_phase(argv, stdin_text))
            parts.append("\n#### end of phase\n")
    finally:
        os.chdir(previous)
    return compact("".join(parts))


def read_transcript(session: Session) -> str:
    with open(session.transcript_path, encoding="utf-8", newline="") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# The transcripts                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("session", SESSIONS, ids=[s.name for s in SESSIONS])
def test_transcript_reproduces_exactly(session: Session, tmp_path: Path) -> None:
    expected = read_transcript(session)
    got = run_session(session, tmp_path)
    if got != expected:
        got_lines, want_lines = got.splitlines(), expected.splitlines()
        at = next(
            (i for i, (g, w) in enumerate(zip(got_lines, want_lines)) if g != w),
            min(len(got_lines), len(want_lines)),
        )
        pytest.fail(
            f"{session.name}: transcript diverges at line {at + 1}\n"
            f"  expected: {want_lines[at] if at < len(want_lines) else '<end>'!r}\n"
            f"  got:      {got_lines[at] if at < len(got_lines) else '<end>'!r}",
            pytrace=False,
        )


@pytest.mark.parametrize("session", SESSIONS, ids=[s.name for s in SESSIONS])
def test_transcript_reaches_its_target(session: Session) -> None:
    """The frozen transcript shows what the session is for, and ends cleanly.

    Guards the captured data itself: a script that derailed early would still
    reproduce exactly, and prove nothing about the flow it was meant to cover.
    """
    text = read_transcript(session)
    missing = [marker for marker in session.markers if marker not in text]
    if missing:
        pytest.fail(f"{session.name}: never reached {missing}", pytrace=False)
    assert "Traceback" not in text
    assert text.count("#### end of phase\n") == len(session.phases)


def test_compaction_is_lossless() -> None:
    """Expanding every back-reference gives back the original text."""
    import re

    original = "".join(
        ["short\n", "x" * 70 + "\n", "y" * 70 + "\n", "short\n"]
        + ["x" * 70 + "\n", "y" * 70 + "\n", "z" * 70 + "\n", "y" * 70 + "\n"]
    )
    packed = compact(original)
    lines = packed.splitlines(keepends=True)
    expanded: list[str] = []
    for line in lines:
        match = re.fullmatch(r"<<repeats lines? (\d+)(?:-(\d+))?>>\n", line)
        if match is None:
            expanded.append(line)
            continue
        first = int(match.group(1))
        last = int(match.group(2) or first)
        expanded.extend(lines[first - 1 : last])
    assert "".join(expanded) == original
    assert len(lines) < len(original.splitlines())


# --------------------------------------------------------------------------- #
# The fight recordings                                                         #
# --------------------------------------------------------------------------- #

RECORDINGS = sorted(FIGHTS_DIR.glob("*.json"))


def test_there_are_recordings_to_replay() -> None:
    assert RECORDINGS, f"no fight recordings under {FIGHTS_DIR}"


@pytest.mark.parametrize("path", RECORDINGS, ids=[p.stem for p in RECORDINGS])
def test_recording_replays_without_divergence(path: Path) -> None:
    from data.game_configs.mafia_1920s.combat_rules import build_rules
    from engine.recording import load, replay

    recording = load(path, rules=build_rules())
    assert recording.events, f"{path.name} records no decisions: its replay is vacuous"
    hits = [e for e in recording.events if (getattr(e, "result", None) or {}).get("hit")]
    assert hits, f"{path.name} records no hit: its replay checks no damage"
    report = replay(recording)
    assert not report.diverged, f"{path.name} diverged at event {report.at_index}: {report}"

"""One-key reads: the terminal's answer to a prompt read with one key (``getx$``).

:func:`read_key` reads one key press from a stream: in raw mode on a terminal, so the
key needs no Enter; from a pipe (tests, scripts, redirected files), one key per line.
:func:`decode_key` names what a terminal sends for a key: a printable key is itself in
lower case, Enter is :data:`ENTER`, and the escape sequences terminals send for the
function keys F1-F5 are ``"f1"``-``"f5"``.
"""

from __future__ import annotations

import os
import select
from collections.abc import Iterable
from typing import TextIO

__all__ = ["ENTER", "decode_key", "read_key"]

#: The name of the Enter key (RETURN).
ENTER = "\r"

#: The escape sequences terminals send for F1-F5 (``ESC`` stripped): xterm's SS3
#: forms for F1-F4 (``ESC O P`` ...), the VT220 / rxvt ``ESC [ 11~`` ... forms, xterm's
#: ``ESC [ 15~`` for F5, and the Linux console's ``ESC [ [ A`` ... ``ESC [ [ E``.
_FUNCTION_KEYS = {
    "OP": "f1",
    "OQ": "f2",
    "OR": "f3",
    "OS": "f4",
    "[11~": "f1",
    "[12~": "f2",
    "[13~": "f3",
    "[14~": "f4",
    "[15~": "f5",
    "[[A": "f1",
    "[[B": "f2",
    "[[C": "f3",
    "[[D": "f4",
    "[[E": "f5",
}
_ESC = "\x1b"
_CTRL_C = "\x03"
#: How long to wait for the rest of an escape sequence after a lone ESC (seconds).
_SEQUENCE_WAIT = 0.05


def decode_key(raw: str) -> str:
    """Name the key ``raw`` is: what one key press sent (pure; no I/O).

    ``"\\r"`` and ``"\\n"`` are :data:`ENTER`; a function key's escape sequence is its
    name (``"\\x1bOP"`` is ``"f1"``); an escape sequence this does not know is
    returned as sent, so it answers no prompt; any other input is its first
    character in lower case. ``raw`` is not empty.
    """
    if raw[0] in "\r\n":
        return ENTER
    if raw[0] == _ESC and len(raw) > 1:
        return _FUNCTION_KEYS.get(raw[1:], raw)
    return raw[0].lower()


def read_key(stdin: TextIO, answer_keys: Iterable[str] = ()) -> str | None:
    """Read one key press from ``stdin``; ``None`` at end of input.

    On a terminal the key is read in raw mode (no Enter, no echo) and decoded with
    :func:`decode_key`; Ctrl-C raises :class:`KeyboardInterrupt` as it would in
    cooked mode, and a read of nothing (a hung-up terminal) is the end of input.

    From a stream that is not a terminal, one line is one key: a blank line is
    :data:`ENTER`, a line that is exactly one of ``answer_keys`` (``"f1"``) is that
    key, a line holding an escape sequence is decoded, and any other line is its
    first character in lower case.
    """
    try:
        import termios
        import tty

        fd = stdin.fileno()
        old = termios.tcgetattr(fd)  # raises termios.error on a non-terminal
    except Exception:
        return _read_piped_key(stdin, answer_keys)
    try:
        # TCSANOW: keys typed ahead stay queued for this read, as the C64's keyboard
        # buffer keeps them (the default, TCSAFLUSH, would discard them).
        tty.setraw(fd, termios.TCSANOW)
        data = os.read(fd, 1)
        if data == b"\x1b":
            # The rest of an escape sequence follows at once; a lone ESC does not.
            while not _sequence_complete(data) and _more_waiting(fd):
                more = os.read(fd, 1)
                if not more:
                    break
                data += more
    # A hang-up ends the input: Linux reads b"", other systems fail the read (EIO).
    except OSError:
        data = b""
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except termios.error:  # a hung-up terminal has no mode left to restore
            pass
    if not data:
        return None
    raw = data.decode("utf-8", errors="replace")
    if raw[0] == _CTRL_C:
        raise KeyboardInterrupt
    return decode_key(raw)


def _more_waiting(fd: int) -> bool:
    return bool(select.select([fd], [], [], _SEQUENCE_WAIT)[0])


def _sequence_complete(data: bytes) -> bool:
    """Whether an escape sequence read so far is whole: ``ESC O`` and a letter, or
    ``ESC [`` ... ending in a letter or ``~`` (``ESC [ [`` still wants its letter)."""
    if len(data) < 3:
        return False
    last = data[-1:]
    if data[1:2] == b"O":
        return True
    return last == b"~" or last.isalpha()


def _read_piped_key(stdin: TextIO, answer_keys: Iterable[str]) -> str | None:
    line = stdin.readline()
    if line == "":
        return None
    text = line.strip()
    if not text:
        return ENTER
    if text.lower() in set(answer_keys):
        return text.lower()
    return decode_key(text)

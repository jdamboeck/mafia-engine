"""U10 — the terminal client: a thin renderer over the frozen protocol (docs/design §7).

NO game logic, NO local simulation state. The client is two callables plus a map REPL,
all consuming the shared headless resolver (:mod:`engine.strings`):

* :class:`TerminalInput` — an ``input_source(interaction) -> response`` the driver
  (:func:`engine.interactions.run`) pulls from. Control flow is INVERTED: the driver owns
  the loop, validation, re-prompt, and cancel-throw; the client only renders the prompt
  and relays one typed response. It never prompts on ``ShowMessage`` (the driver auto-acks
  it) and never sends ``Cancelled`` (it returns the ``CANCEL`` *input* sentinel; the driver
  throws ``Cancelled`` into the handler).
* :func:`render_result` / :func:`render_message` — print resolved text + a status bar off
  ``EngineResult.state`` between actions.
* :func:`map_repl` — read a key, call ``try_move``/``run_option``, adopt ``result.state``,
  render, loop until turn end. Enforces nothing the engine already enforces.

Screen handling uses raw ANSI (no ``rich``/``curses`` dependency — see
docs/plans/u10-terminal-client-notes.md). The package imports from ``engine/`` only.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from typing import Any, TextIO

from engine.interactions import (
    CANCEL,
    Confirm,
    PromptChoice,
    PromptInt,
    ShowMessage,
)
from engine.strings import Resolver

_DEFAULT_CONFIG_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "game_configs" / "mafia_1920s"
)

__all__ = [
    "TerminalInput",
    "ScreenContext",
    "render_message",
    "render_result",
    "map_repl",
    "CLEAR",
    "DIM",
    "RESET",
    "CURSOR_HIDE",
    "CURSOR_SHOW",
    "hide_cursor",
    "show_cursor",
]

# --- ANSI (stdlib-only; ~a handful of constants, keeps stdout assertable) ---- #
CLEAR = "\033[2J\033[H"  # clear screen + home cursor (never os.system('clear'))
DIM = "\033[2m"
RESET = "\033[0m"
CURSOR_HIDE = "\033[?25l"
CURSOR_SHOW = "\033[?25h"

#: Inputs that mean "cancel" at a cancellable prompt (blank line or an explicit token).
_CANCEL_TOKENS = {"", "q", "quit", "cancel"}
#: Inputs that read as truthy for a Confirm prompt.
_YES_TOKENS = {"y", "yes", "j", "ja", "1", "true"}


# ---------------------------------------------------------------------------
# Cursor visibility helpers
# ---------------------------------------------------------------------------


def hide_cursor(out: TextIO) -> None:
    """Hide the terminal cursor."""
    out.write(CURSOR_HIDE)
    out.flush()


def show_cursor(out: TextIO) -> None:
    """Show the terminal cursor."""
    out.write(CURSOR_SHOW)
    out.flush()


# ---------------------------------------------------------------------------
# Screen context — tracks current color scheme per game screen
# ---------------------------------------------------------------------------


class ScreenContext:
    """Tracks the active color scheme (bg, fg) and applies it on transitions.

    Context names map to entries in ``themes/<theme>/renderer/contexts.yaml``.
    When switching, the context writes the appropriate ANSI background/foreground
    escape sequences to the output stream.
    """

    def __init__(
        self,
        contexts: dict[str, dict[str, str]],
        out: TextIO,
    ) -> None:
        self._contexts = contexts
        self._out = out
        self._current_name: str | None = None
        self._current: dict[str, str] | None = None

    @classmethod
    def from_config(cls, config_dir: Path, out: TextIO, theme: str = "classic") -> ScreenContext:
        """Load contexts from ``themes/<theme>/renderer/contexts.yaml``."""
        import yaml

        ctx_path = config_dir / "themes" / theme / "renderer" / "contexts.yaml"
        try:
            raw = yaml.safe_load(ctx_path.read_text(encoding="utf-8"))
            contexts = raw if isinstance(raw, dict) else {}
        except (OSError, yaml.YAMLError):
            contexts = {}
        return cls(contexts, out)

    def switch(self, context_name: str) -> None:
        """Switch to a named context, applying its colors."""
        ctx = self._contexts.get(context_name)
        if ctx is None:
            return
        self._current_name = context_name
        self._current = ctx
        self.apply()

    def apply(self) -> None:
        """Re-apply the current context's colors to the output stream."""
        if self._current is None:
            return
        from clients.terminal.palette import bg as bg_ansi, fg as fg_ansi, load_palette

        pal = load_palette(_DEFAULT_CONFIG_DIR)
        bg_name = self._current.get("bg")
        fg_name = self._current.get("fg")
        if bg_name:
            self._out.write(bg_ansi(bg_name, pal))
        if fg_name:
            self._out.write(fg_ansi(fg_name, pal))
        self._out.flush()

    def reset(self) -> None:
        """Reset to default colors (no bg/fg override)."""
        self._current_name = None
        self._current = None
        self._out.write("\033[39m\033[49m")  # reset fg + bg
        self._out.flush()

    @property
    def name(self) -> str | None:
        return self._current_name


# ---------------------------------------------------------------------------
# SIGWINCH handling
# ---------------------------------------------------------------------------

# Module-level flag checked by main loops after each key read.
_resize_pending = False


def _sigwinch_handler(signum: int, frame: Any) -> None:
    global _resize_pending
    _resize_pending = True


def check_resize() -> bool:
    """Check (and clear) the resize flag.  Returns True if a resize happened."""
    global _resize_pending
    if _resize_pending:
        _resize_pending = False
        return True
    return False


def install_sigwinch_handler() -> None:
    """Install the SIGWINCH handler (no-op if signal.SIGWINCH is unavailable)."""
    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, _sigwinch_handler)


def render_message(resolver: Resolver, message: ShowMessage, out: TextIO) -> None:
    """Resolve and print one ``ShowMessage`` (key + params -> text)."""
    out.write(resolver.resolve(message.key, message.params) + "\n")


class TerminalInput:
    """An ``input_source`` callable: render a prompt, read one line, relay the response.

    The driver calls this once per interaction (again on re-prompt). It resolves the
    prompt's key to text via the shared resolver, reads a single line of input, and
    returns the typed response — an ``int``-bearing string for :class:`PromptInt`/
    :class:`PromptChoice` (the driver coerces it), a ``bool`` for :class:`Confirm`, or the
    :data:`CANCEL` sentinel at a cancellable prompt. A non-numeric entry is relayed
    verbatim; the DRIVER decides it is invalid and re-prompts (validation ownership is the
    driver's — KTD-8). ``ShowMessage`` is rendered here for completeness but the driver
    auto-acks it without ever consulting this callable.
    """

    def __init__(
        self,
        *,
        resolver: Resolver,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self._resolver = resolver
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout

    def __call__(self, interaction: Any) -> Any:
        if isinstance(interaction, ShowMessage):
            # Driver auto-acks ShowMessage; if we are ever consulted, just render it.
            render_message(self._resolver, interaction, self._stdout)
            return None

        self._stdout.write(self._prompt_text(interaction))
        raw = self._read_line()

        if isinstance(interaction, Confirm):
            return raw.strip().lower() in _YES_TOKENS

        cancellable = getattr(interaction, "cancellable", False)
        if cancellable and raw.strip().lower() in _CANCEL_TOKENS:
            return CANCEL
        # Relay the raw line as-is; the driver coerces/validates and re-prompts if needed.
        return raw.strip()

    def _prompt_text(self, interaction: Any) -> str:
        if isinstance(interaction, PromptChoice):
            lines = [self._resolver.resolve(interaction.key)]
            for i, opt in enumerate(interaction.options):
                # Options are already-resolved labels or plain values — show as given.
                lines.append(f"  {i}) {opt}")
            return "\n".join(lines) + "\n> "
        if isinstance(interaction, (PromptInt, Confirm)):
            return self._resolver.resolve(interaction.key) + "\n> "
        return "> "

    def _read_line(self) -> str:
        line = self._stdin.readline()
        # EOF returns "" from readline; treat as an empty line (cancel/re-prompt fodder).
        return line.rstrip("\n")


def render_result(result: Any, out: TextIO) -> None:
    """Between actions, render a status bar off ``result.state``.

    Display-only. ``result`` is an :class:`engine.actions.EngineResult`; a ``cancelled``
    status renders as a quiet no-op (the action committed nothing).
    """
    if result.status == "cancelled":
        return
    state = result.state
    if state is not None and state.players:
        p = state.players[state.clock.active_player]
        out.write(f"{DIM}[cash {p.ka}$ | pos {p.po} | ms {p.ms}]{RESET}\n")


def map_repl(
    *,
    state: Any,
    city: Any,
    key_reader,
    move_for_key,
    out: TextIO | None = None,
) -> Any:
    """Drive one turn on the map: read a key, move, adopt the new state, render, repeat.

    Holds no rules — it calls ``try_move`` (imported lazily to keep this module's import
    graph minimal) and adopts the returned ``result.state`` (movement is pure). Loops until
    ``ms <= 0`` / a turn-over move. ``key_reader() -> str`` yields the next key; ``move_for_key``
    maps a key to a movement delta (or ``None`` to quit). Returns the final state.
    """
    from engine.movement import try_move

    sink: TextIO = out if out is not None else sys.stdout
    while True:
        key = key_reader()
        delta = move_for_key(key)
        if delta is None:
            return state
        result = try_move(state, city, delta)
        state = result.state  # adopt (movement is pure)
        render_result(result, sink)
        if getattr(result.payload, "turn_over", False):
            return state

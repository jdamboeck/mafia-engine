"""The terminal client: a thin renderer over the frozen protocol (docs/design §7).

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

Screen handling uses raw ANSI escapes: stdlib-only, and stdout stays plain text that
tests can assert on (``rich`` would pollute captured output; ``curses`` fights the
``input_source`` model and cannot run under piped stdin). The package imports from
``engine/`` only.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from typing import Any, TextIO

from engine.interactions import (
    CANCEL,
    OBSERVE_PROMPT,
    CombatScreen,
    Confirm,
    PromptChoice,
    PromptInt,
    ShowMessage,
)
from engine.strings import MissingKeyError, Resolver

from clients.terminal.palette import DIM, RESET, RESET_FG, RESET_BG, Colors

#: The game config this client plays: the default ``mafia_1920s`` config.
CONFIG_DIR = Path(__file__).resolve().parents[2] / "data" / "game_configs" / "mafia_1920s"

__all__ = [
    "CONFIG_DIR",
    "play",
    "main",
    "TerminalSession",
    "EndOfInput",
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
    "client_text",
]

# --- ANSI (cursor/screen control — color constants live in palette.py) ------ #
CLEAR = "\033[2J\033[H"  # clear screen + home cursor
CURSOR_HIDE = "\033[?25l"
CURSOR_SHOW = "\033[?25h"

#: Inputs that mean "cancel" at a cancellable prompt (blank line or an explicit token).
_CANCEL_TOKENS = {"", "q", "quit", "cancel"}
#: Inputs that read as truthy for a Confirm prompt.
_YES_TOKENS = {"y", "yes", "j", "ja", "1", "true"}

# ---------------------------------------------------------------------------
# Combat key mapping -- "key bindings are presentation, formulas are not". The
# original's raw C64 GET keys are `:`/`;`/`@`/`/` (move/aim left, right, up, down;
# mf-prg.bas:30130-30133 for movement, 30206-30209 for the aim step),
# RETURN to enter the aim-then-fire sub-loop (30134), SPACE to pass (30135), and
# `q` to surrender (30136). Those raw glyphs are awkward on a real keyboard and
# `q` for "surrender" collides with this client's OWN pre-existing "quit" key
# (_is_quit in session.py) at every OTHER screen -- so this client maps to
# client-appropriate keys instead of the original's literal GET characters (a
# client may rebind keys; it may not change what an action does). WASD mirrors the
# map-walk keys already bound in session.py (_MOVE_KEYS) so the player's fingers do not have to relearn
# directions between the map and the grid; F is "fire" (enter the aim step); P
# passes (SPACE is what the source binds, but a literal space is easy to lose in
# a piped-stdin test script, so this client spells it as a letter key instead --
# presentation, not formula); SURRENDER is spelled out (not bound to a
# single letter) so it cannot be hit by accident the way a bare `q` could.
#: The combat grid is 40 columns wide (CLAUDE.md: "the combat grid is 40x13" -- a
#: fact of the wire protocol's coordinate space, not simulation logic, so it is
#: safe to state here without importing engine.combat). WASD -> the same linear
#: step delta engine.combat.STEPS uses (left/right = ±1, up/down = ∓40), so the
#: response this module sends is exactly what the driver's ``fight.try_move``/
#: ``fight.shoot`` already expect -- no engine.combat import needed to produce it.
_COMBAT_GRID_COLS = 40
_COMBAT_MOVE_KEYS = {"w": -_COMBAT_GRID_COLS, "s": _COMBAT_GRID_COLS, "a": -1, "d": 1}
#: Same four letters read a SECOND time, after 'f', as the aim direction for a shot.
_COMBAT_AIM_KEYS = dict(_COMBAT_MOVE_KEYS)
_COMBAT_FIRE_KEY = "f"
#: "p" for pass -- a literal space is easy to lose in a piped-stdin script (a script
#: line of " " round-trips through strip() indistinguishably from a genuinely blank
#: line), so this client spells the pass key out rather than binding raw SPACE; a
#: blank/unrecognized line re-prompts instead of silently passing the activation.
_COMBAT_PASS_KEY = "p"
_COMBAT_SURRENDER_TOKENS = {"surrender", "give up", "yield"}


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
    escape sequences to the output stream, in the session's ``colors``.
    """

    def __init__(
        self,
        contexts: dict[str, dict[str, str]],
        out: TextIO,
        colors: Colors,
    ) -> None:
        self._contexts = contexts
        self._out = out
        self._colors = colors
        self._current_name: str | None = None
        self._current: dict[str, str] | None = None

    @classmethod
    def from_config(
        cls, config_dir: Path, out: TextIO, colors: Colors, theme: str = "classic"
    ) -> ScreenContext:
        """Load contexts from ``themes/<theme>/renderer/contexts.yaml``."""
        import yaml

        ctx_path = config_dir / "themes" / theme / "renderer" / "contexts.yaml"
        try:
            raw = yaml.safe_load(ctx_path.read_text(encoding="utf-8"))
            contexts = raw if isinstance(raw, dict) else {}
        except (OSError, yaml.YAMLError):
            contexts = {}
        return cls(contexts, out, colors)

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
        bg_name = self._current.get("bg")
        fg_name = self._current.get("fg")
        if bg_name:
            self._out.write(self._colors.bg(bg_name))
        if fg_name:
            self._out.write(self._colors.fg(fg_name))
        self._out.flush()

    def reset(self) -> None:
        """Reset to default colors (no bg/fg override)."""
        self._current_name = None
        self._current = None
        self._out.write(RESET_FG + RESET_BG)
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


def client_text(key: str, params: dict | None = None, *, resolver: Resolver) -> str:
    """Resolve one of the client's own ``client.*`` theme keys to display text.

    ``resolver`` is the session's resolver; a theme that lacks a ``client.*`` key
    fails loudly (``MissingKeyError``) like any other theme gap.
    """
    return resolver.resolve(key, params)


class EndOfInput(Exception):
    """stdin is exhausted (real EOF, not a blank line) at a non-combat prompt.

    Raised by :class:`TerminalInput` out through the driver and the handler, so the
    in-flight handler never resolves and its result -- hence its effects -- is never
    adopted. :func:`clients.terminal.session.play` catches it and ends the session
    exactly like a quit. Lives in the clients layer: the engine never sees it as
    anything but an exception escaping its ``input_source``.
    """


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
    verbatim; the DRIVER decides it is invalid and re-prompts (validation has a single
    owner, and it is the driver). ``ShowMessage`` is rendered here for completeness but
    the driver auto-acks it without ever consulting this callable.
    """

    def __init__(
        self,
        *,
        resolver: Resolver,
        colors: Colors,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        weapon_names: list[str] | None = None,
        observe_ai: bool = False,
    ) -> None:
        self._resolver = resolver
        self._colors = colors
        #: ``--watch-ai`` opt-in: :func:`engine.fight_loop._drive_fight` reads this
        #: attribute and, only when it is true, hands us a display-only ``prompt="observe"``
        #: CombatScreen after every CPU activation. Off by default -- the original shows
        #: nothing between CPU moves (mf-prg.bas:30110).
        self.observes_ai = observe_ai
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        #: Weapon id -> name, for the combat fighter panel. Optional: a caller whose
        #: handlers never yield CombatScreen need not supply it.
        self._weapon_names = weapon_names or []

    def __call__(self, interaction: Any) -> Any:
        return self.answer(interaction)

    def answer(self, interaction: Any, *, banner: str | None = None) -> Any:
        """Answer one interaction. ``banner`` is a line drawn at the top of a combat
        board, under the screen clear (the session's whose-turn line)."""
        if isinstance(interaction, ShowMessage):
            # Driver auto-acks ShowMessage; if we are ever consulted, just render it.
            render_message(self._resolver, interaction, self._stdout)
            return None

        if isinstance(interaction, CombatScreen) and interaction.prompt == OBSERVE_PROMPT:
            # A display-only frame after a CPU activation. Draw the board, wait for
            # one key, and return nothing -- the driver ignores the response, so no key
            # can ever act in the fight. EOF here is NOT a surrender and NOT an
            # EndOfInput: the frame just continues (readline() returns "" at once, so it
            # cannot hang), and the next real prompt meets the same EOF and ends the
            # fight/session through its own, already-defined EOF path.
            self._render_combat_screen(
                interaction, footer=("combat.observe_prompt",), banner=banner
            )
            self._stdin.readline()
            return None

        if isinstance(interaction, CombatScreen):
            # This callable owns the WHOLE combat turn -- render the grid, the
            # active fighter's panel, and any message from the last activation, THEN
            # read one key and apply the combat key mapping. EOF -> CANCEL, which
            # _parse_combat_response (engine/fight_loop.py) maps to a surrender --
            # combat prompts are non-cancellable, so CANCEL here is never a
            # "discard the action" signal, only "give up".
            self._render_combat_screen(interaction, banner=banner)
            return self._read_combat_action()

        self._stdout.write(self._prompt_text(interaction))
        raw = self._read_line()

        if isinstance(interaction, Confirm):
            return raw.strip().lower() in _YES_TOKENS

        cancellable = getattr(interaction, "cancellable", False)
        if cancellable and raw.strip().lower() in _CANCEL_TOKENS:
            return CANCEL
        # Relay the raw line as-is; the driver coerces/validates and re-prompts if needed.
        return raw.strip()

    def _render_combat_screen(
        self,
        screen: CombatScreen,
        footer: tuple[str, ...] = ("combat.action_prompt", "combat.key_legend"),
        banner: str | None = None,
    ) -> None:
        """Draw one activation's full combat screen: grid, message, panel, prompt.

        ``footer`` is the theme keys printed under the panel: the action prompt + key
        legend for a real activation, or the "press a key" line for an observe frame.
        ``banner``, when given, is printed first, right under the screen clear.

        Renders straight from ``screen.to_json()`` (the JSON-serializable payload,
        never the engine's ``CombatState``/``Fighter`` objects) so this client
        depends only on the wire shape, matching the pattern the rest of
        this class already follows for ``ShowMessage``.
        """
        from clients.terminal.renderers import (
            render_combat_grid,
            render_combat_message,
            render_fighter_panel,
            render_screen_clear,
        )

        payload = screen.to_json()
        render_screen_clear(self._stdout)
        if banner is not None:
            self._stdout.write(banner + "\n")
        render_combat_grid(payload, self._stdout, self._colors)
        render_combat_message(payload, self._resolver, self._stdout, self._colors)
        render_fighter_panel(
            payload, self._resolver, self._weapon_names, self._stdout, self._colors
        )
        for key in footer:
            self._stdout.write(self._resolver.resolve(key) + "\n")
        self._stdout.write("> ")
        self._stdout.flush()

    def _read_combat_action(self) -> Any:
        """Read one combat key and return the ``(action, argument)`` response.

        Key mapping (client-appropriate, NOT the original's raw GET chars):
        WASD move one cell; ``f`` then WASD aims and fires; ``p`` passes; the literal
        word ``surrender`` ends the fight. EOF returns :data:`CANCEL` (-> surrender:
        combat prompts are non-cancellable). An unrecognized line is relayed as an
        unknown-action string, which the driver's ``_parse_combat_response`` normalizes
        to a re-prompt.

        EOF is detected directly off ``readline()``'s own return ("" with nothing
        consumed means the stream is exhausted) rather than through ``_read_line``,
        which already collapses EOF and a literal blank line to the same "" and so
        cannot distinguish them -- the distinction matters here (blank re-prompts,
        EOF surrenders).
        """
        raw = self._stdin.readline()
        if raw == "":
            return CANCEL
        key = raw.rstrip("\n").strip().lower()
        if key in _COMBAT_SURRENDER_TOKENS:
            return ("surrender", None)
        if key == _COMBAT_PASS_KEY:
            return ("pass", None)
        if key in _COMBAT_MOVE_KEYS:
            return ("move", _COMBAT_MOVE_KEYS[key])
        if key == _COMBAT_FIRE_KEY:
            aim_raw = self._stdin.readline()
            if aim_raw == "":
                return CANCEL
            aim_key = aim_raw.rstrip("\n").strip().lower()
            if aim_key in _COMBAT_AIM_KEYS:
                return ("shoot", _COMBAT_AIM_KEYS[aim_key])
            return "unknown_aim"
        return "unknown_action"

    def _option_label(self, opt: Any) -> str:
        try:
            return self._resolver.resolve(str(opt))
        except MissingKeyError:
            return str(opt)

    def _prompt_text(self, interaction: Any) -> str:
        if isinstance(interaction, PromptChoice):
            lines = [self._resolver.resolve(interaction.key)]
            for i, opt in enumerate(interaction.options):
                # An option is either a theme KEY (sph's game menu, waf's venue menu) or a
                # plain value (a gangster's name). Resolve keys; show plain values as given.
                lines.append(f"  {i}) {self._option_label(opt)}")
            return "\n".join(lines) + "\n> "
        if isinstance(interaction, (PromptInt, Confirm)):
            return self._resolver.resolve(interaction.key) + "\n> "
        return "> "

    def _read_line(self) -> str:
        """Read one line for a non-combat prompt; raise :class:`EndOfInput` at EOF.

        ``readline()`` returns ``""`` only at real EOF; a blank line is ``"\\n"``. The
        two MUST differ here: a blank line is cancel (cancellable prompt) or re-ask
        (non-cancellable), but EOF can never be answered -- relaying it as a blank
        line made the driver re-ask a non-cancellable prompt forever.
        """
        line = self._stdin.readline()
        if line == "":
            raise EndOfInput
        return line.rstrip("\n")


def render_result(result: Any, out: TextIO, resolver: Resolver, colors: Colors) -> None:
    """Between actions, render a status bar off ``result.state``.

    Display-only. ``result`` is an :class:`engine.actions.EngineResult`; a ``cancelled``
    status renders as a quiet no-op (the action committed nothing).
    """
    if result.status == "cancelled":
        return
    from clients.terminal.renderers import render_status_bar_from_state

    render_status_bar_from_state(result.state, out, resolver, colors)


def map_repl(
    *,
    state: Any,
    city: Any,
    key_reader,
    move_for_key,
    resolver: Resolver,
    colors: Colors,
    out: TextIO | None = None,
) -> Any:
    """Drive one turn on the map: read a key, move, adopt the new state, render, repeat.

    Holds no rules — it calls ``try_move`` (imported lazily to keep this module's import
    graph minimal) and adopts the returned ``result.state`` (movement is pure). Loops until
    ``ms <= 0`` / a turn-over move. ``key_reader() -> str`` yields the next key; ``move_for_key``
    maps a key to a movement delta (or ``None`` to quit); ``resolver`` words the status
    bar and ``colors`` colours it. Returns the final state.
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
        render_result(result, sink, resolver, colors)
        if getattr(result.payload, "turn_over", False):
            return state


# The session and the command line are re-exported last: both modules import the
# building blocks above from this package, so those must exist before they load.
# Neither is ``__main__``, so ``python -m clients.terminal`` stays free of runpy's
# "found in sys.modules" warning.
from clients.terminal.session import TerminalSession, play  # noqa: E402
from clients.terminal.cli import main  # noqa: E402

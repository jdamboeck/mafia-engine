"""U10 — terminal client (thin renderer over the frozen input_source protocol).

The client holds NO game logic and NO local simulation state. It is two callables plus a
map REPL:

* ``TerminalInput`` — an ``input_source(interaction) -> response`` the driver pulls from.
  It renders the prompt via the shared resolver, reads one line, and returns the typed
  response (int / bool / the CANCEL sentinel). It NEVER prompts on ``ShowMessage`` (the
  driver auto-acks that) and NEVER sends ``Cancelled`` (it sends the CANCEL *input*; the
  driver throws ``Cancelled`` into the handler).
* ``render(result)`` — prints status off ``result.state`` between actions.

Validation lives in the driver, never here (KTD-8). These tests drive the client with
mocked stdin/stdout and assert rendering + relay only — no game-rule assertions.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import yaml


def _yaml_load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))

_CONFIG_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
)
sys.path.insert(0, str(_CONFIG_DIR.parent.parent))

from engine.interactions import (  # noqa: E402
    CANCEL,
    Confirm,
    PromptChoice,
    PromptInt,
    ShowMessage,
    run,
)
from engine.strings import Resolver  # noqa: E402
from engine.movement import DOWN, load_city  # noqa: E402
from clients.terminal import TerminalInput, map_repl, render_message  # noqa: E402

_CITY_YAML = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _resolver():
    return Resolver.from_config(_CONFIG_DIR, theme="classic")


def _client(stdin_lines: list[str]):
    """A TerminalInput wired to scripted stdin and a capture buffer for stdout."""
    out = io.StringIO()
    inp = TerminalInput(
        resolver=_resolver(),
        stdin=io.StringIO("\n".join(stdin_lines) + "\n"),
        stdout=out,
    )
    return inp, out


# --------------------------------------------------------------------------- #
# ShowMessage: rendered, never consults stdin.                                  #
# --------------------------------------------------------------------------- #
def test_show_message_never_consults_input_source():
    """The driver auto-acks ShowMessage WITHOUT consulting the input_source, so a
    ShowMessage-only handler must complete with the client never reading stdin (empty
    stdin would raise if it did). Rendering ShowMessage is render_message's job (below),
    not the input_source's — this is the U10 notes' 'client does not prompt on it'."""
    inp, out = _client([])  # empty stdin: if the client read it here, it would block/fail
    consulted = []

    class _Spy(TerminalInput):
        def __call__(self, interaction):
            consulted.append(interaction)
            return super().__call__(interaction)

    spy = _Spy(resolver=_resolver(), stdin=io.StringIO(""), stdout=out)

    def handler(ctx):
        yield ShowMessage("locations.slw.no_room")
        return []

    run(handler, spy, state=None, rng=None)
    assert consulted == []  # driver auto-acked; the input_source was never called


def test_show_message_with_params_substitutes():
    inp, out = _client([])
    render_message(_resolver(), ShowMessage("locations.slw.rent_quote", {"price": 500}), out)
    assert "500$ miete" in out.getvalue()


# --------------------------------------------------------------------------- #
# PromptInt: returns the entered int; the driver (not the client) re-prompts.   #
# --------------------------------------------------------------------------- #
def test_prompt_int_returns_entered_value():
    inp, out = _client(["7"])
    seen = []

    def handler(ctx):
        val = yield PromptInt("locations.slw.months_prompt", min=1, max=12)
        seen.append(val)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [7]
    # The prompt text was rendered.
    assert "monate" in out.getvalue().lower()


def test_prompt_int_invalid_then_valid_is_reprompted_by_driver():
    # "abc" is non-numeric -> the DRIVER re-prompts and consults the client again.
    inp, out = _client(["abc", "3"])
    seen = []

    def handler(ctx):
        val = yield PromptInt("locations.slw.months_prompt", min=1, max=12)
        seen.append(val)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [3]  # the client just relayed each line; the driver did the re-prompt


# --------------------------------------------------------------------------- #
# PromptChoice / Confirm relay.                                                 #
# --------------------------------------------------------------------------- #
def test_prompt_choice_returns_index():
    inp, out = _client(["1"])
    seen = []

    def handler(ctx):
        idx = yield PromptChoice("locations.slw.menu.rent", options=["a", "b", "c"])
        seen.append(idx)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [1]


def test_confirm_returns_bool():
    inp, out = _client(["y"])
    seen = []

    def handler(ctx):
        ok = yield Confirm("locations.slw.menu.rent")
        seen.append(ok)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [True]


# --------------------------------------------------------------------------- #
# Cancel: an empty line at a cancellable prompt returns the CANCEL sentinel.     #
# --------------------------------------------------------------------------- #
def test_cancellable_empty_line_returns_cancel_sentinel():
    inp, _out = _client([""])  # empty line = cancel
    got = inp(PromptInt("locations.slw.months_prompt", min=1, max=12, cancellable=True))
    assert got is CANCEL


def test_cancel_unwinds_handler_to_cancelled_status():
    inp, _out = _client([""])

    def handler(ctx):
        try:
            yield PromptInt("locations.slw.months_prompt", min=0, max=12, cancellable=True)
        finally:
            pass
        return []

    result = run(handler, inp, state=None, rng=None)
    assert result.status == "cancelled"
    assert result.effects == []


# --------------------------------------------------------------------------- #
# map_repl: crosses client -> movement; adopts result.state, ends the turn.     #
# --------------------------------------------------------------------------- #
def test_map_repl_adopts_state_and_ends_on_quit():
    """map_repl holds no rules: it moves via try_move, adopts the pure result.state, and
    stops when move_for_key returns None. Real movement through the engine layer."""
    from engine.config_loader import load_game_config

    cfg = load_game_config(_CONFIG_DIR)
    city = load_city(_yaml_load(_CITY_YAML))
    state = cfg.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("a", "b")]
    )
    state.players[0].po = 141  # a known walkable cell (per the slice test)

    keys = iter(["down", "quit"])
    out = io.StringIO()
    final = map_repl(
        state=state,
        city=city,
        key_reader=lambda: next(keys),
        move_for_key=lambda k: DOWN if k == "down" else None,
        out=out,
    )
    # One real step happened and the client adopted the new pure state (po moved, ms spent).
    assert final.players[0].po == 181
    assert final.players[0].ms == state.players[0].ms - 1
    assert "pos 181" in out.getvalue()

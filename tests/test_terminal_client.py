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
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml


def _yaml_load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
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
from clients.terminal import (  # noqa: E402
    CONFIG_DIR,
    CURSOR_HIDE,
    CURSOR_SHOW,
    EndOfInput,
    TerminalInput,
    main,
    map_repl,
    play,
    render_message,
)
from clients.terminal.palette import ColorSupport, Colors, load_palette  # noqa: E402
from tests.helpers import deadline, with_player  # noqa: E402

_CITY_YAML = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _resolver():
    return Resolver.from_config(_CONFIG_DIR, theme="classic")


#: The classic palette in truecolor, for tests that build a TerminalInput directly.
_COLORS = Colors(load_palette(_CONFIG_DIR), ColorSupport.TRUECOLOR)


def _client(stdin_lines: list[str]):
    """A TerminalInput wired to scripted stdin and a capture buffer for stdout."""
    out = io.StringIO()
    inp = TerminalInput(
        resolver=_resolver(),
        colors=_COLORS,
        stdin=io.StringIO("\n".join(stdin_lines) + "\n"),
        stdout=out,
    )
    return inp, out


# --------------------------------------------------------------------------- #
# ShowMessage: rendered, never consults stdin.                                  #
# --------------------------------------------------------------------------- #
def test_show_message_is_rendered_by_the_client_without_prompting():
    """The client RENDERS a ShowMessage and does not prompt on it (#43).

    Since #43 the driver hands ShowMessage to the input_source — that is the only way
    narration can ever reach a screen. The U10 note it must still honour is "the client
    does not prompt on it": stdin is empty here, so if ``TerminalInput`` read from it
    the run would fail. Instead the text must appear on stdout.
    """
    out = io.StringIO()
    consulted = []

    class _Spy(TerminalInput):
        def __call__(self, interaction):
            consulted.append(interaction)
            return super().__call__(interaction)

    spy = _Spy(resolver=_resolver(), colors=_COLORS, stdin=io.StringIO(""), stdout=out)

    def handler(ctx):
        yield ShowMessage("locations.slw.no_room")
        return []

    result = run(handler, spy, state=None, rng=None)

    assert result.status == "completed"  # empty stdin was never read -> no prompt
    assert [type(i).__name__ for i in consulted] == ["ShowMessage"]  # delivered
    assert out.getvalue().strip()  # ...and actually rendered to the screen


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


def test_prompt_choice_resolves_option_keys_and_shows_plain_values_as_given():
    # sph's game menu passes theme KEYS as options; a recruit menu passes plain names.
    # A key must render as its theme text, never raw; a non-key renders unchanged.
    inp, out = _client(["0"])

    def handler(ctx):
        yield PromptChoice("locations.sph.game_menu", options=["locations.sph.poker", "alcapone"])
        return []

    run(handler, inp, state=None, rng=None)
    text = out.getvalue()
    assert "0) 1 - poker" in text
    assert "1) alcapone" in text
    assert "locations.sph.poker" not in text


def test_confirm_returns_bool():
    inp, out = _client(["y"])
    seen = []

    def handler(ctx):
        ok = yield Confirm("locations.slw.menu.rent")
        seen.append(ok)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [True]


def test_confirm_returns_false_for_n():
    inp, out = _client(["n"])
    seen = []

    def handler(ctx):
        ok = yield Confirm("locations.slw.menu.rent")
        seen.append(ok)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [False]


def test_prompt_choice_returns_zero_index():
    inp, out = _client(["0"])
    seen = []

    def handler(ctx):
        idx = yield PromptChoice("locations.slw.menu.rent", options=["a", "b", "c"])
        seen.append(idx)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [0]


def test_prompt_choice_returns_higher_index():
    inp, out = _client(["3"])
    seen = []

    def handler(ctx):
        idx = yield PromptChoice("locations.slw.menu.rent", options=["a", "b", "c", "d"])
        seen.append(idx)
        return []

    run(handler, inp, state=None, rng=None)
    assert seen == [3]


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
# EOF vs blank line (R11): a blank line is cancel/re-ask fodder; real EOF ends   #
# the session. (EOF at a CombatScreen still surrenders -- covered by             #
# tests/test_client_loop.py::TestInteractiveCombatThroughTerminalInput::         #
# test_eof_mid_fight_surrenders_and_exits_cleanly.)                              #
# --------------------------------------------------------------------------- #
def _eof_client(stdin_text: str):
    """A TerminalInput over EXACT stdin text (``_client`` always appends a newline,
    so it can never produce real EOF on the first read)."""
    out = io.StringIO()
    return TerminalInput(
        resolver=_resolver(), colors=_COLORS, stdin=io.StringIO(stdin_text), stdout=out
    ), out


def test_eof_at_non_cancellable_prompt_int_raises_end_of_input():
    inp, _out = _eof_client("")  # readline() returns "" -> real EOF
    with pytest.raises(EndOfInput):
        inp(PromptInt("locations.sph.wager_prompt", min=1, max=100))


def test_eof_at_cancellable_prompt_raises_rather_than_cancelling():
    """EOF is not a blank line even where a blank line would cancel: it ends input."""
    inp, _out = _eof_client("")
    with pytest.raises(EndOfInput):
        inp(PromptChoice("locations.sph.wager_prompt", options=["a", "b"], cancellable=True))


def test_blank_line_then_eof_at_non_cancellable_prompt_escapes_the_driver():
    """Through the real driver: the blank line re-asks (unchanged), the EOF on the
    re-ask escapes ``run`` as EndOfInput instead of re-asking forever -- and the
    handler's would-be effects are never returned."""
    inp, _out = _eof_client("\n")  # one blank line, then EOF

    def handler(ctx):
        yield PromptInt("locations.sph.wager_prompt", min=1, max=100)
        return ["an effect that must never be committed"]

    with deadline(  # a regression fails, not hangs
        5.0, "run() re-asked past EOF instead of ending input (spin)", exc_type=AssertionError
    ):
        with pytest.raises(EndOfInput):
            run(handler, inp, state=None, rng=None)


# --------------------------------------------------------------------------- #
# map_repl: crosses client -> movement; adopts result.state, ends the turn.     #
# --------------------------------------------------------------------------- #
def test_map_repl_adopts_state_and_ends_on_quit():
    """map_repl holds no rules: it moves via try_move, adopts the pure result.state, and
    stops when move_for_key returns None. Real movement through the engine layer."""
    from engine.config_loader import load_game_config

    cfg = load_game_config(_CONFIG_DIR)
    city = load_city(_yaml_load(_CITY_YAML))
    state = cfg.module.new_game(seed=42, end_year=1930, score_weight=1.0, players=[("a", "b")])
    state = with_player(state, 0, po=141)  # a known walkable cell (per the slice test)

    keys = iter(["down", "quit"])
    out = io.StringIO()
    final = map_repl(
        state=state,
        city=city,
        key_reader=lambda: next(keys),
        move_for_key=lambda k: DOWN if k == "down" else None,
        resolver=_resolver(),
        colors=_COLORS,
        out=out,
    )
    # One real step happened and the client adopted the new pure state (po moved, ms spent).
    assert final.players[0].po == 181
    assert final.players[0].ms == state.players[0].ms - 1
    assert "pos 181" in out.getvalue()


# --------------------------------------------------------------------------- #
# U5 — setup prompts (R1, R22) and flags (R2, R23) for end year / score weight. #
# The original asks "spielende (1928-1978):" (mf-prg.bas:170, re-asks via :172) #
# then "punktewertigkeit (0.1 - 2):" (:175, re-asks via :176), right after the  #
# title. play() prompts only for a value its caller did not supply (KTD-4).     #
# --------------------------------------------------------------------------- #
_END_YEAR_PROMPT = "spielende (1928-1978):"
_SCORE_WEIGHT_PROMPT = "punktewertigkeit (0.1 - 2):"


def _play_capturing_state(monkeypatch, stdin_text: str, seconds: float = 20.0, **play_kwargs):
    """Drive the real ``play()`` over EXACT stdin text; return (its state, stdout).

    After setup the first upkeep screen and the map each read a line; the script
    ends there, so both meet EOF (an ack, then a quit) and ``play()`` returns the
    state of the game setup just built -- ``None`` if the session ended before setup
    finished. A SIGALRM deadline turns a re-prompt spin into a failure instead of a
    hung suite.
    """
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys, "stdout", out)

    with deadline(
        seconds,
        f"play() did not return within {seconds}s (EOF spin?)",
        exc_type=AssertionError,
    ):
        state, _rng = play(seed=42, **play_kwargs)
    return state, out.getvalue()


class TestSetupPrompts:
    def test_prompt_texts_render_from_theme(self):
        r = _resolver()
        assert r.resolve("setup.end_year_prompt") == _END_YEAR_PROMPT
        assert r.resolve("setup.score_weight_prompt") == _SCORE_WEIGHT_PROMPT

    def test_end_year_out_of_range_reasks_once(self, monkeypatch):
        """AE4: 1927 is rejected and re-asked; 1940 is accepted."""
        state, text = _play_capturing_state(monkeypatch, "\n1927\n1940\n1\n")
        assert text.count(_END_YEAR_PROMPT) == 2
        assert text.count(_SCORE_WEIGHT_PROMPT) == 1
        assert state is not None
        assert state.clock.end_year == 1940

    def test_end_year_non_numeric_reasks(self, monkeypatch):
        state, text = _play_capturing_state(monkeypatch, "\nabc\n1950\n1\n")
        assert text.count(_END_YEAR_PROMPT) == 2
        assert state.clock.end_year == 1950

    def test_score_weight_out_of_range_reasks_twice(self, monkeypatch):
        """AE7: 0.05 and 2.5 are rejected; 0.5 is accepted as Config.score_mult."""
        state, text = _play_capturing_state(monkeypatch, "\n1940\n0.05\n2.5\n0.5\n")
        assert text.count(_END_YEAR_PROMPT) == 1
        assert text.count(_SCORE_WEIGHT_PROMPT) == 3
        assert state.config.score_mult == 0.5

    def test_score_weight_half_halves_a_score_gain(self, monkeypatch):
        """AE7: with weight 0.5 from setup, a committed score gain of 4 adds 2 to gf."""
        from engine.effects import ScoreAndRank, commit

        state, _text = _play_capturing_state(monkeypatch, "\n1940\n0.5\n")
        before = state.players[0].gf
        after = commit(state, [ScoreAndRank(amount=4, rank_divisor=11.1, player=0)]).state
        assert after.players[0].gf - before == 2

    def test_supplied_values_skip_the_prompts(self, monkeypatch):
        state, text = _play_capturing_state(monkeypatch, "\n", end_year=1950, score_weight=1.5)
        assert _END_YEAR_PROMPT not in text
        assert _SCORE_WEIGHT_PROMPT not in text
        assert state.clock.end_year == 1950
        assert state.config.score_mult == 1.5

    def test_eof_at_end_year_prompt_ends_session_cleanly(self, monkeypatch):
        state, text = _play_capturing_state(monkeypatch, "\n")  # title, then EOF
        assert _END_YEAR_PROMPT in text
        assert "bye." in text
        assert state is None  # no game was set up

    def test_eof_at_score_weight_prompt_ends_session_cleanly(self, monkeypatch):
        state, text = _play_capturing_state(monkeypatch, "\n1940\n")
        assert _SCORE_WEIGHT_PROMPT in text
        assert "bye." in text
        assert state is None


class TestSetupFlags:
    @staticmethod
    def _main(monkeypatch, tmp_path, argv, stdin_text):
        """Run ``main()`` end to end, pressing ``p`` on the first map screen; return
        its stdout and the state it saved."""
        from engine.persistence import load_game

        save = tmp_path / "s.jsonl"
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "main() did not return (spin?)", exc_type=AssertionError):
            main([*argv, "--save", str(save)])
        return out.getvalue(), load_game(save).state

    def test_flags_reach_play(self, monkeypatch, tmp_path):
        # title ack, upkeep ack, p, q -- no setup answers: the flags supply them.
        text, state = self._main(
            monkeypatch, tmp_path, ["--end-year", "1950", "--score-weight", "1.5"], "\n\np\nq\n"
        )
        assert _END_YEAR_PROMPT not in text and _SCORE_WEIGHT_PROMPT not in text
        assert state.clock.end_year == 1950
        assert state.config.score_mult == 1.5

    def test_absent_flags_are_asked_at_setup(self, monkeypatch, tmp_path):
        text, state = self._main(monkeypatch, tmp_path, [], "\n1940\n0.5\n\np\nq\n")
        assert text.count(_END_YEAR_PROMPT) == 1 and text.count(_SCORE_WEIGHT_PROMPT) == 1
        assert state.clock.end_year == 1940
        assert state.config.score_mult == 0.5

    @pytest.mark.parametrize(
        "argv",
        [
            ["--end-year", "1927"],
            ["--end-year", "1979"],
            ["--score-weight", "0.05"],
            ["--score-weight", "2.5"],
        ],
    )
    def test_out_of_range_flag_is_rejected(self, monkeypatch, capsys, argv):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == "", "the game started (the title screen was drawn)"
        # Rejected as out of range -- not merely as an unknown flag (which would
        # also exit non-zero and make this test pass without the feature).
        assert "unrecognized arguments" not in captured.err
        assert argv[0] in captured.err


class TestClientErrorGuard:
    """U9 / KTD-9: known bad inputs end as ONE readable stderr line and a non-zero
    exit -- never a traceback -- while unknown errors keep theirs (AE4, AE6)."""

    @staticmethod
    def _valid_save(path: Path, *, rng_log=()) -> Path:
        from engine.config_loader import load_game_config
        from engine.persistence import save_game

        cfg = load_game_config(CONFIG_DIR)
        state = cfg.module.new_game(
            seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
        )
        save_game(path, state, effect_log=[], rng_log=list(rng_log), seed=42)
        return path

    @staticmethod
    def _fail(capsys, argv) -> tuple[int | str | None, str]:
        with pytest.raises(SystemExit) as exc:
            main(argv)
        return exc.value.code, capsys.readouterr().err

    @staticmethod
    def _assert_one_readable_line(code, err, path) -> None:
        assert code not in (0, None)
        assert "Traceback" not in err
        lines = err.strip().splitlines()
        assert len(lines) == 1, err
        assert lines[0].startswith(f"cannot load {path}: "), err

    def test_missing_load_file_names_it_and_says_not_found(self, capsys, tmp_path):
        path = tmp_path / "missing.jsonl"
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        assert "not found" in err

    def test_load_path_is_a_directory(self, capsys, tmp_path):
        path = tmp_path / "a-dir.jsonl"
        path.mkdir()
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        # The reason only -- tmp_path's own name carries this test's name.
        assert "directory" in err.split(f"{path}: ", 1)[1]

    def test_non_utf8_save(self, capsys, tmp_path):
        path = tmp_path / "binary.jsonl"
        path.write_bytes(b"\xff\xfe\x00")
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        assert "text file" in err.split(f"{path}: ", 1)[1]

    def test_invalid_json_save(self, capsys, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text("{this is not json\n", encoding="utf-8")
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        assert "JSON" in err

    def test_wrong_schema_version_save(self, capsys, tmp_path):
        import json

        path = self._valid_save(tmp_path / "old.jsonl")
        header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        header["version"] = 999
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        assert "999" in err

    def test_snapshot_missing_a_field(self, capsys, tmp_path):
        import json

        path = self._valid_save(tmp_path / "hole.jsonl")
        header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        dropped = next(iter(header["snapshot"]))
        del header["snapshot"][dropped]
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)

    def test_rng_log_not_matching_seed(self, capsys, tmp_path):
        from engine.rng import Rng

        real = Rng(42).range(1000)
        path = self._valid_save(
            tmp_path / "forged.jsonl", rng_log=[("range", (1000,), (real + 1) % 1000)]
        )
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        assert "seed" in err

    def test_out_of_range_end_year_names_the_range(self, capsys, monkeypatch):
        from engine.config_loader import load_game_config

        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        code, err = self._fail(capsys, ["--end-year", "1927"])
        assert code not in (0, None)
        assert "Traceback" not in err
        assert capsys.readouterr().out == "", "the game started"
        bounds = load_game_config(CONFIG_DIR).config["input_ranges"]["end_year"]
        assert f"[{bounds['min']}, {bounds['max']}]" in err

    def test_unknown_error_inside_play_keeps_its_traceback(self, monkeypatch, capsys):
        # Standard input that breaks at the first map prompt of a new game: not a
        # failure main() knows, so it must escape with its traceback, not one line.
        monkeypatch.setattr(sys, "stdin", _FailingStdin(["", ""], RuntimeError("deep bug")))
        with pytest.raises(RuntimeError, match="deep bug") as exc:
            main(["--end-year", "1930", "--score-weight", "1"])
        assert any(entry.name == "readline" for entry in exc.traceback), "not stdin's error"
        assert capsys.readouterr().err == ""

    def test_keyboard_interrupt_exits_quietly_with_cursor_restored(self, monkeypatch, capsys):
        # Ctrl-C at the first map prompt of a new game (title and upkeep acked).
        monkeypatch.setattr(sys, "stdin", _FailingStdin(["", ""], KeyboardInterrupt()))
        with pytest.raises(SystemExit) as exc:
            main(["--end-year", "1930", "--score-weight", "1"])
        assert exc.value.code == 130
        captured = capsys.readouterr()
        assert captured.err == ""
        assert "move: W/A/S/D" in captured.out, "the interrupt did not come at the map"
        assert CURSOR_HIDE in captured.out
        assert captured.out.endswith(CURSOR_SHOW)


class _FailingStdin(io.StringIO):
    """Standard input that answers ``lines``, then raises ``exc`` on the next read:
    Ctrl-C at a prompt (``KeyboardInterrupt``) or a stream that breaks."""

    def __init__(self, lines: list[str], exc: BaseException) -> None:
        super().__init__("".join(f"{line}\n" for line in lines))
        self._exc = exc

    def readline(self, *args) -> str:
        line = super().readline(*args)
        if line == "":
            raise self._exc
        return line


# --------------------------------------------------------------------------- #
# #45 --watch-ai: the opt-in observation frame after each CPU activation        #
# --------------------------------------------------------------------------- #
class TestWatchAi:
    @staticmethod
    def _sides():
        from tests.helpers import combat_fighter as _f

        return (
            (_f(name="hero", weapon=5, energie=20, kraft=34, brutalitaet=28, position=255),),
            (_f(name="ambusher", weapon=6, energie=35, kraft=30, brutalitaet=30, position=262),),
        )

    def _frame(self):
        from engine.interactions import CombatScreen

        return CombatScreen(
            sides=self._sides(), grid=(), active_side=2, active_fighter=1, prompt="observe"
        )

    def _fight(self, inp):
        """Hero (side 1, the client) vs. the real AI on side 2, at Rng(42)."""
        from data.game_configs.mafia_1920s.combat_rules import build_rules
        from engine.interactions import StartCombat
        from engine.rng import Rng

        def handler(ctx):
            result = yield StartCombat(sides=self._sides(), grid=(), rules=build_rules())
            return result

        return run(handler, inp, state=None, rng=Rng(42))

    def test_only_an_opted_in_terminal_input_advertises_the_opt_in(self):
        watching = TerminalInput(resolver=_resolver(), colors=_COLORS, observe_ai=True)
        plain = TerminalInput(resolver=_resolver(), colors=_COLORS)
        assert watching.observes_ai is True
        assert getattr(plain, "observes_ai", False) is False

    def test_observe_frame_renders_the_board_and_consumes_exactly_one_line(self):
        inp, out = _client(["x", "w"])
        assert inp(self._frame()) is None
        text = out.getvalue()
        assert _resolver().resolve("combat.observe_prompt") in text
        assert _resolver().resolve("combat.key_legend") not in text, "not an action prompt"
        assert "ambusher" in text, "the acting CPU fighter's panel is drawn"
        assert inp._stdin.readline() == "w\n", "the frame must consume one line, no more"

    def test_eof_at_an_observe_frame_continues_instead_of_ending_input(self):
        inp, _out = _eof_client("")
        assert inp(self._frame()) is None  # no EndOfInput, no CANCEL/surrender

    def test_a_watched_fight_shows_one_frame_per_cpu_move_through_the_real_driver(self):
        # Human turn = fire + aim right ("f", "d"); each CPU activation = one key (".").
        out = io.StringIO()
        inp = TerminalInput(
            resolver=_resolver(),
            colors=_COLORS,
            stdin=io.StringIO("f\nd\n.\n" * 30),
            stdout=out,
            observe_ai=True,
        )
        result = self._fight(inp)
        assert result.status == "completed"
        winner = result.payload.returned.winner
        frames = out.getvalue().count(_resolver().resolve("combat.observe_prompt"))
        human_turns = out.getvalue().count(_resolver().resolve("combat.key_legend"))
        assert frames >= 3
        # Human first each round and the CPU lands the last shot (seed 42): one frame
        # per CPU activation, one per human turn, and no key was stolen from a turn.
        assert winner == 2
        assert frames == human_turns

    def test_eof_mid_watched_fight_surrenders_at_the_next_real_prompt(self):
        # One human turn, then EOF: the observe frame after the CPU's reply continues,
        # and the next action prompt meets EOF -> surrender (KTD-2), side 2 wins.
        out = io.StringIO()
        inp = TerminalInput(
            resolver=_resolver(),
            colors=_COLORS,
            stdin=io.StringIO("f\nd\n"),
            stdout=out,
            observe_ai=True,
        )
        result = self._fight(inp)
        assert result.payload.returned.winner == 2
        assert out.getvalue().count(_resolver().resolve("combat.observe_prompt")) == 1

    @staticmethod
    def _shift_fight_output(monkeypatch, argv: list[str]) -> str:
        """``main(argv)`` over a seed-5 game whose second turn is a bouncer shift fight
        (as in ``TestJobShiftThroughClient``): walk to the pub, take the job, end the
        turn, then pass six times in the fight until input runs out (a surrender)."""
        from engine.movement import load_city
        from tests.test_client_loop import (
            find_door_cell,
            load_city_raw,
            new_state,
            walk_keys_to_cell,
        )

        city_raw = load_city_raw()
        city = load_city(city_raw)
        walk = walk_keys_to_cell(new_state(5), city, find_door_cell(city_raw, "pub", ln=2))
        keys = ["", ""] + walk + ["", "2", "j", "w", "x", "x", "x"] + ["p"] * 6
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(keys) + "\n"))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "main() did not return (spin?)", exc_type=AssertionError):
            main([*argv, "--seed", "5", "--end-year", "1930", "--score-weight", "1"])
        return out.getvalue()

    def test_watch_ai_shows_the_board_after_each_cpu_move_in_a_real_fight(self, monkeypatch):
        text = self._shift_fight_output(monkeypatch, ["--watch-ai"])
        assert _resolver().resolve("combat.key_legend") in text, "the fight never started"
        assert text.count(_resolver().resolve("combat.observe_prompt")) >= 1

    def test_without_watch_ai_no_observation_frame_is_shown(self, monkeypatch):
        text = self._shift_fight_output(monkeypatch, [])
        assert _resolver().resolve("combat.key_legend") in text, "the fight never started"
        assert _resolver().resolve("combat.observe_prompt") not in text


# --------------------------------------------------------------------------- #
# Client text lives in the theme (client.yaml), not in the client's code.     #
# --------------------------------------------------------------------------- #
class TestClientTextComesFromTheTheme:
    _CLIENT_YAML = _CONFIG_DIR / "themes" / "classic" / "strings" / "client.yaml"

    @staticmethod
    def _leaf_keys(tree: dict, prefix: str) -> list[str]:
        keys = []
        for name, value in tree.items():
            key = f"{prefix}.{name}"
            keys.extend(
                TestClientTextComesFromTheTheme._leaf_keys(value, key)
                if isinstance(value, dict)
                else [key]
            )
        return keys

    #: A theme directory outside the config: it rewords ``bye``, the map hint, the
    #: turn-over summary and the load error, and leaves every other key to classic.
    _TEST_THEME = Path(__file__).resolve().parent / "fixtures" / "themes" / "test"

    @staticmethod
    def _main(monkeypatch, argv: list[str], lines: list[str]) -> str:
        """``main(argv)`` over EXACT stdin ``lines``; return its stdout."""
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "main() did not return", exc_type=AssertionError):
            main(argv)
        return out.getvalue()

    def test_every_client_key_resolves(self):
        import string

        resolver = _resolver()
        keys = self._leaf_keys(_yaml_load(self._CLIENT_YAML)["client"], "client")
        assert "client.bye" in keys and "client.turn_over.summary" in keys
        for key in keys:
            template: Any = resolver.tree
            for segment in key.split("."):
                template = template[segment]
            params = {f: "x" for _, f, _, _ in string.Formatter().parse(template) if f}
            assert resolver.resolve(key, params).strip(), key

    def test_a_theme_path_changes_what_a_quit_prints(self, monkeypatch):
        argv = ["--theme", str(self._TEST_THEME), "--end-year", "1930", "--score-weight", "1"]
        text = self._main(monkeypatch, argv, ["", "", "q"])
        assert "ciao." in text and "bye." not in text
        assert "walk on." in text and "move: W/A/S/D" not in text
        # Keys the theme leaves alone still come from classic.
        assert "press any key..." in text

    def test_a_theme_path_changes_the_turn_over_labels(self, monkeypatch):
        from tests.test_client_loop import burn_turn_keys

        # Walk the first turn to its turn-over screen and quit there.
        walk = burn_turn_keys(42, turns=1)[:-3]
        argv = ["--theme", str(self._TEST_THEME), "--seed", "42", "--end-year", "1930"]
        text = self._main(monkeypatch, [*argv, "--score-weight", "1"], ["", "", *walk, "q"])
        turn_over = text[text.index("  turn_over  ") :]
        assert re.search(r"geld: \d+\$ \| feld \d+ \| schritte 0 \| rang 1", turn_over)
        assert "cash:" not in turn_over and "movement:" not in turn_over
        assert turn_over.endswith("ciao.\n" + CURSOR_SHOW)

    def test_a_theme_path_changes_the_load_error_line(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            main(["--theme", str(self._TEST_THEME), "--load", "/nonexistent.jsonl"])
        err = capsys.readouterr().err
        assert err.strip() == "kaputt /nonexistent.jsonl -- weg"

    def test_an_unknown_theme_is_one_readable_line(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        with pytest.raises(SystemExit) as exc:
            main(["--theme", "nosuch"])
        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert captured.out == "", "the game started"
        lines = captured.err.strip().splitlines()
        assert len(lines) == 1, captured.err
        assert lines[0].startswith("cannot load theme nosuch: ")
        assert "Traceback" not in captured.err

    def test_the_default_theme_is_classic(self, monkeypatch):
        by_name = self._main(
            monkeypatch,
            ["--theme", "classic", "--end-year", "1930", "--score-weight", "1"],
            ["", "", "q"],
        )
        default = self._main(
            monkeypatch, ["--end-year", "1930", "--score-weight", "1"], ["", "", "q"]
        )
        assert by_name == default
        assert "bye." in default

    def test_the_status_bar_reads_the_session_resolver(self):
        from clients.terminal.renderers import render_status_bar

        override = _resolver().with_override({"client": {"status_bar": "[{name}/{cash}]"}})
        buf = io.StringIO()
        render_status_bar("alcapone", 5400, 181, 19, buf, resolver=override, colors=_COLORS)
        assert "[alcapone/5400]" in buf.getvalue() and "cash" not in buf.getvalue()


# --------------------------------------------------------------------------- #
# --theme: name vs path, broken themes, the theme's palette, colour detection. #
# --------------------------------------------------------------------------- #
class TestThemeSelection:
    """``--theme`` resolves a bare name as a config theme and only a path-shaped value
    (a separator, or a leading ``.`` or ``~``) as a directory (KTD-5); the chosen
    theme's palette colours the screens; colour support is read once per session."""

    _TEST_THEME = TestClientTextComesFromTheTheme._TEST_THEME
    _NEW_GAME = ["--end-year", "1930", "--score-weight", "1"]
    _TOP_BORDER = "╔" + "═" * 40 + "╗"

    @staticmethod
    def _main(monkeypatch, argv: list[str], lines: list[str]) -> str:
        return TestClientTextComesFromTheTheme._main(monkeypatch, argv, lines)

    @staticmethod
    def _fails_with_one_line(argv: list[str], capsys) -> str:
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert captured.out == "", "the game started"
        assert "Traceback" not in captured.err
        lines = captured.err.strip().splitlines()
        assert len(lines) == 1, captured.err
        return lines[0]

    def test_a_classic_directory_in_the_cwd_does_not_shadow_the_default_theme(
        self, monkeypatch, tmp_path
    ):
        (tmp_path / "classic").mkdir()
        monkeypatch.chdir(tmp_path)
        default = self._main(monkeypatch, self._NEW_GAME, ["", "", "q"])
        assert default.endswith("bye.\n" + CURSOR_SHOW)
        by_name = self._main(monkeypatch, ["--theme", "classic", *self._NEW_GAME], ["", "", "q"])
        assert by_name == default

    def test_a_dot_prefixed_value_is_a_path(self, monkeypatch, tmp_path):
        """``.mytheme`` has no separator; the leading dot alone makes it a path."""
        (tmp_path / ".mytheme").symlink_to(self._TEST_THEME, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        text = self._main(monkeypatch, ["--theme", ".mytheme", *self._NEW_GAME], ["", "", "q"])
        assert text.endswith("ciao.\n" + CURSOR_SHOW)

    @pytest.mark.parametrize("value", ["~", "~/mytheme"])
    def test_a_tilde_prefixed_value_is_a_path_under_home(self, monkeypatch, tmp_path, value):
        """``~`` alone has no separator; the leading tilde makes it a path, and it
        expands to ``$HOME``."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "mytheme").symlink_to(self._TEST_THEME, target_is_directory=True)
        monkeypatch.setenv("HOME", str(home / "mytheme") if value == "~" else str(home))
        text = self._main(monkeypatch, ["--theme", value, *self._NEW_GAME], ["", "", "q"])
        assert text.endswith("ciao.\n" + CURSOR_SHOW)

    def test_a_theme_file_with_a_list_root_is_one_readable_line(self, tmp_path, capsys):
        (tmp_path / "strings").mkdir()
        (tmp_path / "strings" / "x.yaml").write_text("- one\n- two\n", encoding="utf-8")
        line = self._fails_with_one_line(["--theme", str(tmp_path)], capsys)
        assert line.startswith(f"cannot load theme {tmp_path}: ")
        assert "x.yaml" in line

    def test_the_themes_palette_colours_the_screens(self, monkeypatch, truecolor):
        """The fixture theme's palette sets ``red`` (the player on the map, the title)
        and ``light_grey`` (the map background, headers); ``dark_grey`` (the map
        border) is left to classic's palette."""
        themed = self._main(
            monkeypatch, ["--theme", str(self._TEST_THEME), *self._NEW_GAME], ["", "", "q"]
        )
        classic = self._main(monkeypatch, self._NEW_GAME, ["", "", "q"])
        red, grey_bg = "\033[38;2;1;2;3m", "\033[48;2;4;5;6m"
        classic_red, classic_grey_bg = "\033[38;2;158;52;38m", "\033[48;2;178;178;178m"
        border = "\033[38;2;82;82;82m"  # classic's dark_grey, kept by the theme
        assert red in themed and grey_bg in themed and border in themed
        assert classic_red not in themed and classic_grey_bg not in themed
        assert classic_red in classic and classic_grey_bg in classic
        assert red not in classic

    def test_colour_support_is_read_once_per_session(self, monkeypatch, hostile_color_env):
        """Detection happens when the session starts: a terminal does not change what
        it can show mid-game, and frames must not switch colour modes half-way. An env
        change during a session is ignored; the next session sees it."""

        class FlipAtFirstMapKey(io.StringIO):
            """Scripted stdin that turns truecolor on as the first map key is read."""

            reads = 0

            def readline(self, *args):
                FlipAtFirstMapKey.reads += 1
                if FlipAtFirstMapKey.reads == 3:  # 1 title, 2 upkeep, 3 first map key
                    monkeypatch.setenv("COLORTERM", "truecolor")
                return super().readline(*args)

        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", FlipAtFirstMapKey("\n\nx\nx\nq\n"))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "main() did not return", exc_type=AssertionError):
            main(self._NEW_GAME)
        first = out.getvalue()
        assert FlipAtFirstMapKey.reads >= 5, "the script did not reach the later map frames"
        _ansi = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
        frames = [line for line in first.split("\n") if _ansi.sub("", line) == self._TOP_BORDER]
        assert len(frames) == 3, "expected three map frames: before and after the flip"
        assert "\033[38;5;" in first
        assert "\033[38;2;" not in first, "colours switched mode mid-session"

        second = self._main(monkeypatch, self._NEW_GAME, ["", "", "q"])
        assert "\033[38;2;" in second and "\033[38;5;" not in second

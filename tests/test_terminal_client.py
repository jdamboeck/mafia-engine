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
    CLEAR,
    CONFIG_DIR,
    CURSOR_HIDE,
    CURSOR_SHOW,
    EndOfInput,
    TerminalInput,
    main,
    play,
    render_message,
)
from clients.terminal.palette import ColorSupport, Colors, load_palette  # noqa: E402
from tests.helpers import NEW_GAME_ACKS, SOLO, deadline, make_walk_script  # noqa: E402

#: ``--player`` for :data:`~tests.helpers.SOLO`: a ``main()`` run that names its player
#: is asked no player count and no names (its script is :data:`NEW_GAME_ACKS`).
_SOLO_ARGS = ["--player", ":".join(SOLO[0])]

_CITY_YAML = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _plain(text: str) -> str:
    """``text`` without its ANSI colour and cursor codes."""
    return re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", text)


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
    assert "das 500 $ miete" in out.getvalue()  # :10020 "kostet das"p"$": PRINT's spacing


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
# The map loop: crosses client -> movement; adopts result.state, renders it.    #
# --------------------------------------------------------------------------- #
def test_the_map_loop_adopts_the_moved_state_and_renders_it(monkeypatch):
    """``play()``'s map loop holds no rules: a walking key moves the player through
    ``try_move``, the client adopts the pure ``result.state``, draws it, and the quit
    key ends the session. Real movement through the engine layer."""
    from engine.config_loader import load_game_config
    from engine.movement import try_move

    cfg = load_game_config(_CONFIG_DIR)
    city = load_city(_yaml_load(_CITY_YAML))
    start = cfg.module.new_game(
        seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )
    # The engine says where one step down lands; the test does not hardcode the map.
    moved = try_move(start, city, DOWN)
    assert moved.payload.kind == "step"
    before, after = start.players[0], moved.state.players[0]
    assert after.po != before.po

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", make_walk_script(["s", "q"]))
    monkeypatch.setattr(sys, "stdout", out)
    with deadline(20.0, "play() did not return (EOF spin?)", exc_type=AssertionError):
        final, _rng = play(seed=42, end_year=1930, score_weight=1.0, players=SOLO)
    # One real step happened and the client adopted the new pure state (po moved, ms spent).
    assert final.players[0].po == after.po
    assert final.players[0].ms == before.ms - 1
    # The map was drawn before the step and redrawn from the adopted state after it.
    text = out.getvalue()
    drawn_before = text.index(f"pos {before.po} ")
    assert text.index(f"pos {after.po} ", drawn_before) > drawn_before
    assert f"ms {before.ms - 1} " in text


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

    The game is :data:`~tests.helpers.SOLO`'s unless ``players`` is given. After
    setup the eigenschaften screen, the first upkeep screen and the turn menu each read
    a line; the script ends there, so they meet EOF (acks, then a quit) and ``play()``
    returns the state of the game setup just built -- ``None`` if the session ended
    before setup finished. A SIGALRM deadline turns a re-prompt spin into a failure
    instead of a hung suite.
    """
    play_kwargs.setdefault("players", SOLO)
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
        state, text = _play_capturing_state(monkeypatch, "\n1927\n1940\n1\n\n")
        assert text.count(_END_YEAR_PROMPT) == 2
        assert text.count(_SCORE_WEIGHT_PROMPT) == 1
        assert state is not None
        assert state.clock.end_year == 1940

    def test_end_year_non_numeric_reasks(self, monkeypatch):
        state, text = _play_capturing_state(monkeypatch, "\nabc\n1950\n1\n\n")
        assert text.count(_END_YEAR_PROMPT) == 2
        assert state.clock.end_year == 1950

    def test_score_weight_out_of_range_reasks_twice(self, monkeypatch):
        """AE7: 0.05 and 2.5 are rejected; 0.5 is accepted as formula_params["score_mult"]."""
        state, text = _play_capturing_state(monkeypatch, "\n1940\n0.05\n2.5\n0.5\n\n")
        assert text.count(_END_YEAR_PROMPT) == 1
        assert text.count(_SCORE_WEIGHT_PROMPT) == 3
        assert state.config.formula_params["score_mult"] == 0.5

    def test_score_weight_half_halves_a_score_gain(self, monkeypatch):
        """AE7: with weight 0.5 from setup, a committed score gain of 4 adds 2 to gf."""
        from engine.effects import commit
        from data.game_configs.mafia_1920s.effects import ScoreAndRank

        state, _text = _play_capturing_state(monkeypatch, "\n1940\n0.5\n\n")
        before = state.players[0].gf
        after = commit(state, [ScoreAndRank(amount=4, rank_divisor=11.1, player=0)]).state
        assert after.players[0].gf - before == 2

    def test_supplied_values_skip_the_prompts(self, monkeypatch):
        state, text = _play_capturing_state(monkeypatch, "\n\n", end_year=1950, score_weight=1.5)
        assert _END_YEAR_PROMPT not in text
        assert _SCORE_WEIGHT_PROMPT not in text
        assert state.clock.end_year == 1950
        assert state.config.formula_params["score_mult"] == 1.5

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
        from engine.config_loader import load_game_config
        from engine.persistence import load_game

        save = tmp_path / "s.jsonl"
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "main() did not return (spin?)", exc_type=AssertionError):
            main([*argv, "--save", str(save)])
        return out.getvalue(), load_game(save, load_game_config(CONFIG_DIR).registries).state

    def test_flags_reach_play(self, monkeypatch, tmp_path):
        # title ack, house-rules offer, eigenschaften key, upkeep ack, p, q -- no setup
        # answers: the flags supply them.
        text, state = self._main(
            monkeypatch,
            tmp_path,
            [*_SOLO_ARGS, "--end-year", "1950", "--score-weight", "1.5"],
            "\n\n\n\np\nq\n",
        )
        assert _END_YEAR_PROMPT not in text and _SCORE_WEIGHT_PROMPT not in text
        assert state.clock.end_year == 1950
        assert state.config.formula_params["score_mult"] == 1.5

    def test_absent_flags_are_asked_at_setup(self, monkeypatch, tmp_path):
        text, state = self._main(monkeypatch, tmp_path, _SOLO_ARGS, "\n1940\n0.5\n\n\n\np\nq\n")
        assert text.count(_END_YEAR_PROMPT) == 1 and text.count(_SCORE_WEIGHT_PROMPT) == 1
        assert state.clock.end_year == 1940
        assert state.config.formula_params["score_mult"] == 0.5

    @pytest.mark.parametrize("text", ["1.99", "0.11", "1.5", "1.398259791907483378"])
    def test_the_flag_and_the_prompt_reach_the_same_weight(self, monkeypatch, tmp_path, text):
        """``--score-weight`` and the ``:175`` prompt parse the same text the same way:
        under the faithful ``c64_float_score``, the C64 parser's value (``1.99`` and
        ``0.11`` are a unit off the nearest 5-byte value, tests/test_c64_float.py). The
        long one parses elsewhere than its double's shortest text would (the parse
        capture), so the flag must hand over the text, not a float."""
        from engine.c64_numbers import c64_val

        _, flagged = self._main(
            monkeypatch,
            tmp_path,
            [*_SOLO_ARGS, "--end-year", "1950", "--score-weight", text],
            "\n\n\n\np\nq\n",
        )
        stdin = f"\n1950\n{text}\n\n\n\np\nq\n"
        _, prompted = self._main(monkeypatch, tmp_path, _SOLO_ARGS, stdin)
        weights = {state.config.formula_params["score_mult"] for state in (flagged, prompted)}
        assert weights == {c64_val(text)}

    @pytest.mark.parametrize("text", ["1.5x", "1.1_5", "1" + "0" * 40 + "e-40"])
    def test_a_score_weight_flag_that_is_no_number_is_refused(self, monkeypatch, capsys, text):
        # "1.1_5" and the 41-digit text are numbers to float() but not to the setup's
        # reading (the C64 parser): refused here, not as a traceback after the title.
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        with pytest.raises(SystemExit) as exc:
            main(["--score-weight", text])
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == "", "the game started (the title screen was drawn)"
        assert "--score-weight" in captured.err and text in captured.err
        assert "Traceback" not in captured.err

    @pytest.mark.parametrize("spec", ["", ":gang", "a" * 14, "name:" + "g" * 14])
    def test_a_player_name_over_13_characters_or_empty_is_rejected(self, monkeypatch, capsys, spec):
        # :291 ``ifx$=""orlen(x$)>13`` bounds both names; the flag is refused, not run.
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        with pytest.raises(SystemExit) as exc:
            main(["--player", spec])
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == "", "the game started (the title screen was drawn)"
        assert "1 to 13 characters" in captured.err

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
        save_game(
            path, state, registries=cfg.registries, effect_log=[], rng_log=list(rng_log), seed=42
        )
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

    @staticmethod
    def _rewrite_header(path: Path, **changes) -> None:
        import json

        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = json.dumps({**json.loads(lines[0]), **changes})
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_ae6_a_version_1_save_says_it_was_made_by_an_older_version(self, capsys, tmp_path):
        """AE6: a save written before the version bump is one line naming it older,
        exit status 1, no traceback."""
        path = self._valid_save(tmp_path / "v1.jsonl")
        self._rewrite_header(path, version=1)
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        assert code == 1
        assert err == (
            f"cannot load {path}: the save was made by an older version of the game "
            "(save version 1; this version reads 2)\n"
        )

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        [
            ("config_id", "chicago_1930s", "the save belongs to another game"),
            ("content_version", 99, "the save was made for another content version"),
        ],
    )
    def test_a_save_from_another_config_or_content_version(
        self, capsys, tmp_path, field, value, reason
    ):
        path = self._valid_save(tmp_path / "other.jsonl")
        self._rewrite_header(path, **{field: value})
        code, err = self._fail(capsys, ["--load", str(path)])
        self._assert_one_readable_line(code, err, path)
        assert code == 1
        assert err.split(f"{path}: ", 1)[1].startswith(reason), err
        assert str(value) in err

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
        monkeypatch.setattr(
            sys, "stdin", _FailingStdin([*NEW_GAME_ACKS, "2"], RuntimeError("deep bug"))
        )
        with pytest.raises(RuntimeError, match="deep bug") as exc:
            main([*_SOLO_ARGS, "--end-year", "1930", "--score-weight", "1"])
        assert any(entry.name == "readline" for entry in exc.traceback), "not stdin's error"
        assert capsys.readouterr().err == ""

    def test_keyboard_interrupt_exits_quietly_with_cursor_restored(self, monkeypatch, capsys):
        # Ctrl-C at the first map prompt of a new game (title, house rules, upkeep acked).
        monkeypatch.setattr(sys, "stdin", _FailingStdin([*NEW_GAME_ACKS, "2"], KeyboardInterrupt()))
        with pytest.raises(SystemExit) as exc:
            main([*_SOLO_ARGS, "--end-year", "1930", "--score-weight", "1"])
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
            result = yield StartCombat(sides=self._sides(), grid=(), rules=build_rules({}))
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
        # The pub's menu: 1 drink, 2 recruit, 3 tip, 4 job, 5 leave.
        keys = [*NEW_GAME_ACKS, "2"] + walk + ["", "4", "j", "w", "x", "x", "x"] + ["p"] * 6
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(keys) + "\n"))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "main() did not return (spin?)", exc_type=AssertionError):
            main([*argv, *_SOLO_ARGS, "--seed", "5", "--end-year", "1930", "--score-weight", "1"])
        return out.getvalue()

    def test_watch_ai_shows_the_board_after_each_cpu_move_in_a_real_fight(self, monkeypatch):
        text = self._shift_fight_output(monkeypatch, ["--watch-ai"])
        assert _resolver().resolve("combat.key_legend") in text, "the fight never started"
        assert text.count(_resolver().resolve("combat.observe_prompt")) >= 1

    def test_without_watch_ai_no_observation_frame_is_shown(self, monkeypatch):
        text = self._shift_fight_output(monkeypatch, [])
        assert _resolver().resolve("combat.key_legend") in text, "the fight never started"
        assert _resolver().resolve("combat.observe_prompt") not in text

    def test_a_fight_opens_on_both_gang_names_and_the_begins_banner(self, monkeypatch):
        """:30015 ``printbn$(ks(i))`` -- side 1's gang at column 0, side 2's at column
        20, under the board on every screen; :30025 ``print"{rvon}der kampf
        beginnt..."`` on the first screen only."""
        text = self._shift_fight_output(monkeypatch, [])
        boards = text.split(_resolver().resolve("combat.key_legend"))[:-1]
        assert len(boards) >= 2, "the fight never reached a second activation"
        assert "der kampf beginnt..." in boards[0]
        assert all("der kampf beginnt..." not in board for board in boards[1:])
        # :25035-25042: seed 5's bouncer shift draws one of the three brawlers.
        brawlers = ("wurstfinger-fred", "affenface-alf", "der schlachter")
        rows = {f"{SOLO[0][1]:<20}{name}" for name in brawlers}
        for board in boards:
            assert rows & set(_plain(board).split("\n")), board

    def test_a_cpu_activation_leaves_spieler_and_its_number_on_the_board(self, monkeypatch):
        """:30400 ``poke211,20:poke214,18:syscs:print"{rvon}spieler"f`` on every CPU
        activation: column 20, the number with PRINT's spaces. Nothing erases it, so
        the human's next board shows it; the first board, before any CPU move, does not."""
        text = self._shift_fight_output(monkeypatch, [])
        boards = [
            _plain(b).split("\n") for b in text.split(_resolver().resolve("combat.key_legend"))[:-1]
        ]
        label = " " * 20 + "spieler 1 "
        assert label not in boards[0]
        assert all(label in board for board in boards[1:])


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
        argv = ["--theme", str(self._TEST_THEME), *_SOLO_ARGS, "--end-year", "1930"]
        argv += ["--score-weight", "1"]
        text = self._main(monkeypatch, argv, [*NEW_GAME_ACKS, "2", "q"])
        assert "ciao." in text and "bye." not in text
        assert "walk on." in text and "move: W/A/S/D" not in text
        # Keys the theme leaves alone still come from classic.
        assert "press any key..." in text

    def test_a_theme_path_changes_the_turn_over_labels(self, monkeypatch):
        from tests.test_client_loop import burn_turn_keys

        # Walk the first turn to its turn-over screen and quit there.
        walk = burn_turn_keys(42, turns=1)[:-3]
        argv = ["--theme", str(self._TEST_THEME), *_SOLO_ARGS, "--seed", "42", "--end-year", "1930"]
        text = self._main(
            monkeypatch, [*argv, "--score-weight", "1"], [*NEW_GAME_ACKS, "2", *walk, "q"]
        )
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
            ["--theme", "classic", *_SOLO_ARGS, "--end-year", "1930", "--score-weight", "1"],
            [*NEW_GAME_ACKS, "q"],
        )
        default = self._main(
            monkeypatch,
            [*_SOLO_ARGS, "--end-year", "1930", "--score-weight", "1"],
            [*NEW_GAME_ACKS, "q"],
        )
        assert by_name == default
        assert "bye." in default

    def test_the_status_bar_reads_the_session_resolver(self):
        from clients.terminal.renderers import render_status_bar

        override = _resolver().with_override({"client": {"status_bar": "[{name}/{cash:raw}]"}})
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
    _NEW_GAME = [*_SOLO_ARGS, "--end-year", "1930", "--score-weight", "1"]
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
        default = self._main(monkeypatch, self._NEW_GAME, [*NEW_GAME_ACKS, "q"])
        assert default.endswith("bye.\n" + CURSOR_SHOW)
        by_name = self._main(
            monkeypatch, ["--theme", "classic", *self._NEW_GAME], [*NEW_GAME_ACKS, "q"]
        )
        assert by_name == default

    def test_a_dot_prefixed_value_is_a_path(self, monkeypatch, tmp_path):
        """``.mytheme`` has no separator; the leading dot alone makes it a path."""
        (tmp_path / ".mytheme").symlink_to(self._TEST_THEME, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        text = self._main(
            monkeypatch, ["--theme", ".mytheme", *self._NEW_GAME], [*NEW_GAME_ACKS, "q"]
        )
        assert text.endswith("ciao.\n" + CURSOR_SHOW)

    @pytest.mark.parametrize("value", ["~", "~/mytheme"])
    def test_a_tilde_prefixed_value_is_a_path_under_home(self, monkeypatch, tmp_path, value):
        """``~`` alone has no separator; the leading tilde makes it a path, and it
        expands to ``$HOME``."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "mytheme").symlink_to(self._TEST_THEME, target_is_directory=True)
        monkeypatch.setenv("HOME", str(home / "mytheme") if value == "~" else str(home))
        text = self._main(monkeypatch, ["--theme", value, *self._NEW_GAME], [*NEW_GAME_ACKS, "q"])
        assert text.endswith("ciao.\n" + CURSOR_SHOW)

    def test_a_theme_file_with_a_list_root_is_one_readable_line(self, tmp_path, capsys):
        (tmp_path / "strings").mkdir()
        (tmp_path / "strings" / "x.yaml").write_text("- one\n- two\n", encoding="utf-8")
        line = self._fails_with_one_line(["--theme", str(tmp_path)], capsys)
        assert line.startswith(f"cannot load theme {tmp_path}: ")
        assert "x.yaml" in line

    # A broken CLASSIC theme -- the one every line, --help included, is worded in.
    @staticmethod
    def _classic_strings(tmp_path, monkeypatch, files: dict[str, str]) -> Path:
        """A config dir whose classic theme holds only ``files``, installed as the
        client's config; returns that theme's strings dir."""
        import clients.terminal.cli as cli

        strings = tmp_path / "cfg" / "themes" / "classic" / "strings"
        strings.mkdir(parents=True)
        for name, body in files.items():
            (strings / name).write_text(body, encoding="utf-8")
        monkeypatch.setattr(cli, "_CONFIG_DIR", tmp_path / "cfg")
        return strings

    def test_a_corrupt_classic_theme_still_prints_help(self, monkeypatch, tmp_path, capsys):
        strings = self._classic_strings(tmp_path, monkeypatch, {"client.yaml": "- broken\n"})
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        # Every flag is listed, and the built-in description says why there is no help.
        for flag in ("--seed", "--player", "--end-year", "--load", "--save", "--theme"):
            assert flag in captured.out
        # argparse wraps the description; the path has no whitespace of its own.
        assert str(strings / "client.yaml") in "".join(captured.out.split())

    def test_a_list_topped_classic_strings_file_is_one_line_naming_it(
        self, monkeypatch, tmp_path, capsys
    ):
        strings = self._classic_strings(tmp_path, monkeypatch, {"client.yaml": "- broken\n"})
        line = self._fails_with_one_line(["--end-year", "1930"], capsys)
        assert line.startswith(f"{strings / 'client.yaml'}: ")
        assert "mapping" in line

    def test_a_help_string_containing_percent_prints(self, monkeypatch, tmp_path, capsys):
        import shutil

        cfg = tmp_path / "cfg" / "themes" / "classic"
        shutil.copytree(_CONFIG_DIR / "themes" / "classic" / "strings", cfg / "strings")
        client = cfg / "strings" / "client.yaml"
        tree = _yaml_load(client)
        tree["client"]["cli"]["help_seed"] = "100% reproducible from seed {seed} (50 % chance)"
        client.write_text(yaml.safe_dump(tree, allow_unicode=True), encoding="utf-8")
        import clients.terminal.cli as cli

        monkeypatch.setattr(cli, "_CONFIG_DIR", tmp_path / "cfg")
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = " ".join(capsys.readouterr().out.split())  # argparse wraps help lines
        assert "100% reproducible from seed 42 (50 % chance)" in out

    def test_the_themes_palette_colours_the_screens(self, monkeypatch, truecolor):
        """The fixture theme's palette sets ``red`` (the player on the map, the title)
        and ``light_grey`` (the map background, headers); ``dark_grey`` (the map
        border) is left to classic's palette."""
        themed = self._main(
            monkeypatch,
            ["--theme", str(self._TEST_THEME), *self._NEW_GAME],
            [*NEW_GAME_ACKS, "2", "q"],
        )
        classic = self._main(monkeypatch, self._NEW_GAME, [*NEW_GAME_ACKS, "2", "q"])
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
                # 1 title, 2 house rules, 3 eigenschaften, 4 upkeep, 5 the turn menu
                # (walk), 6 first map key
                if FlipAtFirstMapKey.reads == 6:
                    monkeypatch.setenv("COLORTERM", "truecolor")
                return super().readline(*args)

        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", FlipAtFirstMapKey("\n\n\n\n2\nx\nx\nq\n"))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "main() did not return", exc_type=AssertionError):
            main(self._NEW_GAME)
        first = out.getvalue()
        assert FlipAtFirstMapKey.reads >= 8, "the script did not reach the later map frames"
        _ansi = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
        frames = [line for line in first.split("\n") if _ansi.sub("", line) == self._TOP_BORDER]
        assert len(frames) == 3, "expected three map frames: before and after the flip"
        assert "\033[38;5;" in first
        assert "\033[38;2;" not in first, "colours switched mode mid-session"

        second = self._main(monkeypatch, self._NEW_GAME, [*NEW_GAME_ACKS, "2", "q"])
        assert "\033[38;2;" in second and "\033[38;5;" not in second


# --------------------------------------------------------------------------- #
# Multiplayer setup (U3 of the multiplayer-setup plan): a plain start asks the  #
# player count (:205-206), each player's name and gang name (:210-215,          #
# :290-292) and runs each player's eigenschaften screen (:300-316). The setup   #
# handler asks; the client only draws it.                                       #
# --------------------------------------------------------------------------- #
_COUNT_PROMPT = "spieleranzahl:"
_EIGENSCHAFTEN = "eigenschaften:"
#: The flags that pre-fill the end year and the score weight (the players stay asked).
_SETUP_FLAGS = ["--end-year", "1930", "--score-weight", "1"]


def _name_prompt(number: int) -> str:
    return _resolver().resolve("setup.player_name_prompt", {"number": number})


def _stopped_rolls(text: str) -> list[tuple[int, int, int]]:
    """``(kraft, intelligenz, brutalitaet)`` each eigenschaften screen kept, in order.

    A roll's kept frame is the one its line ends on: a frame the roll went past is
    redrawn in place (no line break after it). The labels come from the theme.
    """
    plain = re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", text)
    kept = {}
    for stat in ("kraft", "intelligenz", "brutalitaet"):
        label = _resolver().resolve(f"setup.roll.{stat}", {"value": 98765}).split("98765")[0]
        frames = re.findall(re.escape(label.strip()) + r" *(\d+) *\n", plain)
        kept[stat] = [int(v) for v in frames]
    return list(zip(kept["kraft"], kept["intelligenz"], kept["brutalitaet"]))


def _stats(player) -> tuple[int, int, int]:
    attrs = player.roster[0].attrs
    return attrs["kraft"], attrs["intelligenz"], attrs["brutalitaet"]


class TestMultiplayerSetup:
    """A plain start sets up a hot-seat game through the source's setup screens."""

    @staticmethod
    def _main(monkeypatch, tmp_path, argv, lines, *, name="s.jsonl"):
        """``main(argv + --save)`` over exactly ``lines``; return stdout and the save
        (``None`` when nothing was saved)."""
        from engine.config_loader import load_game_config
        from engine.persistence import load_game

        save = tmp_path / name
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(30, "main() did not return (a re-ask spin?)", exc_type=AssertionError):
            main([*argv, "--save", str(save)])
        loaded = load_game(save, load_game_config(CONFIG_DIR).registries) if save.exists() else None
        return out.getvalue(), loaded

    #: Two players asked: title, house-rules offer, the count, then per player the
    #: name, the gang name and the eigenschaften key; then the first upkeep.
    _TWO_ASKED = ["", "", "2", "anna", "die bande", "", "bert", "das syndikat", "", ""]

    def test_two_asked_players_both_take_turns(self, monkeypatch, tmp_path):
        """Covers AE1: the count and both names are asked; both players play the round."""
        # At anna's turn menu: 4 (next player), then acks until bert's menu, where p saves.
        lines = [*self._TWO_ASKED, "4", "x", "x", "p", "q"]
        text, loaded = self._main(monkeypatch, tmp_path, ["--seed", "3", *_SETUP_FLAGS], lines)
        assert text.count(_COUNT_PROMPT) == 1
        assert _name_prompt(1) in text and _name_prompt(2) in text
        assert text.count(_EIGENSCHAFTEN) == 2
        assert "anna - die bande" in text and "bert - das syndikat" in text
        assert text.index("anna - die bande") < text.index("bert - das syndikat")
        assert loaded is not None, "the game never reached bert's turn menu"
        state = loaded.state
        assert [p.name for p in state.players] == ["anna", "bert"]
        assert state.clock.active_player == 1

    def test_the_stats_shown_are_the_stats_played(self, monkeypatch, tmp_path):
        """Covers AE1: what each eigenschaften screen kept is what the game holds, and
        anna's overview shows it (intelligenz as :311 ``in=xor30`` keeps it)."""
        # anna's overview (its pages acked; a spare x at the menu is ignored), then save.
        lines = [*self._TWO_ASKED, "1", "x", "x", "x", "p", "q"]
        text, loaded = self._main(monkeypatch, tmp_path, ["--seed", "3", *_SETUP_FLAGS], lines)
        shown = _stopped_rolls(text)
        assert len(shown) == 2, "expected two eigenschaften screens"
        assert loaded is not None
        for (kraft, intel, brut), player in zip(shown, loaded.state.players):
            assert _stats(player) == (kraft, intel | 30, brut)  # faithful :311
        kraft, intel, brut = shown[0]
        overview = _resolver().resolve(
            "turn.overview.gangster",
            {
                "name": "anna",
                "energie": 5,
                "kraft": kraft,
                "intelligenz": intel | 30,
                "brutalitaet": brut,
                "weapon": "",
            },
        )
        # The stats line without its energie (upkeep has raised that by the overview).
        stats_line = overview.split("\n")[1].split(" ", 1)[1]
        assert stats_line in re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", text)
        for cash, player in zip(re.findall(r"kapital: *(\d+) *\$", text), loaded.state.players):
            assert int(cash) == player.ka

    def test_same_seed_same_starting_stats_as_new_game(self, monkeypatch, tmp_path):
        """Covers AE2: piped rolls stop on their first frame -- two runs of a seed set
        up the same players, and the same as ``new_game(seed=...)``."""
        from engine.config_loader import load_game_config

        lines = [*self._TWO_ASKED, "p", "q"]
        argv = ["--seed", "7", *_SETUP_FLAGS]
        _, first = self._main(monkeypatch, tmp_path, argv, lines, name="a.jsonl")
        _, second = self._main(monkeypatch, tmp_path, argv, lines, name="b.jsonl")
        assert first is not None and second is not None
        expected = load_game_config(CONFIG_DIR).module.new_game(
            seed=7,
            end_year=1930,
            score_weight=1.0,
            players=[("anna", "die bande"), ("bert", "das syndikat")],
        )
        for state in (first.state, second.state):
            assert [(_stats(p), p.ka) for p in state.players] == [
                (_stats(p), p.ka) for p in expected.players
            ]

    def test_player_flags_skip_the_count_and_names_not_the_eigenschaften(
        self, monkeypatch, tmp_path
    ):
        """Covers AE3: ``--player`` twice asks no count and no name, and still shows both
        eigenschaften screens."""
        argv = ["--player", "a:x", "--player", "b:y", *_SETUP_FLAGS]
        text, loaded = self._main(monkeypatch, tmp_path, argv, ["", "", "", "", "", "p", "q"])
        assert _COUNT_PROMPT not in text
        assert (
            _name_prompt(1) not in text
            and _resolver().resolve("setup.gang_name_prompt") not in text
        )
        assert text.count(_EIGENSCHAFTEN) == 2
        assert loaded is not None
        assert [p.name for p in loaded.state.players] == ["a", "b"]
        # Each screen keeps its stats on screen up to its key wait (:316 goto1100): no
        # clear between the title and the "press any key" line, and all five lines in it.
        press = _resolver().resolve("client.press_any_key")
        for start in [m.start() for m in re.finditer(_EIGENSCHAFTEN, text)]:
            screen = text[start : text.index(press, start)]
            assert CLEAR not in screen, "the eigenschaften screen was cleared before its key"
            for line in ("kraft:", "intelligenz:", "brutalitaet:", "energie:", "kapital:"):
                assert line in screen

    def test_a_bad_count_and_a_long_name_are_asked_again(self, monkeypatch, tmp_path):
        """:206 asks the count again below 1 or above 4; :291 the name over 13 characters."""
        lines = ["", "", "0", "5", "1", "a" * 14, "anna", "die bande", "", "", "p", "q"]
        text, loaded = self._main(monkeypatch, tmp_path, _SETUP_FLAGS, lines)
        assert text.count(_COUNT_PROMPT) == 3
        assert text.count(_name_prompt(1)) == 2
        assert loaded is not None
        assert [p.name for p in loaded.state.players] == ["anna"]

    def test_piped_rolls_read_no_input(self, monkeypatch, tmp_path):
        """Exactly the listed answers reach anna's overview: a roll that read a line
        would swallow an answer and the overview would never open."""
        lines = ["", "", "1", "anna", "die bande", "", "", "1"]
        text, _ = self._main(monkeypatch, tmp_path, _SETUP_FLAGS, lines)
        assert _resolver().resolve("turn.overview.title", {"name": "anna"}) in text

    def test_a_save_of_an_asked_game_loads_and_plays_on(self, monkeypatch, tmp_path):
        """R8: a save from the first turn of an interactively set-up game resumes."""
        from engine.movement import try_move

        lines = ["", "", "1", "anna", "die bande", "", "", "p", "q"]
        _, saved = self._main(monkeypatch, tmp_path, ["--seed", "3", *_SETUP_FLAGS], lines)
        assert saved is not None
        start = saved.state.players[0]
        city = load_city(_yaml_load(_CITY_YAML))
        step_key = next(
            key
            for key, delta in {"s": DOWN}.items()
            if getattr(try_move(saved.state, city, delta).payload, "kind", None) == "step"
        )
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"2\n{step_key}\nq\n"))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(30, "play() did not return", exc_type=AssertionError):
            state, _rng = play(load=str(tmp_path / "s.jsonl"))
        assert state.players[0].name == "anna"
        assert state.players[0].ms == start.ms - 1, "the loaded game did not play on"
        assert _stats(state.players[0]) == _stats(start)

    def test_rolls_stopped_late_save_load_and_replay(self, monkeypatch, tmp_path):
        """R8: rolls stopped on later frames are what the save holds, and the replay of
        the save reaches the same starting stats.

        The client's roll seam (``_roll_key_pressed``) stands in for the keyboard: the
        key comes on each roll's third frame.
        """
        from engine.config_loader import load_game_config
        from engine.persistence import replay

        import clients.terminal.session as session

        frames = iter(([False, False, True] * 3) * 2)
        monkeypatch.setattr(session, "_roll_key_pressed", lambda: next(frames))
        text, loaded = self._main(
            monkeypatch, tmp_path, ["--seed", "3", *_SETUP_FLAGS], [*self._TWO_ASKED, "p", "q"]
        )
        assert next(frames, None) is None, "not every roll ran to its third frame"
        shown = _stopped_rolls(text)
        assert len(shown) == 2
        assert loaded is not None
        played = [_stats(p) for p in loaded.state.players]
        assert played == [(k, i | 30, b) for k, i, b in shown]
        cfg = load_game_config(CONFIG_DIR)
        assert [_stats(p) for p in replay(loaded, cfg.registries).players] == played
        first_frames = cfg.module.new_game(
            seed=3,
            end_year=1930,
            score_weight=1.0,
            players=[("anna", "die bande"), ("bert", "das syndikat")],
        )
        assert played != [_stats(p) for p in first_frames.players], "late stops changed nothing"


def test_the_client_holds_no_setup_rule_or_roll_formula():
    """R7: the name rule, the ranges and the roll formulas are the config's; the
    client only draws setup. A pattern per rule the client once held (or could)."""
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in (_CONFIG_DIR.parents[2] / "clients").rglob("*.py")
    }
    session = (_CONFIG_DIR.parents[2] / "clients" / "terminal" / "session.py").read_text(
        encoding="utf-8"
    )
    forbidden_everywhere = [
        r"len\([^)]*\)\s*[<>]=?\s*13\b",  # :291 len(x$)>13
        r"\b13\s*[<>]=?\s*len\(",
        r"stat_roll|cash_roll|start_energy|intelligenz_or",  # the roll data's keys
        r"\*\s*5\s*\+\s*10\b|\*\s*500\s*\+\s*5000\b",  # :350 / :315 formulas
        r"\b_roll_stat\b|\bname_fits\b|\bparse_setup_number\b",
    ]
    for pattern in forbidden_everywhere:
        hits = [str(p) for p, text in sources.items() if re.search(pattern, text)]
        assert hits == [], f"{pattern!r} in {hits}"
    for pattern in (
        r"name_length",
        r"[\"']player_count[\"']",
        r"input_ranges",
        r"\.range\(",
        r"\.hit\(",
    ):
        assert re.search(pattern, session) is None, f"session.py holds {pattern!r}"

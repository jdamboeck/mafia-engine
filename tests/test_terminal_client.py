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
from clients.terminal import EndOfInput, TerminalInput, map_repl, render_message  # noqa: E402
from tests.helpers import deadline, with_player  # noqa: E402

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

    spy = _Spy(resolver=_resolver(), stdin=io.StringIO(""), stdout=out)

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
    return TerminalInput(resolver=_resolver(), stdin=io.StringIO(stdin_text), stdout=out), out


def test_eof_at_non_cancellable_prompt_int_raises_end_of_input():
    inp, _out = _eof_client("")  # readline() returns "" -> real EOF
    with pytest.raises(EndOfInput):
        inp(PromptInt("locations.sph.wager_prompt", min=1, max=100))


def test_eof_at_cancellable_prompt_raises_rather_than_cancelling():
    """EOF is not a blank line even where a blank line would cancel: it ends input."""
    inp, _out = _eof_client("")
    with pytest.raises(EndOfInput):
        inp(PromptChoice("locations.sph.wager_prompt", options=("a", "b"), cancellable=True))


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
    """Drive the real ``play()`` over EXACT stdin text; return (first state, stdout).

    The first turn-start state is captured by spying on ``_run_upkeep_screen`` (the
    module-level seam ``play()`` calls right after setup); the spy reads no input, so
    stdin runs straight into the map loop, where EOF quits. A SIGALRM deadline turns a
    re-prompt spin into a failure instead of a hung suite.
    """
    import clients.terminal.__main__ as tmain

    seen = []

    def _spy(state, *a, **k):
        seen.append(state)
        return state

    monkeypatch.setattr(tmain, "_run_upkeep_screen", _spy)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys, "stdout", out)

    with deadline(
        seconds,
        f"play() did not return within {seconds}s (EOF spin?)",
        exc_type=AssertionError,
    ):
        tmain.play(seed=42, **play_kwargs)
    return (seen[0] if seen else None), out.getvalue()


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
    def _main(self, monkeypatch, argv):
        import clients.terminal.__main__ as tmain

        calls = []
        monkeypatch.setattr(tmain, "play", lambda *a, **k: calls.append((a, k)))
        tmain.main(argv)
        return calls

    def test_flags_reach_play(self, monkeypatch):
        calls = self._main(monkeypatch, ["--end-year", "1950", "--score-weight", "1.5"])
        assert calls[0][1]["end_year"] == 1950
        assert calls[0][1]["score_weight"] == 1.5

    def test_absent_flags_pass_none(self, monkeypatch):
        calls = self._main(monkeypatch, [])
        assert calls[0][1]["end_year"] is None
        assert calls[0][1]["score_weight"] is None

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
        import clients.terminal.__main__ as tmain

        calls = []
        monkeypatch.setattr(tmain, "play", lambda *a, **k: calls.append((a, k)))
        with pytest.raises(SystemExit) as exc:
            tmain.main(argv)
        assert exc.value.code != 0
        assert calls == []
        # Rejected as out of range -- not merely as an unknown flag (which would
        # also exit non-zero and make this test pass without the feature).
        err = capsys.readouterr().err
        assert "unrecognized arguments" not in err
        assert argv[0] in err


class TestClientErrorGuard:
    """U9 / KTD-9: known bad inputs end as ONE readable stderr line and a non-zero
    exit -- never a traceback -- while unknown errors keep theirs (AE4, AE6)."""

    @staticmethod
    def _valid_save(path: Path, *, rng_log=()) -> Path:
        from engine.config_loader import load_game_config
        from engine.persistence import save_game

        import clients.terminal.__main__ as tmain

        cfg = load_game_config(tmain._CONFIG_DIR)
        state = cfg.module.new_game(
            seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
        )
        save_game(path, state, effect_log=[], rng_log=list(rng_log), seed=42)
        return path

    @staticmethod
    def _fail(capsys, argv) -> tuple[int, str]:
        import clients.terminal.__main__ as tmain

        with pytest.raises(SystemExit) as exc:
            tmain.main(argv)
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
        import clients.terminal.__main__ as tmain

        monkeypatch.setattr(tmain, "play", lambda *a, **k: pytest.fail("play ran"))
        code, err = self._fail(capsys, ["--end-year", "1927"])
        assert code not in (0, None)
        assert "Traceback" not in err
        bounds = tmain.load_game_config(tmain._CONFIG_DIR).config["input_ranges"]["end_year"]
        assert f"[{bounds['min']}, {bounds['max']}]" in err

    def _load_and_break_render(self, monkeypatch, tmp_path, exc):
        """Load a VALID save (so loading must succeed) and fail deep inside play()."""
        import clients.terminal.__main__ as tmain

        path = self._valid_save(tmp_path / "ok.jsonl")
        monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))

        def boom(*a, **k):
            raise exc

        monkeypatch.setattr(tmain, "render_map", boom)
        return tmain, path

    def test_unknown_error_inside_play_keeps_its_traceback(self, monkeypatch, tmp_path):
        tmain, path = self._load_and_break_render(monkeypatch, tmp_path, RuntimeError("deep bug"))
        with pytest.raises(RuntimeError, match="deep bug"):
            tmain.main(["--load", str(path)])

    def test_keyboard_interrupt_exits_quietly_with_cursor_restored(
        self, monkeypatch, capsys, tmp_path
    ):
        from clients.terminal import CURSOR_HIDE, CURSOR_SHOW

        tmain, path = self._load_and_break_render(monkeypatch, tmp_path, KeyboardInterrupt())
        with pytest.raises(SystemExit) as exc:
            tmain.main(["--load", str(path)])
        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert CURSOR_HIDE in captured.out
        assert captured.out.rstrip().endswith(CURSOR_SHOW)


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
        watching = TerminalInput(resolver=_resolver(), observe_ai=True)
        plain = TerminalInput(resolver=_resolver())
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
            resolver=_resolver(), stdin=io.StringIO("f\nd\n"), stdout=out, observe_ai=True
        )
        result = self._fight(inp)
        assert result.payload.returned.winner == 2
        assert out.getvalue().count(_resolver().resolve("combat.observe_prompt")) == 1

    def test_watch_ai_flag_reaches_play_and_defaults_off(self, monkeypatch):
        import clients.terminal.__main__ as tmain

        calls = []
        monkeypatch.setattr(tmain, "play", lambda *a, **k: calls.append(k))
        tmain.main(["--watch-ai"])
        tmain.main([])
        assert calls[0]["watch_ai"] is True
        assert calls[1]["watch_ai"] is False

    def test_play_hands_the_opt_in_to_the_sessions_terminal_input(self, monkeypatch):
        import clients.terminal.__main__ as tmain

        seen = []

        class _Stop(Exception):
            pass

        def spy(**kwargs):
            seen.append(kwargs.get("observe_ai"))
            raise _Stop

        monkeypatch.setattr(tmain, "TerminalInput", spy)
        for flag in (True, False):
            with pytest.raises(_Stop):
                tmain.play(seed=1, end_year=1950, score_weight=1.0, watch_ai=flag)
        assert seen == [True, False]


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

    @staticmethod
    def _overridden(monkeypatch, override: dict):
        """Make every ``Resolver.from_config`` in the client return ``override`` merged
        over the classic theme -- a runtime theme swap, as the modding model allows."""
        import clients.terminal.__main__ as tmain

        real = Resolver.from_config(_CONFIG_DIR, theme="classic")

        class _Themed:
            @staticmethod
            def from_config(*_a, **_k):
                return real.with_override({"client": override})

        monkeypatch.setattr(tmain, "Resolver", _Themed)
        return tmain

    def test_every_client_key_resolves(self):
        import string

        resolver = _resolver()
        keys = self._leaf_keys(_yaml_load(self._CLIENT_YAML)["client"], "client")
        assert "client.bye" in keys and "client.turn_over.summary" in keys
        for key in keys:
            template = resolver.tree
            for segment in key.split("."):
                template = template[segment]
            params = {f: "x" for _, f, _, _ in string.Formatter().parse(template) if f}
            assert resolver.resolve(key, params).strip(), key

    def test_overriding_the_bye_line_changes_what_a_quit_prints(self, monkeypatch):
        tmain = self._overridden(monkeypatch, {"bye": "ciao.", "map": {"hint": "walk on."}})
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n\nq\n"))
        monkeypatch.setattr(sys, "stdout", out)
        with deadline(20, "play() did not return", exc_type=AssertionError):
            tmain.play(seed=42, end_year=1930, score_weight=1.0)
        text = out.getvalue()
        assert "ciao." in text and "bye." not in text
        assert "walk on." in text and "move: W/A/S/D" not in text

    def test_overriding_a_turn_over_label_changes_the_summary(self, monkeypatch):
        from engine.config_loader import load_game_config

        summary = "geld: {cash}$ | {position} {movement} {rank} {jail_months}"
        tmain = self._overridden(monkeypatch, {"turn_over": {"summary": summary}})
        session_out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", session_out)
        monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
        session = tmain.TerminalSession(
            seed=42,
            players=None,
            end_year=1930,
            score_weight=1.0,
            load=None,
            save=None,
            watch_ai=False,
        )
        session.state = load_game_config(_CONFIG_DIR).module.new_game(
            seed=42, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
        )
        assert session.turn_over() is False  # "q" quits
        text = session_out.getvalue()
        cash = session.state.players[0].ka
        assert f"geld: {cash}$" in text and "cash:" not in text

    def test_overriding_the_load_error_changes_the_stderr_line(self, monkeypatch, capsys):
        tmain = self._overridden(
            monkeypatch,
            {"load": {"error": "kaputt {path} -- {reason}", "reason": {"not_found": "weg"}}},
        )
        with pytest.raises(SystemExit):
            tmain.main(["--load", "/nonexistent.jsonl"])
        err = capsys.readouterr().err
        assert err.strip() == "kaputt /nonexistent.jsonl -- weg"

    def test_the_status_bar_reads_the_session_resolver(self):
        from clients.terminal.renderers import render_status_bar

        override = _resolver().with_override({"client": {"status_bar": "[{name}/{cash}]"}})
        buf = io.StringIO()
        render_status_bar("alcapone", 5400, 181, 19, buf, resolver=override)
        assert "[alcapone/5400]" in buf.getvalue() and "cash" not in buf.getvalue()

"""Port tests: every engine or config function that ports an arithmetic BASIC formula.

A "port" (KTD-8) is a function whose code or docstring cites a ``mf-prg.bas`` line
holding an arithmetic assignment. :data:`PORTS` is the inventory: each entry names the
line, quotes the statement(s) verbatim from the source, names the engine callable, and
carries an input grid. :func:`test_port_matches_basic` runs the real engine callable
over that grid and compares every result with the quoted BASIC text evaluated by
:mod:`tests.basic_eval` (a C64 BASIC V2 expression evaluator). Nothing here restates a
formula in Python: the BASIC side is only ever the quoted text, and the engine side is
only ever the engine's own code.

Conventions
-----------
- A :class:`Quote` is one statement (or the condition of an ``if``) copied verbatim
  from its line. :func:`test_quote_is_verbatim` checks each one against
  ``mf-prg.bas`` when the research tree is present (it is not in CI).
- ``fnm(ln)`` and ``fnr(0)`` are user functions (``def fn``, lines 115/117), which
  the evaluator does not support. :func:`_expand` substitutes their quoted bodies.
- Control flow (``if``/``goto`` between statements) is expressed in Python in each
  ``_basic_*`` function; the arithmetic is always the quoted text.
- ``rnd(1)``: the engine draws through ``ctx.rng``; :class:`StubRng` answers each draw
  with a scripted ``rnd(1)`` value ``r`` using the correspondence ``engine/rng.py``
  documents: ``range(n)`` is ``int(rnd(1)*n)``, and ``hit(a, b)`` is
  ``int(rnd(1)*(b-a+1))+a``. The BASIC side gets the same ``r``. The ``r`` grid uses
  dyadic fractions (exact in binary floating point) plus a few points chosen to sit
  just either side of a rounding boundary.
- A port that diverges from its BASIC line is a finding, not a test to bend: its entry
  carries ``divergence=...`` and runs as a strict ``xfail`` (so it flips to a failure
  the moment the engine is fixed and the mark must be removed). Where the divergence
  is confined to part of the input range, the rest of the range is a separate, passing
  entry, so it stays protected.
"""

from __future__ import annotations

import functools
import itertools
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from data.game_configs.mafia_1920s.combat_rules import damage_roll, is_hit
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.setup import (
    apply_outcome,
    fnm,
    load_encounter,
    load_gangster_candidates,
    load_vehicles,
    load_weapons,
    new_game,
    score_and_rank,
)
from engine.c64_numbers import c64_float
from engine.combat import CombatFight, CombatResult
from engine.combat_setup import SIDE1_ANCHOR, SIDE2_ANCHOR, placement_position
from engine.config_loader import load_config, load_game_config
from engine.effects import apply, commit
from data.game_configs.mafia_1920s.effects import JobSet, TipSet
from engine.interactions import (
    Ack,
    Confirm,
    Ctx,
    LoadSubState,
    PromptChoice,
    PromptInt,
    PromptText,
    RollFrame,
    ShowMessage,
    StartCombat,
    run,
)
from engine.locations import HANDLERS
from engine.turns import (
    ROADBLOCK_HOOK_KEY,
    SCORE_TRUNCATION_HOOK_KEY,
    SETUP_HANDLER_KEY,
    SPECIAL_CELL_HOOK_KEY,
)
from engine.rng import Rng
from engine.state import Clock, CombatState, Config, Fighter, GameState, Player
from data.game_configs.mafia_1920s.state import Business, Contraband, Debt, Job, Wanted
from data.game_configs.mafia_1920s.handlers import police, win_flows
from tests.basic_eval import eval_assignment, eval_expr
from tests.helpers import is_effect, load_source, with_tenancy
import data.game_configs.mafia_1920s.state as game

_REPO = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _REPO / "data" / "game_configs" / "mafia_1920s"

load_game_config(_CONFIG_DIR)

_PARAMS: dict[str, Any] = dict(load_config(_CONFIG_DIR / "config.yaml")["formula_params"])
_WEAPONS = load_weapons(_CONFIG_DIR / "entities" / "weapons.yaml")
_VEHICLES = load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")
_AMBUSH = load_encounter(_CONFIG_DIR / "content" / "encounters" / "kdh_ambush.yaml")
_CANDIDATES = load_gangster_candidates(_CONFIG_DIR / "entities" / "gangsters.yaml")

Values = Mapping[str, Any]


# --------------------------------------------------------------------------- #
# Quotes                                                                      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Quote:
    """One statement (or ``if`` condition) quoted verbatim from ``mf-prg.bas:line``."""

    line: int
    text: str

    def assign(self, values: Values) -> float:
        """The value of an assignment's right-hand side (``fnm``/``fnr`` expanded)."""
        return eval_assignment(_expand(self.text), values)

    def expr(self, values: Values) -> float:
        """The value of a bare expression, e.g. an ``if`` condition."""
        return eval_expr(_expand(self.text), values)

    def holds(self, values: Values) -> bool:
        """An ``if`` condition: true iff nonzero (C64 true is -1)."""
        return self.expr(values) != 0

    def body(self) -> str:
        """The body of a ``def fn`` line: the text after its first ``=``."""
        return self.text.split("=", 1)[1]


QUOTES: list[Quote] = []


def q(line: int, text: str) -> Quote:
    """Register and return a verbatim quote from ``mf-prg.bas:line``."""
    quote = Quote(line, text)
    QUOTES.append(quote)
    return quote


# The two user functions the ported lines call (``def fn``, :115/:117).
FNM = q(115, "deffnm(ln)=50-50*(ln=3orln=4)-100*(ln=1)")
FNR = q(117, "deffnr(x)=int(rnd(1)*8)+8")


def _expand(text: str) -> str:
    """Inline the quoted ``def fn`` bodies the evaluator cannot call."""
    return text.replace("fnm(ln)", f"({FNM.body()})").replace("fnr(0)", f"({FNR.body()})")


# --------------------------------------------------------------------------- #
# The rnd(1) stub and the handler stepper                                     #
# --------------------------------------------------------------------------- #
class StubRng:
    """Answers each engine draw with the next scripted ``rnd(1)`` value.

    ``range(n)`` returns ``int(r*n)`` and ``hit(a, b)`` returns ``a+int(r*(b-a+1))``:
    the ``int(rnd(1)*N)`` correspondence ``engine/rng.py`` is built on. Drawing past
    the script raises, so a port that draws more than its BASIC line cannot hide.
    """

    def __init__(self, draws: Iterable[float] = ()) -> None:
        self._draws = list(draws)
        self.used = 0

    def _next(self) -> float:
        if self.used >= len(self._draws):
            raise AssertionError(f"engine drew more than the {len(self._draws)} scripted rnd(1)")
        r = self._draws[self.used]
        self.used += 1
        return r

    def range(self, n: int) -> int:
        return int(self._next() * n)

    def hit(self, a: int, b: int) -> int:
        return a + int(self._next() * (b - a + 1))


class _FightStarted(Exception):
    """The handler yielded ``StartCombat``: the branch under test led into a fight.

    Carries the effects the handler applied before the fight began.
    """

    def __init__(self, effects: list[Any]) -> None:
        super().__init__("StartCombat")
        self.effects = effects


class _RecordingCtx(Ctx):
    """A :class:`Ctx` that also keeps the applied effects where the test can read them."""

    def __init__(self, state: GameState, rng: StubRng) -> None:
        super().__init__(state=state, rng=rng)
        self.applied: list[Any] = []

    def apply(self, effect: Any) -> None:
        super().apply(effect)
        self.applied.append(effect)


@dataclass
class _Run:
    state: GameState
    effects: list[Any]
    asked: list[Any] = field(default_factory=list)
    shown: list[ShowMessage] = field(default_factory=list)
    draws_used: int = 0


def _drive(
    handler: Callable[[Ctx], Any],
    state: GameState,
    draws: Sequence[float] = (),
    answer: Callable[[Any], Any] = lambda interaction: None,
    fight: Any = None,
) -> _Run:
    """Step ``handler`` to completion and commit its effects.

    ``ShowMessage`` is acked (and kept in ``shown``) and ``LoadSubState`` (display
    only here) answered with ``None``; every other interaction goes to ``answer``.
    ``StartCombat`` is answered with ``fight`` when one is given (a stand-in
    ``CombatResult``), and otherwise raises :class:`_FightStarted`.
    """
    rng = StubRng(draws)
    ctx = _RecordingCtx(state, rng)
    gen = handler(ctx)
    asked: list[Any] = []
    shown: list[ShowMessage] = []
    try:
        interaction = next(gen)
        while True:
            if isinstance(interaction, StartCombat):
                if fight is None:
                    raise _FightStarted(list(ctx.applied))
                interaction = gen.send(fight)
                continue
            if isinstance(interaction, ShowMessage):
                shown.append(interaction)
                response = Ack
            elif isinstance(interaction, LoadSubState):
                response = None
            else:
                asked.append(interaction)
                response = answer(interaction)
            interaction = gen.send(response)
    except StopIteration:
        pass
    return _Run(commit(state, ctx.applied).state, ctx.applied, asked, shown, rng.used)


def _gangster(kr: int = 99, in_: int = 99, bt: int = 99, en: int = 5, weapon: int = 0) -> Gangster:
    return Gangster(name="g", weapon=weapon, energie=en, kraft=kr, intelligenz=in_, brutalitaet=bt)


def _state(player: Player, *, score_mult: float = 1.0) -> GameState:
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params={**_PARAMS, "score_mult": score_mult}),
    )


#: The game-state arguments ``_player`` takes: entity views, and scalar value-map keys.
_VIEW_ARGS = ("debt", "business", "contraband", "jobs", "wanted")
_SCALAR_ARGS = ("tip_target", "rented_months", "nr")


def _player(**fields: Any) -> Player:
    """A player; game state given by the old field names lands in its value map."""
    fields.setdefault("name", "p")
    fields.setdefault("ka", 10**6)
    fields.setdefault("roster", (_gangster(),))
    views = [fields.pop(k) for k in _VIEW_ARGS if k in fields]
    scalars = {k: fields.pop(k) for k in _SCALAR_ARGS if k in fields}
    return Player(**fields, values=game.values_of(*views, **scalars))


def _grid(**axes: Iterable[Any]) -> tuple[dict[str, Any], ...]:
    """The cartesian product of the named axes, as a tuple of input points."""
    names = list(axes)
    return tuple(dict(zip(names, combo)) for combo in itertools.product(*axes.values()))


#: rnd(1) values: dyadic fractions (exact in binary) plus boundary-straddling points.
R = (0.0, 0.125, 0.25, 0.3, 0.375, 0.5, 0.625, 0.7, 0.75, 0.875, 0.9990234375)


# --------------------------------------------------------------------------- #
# Ports                                                                       #
# --------------------------------------------------------------------------- #
# --- :115 fnm rent -------------------------------------------------------------
def _basic_fnm(v: Values) -> Any:
    return eval_expr(FNM.body(), {"ln": v["ln"]})


def _engine_fnm(v: Values) -> Any:
    return fnm(v["ln"], _PARAMS["fnm"])


# --- :10020-10040 slw rent -------------------------------------------------------
Q_10020 = q(10020, "p=fnm(ln)")
Q_10035 = q(10035, "ka(sp)<x*p")
Q_10040_KA = q(10040, "ka(sp)=ka(sp)-x*p")
Q_10040_UM = q(10040, "um(sp)=um(sp)+x")


def _basic_slw(v: Values) -> Any:
    b = {"sp": 1, "ln": v["ln"], "x": v["x"], "ka(1)": v["ka"], "um(1)": 0}
    b["p"] = Q_10020.assign(b)
    if Q_10035.holds(b):
        return (b["ka(1)"], b["um(1)"])
    return (Q_10040_KA.assign(b), Q_10040_UM.assign(b))


def _engine_slw(v: Values) -> Any:
    run = _drive(
        HANDLERS["slw.rent"],
        _state(_player(ka=v["ka"], last_location=v["ln"])),
        answer=lambda i: v["x"],
    )
    p = run.state.players[0]
    return (p.ka, game.rented_months(p))


# --- :1160-1165 score and rank -------------------------------------------------------
Q_1160 = q(1160, "gf(sp)=gf(sp)+(x*x8)")
Q_1160_CAP = q(1160, "gf(sp)>100")
Q_1161_FLOOR = q(1161, "gf(sp)<0")
Q_1165 = q(1165, "nr(sp)=int(gf(sp)/11.1)+1")


def _basic_score(v: Values) -> Any:
    b = {"sp": 1, "gf(1)": v["gf"], "x": v["x"], "x8": v["x8"]}
    b["gf(1)"] = Q_1160.assign(b)
    if Q_1160_CAP.holds(b):
        b["gf(1)"] = 100
    if Q_1161_FLOOR.holds(b):
        b["gf(1)"] = 0
    return (b["gf(1)"], Q_1165.assign(b))


def _engine_score(v: Values) -> Any:
    state = _state(_player(gf=v["gf"]), score_mult=v["x8"])
    p = apply(state, score_and_rank(v["x"], _PARAMS)).players[0]
    return (p.gf, game.next_rank(p))


# --- :1013 per-turn score truncation --------------------------------------------------
Q_1013 = q(1013, "gf(sp)=int(gf(sp)*100)/100")


def _basic_truncate(v: Values) -> Any:
    # The C64 holds gf(sp) and the stored result as 5-byte floats (engine.c64_numbers,
    # checked against VICE in tests/test_c64_float.py); the evaluator's doubles do the
    # line in between. That is exact here: int() floors gf*100, which a 32-bit mantissa
    # times 100 never rounds, and the one division rounds once more at the store
    # (n/100's 20-bit period rules out a double landing on a 5-byte tie).
    return c64_float(Q_1013.assign({"sp": 1, "gf(1)": c64_float(v["gf"])}))


def _engine_truncate(v: Values) -> Any:
    run = _drive(HANDLERS[SCORE_TRUNCATION_HOOK_KEY], _state(_player(gf=v["gf"])))
    return run.state.players[0].gf


# --- :4015/:4020 energy regen -------------------------------------------------------
Q_4015 = q(4015, "en=en+int(kr/10)+1")
Q_4020 = q(4020, "x=2+int(kr/4)+int(bt/4)")
Q_4020_CAP = q(4020, "en>x")


def _basic_regen(v: Values) -> Any:
    b = {"kr": v["kr"], "bt": v["bt"], "en": v["en"]}
    b["en"] = Q_4015.assign(b)
    b["x"] = Q_4020.assign(b)
    if Q_4020_CAP.holds(b):
        b["en"] = b["x"]
    return b["en"]


def _engine_regen(v: Values) -> Any:
    player = _player(roster=(_gangster(kr=v["kr"], bt=v["bt"], en=v["en"]),))
    run = _drive(HANDLERS["upkeep.turn_start"], _state(player))
    return run.state.players[0].roster[0].vitality


# --- :4305 debt grace tick -------------------------------------------------------------
Q_4305 = q(4305, "kz(sp)=kz(sp)+(kz(sp)>0)")
Q_4305_DUE = q(4305, "kz(sp)=0")


def _basic_grace(v: Values) -> Any:
    b = {"sp": 1, "kz(1)": v["kz"]}
    b["kz(1)"] = Q_4305.assign(b)
    return (b["kz(1)"], Q_4305_DUE.holds(b))


def _engine_grace(v: Values) -> Any:
    # A debt of 1000 is outstanding, so :4040's gosub 4300 runs; a counter that reaches
    # 0 goes on to the collectors' fight (:4350).
    state = _state(_player(debt=Debt(amount=1000, months=v["kz"])))
    try:
        run = _drive(HANDLERS["upkeep.turn_start"], state)
    except _FightStarted as fight:
        return (game.debt(commit(state, fight.effects).state.players[0]).months, True)
    return (game.debt(run.state.players[0]).months, False)


# --- :4405/:4410 loan-shop income --------------------------------------------------------
Q_4405 = q(4405, "int(rnd(1)*3)")
Q_4410_P = q(4410, "p=int(rnd(1)*kk(sp)/20+kk(sp)/10)")
Q_4410_KA = q(4410, "ka(sp)=ka(sp)+p")


def _basic_income(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "kk(1)": v["kk"], "ka(1)": 0, "rnd(1)": v["r0"]}
    if not Q_4405.holds(b):
        return 0
    b["rnd(1)"] = v["r"]
    b["p"] = Q_4410_P.assign(b)
    return Q_4410_KA.assign(b)


def _engine_income(v: Values) -> Any:
    player = _player(ka=0, business=Business(shop_tile=1, shop_capital=v["kk"]))
    run = _drive(HANDLERS["upkeep.turn_start"], _state(player), draws=(v["r0"], v["r"]))
    return run.state.players[0].ka


# --- :4045-4046/:4605-4651 rent countdown and late rent ---------------------------------
Q_4045 = q(4045, "um(sp)=0")
Q_4046_DEC = q(4046, "um(sp)=um(sp)-1")
Q_4046_DUE = q(4046, "um(sp)=0")
Q_4046_RESET = q(4046, "um(sp)=1")
Q_4605_P = q(4605, "p=int(rnd(1)*100)+200")
Q_4605_CAP = q(4605, "p>ka(sp)")
Q_4605_CAPPED = q(4605, "p=ka(sp)")
Q_4605_EVICT = q(4605, "p=0")
Q_4620 = q(4620, "ka(sp)=ka(sp)-p")
Q_4651 = q(4651, "gz(sp)=1")


def _basic_rent(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "um(1)": v["um"], "ka(1)": v["ka"], "gz(1)": 3}
    b["rnd(1)"] = v["r"]
    if Q_4045.holds(b):
        return (b["ka(1)"], b["um(1)"], b["gz(1)"])
    b["um(1)"] = Q_4046_DEC.assign(b)
    if Q_4046_DUE.holds(b):
        b["um(1)"] = Q_4046_RESET.assign(b)
        # gosub4600: :4605's second `if` runs only inside the first one's `then`.
        b["p"] = Q_4605_P.assign(b)
        if Q_4605_CAP.holds(b):
            b["p"] = Q_4605_CAPPED.assign(b)
            if Q_4605_EVICT.holds(b):
                b["gz(1)"] = Q_4651.assign(b)
                return (b["ka(1)"], b["um(1)"], b["gz(1)"])
        b["ka(1)"] = Q_4620.assign(b)
    return (b["ka(1)"], b["um(1)"], b["gz(1)"])


def _engine_rent(v: Values) -> Any:
    player = _player(ka=v["ka"], rented_months=v["um"], roster=(_gangster(),) * 3)
    run = _drive(HANDLERS["upkeep.turn_start"], _state(player), draws=(v["r"],))
    p = run.state.players[0]
    return (p.ka, game.rented_months(p), len(p.roster))


# --- :31000-31010 arms-deal payout ----------------------------------------------------
Q_31000 = q(31000, "int(rnd(1)*5)=0")
Q_31005 = q(31005, "p=int(rnd(1)*9500)+5500")
Q_31010 = q(31010, "ka(sp)=ka(sp)+p")


def _basic_arms(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ka(1)": 0, "rnd(1)": v["r0"]}
    if Q_31000.holds(b):
        return 0
    b["rnd(1)"] = v["r"]
    b["p"] = Q_31005.assign(b)
    return Q_31010.assign(b)


def _engine_arms(v: Values) -> Any:
    player = _player(ka=0, tip_target=4)
    run = _drive(HANDLERS["upkeep.turn_start"], _state(player), draws=(v["r0"], v["r"]))
    return run.state.players[0].ka


# --- :4055-4056 the marks fade -------------------------------------------------------
Q_4055 = q(4055, "int(rnd(1)*8)=0")
Q_4055_AG = q(4055, "ag(sp)=ag(sp)and254")
Q_4056 = q(4056, "int(rnd(1)*8)=0")
Q_4056_AG = q(4056, "ag(sp)=ag(sp)and253")


def _ag(player: Player) -> int:
    """``ag(sp)`` rebuilt from the two marks: the passport is bit 1, counterfeit bit 2."""
    held = game.contraband(player)
    return held.fake_papers + 2 * held.counterfeit


def _marks(ag: int) -> Contraband:
    return Contraband(fake_papers=ag & 1, counterfeit=(ag & 2) // 2)


def _basic_decay(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ag(1)": v["ag"], "rnd(1)": v["r0"]}
    if Q_4055.holds(b):
        b["ag(1)"] = Q_4055_AG.assign(b)
    b["rnd(1)"] = v["r1"]
    if Q_4056.holds(b):
        b["ag(1)"] = Q_4056_AG.assign(b)
    return b["ag(1)"]


def _engine_decay(v: Values) -> Any:
    # The port rolls only for a held mark (a roll on a clear bit changes nothing), so it
    # is handed the source's roll for each mark it holds, in the source's order.
    draws = [r for bit, r in ((1, v["r0"]), (2, v["r1"])) if v["ag"] & bit]
    player = _player(contraband=_marks(v["ag"]))
    run = _drive(HANDLERS["upkeep.turn_start"], _state(player), draws=draws)
    assert run.draws_used == len(draws)
    return _ag(run.state.players[0])


# --- :22010-22020 ble passport ---------------------------------------------------------
Q_22010 = q(22010, "p=1000*x")
Q_22014 = q(22014, "ka(sp)<p")
Q_22020_KA = q(22020, "ka(sp)=ka(sp)-p")
Q_22020_AG = q(22020, "ag(sp)=ag(sp)or1")


def _basic_passport(v: Values) -> Any:
    b = {"sp": 1, "x": v["gz"], "ka(1)": v["ka"], "ag(1)": v["ag"]}
    b["p"] = Q_22010.assign(b)
    if Q_22014.holds(b):
        return (b["ka(1)"], b["ag(1)"])
    return (Q_22020_KA.assign(b), Q_22020_AG.assign(b))


def _engine_passport(v: Values) -> Any:
    player = _player(ka=v["ka"], roster=(_gangster(),) * v["gz"], contraband=_marks(v["ag"]))
    run = _drive(HANDLERS["ble.passport"], _state(player), answer=lambda i: True)
    return (run.state.players[0].ka, _ag(run.state.players[0]))


# --- :22105-22120 ble counterfeit money ------------------------------------------------
Q_22105 = q(22105, "q<=0")
Q_22106 = q(22106, "q>5000orq>ka(sp)")
Q_22110 = q(22110, "p=int(rnd(1)*q/2)+q+100")
Q_22120_KA = q(22120, "ka(sp)=ka(sp)-q+p")
Q_22120_AG = q(22120, "ag(sp)=ag(sp)or2")


class _AskedAgain(Exception):
    """The answer is outside the prompt's bounds: the driver would ask again."""


def _basic_counterfeit(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "q": v["q"], "ka(1)": v["ka"], "ag(1)": v["ag"]}
    if Q_22105.holds(b):
        return (b["ka(1)"], b["ag(1)"])
    if Q_22106.holds(b):
        return "asked again"
    b["rnd(1)"] = v["r"]
    b["p"] = Q_22110.assign(b)
    return (Q_22120_KA.assign(b), Q_22120_AG.assign(b))


def _engine_counterfeit(v: Values) -> Any:
    def answer(interaction: Any) -> Any:
        if isinstance(interaction, PromptInt):
            if not interaction.min <= v["q"] <= interaction.max:
                raise _AskedAgain
            return v["q"]
        return True  # the :22115 confirm

    player = _player(ka=v["ka"], contraband=_marks(v["ag"]))
    try:
        run = _drive(HANDLERS["ble.counterfeit"], _state(player), draws=(v["r"],), answer=answer)
    except _AskedAgain:
        return "asked again"
    return (run.state.players[0].ka, _ag(run.state.players[0]))


# --- :26000-26010 the police squad ----------------------------------------------------
Q_26000 = q(26000, "gz(0)=5+int(rnd(1)*ra(sp)/2)")
Q_26010_W = q(26010, "w=5-2*(ra(sp)>5)")
Q_26010_E = q(26010, "e=20+2*(ra(sp)-1)-int(rnd(1)*21)")


def _basic_squad(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ra(1)": v["ra"], "rnd(1)": v["r0"]}
    count = Q_26000.assign(b)
    weapon = Q_26010_W.assign(b)
    b["rnd(1)"] = v["r1"]
    return (count, weapon, Q_26010_E.assign(b))


def _engine_squad(v: Values) -> Any:
    state = _state(_player(rank=v["ra"]))
    rng = StubRng((v["r0"], v["r1"]))
    gen = police.police_fight(Ctx(state=state, rng=rng), police.Arrest())
    start = next(gen)
    gen.close()
    assert isinstance(start, StartCombat) and start.scenario is not None
    assert start.scenario.sides is not None
    squad = start.scenario.sides[1]
    ((weapon, energy),) = {(f.weapon, f.vitality) for f in squad}  # one w, one e
    return (len(squad), weapon, energy)


# --- :26021-26039 the arrest: the chief-bribe auto-pay and the bribe ----------------------
Q_26021 = q(26021, "pl(sp)andint(rnd(1)*2)<>0")
Q_26035 = q(26035, "p=500+500*ra(sp)")
Q_26037 = q(26037, "ka(sp)<p")
Q_26038_KA = q(26038, "ka(sp)=ka(sp)-p")
Q_26038 = q(26038, "int(rnd(1)*5)=0")


def _basic_payment(b: dict[str, Any], r: float) -> Any:
    """:26037-26039 from the price ``b["p"]``: (cash, trial or free)."""
    if Q_26037.holds(b):
        return (b["ka(1)"], "trial")
    b["ka(1)"] = Q_26038_KA.assign(b)
    b["rnd(1)"] = r
    return (b["ka(1)"], "trial" if Q_26038.holds(b) else "free")


def _arrest_outcome(run: _Run) -> Any:
    player = run.state.players[0]
    return (player.ka, "trial" if player.po == 911 else "free")


class _MenuShown(Exception):
    """The capture menu was asked: :26021 did not skip it."""


def _capture_answer(menu: int | None) -> Callable[[Any], Any]:
    """Answers the capture's prompts: ``menu`` at the menu (``None``: raise), yes to the
    bribe, no to the trial's lawyer."""

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, PromptChoice):
            if menu is None:
                raise _MenuShown
            return menu
        return interaction.key == "police.confirm"

    return answer


def _basic_autopay(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "pl(1)": v["pl"], "rnd(1)": v["r0"], "ka(1)": v["ka"]}
    if not Q_26021.holds(b):
        return "menu"
    b["p"] = v["p"]
    return _basic_payment(b, v["r1"])


def _engine_autopay(v: Values) -> Any:
    player = _player(ka=v["ka"], wanted=Wanted(bribe_months=v["pl"]), po=400)
    handler = lambda ctx: police.caught(ctx, police.Arrest(p=v["p"]))  # noqa: E731
    try:
        run = _drive(handler, _state(player), (v["r0"], v["r1"]), _capture_answer(None))
    except _MenuShown:
        return "menu"
    return _arrest_outcome(run)


def _basic_bribe(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ra(1)": v["ra"], "ka(1)": v["ka"]}
    b["p"] = Q_26035.assign(b)
    return _basic_payment(b, v["r"])


def _engine_bribe(v: Values) -> Any:
    player = _player(ka=v["ka"], rank=v["ra"], po=400)
    handler = lambda ctx: police.caught(ctx, police.Arrest(p=0))  # noqa: E731
    return _arrest_outcome(_drive(handler, _state(player), (0.0, v["r"]), _capture_answer(0)))


# --- :26040 the flight -----------------------------------------------------------------
Q_26040 = q(26040, "int(rnd(1)*tr(sp)/11)=0")


def _basic_flight(v: Values) -> Any:
    sp = v["sp"]
    b = {"sp": sp, f"tr({sp})": _VEHICLES[sp]["tr"], "rnd(1)": v["r"]}
    return "caught" if Q_26040.holds(b) else "escaped"


def _engine_flight(v: Values) -> Any:
    players = tuple(_player(po=400) for _ in range(v["sp"]))
    state = replace(
        _state(players[0]),
        players=players,
        clock=Clock(active_player=v["sp"] - 1, player_count=v["sp"]),
    )
    handler = lambda ctx: police.caught(ctx, police.Arrest(p=0))  # noqa: E731
    run = _drive(handler, state, (0.0, v["r"]), _capture_answer(1))
    return "caught" if run.state.players[v["sp"] - 1].po == 911 else "escaped"


# --- :2041, :6015-6036 the map roadblock ---------------------------------------------------
Q_110_BR = q(110, "br=52224")
Q_2030 = q(2030, "p=br+po(sp)+x")
Q_2041 = q(2041, "ms/20=int(ms/20)andint(rnd(1)*5)=0andra(sp)>3")
Q_6015 = q(6015, "int(rnd(1)*3)=0")
Q_6016 = q(6016, "(ag(sp)and2)<>0")
Q_6017 = q(6017, "ta(sp)")
Q_6018 = q(6018, "(ag(sp)and1)<>0")
Q_6036 = q(6036, "ta(sp)=0")
#: The capture after a stop: chief-bribe months, the :26021 roll skipping the menu, and
#: :26038's roll letting the player go, so the cash shows the ``p`` capture was handed.
_ROADBLOCK_CAPTURE_R = 0.5


def _basic_roadblock(v: Values) -> Any:
    """(outcome, ta, ka) after a street step from ``po`` by ``x``."""
    b: dict[str, Any] = {"sp": 1, "ms": v["ms"], "ra(1)": v["ra"], "rnd(1)": v["r0"]}
    b.update({"ag(1)": v["ag"], "ta(1)": v["ta"], "ka(1)": 10**6, "po(1)": v["po"], "x": v["x"]})
    if not Q_2041.holds(b):
        return ("none", b["ta(1)"], b["ka(1)"])
    b["rnd(1)"] = v["r1"]
    if Q_6015.holds(b) or (
        not Q_6016.holds(b) and not Q_6017.holds(b) and Q_6018.holds(b)
    ):  # :6015 / :6018 goto6025
        return ("pass", b["ta(1)"], b["ka(1)"])
    if not Q_6016.holds(b) and Q_6017.holds(b):
        b["ta(1)"] = Q_6036.assign(b)
    b["br"] = Q_110_BR.assign(b)
    b["p"] = Q_2030.assign(b)
    ka, _ = _basic_payment(b, _ROADBLOCK_CAPTURE_R)
    return ("caught", b["ta(1)"], ka)


def _engine_roadblock(v: Values) -> Any:
    marks = Contraband(
        fake_papers=v["ag"] & 1, counterfeit=(v["ag"] & 2) // 2, alcohol_barrels=v["ta"]
    )
    player = _player(
        ka=10**6,
        rank=v["ra"],
        ms=v["ms"],
        po=v["po"] + v["x"],  # the runner has made the step (:2040)
        contraband=marks,
        wanted=Wanted(bribe_months=1),
    )
    # The port draws the :2041 roll only when the rank and the points allow a stop (a
    # roll that cannot change the outcome is not drawn); the stop draws :6015's, and a
    # capture the two above.
    draws: list[float] = []
    if v["ra"] > 3 and v["ms"] % 20 == 0:
        draws = [v["r0"], v["r1"], _ROADBLOCK_CAPTURE_R, _ROADBLOCK_CAPTURE_R]
    screens: list[Any] = []

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, PromptChoice):
            raise AssertionError("the capture menu showed")
        screens.append(interaction)
        return None

    run = _drive(HANDLERS[ROADBLOCK_HOOK_KEY], _state(player), draws, answer)
    after = run.state.players[0]
    if not screens:
        outcome = "none"
    else:
        outcome = "pass" if screens[0].params["lines"][-1][0] == "roadblock.nothing" else "caught"
    return (outcome, game.contraband(after).alcohol_barrels, after.ka)


# --- :26045-26065 the trial: the months and the lawyer -----------------------------------
Q_26045 = q(26045, "gs(sp)=int(ra(sp)/2+.5)")
Q_26050 = q(26050, "ra(sp)<5")
Q_26061 = q(26061, "x>ka(sp)orx<0")
Q_26062_KA = q(26062, "ka(sp)=ka(sp)-x")
Q_26062_Y = q(26062, "y=int(rnd(1)*(x/1000+1))+1")
Q_26065 = q(26065, "gs(sp)=gs(sp)-y")
Q_26065_FLOOR = q(26065, "gs(sp)<0")


def _basic_trial(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ra(1)": v["ra"], "ka(1)": v["ka"], "x": v["x"]}
    b["gs(1)"] = Q_26045.assign(b)
    if Q_26050.holds(b) or b["x"] == 0:  # :26060 ``ifx=0goto26075``
        return (b["ka(1)"], b["gs(1)"])
    if Q_26061.holds(b):
        return "asked again"
    b["ka(1)"] = Q_26062_KA.assign(b)
    b["rnd(1)"] = v["r"]
    b["y"] = Q_26062_Y.assign(b)
    b["gs(1)"] = Q_26065.assign(b)
    if Q_26065_FLOOR.holds(b):
        b["gs(1)"] = 0
    return (b["ka(1)"], b["gs(1)"])


def _engine_trial(v: Values) -> Any:
    asked: list[Any] = []

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, PromptInt):
            asked.append(interaction)
            if len(asked) > 1:
                raise _AskedAgain
            return v["x"]
        return True  # :26055 yes, a lawyer

    player = _player(ka=v["ka"], rank=v["ra"], po=400)
    handler = lambda ctx: police.sentence(ctx, police.Arrest())  # noqa: E731
    try:
        run = _drive(handler, _state(player), (v["r"],), answer)
    except _AskedAgain:
        return "asked again"
    after = run.state.players[0]
    return (after.ka, game.wanted(after).jail_months)


# --- :4050 the police chief's months age ---------------------------------------------
Q_4050 = q(4050, "pl(sp)=pl(sp)+(pl(sp)>0)")


def _basic_bribe_aging(v: Values) -> Any:
    return Q_4050.assign({"sp": 1, "pl(1)": v["pl"]})


def _engine_bribe_aging(v: Values) -> Any:
    run = _drive(
        HANDLERS["upkeep.turn_start"], _state(_player(wanted=Wanted(bribe_months=v["pl"])))
    )
    return game.wanted(run.state.players[0]).bribe_months


# --- :21011-21020 pol, the chief bribe ------------------------------------------------
Q_21011 = q(21011, "p=1000*x")
Q_21011_ZERO = q(21011, "x=0")
Q_21015 = q(21015, "ka(sp)<p")
Q_21020_KA = q(21020, "ka(sp)=ka(sp)-p")
Q_21020_PL = q(21020, "pl(sp)=pl(sp)+x+1")


def _basic_chief(v: Values) -> Any:
    b = {"sp": 1, "x": v["x"], "ka(1)": v["ka"], "pl(1)": v["pl"]}
    b["p"] = Q_21011.assign(b)
    if Q_21011_ZERO.holds(b) or Q_21015.holds(b):
        return (b["ka(1)"], b["pl(1)"])
    return (Q_21020_KA.assign(b), Q_21020_PL.assign(b))


def _engine_chief(v: Values) -> Any:
    def answer(interaction: Any) -> Any:
        # This stepper hands the answer straight to the handler, so it plays the
        # driver's range check: an answer outside the prompt's bounds is asked again.
        if not interaction.min <= v["x"] <= interaction.max:
            raise _AskedAgain
        return v["x"]

    player = _player(ka=v["ka"], po=909, wanted=Wanted(bribe_months=v["pl"]))
    run = _drive(HANDLERS["pol.bribe"], _state(player), answer=answer)
    p = run.state.players[0]
    return (p.ka, game.wanted(p).bribe_months)


# --- :21130-21140 pol, the guards' price ----------------------------------------------
Q_21130 = q(21130, "p=500*int(rnd(1)*5)+3000")
Q_21135 = q(21135, "ka(sp)<p")
Q_21140 = q(21140, "ka(sp)=ka(sp)-p")


def _jailed_pair(ka: int, freed_ka: int = 0) -> GameState:
    """Player 0 active at rank 1 (no phantom), player 1 jailed for 2 months."""
    state = _state(_player(ka=ka))
    inmate = _player(name="q", ka=freed_ka, wanted=Wanted(jail_months=2))
    return replace(
        state, players=(state.players[0], inmate), clock=Clock(active_player=0, player_count=2)
    )


def _basic_release(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ka(1)": v["ka"], "rnd(1)": v["r"]}
    b["p"] = Q_21130.assign(b)
    if Q_21135.holds(b):
        return b["ka(1)"]
    return Q_21140.assign(b)


def _engine_release(v: Values) -> Any:
    def answer(interaction: Any) -> Any:
        if isinstance(interaction, Confirm):
            return True
        return 1 if interaction.key == "locations.pol.free_prompt" else 0

    run = _drive(HANDLERS["pol.free"], _jailed_pair(v["ka"]), draws=(0.5, v["r"]), answer=answer)
    assert run.draws_used == 2
    return run.state.players[0].ka


# --- :21252-21255 pol, the freed player's thanks --------------------------------------
Q_21252 = q(21252, "y>ka(x)")
Q_21253 = q(21253, "y>ka(x)ory<0")
Q_21255_KX = q(21255, "ka(x)=ka(x)-y")
Q_21255_KSP = q(21255, "ka(sp)=ka(sp)+y")


def _basic_thanks(v: Values) -> Any:
    b = {"sp": 1, "x": 2, "y": v["y"], "ka(1)": 1000, "ka(2)": v["kax"]}
    if Q_21252.holds(b) or Q_21253.holds(b):
        return "asked again"
    return (Q_21255_KSP.assign(b), Q_21255_KX.assign(b))


def _engine_thanks(v: Values) -> Any:
    asks = []

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, Confirm):
            return True
        if interaction.key == "locations.pol.free_prompt":
            return 1
        asks.append(interaction)
        if len(asks) > 1:
            raise _AskedAgain
        return v["y"]

    # The price (3000) is paid first, so the rescuer starts at 4000 and holds 1000.
    try:
        run = _drive(
            HANDLERS["pol.free"], _jailed_pair(4000, v["kax"]), draws=(0.5, 0.0), answer=answer
        )
    except _AskedAgain:
        return "asked again"
    return (run.state.players[0].ka, run.state.players[1].ka)


# --- :14010-14050 aut, the showroom, the trade-in and the sale --------------------------
Q_14010_X = q(14010, "x=2")
Q_14010_LN = q(14010, "ln=2")
Q_14010_X2 = q(14010, "x=x+1")
Q_14030 = q(14030, "(y-1)>x")
Q_14035_P = q(14035, "p=3000+1000*(y-1)")
Q_14035 = q(14035, "ka(sp)<p")
Q_14040 = q(14040, "tm(sp)=0")
Q_14045_Q = q(14045, "q=1000+1000*tm(sp)")
Q_14045_IF = q(14045, "tm(sp)=5")
Q_14045_STOLEN = q(14045, "q=1000")
Q_14050_KA = q(14050, "ka(sp)=ka(sp)-p+q")
Q_14050_MS = q(14050, "ms=tr(y)-tr(tm(sp))+ms")
Q_14050_TM = q(14050, "tm(sp)=y")


def _basic_car_sale(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ln": v["ln"], "y": v["y"], "q": 0}
    b.update({"ka(1)": v["ka"], "tm(1)": v["tm"], "ms": 3})
    b.update({f"tr({i})": w["tr"] for i, w in enumerate(_VEHICLES)})
    b["x"] = Q_14010_X.assign(b)
    if Q_14010_LN.holds(b):
        b["x"] = Q_14010_X2.assign(b)
    if Q_14030.holds(b):
        return "read again"
    b["p"] = Q_14035_P.assign(b)
    if Q_14035.holds(b):
        return (b["ka(1)"], b["ms"], b["tm(1)"])
    if not Q_14040.holds(b):
        b["q"] = Q_14045_Q.assign(b)
        if Q_14045_IF.holds(b):
            b["q"] = Q_14045_STOLEN.assign(b)
    b["ka(1)"] = Q_14050_KA.assign(b)
    b["ms"] = Q_14050_MS.assign(b)
    return (b["ka(1)"], b["ms"], Q_14050_TM.assign(b))


def _engine_car_sale(v: Values) -> Any:
    asked: list[Any] = []

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, Confirm):
            return True  # :14047 the trade-in taken
        asked.append(interaction)
        if len(asked) > 1:
            return 0  # the showroom again (a refused sale): leave
        if not interaction.min <= v["y"] <= interaction.max:
            raise _AskedAgain
        return v["y"]

    player = _player(ka=v["ka"], vehicle=v["tm"], ms=3, last_location=v["ln"])
    try:
        run = _drive(HANDLERS["aut.buy"], _state(player), answer=answer)
    except _AskedAgain:
        return "read again"
    p = run.state.players[0]
    return (p.ka, p.ms, p.vehicle)


# --- :14100-14110 aut, the crowd and the lock ------------------------------------------
Q_14100 = q(14100, "ln<>4andint(rnd(1)*3)<>0")
Q_14110 = q(14110, "int(rnd(1)*(in/40+kr/30))=0")


def _basic_car_theft(v: Values) -> Any:
    b: dict[str, Any] = {"ln": v["ln"], "rnd(1)": v["r0"]}
    if Q_14100.holds(b):
        return "crowded"
    b.update({"rnd(1)": v["r"], "in": v["in"], "kr": v["kr"]})
    return "caught" if Q_14110.holds(b) else "stolen"


def _engine_car_theft(v: Values) -> Any:
    thief = Gangster(name="t", energie=40, kraft=v["kr"], intelligenz=v["in"])
    player = _player(last_location=v["ln"], roster=(thief,))
    try:
        run = _drive(
            HANDLERS["aut.steal"], _state(player), draws=(v["r0"], v["r"]), answer=lambda i: 1
        )
    except _FightStarted:
        return "caught"
    if run.draws_used == 1:
        assert run.state.players[0].vehicle == 0
        return "crowded"
    assert run.state.players[0].vehicle == 5
    return "stolen"


# --- :18015-18052 sub, the ticket, the manual, the catch and the loot ----------------
Q_18025 = q(18025, "ka(sp)<50")
Q_18030 = q(18030, "ka(sp)=ka(sp)-50")
Q_18040 = q(18040, "int(rnd(1)*15)=10")
Q_18041 = q(18041, "int(rnd(1)*(in/10))")
Q_18045 = q(18045, "int(rnd(1)*4)-(w=2)-(la<>9)")
Q_18047 = q(18047, "ka(sp)=ka(sp)+50")
Q_18049 = q(18049, "ka(sp)=ka(sp)+100")
Q_18050 = q(18050, "ka(sp)=ka(sp)+500")
Q_18051 = q(18051, "ka(sp)=ka(sp)+800")
#: :18045's ``on ... goto18047,18048,18049,18050,18051``; 0 falls through to :18046, and
#: :18046 and :18048 pay nothing.
_SUB_LOOT = {1: Q_18047, 3: Q_18049, 4: Q_18050, 5: Q_18051}


def _basic_pickpocket(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ka(1)": v["ka"], "w": v["w"], "la": v["la"], "in": v["in"]}
    if v["w"] == 2:  # :18010 ``onwgoto18035,18015``
        if Q_18025.holds(b):
            return ("broke", b["ka(1)"])
        b["ka(1)"] = Q_18030.assign(b)
    b["rnd(1)"] = v["r0"]
    if Q_18040.holds(b):
        return ("manual", b["ka(1)"])
    b["rnd(1)"] = v["r1"]
    if not Q_18041.holds(b):
        return ("caught", b["ka(1)"])
    b["rnd(1)"] = v["r2"]
    index = int(Q_18045.expr(b))
    if index in _SUB_LOOT:
        b["ka(1)"] = _SUB_LOOT[index].assign(b)
    return (f"loot {index}", b["ka(1)"])


_SUB_ITEMS = ("handbag", "camera", "pearls", "watch", "wallet", "diamond")


def _engine_pickpocket(v: Values) -> Any:
    thief = Gangster(name="t", energie=40, kraft=30, intelligenz=v["in"])
    player = _player(ka=v["ka"], last_la=v["la"], last_location=1, roster=(thief,))
    key = "sub.train" if v["w"] == 2 else "sub.platform"

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, Confirm):
            return True
        if isinstance(interaction, PromptChoice):
            return 2  # surrender at the arrest (:26030 key 3)
        return 1  # the thief (:1145)

    run = _drive(HANDLERS[key], _state(player), (v["r0"], v["r1"], v["r2"]), answer)
    keys = [m.key for m in run.shown]
    player_after = run.state.players[0]
    ka = player_after.ka
    if "system.not_enough_money" in keys:
        return ("broke", ka)
    if "locations.sub.loot_manual" in keys:
        assert game.safe_skill(player_after) == 5
        return ("manual", ka)
    if "locations.sub.caught" in keys:
        return ("caught", ka)
    (item,) = [k.rsplit("_", 1)[1] for k in keys if k.startswith("locations.sub.loot_")]
    return (f"loot {_SUB_ITEMS.index(item)}", ka)


# --- :19015-19040 bhf, the mail train, and the heist payout :20050-20060 ------------------
Q_19015 = q(19015, "tp(sp)<>1")
Q_19016 = q(19016, "gz(sp)<3")
Q_19016_TP = q(19016, "tp(sp)=0")
Q_20050_P = q(20050, "p=int(rnd(1)*3000)+4000-500*(la=10andln=1)")
Q_20050_X = q(20050, "x=tp(sp)")
Q_20051 = q(20051, "(x=1andla=9)or(x=2andla=10andln=2)or(x=3andla=13)")
Q_20051_TP = q(20051, "tp(sp)=0")
Q_20051_P = q(20051, "p=p+3000")
Q_20060 = q(20060, "ka(sp)=ka(sp)+p")


def _basic_mail_train(v: Values) -> Any:
    """The station (``la=9``, ``ln=1``); the guards' fight is won (:19030)."""
    b: dict[str, Any] = {"sp": 1, "tp(1)": v["tp"], "gz(1)": v["gz"], "ka(1)": 1000}
    b.update({"la": 9, "ln": 1})
    if Q_19015.holds(b):
        return ("no train", b["ka(1)"], b["tp(1)"])
    if Q_19016.holds(b):
        return ("too few", b["ka(1)"], Q_19016_TP.assign(b))
    b["rnd(1)"] = v["r"]
    b["p"] = Q_20050_P.assign(b)
    b["x"] = Q_20050_X.assign(b)
    if Q_20051.holds(b):
        b["tp(1)"] = Q_20051_TP.assign(b)
        b["p"] = Q_20051_P.assign(b)
    return ("robbed", Q_20060.assign(b), b["tp(1)"])


def _engine_mail_train(v: Values) -> Any:
    player = _player(
        ka=1000,
        last_la=9,
        last_location=1,
        tip_target=v["tp"],
        roster=tuple(_gangster() for _ in range(v["gz"])),
    )
    run = _drive(
        HANDLERS["bhf.mail_train"],
        _state(player),
        draws=(v["r"],),
        fight=CombatResult(winner=1, losses=(0, 3)),
    )
    keys = [m.key for m in run.shown]
    after = run.state.players[0]
    outcome = (
        "no train"
        if "locations.bhf.no_train" in keys
        else "too few"
        if "locations.bhf.too_few" in keys
        else "robbed"
    )
    return (outcome, after.ka, game.tip_target(after))


# --- :20009-20060 ban, the hold-up, through the heist payout -----------------------------
Q_20009 = q(20009, "gz(sp)=1")
Q_20010 = q(20010, "int(rnd(1)*3)=0")
Q_20012 = q(20012, "gz(0)=3-(ln=1)")


def _basic_holdup(v: Values) -> Any:
    """The bank (``la=10``) on tile ``ln``; the guards' fight, if any, is won (:20015)."""
    b: dict[str, Any] = {"sp": 1, "tp(1)": v["tp"], "gz(1)": v["gz"], "ka(1)": 1000}
    b.update({"la": 10, "ln": v["ln"]})
    if Q_20009.holds(b):
        return ("alone", b["ka(1)"], b["tp(1)"])
    b["rnd(1)"] = v["r0"]
    fought = not Q_20010.holds(b)
    b["rnd(1)"] = v["r"]
    b["p"] = Q_20050_P.assign(b)
    b["x"] = Q_20050_X.assign(b)
    if Q_20051.holds(b):
        b["tp(1)"] = Q_20051_TP.assign(b)
        b["p"] = Q_20051_P.assign(b)
    return ("robbed", fought, Q_20060.assign(b), b["tp(1)"])


def _engine_holdup(v: Values) -> Any:
    player = _player(
        ka=1000,
        rank=3,
        last_la=10,
        last_location=v["ln"],
        tip_target=v["tp"],
        roster=tuple(_gangster() for _ in range(v["gz"])),
    )
    run = _drive(
        HANDLERS["ban.holdup"],
        _state(player),
        draws=(v["r0"], v["r"]),
        fight=CombatResult(winner=1, losses=(0, 3)),
    )
    keys = [m.key for m in run.shown]
    after = run.state.players[0]
    if "locations.ban.alone" in keys:
        return ("alone", after.ka, game.tip_target(after))
    fought = "locations.ban.guards" in keys
    return ("robbed", fought, after.ka, game.tip_target(after))


def _basic_bank_guards(v: Values) -> Any:
    return Q_20012.assign({"ln": v["ln"]})


def _engine_bank_guards(v: Values) -> Any:
    player = _player(rank=3, last_la=10, last_location=v["ln"], roster=(_gangster(),) * 2)
    # :20010's roll 0.5: int(0.5*3)=1, the guards fight.
    gen = HANDLERS["ban.holdup"](Ctx(state=_state(player), rng=StubRng((0.5,))))
    interaction = next(gen)
    while not isinstance(interaction, StartCombat):
        interaction = gen.send(Ack)
    gen.close()
    assert interaction.scenario is not None and interaction.scenario.sides is not None
    return len(interaction.scenario.sides[1])


# --- :2002-2003, :23000-23030, :24000-24020 the map win flows ---------------------------
Q_2002 = q(2002, "tp(sp)=3")
Q_2003 = q(2003, "tp(sp)=5")
Q_23010 = q(23010, "gz(sp)<3")
Q_23010_TP = q(23010, "tp(sp)=0")
Q_24020_KA = q(24020, "ka(sp)=ka(sp)+7000")
Q_24020_AG = q(24020, "ag(sp)=ag(sp)or1")
Q_24020_TP = q(24020, "tp(sp)=0")


def _basic_armed(v: Values) -> Any:
    """The cells ``:2002``/``:2003`` poke off the street code for the held tip."""
    b = {"sp": 1, "tp(1)": v["tp"]}
    return {cell for cell, quote in ((569, Q_2002), (861, Q_2003)) if quote.holds(b)}


def _engine_armed(v: Values) -> Any:
    return win_flows.armed_cells(_state(_player(tip_target=v["tp"])))


def _win_flow(cell: int) -> Callable[[Ctx], Any]:
    """The special-cell hook for a move onto ``cell``."""
    return functools.partial(
        HANDLERS[SPECIAL_CELL_HOOK_KEY], cell=cell, la={569: 13, 861: 14}[cell]
    )


def _basic_transport(v: Values) -> Any:
    """The cash transport (``la=13``, ``ln=1``) with tip 3; the escort's fight is won."""
    b: dict[str, Any] = {"sp": 1, "tp(1)": 3, "gz(1)": v["gz"], "ka(1)": 1000}
    b.update({"la": 13, "ln": 1})
    if Q_23010.holds(b):
        return ("too few", b["ka(1)"], Q_23010_TP.assign(b))
    b["rnd(1)"] = v["r"]
    b["p"] = Q_20050_P.assign(b)
    b["x"] = Q_20050_X.assign(b)
    if Q_20051.holds(b):
        b["tp(1)"] = Q_20051_TP.assign(b)
        b["p"] = Q_20051_P.assign(b)
    return ("robbed", Q_20060.assign(b), b["tp(1)"])


def _engine_transport(v: Values) -> Any:
    player = _player(ka=1000, tip_target=3, roster=tuple(_gangster() for _ in range(v["gz"])))
    run = _drive(
        _win_flow(569), _state(player), draws=(v["r"],), fight=CombatResult(winner=1, losses=(0, 3))
    )
    after = run.state.players[0]
    outcome = "robbed" if any(m.key == "locations.ban.loot" for m in run.shown) else "too few"
    return (outcome, after.ka, game.tip_target(after))


def _basic_mayor(v: Values) -> Any:
    """The mayor hit with tip 5; both fights are won."""
    b: dict[str, Any] = {"sp": 1, "tp(1)": 5, "ka(1)": v["ka"], "ag(1)": v["ag"]}
    return (Q_24020_KA.assign(b), Q_24020_AG.assign(b), Q_24020_TP.assign(b))


def _engine_mayor(v: Values) -> Any:
    player = _player(ka=v["ka"], tip_target=5, contraband=_marks(v["ag"]))
    run = _drive(_win_flow(861), _state(player), fight=CombatResult(winner=1, losses=(0, 1)))
    after = run.state.players[0]
    return (after.ka, _ag(after), game.tip_target(after))


# --- :27020-27045 the gang war duel's consequences ----------------------------------------
Q_27020_A = q(27020, "a=ks(s)")
Q_27020_B = q(27020, "b=ks(1-(s=1))")
Q_27025 = q(27025, "p=int(rnd(1)*ka(b)/6)+int(ka(b)/4)")
Q_27028 = q(27028, "tm(b)=0")
Q_27031_TMA = q(27031, "tm(a)=tm(b)")
Q_27031_TMB = q(27031, "tm(b)=0")
Q_27035_KAA = q(27035, "ka(a)=ka(a)+p")
Q_27035_KAB = q(27035, "ka(b)=ka(b)-p")
Q_27035_AGA = q(27035, "ag(a)=ag(a)or(ag(b)and1)")
Q_27035_AGB = q(27035, "ag(b)=ag(b)and254")
Q_27040_X = q(27040, "x=tk(tm(a))-ta(a)")
Q_27040_IF = q(27040, "x>ta(b)")
Q_27040_CAP = q(27040, "x=ta(b)")
Q_27041_TAA = q(27041, "ta(a)=ta(a)+x")
Q_27041_TAB = q(27041, "ta(b)=ta(b)-x")
Q_27045 = q(27045, "ms=ms-10")


def _basic_gang_war(v: Values) -> Any:
    """sp=1 attacks us=2 (``ks(1)=us:ks(2)=sp``); side ``s`` wins; faithful scoring."""
    b: dict[str, Any] = {"sp": 1, "s": v["s"], "ks(1)": 2, "ks(2)": 1, "x8": 1.0, "ms": 21}
    for i, (ka, tm, ta, ag, gf) in enumerate(v["players"], start=1):
        b.update({f"ka({i})": ka, f"tm({i})": tm, f"ta({i})": ta, f"ag({i})": ag})
        b[f"gf({i})"] = gf
    for i, vehicle in enumerate(_VEHICLES):
        b[f"tk({i})"] = vehicle["tank"]
    b["a"] = Q_27020_A.assign(b)
    b["b"] = Q_27020_B.assign(b)
    a, lo = int(b["a"]), int(b["b"])
    b["rnd(1)"] = v["r"]
    b["p"] = Q_27025.assign(b)
    if not Q_27028.holds(b) and v["ans"] == "j":  # :27031 ifx$="j"then
        b[f"tm({a})"] = Q_27031_TMA.assign(b)
        b[f"tm({lo})"] = Q_27031_TMB.assign(b)
    ka_a, ka_b = Q_27035_KAA.assign(b), Q_27035_KAB.assign(b)
    ag_a, ag_b = Q_27035_AGA.assign(b), Q_27035_AGB.assign(b)
    b.update({f"ka({a})": ka_a, f"ka({lo})": ka_b, f"ag({a})": ag_a, f"ag({lo})": ag_b})
    b["x"] = Q_27040_X.assign(b)
    if Q_27040_IF.holds(b):
        b["x"] = Q_27040_CAP.assign(b)
    ta_a, ta_b = Q_27041_TAA.assign(b), Q_27041_TAB.assign(b)
    b.update({f"ta({a})": ta_a, f"ta({lo})": ta_b})
    # :27041 x=3:gosub1160:y=sp:sp=b:x=-1:gosub1160:sp=y
    for scored, x in ((1, 3), (lo, -1)):
        b.update({"sp": scored, "x": x})
        b[f"gf({scored})"] = Q_1160.assign(b)
        if Q_1160_CAP.holds(b):
            b[f"gf({scored})"] = 100
        if Q_1161_FLOOR.holds(b):
            b[f"gf({scored})"] = 0
    ms = Q_27045.assign(b)
    return tuple(
        (b[f"ka({i})"], b[f"tm({i})"], b[f"ta({i})"], b[f"ag({i})"], b[f"gf({i})"]) for i in (1, 2)
    ) + (ms,)


def _engine_gang_war(v: Values) -> Any:
    players = tuple(
        _player(
            name=f"p{i}",
            ka=ka,
            vehicle=tm,
            gf=gf,
            ms=21,
            contraband=replace(_marks(ag), alcohol_barrels=ta),
        )
        for i, (ka, tm, ta, ag, gf) in enumerate(v["players"])
    )
    state = GameState(
        players=players,
        clock=Clock(active_player=0, player_count=2, month=4),
        config=Config(formula_params={**_PARAMS, "score_mult": 1.0}),
    )

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, PromptInt):
            return 2  # the defender, us=2
        if isinstance(interaction, Confirm):
            return v["ans"] == "j"
        return None

    run = _drive(
        HANDLERS["turn.gang_war"],
        state,
        draws=(v["r"],),
        answer=answer,
        fight=CombatResult(winner=v["s"], losses=(0, 0)),
    )
    after = run.state.players
    return tuple(
        (p.ka, p.vehicle, game.contraband(p).alcohol_barrels, _ag(p), p.gf) for p in after
    ) + (after[0].ms,)


#: ``(ka, tm, ta, ag, gf)`` of the attacker and the defender: cash a multiple of 6 and
#: not, on foot or driving, barrels below, at and above a tank, each passport bit,
#: and a score at the clamp.
_GANG_WAR_SEATS = (
    ((6000, 0, 0, 0, 50.0), (7, 3, 40, 3, 50.0)),
    ((1, 1, 180, 1, 99.0), (6001, 4, 10, 0, 0.5)),
    ((0, 4, 30, 2, 100.0), (0, 0, 0, 1, 0.0)),
    ((1001, 3, 200, 3, 0.0), (5, 1, 120, 2, 99.0)),
)


# --- :27100-27150 the prison brawl ----------------------------------------------------
Q_27115 = q(27115, "ka(sp)<3000")
Q_27120 = q(27120, "ka(sp)=ka(sp)-3000")
Q_27140 = q(27140, "x=int(rnd(1)*2)+1")
Q_27145 = q(27145, "gs(us)=gs(us)+x")
Q_27150_MS = q(27150, "ms=ms-10")
Q_27150_X = q(27150, "x=2")


def _basic_prison_brawl(v: Values) -> Any:
    """sp=1 pays for the brawl against the jailed us=2 ("j" at :1110); side ``s`` wins."""
    b: dict[str, Any] = {"sp": 1, "us": 2, "s": v["s"], "x8": 1.0, "ms": 21}
    b.update({"ka(1)": v["ka"], "gf(1)": v["gf"], "gs(2)": v["gs"], "rnd(1)": v["r"]})
    if Q_27115.holds(b):  # :27115 goto1125 -- nothing changes
        return (b["ka(1)"], b["gs(2)"], b["gf(1)"], b["ms"])
    b["ka(1)"] = Q_27120.assign(b)
    if b["s"] != 2:  # :27135 ifs=2goto27146
        b["x"] = Q_27140.assign(b)
        b["gs(2)"] = Q_27145.assign(b)
    b["ms"] = Q_27150_MS.assign(b)
    b["x"] = Q_27150_X.assign(b)
    b["gf(1)"] = Q_1160.assign(b)
    if Q_1160_CAP.holds(b):
        b["gf(1)"] = 100
    if Q_1161_FLOOR.holds(b):
        b["gf(1)"] = 0
    return (b["ka(1)"], b["gs(2)"], b["gf(1)"], b["ms"])


def _engine_prison_brawl(v: Values) -> Any:
    players = (
        _player(name="p0", ka=v["ka"], gf=v["gf"], ms=21),
        _player(name="p1", wanted=Wanted(jail_months=v["gs"])),
    )
    state = GameState(
        players=players,
        clock=Clock(active_player=0, player_count=2, month=4),
        config=Config(formula_params={**_PARAMS, "score_mult": 1.0}),
    )

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, PromptInt):
            return 2  # the jailed defender, us=2
        if isinstance(interaction, Confirm):
            return True  # :1110 "j"
        return None

    run = _drive(
        HANDLERS["turn.gang_war"],
        state,
        draws=(v["r"],) if v["s"] == 1 and v["ka"] >= 3000 else (),
        answer=answer,
        fight=CombatResult(winner=v["s"], losses=(0, 0)),
    )
    attacker, jailed = run.state.players
    return (attacker.ka, game.wanted(jailed).jail_months, attacker.gf, attacker.ms)


# --- :20100-20150 ban, the night safe-crack ----------------------------------------
Q_20100 = q(20100, "in>=40andkr>=15andbt>=20")
Q_20110_RD = q(20110, "rd(i)=1+i")
Q_20110_CD = q(20110, "cd(i)=int(rnd(1)*10)")
Q_20111_Y = q(20111, "y=20+int(in/10)+3*(ln=1)+s9(sp)")
Q_20111_S9 = q(20111, "s9(sp)=s9(sp)-1")
Q_20111_CAP = q(20111, "s9(sp)<0")
Q_20116_X = q(20116, "x=x-133")
Q_20116_RD = q(20116, "rd(x)=rd(x)+1")
Q_20116_WRAP = q(20116, "rd(x)=10")
Q_20125 = q(20125, "int(rnd(1)*(in/8))=0orrd(x)<>cd(x)")
Q_20130 = q(20130, "rd(i)=cd(i)")
Q_20135_Y = q(20135, "y=y-1")
Q_20135 = q(20135, "y>0")


def _basic_safe_gate(v: Values) -> Any:
    return (
        "trained" if Q_20100.holds({"in": v["in"], "kr": v["kr"], "bt": v["bt"]}) else "untrained"
    )


def _engine_safe_gate(v: Values) -> Any:
    boss = _gangster(kr=v["kr"], in_=v["in"], bt=v["bt"])
    player = _player(rank=3, last_la=10, last_location=3, roster=(boss,))
    run = _drive(HANDLERS["ban.safe"], _state(player), answer=lambda _: 0)  # :20104 y=0
    keys = [m.key for m in run.shown]
    return "trained" if "locations.ban.safe_who" in keys else "untrained"


#: The cracker's strategy, the same on both sides: turn each dial to the code in turn
#: (F1, F3, F5 are keys 133, 134, 135), then keep turning the third.
def _safe_keys(code: Sequence[int]) -> list[int]:
    keys = [133 + i for i in range(3) for _ in range((code[i] - (1 + i)) % 10)]
    return keys + [135] * 60


def _safe_draws(v: Values) -> list[float]:
    slips = v["slips"]
    return [*v["code"], *(slips[i % len(slips)] for i in range(60))]


def _basic_safe(v: Values) -> Any:
    """:20110-20135: the dials, the code, the tries, then each press."""
    draws = _safe_draws(v)
    b: dict[str, Any] = {"sp": 1, "in": v["in"], "ln": v["ln"], "s9(1)": v["s9"]}
    for i in range(3):  # :20110 ``fori=0to2``
        b["i"] = i
        b["rnd(1)"] = draws[i]
        b[f"rd({i})"] = Q_20110_RD.assign(b)
        b[f"cd({i})"] = Q_20110_CD.assign(b)
    b["y"] = Q_20111_Y.assign(b)
    b["s9(1)"] = Q_20111_S9.assign(b)
    if Q_20111_CAP.holds(b):
        b["s9(1)"] = 0
    keys = _safe_keys([int(b[f"cd({i})"]) for i in range(3)])
    presses = 0
    while True:
        b["x"] = keys[presses]  # :20115
        b["rnd(1)"] = draws[3 + presses]
        presses += 1
        b["x"] = Q_20116_X.assign(b)
        x = int(b["x"])
        b[f"rd({x})"] = Q_20116_RD.assign(b)
        if Q_20116_WRAP.holds(b):
            b[f"rd({x})"] = 0
        if not Q_20125.holds(b):
            opened = True
            for i in range(3):  # :20130 ``fori=0to2:ifrd(i)=cd(i)thennext``
                b["i"] = i
                if not Q_20130.holds(b):
                    opened = False
                    break
            if opened:
                outcome = "cracked"
                break
        b["y"] = Q_20135_Y.assign(b)
        if not Q_20135.holds(b):
            outcome = "failed"
            break
    dials = tuple(int(b[f"rd({i})"]) for i in range(3))
    return (outcome, presses, int(b["s9(1)"]), dials)


def _engine_safe(v: Values) -> Any:
    from engine.substates import SUBSTATES

    cracker = _gangster(in_=v["in"])
    player = _player(rank=3, last_la=10, last_location=v["ln"], roster=(cracker,))
    player = replace(player, values={**player.values, "safe_skill": v["s9"]})
    draws = _safe_draws(v)
    keys = _safe_keys([int(r * 10) for r in v["code"]])
    outcome: list[Any] = []

    def handler(ctx: Ctx) -> Any:
        outcome.append((yield from SUBSTATES["safe_crack"](ctx, {"intelligenz": v["in"]})))
        return []

    pressed = iter(keys)
    run = _drive(handler, _state(player), draws, lambda _: next(pressed) - 133)
    dials = run.shown[-1].params
    return (
        outcome[0],
        len(run.asked),
        game.safe_skill(run.state.players[0]),
        (dials["d1"], dials["d2"], dials["d3"]),
    )


# --- :17210-17592 sgl, Jack's gang, the payout and what follows -----------------------
Q_17210 = q(17210, "gz(0)=3-2*(gz(sp)>5)")
Q_17500 = q(17500, "int(rnd(1)*3)=0")
Q_17505 = q(17505, "p=int(rnd(1)*200)+800-300*(ln=2)-200*(ln=7)-200*(ln=9)+600*(w=2)")
Q_17530 = q(17530, "p=int(rnd(1)*100)+100")
Q_17550 = q(17550, "ka(sp)=ka(sp)+p")
Q_17575 = q(17575, "p=int(rnd(1)*100)+300")
Q_17578 = q(17578, "ka(sp)=ka(sp)+p")
Q_17590 = q(17590, "p=int(rnd(1)*100)+200")


def _basic_jack_count(v: Values) -> Any:
    return Q_17210.assign({"sp": 1, "gz(1)": v["gz"]})


def _engine_jack_count(v: Values) -> Any:
    # Tile 1 refuses protection (:17200), so Jack's gang fights; count its men.
    player = _player(rank=2, last_la=7, last_location=1, roster=(_gangster(),) * v["gz"])
    gen = HANDLERS["sgl.protection"](Ctx(state=_state(player), rng=StubRng(())))
    interaction = next(gen)
    while not isinstance(interaction, StartCombat):
        interaction = gen.send(Ack)
    gen.close()
    assert interaction.scenario is not None and interaction.scenario.sides is not None
    return len(interaction.scenario.sides[1])


def _jack_won(w: int) -> CombatResult:
    """A won Jack fight (:17210) whose last shooter carries weapon ``w`` (:30215).
    Protection pays at once on tiles 2, 3 and 8 (``w=3``); elsewhere it goes through
    this fight."""
    return CombatResult(winner=1, losses=(0, 3), last_shooter=Fighter(weapon=w))


def _sgl_w(v: Values) -> int:
    return 3 if v["ln"] in (2, 3, 8) else v["w"]


def _basic_extortion(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ln": v["ln"], "w": _sgl_w(v), "ka(1)": 1000}
    b["rnd(1)"] = v["r0"]
    small = Q_17500.holds(b)
    b["rnd(1)"] = v["r"]
    b["p"] = Q_17530.assign(b) if small else Q_17505.assign(b)
    return Q_17550.assign(b)


def _engine_extortion(v: Values) -> Any:
    player = _player(rank=2, ka=1000, last_la=7, last_location=v["ln"])
    run = _drive(
        HANDLERS["sgl.protection"],
        _state(player),
        draws=(v["r0"], v["r"]),
        answer=lambda i: 0,
        fight=_jack_won(v["w"]),
    )
    return run.state.players[0].ka


def _basic_settle(v: Values) -> Any:
    """Take the small payment (:17530), then wreck the shop (:17575) or finish the owner
    (:17590); any fight is won, and the till or the pockets go to :17578."""
    b: dict[str, Any] = {"sp": 1, "ln": v["ln"], "ka(1)": 1000, "rnd(1)": v["r"]}
    b["p"] = Q_17530.assign(b)
    b["ka(1)"] = Q_17550.assign(b)
    b["rnd(1)"] = v["r2"]
    b["p"] = Q_17575.assign(b) if v["choice"] == 2 else Q_17590.assign(b)
    return Q_17578.assign(b)


def _engine_settle(v: Values) -> Any:
    player = _player(rank=2, ka=1000, last_la=7, last_location=v["ln"])
    run = _drive(
        HANDLERS["sgl.protection"],
        _state(player),
        draws=(0.0, v["r"], v["r2"]),
        answer=lambda i: v["choice"] - 1,
        fight=_jack_won(3),
    )
    return run.state.players[0].ka


# --- :13110-13127 waf range training ------------------------------------------------
Q_13110 = q(13110, "p=800+200*ra(sp)")
Q_13116 = q(13116, "ka(sp)<p")
Q_13125_KA = q(13125, "ka(sp)=ka(sp)-p")
Q_13125_KR = q(13125, "kr=kr+5")
Q_13125_CAP = q(13125, "kr>99")
Q_13126 = q(13126, "in=in+3-2*(ln=1)")
Q_13126_CAP = q(13126, "in>99")
Q_13127 = q(13127, "bt=bt+2-3*(ln=2)")
Q_13127_CAP = q(13127, "bt>99")


def _capped(b: dict[str, Any], name: str, gain: Quote, cap: Quote) -> None:
    """``name=<gain>:if<cap>then name=99`` — the shape of every waf training line."""
    b[name] = gain.assign(b)
    if cap.holds(b):
        b[name] = 99


def _basic_range(v: Values) -> Any:
    kr, in_, bt = v["stats"]
    b: dict[str, Any] = {"sp": 1, "ra(1)": v["ra"], "ka(1)": v["ka"], "ln": v["ln"]}
    b.update(kr=kr, **{"in": in_}, bt=bt)
    b["p"] = Q_13110.assign(b)
    if Q_13116.holds(b):
        return (b["ka(1)"], kr, in_, bt)
    b["ka(1)"] = Q_13125_KA.assign(b)
    _capped(b, "kr", Q_13125_KR, Q_13125_CAP)
    _capped(b, "in", Q_13126, Q_13126_CAP)
    _capped(b, "bt", Q_13127, Q_13127_CAP)
    return (b["ka(1)"], b["kr"], b["in"], b["bt"])


def _train_answer(venue: int) -> Callable[[Any], Any]:
    def answer(interaction: Any) -> Any:
        if isinstance(interaction, Confirm):
            return True
        if (
            isinstance(interaction, PromptChoice)
            and interaction.key == "locations.waf.venue_prompt"
        ):
            return venue
        return 1  # the gangster pick (:1145, 1-based): the only gangster

    return answer


def _engine_train(v: Values, venue: int, draws: Sequence[float] = ()) -> Any:
    kr, in_, bt = v["stats"]
    player = _player(
        ka=v["ka"], rank=v["ra"], last_location=v["ln"], roster=(_gangster(kr, in_, bt),)
    )
    run = _drive(HANDLERS["waf.train"], _state(player), draws, _train_answer(venue))
    p = run.state.players[0]
    g = p.roster[0]
    return (p.ka, g.attrs["kraft"], g.attrs["intelligenz"], g.attrs["brutalitaet"])


def _engine_range(v: Values) -> Any:
    return _engine_train(v, venue=0)


# --- :13150-13172 waf camp training ------------------------------------------------
Q_13150 = q(13150, "p=2500+500*ra(sp)")
Q_13160 = q(13160, "ka(sp)<p")
Q_13170_KA = q(13170, "ka(sp)=ka(sp)-p")
Q_13170_IN = q(13170, "in=in+fnr(0)")
Q_13170_CAP = q(13170, "in>99")
Q_13171 = q(13171, "bt=bt+fnr(0)")
Q_13171_CAP = q(13171, "bt>99")
Q_13172 = q(13172, "kr=kr+fnr(0)")
Q_13172_CAP = q(13172, "kr>99")


def _basic_camp(v: Values) -> Any:
    kr, in_, bt = v["stats"]
    r_in, r_bt, r_kr = v["r"]
    b: dict[str, Any] = {"sp": 1, "ra(1)": v["ra"], "ka(1)": v["ka"]}
    b.update(kr=kr, **{"in": in_}, bt=bt)
    b["p"] = Q_13150.assign(b)
    if Q_13160.holds(b):
        return (b["ka(1)"], kr, in_, bt)
    b["ka(1)"] = Q_13170_KA.assign(b)
    b["rnd(1)"] = r_in
    _capped(b, "in", Q_13170_IN, Q_13170_CAP)
    b["rnd(1)"] = r_bt
    _capped(b, "bt", Q_13171, Q_13171_CAP)
    b["rnd(1)"] = r_kr
    _capped(b, "kr", Q_13172, Q_13172_CAP)
    return (b["ka(1)"], b["kr"], b["in"], b["bt"])


def _engine_camp(v: Values) -> Any:
    return _engine_train(v, venue=1, draws=v["r"])


# --- :13065-13075 waf weapon-buy score, trade-in and settle ----------------------------
Q_13065_NEW = q(13065, "gw(sp,y)=0")
Q_13065_GF = q(13065, "gf(sp)=gf(sp)-x8*1*(gf(sp)<100)")
Q_13070 = q(13070, "q=int(wp(gw(sp,y))/1.5)")
Q_13072_UP = q(13072, "x>gw(sp,y)")
Q_13072_GF = q(13072, "gf(sp)=gf(sp)-x8*1*(gf(sp)<100)")
Q_13073_GF = q(13073, "gf(sp)=gf(sp)+x8*2*(gf(sp)>0)")
Q_13075 = q(13075, "ka(sp)=ka(sp)+q-wp(x)")


def _basic_buy(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "y": 1, "x": v["x"], "x8": v["x8"], "q": 0}
    b.update({"gw(1,1)": v["old"], "gf(1)": v["gf"], "ka(1)": 10**6})
    b.update({f"wp({i})": w["price"] for i, w in enumerate(_WEAPONS)})
    if Q_13065_NEW.holds(b):
        b["q"] = 0
        b["gf(1)"] = Q_13065_GF.assign(b)
    else:
        b["q"] = Q_13070.assign(b)
        if Q_13072_UP.holds(b):
            b["gf(1)"] = Q_13072_GF.assign(b)
        else:
            b["gf(1)"] = Q_13073_GF.assign(b)
    return (Q_13075.assign(b), b["gf(1)"])


def _engine_buy(v: Values) -> Any:
    # ln=2 stocks weapons 1..5; ln=1 at rank > 5 with a 0 grenade roll stocks 3..8.
    ln, draws = (2, ()) if v["x"] <= 5 else (1, (0.0,))
    player = _player(rank=6, gf=v["gf"], last_location=ln, roster=(_gangster(weapon=v["old"]),))

    def answer(interaction: Any) -> Any:
        if interaction.key == "turn.picker.prompt":
            return 1  # the only gangster (:1145, 1-based)
        if isinstance(interaction, PromptInt):
            return v["x"]
        if isinstance(interaction, Confirm):
            return True
        raise AssertionError(f"unexpected prompt {interaction!r}")

    run = _drive(HANDLERS["waf.buy"], _state(player, score_mult=v["x8"]), draws, answer)
    p = run.state.players[0]
    assert p.roster[0].weapon == v["x"]
    return (p.ka, p.gf)


# --- :25560 job completion score ----------------------------------------------------
Q_25560 = q(25560, "x=3+3*(jo(sp)=2)")


def _basic_completion(v: Values) -> Any:
    return Q_25560.assign({"sp": 1, "jo(1)": v["jo"]})


#: Per job type, the draws that make the last shift of a contract succeed: bouncer and
#: doorman get a quiet night (:25025 draw 0), the croupier is not caught (:25120) and
#: takes the minimum bonus; the killer's fight is answered as won.
_WINNING_SHIFT_DRAWS = {1: (0.0,), 2: (0.5, 0.0), 3: (0.0,), 4: ()}


def _engine_completion(v: Values) -> Any:
    # months_left=1: the shift completes the contract, which awards the :25560 score
    # through score_and_rank at x8=1, so the award is the change in gf.
    player = _player(gf=50, jobs=Job(type=v["jo"], pending_pay=1000, months_left=1))
    run = _drive(
        HANDLERS["job.shift"],
        _state(player),
        draws=_WINNING_SHIFT_DRAWS[v["jo"]],
        answer=lambda i: 1,  # the croupier's trick
        fight=SimpleNamespace(winner=1, losses=(0, 0)),
    )
    assert game.job(run.state.players[0]) == Job(), "the contract did not complete"
    return run.state.players[0].gf - 50


# --- :25120-25126 croupier catch check and bonus ---------------------------------------
Q_25120 = q(25120, "int(rnd(1)*(6-x))=0")
Q_25125 = q(25125, "p=int(rnd(1)*100*x)+300")
Q_25126 = q(25126, "ka(sp)=ka(sp)+p")


def _basic_croupier(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "x": v["trick"], "ka(1)": 0, "rnd(1)": v["r_catch"]}
    if Q_25120.holds(b):
        return "caught"
    b["rnd(1)"] = v["r"]
    b["p"] = Q_25125.assign(b)
    return Q_25126.assign(b)


def _engine_croupier(v: Values) -> Any:
    # months_left=2: a successful shift only ticks the contract, so ka moves by the bonus.
    player = _player(ka=0, jobs=Job(type=2, pending_pay=1000, months_left=2))
    try:
        run = _drive(
            HANDLERS["job.shift"],
            _state(player),
            draws=(v["r_catch"], v["r"]),
            answer=lambda i: v["trick"],
        )
    except _FightStarted:
        return "caught"
    return run.state.players[0].ka


# --- :12106/:12107 pub recruit offer count ------------------------------------------
Q_12106_Y0 = q(12106, "y=0")
Q_12106_Y = q(12106, "y=y-(sg(i)=0)")
Q_12106_CAP = q(12106, "y>3")
Q_12106_SET = q(12106, "y=3")
Q_12107_X = q(12107, "x=int(rnd(1)*(y+1))")
Q_12107_NONE = q(12107, "x=0orln=3")


def _sg(hired: Iterable[int]) -> dict[str, int]:
    """``sg(1..30)``: 1 for a hired candidate (:12165 ``sg(g(i))=1``), else 0."""
    return {f"sg({i})": int(i - 1 in hired) for i in range(1, 31)}


def _basic_pool(b: dict[str, Any]) -> None:
    """:12106 into ``b["y"]``: the unhired candidates among ``sg(1..30)``, at most 3."""
    b["y"] = Q_12106_Y0.assign(b)
    for i in range(1, 31):  # `fori=1to30 ... next`
        b["i"] = i
        b["y"] = Q_12106_Y.assign(b)
    if Q_12106_CAP.holds(b):
        b["y"] = Q_12106_SET.assign(b)


def _basic_offer_count(v: Values) -> Any:
    b: dict[str, Any] = {"ln": v["ln"], "rnd(1)": v["r"], **_sg(v["hired"])}
    _basic_pool(b)
    b["x"] = Q_12107_X.assign(b)
    return 0 if Q_12107_NONE.holds(b) else b["x"]


def _recruit_state(hired: Iterable[int], ln: int) -> GameState:
    """Rank 5 with an apartment and a one-man gang: every :12100-12105 guard passes."""
    player = _player(rank=5, last_location=ln)
    return replace(
        _state(player),
        values={**game.tenancy_values({1: 0}), **game.hired_values(hired)},
    )


def _offered(run: _Run) -> tuple[int, ...]:
    """The 1-based candidate numbers (``g(i)``) of the offers the handler showed."""
    names = [c["name"] for c in _CANDIDATES]
    return tuple(
        names.index(m.params["name"]) + 1
        for m in run.shown
        if m.key == "locations.pub.recruit_offer"
    )


def _engine_offer_count(v: Values) -> Any:
    # Every offer is declined. The pick draws land on distinct unhired candidates, so
    # each offer takes one draw whatever the count.
    free = [c for c in range(len(_CANDIDATES)) if c not in v["hired"]]
    picks = tuple((c + 0.5) / len(_CANDIDATES) for c in free[:3])
    run = _drive(
        HANDLERS["pub.recruit"],
        _recruit_state(v["hired"], v["ln"]),
        draws=(v["r"], *picks),
        answer=lambda i: False,
    )
    return len(_offered(run))


# --- :12110-12113 pub recruit pick and reroll ------------------------------------------
Q_12110_G = q(12110, "g(i)=int(rnd(1)*30)+1")
Q_12110_HIRED = q(12110, "sg(g(i))")
Q_12112 = q(12112, "g(j)=g(i)")

#: rnd(1) for every offer count: `int(rnd(1)*(y+1))` is y, the whole capped pool.
_ALL_OFFERS = 0.9990234375


def _basic_pick(v: Values) -> Any:
    """``(g(1), .., g(x))`` and the number of ``rnd(1)`` draws the picks took."""
    b: dict[str, Any] = {"rnd(1)": _ALL_OFFERS, **_sg(v["hired"])}
    _basic_pool(b)
    x = int(Q_12107_X.assign(b))
    draws = iter(v["draws"])
    used = 0
    for i in range(1, x + 1):  # :12108 `fori=1tox`
        b["i"] = i
        while True:  # :12110 rerolls by `goto12110`
            b["rnd(1)"] = next(draws)
            used += 1
            b[f"g({i})"] = Q_12110_G.assign(b)
            if Q_12110_HIRED.holds(b):
                continue
            # :12111 `ifi=1goto12115`, then :12112 `forj=1toi-1` over earlier picks.
            earlier = range(1, i)
            if any(Q_12112.holds({**b, "j": j}) for j in earlier):
                continue
            break
    return (tuple(int(b[f"g({i})"]) for i in range(1, x + 1)), used)


def _engine_pick(v: Values) -> Any:
    run = _drive(
        HANDLERS["pub.recruit"],
        _recruit_state(v["hired"], 1),
        draws=(_ALL_OFFERS, *v["draws"]),
        answer=lambda i: False,  # declined: only the batch check stops a repeat
    )
    return (_offered(run), run.draws_used - 1)


def _pick_draws(*candidates: float) -> tuple[float, ...]:
    """rnd(1) values landing on the given 0-based candidates; a fraction moves within one."""
    return tuple(c / len(_CANDIDATES) for c in candidates)


_PICK_GRID = tuple(
    {"hired": hired, "draws": _pick_draws(*draws)}
    for hired, draws in (
        ((), (5.5, 5.5, 7.5, 9.5)),  # a repeat of the first pick is redrawn
        ((), (0.0, 0.5, 29.99, 29.5, 1.0)),  # the edges of the 1..30 range
        ((), (3.5, 8.5, 3.5, 8.5, 3.5, 12.5)),  # the third pick repeats both earlier
        ((4,), (4.5, 4.5, 5.5, 4.5, 5.5, 6.5, 7.5)),  # a hired candidate is redrawn
        ((4, 6), (6.5, 4.5, 6.5, 7.5, 4.5, 7.5, 8.5, 0.5)),
        (tuple(range(27)), (0.5, 27.5, 26.5, 27.5, 28.5, 3.5, 29.5)),  # three left
        (tuple(range(28)), (28.5, 1.5, 28.5, 29.5)),  # two left: two offers
        (tuple(range(29)), (0.5, 12.5, 29.5)),  # one left
    )
)


# --- :12136-12160 pub recruit confirm, cash check, cap and hire ------------------------
Q_12140 = q(12140, "ka(sp)<p")
Q_12145 = q(12145, "gz(sp)=10")
Q_12160_KA = q(12160, "ka(sp)=ka(sp)-p")
Q_12160_GZ = q(12160, "gz(sp)=gz(sp)+1")

#: The batch offers 0-based candidates 0, 2, 1 (prices 3000, 2500, 2000): falling prices,
#: so a player short for the second can still afford the third.
_BATCH_IDS = (0, 2, 1)
_BATCH_PICKS = _pick_draws(*(c + 0.5 for c in _BATCH_IDS))


def _basic_batch(v: Values) -> Any:
    """Each offer's outcome, then ``gz(sp)`` and ``ka(sp)`` after the batch.

    :12136 a "no" goes to :12175 ``nexti``. :12140 too little cash prints :1125 and
    goes to :12175. :12145 at the cap goes to :12005, which re-enters :12100 and ends
    at :12105's "maximal 10 gangster!" (``goto1100``): the batch is over.
    """
    b: dict[str, Any] = {"sp": 1, "ka(1)": v["ka"], "gz(1)": v["gz"]}
    outcomes: list[str] = []
    for candidate, yes in zip(_BATCH_IDS, v["answers"]):  # :12108 `fori=1tox`
        b["p"] = _CANDIDATES[candidate]["price"]
        if not yes:
            outcomes.append("declined")
            continue
        if Q_12140.holds(b):
            outcomes.append("broke")
            continue
        if Q_12145.holds(b):
            outcomes.append("full")
            break
        b["ka(1)"] = Q_12160_KA.assign(b)
        b["gz(1)"] = Q_12160_GZ.assign(b)
        outcomes.append("hired")
    return (tuple(outcomes), b["gz(1)"], b["ka(1)"])


_BATCH_OUTCOMES = {
    "locations.pub.recruit_hired": "hired",
    "system.not_enough_money": "broke",
    "locations.pub.recruit_gang_full": "full",
}


def _engine_batch(v: Values) -> Any:
    player = _player(
        rank=5,
        last_location=1,
        ka=v["ka"],
        roster=tuple(_gangster() for _ in range(v["gz"])),
    )
    state = with_tenancy(_state(player), ln=1, owner=0)
    answers = iter(v["answers"])
    run = _drive(
        HANDLERS["pub.recruit"],
        state,
        draws=(_ALL_OFFERS, *_BATCH_PICKS),
        answer=lambda i: next(answers),
    )
    # An offer's outcome is the message that follows it; none means it was declined.
    keys = [m.key for m in run.shown]
    outcomes = [
        _BATCH_OUTCOMES.get(keys[k + 1], "declined") if k + 1 < len(keys) else "declined"
        for k, key in enumerate(keys)
        if key == "locations.pub.recruit_offer"
    ]
    after = run.state.players[0]
    return (tuple(outcomes), len(after.roster), after.ka)


_BATCH_GRID = _grid(
    gz=(1, 8, 9),
    ka=(0, 1999, 2000, 2999, 3000, 4999, 5000, 7499, 7500, 10**6),
    answers=tuple(itertools.product((True, False), repeat=3)),
)

# --- :16026-16040 casino --------------------------------------------------------------
Q_16026 = q(16026, "ka(sp)=ka(sp)-p")
Q_16030_WIN = q(16030, "int(rnd(1)*(1+x))=0")
Q_16030_P = q(16030, "p=int(p*(.5+x))")
Q_16040 = q(16040, "ka(sp)=ka(sp)+p")


def _basic_casino(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "x": v["x"], "p": v["stake"], "ka(1)": 10**6}
    b["rnd(1)"] = v["r"]
    b["ka(1)"] = Q_16026.assign(b)
    if Q_16030_WIN.holds(b):
        b["p"] = Q_16030_P.assign(b)
        b["ka(1)"] = Q_16040.assign(b)
    return b["ka(1)"]


def _engine_casino(v: Values) -> Any:
    def answer(interaction: Any) -> Any:
        return v["x"] - 1 if isinstance(interaction, PromptChoice) else v["stake"]

    run = _drive(HANDLERS["sph"], _state(_player()), draws=(v["r"],), answer=answer)
    return run.state.players[0].ka


# --- :12010-12035 pub alcohol buy ---------------------------------------------------------
Q_12010 = q(12010, "ln=4orln=5")
Q_12020_X = q(12020, "x=int(rnd(1)*200)+100")
Q_12020_P = q(12020, "p=int(rnd(1)*5)+5")
Q_12025_Q = q(12025, "q=tk(tm(sp))-ta(sp)")
Q_12025_CAP = q(12025, "q<x")
Q_12030 = q(12030, "ka(sp)<y*p")
Q_12035_TA = q(12035, "ta(sp)=ta(sp)+y")
Q_12035_KA = q(12035, "ka(sp)=ka(sp)-p*y")


def _basic_alcohol_buy(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "tm(1)": v["vehicle"], "ta(1)": v["ta"], "ka(1)": v["ka"]}
    b["ln"] = v["ln"]
    if not Q_12010.holds(b):
        return "no buy"
    b.update({f"tk({i})": veh["tank"] for i, veh in enumerate(_VEHICLES)})
    b["rnd(1)"] = v["r_x"]
    b["x"] = Q_12020_X.assign(b)
    b["rnd(1)"] = v["r_p"]
    b["p"] = Q_12020_P.assign(b)
    b["q"] = Q_12025_Q.assign(b)
    if Q_12025_CAP.holds(b):
        b["x"] = b["q"]
    b["y"] = min(v["want"], b["x"])  # the test's answer policy, same on both sides
    if b["y"] == 0 or Q_12030.holds(b):
        return (b["x"], b["ka(1)"], b["ta(1)"])
    return (b["x"], Q_12035_KA.assign(b), Q_12035_TA.assign(b))


def _engine_alcohol_buy(v: Values) -> Any:
    player = _player(
        ka=v["ka"],
        last_location=v["ln"],
        vehicle=v["vehicle"],
        contraband=Contraband(alcohol_barrels=v["ta"]),
    )
    run = _drive(
        HANDLERS["pub.drink"],
        _state(player),
        draws=(v["r_x"], v["r_p"]),
        answer=lambda i: min(v["want"], i.max),
    )
    if "locations.pub.drink_offer" not in [m.key for m in run.shown]:
        return "no buy"  # :12015's roll instead: refused, or the sell offer
    offered = run.asked[0].max
    p = run.state.players[0]
    return (offered, p.ka, game.contraband(p).alcohol_barrels)


# --- :12028-12035 pub alcohol buy, a negative count (faithful c64_input_negatives) ------
# The C64 INPUT stores a negative count (tests/fixtures/c64_input/vice_capture.txt), and
# no line of the buy refuses it: the price ``y*p`` is below 0, so :12030 passes, and
# :12035 sells the barrels to the pub and still scores ``x=2`` through the score routine.
Q_12028 = q(12028, "y>x")
Q_12029 = q(12029, "y=0")
Q_12035_X = q(12035, "x=2")


def _basic_alcohol_buy_negative(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "tm(1)": v["vehicle"], "ta(1)": v["ta"], "ka(1)": v["ka"]}
    b.update({f"tk({i})": veh["tank"] for i, veh in enumerate(_VEHICLES)})
    b["gf(1)"] = v["gf"]
    b["x8"] = 1.0
    b["rnd(1)"] = v["r_x"]
    b["x"] = Q_12020_X.assign(b)
    b["rnd(1)"] = v["r_p"]
    b["p"] = Q_12020_P.assign(b)
    b["q"] = Q_12025_Q.assign(b)
    if Q_12025_CAP.holds(b):
        b["x"] = b["q"]
    b["y"] = v["y"]
    if Q_12028.holds(b):
        return "asked again"
    if Q_12029.holds(b) or Q_12030.holds(b):
        return (b["ka(1)"], b["ta(1)"], b["gf(1)"])
    b["ta(1)"] = Q_12035_TA.assign(b)
    b["ka(1)"] = Q_12035_KA.assign(b)
    b["x"] = Q_12035_X.assign(b)
    b["gf(1)"] = Q_1160.assign(b)
    if Q_1160_CAP.holds(b):
        b["gf(1)"] = 100
    return (b["ka(1)"], b["ta(1)"], b["gf(1)"])


def _engine_alcohol_buy_negative(v: Values) -> Any:
    def answer(interaction: Any) -> Any:
        # The driver's range check: an answer outside the prompt's bounds is asked again.
        if not interaction.min <= v["y"] <= interaction.max:
            raise _AskedAgain
        return v["y"]

    player = _player(
        ka=v["ka"],
        gf=v["gf"],
        last_location=4,
        vehicle=v["vehicle"],
        contraband=Contraband(alcohol_barrels=v["ta"]),
    )
    try:
        run = _drive(
            HANDLERS["pub.drink"], _state(player), draws=(v["r_x"], v["r_p"]), answer=answer
        )
    except _AskedAgain:
        return "asked again"
    p = run.state.players[0]
    return (p.ka, game.contraband(p).alcohol_barrels, p.gf)


# --- :12050/:12075 pub alcohol sell -------------------------------------------------------
Q_12050 = q(12050, "x=int(rnd(1)*20)+10")
Q_12075_KA = q(12075, "ka(sp)=ka(sp)+y*x")
Q_12075_TA = q(12075, "ta(sp)=ta(sp)-y")


def _basic_alcohol_sell(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ta(1)": v["ta"], "ka(1)": 0, "y": v["y"], "rnd(1)": v["r"]}
    b["x"] = Q_12050.assign(b)
    return (Q_12075_KA.assign(b), Q_12075_TA.assign(b))


def _engine_alcohol_sell(v: Values) -> Any:
    # The :12015 1-in-2 gate is not compared pointwise: the source branches to the sell
    # offer when int(rnd(1)*2)=0, the port when the draw is nonzero (same odds, opposite
    # sense), so the engine is fed a passing draw here.
    player = _player(ka=0, last_location=1, contraband=Contraband(alcohol_barrels=v["ta"]))
    run = _drive(
        HANDLERS["pub.drink"], _state(player), draws=(0.75, v["r"]), answer=lambda i: v["y"]
    )
    p = run.state.players[0]
    return (p.ka, game.contraband(p).alcohol_barrels)


# --- :12215-12225 pub tip price and tip id -----------------------------------------------
Q_12215 = q(12215, "p=1000+int(rnd(1)*3)*500")
Q_12220 = q(12220, "ka(sp)<p")
Q_12225_KA = q(12225, "ka(sp)=ka(sp)-p")
Q_12225_TP = q(12225, "tp(sp)=int(rnd(1)*5)+1")


def _basic_tip(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ka(1)": v["ka"], "rnd(1)": v["r_p"]}
    b["p"] = Q_12215.assign(b)
    if Q_12220.holds(b):
        return (b["ka(1)"], None)
    b["ka(1)"] = Q_12225_KA.assign(b)
    b["rnd(1)"] = v["r_tp"]
    return (b["ka(1)"], Q_12225_TP.assign(b))


def _engine_tip(v: Values) -> Any:
    # First draw 0.0 passes the :12210 "nothing for you" gate (same sense in both).
    # The tip-4 stake offer is declined, so no stake money moves.
    player = _player(ka=v["ka"], rank=4)
    run = _drive(
        HANDLERS["pub.tip"],
        _state(player),
        draws=(0.0, v["r_p"], v["r_tp"]),
        answer=lambda i: i.key == "locations.pub.tip_confirm",
    )
    tips = [e.tip_type for e in run.effects if is_effect(e, TipSet)]
    return (run.state.players[0].ka, tips[0] if tips else None)


# --- :12305-12322 pub job type, pay and duration ------------------------------------------
Q_12305 = q(12305, "x=int(rnd(1)*4)+1")
Q_JOB_PAY = {
    1: q(12308, "p=int(rnd(1)*1000)+2000"),
    2: q(12311, "p=int(rnd(1)*500)+1000"),
    3: q(12316, "p=int(rnd(1)*500)+2000"),
    4: q(12322, "p=int(rnd(1)*500)+2000"),
}
Q_JOB_DURATION = {
    1: q(12308, "jd(sp)=3"),
    2: q(12311, "jd(sp)=2"),
    3: q(12316, "jd(sp)=2"),
    4: q(12322, "jd(sp)=1"),
}


def _basic_job(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "rnd(1)": v["r_type"]}
    x = int(Q_12305.assign(b))
    b["rnd(1)"] = v["r_pay"]
    return (x, Q_JOB_PAY[x].assign(b), Q_JOB_DURATION[x].assign(b))


def _engine_job(v: Values) -> Any:
    # First draw 0.5 passes the :12302 "nobody has work" gate (same sense in both).
    run = _drive(
        HANDLERS["pub.job"],
        _state(_player(rank=1)),
        draws=(0.5, v["r_type"], v["r_pay"]),
        answer=lambda i: True,
    )
    (job,) = [e for e in run.effects if is_effect(e, JobSet)]
    return (job.type, job.pending_pay, job.months_left)


# --- :15110-15155 kdh shop buy and sell prices ---------------------------------------------
Q_15110 = q(15110, "p=int(rnd(1)*11)*100+5000")
Q_15115 = q(15115, "ka(sp)<p")
Q_15120 = q(15120, "ka(sp)=ka(sp)-p")
Q_15150 = q(15150, "p=int(rnd(1)*11)*100+4500")
Q_15155 = q(15155, "ka(sp)=ka(sp)+p")


def _basic_shop_buy(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ka(1)": v["ka"], "rnd(1)": v["r"]}
    b["p"] = Q_15110.assign(b)
    if Q_15115.holds(b):
        return (b["ka(1)"], 0)
    return (Q_15120.assign(b), 1)


def _engine_shop_buy(v: Values) -> Any:
    player = _player(ka=v["ka"], last_location=1)
    run = _drive(HANDLERS["kdh.trade"], _state(player), (v["r"],), lambda i: True)
    p = run.state.players[0]
    return (p.ka, game.business(p).shop_tile)


def _basic_shop_sell(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ka(1)": 0, "rnd(1)": v["r"]}
    b["p"] = Q_15150.assign(b)
    return Q_15155.assign(b)


def _engine_shop_sell(v: Values) -> Any:
    player = _player(ka=0, last_location=1, business=Business(shop_tile=1))
    run = _drive(HANDLERS["kdh.trade"], _state(player), (v["r"],), lambda i: True)
    return run.state.players[0].ka


# --- :15320/:15321 kdh ambush loot -------------------------------------------------------
Q_15320 = q(15320, "p=int(rnd(1)*1000)+500")
Q_15321 = q(15321, "ka(sp)=ka(sp)+p")


def _basic_loot(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "ka(1)": 0, "rnd(1)": v["r"]}
    b["p"] = Q_15320.assign(b)
    return Q_15321.assign(b)


def _engine_loot(v: Values) -> Any:
    def handler(ctx: Ctx) -> Any:
        yield from apply_outcome(ctx, _AMBUSH, SimpleNamespace(winner=1))
        return []

    return _drive(handler, _state(_player(ka=0)), (v["r"],)).state.players[0].ka


# --- :310-350 new-game stats and cash ----------------------------------------------------
Q_350 = q(350, "x=int(rnd(1)*9)*5+10")
Q_310 = q(310, "kr=x")
Q_311 = q(311, "in=xor30")
Q_312 = q(312, "bt=x")
Q_315 = q(315, "ka(i)=int(rnd(1)*5)*500+5000")


def _basic_new_game(v: Values) -> Any:
    r_kr, r_in, r_bt, r_ka = v["r"]
    out = []
    for r, stat in ((r_kr, Q_310), (r_in, Q_311), (r_bt, Q_312)):
        x = Q_350.assign({"rnd(1)": r})
        out.append(stat.assign({"x": x}))
    out.append(Q_315.assign({"i": 1, "rnd(1)": r_ka}))
    return tuple(out)


_FOUR = [("a", "b"), ("c", "d"), ("e", "f"), ("g", "h")]


def _engine_new_game(v: Values) -> Any:
    state = new_game(seed=v["seed"], end_year=1930, score_weight=1.0, players=_FOUR)
    p = state.players[v["player"]]
    g = p.roster[0]
    return (g.attrs["kraft"], g.attrs["intelligenz"], g.attrs["brutalitaet"], p.ka)


def _new_game_grid(seeds: Iterable[int]) -> tuple[dict[str, Any], ...]:
    """Each player of a four-player game per seed, with the ``rnd(1)`` it rolled.

    ``new_game(seed)`` draws from ``Rng(seed)``, so the same seed replays its rolls:
    per player, :310-312 roll kr, in and bt through :350 (``int(rnd(1)*9)``) and then
    :315 rolls the cash (``int(rnd(1)*5)``). Each roll ``k`` of ``n`` becomes the
    ``rnd(1)`` value ``(k+0.5)/n``, the middle of the interval that yields ``k``.
    """
    points = []
    for seed in seeds:
        rng = Rng(seed)
        for player in range(len(_FOUR)):
            rolls = [(rng.range(n), n) for n in (9, 9, 9, 5)]
            r = tuple((k + 0.5) / n for k, n in rolls)
            points.append({"seed": seed, "player": player, "r": r})
    return tuple(points)


# --- :205-210 player count ---------------------------------------------------------------
# :205 ``sz=val(x$)`` is the identity on the grid's numeric answers (the evaluator has no
# ``val``); :206's condition asks again, and :210's ``for`` sets up the players.
Q_206 = q(206, "sz<1orsz>4")
Q_210_FOR = q(210, "fori=1tosz")


def _basic_player_count(v: Values) -> Any:
    if Q_206.holds({"sz": v["sz"]}):
        return "asked again"  # :206 goto205
    players, i = 0, 1  # :210 fori=1tosz ... :220 next: the body runs while i<=sz
    while i <= v["sz"]:
        players += 1
        i += 1
    return players


class _CountAskedAgain(Exception):
    """The setup handler asked for the player count a second time."""


def _engine_player_count(v: Values) -> Any:
    counts: list[Any] = []

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, PromptText):
            if interaction.key == "setup.player_count_prompt":
                if counts:
                    raise _CountAskedAgain
                counts.append(v["sz"])
                return repr(v["sz"])
            return "a"
        return True  # RollFrame: stopped; display interactions ignore the answer

    handler = functools.partial(
        HANDLERS[SETUP_HANDLER_KEY], end_year=1930, score_weight=1.0, house_rules={}
    )
    try:
        result = run(handler, answer, state=None, rng=Rng(1))
    except _CountAskedAgain:
        return "asked again"
    return len(result.payload.returned.players)


# --- :350-355 a stat roll stopped on frame k -----------------------------------------------
# Q_350 (below) is the draw; :355 ``getx$:ifx$=""goto350`` draws again until a key.


def _basic_roll_frames(v: Values) -> Any:
    shown = tuple(Q_350.assign({"rnd(1)": r}) for r in v["r"])  # one :350 pass per frame
    return shown, shown[-1]  # the key stops the loop on the last value printed


def _engine_roll_frames(v: Values) -> Any:
    frames: list[int] = []

    def answer(interaction: Any) -> Any:
        if isinstance(interaction, RollFrame) and interaction.key == "setup.roll.kraft":
            frames.append(interaction.params["value"])
            return len(frames) == len(v["r"])
        return True

    handler = functools.partial(
        HANDLERS[SETUP_HANDLER_KEY],
        end_year=1930,
        score_weight=1.0,
        house_rules={},
        players=[("a", "b")],
    )
    # kraft's frames, then intelligenz, brutalitaet and the cash, each on its first draw
    rng = StubRng((*v["r"], 0.0, 0.0, 0.0))
    result = run(handler, answer, state=None, rng=rng)
    assert rng.used == len(v["r"]) + 3
    return tuple(frames), result.payload.returned.players[0].kraft


# --- :30000 combat side anchors -------------------------------------------------------
Q_30000 = q(30000, "kp(i,j)=129-18*(i=2)+p(j)")
Q_50400 = q(50400, "data 122,81,161,120,42,202,40,200,1,241")
_STAGGER = [int(n) for n in Q_50400.text.removeprefix("data ").split(",")]


def _basic_anchor(v: Values) -> Any:
    b: dict[str, Any] = {"i": v["i"], "j": v["j"]}
    b.update({f"p({j})": off for j, off in enumerate(_STAGGER, start=1)})
    return Q_30000.assign(b)


def _engine_anchor(v: Values) -> Any:
    anchor = {1: SIDE1_ANCHOR, 2: SIDE2_ANCHOR}[v["i"]]
    return placement_position(anchor, v["j"])


# --- :30108 side toggle ------------------------------------------------------------------
Q_30108 = q(30108, "s=1-(s=1)")


def _basic_toggle(v: Values) -> Any:
    return Q_30108.assign({"s": v["s"]})


def _engine_toggle(v: Values) -> Any:
    return CombatFight(CombatState()).hostile_to(v["s"])[0]


# --- :30247 hit test ---------------------------------------------------------------------
Q_30247 = q(30247, "int(rnd(1)*ts(w))=0orint(rnd(1)*(kr/10+1))=0")


def _basic_hit(v: Values) -> Any:
    # The line reads `if <miss> goto 30235`; C64 `or` evaluates both draws.
    miss = Q_30247.holds({"w": 1, "ts(1)": v["ts"], "kr": v["kr"], "rnd(1)": list(v["r"])})
    return not miss


def _engine_hit(v: Values) -> Any:
    return is_hit(v["kr"], {"ts": v["ts"]}, StubRng(v["r"]))


# --- :30255 damage roll --------------------------------------------------------------------
Q_30255 = q(30255, "y=int(rnd(1)*tg(w)+bt/10)+1")


def _basic_damage(v: Values) -> Any:
    return Q_30255.assign({"w": 1, "tg(1)": v["tg"], "bt": v["bt"], "rnd(1)": v["r"]})


def _engine_damage(v: Values) -> Any:
    return damage_roll(v["bt"], {"tg": v["tg"]}, StubRng((v["r"],)))


# --------------------------------------------------------------------------- #
# The inventory                                                               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Port:
    """One inventoried port: quotes, engine callable, input grid, the two evaluators."""

    name: str
    quotes: tuple[Quote, ...]
    callable: str
    grid: tuple[Mapping[str, Any], ...]
    basic: Callable[[Values], Any]
    engine: Callable[[Values], Any]
    divergence: str | None = None

    @property
    def line(self) -> int:
        return self.quotes[0].line


_TS = sorted({w["ts"] for w in _WEAPONS})
_TG = sorted({w["tg"] for w in _WEAPONS})
_R_PAIRS = tuple(itertools.product((0.0, 0.25, 0.3, 0.5, 0.7, 0.9990234375), repeat=2))
# kr/10 is continuous at :30247, so kraft off a multiple of 10 is where a truncating
# port would diverge.
_KRAFT = (0, 1, 5, 9, 10, 15, 19, 20, 25, 30, 37, 40, 50, 55, 90, 99)
_STAT_TRIPLES = ((10, 10, 10), (50, 93, 94), (94, 96, 97), (95, 97, 98), (99, 99, 99))
_CAMP_R = ((0.0, 0.0, 0.0), (0.125, 0.5, 0.875), (0.3, 0.7, 0.9990234375), (0.9990234375,) * 3)
_NEW_GAME_GRID = _new_game_grid(range(25))
_CAPITALS = (1, 2, 9, 10, 19, 20, 21, 99, 100, 101, 999, 1000, 1234, 2500, 4999, 5000)

PORTS: list[Port] = [
    Port(
        "fnm rent",
        (FNM,),
        "setup.fnm",
        _grid(ln=range(1, 10)),
        _basic_fnm,
        _engine_fnm,
    ),
    Port(
        "slw rent",
        (Q_10020, FNM, Q_10035, Q_10040_KA, Q_10040_UM),
        "HANDLERS['slw.rent']",
        _grid(ln=range(1, 10), x=(1, 2, 5, 12, 999), ka=(0, 49, 50, 150, 300, 1800, 10**6)),
        _basic_slw,
        _engine_slw,
    ),
    Port(
        "score and rank",
        (Q_1160, Q_1160_CAP, Q_1161_FLOOR, Q_1165),
        "setup.score_and_rank + effects.apply(ScoreAndRank)",
        _grid(
            gf=(0, 0.5, 11.1, 33.3, 50, 88.8, 99.5, 100),
            x=(-10, -2, 1, 2, 3, 6),
            x8=(0.1, 0.3, 1.0, 1.7, 2.0),
        ),
        _basic_score,
        _engine_score,
    ),
    Port(
        "per-turn score truncation",
        (Q_1013,),
        f"HANDLERS[{SCORE_TRUNCATION_HOOK_KEY!r}]",
        # Exact and far-from-a-cent scores, and whole cents the C64 keeps (51.2, .29)
        # or cuts (25.4 to 25.39, 12.34 to 12.33, .01 to 0), double drift included.
        _grid(
            gf=(0, 0.0078125, 0.125, 0.5, 11.1, 25.1953125, 25.5, 33.337, 51.25, 88.8)
            + (99.9990234375, 100, 101.5, -0.0078125, -0.125, -2.9990234375, -3.5, -12.345)
            + (25.4, 12.34, 51.2, 0.29, 0.57, 1.13, 0.01, 99.99, 1.2 * 21, 8.4 * 3, -25.4)
        ),
        _basic_truncate,
        _engine_truncate,
    ),
    Port(
        "energy regen",
        (Q_4015, Q_4020, Q_4020_CAP),
        "HANDLERS['upkeep.turn_start'] (EnergyChange)",
        _grid(kr=range(100), bt=range(0, 100, 9), en=(0, 5, 17, 40)),
        _basic_regen,
        _engine_regen,
    ),
    Port(
        "debt grace tick",
        (Q_4305, Q_4305_DUE),
        "HANDLERS['upkeep.turn_start'] (DebtChange)",
        _grid(kz=range(7)),
        _basic_grace,
        _engine_grace,
    ),
    Port(
        "loan-shop income",
        (Q_4405, Q_4410_P, Q_4410_KA),
        "HANDLERS['upkeep.turn_start'] (shop income)",
        _grid(kk=_CAPITALS, r0=(0.0, 0.3, 0.5, 0.9990234375), r=R),
        _basic_income,
        _engine_income,
    ),
    Port(
        "rent countdown and late rent",
        (Q_4045, Q_4046_DEC, Q_4046_DUE, Q_4046_RESET, Q_4605_P, Q_4605_CAP, Q_4605_CAPPED)
        + (Q_4605_EVICT, Q_4620, Q_4651),
        "HANDLERS['upkeep.turn_start'] (rent)",
        # ka around the 200..299 fine band, plus 0 (eviction) and a negative balance
        # (the cap then makes p negative and the "fine" lifts ka to 0, as in BASIC).
        _grid(
            um=(0, 1, 2, 3, 12),
            ka=(-50, 0, 1, 150, 199, 200, 237, 250, 299, 300, 10**6),
            r=(0.0, 0.25, 0.37, 0.5, 0.9990234375),
        ),
        _basic_rent,
        _engine_rent,
    ),
    Port(
        "the marks fade",
        (Q_4055, Q_4055_AG, Q_4056, Q_4056_AG),
        "HANDLERS['upkeep.turn_start'] (marks fade)",
        _grid(ag=range(4), r0=(0.0, 0.1249, 0.125, 0.5, 0.9990234375), r1=(0.0, 0.125, 0.7)),
        _basic_decay,
        _engine_decay,
    ),
    Port(
        "ble passport",
        (Q_22010, Q_22014, Q_22020_KA, Q_22020_AG),
        "HANDLERS['ble.passport']",
        _grid(gz=(1, 2, 5, 10), ka=(0, 999, 1000, 1999, 2000, 5000, 10**6), ag=range(4)),
        _basic_passport,
        _engine_passport,
    ),
    Port(
        "ble counterfeit money",
        (Q_22105, Q_22106, Q_22110, Q_22120_KA, Q_22120_AG),
        "HANDLERS['ble.counterfeit']",
        # q <= 0 leaves, q above 5000 or the cash is asked again; odd q leaves a
        # half-width last bucket in int(rnd(1)*q/2).
        _grid(
            q=(-5000, -1, 0, 1, 2, 3, 7, 100, 999, 1000, 1001, 4999, 5000, 5001, 99999),
            ka=(-50, 0, 1, 1000, 5000, 10**6),
            ag=(0, 1, 2, 3),
            r=R,
        ),
        _basic_counterfeit,
        _engine_counterfeit,
    ),
    Port(
        "chief-bribe months age",
        (Q_4050,),
        "HANDLERS['upkeep.turn_start'] (chief-bribe months)",
        _grid(pl=(-3, -1, 0, 1, 2, 7)),
        _basic_bribe_aging,
        _engine_bribe_aging,
    ),
    Port(
        "pol chief bribe",
        (Q_21011, Q_21011_ZERO, Q_21015, Q_21020_KA, Q_21020_PL),
        "HANDLERS['pol.bribe']",
        # negative counts pay out (faithful); the cash check is strict.
        _grid(
            x=(-40, -3, -2, -1, 0, 1, 2, 3, 10, 40),
            ka=(0, 999, 1000, 2999, 3000, 10**6),
            pl=(-2, 0, 2),
        ),
        _basic_chief,
        _engine_chief,
    ),
    Port(
        "pol release price",
        (Q_21130, Q_21135, Q_21140),
        "HANDLERS['pol.free'] (the guards' price)",
        _grid(ka=(0, 2999, 3000, 3500, 4999, 5000, 10**6), r=R),
        _basic_release,
        _engine_release,
    ),
    Port(
        "aut car sale",
        (Q_14010_X, Q_14010_LN, Q_14010_X2, Q_14030, Q_14035_P, Q_14035, Q_14040)
        + (Q_14045_Q, Q_14045_IF, Q_14045_STOLEN, Q_14050_KA, Q_14050_MS, Q_14050_TM),
        "HANDLERS['aut.buy']",
        # the cash check ignores the trade-in: ka=4999 with a talbot cannot buy the buick.
        _grid(
            ln=(1, 2, 3, 4),
            y=(1, 2, 3, 4, 5),
            tm=range(6),
            ka=(0, 2999, 3000, 4999, 5000, 6000, 10**6),
        ),
        _basic_car_sale,
        _engine_car_sale,
    ),
    Port(
        "aut car theft",
        (Q_14100, Q_14110),
        "HANDLERS['aut.steal'] (the crowd and the steal roll)",
        # 3*in+4*kr is the integer bound: 120 (in=40 or kr=30 alone) is always caught.
        _grid(
            ln=(1, 2, 4),
            r0=(0.0, 0.3, 0.9990234375),
            r=R,
            **{"in": (0, 40, 99)},
            kr=(0, 30, 99),
        ),
        _basic_car_theft,
        _engine_car_theft,
    ),
    Port(
        "sgl jack's gang",
        (Q_17210,),
        "HANDLERS['sgl.protection'] (the size of Jack's gang)",
        _grid(gz=range(1, 11)),
        _basic_jack_count,
        _engine_jack_count,
    ),
    Port(
        "sgl payout",
        (Q_17500, Q_17505, Q_17530, Q_17550),
        "HANDLERS['sgl.protection'] (sgl._extort)",
        # w is the last shooter's weapon after Jack's fight: 2 pays +600.
        _grid(ln=range(1, 10), w=range(9), r0=(0.0, 0.3, 0.375, 0.9990234375), r=R),
        _basic_extortion,
        _engine_extortion,
    ),
    Port(
        "sgl shop wrecked, owner finished",
        (Q_17530, Q_17550, Q_17575, Q_17590, Q_17578),
        "HANDLERS['sgl.protection'] (sgl._demolish, sgl._kill_owner)",
        _grid(ln=range(1, 10), choice=(2, 3), r=(0.0, 0.5), r2=R),
        _basic_settle,
        _engine_settle,
    ),
    Port(
        "sub pickpocketing",
        (Q_18025, Q_18030, Q_18040, Q_18041, Q_18045, Q_18047, Q_18049, Q_18050, Q_18051),
        "HANDLERS['sub.platform'], HANDLERS['sub.train'] (sub.pickpocket)",
        # r0=0.7 is the manual (int(0.7*15)=10); in=10 and below is always caught.
        _grid(
            w=(1, 2),
            la=(8, 9),
            ka=(49, 50, 1000),
            r0=(0.0, 0.7, 0.9990234375),
            r1=R,
            r2=(0.0, 0.25, 0.5, 0.75, 0.9990234375),
            **{"in": (0, 5, 10, 11, 40, 99)},
        ),
        _basic_pickpocket,
        _engine_pickpocket,
    ),
    Port(
        "bhf mail train",
        (
            Q_19015,
            Q_19016,
            Q_19016_TP,
            Q_20050_P,
            Q_20050_X,
            Q_20051,
            Q_20051_TP,
            Q_20051_P,
            Q_20060,
        ),
        "HANDLERS['bhf.mail_train'] (ban.heist_payout)",
        _grid(tp=range(6), gz=range(5), r=R),
        _basic_mail_train,
        _engine_mail_train,
    ),
    Port(
        "ban hold-up",
        (Q_20009, Q_20010, Q_20050_P, Q_20050_X, Q_20051, Q_20051_TP, Q_20051_P, Q_20060),
        "HANDLERS['ban.holdup'] (ban.heist_payout)",
        _grid(ln=range(1, 6), tp=range(6), gz=(1, 2), r0=(0.0, 0.3, 0.5, 0.9990234375), r=R),
        _basic_holdup,
        _engine_holdup,
    ),
    Port(
        "ban guards",
        (Q_20012,),
        "HANDLERS['ban.holdup'] (the number of guards)",
        _grid(ln=range(1, 6)),
        _basic_bank_guards,
        _engine_bank_guards,
    ),
    Port(
        "win cells armed",
        (Q_2002, Q_2003),
        "win_flows.armed_cells (the city's armed guards)",
        _grid(tp=range(6)),
        _basic_armed,
        _engine_armed,
    ),
    Port(
        "win cash transport",
        (
            Q_23010,
            Q_23010_TP,
            Q_20050_P,
            Q_20050_X,
            Q_20051,
            Q_20051_TP,
            Q_20051_P,
            Q_20060,
        ),
        "HANDLERS['turn.special_cell'] on 569 (win_flows.cash_transport)",
        _grid(gz=range(5), r=R),
        _basic_transport,
        _engine_transport,
    ),
    Port(
        "win mayor hit",
        (Q_24020_KA, Q_24020_AG, Q_24020_TP),
        "HANDLERS['turn.special_cell'] on 861 (win_flows.mayor_hit)",
        _grid(ka=(0, 1000), ag=range(4)),
        _basic_mayor,
        _engine_mayor,
    ),
    Port(
        "gang war duel",
        (Q_27020_A, Q_27020_B, Q_27025, Q_27028, Q_27031_TMA, Q_27031_TMB)
        + (Q_27035_KAA, Q_27035_KAB, Q_27035_AGA, Q_27035_AGB)
        + (Q_27040_X, Q_27040_IF, Q_27040_CAP, Q_27041_TAA, Q_27041_TAB, Q_27045),
        "HANDLERS['turn.gang_war'] (gang_war.gang_war, gang_war.plunder)",
        _grid(
            s=(1, 2),
            players=_GANG_WAR_SEATS + tuple(seats[::-1] for seats in _GANG_WAR_SEATS),
            r=R,
            ans=("j", "n"),
        ),
        _basic_gang_war,
        _engine_gang_war,
    ),
    Port(
        "prison brawl",
        (Q_27115, Q_27120, Q_27140, Q_27145, Q_27150_MS, Q_27150_X),
        "HANDLERS['turn.gang_war'] (gang_war._prison_brawl)",
        _grid(s=(1, 2), ka=(2999, 3000, 3001, 9000), gs=(1, 4), gf=(50.0, 98.5, 0.0), r=R),
        _basic_prison_brawl,
        _engine_prison_brawl,
    ),
    Port(
        "ban safe gate",
        (Q_20100,),
        "HANDLERS['ban.safe'] (the boss's stats)",
        _grid(**{"in": (39, 40, 99)}, kr=(14, 15), bt=(19, 20)),
        _basic_safe_gate,
        _engine_safe_gate,
    ),
    Port(
        "ban safe-crack",
        (Q_20110_RD, Q_20110_CD, Q_20111_Y, Q_20111_S9, Q_20111_CAP, Q_20116_X, Q_20116_RD)
        + (Q_20116_WRAP, Q_20125, Q_20130, Q_20135_Y, Q_20135),
        "SUBSTATES['safe_crack'] (ban.safe_crack)",
        _grid(
            **{"in": (0, 7, 8, 12, 40, 63, 99)},
            ln=(1, 3),
            s9=(0, 1, 5),
            code=((0.0, 0.0, 0.0), (0.1, 0.2, 0.3), (0.9990234375, 0.5, 0.25)),
            slips=((0.5,), (0.0,), (0.0, 0.5), (0.125, 0.9990234375, 0.3)),
        ),
        _basic_safe,
        _engine_safe,
    ),
    Port(
        "pol thank-you",
        (Q_21252, Q_21253, Q_21255_KX, Q_21255_KSP),
        "HANDLERS['pol.free'] (the freed player's thanks)",
        _grid(y=(-5, -1, 0, 1, 499, 500, 501, 10**6), kax=(0, 1, 500)),
        _basic_thanks,
        _engine_thanks,
    ),
    Port(
        "arms-deal payout",
        (Q_31000, Q_31005, Q_31010),
        "HANDLERS['upkeep.turn_start'] (arms deal)",
        _grid(r0=(0.0, 0.125, 0.25, 0.9990234375), r=R),
        _basic_arms,
        _engine_arms,
    ),
    Port(
        "waf range training",
        (Q_13110, Q_13116, Q_13125_KA, Q_13125_KR, Q_13125_CAP, Q_13126, Q_13126_CAP)
        + (Q_13127, Q_13127_CAP),
        "HANDLERS['waf.train'] (schiesstand)",
        _grid(ra=range(1, 11), ln=(1, 2, 3), stats=_STAT_TRIPLES, ka=(1000, 10**6)),
        _basic_range,
        _engine_range,
    ),
    Port(
        "waf camp training",
        (Q_13150, Q_13160, Q_13170_KA, Q_13170_IN, Q_13170_CAP, Q_13171, Q_13171_CAP)
        + (Q_13172, Q_13172_CAP, FNR),
        "HANDLERS['waf.train'] (trainingscamp)",
        _grid(ra=range(5, 11), ln=(1,), stats=_STAT_TRIPLES, r=_CAMP_R, ka=(5000, 10**6)),
        _basic_camp,
        _engine_camp,
    ),
    Port(
        "waf buy score and trade-in",
        (Q_13065_NEW, Q_13065_GF, Q_13070, Q_13072_UP, Q_13072_GF, Q_13073_GF, Q_13075),
        "HANDLERS['waf.buy'] (_pick_gangster_and_arm)",
        # The second grid sits at the edge of 0..100, where the unclamped source takes
        # gf past it (gf=99,x8=2 upgrading gives 101; gf=1,x8=2 downgrading gives -3).
        _grid(old=range(9), x=range(1, 9), gf=(10, 50, 90), x8=(0.1, 1.0, 2.0))
        + _grid(old=(0, 3), x=(2, 5), gf=(0, 0.5, 1, 99, 99.5, 100), x8=(0.1, 1.0, 2.0)),
        _basic_buy,
        _engine_buy,
    ),
    Port(
        "job completion score",
        (Q_25560,),
        "HANDLERS['job.shift'] (contract completed)",
        _grid(jo=range(1, 5)),
        _basic_completion,
        _engine_completion,
    ),
    Port(
        "croupier catch and bonus",
        (Q_25120, Q_25125, Q_25126),
        "HANDLERS['job.shift'] (croupier)",
        _grid(
            trick=(1, 2, 3),
            r_catch=(0.0, 0.19, 0.2, 0.24, 0.25, 0.3, 0.33, 0.34, 0.5, 0.9990234375),
            r=R,
        ),
        _basic_croupier,
        _engine_croupier,
    ),
    Port(
        "pub recruit offer count",
        (Q_12106_Y0, Q_12106_Y, Q_12106_CAP, Q_12106_SET, Q_12107_X, Q_12107_NONE),
        "HANDLERS['pub.recruit'] (pool and roll)",
        _grid(
            hired=((), (0, 1), tuple(range(26)), tuple(range(27)), tuple(range(1, 29)))
            + (tuple(range(29)), tuple(range(30))),
            ln=(1, 3),
            r=(0.0, 0.25, 0.34, 0.5, 0.66, 0.67, 0.75, 0.9990234375),
        ),
        _basic_offer_count,
        _engine_offer_count,
    ),
    Port(
        "pub recruit pick and reroll",
        (Q_12110_G, Q_12110_HIRED, Q_12112, Q_12106_Y, Q_12107_X),
        "HANDLERS['pub.recruit'] (candidate draw)",
        _PICK_GRID,
        _basic_pick,
        _engine_pick,
    ),
    Port(
        "pub recruit confirm, cash and cap",
        (Q_12140, Q_12145, Q_12160_KA, Q_12160_GZ),
        "HANDLERS['pub.recruit'] (offer loop)",
        _BATCH_GRID,
        _basic_batch,
        _engine_batch,
    ),
    Port(
        "casino",
        (Q_16026, Q_16030_WIN, Q_16030_P, Q_16040),
        "HANDLERS['sph']",
        _grid(
            x=(1, 2, 3),
            stake=(1, 2, 3, 7, 100, 999, 4321),
            r=(0.0, 0.1, 0.24, 0.25, 0.3, 0.33, 0.34, 0.49, 0.5, 0.9990234375),
        ),
        _basic_casino,
        _engine_casino,
    ),
    Port(
        "pub alcohol buy",
        (Q_12010, Q_12020_X, Q_12020_P, Q_12025_Q, Q_12025_CAP, Q_12030, Q_12035_TA, Q_12035_KA),
        "HANDLERS['pub.drink'] (buy, ln=4 and the station pub's ln=5)",
        _grid(
            ln=(3, 4, 5, 6),
            vehicle=range(len(_VEHICLES)),
            ta=(0, 30),
            r_x=(0.0, 0.5, 0.9990234375),
            r_p=(0.0, 0.5, 0.9990234375),
            want=(1, 1000),
            ka=(100, 10**6),
        ),
        _basic_alcohol_buy,
        _engine_alcohol_buy,
    ),
    Port(
        "pub alcohol buy, a negative count",
        (Q_12030, Q_12035_TA, Q_12035_KA, Q_12035_X, Q_12028, Q_12029, Q_12025_Q, Q_12025_CAP),
        "HANDLERS['pub.drink'] (buy, faithful c64_input_negatives)",
        # vehicle 0 (tank 50) with ta=60 offers -10: 0 and -5 are asked again (:12028).
        _grid(
            vehicle=(0, 1),
            ta=(0, 30, 60),
            r_x=(0.0, 0.9990234375),
            r_p=(0.0, 0.5, 0.9990234375),
            y=(-1, -5, -10, -11, -1000, 0),
            ka=(0, 100),
            gf=(0.0, 99.0),
        ),
        _basic_alcohol_buy_negative,
        _engine_alcohol_buy_negative,
    ),
    Port(
        "pub alcohol sell",
        (Q_12050, Q_12075_KA, Q_12075_TA),
        "HANDLERS['pub.drink'] (sell)",
        _grid(ta=(1, 7, 50), y=(1,), r=R) + _grid(ta=(7, 50), y=(7,), r=R),
        _basic_alcohol_sell,
        _engine_alcohol_sell,
    ),
    Port(
        "pub tip price and tip",
        (Q_12215, Q_12220, Q_12225_KA, Q_12225_TP),
        "HANDLERS['pub.tip']",
        _grid(ka=(1000, 1499, 1500, 2000, 10**6), r_p=R, r_tp=(0.0, 0.2, 0.5, 0.7, 0.9990234375)),
        _basic_tip,
        _engine_tip,
    ),
    Port(
        "pub job type, pay and duration",
        (Q_12305, *Q_JOB_PAY.values(), *Q_JOB_DURATION.values()),
        "HANDLERS['pub.job']",
        _grid(r_type=R, r_pay=R),
        _basic_job,
        _engine_job,
    ),
    Port(
        "kdh shop buy price",
        (Q_15110, Q_15115, Q_15120),
        "HANDLERS['kdh.trade'] (buy)",
        _grid(ka=(5000, 5999, 6000, 10**6), r=R),
        _basic_shop_buy,
        _engine_shop_buy,
    ),
    Port(
        "kdh shop sell price",
        (Q_15150, Q_15155),
        "HANDLERS['kdh.trade'] (sell)",
        _grid(r=R),
        _basic_shop_sell,
        _engine_shop_sell,
    ),
    Port(
        "kdh ambush loot",
        (Q_15320, Q_15321),
        "setup.apply_outcome + kdh_ambush.yaml on_win",
        _grid(r=R),
        _basic_loot,
        _engine_loot,
    ),
    Port(
        "player count",
        (Q_206, Q_210_FOR),
        "HANDLERS[SETUP_HANDLER_KEY] (count prompt)",
        _grid(sz=(-1, 0, 0.5, 0.9990234375, 1, 1.5, 2, 2.5, 3, 3.75, 4, 4.0009765625, 4.5, 5, 9)),
        _basic_player_count,
        _engine_player_count,
    ),
    Port(
        "stat roll stopped on frame k",
        (Q_350,),
        "HANDLERS[SETUP_HANDLER_KEY] (:350 RollFrame loop)",
        tuple(
            {"r": r}
            for r in (
                *((a,) for a in R),
                *itertools.product(R[::3], repeat=2),
                *itertools.product((0.0, 0.5, 0.9990234375), repeat=3),
            )
        ),
        _basic_roll_frames,
        _engine_roll_frames,
    ),
    Port(
        "new-game stats and cash",
        (Q_350, Q_310, Q_311, Q_312, Q_315),
        "setup.new_game (seeded; _roll_stat)",
        _NEW_GAME_GRID,
        _basic_new_game,
        _engine_new_game,
    ),
    Port(
        "police squad",
        (Q_26000, Q_26010_W, Q_26010_E),
        "police.police_fight",
        # an odd rank gives int(rnd(1)*ra/2) a half-width top bucket
        _grid(ra=range(1, 11), r0=R, r1=(0.0, 0.5, 0.9990234375)),
        _basic_squad,
        _engine_squad,
    ),
    Port(
        "police chief-bribe auto-pay",
        (Q_26021, Q_26037, Q_26038_KA, Q_26038),
        "police.caught (auto-pay)",
        _grid(
            pl=(0, 1, 3),
            p=(0, 520, 52724),
            ka=(0, 519, 520, 10**6),
            r0=(0.0, 0.4990234375, 0.5, 0.9990234375),
            r1=(0.0, 0.1990234375, 0.2, 0.9990234375),
        ),
        _basic_autopay,
        _engine_autopay,
    ),
    Port(
        "police bribe",
        (Q_26035, Q_26037, Q_26038_KA, Q_26038),
        "police.caught (bribe)",
        _grid(
            ra=range(1, 11),
            ka=(0, 999, 1000, 3000, 5499, 5500, 10**6),
            r=(0.0, 0.1990234375, 0.2, 0.9990234375),
        ),
        _basic_bribe,
        _engine_bribe,
    ),
    Port(
        "police flight",
        (Q_26040,),
        "police.caught (flight)",
        _grid(sp=(1, 2, 3, 4), r=R + (0.18, 0.19, 0.27, 0.28, 0.31, 0.32)),
        _basic_flight,
        _engine_flight,
    ),
    Port(
        "the map roadblock",
        (Q_2041, Q_6015, Q_6016, Q_6017, Q_6018, Q_6036, Q_110_BR, Q_2030),
        "HANDLERS['turn.roadblock']",
        _grid(
            ra=(3, 4, 10),
            ms=(0, 19, 20, 40),
            r0=(0.0, 0.1990234375, 0.2),
            r1=(0.0, 0.3330078125, 0.333984375, 0.9990234375),
            ag=range(4),
            ta=(0, 3),
            po=(500,),
            x=(1, -40),
        ),
        _basic_roadblock,
        _engine_roadblock,
    ),
    Port(
        "trial and lawyer",
        (Q_26045, Q_26050, Q_26061, Q_26062_KA, Q_26062_Y, Q_26065, Q_26065_FLOOR),
        "police.sentence",
        _grid(
            ra=(1, 2, 3, 4, 5, 6, 9, 10),
            x=(-5, 0, 1, 999, 1000, 1500, 2999, 3000, 9999, 20000),
            ka=(0, 1000, 10**6),
            r=R,
        ),
        _basic_trial,
        _engine_trial,
    ),
    Port(
        "combat side anchors",
        (Q_30000, Q_50400),
        "combat_setup.placement_position + SIDE1/SIDE2_ANCHOR",
        _grid(i=(1, 2), j=range(1, 11)),
        _basic_anchor,
        _engine_anchor,
    ),
    Port(
        "combat side toggle",
        (Q_30108,),
        "combat.CombatFight.hostile_to",
        _grid(s=(1, 2)),
        _basic_toggle,
        _engine_toggle,
    ),
    Port(
        "hit test",
        (Q_30247,),
        "combat_rules.is_hit",
        _grid(ts=_TS, kr=_KRAFT, r=_R_PAIRS),
        _basic_hit,
        _engine_hit,
    ),
    Port(
        "damage roll",
        (Q_30255,),
        "combat_rules.damage_roll",
        # bt not a multiple of 10 is where bt/10's fraction carries into the int().
        _grid(tg=_TG, bt=(0, 5, 10, 15, 25, 30, 37, 50, 90, 99), r=R),
        _basic_damage,
        _engine_damage,
    ),
]


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #
def _port_param(port: Port) -> Any:
    marks = ()
    if port.divergence is not None:
        marks = (pytest.mark.xfail(strict=True, reason=f"known divergence: {port.divergence}"),)
    return pytest.param(port, id=f"{port.line}-{port.name}", marks=marks)


@pytest.mark.parametrize("port", [_port_param(p) for p in PORTS])
def test_port_matches_basic(port: Port) -> None:
    """The engine callable agrees with the quoted BASIC over the whole input grid."""
    mismatches = []
    for point in port.grid:
        engine_value = port.engine(point)
        basic_value = port.basic(point)
        if engine_value != basic_value:
            mismatches.append((point, engine_value, basic_value))
    shown = "\n".join(
        f"  inputs={point} engine={e!r} basic={b!r}" for point, e, b in mismatches[:8]
    )
    assert not mismatches, (
        f"{port.callable} diverges from mf-prg.bas:{port.line} "
        f"at {len(mismatches)}/{len(port.grid)} points:\n{shown}"
    )


def test_completion_score_ae1() -> None:
    """AE1: :25560 gives 0 for the croupier (jo=2) and 3 for every other job."""
    assert [Q_25560.assign({"sp": 1, "jo(1)": jo}) for jo in (1, 2, 3, 4)] == [3, 0, 3, 3]
    assert [_engine_completion({"jo": jo}) for jo in (1, 2, 3, 4)] == [3, 0, 3, 3]


def test_new_game_grid_rolls_every_value() -> None:
    """The seeded grid reaches every stat roll (0..8) and cash roll (0..4)."""
    stats = {int(p["r"][i] * 9) for p in _NEW_GAME_GRID for i in range(3)}
    cash = {int(p["r"][3] * 5) for p in _NEW_GAME_GRID}
    assert (stats, cash) == (set(range(9)), set(range(5)))


def test_pick_grid_redraws_for_both_reasons() -> None:
    """The pick grid is not vacuous: its scripts reroll on a hired and a repeated pick."""
    redrawn = [p for p in _PICK_GRID if _basic_pick(p)[1] > len(_basic_pick(p)[0])]
    assert any(p["hired"] == () for p in redrawn), "no repeat-only reroll"
    assert any(p["hired"] == (4,) for p in redrawn), "no hired reroll"


def test_batch_grid_reaches_every_order() -> None:
    """The batch grid is not vacuous: at the cap it declines, runs short of cash and
    offers again after either, and it hits the cap mid-batch."""
    outcomes = {_basic_batch(p)[0] for p in _BATCH_GRID}
    assert ("hired", "declined", "full") in outcomes
    assert ("hired", "broke", "full") in outcomes
    assert ("hired", "full") in outcomes
    assert ("full",) not in outcomes  # gz=10 on entry is :12105, not this loop


def test_every_quote_belongs_to_a_port() -> None:
    """No stray quote: each registered quote is used by at least one inventory entry."""
    used = {quote for port in PORTS for quote in port.quotes}
    assert [quote for quote in QUOTES if quote not in used] == []


# A quote must start at a statement or condition boundary and end at one. ``on`` opens
# the selector expression of an ``on ... goto`` (:18045).
_BEFORE = r"(?:^|:|\bif|then|\bon)\s*"
_AFTER = r"\s*(?:$|:|then|goto|gosub)"


@pytest.mark.parametrize("quote", QUOTES, ids=lambda quote: f"{quote.line}:{quote.text}")
def test_quote_is_verbatim(quote: Quote) -> None:
    """Every quote is one statement (or ``if`` condition) of its cited line, verbatim."""
    line = load_source().get(quote.line)
    assert line is not None, f"mf-prg.bas has no line {quote.line}"
    pattern = _BEFORE + re.escape(quote.text) + _AFTER
    assert re.search(pattern, line), (
        f"{quote.text!r} is not a statement of mf-prg.bas:{quote.line}: {line!r}"
    )

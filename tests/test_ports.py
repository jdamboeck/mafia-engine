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

import itertools
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from data.game_configs.mafia_1920s import setup as cfg_setup
from data.game_configs.mafia_1920s.combat_rules import damage_roll, is_hit
from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.handlers.jobs import _completion_score
from data.game_configs.mafia_1920s.setup import (
    apply_outcome,
    fnm,
    load_encounter,
    load_vehicles,
    load_weapons,
    new_game,
    score_and_rank,
)
from engine.combat import CombatFight
from engine.combat_setup import SIDE1_ANCHOR, SIDE2_ANCHOR, placement_position
from engine.config_loader import load_config, load_game_config
from engine.effects import JobSet, TipSet, apply, commit
from engine.interactions import (
    Ack,
    Confirm,
    Ctx,
    LoadSubState,
    PromptChoice,
    PromptInt,
    ShowMessage,
    StartCombat,
)
from engine.locations import HANDLERS
from engine.state import (
    Business,
    Clock,
    CombatState,
    Config,
    Contraband,
    Debt,
    GameState,
    Job,
    Player,
)
from tests.basic_eval import eval_assignment, eval_expr

_REPO = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _REPO / "data" / "game_configs" / "mafia_1920s"
_SOURCE = _REPO.parent / "research" / "src" / "decompiled_basic" / "mf-prg.bas"

load_game_config(_CONFIG_DIR)

_PARAMS: dict[str, Any] = dict(load_config(_CONFIG_DIR / "config.yaml")["formula_params"])
_WEAPONS = load_weapons(_CONFIG_DIR / "entities" / "weapons.yaml")
_VEHICLES = load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")
_AMBUSH = load_encounter(_CONFIG_DIR / "content" / "encounters" / "kdh_ambush.yaml")

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


def _drive(
    handler: Callable[[Ctx], Any],
    state: GameState,
    draws: Sequence[float] = (),
    answer: Callable[[Any], Any] = lambda interaction: None,
) -> _Run:
    """Step ``handler`` to completion and commit its effects.

    ``ShowMessage`` is acked and ``LoadSubState`` (display only here) answered with
    ``None``; every other interaction goes to ``answer``. ``StartCombat`` raises
    :class:`_FightStarted`.
    """
    ctx = _RecordingCtx(state, StubRng(draws))
    gen = handler(ctx)
    asked: list[Any] = []
    try:
        interaction = next(gen)
        while True:
            if isinstance(interaction, StartCombat):
                raise _FightStarted(list(ctx.applied))
            if isinstance(interaction, ShowMessage):
                response = Ack
            elif isinstance(interaction, LoadSubState):
                response = None
            else:
                asked.append(interaction)
                response = answer(interaction)
            interaction = gen.send(response)
    except StopIteration:
        pass
    return _Run(commit(state, ctx.applied).state, ctx.applied, asked)


def _gangster(kr: int = 99, in_: int = 99, bt: int = 99, en: int = 5, weapon: int = 0) -> Gangster:
    return Gangster(name="g", weapon=weapon, energie=en, kraft=kr, intelligenz=in_, brutalitaet=bt)


def _state(player: Player, *, score_mult: float = 1.0) -> GameState:
    return GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(score_mult=score_mult, formula_params=_PARAMS),
    )


def _player(**fields: Any) -> Player:
    fields.setdefault("name", "p")
    fields.setdefault("ka", 10**6)
    fields.setdefault("roster", (_gangster(),))
    return Player(**fields)


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
    return (p.ka, p.rented_months)


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
    return (p.gf, p.nr)


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
        return (commit(state, fight.effects).state.players[0].debt.months, True)
    return (run.state.players[0].debt.months, False)


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
        return 0  # the gangster pick: the only gangster

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
        if isinstance(interaction, PromptInt):
            return v["x"]
        if isinstance(interaction, Confirm):
            return True
        return 0

    run = _drive(HANDLERS["waf.buy"], _state(player, score_mult=v["x8"]), draws, answer)
    p = run.state.players[0]
    assert p.roster[0].weapon == v["x"]
    return (p.ka, p.gf)


# --- :25560 job completion score ----------------------------------------------------
Q_25560 = q(25560, "x=3+3*(jo(sp)=2)")


def _basic_completion(v: Values) -> Any:
    return Q_25560.assign({"sp": 1, "jo(1)": v["jo"]})


def _engine_completion(v: Values) -> Any:
    return _completion_score(v["jo"])


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


# --- :12020-12035 pub alcohol buy ---------------------------------------------------------
Q_12020_X = q(12020, "x=int(rnd(1)*200)+100")
Q_12020_P = q(12020, "p=int(rnd(1)*5)+5")
Q_12025_Q = q(12025, "q=tk(tm(sp))-ta(sp)")
Q_12025_CAP = q(12025, "q<x")
Q_12030 = q(12030, "ka(sp)<y*p")
Q_12035_TA = q(12035, "ta(sp)=ta(sp)+y")
Q_12035_KA = q(12035, "ka(sp)=ka(sp)-p*y")


def _basic_alcohol_buy(v: Values) -> Any:
    b: dict[str, Any] = {"sp": 1, "tm(1)": v["vehicle"], "ta(1)": v["ta"], "ka(1)": v["ka"]}
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
        last_location=4,
        vehicle=v["vehicle"],
        contraband=Contraband(alcohol_barrels=v["ta"]),
    )
    run = _drive(
        HANDLERS["pub.drink"],
        _state(player),
        draws=(v["r_x"], v["r_p"]),
        answer=lambda i: min(v["want"], i.max),
    )
    offered = run.asked[0].max
    p = run.state.players[0]
    return (offered, p.ka, p.contraband.alcohol_barrels)


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
    return (p.ka, p.contraband.alcohol_barrels)


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
    tips = [e.tip_type for e in run.effects if isinstance(e, TipSet)]
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
    (job,) = [e for e in run.effects if isinstance(e, JobSet)]
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
    return (p.ka, p.business.shop_tile)


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


def _engine_new_game(v: Values) -> Any:
    with mock.patch.object(cfg_setup, "Rng", lambda seed: StubRng(v["r"])):
        state = new_game(seed=0, end_year=1930, score_weight=1.0, players=[("a", "b")])
    p = state.players[0]
    g = p.roster[0]
    return (g.attrs["kraft"], g.attrs["intelligenz"], g.attrs["brutalitaet"], p.ka)


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
_NEW_GAME_R = tuple(
    ((k + 0.5) / 9, ((k + 3) % 9 + 0.5) / 9, ((k + 5) % 9 + 0.5) / 9, (k % 5 + 0.5) / 5)
    for k in range(9)
) + ((0.0, 0.0, 0.0, 0.0), (0.9990234375,) * 4)
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
        "jobs._completion_score",
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
        (Q_12020_X, Q_12020_P, Q_12025_Q, Q_12025_CAP, Q_12030, Q_12035_TA, Q_12035_KA),
        "HANDLERS['pub.drink'] (buy, ln=4)",
        _grid(
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
        "new-game stats and cash",
        (Q_350, Q_310, Q_311, Q_312, Q_315),
        "setup.new_game (_roll_stat)",
        _grid(r=_NEW_GAME_R),
        _basic_new_game,
        _engine_new_game,
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
    assert [_completion_score(jo) for jo in (1, 2, 3, 4)] == [3, 0, 3, 3]


def test_every_quote_belongs_to_a_port() -> None:
    """No stray quote: each registered quote is used by at least one inventory entry."""
    used = {quote for port in PORTS for quote in port.quotes}
    assert [quote for quote in QUOTES if quote not in used] == []


def _source_lines() -> dict[int, str]:
    lines: dict[int, str] = {}
    for raw in _SOURCE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*(\d+) (.*)$", raw)
        if match:
            lines[int(match.group(1))] = match.group(2)
    return lines


# A quote must start at a statement or condition boundary and end at one.
_BEFORE = r"(?:^|:|\bif|then)\s*"
_AFTER = r"\s*(?:$|:|then|goto|gosub)"


@pytest.mark.skipif(not _SOURCE.exists(), reason="research tree (mf-prg.bas) not present")
@pytest.mark.parametrize("quote", QUOTES, ids=lambda quote: f"{quote.line}:{quote.text}")
def test_quote_is_verbatim(quote: Quote) -> None:
    """Every quote is one statement (or ``if`` condition) of its cited line, verbatim."""
    line = _source_lines().get(quote.line)
    assert line is not None, f"mf-prg.bas has no line {quote.line}"
    pattern = _BEFORE + re.escape(quote.text) + _AFTER
    assert re.search(pattern, line), (
        f"{quote.text!r} is not a statement of mf-prg.bas:{quote.line}: {line!r}"
    )

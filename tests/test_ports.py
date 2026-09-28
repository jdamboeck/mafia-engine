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
from engine.combat import CombatFight
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
    ShowMessage,
    StartCombat,
)
from engine.locations import HANDLERS
from engine.turns import SCORE_TRUNCATION_HOOK_KEY
from engine.rng import Rng
from engine.state import Clock, CombatState, Config, GameState, Player
from data.game_configs.mafia_1920s.state import Business, Contraband, Debt, Job
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
_VIEW_ARGS = ("debt", "business", "contraband", "jobs")
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
    return Q_1013.assign({"sp": 1, "gf(1)": v["gf"]})


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
    return (offered, p.ka, game.contraband(p).alcohol_barrels)


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
        # Values whose gf*100 is exact or far from a whole number, where IEEE and C64
        # floats agree. Near a whole cent the config snaps the float drift first (see
        # handlers/turn.py _SCORE_SNAP_DECIMALS), which this float evaluator does not model.
        _grid(
            gf=(0, 0.0078125, 0.125, 0.5, 11.1, 25.1953125, 25.5, 33.337, 51.25, 88.8)
            + (99.9990234375, 100, 101.5, -0.0078125, -0.125, -2.9990234375, -3.5, -12.345)
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
        "setup.new_game (seeded; _roll_stat)",
        _NEW_GAME_GRID,
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


# A quote must start at a statement or condition boundary and end at one.
_BEFORE = r"(?:^|:|\bif|then)\s*"
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

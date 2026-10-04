"""The new-game setup — the handler at :data:`~engine.turns.SETUP_HANDLER_KEY`.

After the title screen the source asks, in this order (``:30``
``gosub100:gosub170:gosub200:goto1000``):

* ``:170-172`` the end year ``x9``: ``x9=int(val(x$))``, asked again by
  ``ifx9<1928orx9>1978goto170``;
* ``:175-176`` the score weight ``x8``: ``x8=val(x$)``, asked again by
  ``ifx8<0.1orx8>2goto175``;
* the house rules -- the port's own step, not the source's: every switchable quirk of
  the catalogue (:mod:`..house_rules`) at faithful unless the players change it;
* ``:205-206`` the player count: ``sz=val(x$)``, asked again by ``ifsz<1orsz>4goto205``;
  ``:210`` ``fori=1tosz`` then sets up ``int(sz)`` players (a ``for`` runs while the
  counter has not passed the limit, so ``2.5`` is two players);
* per player (``:210-220``): the name (``:210``) and the gang name (``:215``), each
  through the input routine (``:290-292``), whose ``:291`` ``ifx$=""orlen(x$)>13``
  asks again; then the eigenschaften screen, ``:220`` ``gosub300`` (``:300-316``).

The eigenschaften screen: ``:300`` opens it; ``:310-312`` roll kraft, intelligenz and
brutalitaet, each through ``:350-360``: ``x=int(rnd(1)*9)*5+10`` is drawn and printed
over and over until a key is pressed (``:355 getx$:ifx$=""goto350``), and the value on
screen then is the value kept. Each draw is one :class:`~engine.interactions.RollFrame`;
the frame answered "stopped" keeps its value. ``:311 in=xor30`` is applied when the
state is built (:func:`..setup.new_game`, by the ``intelligence_or_30`` house rule); the
frame shows the roll ``:350`` prints. ``:313`` prints ``energie: 5``, ``:315`` rolls the
kapital ``ka(i)=int(rnd(1)*5)*500+5000`` and ``:316`` prints it, then ``goto1100``
waits for a key (:class:`~engine.interactions.Acknowledge`).

The handler runs before there is a state (``ctx.state`` is ``None``): it applies no
effects and returns a :class:`..setup.SetupRecord`, which ``new_game(record)`` builds
the first state from. It draws only from ``ctx.rng`` -- a driver passes ``Rng(seed)``,
so a run whose every roll stops on its first frame draws what the keyword ``new_game``
draws for that seed. Every per-player interaction names its player (0-based): with no
state the driver cannot fill it in.

Pre-fills: a caller that already has a setup value passes it as a keyword argument
(``functools.partial``) and that question is not asked. ``players`` is a list of
``(name, gang_name)``; given, the count and name prompts are skipped and each player's
eigenschaften screen still runs. A bad pre-fill raises ``ValueError`` exactly as
``new_game`` refuses it -- a caller bug, not an answer to ask again.

Handler-API conformance: reads only ``ctx.rng``, yields interactions, and uses this
config's own setup helpers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from engine.config_loader import load_config
from engine.interactions import Acknowledge, Heading, PromptText, RollFrame, ShowMessage
from engine.locations import register
from engine.state import FAITHFUL, INTENT
from engine.turns import SETUP_HANDLER_KEY

from ..house_rules import CATALOGUE, CHANGE_KEY, switchable
from ..setup import (
    SetupPlayer,
    SetupRecord,
    _house_rules_map,
    _roll_stat,
    check_end_year,
    check_players,
    check_score_weight,
    in_range,
    name_fits,
    parse_setup_number,
)

__all__ = ["EIGENSCHAFTEN_SCREEN", "setup"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: Heading and Acknowledge of a player's eigenschaften screen (``:300``-``:316``).
EIGENSCHAFTEN_SCREEN = "setup.eigenschaften"

#: The ``:350`` roll's frame key for each of ``:310-312``, in roll order.
_ROLLS = ("setup.roll.kraft", "setup.roll.intelligenz", "setup.roll.brutalitaet")


@register(SETUP_HANDLER_KEY)
def setup(
    ctx,
    *,
    end_year: int | None = None,
    score_weight: float | None = None,
    players: Sequence[tuple[str, str]] | None = None,
    house_rules: Mapping[str, str] | None = None,
):
    """The new-game setup, asking only what was not pre-filled; returns a SetupRecord."""
    cfg = load_config(_CONFIG_DIR / "config.yaml")
    ranges = cfg["input_ranges"]
    # A pre-fill is checked as new_game checks it, before anything is asked.
    if end_year is not None:
        check_end_year(end_year, ranges)
    if score_weight is not None:
        check_score_weight(score_weight, ranges)
    if players is not None:
        check_players(players, ranges)
    chosen = _house_rules_map(_CONFIG_DIR, house_rules) if house_rules is not None else None

    if end_year is None:  # :170-172
        end_year = int((yield from _ask_number("setup.end_year_prompt", ranges["end_year"], True)))
    if score_weight is None:  # :175-176
        score_weight = yield from _ask_number(
            "setup.score_weight_prompt", ranges["score_weight"], False
        )
    if chosen is None:
        chosen = yield from _ask_house_rules(switchable(CATALOGUE))

    if players is None:
        # :205-206; :210 fori=1tosz runs while i<=sz, so a fraction counts its whole part.
        count = int(
            (yield from _ask_number("setup.player_count_prompt", ranges["player_count"], False))
        )
    else:
        count = len(players)

    rolled: list[SetupPlayer] = []
    for i in range(count):
        if players is None:
            name = yield from _ask_name("setup.player_name_prompt", {"number": i + 1}, ranges, i)
            gang_name = yield from _ask_name("setup.gang_name_prompt", {}, ranges, i)
        else:
            name, gang_name = players[i]
        rolled.append((yield from _eigenschaften(ctx.rng, cfg["setup"], name, gang_name, i)))

    return SetupRecord(
        end_year=end_year,
        score_weight=score_weight,
        house_rules=chosen,
        players=tuple(rolled),
    )


def _ask_number(key: str, bounds: Mapping, integer: bool):
    """Ask ``key`` until the answer is a number inside ``bounds`` (:172, :176, :206)."""
    while True:
        value = parse_setup_number((yield PromptText(key)), integer=integer)
        if value is not None and in_range(value, bounds):
            return value


def _ask_name(key: str, params: dict, ranges: Mapping, player: int):
    """:290-292: ask ``key`` until the name is 1 to 13 characters (:291).

    C64 ``INPUT`` hands back the line without its leading and trailing spaces, so they
    are dropped before the length check; a name of spaces alone is empty.
    """
    while True:
        name = str((yield PromptText(key, params, player=player))).strip(" ")
        if name_fits(name, ranges):
            return name


def _roll(rng, key: str, roll: Mapping, player: int):
    """:350-360: draw and show until a frame is answered "stopped"; keep that draw."""
    while True:
        x = _roll_stat(rng, roll)  # :350 x=int(rnd(1)*9)*5+10
        if (yield RollFrame(key, {"value": x}, player=player)):  # :355 getx$:ifx$=""goto350
            return x


def _eigenschaften(rng, setup_cfg: Mapping, name: str, gang_name: str, player: int):
    """:300-316, one player's eigenschaften screen; returns their :class:`SetupPlayer`."""
    yield Heading(EIGENSCHAFTEN_SCREEN, player=player)  # :300
    kept = []
    for key in _ROLLS:  # :310-312
        kept.append((yield from _roll(rng, key, setup_cfg["stat_roll"], player)))
    kraft, intelligenz, brutalitaet = kept
    yield ShowMessage("setup.energie", {"energy": setup_cfg["start_energy"]}, player=player)  # :313
    # :315 ka(i)=int(rnd(1)*5)*500+5000. Its ``ifpeek(53247)=1thenka(i)=500000`` is a
    # debug switch (as :1208's peek(53247) line in handlers/turn.py) and not ported.
    cash = _roll_stat(rng, setup_cfg["cash_roll"])
    yield ShowMessage("setup.kapital", {"cash": cash}, player=player)  # :316
    yield Acknowledge(EIGENSCHAFTEN_SCREEN, player=player)  # :316 goto1100
    return SetupPlayer(name, gang_name, kraft, intelligenz, brutalitaet, cash)


def _ask_house_rules(rules):
    """The house-rules step: each switchable rule's setting, faithful by default.

    With no switchable rule the step is not shown. Any answer at the offer but
    :data:`~..house_rules.CHANGE_KEY` (in any case) keeps every rule faithful; the change
    key opens the list, where a rule's number switches it between faithful and intent
    and an empty answer starts the game (any other answer shows the list again). The
    list prompt carries the rules as ``params["rules"]``: ``{number, id, setting}`` each.
    """
    chosen = {rule.id: FAITHFUL for rule in rules}
    if not rules:
        return chosen
    offer = yield PromptText("setup.house_rules.offer", {"key": CHANGE_KEY})
    if str(offer).strip().lower() != CHANGE_KEY.lower():
        return chosen
    while True:
        listing = [
            {"number": number, "id": rule.id, "setting": chosen[rule.id]}
            for number, rule in enumerate(rules, start=1)
        ]
        answer = str((yield PromptText("setup.house_rules.prompt", {"rules": listing}))).strip()
        if answer == "":
            return chosen
        if answer.isdecimal() and 1 <= int(answer) <= len(rules):
            rule_id = rules[int(answer) - 1].id
            chosen[rule_id] = INTENT if chosen[rule_id] == FAITHFUL else FAITHFUL

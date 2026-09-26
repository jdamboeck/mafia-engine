"""Tests for the weapon entity table + ``load_weapons`` loader (U2).

Ports the 9-record weapon DATA table verbatim from ``mf-prg.bas:50100-50115``
(field order ``name, price, ts, tg, ws`` per ``:121``) plus the per-weapon
stat-requirement metadata DERIVED from the buy-guard lines ``13050-13060``:

- intelligenz >= 40 for weapon index > 5        (``13050``: ``in<40 and x>5``)
- kraft       >= 20 for weapon index in {2, 3}  (``13055``: ``kr<20 and (x=2 or x=3)``)
- brutalitaet >= 40 for weapon index 3 or > 6   (``13060``: ``bt<40 and (x=3 or x>6)``)

Encoded as ``req_int`` / ``req_kraft`` / ``req_brut`` per record (0 = no requirement).

Also covers ``range`` — the shot's travel distance in combat cells, likewise DERIVED
rather than tabulated, from the attack block ``30215-30216``:

- ``r=2`` for every weapon                  (``30215``)
- ``ifw>3thenr=15`` widens ids 4..8         (``30215``)
- ``ifw=6orw=7thenr=20`` — AFTER the widening, so ONLY ids 6/7 reach 20 (``30216``)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data.game_configs.mafia_1920s.setup import load_weapons, weapon_stats_by_id
from engine.types import ConfigValidationError
from tests.test_citations import parse_source

_WEAPONS_YAML = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "game_configs"
    / "mafia_1920s"
    / "entities"
    / "weapons.yaml"
)


@pytest.fixture
def weapons():
    return load_weapons(_WEAPONS_YAML)


def test_loader_returns_nine_weapons_indexed_0_to_8(weapons):
    assert len(weapons) == 9
    assert weapons[0]["name"] == "haende"
    assert weapons[0]["price"] == 0
    assert weapons[8]["name"] == "handgranaten"
    assert weapons[8]["price"] == 10000


def test_field_values_match_source_table(weapons):
    # Ports mf-prg.bas:50100-50115 verbatim. Spot-check the accuracy/damage columns.
    revolver = weapons[5]
    assert revolver["name"] == "revolver"
    assert revolver["price"] == 4000
    assert revolver["ts"] == 5  # accuracy
    assert revolver["tg"] == 10  # damage
    assert revolver["ws"] == 2  # sound

    grenades = weapons[8]
    assert grenades["ts"] == 7
    assert grenades["tg"] == 18
    assert grenades["ws"] == 3

    # A full-row check on messer (index 1): name/price/ts/tg/ws = messer/50/3/5/0.
    messer = weapons[1]
    assert (messer["name"], messer["price"], messer["ts"], messer["tg"], messer["ws"]) == (
        "messer",
        50,
        3,
        5,
        0,
    )


def test_stat_requirement_metadata_boundaries(weapons):
    # Covers the exact 13050-13060 gate boundaries.

    # index 6 (maschinenpistole): x>5 → requires intelligenz>=40, but index 6 is NOT
    # >6 and not in {2,3}, so no kraft/brut requirement.
    masch = weapons[6]
    assert masch["req_int"] == 40
    assert masch["req_kraft"] == 0
    assert masch["req_brut"] == 0

    # index 8 (handgranaten): x>5 → int>=40 AND x>6 → brut>=40.
    grenades = weapons[8]
    assert grenades["req_int"] == 40
    assert grenades["req_brut"] == 40
    assert grenades["req_kraft"] == 0

    # index 3 (schlagkette): x in {2,3} → kraft>=20 AND x=3 → brut>=40; x not >5 → no int.
    schlag = weapons[3]
    assert schlag["req_kraft"] == 20
    assert schlag["req_brut"] == 40
    assert schlag["req_int"] == 0

    # index 2 (knueppel): x in {2,3} → kraft>=20 only.
    knueppel = weapons[2]
    assert knueppel["req_kraft"] == 20
    assert knueppel["req_int"] == 0
    assert knueppel["req_brut"] == 0

    # index 5 (revolver): x=5 is NOT >5, NOT in {2,3}, NOT 3/>6 → no requirements.
    revolver = weapons[5]
    assert revolver["req_int"] == 0
    assert revolver["req_kraft"] == 0
    assert revolver["req_brut"] == 0

    # index 0 (haende, unarmed): no requirements.
    haende = weapons[0]
    assert (haende["req_int"], haende["req_kraft"], haende["req_brut"]) == (0, 0, 0)


#: The value the old hardcoded ``engine.combat.shot_range`` ladder produced for each
#: id, recomputed here in the source's own order rather than copied from the YAML —
#: this is the differential that licensed deleting the function.
def _source_shot_range(weapon: int) -> int:
    r = 2  # 30215: r=2
    if weapon > 3:
        r = 15  # 30215: ifw>3thenr=15
    if weapon in (6, 7):
        r = 20  # 30216: ifw=6orw=7thenr=20 — runs AFTER the widening
    return r


@pytest.mark.parametrize("weapon_id", range(9))
def test_range_attribute_reproduces_the_source_ladder(weapons, weapon_id):
    assert weapons[weapon_id]["range"] == _source_shot_range(weapon_id)


def test_grenades_carry_the_ranged_reach_not_the_heavy_one(weapons):
    # Named guard for the widening edge: id 8 is >3 (so 15) but is neither 6 nor 7.
    assert weapons[8]["name"] == "handgranaten"
    assert weapons[8]["range"] == 15


@pytest.mark.parametrize("weapon_id", range(9))
def test_adjacent_only_reach_selects_exactly_the_sources_melee_ids(weapons, weapon_id):
    # 30415's ``orgw(...)<4`` and "reaches no further than the neighbour" agree on
    # every one of the nine weapons — which is why no separate ``melee`` field exists.
    assert (weapons[weapon_id]["range"] <= 2) == (weapon_id < 4)


def test_stats_by_id_carries_range_alongside_accuracy_and_damage():
    # The combat-start path: handlers hand this table to StartCombat.weapon_stats,
    # and it is the only way a weapon's reach can reach the engine.
    stats = weapon_stats_by_id(_WEAPONS_YAML)
    assert stats[5] == (5, 10, 15)  # revolver: ts, tg, range
    assert stats[6] == (5, 12, 20)  # gewehr: the heavy reach
    assert stats[8] == (7, 18, 15)  # handgranaten: widened, never promoted
    assert stats[0] == (2, 2, 2)  # haende: melee reach


def test_a_weapon_missing_range_is_rejected_at_load_time(tmp_path):
    # ``range`` is part of the weapon contract, not an optional extra: a config that
    # omits it fails validation rather than silently defaulting.
    entry = dict(load_weapons(_WEAPONS_YAML)[5])
    del entry["range"]
    bad = tmp_path / "weapons.yaml"
    bad.write_text(yaml.safe_dump({"weapons": [entry]}), encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="range"):
        load_weapons(bad)


# --------------------------------------------------------------------------- #
# The weapon order, as :120/:121 READ the DATA                                #
# --------------------------------------------------------------------------- #
# The READ statements and the DATA lines they consume, quoted verbatim (checked by
# test_weapon_read_quotes_are_verbatim). :120 reads the 12 location codes of :50005
# first, so :121's nine records start at :50100 and the list index of weapons.yaml
# is the in-game weapon index. basic-relational-boolean-is-minus-one-when-porting.md
# rests its "x>gw is an upgrade" argument on this order.
_READS = (
    (120, "fori=1to12:readlk$(i):next"),
    (121, "fori=0to8:readwa$(i),wp(i),ts(i),tg(i),ws(i):next"),
)
_DATA = (
    (50005, "data slw,pub,waf,aut,kdh,sph,sgl,sub,bhf,ban,pol,ble"),
    (50100, 'data "haende",0,2,2,1,"messer",50,3,5,0,"knueppel",100,4,3,1'),
    (50105, 'data "schlagkette",500,4,4,4,"wurfsterne",3000,2,7,0'),
    (50110, 'data "revolver",4000,5,10,2,"gewehr",4500,5,12,2'),
    (50115, 'data "maschinenpistole",8000,6,15,10,"handgranaten",10000,7,18,3'),
)
_SOURCE = (
    Path(__file__).resolve().parents[2] / "research" / "src" / "decompiled_basic" / "mf-prg.bas"
)


def _read_weapon_records() -> list[tuple[str, int, int, int, int]]:
    """Run :120 then :121 over the quoted DATA: the ``(wa$, wp, ts, tg, ws)`` records."""
    items = iter(
        item.strip('"') for _line, text in _DATA for item in text.removeprefix("data ").split(",")
    )
    for _ in range(12):  # :120 — readlk$(i), i=1..12
        next(items)
    return [
        (next(items), int(next(items)), int(next(items)), int(next(items)), int(next(items)))
        for _ in range(9)  # :121 — i=0..8
    ]


@pytest.mark.skipif(not _SOURCE.exists(), reason=f"research tree absent: {_SOURCE}")
@pytest.mark.parametrize(("line", "text"), _READS + _DATA)
def test_weapon_read_quotes_are_verbatim(line: int, text: str) -> None:
    source = parse_source(_SOURCE.read_text(encoding="utf-8"))
    assert source.get(line) == text, f"mf-prg.bas:{line} is {source.get(line)!r}"


def test_weapon_index_is_the_order_121_reads_the_data(weapons):
    """Every weapons.yaml row, by index, is the record :121 reads into that index."""
    table = [(w["name"], w["price"], w["ts"], w["tg"], w["ws"]) for w in weapons]
    assert table == _read_weapon_records()
    assert table[0][0] == "haende" and table[8][0] == "handgranaten"


def test_weapon_prices_strictly_ascend_with_the_index(weapons):
    """Price rises with every index (0$ haende .. 10000$ handgranaten), so at :13072
    a higher index ``x > gw`` is always the dearer weapon. Power only broadly rises:
    messer (tg 5) outhits knueppel (tg 3), and wurfsterne has the worst aim (ts 2)."""
    prices = [w["price"] for w in weapons]
    assert prices == sorted(set(prices))
    assert prices == [0, 50, 100, 500, 3000, 4000, 4500, 8000, 10000]

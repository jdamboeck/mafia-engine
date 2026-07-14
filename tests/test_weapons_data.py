"""Tests for the weapon entity table + ``load_weapons`` loader (U2).

Ports the 9-record weapon DATA table verbatim from ``mf-prg.bas:50100-50115``
(field order ``name, price, ts, tg, ws`` per ``:121``) plus the per-weapon
stat-requirement metadata DERIVED from the buy-guard lines ``13050-13060``:

- intelligenz >= 40 for weapon index > 5        (``13050``: ``in<40 and x>5``)
- kraft       >= 20 for weapon index in {2, 3}  (``13055``: ``kr<20 and (x=2 or x=3)``)
- brutalitaet >= 40 for weapon index 3 or > 6   (``13060``: ``bt<40 and (x=3 or x>6)``)

Encoded as ``req_int`` / ``req_kraft`` / ``req_brut`` per record (0 = no requirement).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data.game_configs.mafia_1920s.setup import load_weapons

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

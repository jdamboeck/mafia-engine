"""Tests for the committed city map (U1).

The city map is decoded from the original ``karte`` screen file by a one-shot
``tools/`` script and **committed** as ``content/map/city.yaml`` (KTD-1): the
runtime loads plain config data and has no binary-parsing dependency. These
tests assert the committed artifact, not the decode tool — they are what proves
the map data is correct and stays correct.

Ground truth (research, which wins over design-doc prose where they differ):
- The 40x25 map is ``../research/src/karte`` reversed (``data[:1000][::-1]``),
  ``render_c64_assets.py:113-119``. The reversal yields the correct C64 screen-
  code vocabulary (160/156/32/163/224/147 dominant), matching the research's
  code histogram for the map.
- The 41 door tiles + their ``(la, ln)`` come from the ``lc`` location table,
  ``data-structures.yaml:840-883``. The lookup is authoritative on tile
  *cell/address* (``mf-prg.bas:2030,2050``: ``p = br + po(sp) + x``, ``syslc,p``
  -> la/ln), NOT on the screen code at the cell. The table's ``karte_code``
  column is a non-authoritative annotation assembled from mixed sources
  (``data-structures.yaml:905``), so tests assert the cell -> (la, ln) mapping
  and the grid's validity — never that the grid code equals ``karte_code``.
- Special cells 569/861 are dynamic event overlays (``mf-prg.bas:2002-2003``),
  the la=13 cash-transport / la=14 mayor-hit flows — flagged, handlers oos.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CITY_YAML = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "game_configs"
    / "mafia_1920s"
    / "content"
    / "map"
    / "city.yaml"
)


@pytest.fixture(scope="module")
def city():
    assert CITY_YAML.is_file(), f"committed map missing: {CITY_YAML}"
    with CITY_YAML.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- grid shape -----------------------------------------------------------


def test_grid_is_40x25_and_1000_cells(city):
    grid = city["grid"]
    assert len(grid) == 25, "city map has 25 rows"
    assert all(len(row) == 40 for row in grid), "every row is 40 wide"
    flat = [code for row in grid for code in row]
    assert len(flat) == 1000


def test_grid_codes_are_bytes(city):
    flat = [code for row in city["grid"] for code in row]
    assert all(0 <= c <= 255 for c in flat)


# --- door lookup ----------------------------------------------------------


def _door_by_cell(city):
    return {d["cell"]: d for d in city["doors"]}


def test_all_41_doors_present(city):
    assert len(city["doors"]) == 41, "the lc table has 41 tiles"
    cells = [d["cell"] for d in city["doors"]]
    assert len(set(cells)) == 41, "all door cells are unique"
    assert all(0 <= c <= 999 for c in cells)


def test_slw_doors_resolve_to_la1_ln1_5(city):
    by_cell = _door_by_cell(city)
    expected = {122: 1, 180: 2, 406: 3, 666: 4, 811: 5}
    for cell, ln in expected.items():
        assert cell in by_cell, f"slw door cell {cell} missing"
        assert by_cell[cell]["la"] == 1, f"cell {cell} is la=1 (slw)"
        assert by_cell[cell]["ln"] == ln, f"cell {cell} is ln={ln}"


def test_door_code_field_matches_decoded_grid(city):
    """Each door's recorded ``code`` is exactly the decoded grid cell.

    The ``code`` is informational (the reversal's screen code at the cell); we
    only assert internal consistency with the emitted grid, NOT equality with
    the research ``karte_code`` annotation (which is not a byte-faithful read).
    """
    grid = city["grid"]
    for d in city["doors"]:
        row, col = divmod(d["cell"], 40)
        assert d["code"] == grid[row][col]


def test_door_lookup_is_cell_keyed_across_multiple_locations(city):
    """The authoritative lookup is cell -> (la, ln), spanning several locations."""
    by_cell = _door_by_cell(city)
    # A few authoritative table anchors (data-structures.yaml:840-883).
    assert by_cell[433]["la"] == 2 and by_cell[433]["location"] == "pub"
    assert by_cell[910]["la"] == 11 and by_cell[910]["location"] == "pol"
    assert by_cell[371]["la"] == 12 and by_cell[371]["location"] == "ble"
    las = {d["la"] for d in city["doors"]}
    assert las == set(range(1, 13)), "all 12 locations la=1..12 are represented"


def test_map_uses_expected_c64_code_vocabulary(city):
    """The reversal yields real C64 screen codes; 160 and 156 dominate the map."""
    flat = [code for row in city["grid"] for code in row]
    assert 160 in flat and 156 in flat
    # These two codes dominate the map per the research histogram.
    assert flat.count(160) > 100
    assert flat.count(156) > 100


# --- special event cells --------------------------------------------------


def test_special_event_cells_present_and_flagged(city):
    special = city["special_cells"]
    by_cell = {s["cell"]: s for s in special}
    assert 569 in by_cell, "cash-transport event cell 569 present"
    assert 861 in by_cell, "mayor-hit event cell 861 present"
    assert by_cell[569]["la"] == 13, "569 is the la=13 cash-transport flow"
    assert by_cell[861]["la"] == 14, "861 is the la=14 mayor-hit flow"


def test_special_cells_are_not_in_the_door_table(city):
    """569/861 are dynamic overlays, disjoint from the 41 static lc doors."""
    door_cells = {d["cell"] for d in city["doors"]}
    assert 569 not in door_cells
    assert 861 not in door_cells

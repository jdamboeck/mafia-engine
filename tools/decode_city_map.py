#!/usr/bin/env python3
"""One-shot decoder: emit the committed city map config from research data.

The runtime must NOT depend on parsing the original binary (KTD-1), so this
``tools/`` script reads the reverse-engineering sources once and writes a plain
config artifact — ``content/map/city.yaml`` — that the engine loads as data.

Two inputs, both authoritative research sources:

1. ``../research/src/karte`` — the 2003-byte C64 screen file for the city map.
   The bl loader stores it bottom-up / right-to-left, so the 40x25 = 1000 cell
   codes are the file's first 1000 bytes **reversed**
   (``data[:1000][::-1]``), exactly as ``render_c64_assets.py:113-119`` does.

2. ``research-data/pass-2/data-structures.yaml`` — the ``lc_location_table``
   whose ``decoded_entries`` give the 41 door tiles and their ``(la, ln)``.
   The lookup keys on tile address/cell (not screen code), so door cells carry
   varied codes; we record each door's actual code from the decoded grid.

Special event cells 569 (la=13 cash transport) and 861 (la=14 mayor hit) are
dynamic overlays poked by ``mf-prg.bas:2002-2003`` — not lc entries — so they
are emitted in a separate ``special_cells`` block, flagged for the two win
flows whose handlers are out of scope for this slice.

Run from the engine repo root:  ``python tools/decode_city_map.py``
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Reversal constants, mirrored from research/tools/render_c64_assets.py so the
# byte math is not re-derived.
SCREEN_CODES = 1000
SCREEN_FILE_SIZE = 2003  # 1000 screen codes + 1000 color RAM + 3 mode bytes
GRID_COLS = 40
GRID_ROWS = 25

ENGINE_ROOT = Path(__file__).resolve().parent.parent
RESEARCH_ROOT = ENGINE_ROOT.parent / "research"
KARTE = RESEARCH_ROOT / "src" / "karte"
DATA_STRUCTURES = RESEARCH_ROOT / "research-data" / "pass-2" / "data-structures.yaml"
OUT = ENGINE_ROOT / "data" / "game_configs" / "mafia_1920s" / "content" / "map" / "city.yaml"

# Dynamic event-overlay cells (mf-prg.bas:2002-2003); la 13/14 win flows.
SPECIAL_CELLS = [
    {"cell": 569, "la": 13, "flow": "cash_transport", "win_flag": "x5"},
    {"cell": 861, "la": 14, "flow": "mayor_hit", "win_flag": "x6"},
]


def decode_screen_codes(karte: Path) -> list[int]:
    """Return the 1000 row-major cell codes (the karte file's first 1KB, reversed)."""
    data = karte.read_bytes()
    if len(data) != SCREEN_FILE_SIZE:
        raise ValueError(f"{karte.name}: {len(data)} bytes, expected {SCREEN_FILE_SIZE}")
    return list(data[:SCREEN_CODES][::-1])


def decode_color_ram(karte: Path) -> list[int]:
    """Return the 1000 row-major color RAM values (bytes 1000-1999, reversed).

    Each byte's low nibble is the C64 color index (0-15).
    """
    data = karte.read_bytes()
    if len(data) != SCREEN_FILE_SIZE:
        raise ValueError(f"{karte.name}: {len(data)} bytes, expected {SCREEN_FILE_SIZE}")
    return [b & 0x0F for b in data[SCREEN_CODES : SCREEN_CODES * 2][::-1]]


def to_grid(codes: list[int]) -> list[list[int]]:
    """Fold 1000 row-major codes into a 25-row x 40-col grid."""
    if len(codes) != SCREEN_CODES:
        raise ValueError(f"expected {SCREEN_CODES} codes, got {len(codes)}")
    return [codes[r * GRID_COLS : (r + 1) * GRID_COLS] for r in range(GRID_ROWS)]


def _find_key(node: object, key: str) -> object | None:
    """Depth-first search for ``key`` anywhere in a nested dict/list document."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def load_door_entries() -> list[dict]:
    """Read the lc location table's 41 decoded door tiles from the research YAML.

    ``lc_location_table`` lives under ``machine_code:`` in the source; we locate
    it by key rather than a fixed path so a research-side reorg does not break
    the decode silently.
    """
    with DATA_STRUCTURES.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    table = _find_key(doc, "lc_location_table")
    if not isinstance(table, dict):
        raise ValueError("lc_location_table not found in data-structures.yaml")
    entries = table["decoded_entries"]
    if len(entries) != 41:
        raise ValueError(f"expected 41 lc door tiles, got {len(entries)}")
    return entries


def build_doors(entries: list[dict], grid: list[list[int]]) -> list[dict]:
    """Emit ``{cell, la, ln, location, code}`` per door.

    The authoritative door lookup is the lc table's cell -> (la, ln) mapping
    (``mf-prg.bas:2030,2050``: ``p = br + po(sp) + x``; ``syslc,p`` -> la/ln).
    It keys on the tile *cell/address*, NOT the screen code, so we take
    ``cell/la/ln/location`` straight from the table.

    ``code`` is the screen code the reversal decodes at that cell, recorded as
    informational only. It is NOT cross-checked against the research
    ``karte_code`` annotation: that annotation was assembled from mixed sources
    (``data-structures.yaml:905`` — "156/193/32/96 from BASIC"), so it is not a
    byte-faithful read of ``karte`` and would false-positive a drift error.
    """
    doors = []
    for e in entries:
        cell = e["cell"]
        row, col = divmod(cell, GRID_COLS)
        doors.append(
            {
                "cell": cell,
                "la": e["la"],
                "ln": e["ln"],
                "location": e["location"],
                "code": grid[row][col],
            }
        )
    return doors


def main() -> None:
    codes = decode_screen_codes(KARTE)
    colors = decode_color_ram(KARTE)
    grid = to_grid(codes)
    color_grid = to_grid(colors)
    doors = build_doors(load_door_entries(), grid)

    payload = {
        "_generated_by": "tools/decode_city_map.py",
        "_sources": [
            "../research/src/karte (data[:1000][::-1], render_c64_assets.py:113-119)",
            "../research/research-data/pass-2/data-structures.yaml"
            " lc_location_table.decoded_entries (:840-883)",
        ],
        "dims": {"cols": GRID_COLS, "rows": GRID_ROWS},
        "grid": grid,
        "color_grid": color_grid,
        "doors": doors,
        "special_cells": SPECIAL_CELLS,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write(
            "# GENERATED by tools/decode_city_map.py — do not edit by hand.\n"
            "# Re-run the tool to regenerate from ../research/ sources.\n"
        )
        yaml.safe_dump(payload, fh, sort_keys=False, default_flow_style=None)

    print(
        f"wrote {OUT} ({len(doors)} doors, {len(SPECIAL_CELLS)} special cells, color_grid included)"
    )


if __name__ == "__main__":
    main()

"""Tests for U8 — config assembly + new-game setup routine.

Written proof-first (before ``engine/setup.py`` and the config YAML exist).

Covers:
- Starting values from the BASIC setup rolls (mf-prg.bas:220,300-315,350),
  including the intelligenz OR-30 quirk and the on-foot ms=25.
- The RNG determinism pipeline end-to-end (same seed reproduces, different
  seeds differ) — every roll flows through ``ctx.rng``, never ``random``.
- The 500000 cheat branch is absent (cash always in the 5000-7000 band).
- ``engine_api == 1`` validation via ``load_config``.
- The ``fnm`` rent-formula params (fnm(1)==-50 negative-rent quirk).
- Input-range validation (end_year, score_weight, player count).
- Vehicle/rank entity data.
"""

from pathlib import Path

import pytest

from engine.config_loader import load_config, load_game_config

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# new_game / fnm / load_vehicles / load_ranks are now CONFIG-owned code (they moved
# out of engine/ into the config, per docs/design/product-and-scope.md/§6a). Reach them through the config
# package, loaded BY PATH via the engine loader — the config is deliberately not a
# pip-installed package (see engine.config_loader). load_config / engine_api
# validation remain the engine's generic responsibility.
_MAFIA = load_game_config(CONFIG_ROOT).module
new_game = _MAFIA.new_game
fnm = _MAFIA.fnm
load_vehicles = _MAFIA.setup.load_vehicles
load_ranks = _MAFIA.setup.load_ranks
CONFIG_PATH = CONFIG_ROOT / "config.yaml"
VEHICLES_PATH = CONFIG_ROOT / "entities" / "vehicles.yaml"
RANKS_PATH = CONFIG_ROOT / "entities" / "ranks.yaml"

STAT_ROLLS = {10, 15, 20, 25, 30, 35, 40, 45, 50}
INTEL_ROLLS = {30, 31, 62, 63}
CASH_ROLLS = {5000, 5500, 6000, 6500, 7000}


def _one_player_game(seed=1234):
    return new_game(
        seed=seed,
        end_year=1978,
        score_weight=1.0,
        players=[("Al", "Capones")],
    )


# --- starting values -------------------------------------------------------


def test_starting_values_in_range():
    gs = _one_player_game()
    p = gs.players[0]
    assert p.ka in CASH_ROLLS
    assert p.po == 18
    assert p.rank == 1
    assert p.nr == 1
    assert p.vehicle == 0
    assert p.ms == 25  # on-foot tr(0)

    assert len(p.roster) == 1
    g = p.roster[0]
    assert g.name == "Al"  # gangster named after the player
    assert g.energie == 5
    assert g.kraft in STAT_ROLLS
    assert g.brutalitaet in STAT_ROLLS
    assert g.intelligenz in INTEL_ROLLS  # OR-30 quirk


def test_intelligenz_or30_quirk_over_many_seeds():
    for seed in range(200):
        gs = new_game(
            seed=seed, end_year=1978, score_weight=1.0, players=[("P", "G")]
        )
        assert gs.players[0].roster[0].intelligenz in INTEL_ROLLS


# --- rng pipeline determinism ---------------------------------------------


def test_same_seed_reproduces_stats_exactly():
    a = _one_player_game(seed=99)
    b = _one_player_game(seed=99)
    pa, pb = a.players[0], b.players[0]
    ga, gb = pa.roster[0], pb.roster[0]
    assert pa.ka == pb.ka
    assert (ga.kraft, ga.intelligenz, ga.brutalitaet) == (
        gb.kraft,
        gb.intelligenz,
        gb.brutalitaet,
    )


def test_different_seeds_differ():
    # Collect stats across a spread of seeds; they must not all be identical.
    sigs = set()
    for seed in range(30):
        gs = new_game(
            seed=seed, end_year=1978, score_weight=1.0, players=[("P", "G")]
        )
        g = gs.players[0].roster[0]
        sigs.add((gs.players[0].ka, g.kraft, g.intelligenz, g.brutalitaet))
    assert len(sigs) > 1


def test_cheat_branch_absent_cash_band():
    for seed in range(300):
        gs = new_game(
            seed=seed, end_year=1978, score_weight=1.0, players=[("P", "G")]
        )
        assert 5000 <= gs.players[0].ka <= 7000
        assert gs.players[0].ka != 500000


# --- engine_api ------------------------------------------------------------


def test_load_config_accepts_v1():
    cfg = load_config(CONFIG_PATH)
    assert cfg["engine_api"] == 1


def test_load_config_rejects_wrong_version(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine_api: 2\n")
    with pytest.raises((ValueError, RuntimeError)):
        load_config(p)


def test_load_config_rejects_missing_version(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("name: nope\n")
    with pytest.raises((ValueError, RuntimeError)):
        load_config(p)


# --- fnm rent formula ------------------------------------------------------


def test_fnm_negative_rent_quirk():
    cfg = load_config(CONFIG_PATH)
    params = cfg["formula_params"]["fnm"]
    assert fnm(1, params) == -50  # negative-rent quirk — headline
    assert fnm(3, params) == 0
    assert fnm(4, params) == 0
    assert fnm(2, params) == 50
    assert fnm(5, params) == 50
    for ln in (6, 7, 8, 9):
        assert fnm(ln, params) == 50


# --- input validation ------------------------------------------------------


@pytest.mark.parametrize("end_year", [1900, 1927, 1979, 2000])
def test_end_year_out_of_range(end_year):
    with pytest.raises(ValueError):
        new_game(seed=1, end_year=end_year, score_weight=1.0, players=[("P", "G")])


@pytest.mark.parametrize("weight", [0, 0.05, 2.5, 3])
def test_score_weight_out_of_range(weight):
    with pytest.raises(ValueError):
        new_game(seed=1, end_year=1978, score_weight=weight, players=[("P", "G")])


def test_player_count_zero():
    with pytest.raises(ValueError):
        new_game(seed=1, end_year=1978, score_weight=1.0, players=[])


def test_player_count_five():
    with pytest.raises(ValueError):
        new_game(
            seed=1,
            end_year=1978,
            score_weight=1.0,
            players=[("A", "a"), ("B", "b"), ("C", "c"), ("D", "d"), ("E", "e")],
        )


def test_multiplayer_ok():
    gs = new_game(
        seed=7,
        end_year=1950,
        score_weight=0.5,
        players=[("A", "a"), ("B", "b")],
    )
    assert gs.clock.player_count == 2
    assert gs.clock.end_year == 1950
    assert gs.config.score_mult == 0.5
    assert gs.players[1].roster[0].name == "B"


# --- vehicle / rank entity data -------------------------------------------


def test_vehicles_data():
    vs = load_vehicles(VEHICLES_PATH)
    assert len(vs) == 6
    assert vs[0]["name"] == "fuesse"
    assert vs[0]["tank"] == 50
    assert vs[0]["tr"] == 25
    assert vs[4]["name"] == "auburn mod.120"
    assert vs[4]["tr"] == 60
    assert vs[3]["tank"] == 200


def test_ranks_data():
    rs = load_ranks(RANKS_PATH)
    assert len(rs) == 10
    assert rs[0] == "anfaenger"  # index 1 in the game, first entry here
    assert rs[9] == "chef der unterwelt"

"""Tests for U8 — config assembly + new-game setup routine.

Written proof-first (before ``engine/setup.py`` and the config YAML exist).

Covers:
- Starting values from the BASIC setup rolls (mf-prg.bas:220,300-315,350),
  including the intelligenz OR-30 quirk and the on-foot ms=25.
- The RNG determinism pipeline end-to-end (same seed reproduces, different
  seeds differ) — every roll flows through ``ctx.rng``, never ``random``.
- The 500000 cheat branch is absent (cash always in the 5000-7000 band).
- ``engine_api == 1`` validation via ``load_config``.
- The ``fnm`` rent-formula params (fnm(1)==150 premium tile).
- Input-range validation (end_year, score_weight, player count).
- Vehicle/rank entity data.
"""

from pathlib import Path

import pytest

from engine.config_loader import load_config, load_game_config
import data.game_configs.mafia_1920s.state as game

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
    assert game.next_rank(p) == 1
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
        gs = new_game(seed=seed, end_year=1978, score_weight=1.0, players=[("P", "G")])
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
        gs = new_game(seed=seed, end_year=1978, score_weight=1.0, players=[("P", "G")])
        g = gs.players[0].roster[0]
        sigs.add((gs.players[0].ka, g.kraft, g.intelligenz, g.brutalitaet))
    assert len(sigs) > 1


def test_cheat_branch_absent_cash_band():
    for seed in range(300):
        gs = new_game(seed=seed, end_year=1978, score_weight=1.0, players=[("P", "G")])
        assert 5000 <= gs.players[0].ka <= 7000
        assert gs.players[0].ka != 500000


# --- engine_api ------------------------------------------------------------


def test_load_config_accepts_v2():
    cfg = load_config(CONFIG_PATH)
    assert cfg["engine_api"] == 2


def test_a_config_declaring_engine_api_1_is_refused_with_a_clear_message(tmp_path):
    """KTD-5: the engine speaks engine_api 2 only; the real config, set back to 1, is
    refused naming both versions -- before any other check."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    assert "\nengine_api: 2\n" in text
    p = tmp_path / "config.yaml"
    p.write_text(text.replace("\nengine_api: 2\n", "\nengine_api: 1\n"), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(p)
    message = str(exc.value)
    assert "unsupported engine_api 1" in message
    assert "only accepts engine_api == 2" in message


def test_load_config_rejects_missing_version(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("name: nope\n")
    with pytest.raises((ValueError, RuntimeError)):
        load_config(p)


# --- fnm rent formula ------------------------------------------------------


def test_fnm_rent_tiers():
    # :115 `deffnm(ln) = 50 - 50*(ln=3 or ln=4) - 100*(ln=1)`, C64 true = -1:
    #   ln=1   -> 50 - 0     - 100*(-1) = 150  (premium tile)
    #   ln=3,4 -> 50 - 50*(-1)          = 100
    #   else   -> 50
    #
    # #47 audit: this asserted -50/0/50 — a "negative-rent quirk" in which tile ln=1
    # PAID the tenant. That came from the since-reversed true=+1 pin, and it also made
    # the affordability check at :10035 (`ifka(sp)<x*p`) permanently false, i.e. dead
    # code. Under the C64 reading these are an ordinary premium/standard/cheap ladder.
    cfg = load_config(CONFIG_PATH)
    params = cfg["formula_params"]["fnm"]
    assert fnm(1, params) == 150  # premium tile — headline
    assert fnm(3, params) == 100
    assert fnm(4, params) == 100
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


@pytest.mark.parametrize("player", [("", "G"), ("P", ""), ("A" * 14, "G"), ("P", "B" * 14)])
def test_a_name_empty_or_over_13_characters_is_refused(player):
    # :291 ``ifx$=""orlen(x$)>13``: the input routine asks again for both names.
    with pytest.raises(ValueError, match="1 to 13 characters"):
        new_game(seed=1, end_year=1978, score_weight=1.0, players=[player])


def test_a_name_of_13_characters_is_taken():
    gs = new_game(seed=1, end_year=1978, score_weight=1.0, players=[("A" * 13, "B" * 13)])
    assert gs.players[0].name == "A" * 13


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
    assert gs.config.formula_params["score_mult"] == 0.5
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


# --- start year (U4, R3/KTD-3) --------------------------------------------
#
# mf-prg.bas:1000 (``sp=0:ja=1925``): the game clock starts at 1925, NOT at the
# end-year floor 1928 (mf-prg.bas:172 validates x9 to [1928,1978] — unrelated).


def test_new_game_starts_january_1925():
    gs = _one_player_game()
    assert gs.clock.year == 1925
    assert gs.clock.month == 0


def test_ae5_end_year_1928_game_over_on_36th_round():
    """AE5: from Jan 1925 with end year 1928 and one player, each turn is a full round
    (one month); game_over fires first on the 36th turn (Jan 1928)."""
    from tests.helpers import next_turn_by_hand

    st = new_game(seed=1, end_year=1928, score_weight=1.0, players=[("Al", "Capones")])
    for call in range(1, 36):
        st, over = next_turn_by_hand(st)
        assert over is False, f"game_over too early, on call {call}"
    st, over = next_turn_by_hand(st)
    assert over is True
    assert (st.clock.year, st.clock.month) == (1928, 0)


def test_start_year_is_config_data(tmp_path):
    """The start year comes from config.yaml ``setup.start_year``, not code."""
    import shutil

    import yaml

    cfg_dir = tmp_path / "cfg"
    shutil.copytree(CONFIG_ROOT, cfg_dir)
    cfg_file = cfg_dir / "config.yaml"
    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert cfg["setup"]["start_year"] == 1925
    cfg["setup"]["start_year"] = 1931
    cfg_file.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    gs = new_game(
        seed=1,
        end_year=1978,
        score_weight=1.0,
        players=[("Al", "Capones")],
        config_path=cfg_file,
    )
    assert gs.clock.year == 1931
    assert gs.clock.month == 0

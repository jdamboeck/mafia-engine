"""U7 — the waf end-to-end integration trajectory (headless).

Proves waf composes with movement + the driver like the existing slice gate: a seeded
headless play-through walks into the weapon shop, BUYS a weapon (through the spec-sheet
LoadSubState + a passing stat gate), TRAINS a gangster at the range, and asserts the
resulting committed effects / final state — mutation-verified, deterministic, and with
NO import from clients/ (headlessness preserved; U10 is a renderer over this protocol).

Covers R4-R13 in a real playthrough and exercises R7/R14 (LoadSubState) end-to-end, not
just a unit stub.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.interactions import run
from engine.locations import available_options, load_location
from engine.movement import RIGHT, load_city, try_move
from tests.helpers import with_player

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_CONFIG = load_game_config(_CONFIG_DIR)
new_game = _CONFIG.module.new_game

_CITY_YAML = _CONFIG_DIR / "content" / "map" / "city.yaml"
_WAF_SHELL = _CONFIG_DIR / "content" / "locations" / "waf.yaml"
_WAF_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "waf.yaml"

SEED = 42


def _load_city():
    return load_city(yaml.safe_load(_CITY_YAML.read_text(encoding="utf-8")))


def _load_shell():
    return load_location(yaml.safe_load(_WAF_SHELL.read_text(encoding="utf-8")))


def _fresh_state():
    return new_game(seed=SEED, end_year=1930, score_weight=1.0, players=[("al", "outfit")])


def _opt(location, opt_id):
    return next(o for o in location.options if o.id == opt_id)


def _recorder(*answers):
    """input_source returning scripted answers by interaction type is overkill here; use a
    plain in-order recorder that also drives LoadSubState's display-only sub-state."""
    it = iter(answers)
    seen = []

    def source(interaction):
        seen.append(interaction)
        return next(it)

    source.seen = seen
    return source


# --------------------------------------------------------------------------- #
# Config + strings load (structural)                                          #
# --------------------------------------------------------------------------- #
def test_config_loads_with_waf_registered_and_strings_present():
    loc = _load_shell()
    assert loc.key == "waf"
    ids = [o.id for o in loc.options]
    assert ids == ["buy", "train", "leave"]
    assert _opt(loc, "buy").handler is _CONFIG.handlers["waf.buy"]
    assert _opt(loc, "train").handler is _CONFIG.handlers["waf.train"]

    data = yaml.safe_load(_WAF_STRINGS.read_text(encoding="utf-8"))

    def get(dotted):
        node = data
        for part in dotted.split("."):
            node = node[part]
        return node

    # Every key the handler emits must resolve.
    for key in [
        "weapon_prompt", "grenades_in", "gangster_prompt", "too_dumb", "too_weak",
        "not_brutal", "trade_in_offer", "trade_in_confirm", "bought", "spec_sheet",
        "no_gangster", "train_prompt", "venue_prompt", "venue_range", "venue_camp",
        "range_cost", "range_enter", "confirm", "camp_cost", "camp_enter", "camp_done",
    ]:
        assert get(f"locations.waf.{key}"), f"missing string for {key}"
    # The bucketed ts$/tg$ label arrays are present (A2). YAML keeps the numeric bucket
    # keys as ints, so index them directly rather than via the dotted-string helper.
    assert get("locations.waf.accuracy_labels")[3] == "todsicher"
    assert get("locations.waf.effect_labels")[5] == "erschreckend!"


# --------------------------------------------------------------------------- #
# The trajectory                                                              #
# --------------------------------------------------------------------------- #
def _play_trajectory():
    obs: dict = {}
    city = _load_city()
    waf = _load_shell()

    state = _fresh_state()
    p = state.players[0]
    obs["start_cash"] = p.ka
    obs["start_gangster_weapon"] = p.roster[0].weapon
    obs["start_kraft"] = p.roster[0].kraft

    # --- walk into waf ln=2 (door 370) from the left (cell 369 --RIGHT--> enter) ---
    state = with_player(state, po=369)
    p = state.players[0]
    r_enter = try_move(state, city, RIGHT)
    state = r_enter.state
    p = state.players[0]
    assert r_enter.payload.kind == "enter"
    assert r_enter.payload.la == 3 and r_enter.payload.ln == 2
    assert p.last_location == 2  # the ln seam populated by real entry
    obs["ln_after_walk"] = p.last_location

    # --- BUY messer (index 1) for the single (unarmed) gangster 0 ---
    # Sequence: weapon PromptInt(1) -> spec-sheet LoadSubState (auto-run) -> gangster
    # PromptChoice(0). Unarmed -> q=0, no trade-in confirm; cash -= 50, weapon assigned.
    ka_before_buy = p.ka
    buy_rec = _recorder(1, 0)  # weapon 1, gangster 0
    assert "buy" in {o.id for o in available_options(waf, state, ln=2)}
    buy_result = run(_opt(waf, "buy").handler, buy_rec, state=state, rng=None)
    assert buy_result.status == "completed"
    state = buy_result.state
    p = state.players[0]
    assert p.roster[0].weapon == 1  # messer assigned
    assert p.ka == ka_before_buy - 50  # messer price 50, no trade-in
    # The buy reached the gangster pick, which only happens AFTER the spec-sheet
    # LoadSubState ran and threaded its result back (the driver auto-runs the sub-state,
    # so it is not seen by the input_source) — the completed buy proves R7/R14 end-to-end.
    assert any(type(i).__name__ == "PromptChoice" for i in buy_rec.seen)
    obs["weapon_after_buy"] = p.roster[0].weapon
    obs["cash_after_buy"] = p.ka

    # --- TRAIN gangster 0 at the range (rank 1 -> no venue choice) ---
    # Cost 800 + 200*1 = 1000; ln=2 -> kraft+5, int+3, brut+2-3=-1, each capped 99;
    # score reward x=1.
    import engine.rng as _rngmod

    rng = _rngmod.Rng(SEED)
    ka_before_train = p.ka
    kraft_before = p.roster[0].kraft
    train_rec = _recorder(0, True)  # gangster 0, confirm the range cost
    assert "train" in {o.id for o in available_options(waf, state, ln=2)}
    train_result = run(_opt(waf, "train").handler, train_rec, state=state, rng=rng)
    assert train_result.status == "completed"
    state = train_result.state
    p = state.players[0]
    assert p.ka == ka_before_train - 1000
    assert p.roster[0].kraft == min(99, kraft_before + 5)
    obs["cash_after_train"] = p.ka
    obs["kraft_after_train"] = p.roster[0].kraft
    obs["score_after_train"] = p.gf
    obs["rank_nr_after_train"] = p.nr

    return obs


def test_waf_trajectory_end_to_end():
    obs = _play_trajectory()
    # Buy: messer assigned, cash down 50.
    assert obs["weapon_after_buy"] == 1
    assert obs["cash_after_buy"] == obs["start_cash"] - 50
    # Train (range, ln=2): cost 1000, kraft +5.
    assert obs["cash_after_train"] == obs["cash_after_buy"] - 1000
    assert obs["kraft_after_train"] == min(99, obs["start_kraft"] + 5)
    # Training scored: gf rose (x=1 * score_mult), rank recomputed.
    assert obs["score_after_train"] > 0
    assert obs["rank_nr_after_train"] >= 1


def test_waf_trajectory_is_deterministic():
    a = _play_trajectory()
    b = _play_trajectory()
    assert a == b  # same seed -> identical trajectory


def test_headless_no_clients_import():
    # Driving waf pulls in NOTHING NEW from clients/ (headlessness preserved). Compared as
    # a before/after sys.modules delta, not an absolute absence: sys.modules is process-
    # global, so a sibling test (test_bootstrap smoke-imports clients) can leave an entry
    # behind — the waf trajectory just must not ADD one.
    import sys

    before = {m for m in sys.modules if m == "clients" or m.startswith("clients.")}
    _play_trajectory()
    after = {m for m in sys.modules if m == "clients" or m.startswith("clients.")}
    assert not (after - before), (
        f"the waf trajectory imported {sorted(after - before)} from clients — "
        "it must be fully headless"
    )

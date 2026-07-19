"""Tests for the turn-start upkeep spine (U3): ``engine.upkeep.run_upkeep`` +
the config's ``upkeep.turn_start`` generator (ports ``mf-prg.bas:4000-4090``).

Covers:
- energy regen formula + cap, boss included, hand-computed against the research;
- rank commit fires only when ``rank != nr``, and the promotion screen text keys
  resolve;
- atomicity (effects commit together) and the no-cancel-path contract (no player
  input can discard the flow — there IS no input to discard it with);
- the engine-level coupling: ``run_upkeep`` is the one entry point, looked up via
  the SAME ``HANDLERS`` registry a location handler's id resolves against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config_loader import load_game_config
from engine.effects import EnergyChange, RankCommit
from engine.interactions import ShowMessage
from engine.locations import HANDLERS
from engine.state import Clock, Config, Gangster, GameState, Player
from engine.strings import Resolver
from engine.upkeep import UPKEEP_HANDLER_KEY, run_upkeep
from tests.helpers import run_pure

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# Load the config by path so "upkeep.turn_start" registers.
load_game_config(_CONFIG_DIR)


def _state(*, roster=None, rank=1, nr=1, gf=0.0, name="alcapone", gang_name="the outfit"):
    roster = roster if roster is not None else [Gangster(name=name, energie=5, kraft=15, brutalitaet=30)]
    player = Player(name=name, gang_name=gang_name, rank=rank, nr=nr, gf=gf, roster=roster)
    return GameState(
        players=[player],
        clock=Clock(active_player=0, player_count=1),
        config=Config(),
    )


# --------------------------------------------------------------------------- #
# registration: the config populates the SAME HANDLERS registry (KTD-3)       #
# --------------------------------------------------------------------------- #
def test_upkeep_handler_registered_in_shared_handlers_registry():
    assert UPKEEP_HANDLER_KEY in HANDLERS
    assert UPKEEP_HANDLER_KEY == "upkeep.turn_start"


def test_run_upkeep_raises_key_error_when_nothing_registered():
    with pytest.raises(KeyError):
        run_upkeep(_state(), handlers={})


# --------------------------------------------------------------------------- #
# energy regen: en += kraft//10+1, capped at 2+kraft//4+brutalitaet//4         #
# (mf-prg.bas:4015-4025) — hand-computed against the research formula.        #
# --------------------------------------------------------------------------- #
def test_regen_formula_unclamped_case():
    # kraft=15 -> gain = 15//10+1 = 2; cap = 2+15//4+30//4 = 2+3+7 = 12; 5+2=7 < 12.
    state = _state(roster=[Gangster(energie=5, kraft=15, brutalitaet=30)])
    result = run_upkeep(state)
    assert result.status == "completed"
    energy_effects = [e for e in result.effects if isinstance(e, EnergyChange)]
    assert energy_effects == [EnergyChange(amount=2, cap=12, gangster=0)]
    assert result.state.players[0].roster[0].energie == 7


def test_regen_formula_clamps_at_cap():
    # kraft=0, brutalitaet=0 -> gain = 0//10+1 = 1; cap = 2+0+0 = 2. energie starts at 5
    # (above the cap already) -> the clamp brings it DOWN to 2, matching ":4020"'s
    # ifen>xthenen=x (an upper clamp, not a floor-only guard).
    state = _state(roster=[Gangster(energie=5, kraft=0, brutalitaet=0)])
    result = run_upkeep(state)
    assert result.state.players[0].roster[0].energie == 2


def test_regen_formula_gain_added_then_clamped():
    # kraft=40, brutalitaet=20: gain = 40//10+1 = 5; cap = 2+40//4+20//4 = 2+10+5 = 17.
    # energie starts at 10 -> 10+5=15, under cap -> unclamped.
    state = _state(roster=[Gangster(energie=10, kraft=40, brutalitaet=20)])
    result = run_upkeep(state)
    assert result.state.players[0].roster[0].energie == 15


def test_regen_runs_for_every_gangster_including_the_boss():
    # gz(sp) loop (:4010) covers ALL of the player's gangsters, roster[0] (the boss,
    # KTD-6) included — two gangsters means two EnergyChange effects, distinct gangster
    # indices, each computed from ITS OWN stats.
    boss = Gangster(name="boss", energie=5, kraft=10, brutalitaet=10)  # gain=2 cap=2+2+2=6
    hire = Gangster(name="hire", energie=5, kraft=20, brutalitaet=40)  # gain=3 cap=2+5+10=17
    state = _state(roster=[boss, hire])
    result = run_upkeep(state)
    energy_effects = [e for e in result.effects if isinstance(e, EnergyChange)]
    assert energy_effects == [
        EnergyChange(amount=2, cap=6, gangster=0),
        EnergyChange(amount=3, cap=17, gangster=1),
    ]
    assert result.state.players[0].roster[0].energie == 6  # 5+2=7 clamped to 6
    assert result.state.players[0].roster[1].energie == 8  # 5+3=8 under cap 17


# --------------------------------------------------------------------------- #
# rank commit: fires ONLY when rank != nr; the promotion screen text resolves #
# --------------------------------------------------------------------------- #
def test_rank_commit_fires_when_pending_rank_differs():
    state = _state(rank=1, nr=4, gf=52.0)
    result = run_upkeep(state)
    rank_effects = [e for e in result.effects if isinstance(e, RankCommit)]
    assert rank_effects == [RankCommit(new_rank=4)]
    assert result.state.players[0].rank == 4


def test_rank_commit_does_not_fire_when_rank_equals_nr():
    state = _state(rank=3, nr=3, gf=25.0)
    result = run_upkeep(state)
    assert [e for e in result.effects if isinstance(e, RankCommit)] == []
    assert result.state.players[0].rank == 3


def test_promotion_screen_text_keys_resolve():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    banner = resolver.resolve("upkeep.turn_banner", {"name": "alcapone"})
    assert "alcapone" in banner
    promo = resolver.resolve(
        "upkeep.rank_promotion",
        {"gang_name": "the outfit", "name": "alcapone", "score": 52.0, "rank_name": "langfinger"},
    )
    assert "the outfit" in promo
    assert "alcapone" in promo
    assert "langfinger" in promo
    assert "52.0" in promo


def test_promotion_uses_the_configured_rank_name_table():
    # rank 4 -> ranks.yaml index 3 -> "langfinger" (entities/ranks.yaml).
    state = _state(rank=1, nr=4, gf=52.0)
    result = run_upkeep(state)
    assert result.status == "completed"
    # The handler doesn't leak the rank name onto state; assert via the resolver +
    # entities table directly (the same lookup the handler performs).
    import yaml

    ranks = yaml.safe_load((_CONFIG_DIR / "entities" / "ranks.yaml").read_text())["ranks"]
    assert ranks[result.state.players[0].rank - 1] == "langfinger"


# --------------------------------------------------------------------------- #
# atomicity + no-cancel-path                                                  #
# --------------------------------------------------------------------------- #
def test_effects_commit_atomically_energy_and_rank_together():
    state = _state(rank=1, nr=2, gf=15.0, roster=[Gangster(energie=5, kraft=15, brutalitaet=30)])
    result = run_upkeep(state)
    assert result.status == "completed"
    kinds = [type(e).__name__ for e in result.effects]
    assert "EnergyChange" in kinds
    assert "RankCommit" in kinds
    # Both landed on the SAME returned state (one commit, not two separate ones).
    assert result.state.players[0].roster[0].energie == 7
    assert result.state.players[0].rank == 2


def test_upkeep_offers_no_cancel_path():
    # Every interaction upkeep yields is a display-only ShowMessage. Since #43 those
    # ARE delivered to the input source (for rendering) — but they ask nothing, and
    # the driver acks them regardless of the reply. So a source that refuses every
    # QUESTION still proves the point: no prompt ever reached it, hence there is no
    # path for player input to cancel this.
    state = _state(rank=1, nr=4, gf=52.0)
    narrated = []

    def refuse(interaction):
        if isinstance(interaction, ShowMessage):
            narrated.append(interaction)
            return None
        raise AssertionError(f"upkeep must not PROMPT the input source: {interaction!r}")

    result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], refuse, state=state)
    assert narrated, "upkeep's banner/promotion narration must reach the client"
    assert result.status == "completed"  # never "cancelled" — nothing CAN cancel it


def test_run_pure_clean_for_the_registered_handler():
    state = _state()
    result = run_pure(HANDLERS[UPKEEP_HANDLER_KEY], lambda i: None, state=state)
    assert result.status == "completed"


# --------------------------------------------------------------------------- #
# multi-player: upkeep targets the ACTIVE player only                         #
# --------------------------------------------------------------------------- #
def test_upkeep_only_touches_the_active_player():
    p0 = Player(name="p0", rank=1, nr=1, roster=[Gangster(energie=5, kraft=10, brutalitaet=10)])
    p1 = Player(name="p1", rank=1, nr=3, gf=25.0, roster=[Gangster(energie=5, kraft=10, brutalitaet=10)])
    state = GameState(
        players=[p0, p1], clock=Clock(active_player=1, player_count=2), config=Config()
    )
    result = run_upkeep(state)
    assert result.state.players[0] == p0  # untouched — not the active player
    assert result.state.players[1].rank == 3  # promoted
    assert result.state.players[1].roster[0].energie == 6  # 5 + (10//10+1)=2 -> cap 2+2+2=6

"""Tests for the pub.recruit handler — U9, the 30-candidate research recruit flow.

Proof-first: written and observed RED (``RosterAppend`` NotImplementedError from the
U2 groundwork stub, plus the U9-stub's own unconditional raise) before implementation.

Ports ``mf-prg.bas:12100-12175`` verbatim. Guards, in order:

1. ``:12100-12102`` — rank guard ``ra(sp) > 4``.
2. ``:12103-12104`` — housing guard: at least one of 5 apartment tenancy slots
   (``uk(i)=sp``).
3. ``:12105`` — crew cap: ``gz(sp) == 10`` denies. ``gz(sp)`` counts the BOSS at
   ``roster[0]`` (KTD-6, ``mf-prg.bas:300``/``4651``) — so the cap is total roster
   length 10, i.e. at most NINE hires.
4. ``:12106`` — offer pool: unhired-candidate count, capped at 3.
5. ``:12107`` — roll ``x`` in ``[0, pool]``; ``x=0`` OR pub tile ``ln=3`` -> nobody
   available.
6. ``:12108-12175`` — per-candidate loop: draw with reroll on hired/already-drawn,
   gendered intro, offer+confirm, afford check, settle (price deducted, roster
   append at energy 5, candidate globally marked hired). The mid-batch cap check
   sits at ``:12145``, AFTER the confirm (``:12136``) and the cash check (``:12140``).

The ``:12145`` order, gated through ``mafia-oracle conclude 12100-12175`` (plus
``quote 12005``/``1100``/``1110``/``1115``/``1125``):

    According to mf-prg.bas:12136 (``gosub1110:ifx$="n"goto12175``), a "no" skips to
    :12175 (``nexti:return``) and the batch goes on to the next candidate. According
    to mf-prg.bas:12140 (``ifka(sp)<pthengosub1125:goto12175``), too little cash
    prints :1125 ("du hast zu wenig kies!", then :1100 press-a-key) and also goes on
    to the next candidate. Only then does mf-prg.bas:12145 (``ifgz(sp)=10goto12005``)
    test the cap; :12005 (``onwgoto12010,12100,12200,12300``) dispatches on the
    unchanged menu choice ``w`` back to :12100, the rank (:12100) and housing
    (:12103-12104) guards pass again, and :12105
    (``ifgz(sp)=10thenprint"{down}maximal 10 gangster!":goto1100``) prints the
    gang-full message and goes to :1100 (press-a-key, return). Nothing in
    :12108-12145 or the subroutines it calls (:1100, :1110/:1115, :1125) assigns
    ``w``, ``ra(sp)`` or ``uk(i)``, so the re-entry always lands on :12105.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.effects import MoneyChange, RosterAppend
from data.game_configs.mafia_1920s.effects import GangsterMarkHired
from engine.locations import HANDLERS, available_options, load_location
from engine.state import Clock, Config, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster
from tests.helpers import StubRng, run_pure, scripted as _scripted
import data.game_configs.mafia_1920s.state as game

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
load_game_config(_CONFIG_DIR)

_PUB_SHELL = _CONFIG_DIR / "content" / "locations" / "pub.yaml"
_RANKS = yaml.safe_load((_CONFIG_DIR / "entities" / "ranks.yaml").read_text())["ranks"]
_GANGSTERS = yaml.safe_load((_CONFIG_DIR / "entities" / "gangsters.yaml").read_text())["gangsters"]


class _StubRng(StubRng):
    """``pub.recruit`` never calls ``rng.hit()`` — assert loudly if it ever does."""

    def hit(self, a, b):  # pragma: no cover — recruit never calls hit()
        raise AssertionError("pub.recruit should not call rng.hit()")


def _state(
    *,
    rank=5,
    ka=100000,
    ln=1,
    roster=None,
    housed=True,
    hired_gangsters=(),
):
    roster = roster if roster is not None else (Gangster(name="boss"),)
    tenancy = {1: 0} if housed else {}
    player = Player(
        name="p0",
        ka=ka,
        rank=rank,
        roster=roster,
        last_location=ln,
    )
    state = GameState(
        players=(player,),
        clock=Clock(active_player=0, player_count=1),
        config=Config(formula_params={}),
        values={**game.tenancy_values(tenancy), **game.hired_values(hired_gangsters)},
    )
    return state


def _gangster(candidate_id: int) -> Gangster:
    c = _GANGSTERS[candidate_id]
    return Gangster(
        name=c["name"],
        weapon=c["weapon"],
        energie=5,
        kraft=c["kraft"],
        intelligenz=c["intelligenz"],
        brutalitaet=c["brutalitaet"],
    )


# --------------------------------------------------------------------------- #
# Guard matrix — order, denial messages, no RNG spent on a denied guard        #
# --------------------------------------------------------------------------- #
def test_rank_too_low_denies_before_any_rng():
    st = _state(rank=4)
    rng = _StubRng()
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert rng.calls == []


def test_rank_too_low_prints_the_rank_name():
    """``:12101`` ``print"als "ra$(ra(sp))" kannst du noch"`` -- the rank's NAME."""
    st = _state(rank=4)
    src = _scripted()
    run_pure(HANDLERS["pub.recruit"], src, state=st, rng=_StubRng())
    (message,) = src.messages()
    assert message.key == "locations.pub.rank_too_low"
    assert message.params == {"rank": _RANKS[3]}


def test_pub_menu_offers_all_five_options_at_any_rank():
    """The pub always shows its five options (``aw=5``, ``:3030`` prints them all).

    ``:12005`` ``onwgoto12010,12100,12200,12300`` dispatches every one; the recruit
    refusals happen inside ``:12100-12105``, so a low rank or a full gang never hides
    the option or moves the later ones up a place (#122).
    """
    shell = load_location(yaml.safe_load(_PUB_SHELL.read_text(encoding="utf-8")))
    for rank in (1, 4, 5, 10):
        roster = tuple(Gangster(name=f"g{i}") for i in range(10))
        for st in (_state(rank=rank), _state(rank=rank, roster=roster)):
            ids = [o.id for o in available_options(shell, st, ln=1)]
            assert ids == ["drink", "recruit", "tip", "job", "leave"]


def test_rank_exactly_5_passes_the_rank_guard():
    # rank=5 satisfies ra(sp)>4; drive it through to the "nobody available" branch
    # (pool=0 candidates unhired is impossible here, so force offered=0 instead).
    st = _state(rank=5, hired_gangsters=())
    rng = _StubRng(0)  # pool=3 -> range(4) draws 0 -> nobody available
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []


def test_no_housing_denies_after_rank_before_crew_cap():
    st = _state(rank=5, housed=False)
    rng = _StubRng()
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert rng.calls == []  # denied before any offer-pool roll


def test_crew_cap_at_10_denies_before_offer_roll():
    full_roster = tuple(Gangster(name=f"g{i}") for i in range(10))  # boss + 9 hires
    st = _state(rank=5, roster=full_roster)
    rng = _StubRng()
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert rng.calls == []


# --------------------------------------------------------------------------- #
# Offer pool + availability roll                                              #
# --------------------------------------------------------------------------- #
def test_offer_pool_capped_at_3_even_with_all_30_unhired():
    st = _state(rank=5, hired_gangsters=())
    rng = _StubRng(0)  # range(3+1) — pool capped at 3, roll 0 -> nobody
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert rng.calls == [("range", 4)]  # pool+1 == 4, not 31
    assert result.effects == []


def test_offer_pool_shrinks_when_fewer_than_3_unhired():
    # 29 of 30 hired -> only candidate 0 unhired -> pool=1 -> range(2).
    hired = tuple(i for i in range(1, 30))
    st = _state(rank=5, hired_gangsters=hired)
    rng = _StubRng(0)
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert rng.calls == [("range", 2)]
    assert result.effects == []


def test_zero_roll_means_nobody_available():
    st = _state(rank=5)
    rng = _StubRng(0)
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []


def test_tile_3_means_nobody_available_even_with_a_nonzero_roll():
    st = _state(rank=5, ln=3)
    rng = _StubRng(2)  # a nonzero offer count would normally proceed
    result = run_pure(HANDLERS["pub.recruit"], _scripted(), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []


# --------------------------------------------------------------------------- #
# Per-candidate draw: reroll on hired, no duplicate offers within a batch      #
# --------------------------------------------------------------------------- #
def test_candidate_draw_rerolls_a_globally_hired_id():
    st = _state(rank=5, hired_gangsters=(0,))
    rng = _StubRng(
        1,  # offered = 1
        0,  # candidate draw: id 0 -- already hired, reroll
        1,  # candidate draw: id 1 -- ok
    )
    result = run_pure(HANDLERS["pub.recruit"], _scripted(False), state=st, rng=rng)  # decline
    assert result.status == "completed"
    assert rng.calls == [("range", 4), ("range", 30), ("range", 30)]


def test_candidate_draw_rerolls_a_duplicate_within_the_same_batch():
    st = _state(rank=5)
    rng = _StubRng(
        2,  # offered = 2
        5,  # candidate 1: id 5
        5,  # candidate 2 draw: duplicate of 5, reroll
        7,  # candidate 2 draw: id 7, ok
    )
    result = run_pure(HANDLERS["pub.recruit"], _scripted(False, False), state=st, rng=rng)
    assert result.status == "completed"
    assert rng.calls == [
        ("range", 4),
        ("range", 30),
        ("range", 30),
        ("range", 30),
    ]


# --------------------------------------------------------------------------- #
# Hire settlement: money, roster append at energy 5, hired flag persists       #
# --------------------------------------------------------------------------- #
def test_hire_settles_money_roster_and_hired_flag():
    st = _state(rank=5, ka=100000)
    rng = _StubRng(1, 0)  # offered=1, candidate id 0 (killer-jack, price 3000)
    result = run_pure(HANDLERS["pub.recruit"], _scripted(True), state=st, rng=rng)  # accept
    assert result.status == "completed"
    assert result.effects == [
        MoneyChange(-3000),
        RosterAppend(gangster=_gangster(0)),
        GangsterMarkHired(candidate_id=0),
    ]
    assert result.state.players[0].ka == 97000
    assert result.state.players[0].roster == (st.players[0].roster[0], _gangster(0))
    assert result.state.players[0].roster[1].energie == 5
    assert game.hired_ids(result.state) == (0,)


def test_decline_offer_costs_nothing_and_does_not_mark_hired():
    st = _state(rank=5, ka=100000)
    rng = _StubRng(1, 0)
    result = run_pure(HANDLERS["pub.recruit"], _scripted(False), state=st, rng=rng)  # decline
    assert result.status == "completed"
    assert result.effects == []
    assert result.state.players[0].ka == 100000
    assert len(result.state.players[0].roster) == 1
    assert game.hired_ids(result.state) == ()


def test_broke_path_shows_not_enough_money_no_state_change():
    st = _state(rank=5, ka=0)  # candidate 0 costs 3000
    rng = _StubRng(1, 0)
    result = run_pure(HANDLERS["pub.recruit"], _scripted(True), state=st, rng=rng)
    assert result.status == "completed"
    assert result.effects == []
    assert result.state.players[0].ka == 0
    assert len(result.state.players[0].roster) == 1


def test_multi_candidate_batch_hires_both():
    st = _state(rank=5, ka=100000)
    rng = _StubRng(2, 0, 1)  # offered=2, candidate ids 0 then 1
    result = run_pure(HANDLERS["pub.recruit"], _scripted(True, True), state=st, rng=rng)
    assert result.status == "completed"
    assert len(result.state.players[0].roster) == 3  # boss + 2 hires
    assert game.hired_ids(result.state) == (0, 1)
    assert result.state.players[0].ka == 100000 - 3000 - 2000  # candidate 0 + candidate 1 prices


# --------------------------------------------------------------------------- #
# Crew cap during a batch: checked at :12145, after the confirm and cash check #
# --------------------------------------------------------------------------- #
_INTRO = "locations.pub.recruit_intro_male"  # candidates 0, 1, 2 are all male
_OFFER = "locations.pub.recruit_offer"
_HIRED = "locations.pub.recruit_hired"
_FULL = "locations.pub.recruit_gang_full"
_BROKE = "system.not_enough_money"
_FIRST_HIRE = [
    MoneyChange(-3000),
    RosterAppend(gangster=_gangster(0)),
    GangsterMarkHired(candidate_id=0),
]


def _nine_man_roster() -> tuple[Gangster, ...]:
    """Boss + 8 hires: ``gz(sp)=9``, one hire away from the :12105/:12145 cap."""
    return tuple(Gangster(name=f"g{i}") for i in range(9))


def test_ninth_hire_succeeds_tenth_is_offered_then_denied_mid_batch():
    """AE3: the hire that fills the roster does not stop the batch. The next candidate
    is still drawn (:12110), introduced (:12115-12121), offered and confirmed (:12136)
    and cash-checked (:12140); only then does :12145 send the "yes" to the gang-full
    message. Candidate ids 0/1/2 are distinct and unhired, so there are no rerolls."""
    st = _state(rank=5, ka=100000, roster=_nine_man_roster())
    rng = _StubRng(2, 0, 1)  # offered = 2; candidate ids 0 then 1
    source = _scripted(True, True)
    result = run_pure(HANDLERS["pub.recruit"], source, state=st, rng=rng)
    assert result.status == "completed"
    assert rng.calls == [("range", 4), ("range", 30), ("range", 30)]
    assert source.message_keys() == [_INTRO, _OFFER, _HIRED, _INTRO, _OFFER, _FULL]
    assert result.effects == _FIRST_HIRE
    assert len(result.state.players[0].roster) == 10
    assert result.state.players[0].roster[-1] == _gangster(0)


def test_declining_at_the_cap_continues_the_batch():
    """AE4: a "no" at :12136 goes to :12175 ``nexti`` with no message, even with the
    roster full; the third candidate is offered and its "yes" hits :12145."""
    st = _state(rank=5, ka=100000, roster=_nine_man_roster())
    rng = _StubRng(3, 0, 1, 2)  # offered = 3; candidate ids 0, 1, 2
    source = _scripted(True, False, True)
    result = run_pure(HANDLERS["pub.recruit"], source, state=st, rng=rng)
    assert result.status == "completed"
    assert rng.calls == [("range", 4), ("range", 30), ("range", 30), ("range", 30)]
    assert source.message_keys() == [
        *(_INTRO, _OFFER, _HIRED),
        *(_INTRO, _OFFER),  # declined silently
        *(_INTRO, _OFFER, _FULL),
    ]
    assert result.effects == _FIRST_HIRE


def test_declining_the_last_candidate_at_the_cap_shows_no_gang_full():
    """AE4's other half: a "no" never reaches :12145, so no gang-full message."""
    st = _state(rank=5, ka=100000, roster=_nine_man_roster())
    rng = _StubRng(2, 0, 1)
    source = _scripted(True, False)
    result = run_pure(HANDLERS["pub.recruit"], source, state=st, rng=rng)
    assert result.status == "completed"
    assert source.message_keys() == [_INTRO, _OFFER, _HIRED, _INTRO, _OFFER]
    assert result.effects == _FIRST_HIRE


def test_too_little_cash_at_the_cap_shows_cant_afford_not_gang_full():
    """The cash check comes before the cap: a "yes" the player cannot pay for gets
    :12140 ``gosub1125`` ("du hast zu wenig kies!") and ``goto12175``, never :12145."""
    st = _state(rank=5, ka=3000 + 1999, roster=_nine_man_roster())  # cand 1 costs 2000
    rng = _StubRng(2, 0, 1)
    source = _scripted(True, True)
    result = run_pure(HANDLERS["pub.recruit"], source, state=st, rng=rng)
    assert result.status == "completed"
    assert source.message_keys() == [_INTRO, _OFFER, _HIRED, _INTRO, _OFFER, _BROKE]
    assert result.effects == _FIRST_HIRE
    assert result.state.players[0].ka == 1999


def test_crew_cap_reached_before_the_batch_even_starts_still_denies():
    # R7: a fresh call with roster already at 10 is caught by the :12105 entry check
    # before the offer pool is even rolled -- the same message the :12145 -> :12005
    # re-entry reaches mid-batch, so ONE cap value (10) drives both.
    full_roster = tuple(Gangster(name=f"g{i}") for i in range(10))
    st = _state(rank=5, ka=100000, roster=full_roster)
    rng = _StubRng()
    source = _scripted()
    result = run_pure(HANDLERS["pub.recruit"], source, state=st, rng=rng)
    assert source.message_keys() == [_FULL]
    assert result.effects == []
    assert rng.calls == []


# --------------------------------------------------------------------------- #
# Gendered strings: female candidate ids do not affect settlement, only text   #
# --------------------------------------------------------------------------- #
def test_female_candidate_hires_identically_to_male():
    # candidate id 3 == "bloody mary", one of the 4 female ids.
    st = _state(rank=5, ka=100000)
    rng = _StubRng(1, 3)
    result = run_pure(HANDLERS["pub.recruit"], _scripted(True), state=st, rng=rng)
    assert result.state.players[0].roster[1] == _gangster(3)
    assert result.state.players[0].roster[1].name == "bloody mary"


# --------------------------------------------------------------------------- #
# run_pure clean across the whole flow                                        #
# --------------------------------------------------------------------------- #
def test_run_pure_clean_for_recruit_hire_and_decline():
    st = _state(rank=5, ka=100000)
    rng = _StubRng(1, 0)
    result = run_pure(HANDLERS["pub.recruit"], _scripted(True), state=st, rng=rng)
    assert result.status == "completed"

    st2 = _state(rank=5, ka=100000)
    rng2 = _StubRng(1, 0)
    result2 = run_pure(HANDLERS["pub.recruit"], _scripted(False), state=st2, rng=rng2)
    assert result2.status == "completed"


def test_gangsters_yaml_matches_female_ids_and_price_range():
    """Sanity-pins the ported data against the plan's cited facts (not the handler,
    but the config it depends on) -- catches a future accidental edit to the table."""
    females = {i for i, g in enumerate(_GANGSTERS) if g["female"]}
    assert females == {3, 13, 26, 29}
    prices = [g["price"] for g in _GANGSTERS]
    assert min(prices) == 2000
    assert max(prices) == 5500
    assert len(_GANGSTERS) == 30

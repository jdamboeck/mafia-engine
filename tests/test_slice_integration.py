"""U11 — the vertical-slice acceptance test (headless, end-to-end).

This is the slice's Definition-of-Done signal: ONE seeded trajectory that composes
the already-shipped, already-reviewed units into a single real play-through, driving
the whole spine with **no terminal client** in sight.

    new-game setup
      -> walk the real city map into slw (by movement, not teleport)
      -> RENT a room at a positive-rent tile (-100 cash, tenancy + months set)
      -> the negative-rent QUIRK: renting fnm(1) == -50 CREDITS the player (+100)
      -> guard denials reached on the map: rent-occupied + pay-rent-not-resident
      -> the 0-month quiet cancel (zero effects, cash untouched)
      -> walk to the pub -> recruit DENIED at rank 1 (the slice's headline)
      -> determinism: the same seed reproduces the whole final state.

HEADLESSNESS IS THE POINT. This file imports **nothing** from ``clients/`` (U10, the
terminal client, is a renderer over this identical protocol — never a dependency of
playability). The compose contract is exercised directly against the driver:
Responses are fed to ``engine.interactions.run`` and the driver's returned
``EngineResult.state`` is adopted to carry committed effects forward.

The composition it proves (the "how you play a turn" the orchestrator owns):

* Movement (``try_move``) is PURE: it returns an ``EngineResult`` carrying a NEW
  state (the input is never mutated) and, on ``enter``, commits a ``SetEntryContext``
  effect that sets the active player's ``last_location = ln`` (the U7/U9 ln seam) —
  so the slw handler keys ``fnm(ln)`` off the tile actually walked into. Effects
  persist across the trajectory ONLY by adopting ``state = result.state`` after each
  move.
* The driver (``run``) is PURE: on clean completion it returns a NEW ``GameState``
  with the handler's effects committed (an empty buffer applies nothing and returns
  the state unchanged); on a driver-cancel it returns the ORIGINAL, unchanged state. Effects persist
  across the trajectory ONLY by adopting ``state = result.state`` after each driven
  handler.
* The shell (``available_options``) owns guard denial (KTD-8): a denied option is
  EXCLUDED and its handler never runs / commits zero effects; the caller reads the
  excluded option's ``on_denied`` key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.interactions import PromptInt, ShowMessage, run
from engine.locations import available_options, load_location
from engine.movement import DOWN, LEFT, UP, load_city, try_move
from tests.helpers import with_player, with_tenancy

_CONFIG_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
)

# Load the mafia_1920s config BY PATH so its "slw.rent" / "pub.recruit" handlers
# register into engine.locations.HANDLERS (the config is deliberately not a
# pip-installed package — see engine.config_loader).
_CONFIG = load_game_config(_CONFIG_DIR)
new_game = _CONFIG.module.new_game

_CITY_YAML = _CONFIG_DIR / "content" / "map" / "city.yaml"
_SLW_SHELL = _CONFIG_DIR / "content" / "locations" / "slw.yaml"
_PUB_SHELL = _CONFIG_DIR / "content" / "locations" / "pub.yaml"

# A fixed seed for the whole trajectory. Setup rolls give: cash 5500 (in the
# 5000..7000 band), rank 1, ms 25 (on foot), po 18.
SEED = 42


# --------------------------------------------------------------------------- #
# Test doubles: a recording input_source (mirror of tests/test_slw.py's        #
# _scripted, extended to CAPTURE the interactions the driver presents).        #
# --------------------------------------------------------------------------- #
class _Recorder:
    """An input_source recording every interaction the driver presents.

    Since #43 that includes ``ShowMessage``: narration is DELIVERED to the source (so
    a client can render it) but is never asked for an answer, so it consumes no
    scripted response — only real prompts do. That is what lets ``self.seen`` be the
    FULL presented sequence rather than just the prompts.
    """

    def __init__(self, *answers):
        self.seen: list = []
        self._answers = iter(answers)

    def __call__(self, interaction):
        self.seen.append(interaction)
        if isinstance(interaction, ShowMessage):
            return None
        return next(self._answers)

    @property
    def types(self) -> list[type]:
        return [type(i) for i in self.seen]


def _load_city():
    return load_city(yaml.safe_load(_CITY_YAML.read_text(encoding="utf-8")))


def _load_shell(path: Path):
    return load_location(yaml.safe_load(path.read_text(encoding="utf-8")))


def _fresh_state():
    """A fresh single-player game at SEED (alcapone / the outfit)."""
    return new_game(
        seed=SEED,
        end_year=1930,
        score_weight=1.0,
        players=[("alcapone", "the outfit")],
    )


def _opt(location, opt_id):
    """The (possibly-denied) Option object by id from a loaded shell."""
    return next(o for o in location.options if o.id == opt_id)


def _drive_option(location, opt_id, state, ln, recorder):
    """Run the given option's handler through the real driver and return the result.

    The shell's ``Option`` stores the *resolved callable* in ``.handler`` (the loader
    resolves the handler id against ``engine.locations.HANDLERS`` at load time, so the
    id itself is not retained on the Option). We drive that callable through the real
    ``run`` after confirming the shell admitted the option (its guard passed).
    """
    # available_options must have admitted this option (its guard passed).
    admitted = {o.id for o in available_options(location, state, ln)}
    assert opt_id in admitted, f"{opt_id!r} was denied by the shell guard"
    handler = _opt(location, opt_id).handler  # the resolved callable the loader stored
    return run(handler, recorder, state=state, rng=None)


# --------------------------------------------------------------------------- #
# The headline: one seeded trajectory, asserted end to end.                    #
# --------------------------------------------------------------------------- #
def _play_trajectory():
    """Play the whole seeded slice once and RETURN the observations to assert on.

    Returns a dict of every checkpoint value so the determinism test can replay
    this and compare the two runs' final states.
    """
    obs: dict = {}
    city = _load_city()
    slw = _load_shell(_SLW_SHELL)
    pub = _load_shell(_PUB_SHELL)

    # --- setup ------------------------------------------------------------- #
    state = _fresh_state()
    p = state.players[0]
    obs["start_cash"] = p.ka
    obs["start_rank"] = p.rank
    obs["start_ms"] = p.ms
    obs["start_po"] = p.po

    # ================================================================= #
    # A. Walk into slw ln=2 (door 180, POSITIVE rent) and RENT 2 months. #
    #    Real multi-step 156 walk: po=141 --DOWN--> 181 --LEFT--> door 180. #
    # ================================================================= #
    state = with_player(state, 0, po=141)
    r_step = try_move(state, city, DOWN)
    state = r_step.state  # adopt the returned state (movement is pure)
    p = state.players[0]
    assert r_step.payload.kind == "step" and p.po == 181 and p.ms == 24  # ms -= 1
    r_enter = try_move(state, city, LEFT)
    state = r_enter.state
    p = state.players[0]
    assert r_enter.payload.kind == "enter"
    assert r_enter.payload.la == 1 and r_enter.payload.ln == 2
    assert p.po == 181  # po does NOT move onto the door
    assert p.ms == 19  # ms -= 5 on entry
    assert p.last_location == 2  # the ln seam populated by real entry
    obs["po_after_slw_walk"] = p.po
    obs["ms_after_slw_walk"] = p.ms

    ka_before_rent = p.ka
    rent_rec = _Recorder(2)  # rent for 2 months
    result = _drive_option(slw, "rent", state, ln=2, recorder=rent_rec)
    assert result.status == "completed"
    state = result.state  # ADOPT the driver's returned state (fold effects forward)
    p = state.players[0]
    # fnm(2) == base == 50 -> 2 months cost 100; cash drops by exactly 100.
    assert p.ka == ka_before_rent - 100
    assert state.map.tenancy[2] == 0  # tenancy set to sp (active player index 0)
    assert p.rented_months == 2
    # The FULL presented interaction sequence, straight off the recording input_source
    # (#43): quote -> months prompt -> success. Before narration was delivered this
    # had to be observed out-of-band by hand-driving the generator, which proved the
    # handler YIELDED the messages but not that any client could receive them.
    assert rent_rec.types == [ShowMessage, PromptInt, ShowMessage]
    obs["cash_after_positive_rent"] = p.ka
    obs["tenancy_after_rent"] = dict(state.map.tenancy)
    obs["rented_months_after_rent"] = p.rented_months

    # ================================================================= #
    # PREMIUM TILE: renting fnm(1) == 150 costs triple the base rate.     #
    #    Fresh state + ln=1 tile so the charge is clean and isolated.     #
    #                                                                     #
    # #47 audit: this block previously asserted a "negative-rent quirk"    #
    # in which fnm(1) == -50 CREDITED the tenant +100 for 2 months. That   #
    # came from the since-reversed true=+1 pin; :115 under C64 semantics   #
    # is 50 - 100*(-1) = 150, an ordinary premium tier.                    #
    # ================================================================= #
    neg_state = _fresh_state()
    neg_state = with_player(neg_state, 0, last_location=1)  # tile 1 -> fnm(1) == 150
    ka_before_neg = neg_state.players[0].ka
    neg_rec = _Recorder(2)  # rent 2 months at the premium unit
    neg_result = _drive_option(slw, "rent", neg_state, ln=1, recorder=neg_rec)
    assert neg_result.status == "completed"
    neg_state = neg_result.state
    # MoneyChange(-(x*p)) = -(2 * 150) = -300 -> cash DECREASES.
    assert neg_state.players[0].ka == ka_before_neg - 300
    assert neg_state.map.tenancy[1] == 0
    obs["cash_after_premium_rent"] = neg_state.players[0].ka
    obs["premium_rent_delta"] = neg_state.players[0].ka - ka_before_neg

    # ================================================================= #
    # B. Guard denials (zero effects; handler NEVER entered — KTD-8).     #
    #    Read the EXCLUDED option's on_denied key straight off the shell.  #
    # ================================================================= #
    # Rent-occupied: tile 2 taken by a DIFFERENT player -> rent excluded.
    occupied = _fresh_state()
    occupied = with_player(occupied, 0, last_location=2)
    # tile 2 owned by player 1 (not the active 0)
    occupied = with_tenancy(occupied, {2: 1})
    avail_occ = {o.id for o in available_options(slw, occupied, ln=2)}
    assert "rent" not in avail_occ  # guard tenancy==0 fails -> excluded
    assert _opt(slw, "rent").on_denied == "locations.slw.no_room"

    # Pay-rent-not-resident: tile owned by a NON-active player -> pay_rent excluded.
    not_resident = _fresh_state()
    not_resident = with_player(not_resident, 0, last_location=2)
    # someone who isn't the active player
    not_resident = with_tenancy(not_resident, {2: 99})
    avail_nr = {o.id for o in available_options(slw, not_resident, ln=2)}
    assert "pay_rent" not in avail_nr  # guard tenancy==sp fails -> excluded
    assert _opt(slw, "pay_rent").on_denied == "locations.slw.not_resident"

    # ================================================================= #
    # C. 0-month quiet cancel (:10030) — ZERO effects, cash UNCHANGED.    #
    # ================================================================= #
    cancel_state = _fresh_state()
    cancel_state = with_player(cancel_state, 0, last_location=2)  # a FREE tile (guard passes)
    ka_before_cancel = cancel_state.players[0].ka
    cancel_rec = _Recorder(0)  # 0 months -> x<=0 -> quiet return
    cancel_result = _drive_option(slw, "rent", cancel_state, ln=2, recorder=cancel_rec)
    assert cancel_result.status == "completed"  # a quiet return, not a driver-cancel
    assert cancel_result.effects == []  # atomic: nothing applied
    assert cancel_result.state.players[0].ka == ka_before_cancel  # unchanged
    assert 2 not in cancel_result.state.map.tenancy  # no tenancy set

    # ================================================================= #
    # D. Walk to the pub -> recruit DENIED at rank 1 (the HEADLINE).      #
    #    Real walk: po=474 --LEFT--> 473 --UP--> door 433 (la=2, ln=1).   #
    # ================================================================= #
    p = state.players[0]
    state = with_player(state, 0, po=474)
    p = state.players[0]
    ms_before_pub = p.ms
    r_step2 = try_move(state, city, LEFT)
    state = r_step2.state
    p = state.players[0]
    assert r_step2.payload.kind == "step" and p.po == 473
    assert p.ms == ms_before_pub - 1
    r_enter2 = try_move(state, city, UP)
    state = r_enter2.state
    p = state.players[0]
    assert r_enter2.payload.kind == "enter"
    assert r_enter2.payload.la == 2 and r_enter2.payload.ln == 1
    assert p.po == 473  # po unchanged on entry
    obs["po_after_pub_walk"] = p.po
    obs["ms_after_pub_walk"] = p.ms

    avail_pub = {o.id for o in available_options(pub, state, ln=1)}
    assert "recruit" not in avail_pub  # guard rank>4 fails at rank 1 -> EXCLUDED
    assert _opt(pub, "recruit").on_denied == "locations.pub.rank_too_low"
    # No handler ran, so no effects were committed on the denial (nothing to adopt).

    # --- final-state fingerprint (for determinism) ------------------------- #
    p = state.players[0]
    obs["final_cash"] = p.ka
    obs["final_po"] = p.po
    obs["final_ms"] = p.ms
    obs["final_rented_months"] = p.rented_months
    obs["final_tenancy"] = dict(state.map.tenancy)
    obs["final_rank"] = p.rank
    return obs


def test_vertical_slice_end_to_end():
    """The whole seeded trajectory plays through with the asserted checkpoints."""
    obs = _play_trajectory()

    # Setup landed in the expected band, on foot, at rank 1.
    assert 5000 <= obs["start_cash"] <= 7000
    assert obs["start_cash"] == 5500  # exact, seed=42
    assert obs["start_rank"] == 1
    assert obs["start_ms"] == 25
    assert obs["start_po"] == 18

    # Positive-rent tile debited exactly 100.
    assert obs["cash_after_positive_rent"] == obs["start_cash"] - 100
    assert obs["tenancy_after_rent"] == {2: 0}
    assert obs["rented_months_after_rent"] == 2

    # Premium tile (fnm(1)==150) debited exactly 300 for 2 months.
    assert obs["premium_rent_delta"] == -300

    # Walk bookkeeping.
    assert obs["po_after_slw_walk"] == 181 and obs["ms_after_slw_walk"] == 19
    assert obs["po_after_pub_walk"] == 473

    # Final fingerprint reflects only the positive-rent effects on the main line
    # (denials + the cancel committed ZERO effects; the negative-rent run was on a
    # separate isolated state and never touched this trajectory's cash).
    assert obs["final_cash"] == 5400  # 5500 - 100
    assert obs["final_tenancy"] == {2: 0}
    assert obs["final_rented_months"] == 2
    assert obs["final_rank"] == 1


def test_slice_is_deterministic_same_seed():
    """The ENTIRE trajectory is reproducible: same seed -> identical final state."""
    a = _play_trajectory()
    b = _play_trajectory()
    for key in (
        "start_cash",
        "final_cash",
        "final_po",
        "final_ms",
        "final_rented_months",
        "final_tenancy",
        "final_rank",
    ):
        assert a[key] == b[key], f"non-deterministic at {key}: {a[key]!r} != {b[key]!r}"


def test_different_seed_diverges_starting_stats():
    """A light divergence check (full setup determinism was proven in U8): a
    different seed yields different starting stats."""
    s1 = new_game(
        seed=1, end_year=1930, score_weight=1.0, players=[("a", "g")]
    ).players[0]
    s2 = new_game(
        seed=999, end_year=1930, score_weight=1.0, players=[("a", "g")]
    ).players[0]
    g1, g2 = s1.roster[0], s2.roster[0]
    # At least one rolled quantity differs across the two seeds.
    assert (s1.ka, g1.kraft, g1.brutalitaet) != (s2.ka, g2.kraft, g2.brutalitaet)


def test_headless_no_clients_import():
    """Headlessness proof: driving the slice pulls in NOTHING new from ``clients``.

    The real proof is this file's import list (it imports nothing from ``clients``).
    This guards against a transitive engine/config dependency sneaking one in. We
    compare a ``sys.modules`` SNAPSHOT across the trajectory rather than asserting
    ``clients`` is absent process-wide: ``sys.modules`` is process-global, so a
    sibling test file (e.g. ``test_bootstrap`` does a package-existence smoke import
    of ``clients``) can leave a ``clients`` entry behind — the slice must be
    headless, but it can't control what other tests imported first."""
    before = {m for m in sys.modules if m == "clients" or m.startswith("clients.")}
    _play_trajectory()
    after = {m for m in sys.modules if m == "clients" or m.startswith("clients.")}
    new_client_modules = after - before
    assert not new_client_modules, (
        f"the slice imported {sorted(new_client_modules)} from clients — "
        "it must be fully headless"
    )

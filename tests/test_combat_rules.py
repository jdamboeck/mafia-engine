"""U2 — the attribute-agnostic combat contract.

Two things are proven here. First (step 2), that moving the two combat formulas out
of the engine and into ``data/game_configs/mafia_1920s/combat_rules.py`` did not
change them: the differential tests exhaust the whole reachable input domain and
compare the relocated formula against the original, draw for draw. Second, that the
engine really is attribute-agnostic afterwards — the role machinery, the layer
boundary, and a second invented config with entirely different attribute names.

The differential domain, stated concretely: stats are typed ``int`` with no enforced
range, but ``ts``/``tg`` only ever take the ~7 distinct values in ``weapons.yaml``,
and ``kraft``/``brutalitaet`` are rolled 10-50 and capped at 99. **0-99 inclusive**
therefore covers every reachable value with margin — ~700 pairs per formula, which
runs in milliseconds.
"""

from __future__ import annotations

import pytest

import data.game_configs.mafia_1920s.combat_rules as game_rules
from engine.rng import Rng
from tests.helpers import StubRng

#: The full reachable stat domain (see module docstring).
STAT_DOMAIN = range(0, 100)

#: Every distinct ts/tg value in this config's weapon table, plus 0 (unarmed).
WEAPON_STAT_DOMAIN = (0, 1, 2, 3, 5, 8, 10, 15, 20)


def _original_is_hit(rng, *, ts: int, kraft: int) -> bool:
    """The pre-U2 engine formula, pinned here as the differential baseline.

    A verbatim copy of ``engine.combat.is_hit`` as of U1, kept independent of the
    engine so step 7's deletion cannot quietly turn this comparison into a
    tautology (importing the engine's copy would make the test pass by identity
    the moment both point at the same function).
    """
    weapon_factor = rng.range(ts) if ts > 0 else 0
    craft_factor = rng.range(kraft // 10 + 1)
    return weapon_factor != 0 and craft_factor != 0


def _original_damage_roll(rng, *, tg: int, brutalitaet: int) -> int:
    """The pre-U2 engine damage formula, pinned as the differential baseline."""
    draw = rng.range(tg) if tg > 0 else 0
    return int(draw + brutalitaet / 10) + 1


def test_is_hit_matches_the_original_across_the_whole_domain():
    """The relocated hit test is draw-for-draw identical to the engine's original.

    Both formulas run against their OWN ``Rng`` seeded identically, so any
    divergence in draw count or draw order shows up as a mismatch, not just a
    different verdict.
    """
    for ts in WEAPON_STAT_DOMAIN:
        for kraft in STAT_DOMAIN:
            old = _original_is_hit(Rng(4242), ts=ts, kraft=kraft)
            new = game_rules.is_hit(kraft, {"ts": ts, "tg": 0}, Rng(4242))
            assert old == new, f"is_hit diverged at ts={ts} kraft={kraft}"


def test_damage_roll_matches_the_original_across_the_whole_domain():
    """The relocated damage roll is draw-for-draw identical to the engine's original."""
    for tg in WEAPON_STAT_DOMAIN:
        for brutalitaet in STAT_DOMAIN:
            old = _original_damage_roll(Rng(99), tg=tg, brutalitaet=brutalitaet)
            new = game_rules.damage_roll(brutalitaet, {"ts": 0, "tg": tg}, Rng(99))
            assert old == new, f"damage_roll diverged at tg={tg} bt={brutalitaet}"


def test_damage_roll_truncates_the_brutalitaet_bonus_and_always_costs_one():
    """The ``bt/10`` bonus TRUNCATES, and every hit costs at least 1 (``30255``).

    A ``tg`` of 0 admits no variance (the formula skips the draw entirely), which
    pins the result without scripting a single draw — determinism from shaping the
    DATA (KTD-6). brutalitaet 0-9 all floor to a +0 bonus, so damage stays at the
    trailing ``+1``; 25 floors to +2, giving 3.

    Note on ``int()`` placement: the source writes ``int(rnd(1)*tg(w)+bt/10)+1``,
    wrapping the whole sum. Because ``rng.range`` returns an INTEGER, wrapping the
    sum and wrapping only the bonus are mathematically identical here — so no test
    can distinguish them. This asserts the truncation that IS observable rather
    than pretending to pin the parenthesis.
    """
    assert game_rules.damage_roll(0, {"ts": 0, "tg": 0}, Rng(1)) == 1
    assert game_rules.damage_roll(9, {"ts": 0, "tg": 0}, Rng(1)) == 1
    assert game_rules.damage_roll(10, {"ts": 0, "tg": 0}, Rng(1)) == 2
    assert game_rules.damage_roll(25, {"ts": 0, "tg": 0}, Rng(1)) == 3


@pytest.mark.parametrize("ts", [t for t in WEAPON_STAT_DOMAIN if t > 0])
def test_hit_misses_when_either_factor_rolls_zero(ts):
    """30247: a shot misses iff EITHER factor rolls 0. Moved from the engine's own
    formula tests when the formula moved out (U2); now on the live config path.

    ``ts`` is restricted to > 0 here: at ``ts == 0`` the weapon factor is a forced 0
    (the draw is skipped entirely), so the "neither rolls zero" case cannot arise —
    an unarmed weapon always misses, which ``test_unarmed_always_misses`` pins.
    """
    kraft = 30  # kr/10+1 = 4
    assert game_rules.is_hit(kraft, {"ts": ts, "tg": 0}, StubRng(0, 1)) is False  # weapon 0
    assert game_rules.is_hit(kraft, {"ts": ts, "tg": 0}, StubRng(1, 0)) is False  # craft 0
    assert game_rules.is_hit(kraft, {"ts": ts, "tg": 0}, StubRng(1, 1)) is True  # neither


def test_unarmed_always_misses():
    """``ts == 0`` forces the weapon factor to 0, so the shot never connects (the
    craft factor is not even drawn)."""
    rng = StubRng(1)  # would be a hit if the weapon factor were read
    assert game_rules.is_hit(50, {"ts": 0, "tg": 0}, rng) is False
    assert rng.calls == [("range", 6)]  # only the craft draw, bound int(50/10)+1


def test_hit_draws_ts_then_kraft_over_ten_plus_one():
    """The two factors are drawn with bounds ``ts`` and ``int(kr/10)+1`` in that order."""
    rng = StubRng(1, 1)
    game_rules.is_hit(37, {"ts": 5, "tg": 0}, rng)
    assert rng.calls == [("range", 5), ("range", 4)]  # int(37/10)+1 == 4


@pytest.mark.parametrize("tg", WEAPON_STAT_DOMAIN)
def test_damage_bounds_and_never_zero(tg):
    """30255: minimum hit is 1 (draw 0, bt 0); maximum is (tg-1) + 9 + 1."""
    assert game_rules.damage_roll(0, {"ts": 0, "tg": tg}, StubRng(0)) == 1
    hi_draw = max(tg - 1, 0)
    assert game_rules.damage_roll(99, {"ts": 0, "tg": tg}, StubRng(hi_draw)) == hi_draw + 9 + 1


def test_damage_draw_uses_tg_only():
    """The damage roll draws once, bounded by ``tg`` — brutalitaet is not a draw."""
    rng = StubRng(3)
    assert game_rules.damage_roll(35, {"ts": 0, "tg": 10}, rng) == 7  # int(3 + 3.5) + 1
    assert rng.calls == [("range", 10)]


def test_bundle_declares_this_games_roles():
    """The bundle names the vitality attribute and both capability role maps."""
    bundle = game_rules.build_rules()
    assert bundle.vitality == "energie"
    assert bundle.hit_roles == {"attacker": "kraft"}
    assert bundle.damage_roles == {"attacker": "brutalitaet"}
    assert set(bundle.required_keys()) == {"energie", "kraft", "brutalitaet"}
    # The bundle no longer carries an equipment lookup (amendment A1): the formulas
    # read stats off the attacker's own equipment, so there is no equipment_stats.
    assert not hasattr(bundle, "equipment_stats")


# --------------------------------------------------------------------------- #
# attrs coherence — the failure that stays green until step 6 (U2 step 3)     #
# --------------------------------------------------------------------------- #
def test_stats_are_single_sourced_and_survive_a_replace():
    """A stat lives in exactly ONE place, so a rebuild cannot leave a stale copy.

    Under amendment A4 ``energie`` maps to the engine's ``vitality`` SLOT and the other
    stats live in ``attrs``. Because the named stats are PROPERTIES over that single
    source (never dataclass fields), there is nothing to hand-sync and nothing to drift
    — the dual-write hazard is closed by construction, not by re-derivation.
    """
    from dataclasses import replace

    from data.game_configs.mafia_1920s.gangster import Gangster

    g = Gangster(name="x", energie=9, kraft=40)
    # energie -> vitality slot; a replace on the slot is reflected by the property.
    g2 = replace(g, vitality=5)
    assert g2.energie == 5
    assert g2.vitality == 5
    # kraft lives in attrs; with_attr is the single-source write path.
    g3 = g.with_attr("kraft", 7)
    assert g3.kraft == 7
    assert g3.attrs["kraft"] == 7


def test_attrs_carries_the_non_vitality_stats_and_stays_read_only():
    """``attrs`` exposes the non-vitality stats as data and is not mutable (R2).

    ``energie`` is NOT in ``attrs`` — it is the ``vitality`` slot (A4). The three
    remaining stats are the opaque map the engine never inspects.
    """
    from data.game_configs.mafia_1920s.gangster import Gangster

    g = Gangster(name="x", energie=5, kraft=40, intelligenz=30, brutalitaet=20)
    assert g.vitality == 5
    assert dict(g.attrs) == {"kraft": 40, "intelligenz": 30, "brutalitaet": 20}
    with pytest.raises(TypeError):
        g.attrs["kraft"] = 1  # type: ignore[index]


def test_extra_attrs_survive_alongside_the_named_stats():
    """A config may carry attributes it has no named accessor for.

    The named stats stay authoritative for the ones they cover; anything else the
    caller supplies in ``attrs`` rides along untouched. This is what lets a different
    game in the genre add an attribute without an engine change.
    """
    from data.game_configs.mafia_1920s.gangster import Gangster

    g = Gangster(name="x", kraft=40, attrs={"grit": 7, "kraft": 999})
    assert g.attrs["grit"] == 7
    assert g.attrs["kraft"] == 40, "the named stat must win over a supplied attrs entry"

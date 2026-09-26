"""U2 — the attribute-agnostic combat contract.

Two things are proven here. First, that the two combat formulas in
``data/game_configs/mafia_1920s/combat_rules.py`` give the source's exact
probabilities: the distribution tests exhaust the whole reachable input domain,
enumerate every value of each bounded draw, and compare the resulting distribution
with the one ``mf-prg.bas:30247``/``30255`` give a continuous ``rnd(1)``.
(``tests/test_ports.py`` compares the same two ports with the quoted BASIC text, point
by point.) Second, that the engine really is attribute-agnostic — the role machinery,
the layer boundary, and a second invented config with entirely different attribute
names.

The domain, stated concretely: stats are typed ``int`` with no enforced range, but
``ts``/``tg`` only ever take the ~7 distinct values in ``weapons.yaml``, and
``kraft``/``brutalitaet`` are rolled 10-50 and capped at 99. **0-99 inclusive**
therefore covers every reachable value with margin, and runs in well under a second.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

import data.game_configs.mafia_1920s.combat_rules as game_rules
from engine.rng import Rng
from tests.helpers import StubRng

#: The full reachable stat domain (see module docstring).
STAT_DOMAIN = range(0, 100)

#: Every distinct ts/tg value in this config's weapon table, plus 0 (unarmed).
WEAPON_STAT_DOMAIN = (0, 1, 2, 3, 5, 8, 10, 15, 20)


def _enumerate(formula, bound):
    """Run ``formula`` once per possible value of its bounded draw (``0..bound-1``).

    Each value is equally likely under ``rng.range(bound)``, so the list of results
    IS the port's exact distribution.
    """
    return [formula(k) for k in range(bound)]


def test_is_hit_miss_probability_matches_30247_across_the_whole_domain():
    """The kraft factor misses with exactly the source's probability, for every kraft.

    ``:30247``'s factor ``int(rnd(1)*(kr/10+1))`` is 0 iff ``rnd(1) < 1/(kr/10+1)``,
    so the source misses on it with p = ``1/(kr/10+1)`` = ``10/(kr+10)``. The port
    draws ``rng.range(kraft+10)``; enumerating every draw value (weapon factor held
    non-zero) must give that same fraction of misses, for the whole stat domain.
    """
    for kraft in STAT_DOMAIN:
        bound = kraft + 10
        hits = _enumerate(
            lambda k, kraft=kraft: game_rules.is_hit(kraft, {"ts": 2, "tg": 0}, StubRng(1, k)),
            bound,
        )
        port_p_miss = Fraction(hits.count(False), bound)
        basic_p_miss = 1 / (Fraction(kraft, 10) + 1)
        assert port_p_miss == basic_p_miss, f"is_hit miss probability diverged at kraft={kraft}"


def test_damage_roll_distribution_matches_30255_across_the_whole_domain():
    """Every damage value comes up with exactly the source's probability.

    ``:30255`` is ``y=int(rnd(1)*tg(w)+bt/10)+1`` with ``rnd(1)`` uniform on [0, 1), so
    ``y=d`` iff ``rnd(1)*tg`` lies in ``[d-1-bt/10, d-bt/10)``: its probability is that
    interval's overlap with ``[0, tg)``, divided by ``tg``. Enumerating every value of
    the port's ``rng.range(10*tg)`` draw must give the same distribution, for every
    weapon and the whole stat domain.
    """
    for tg in WEAPON_STAT_DOMAIN:
        if tg == 0:
            continue  # no draw: pinned by test_damage_roll_tg_zero_is_the_bonus_plus_one
        for bt in STAT_DOMAIN:
            bound = 10 * tg
            rolls = _enumerate(
                lambda k, tg=tg, bt=bt: game_rules.damage_roll(bt, {"ts": 0, "tg": tg}, StubRng(k)),
                bound,
            )
            offset = Fraction(bt, 10)
            for d in range(1, tg + 12):
                lo = max(Fraction(d - 1) - offset, Fraction(0))
                hi = min(Fraction(d) - offset, Fraction(tg))
                basic_p = max(hi - lo, Fraction(0)) / tg
                port_p = Fraction(rolls.count(d), bound)
                assert port_p == basic_p, f"damage_roll diverged at tg={tg} bt={bt} y={d}"
            assert all(1 <= y <= tg + 11 for y in rolls)


def test_damage_roll_tg_zero_is_the_bonus_plus_one():
    """With ``tg`` 0 the random term is 0, so damage is ``int(bt/10)+1`` (``30255``).

    ``tg`` 0 admits no variance (the formula skips the draw entirely), which pins the
    result without scripting a single draw — determinism from shaping the DATA
    (KTD-6). brutalitaet 0-9 give a +0 bonus, so damage stays at the trailing ``+1``;
    25 gives +2, so 3.
    """
    assert game_rules.damage_roll(0, {"ts": 0, "tg": 0}, Rng(1)) == 1
    assert game_rules.damage_roll(9, {"ts": 0, "tg": 0}, Rng(1)) == 1
    assert game_rules.damage_roll(10, {"ts": 0, "tg": 0}, Rng(1)) == 2
    assert game_rules.damage_roll(25, {"ts": 0, "tg": 0}, Rng(1)) == 3


@pytest.mark.parametrize("ts", [t for t in WEAPON_STAT_DOMAIN if t > 0])
def test_hit_misses_when_either_factor_rolls_zero(ts):
    """30247: a shot misses iff EITHER factor rolls 0. Moved from the engine's own
    formula tests when the formula moved out (U2); now on the live config path.

    The kraft factor ``int(rnd(1)*(kr/10+1))`` is 0 exactly when the port's
    ``rng.range(kr+10)`` draw is below 10, so 9 is its highest miss and 10 its lowest
    pass. ``ts`` is restricted to > 0 here: at ``ts == 0`` the weapon factor is a
    forced 0 (the draw is skipped entirely), so the "neither rolls zero" case cannot
    arise — an unarmed weapon always misses, which ``test_unarmed_always_misses`` pins.
    """
    kraft = 30  # range(40); kr/10+1 = 4
    assert game_rules.is_hit(kraft, {"ts": ts, "tg": 0}, StubRng(0, 10)) is False  # weapon 0
    assert game_rules.is_hit(kraft, {"ts": ts, "tg": 0}, StubRng(1, 9)) is False  # kraft 0
    assert game_rules.is_hit(kraft, {"ts": ts, "tg": 0}, StubRng(1, 10)) is True  # neither


def test_unarmed_always_misses():
    """``ts == 0`` forces the weapon factor to 0, so the shot never connects (the
    weapon factor is not even drawn)."""
    rng = StubRng(10)  # would be a hit if the weapon factor were read
    assert game_rules.is_hit(50, {"ts": 0, "tg": 0}, rng) is False
    assert rng.calls == [("range", 60)]  # only the kraft draw, bound 50+10


def test_hit_draws_ts_then_kraft_plus_ten():
    """The two factors are drawn with bounds ``ts`` and ``kr+10`` in that order."""
    rng = StubRng(1, 10)
    game_rules.is_hit(37, {"ts": 5, "tg": 0}, rng)
    assert rng.calls == [("range", 5), ("range", 47)]  # 37+10


@pytest.mark.parametrize("tg", WEAPON_STAT_DOMAIN)
def test_damage_bounds_and_never_zero(tg):
    """30255: minimum hit is 1 (draw 0, bt 0); the maximum, at bt 99 and the top draw,
    is ``tg+10``: ``rnd(1)*tg`` just under ``tg`` plus 9.9 truncates to ``tg+9``."""
    assert game_rules.damage_roll(0, {"ts": 0, "tg": tg}, StubRng(0)) == 1
    hi_draw = max(10 * tg - 1, 0)
    expected = tg + 10 if tg > 0 else 10
    assert game_rules.damage_roll(99, {"ts": 0, "tg": tg}, StubRng(hi_draw)) == expected


def test_damage_draw_uses_tg_only():
    """The damage roll draws once, bounded by ``10*tg`` — brutalitaet is not a draw.

    Draw 30 of 100 stands for ``rnd(1)=0.3``: ``int(0.3*10 + 3.5)+1`` is 7. Draw 35
    (``rnd(1)=0.35``) shows the carry: ``int(3.5 + 3.5)+1`` is 8.
    """
    rng = StubRng(30)
    assert game_rules.damage_roll(35, {"ts": 0, "tg": 10}, rng) == 7
    assert rng.calls == [("range", 100)]
    assert game_rules.damage_roll(35, {"ts": 0, "tg": 10}, StubRng(35)) == 8


@pytest.mark.parametrize(("attr", "ts", "tg"), [(37, 5, 10), (50, 0, 0), (0, 15, 3), (99, 1, 0)])
def test_declared_draws_are_the_draws_the_formulas_make(attr, ts, tg):
    """``hit_draws``/``damage_draws`` (what a debug viewer names draws by) list exactly
    the bounds :func:`is_hit`/:func:`damage_roll` draw, in order; a 0 bound is not
    drawn. The bundle carries both, so a viewer reads them off the fight's own rules."""
    equipment = {"ts": ts, "tg": tg}
    rng = StubRng(1, 10)
    game_rules.is_hit(attr, equipment, rng)
    declared = [b for _, b in game_rules.hit_draws(attr, equipment) if b > 0]
    assert rng.calls == [("range", b) for b in declared]

    rng = StubRng(0)
    game_rules.damage_roll(attr, equipment, rng)
    declared = [b for _, b in game_rules.damage_draws(attr, equipment) if b > 0]
    assert rng.calls == [("range", b) for b in declared]

    bundle = game_rules.build_rules()
    assert bundle.hit_draws is game_rules.hit_draws
    assert bundle.damage_draws is game_rules.damage_draws


def test_bundle_declares_this_games_roles():
    """The bundle names both capability role maps — and NOT vitality (amendment A5)."""
    bundle = game_rules.build_rules()
    assert bundle.hit_roles == {"attacker": "kraft"}
    assert bundle.damage_roles == {"attacker": "brutalitaet"}
    # A5: the depleting resource is the engine's ``vitality`` SLOT, read directly — the
    # bundle no longer carries a ``vitality`` attrs-key name, and required_keys() lists
    # only the attrs the formulas read (never the slot).
    assert not hasattr(bundle, "vitality")
    assert set(bundle.required_keys()) == {"kraft", "brutalitaet"}
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

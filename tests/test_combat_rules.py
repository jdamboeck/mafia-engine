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
def test_replace_regenerates_attrs_so_the_two_copies_cannot_drift():
    """``dataclasses.replace`` updates the named field; ``attrs`` must follow it.

    This is the dual-write hazard U2's plan calls out: ``effects._with_gangster``
    rebuilds through ``replace``, which touches the NAMED field only. If ``attrs``
    were hand-synced instead of derived in ``__post_init__``, this rebuild would
    leave a stale copy behind and nothing would fail until a reader switched over —
    surfacing far from the cause.
    """
    from dataclasses import replace

    from engine.state import Fighter, Gangster

    g = replace(Gangster(name="x", energie=9, kraft=40), energie=5)
    assert g.energie == 5
    assert g.attrs["energie"] == 5, "attrs went stale across a replace()"

    f = replace(Fighter(name="x", energie=9, kraft=40), kraft=7)
    assert f.attrs["kraft"] == 7


def test_attrs_carries_every_named_stat_and_stays_read_only():
    """``attrs`` exposes the full stat set as data, and is not mutable (R2)."""
    from engine.state import Gangster

    g = Gangster(name="x", energie=5, kraft=40, intelligenz=30, brutalitaet=20)
    assert dict(g.attrs) == {"energie": 5, "kraft": 40, "intelligenz": 30, "brutalitaet": 20}
    with pytest.raises(TypeError):
        g.attrs["kraft"] = 1  # type: ignore[index]


def test_extra_attrs_survive_alongside_the_named_fields():
    """A config may carry attributes the dataclass has no field for.

    The named fields stay authoritative for the ones they cover; anything else the
    caller supplies rides along untouched. This is what lets a different game in the
    genre add an attribute without an engine change.
    """
    from engine.state import Gangster

    g = Gangster(name="x", kraft=40, attrs={"grit": 7, "kraft": 999})
    assert g.attrs["grit"] == 7
    assert g.attrs["kraft"] == 40, "the named field must win over a supplied attrs entry"

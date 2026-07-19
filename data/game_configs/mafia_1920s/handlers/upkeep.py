"""The turn-start upkeep generator — ports ``mf-prg.bas:4000-4090`` (U3).

Registered under :data:`engine.upkeep.UPKEEP_HANDLER_KEY` in the SAME
:data:`engine.locations.HANDLERS` registry a location option's ``handler`` string
resolves against (KTD-3) — :func:`engine.upkeep.run_upkeep` is the engine-level runner
that looks this generator up and drives it at every player's turn start, before the
free turn (or, from U10, a job shift).

This unit lands the flow's HEAD per the order fixed by KTD-3
(``banner -> regen -> rank -> debt -> shop income -> arms deal -> job-shift/free-turn``):

* **banner** (``4005-4006``) — announce the active player.
* **per-gangster energy regen** (``4015-4025``) — ``en += int(kraft/10)+1``, capped at
  ``2+int(kraft/4)+int(brutalitaet/4)``, once per gangster in ``gz(sp)`` order (the
  boss included — ``roster[0]`` IS gangster 1 of ``gz``, KTD-6).
* **rank promotion commit** (``4030``) — ``ra(sp)=nr(sp)`` iff they differ, with the
  wanted-poster promotion screen (``4200-4220``).

Three slots are declared but LEFT AS NO-OPS this unit — later units activate them in
place, in this exact position in the flow, without reordering anything already here:

* **debt check** (``4040``, U12) — the grace-counter tick / collectors fight.
* **shop income** (``4041``, U11) — the passive kdh-shop payout roll.
* **arms deal** (``4060``, U8) — the staked heist-tip resolution.

The job-shift seam (``employed -> shift flow instead of the free turn``, mirroring the
source's ``1012`` dispatch) is **out of this generator entirely**: upkeep only prepares
the player for their turn, it does not decide what KIND of turn follows. That dispatch
belongs to the caller (the client's turn loop today; U10 gives it a real shift-flow
branch) — this generator's ``return []`` handing control back is exactly the hand-off
point.

Deferred lines NOT ported here (Scope Boundaries): ``4045-4046`` (rent countdown/
eviction — an out-of-scope system this slice), ``4050`` (bribe-protection aging),
``4055-4056`` (fake-papers/counterfeit decay) — none are triggered by anything in-slice.

KTD-7 conformance: touches only ``ctx.state`` (read-only), ``yield <Interaction>``,
``ctx.apply(<Effect>)``, and this config's own ``..setup``/entity-loader helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import EnergyChange, RankCommit
from engine.interactions import ShowMessage
from engine.locations import register
from engine.upkeep import UPKEEP_HANDLER_KEY

__all__ = ["upkeep_turn_start"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]


def _rank_names() -> list[str]:
    """This config's rank-name table (``ra$``), 0-based (index i == in-game rank i+1).

    Goes through the config's own ``load_ranks`` loader — which validates every entry
    against ``engine.types.validate_rank`` — rather than reading the YAML directly, so
    the handler and the client's promotion screen share one validated path. Matches
    ``waf.py``'s ``_weapons()`` pattern (KTD-7: a handler reads its OWN config's entity
    data, never the engine's); the config is frozen per game, so a fresh read per call
    is harmless.
    """
    from ..setup import load_ranks

    return load_ranks(_CONFIG_DIR / "entities" / "ranks.yaml")


@register(UPKEEP_HANDLER_KEY)
def upkeep_turn_start(ctx):
    """Run the active player's turn-start upkeep — ports ``mf-prg.bas:4000-4090``.

    Offers NO cancel path (every yielded interaction is a ``ShowMessage``, which the
    driver auto-acks without consulting the input source at all) — no player input can
    discard this flow, matching the Verification Contract.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]

    # --- 4005-4006: turn banner --------------------------------------------
    yield ShowMessage("upkeep.turn_banner", {"name": active.name})

    # --- 4010-4025: per-gangster energy regen (boss included, gz(sp) order) -
    for g_idx, gangster in enumerate(active.roster):
        cap = 2 + gangster.kraft // 4 + gangster.brutalitaet // 4  # :4020
        gain = gangster.kraft // 10 + 1  # :4015
        ctx.apply(EnergyChange(amount=gain, cap=cap, gangster=g_idx))

    # --- 4030: rank promotion commit + wanted-poster screen (4200-4220) ----
    # nr is the PENDING rank ScoreAndRank already recomputes from gf on every score
    # award; rank ("ra") is what guards/prices actually read (waf.py's `active.rank`)
    # and only moves here. Compare against the state READ AT THIS HANDLER'S START —
    # nothing above this point can have changed nr, so this is exactly :4030's read.
    if active.rank != active.nr:
        ranks = _rank_names()
        yield ShowMessage(
            "upkeep.rank_promotion",
            {
                "gang_name": active.gang_name,
                "name": active.name,
                "score": active.gf,
                "rank_name": ranks[active.nr - 1],
            },
        )
        ctx.apply(RankCommit(new_rank=active.nr))

    # --- 4040: debt check — SLOT, no-op this unit (U12 activates in place) -
    # --- 4041: shop income — SLOT, no-op this unit (U11 activates in place) -
    # --- 4060: arms deal — SLOT, no-op this unit (U8 activates in place) ---

    return []

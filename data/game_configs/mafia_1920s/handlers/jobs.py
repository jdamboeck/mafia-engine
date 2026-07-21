"""The job-shift flow — U10, ports ``mf-prg.bas:25000-25560``.

This is the flow that REPLACES an employed player's free turn (KTD-5, the U3 seam):
once ``pub.job`` (``handlers/pub.py``) accepts a job, the CALLER (the client's turn
loop, ``clients/terminal/__main__.py``'s ``play()``) dispatches this generator instead
of offering the map/menu, right after upkeep runs. This module owns no dispatch
decision itself — it is a plain registered handler, driven exactly like any location
option via :func:`engine.interactions.run`.

Ports, dispatching on the accepted job's ``type`` (``jo(sp)``, mf-prg.bas:25010's
``onjo(sp)goto25015,25100,25015,25200`` — note types 1 AND 3 share ONE flow):

- **bouncer (1) / doorman (3)** (``25015-25045``) — one shared flow: 50% quiet day,
  else a single fight against one of three scripted troublemakers (uniform pick).
- **croupier (2)** (``25100-25140``) — pick a cheat trick 1-3; caught with probability
  ``1/(6-trick)``, paying an immediate bonus on success or triggering a fight (one
  scripted gambler) when caught.
- **killer (4)** (``25200-25210``) — always fights the photographed victim.

Outcome resolution (``25500-25560``), common to all four types:

- **lost the fight** -> the job ends UNPAID, score -2 (``:25510``, ``x=-2``), job
  cleared.
- **quiet day / cheat success / won the fight** -> the shift counts as successful:
  decrement ``months_left`` (``jd(sp)``, ``:25550``); at 0 the FULL accumulated wage
  (``jl(sp)``, the ``pending_pay`` rolled at accept time) pays out once, plus a
  completion score bonus (``:25560``: ``x=3+3*(jo(sp)=2)`` -- 3 for every type except
  croupier, which the plan's Open Questions resolves to 6 per the research
  interpretation, not the raw C64 relational value of 0 -- see ``_completion_score``).

Two known engine gaps this unit closes (see this module's own handler docstrings and
the U10 packet):

- **#44** — ``engine.interactions._run_combat`` now buffers ``EnergyChange`` effects
  for the roster side before returning the winner, so this handler's fights actually
  persist post-fight energy (this module does not need to do anything extra for that
  — it is a driver-level fix, exercised transitively by every ``StartCombat`` yield
  here).
- **Outcome narration** — ``_run_combat`` yields no final screen (KTD-1: narrating the
  outcome is the invoking handler's job). This module shows the winner banner + losses
  block itself, right after each ``StartCombat`` resolves, via the SAME theme keys
  U7 exported for exactly this (``combat.winner_banner``/``losses_heading``/
  ``losses_line``).

KTD-7 conformance: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield
<Interaction>``, ``ctx.apply(<Effect>)``, and this config's OWN ``..setup`` helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import JobClear, JobSet, MoneyChange
from engine.interactions import PromptInt, ShowMessage, StartCombat
from engine.locations import register

from ..combat_rules import build_rules, enemy_attrs, equipper
from ..setup import (
    load_combat_backdrop,
    narrate_combat_outcome,
    score_and_rank,
    weapon_stats_by_id,
)

# Job type ids -- shared with pub.py's take-job handler (mf-prg.bas:12305's ON-GOTO
# dispatch order). Imported (not re-declared as separate literals) so the two
# modules cannot drift apart on what each job type id means.
from .pub import JOB_BOUNCER, JOB_CROUPIER, JOB_DOORMAN, JOB_KILLER

__all__ = ["job_shift", "JOB_SHIFT_HANDLER_KEY"]

#: The registry key this shift generator is registered under -- looked up by the
#: caller (the client's turn loop) exactly like ``engine.upkeep.UPKEEP_HANDLER_KEY``,
#: reusing the SAME ``engine.locations.HANDLERS`` registry (KTD-3's "one registry"
#: convention; this is not a location option, but it is still just another handler id).
JOB_SHIFT_HANDLER_KEY = "job.shift"

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The three scripted bouncer/doorman troublemakers (mf-prg.bas:25035-25042): a
#: uniform 1-of-3 pick, weapon id + energy per variant. Weapon ids per
#: ``entities/weapons.yaml``: 0=haende (fists), 1=messer (knife), 3=schlagkette.
_BOUNCER_BRAWLERS = [
    {"name": "wurstfinger-fred", "weapon": 0, "energie": 30},  # :25040
    {"name": "affenface-alf", "weapon": 1, "energie": 20},  # :25041
    {"name": "der schlachter", "weapon": 3, "energie": 20},  # :25042
]

#: The croupier's caught-cheating opponent (mf-prg.bas:25135): one gambler, energy 10,
#: weapon 1 (messer).
_CROUPIER_OPPONENT = {"name": "spieler", "weapon": 1, "energie": 10}

#: The killer job's victim (mf-prg.bas:25210): one target, energy 20, weapon 0 (fists
#: -- the victim is unarmed, per the source's `w=0`).
_KILLER_VICTIM = {"name": "opfer", "weapon": 0, "energie": 20}


def _weapon_stats() -> dict:
    """This config's weapon id -> ``(ts, tg, range)`` table, for ``StartCombat.weapon_stats``.

    Fresh per call (KTD-7: the config is frozen per game, so re-reading is harmless),
    mirroring ``waf.py``'s ``_weapons()``/``pub.py``'s ``_vehicles()`` pattern.
    """
    return weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")


def _backdrop(name: str) -> tuple[int, ...]:
    """Load one of the three in-slice combat backdrops by name (``ks``/``kp``/``km``)."""
    return load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{name}.yaml")


def _completion_score(job_type: int) -> float:
    """The job-completion score bonus -- ports ``mf-prg.bas:25560``: ``x=3+3*(jo(sp)=2)``.

    Every job type scores 3, EXCEPT the croupier job (type 2), which scores **0**:
    the relational ``(jo(sp)=2)`` is -1 in C64 BASIC, giving ``3+3*(-1)=0``.

    Corrected by the #47 fidelity audit. This previously returned 6, following the
    research gloss and the since-reversed ``true=+1`` pin. 0 is also the reading that
    makes design sense: the croupier is the one job that already paid an immediate
    per-shift bonus (``:25125-25126``), so it earns no completion award on top.
    """
    return 0.0 if job_type == JOB_CROUPIER else 3.0


def _fight(ctx, *, opponent: dict, backdrop: str):
    """Run one shift fight against a single scripted NPC; return the winning side.

    Builds ``StartCombat`` from the active player's CURRENT roster (read fresh off
    ``ctx.state`` -- no earlier effect in a shift run touches the roster) and the
    given opponent spec, ``enemy_count=1`` (every shift fight is one-on-one per the
    source's ``gz(0)=1``). Side 1 is always the acting player (KTD-1/#44 convention),
    so a loss/win here also rides the fight's roster energy deltas into ``ctx`` via
    the driver's ``_run_combat`` (#44 -- no extra code needed here for that).
    """
    from engine.combat import setup_combat

    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    combat_state = setup_combat(
        active.roster,
        enemy_count=1,
        enemy_weapon=opponent["weapon"],
        # This game names the vitality slot "energie" (A5); the config maps it here.
        enemy_vitality=opponent["energie"],
        # The fixed CPU-enemy stats (mf-prg.bas:30245) are config data now (A5).
        enemy_attrs=enemy_attrs(ctx.state.config.formula_params),
        enemy_name=opponent["name"],
        grid=_backdrop(backdrop),
        equip=equipper(_weapon_stats()),
    )
    result = yield StartCombat(
        sides=combat_state.sides,
        grid=combat_state.grid,
        rules=build_rules(),
        dir_memory=combat_state.dir_memory,
    )
    # Outcome narration (KTD-1: the invoking handler's job -- _run_combat yields no
    # final screen). Shared with kdh.py/upkeep.py's own fights (narrate_combat_outcome
    # -- ShowMessage's (key, params) shape means the generic client renderer resolves
    # these with no special-case wiring, exactly like every other ported string). The
    # per-side death tallies come off the CombatResult (U3), so the count is real even
    # though every shift fight happens to be 1v1 (enemy_count=1, gz(0)=1).
    yield from narrate_combat_outcome(
        winner=result.winner,
        player_name=active.name,
        enemy_name=opponent["name"],
        player_losses=result.losses[0],
        enemy_losses=result.losses[1],
    )
    return result.winner


@register(JOB_SHIFT_HANDLER_KEY)
def job_shift(ctx):
    """Run one shift for the active player's accepted job -- ports ``25000-25560``.

    Dispatches on ``active.jobs.type`` (``jo(sp)``); the CALLER is responsible for
    only invoking this when a job is actually held (mirrors ``run_upkeep``'s "the
    caller decides whether to call this" shape, except HERE the caller's decision --
    employed vs. free turn -- is exactly the U3 seam this unit fills in).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    job_type = active.jobs.type

    won = True  # quiet day / successful cheat default to "no fight, shift succeeds"

    if job_type in (JOB_BOUNCER, JOB_DOORMAN):
        # :25015-25045 -- shared bouncer/doorman flow. Source-confirmed quirk: the
        # ON-GOTO at :25010 sends BOTH job types to the SAME line 25015, which prints
        # the "rausschmeisser" (bouncer) header/narration even for an employed
        # DOORMAN -- there is no separate doorman-specific text block in the source,
        # so reusing the bouncer strings for both is faithful, not a shortcut.
        yield ShowMessage("job.shift_bouncer_wait")
        if ctx.rng.range(2) == 0:
            # :25025 -- 50% quiet day.
            yield ShowMessage("job.shift_bouncer_quiet")
        else:
            yield ShowMessage("job.shift_bouncer_trouble")
            brawler = _BOUNCER_BRAWLERS[ctx.rng.range(3)]
            winner = yield from _fight(ctx, opponent=brawler, backdrop="kp")
            won = winner == 1

    elif job_type == JOB_CROUPIER:
        # :25100-25140 -- pick a trick, catch check, bonus or fight.
        yield ShowMessage("job.shift_croupier_intro")
        trick = yield PromptInt("job.shift_croupier_pick", min=1, max=3)
        if ctx.rng.range(6 - trick) == 0:
            # :25130 -- caught; a fight starts.
            yield ShowMessage("job.shift_croupier_caught")
            winner = yield from _fight(ctx, opponent=_CROUPIER_OPPONENT, backdrop="kp")
            won = winner == 1
        else:
            # :25125-25126 -- success; immediate bonus, no fight. p=int(rnd(1)*100*x)+300
            # draws 0..100x-1 THEN adds 300, so the inclusive range tops out at
            # 300+100x-1, not 300+100x (an off-by-one the `hit(a, b)` inclusive-bounds
            # helper would otherwise bake in if b were passed as 300+100*trick).
            bonus = ctx.rng.hit(300, 300 + 100 * trick - 1)
            ctx.apply(MoneyChange(bonus))
            yield ShowMessage("job.shift_croupier_bonus", {"amount": bonus})

    elif job_type == JOB_KILLER:
        # :25200-25210 -- always fight the victim.
        yield ShowMessage("job.shift_killer_intro")
        winner = yield from _fight(ctx, opponent=_KILLER_VICTIM, backdrop="km")
        won = winner == 1

    # :25500-25560 -- common outcome resolution.
    if not won:
        # :25505-25510 -- lost the fight: job ends unpaid, score -2.
        params = ctx.state.config.formula_params
        ctx.apply(score_and_rank(-2, params))
        ctx.apply(JobClear())
        yield ShowMessage("job.shift_failed")
        return []

    # :25550 -- successful shift: decrement months_left. JobSet (not a bespoke
    # decrement effect) re-stores the UNCHANGED type/pay alongside the new
    # months_left -- the source only ever writes jd(sp) as part of the same array
    # slot the accept step used, so replaying this effect reproduces the exact same
    # job shape with one field ticked down.
    months_left = active.jobs.months_left - 1
    if months_left != 0:
        ctx.apply(
            JobSet(
                type=active.jobs.type,
                pending_pay=active.jobs.pending_pay,
                months_left=months_left,
            )
        )
        return []

    # :25555-25560 -- contract finished: pay the full wage, award completion score.
    params = ctx.state.config.formula_params
    ctx.apply(MoneyChange(active.jobs.pending_pay))
    ctx.apply(score_and_rank(_completion_score(job_type), params))
    ctx.apply(JobClear())
    yield ShowMessage("job.shift_completed", {"pay": active.jobs.pending_pay})
    return []

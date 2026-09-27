"""The job-shift flow — ports ``mf-prg.bas:25000-25560``.

This is the flow that REPLACES an employed player's free turn (the job-shift seam):
once ``pub.job`` (``handlers/pub.py``) accepts a job, the CALLER (the client's turn
loop, ``clients/terminal/session.py``'s ``play()``) dispatches this generator instead
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
  croupier, which scores 0 because the C64 relational ``(jo(sp)=2)`` is -1 -- see
  ``_completion_score``).

Combat notes:

- **Post-fight energy** — ``engine.fight_loop._run_combat`` buffers ``EnergyChange``
  effects for the roster side before returning the winner, so this handler's fights
  persist post-fight energy with no extra code here (it is driver-level, exercised
  transitively by every ``StartCombat`` yield).
- **Outcome narration** — ``_run_combat`` yields no final screen (narrating the
  outcome is the invoking handler's job). This module shows the winner banner + losses
  block itself, right after each ``StartCombat`` resolves, via the shared combat theme
  keys (``combat.winner_banner``/``losses_heading``/``losses_line``).

Handler-API conformance: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield
<Interaction>``, ``ctx.apply(<Effect>)``, and this config's OWN ``..setup`` helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import MoneyChange
from ..effects import JobClear, JobSet
from engine.interactions import PromptInt, ShowMessage, StartCombat
from engine.locations import register
from engine.scenario import Scenario

from ..combat_rules import build_rules, enemy_attrs, equipper
from ..setup import (
    load_combat_backdrop,
    load_encounter,
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
#: reusing the SAME ``engine.locations.HANDLERS`` registry (the "one registry"
#: convention; this is not a location option, but it is still just another handler id).
JOB_SHIFT_HANDLER_KEY = "job.shift"

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The three shift fights, declared as data. Setup lives in the encounter
#: files; the SELECTION of the bouncer's variant stays in Python (its
#: ``ctx.rng.range(3)`` roll, mf-prg.bas:25035), and each fight's PAYOUT stays with
#: the job (not a declarable consequence), so these encounters carry no
#: ``on_win``/``on_loss``. Loaded once at import (the config is frozen per game).
_ENCOUNTERS_DIR = _CONFIG_DIR / "content" / "encounters"
_BOUNCER_ENCOUNTER = load_encounter(_ENCOUNTERS_DIR / "job_bouncer.yaml")
_CROUPIER_ENCOUNTER = load_encounter(_ENCOUNTERS_DIR / "job_croupier.yaml")
_KILLER_ENCOUNTER = load_encounter(_ENCOUNTERS_DIR / "job_killer.yaml")


def _weapon_stats() -> dict:
    """This config's weapon id -> ``(ts, tg, range)`` table, for ``StartCombat.weapon_stats``.

    Fresh per call (the config is frozen per game, so re-reading is harmless),
    mirroring ``waf.py``'s ``_weapons()``/``pub.py``'s ``_vehicles()`` pattern.
    """
    return weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")


def _backdrop(name: str) -> tuple[int, ...]:
    """Load one of the three combat backdrops by name (``ks``/``kp``/``km``)."""
    return load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{name}.yaml")


def _completion_score(job_type: int) -> float:
    """The job-completion score bonus -- ports ``mf-prg.bas:25560``: ``x=3+3*(jo(sp)=2)``.

    Every job type scores 3, EXCEPT the croupier job (type 2), which scores **0**:
    the relational ``(jo(sp)=2)`` is -1 in C64 BASIC, giving ``3+3*(-1)=0``.

    Confirmed by the #47 fidelity audit: do not "fix" this to 6 -- that is the research
    gloss's reading under a ``true=+1`` convention, which is wrong for C64 BASIC. 0 is
    also the reading that
    makes design sense: the croupier is the one job that already paid an immediate
    per-shift bonus (``:25125-25126``), so it earns no completion award on top.
    """
    return 0.0 if job_type == JOB_CROUPIER else 3.0


def _fight(ctx, *, spec, backdrop: str):
    """Run one shift fight against a single declared enemy; return the winning side.

    ``spec`` is the selected :class:`~..setup.EnemySpec` (a variant of a declared
    encounter); ``backdrop`` is the encounter's grid name. Builds ``StartCombat``
    from the active player's CURRENT roster (read fresh off ``ctx.state`` -- no earlier
    effect in a shift run touches the roster) and the declared enemy setup. Side 1 is
    always the acting player (the ``_run_combat`` convention), so a loss/win here also
    rides the fight's roster energy deltas into ``ctx`` via the driver's ``_run_combat``
    (no extra code needed here for that).

    The fight's PAYOUT is not declarable (it belongs to the shift's win/loss branch in
    :func:`job_shift`), so the encounter carries no ``on_win``/``on_loss`` and this
    helper applies no consequence — it just runs the fight and returns the winner.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    # Setup is the declared encounter's; the enemy stats (mf-prg.bas:30245) and
    # equipment stay handler-supplied. Scenario.from_encounter reads count/weapon/
    # vitality/name off the spec and delegates to from_roster.
    scenario = Scenario.from_encounter(
        spec,
        active.roster,
        build_rules(),
        # The fixed CPU-enemy stats (mf-prg.bas:30245) are config data.
        enemy_attrs=enemy_attrs(ctx.state.config.formula_params),
        grid=_backdrop(backdrop),
        equip=equipper(_weapon_stats()),
    )
    result = yield StartCombat(scenario=scenario)
    # Outcome narration (the invoking handler's job -- _run_combat yields no
    # final screen). Shared with kdh.py/upkeep.py's own fights (narrate_combat_outcome
    # -- ShowMessage's (key, params) shape means the generic client renderer resolves
    # these with no special-case wiring, exactly like every other ported string). The
    # per-side death tallies come off the CombatResult, so the count is real even
    # though every shift fight happens to be 1v1 (enemy_count=1, gz(0)=1).
    yield from narrate_combat_outcome(
        winner=result.winner,
        player_name=active.name,
        enemy_name=spec.name,
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
    employed vs. free turn -- is the job-shift seam).
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
            # :25035 -- the 1-of-3 variant SELECTION stays in Python; the definitions
            # live in the declared encounter.
            spec = _BOUNCER_ENCOUNTER.variants[ctx.rng.range(3)]
            winner = yield from _fight(ctx, spec=spec, backdrop=_BOUNCER_ENCOUNTER.grid)
            won = winner == 1

    elif job_type == JOB_CROUPIER:
        # :25100-25140 -- pick a trick, catch check, bonus or fight.
        yield ShowMessage("job.shift_croupier_intro")
        trick = yield PromptInt("job.shift_croupier_pick", min=1, max=3)
        if ctx.rng.range(6 - trick) == 0:  # :25120 `int(rnd(1)*(6-x))=0`
            # :25130 -- caught; a fight starts.
            yield ShowMessage("job.shift_croupier_caught")
            winner = yield from _fight(
                ctx, spec=_CROUPIER_ENCOUNTER.variants[0], backdrop=_CROUPIER_ENCOUNTER.grid
            )
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
        winner = yield from _fight(
            ctx, spec=_KILLER_ENCOUNTER.variants[0], backdrop=_KILLER_ENCOUNTER.grid
        )
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

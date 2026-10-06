"""The job-shift flow — ports ``mf-prg.bas:25000-25560``.

This is the flow that REPLACES an employed player's free turn (the job-shift seam):
once ``pub.job`` (``handlers/pub.py``) accepts a job, the engine turn runner
(``engine.turns``) runs this generator instead of the free turn, right after upkeep,
whenever this config's job hook (``handlers/turn.py``) says a job is held. This module
owns no dispatch decision itself — it is a plain registered handler, driven exactly
like any location option.

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
from ..state import job
from engine.interactions import PromptInt, ShowMessage
from engine.locations import register
from engine.turns import JOB_SHIFT_HANDLER_KEY as ENGINE_JOB_SHIFT_KEY

from ..setup import (
    load_encounter,
    KEY_WAIT,
    run_encounter,
    score_and_rank,
)

# Job type ids -- shared with pub.py's take-job handler (mf-prg.bas:12305's ON-GOTO
# dispatch order). Imported (not re-declared as separate literals) so the two
# modules cannot drift apart on what each job type id means.
from .pub import JOB_BOUNCER, JOB_CROUPIER, JOB_DOORMAN, JOB_KILLER

__all__ = ["job_shift", "JOB_SHIFT_HANDLER_KEY"]

#: The registry key this shift generator is registered under -- the engine turn
#: runner's (``engine.turns``) fixed key, looked up exactly like
#: ``engine.upkeep.UPKEEP_HANDLER_KEY``, in the SAME ``engine.locations.HANDLERS``
#: registry (this is not a location option, but it is still just another handler id).
JOB_SHIFT_HANDLER_KEY = ENGINE_JOB_SHIFT_KEY

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


def _fight(ctx, encounter, *, variant: int = 0):
    """Run one shift fight (a declared encounter's variant); return the winning side.

    The shared fight helper assembles it from the active player's current roster (no
    earlier effect in a shift run touches the roster) and shows the outcome screen.
    Side 1 is always the acting player. The fight's PAYOUT is not declarable (it
    belongs to the shift's win/loss branch in :func:`job_shift`), so the encounters
    carry no ``on_win``/``on_loss`` and this applies no consequence.
    """
    result = yield from run_encounter(ctx, encounter, variant=variant)
    return result.winner


@register(JOB_SHIFT_HANDLER_KEY)
def job_shift(ctx):
    """Run one shift for the active player's accepted job -- ports ``25000-25560``.

    Dispatches on ``job(active).type`` (``jo(sp)``); the engine turn runner only
    invokes this when the config's job hook (``handlers/turn.py``) says a job is held.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    current_job = job(active)
    job_type = current_job.type

    won = True  # quiet day / successful cheat default to "no fight, shift succeeds"

    # :25000 ``print"{clr}{down}{rvon}{blk} "sp$(sp)":{$a0}job als ":print`` -- the
    # screen's header, under the runner's job-shift screen clear.
    yield ShowMessage("job.shift_header", {"name": active.name})

    if job_type in (JOB_BOUNCER, JOB_DOORMAN):
        # :25015-25045 -- shared bouncer/doorman flow. Source-confirmed quirk: the
        # ON-GOTO at :25010 sends BOTH job types to the SAME line 25015, which prints
        # the "rausschmeisser" (bouncer) header/narration even for an employed
        # DOORMAN -- there is no separate doorman-specific text block in the source,
        # so reusing the bouncer strings for both is faithful, not a shortcut.
        yield ShowMessage("job.shift_bouncer_title")  # :25015
        yield ShowMessage("job.shift_bouncer_wait")
        if ctx.rng.range(2) == 0:
            # :25025 -- 50% quiet day.
            yield ShowMessage("job.shift_bouncer_quiet")
            yield KEY_WAIT  # :25025 ...:gosub1100:goto25550
        else:
            yield ShowMessage("job.shift_bouncer_trouble")
            yield KEY_WAIT  # :25030 ...:gosub1100, before the fight
            # :25035 -- the 1-of-3 variant SELECTION stays in Python; the definitions
            # live in the declared encounter. :25045 ``gosub5000:goto25500``: the fight,
            # then the outcome.
            winner = yield from _fight(ctx, _BOUNCER_ENCOUNTER, variant=ctx.rng.range(3))
            won = winner == 1

    elif job_type == JOB_CROUPIER:
        # :25100-25140 -- pick a trick, catch check, bonus or fight.
        yield ShowMessage("job.shift_croupier_title")  # :25100
        yield ShowMessage("job.shift_croupier_intro")
        trick = yield PromptInt("job.shift_croupier_pick", min=1, max=3)
        if ctx.rng.range(6 - trick) == 0:  # :25120 `int(rnd(1)*(6-x))=0`
            # :25130 -- caught; a fight starts.
            yield ShowMessage("job.shift_croupier_caught")
            yield KEY_WAIT  # :25131 ...:gosub1100, before the fight
            winner = yield from _fight(ctx, _CROUPIER_ENCOUNTER)
            won = winner == 1
        else:
            # :25125-25126 -- success; immediate bonus, no fight. p=int(rnd(1)*100*x)+300
            # draws 0..100x-1 THEN adds 300, so the inclusive range tops out at
            # 300+100x-1, not 300+100x (an off-by-one the `hit(a, b)` inclusive-bounds
            # helper would otherwise bake in if b were passed as 300+100*trick).
            bonus = ctx.rng.hit(300, 300 + 100 * trick - 1)
            ctx.apply(MoneyChange(bonus))
            yield ShowMessage("job.shift_croupier_bonus", {"amount": bonus})
            yield KEY_WAIT  # :25126 ...:gosub1100:goto25550

    elif job_type == JOB_KILLER:
        # :25200-25210 -- always fight the victim.
        yield ShowMessage("job.shift_killer_title")  # :25200
        yield ShowMessage("job.shift_killer_intro")
        yield KEY_WAIT  # :25206 ...:gosub1100, before the fight
        winner = yield from _fight(ctx, _KILLER_ENCOUNTER)
        won = winner == 1

    # :25500-25560 -- common outcome resolution.
    if not won:
        # :25505-25510 -- lost the fight: job ends unpaid, score -2.
        params = ctx.state.config.formula_params
        ctx.apply(score_and_rank(-2, params))
        ctx.apply(JobClear())
        yield ShowMessage("job.shift_failed")
        yield KEY_WAIT  # :25510 ...:jo(sp)=0:goto1100
        return []

    # :25550 -- successful shift: decrement months_left. JobSet (not a bespoke
    # decrement effect) re-stores the UNCHANGED type/pay alongside the new
    # months_left -- the source only ever writes jd(sp) as part of the same array
    # slot the accept step used, so replaying this effect reproduces the exact same
    # job shape with one field ticked down.
    months_left = current_job.months_left - 1
    if months_left != 0:
        ctx.apply(
            JobSet(
                type=current_job.type,
                pending_pay=current_job.pending_pay,
                months_left=months_left,
            )
        )
        return []  # :25550 ...ifjd(sp)<>0thenreturn -- no key wait of its own

    # :25555-25560 -- contract finished: pay the full wage, award completion score.
    params = ctx.state.config.formula_params
    ctx.apply(MoneyChange(current_job.pending_pay))
    ctx.apply(score_and_rank(_completion_score(job_type), params))
    ctx.apply(JobClear())
    yield ShowMessage("job.shift_completed", {"pay": current_job.pending_pay})
    yield KEY_WAIT  # :25560 ...:jo(sp)=0:goto1100
    return []

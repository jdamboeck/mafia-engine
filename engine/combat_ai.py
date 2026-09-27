"""Combat CPU targeting: the ``cr`` nearest-hostile target selection and the default CPU sides.

Holds :class:`AiTarget`, :func:`ai_target` (the port of the ``cr`` machine-code routine)
and :data:`DEFAULT_CPU_SIDES`. The CPU *decision* built on the target (move vs shoot)
is :meth:`engine.combat.CombatFight.ai_decide`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.combat import GRID_COLS, STEP_DOWN, STEP_LEFT, STEP_RIGHT, STEP_UP

if TYPE_CHECKING:
    from engine.combat import CombatFight, CombatView

__all__ = [
    "AiTarget",
    "ai_target",
]

# --------------------------------------------------------------------------- #
# CPU target selection — the `cr` machine-code routine                        #
# --------------------------------------------------------------------------- #
# The CPU hunts whoever is HOSTILE to the acting side, derived per-fight from
# :meth:`CombatFight.hostile_to` rather than a hardcoded "always side 1" constant.
# The original's colour-RAM/``cr`` mechanism that made it always side 1 — and why that
# reduces to the two-party ``(2,)``/``(1,)`` default — is documented on ``hostile_to``.

#: Sides driven by the AI rather than by a client prompt.
#:
#: Ports ``mf-prg.bas:30110`` (``ifks(s)=0thengosub30400:goto30105``) together with
#: ``5010`` (``ks(1)=sp:ks(2)=0``): the combat-launch helper puts the NPC party in
#: slot 2 and marks it CPU with ``ks(2)=0``. ``30020`` agrees — direction memory is
#: only initialized ``ifks(2)=0``. The source *can* express a player-vs-player fight
#: (``27020`` sets ``ks(1)``/``ks(2)`` to two player numbers), so this is a default,
#: not a hardcoded rule: a caller may pass an empty ``cpu_sides`` for hot-seat play.
DEFAULT_CPU_SIDES: tuple[int, ...] = (2,)


@dataclass(frozen=True)
class AiTarget:
    """The nearest hostile fighter, as ``cr`` reports it through ``ua``..``ua+3``.

    ``cr``'s four return bytes (``$A7``..``$AA``) and their BASIC decoding at
    ``mf-prg.bas:30405`` (``x=peek(ua)-1 : y=peek(ua+1)-40``):

    ==========  ===========================================  ===================
    byte        raw meaning (disassembly)                    decoded here
    ==========  ===========================================  ===================
    ``ua+0``    x-direction code 0=left / 1=none / 2=right   :attr:`x`  (-1/0/+1)
    ``ua+1``    y-direction code 0=up / 40=none / 80=down    :attr:`y`  (-40/0/+40)
    ``ua+2``    ``abs(dx)`` to the nearest enemy             :attr:`abs_dx`
    ``ua+3``    ``abs(dy)`` to the nearest enemy             :attr:`abs_dy`
    ==========  ===========================================  ===================

    Note that the decoded ``x``/``y`` are already **linear step deltas** (±1 and ±40,
    i.e. :data:`STEP_LEFT`/:data:`STEP_RIGHT` and :data:`STEP_UP`/:data:`STEP_DOWN`),
    which is exactly why the BASIC can feed them straight into ``p=x`` at ``30450``
    and into the shot resolver at ``30215`` without any conversion. ``y``'s ±40 magnitude
    is the *combat* grid's row width (40 columns) — the same 40 as the city map's width
    by coincidence of screen geometry, not because the two spaces are related (CLAUDE.md).

    ``side``/``index`` locate the chosen fighter for the caller; the original has no
    equivalent (it only ever needs the deltas), so they are port bookkeeping.

    A frozen dataclass, matching every other value type in this module/``engine.state``
    (e.g. :class:`~engine.state.Fighter`) rather than a hand-rolled ``__slots__`` class.
    """

    side: int
    index: int
    x: int
    y: int
    abs_dx: int
    abs_dy: int

    @property
    def distance(self) -> int:
        """The ``cr`` distance metric ``dy*40 + dx`` (the ``$ECF0`` table)."""
        return self.abs_dy * GRID_COLS + self.abs_dx


def ai_target(fight: "CombatFight | CombatView") -> AiTarget | None:
    """Pick the active fighter's target the way ``cr`` (``$C000``) does.

    **The metric.** The disassembly's runtime-confirmed ``$ECF0`` table holds multiples
    of ``$28`` (40): ``LDA $ECF0,X`` with ``X = abs(dy)`` yields ``dy*40``, and the
    following ``ADC abs(dx)`` gives ``dy*40 + dx``. The **smallest** such value wins —
    "the nearest enemy in reading order" (ml-core-disassembly.yaml, ``cr.distance_metric``).

    This is emphatically **not** Euclidean or Chebyshev distance: a fighter five columns
    away on the same row (metric 5) is "nearer" than one a single row away in the same
    column (metric 40). The AI's whole pursuit shape follows from that bias.

    **Who is a candidate.** The fighters on the side(s) hostile to the ACTIVE side
    (:meth:`CombatFight.hostile_to`), never the active fighter's own — self-exclusion
    falls out of the derivation, since a side is never hostile to itself. In the
    original ``cr`` scans for cells holding char 193 with colour-RAM low nibble 2, which
    is always side 1; deriving from ``active_side`` here is bit-identical for the
    side-2-acts case the original produces, and additionally correct when a caller puts
    side 1 under the AI (which ``cpu_sides`` permits). Downed fighters are excluded
    because ``mf-prg.bas:30310`` pokes their cell back to 32, removing the 193 glyph
    ``cr`` matches on; this port checks ``Fighter.down`` instead, which is the same set.

    Returns ``None`` when no hostile fighter is standing — in the original that state is
    unreachable, because the victory check at ``30106`` fires before the AI branch at
    ``30110`` ever runs. The port returns ``None`` rather than raising so a degenerate
    setup degrades to "no action" instead of crashing a fight.

    Ties are broken by scan order (hostile side order, then lowest fighter index),
    matching ``cr``'s strict ``<`` comparison as it walks candidates: the first
    candidate at the minimum wins.
    """
    origin = fight.active.position
    oy, ox = divmod(origin, GRID_COLS)

    best: AiTarget | None = None
    for hostile_side in fight.hostile_to(fight.active_side):
        for index, other in enumerate(fight.sides[hostile_side - 1]):
            if other.down:
                continue
            row, col = divmod(other.position, GRID_COLS)
            dx = col - ox
            dy = row - oy
            candidate = AiTarget(
                side=hostile_side,
                index=index,
                # 30405's decoding: the direction bytes collapse the delta to its SIGN,
                # scaled to a one-cell step (±1 horizontally, ±40 = one row vertically).
                x=(STEP_RIGHT if dx > 0 else STEP_LEFT if dx < 0 else 0),
                y=(STEP_DOWN if dy > 0 else STEP_UP if dy < 0 else 0),
                abs_dx=abs(dx),
                abs_dy=abs(dy),
            )
            if best is None or candidate.distance < best.distance:
                best = candidate
    return best

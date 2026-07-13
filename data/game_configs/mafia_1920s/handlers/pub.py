"""The pub (Kneipe) recruit handler — STUB only (U9).

U9 builds the movement/turn economy that lets the player WALK to the pub and hit
its recruit guard. The pub's declarative shell (``content/locations/pub.yaml``)
references a ``pub.recruit`` handler, and the loader requires every referenced
handler to be registered at load time. So this module registers a **stub**:

* at rank 1 the shell guard (``ra(sp) > 4``) denies the recruit option, so the
  handler is **never entered** this slice (KTD-8: a denied option is never run);
* the stub therefore just raises :class:`NotImplementedError` if ever invoked.

The real recruit body — the candidate pool, the offer/negotiation, the hire —
is a **later unit** (ports ``mf-prg.bas:12100-12175``). This module exists only so
the pub shell loads with a registered handler.
"""

from __future__ import annotations

from engine.locations import register

__all__ = ["pub_recruit"]


@register("pub.recruit")
def pub_recruit(ctx):
    """Pub recruit — STUB (body is a later unit; ports mf-prg.bas:12100-12175).

    Never reached this slice: at rank 1 the shell guard ``ra(sp) > 4`` denies the
    option, so the handler is not entered. If a future caller reaches it before the
    real body lands, fail loudly rather than silently no-op.
    """
    raise NotImplementedError(
        "pub.recruit body is a later unit (mf-prg.bas:12100-12175); at rank 1 the "
        "shell guard ra>4 denies this option, so it is never entered this slice."
    )
    yield  # pragma: no cover — marks this a generator (handler protocol) though unreached

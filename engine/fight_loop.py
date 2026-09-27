"""The fight loop: per-side combat drivers and the shared activation loop that runs a fight to a result.

Holds the :class:`Driver` kinds (human / ai / policy / replay), :func:`_run_combat` (the
``StartCombat`` sub-protocol a handler's fight runs through, called from
:mod:`engine.interactions`'s driver), :func:`_drive_fight` (the ONE activation loop,
shared by every entry point) and :func:`simulate` (the headless entry). The rules of a
fight live in :mod:`engine.combat`; this module only sequences them. ``engine.combat``
is imported lazily inside the functions so this module's runtime import graph stays
free of it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from engine.interactions import CANCEL, OBSERVE_PROMPT, CombatScreen, Ctx, StartCombat

if TYPE_CHECKING:
    # Type-only: keep this module's RUNTIME import graph free of engine.combat (it is
    # imported lazily inside the functions, see the module docstring). The
    # ``from __future__ import annotations`` above makes every annotation a string, so
    # ``-> CombatResult`` never triggers a runtime import; this block only lets a type
    # checker resolve the name.
    from engine.combat import CombatResult

__all__ = [
    # Per-side combat drivers
    "Driver",
    "HumanDriver",
    "AiDriver",
    "PolicyDriver",
    "ReplayDriver",
    # Headless fight entry
    "simulate",
]


# --------------------------------------------------------------------------- #
# Per-side combat drivers — who answers each side's activations                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Driver:
    """How ONE side's activations are answered inside :func:`_drive_fight`.

    The combat loop's dispatch is a **two-way branch on ``kind``**: suspend-and-ask
    (the human path — the ONLY path that ``yield``s a :class:`CombatScreen`) vs.
    call-and-continue (every other kind — the fight runs headlessly on that side).
    ``yield`` cannot cross a plain function call, so a "callable driver" can never
    suspend; that is exactly why ``human`` carries no callable and the rest do.

    Four kinds, all resolved without moving dispatch out of the generator frame:

    ``human``
        No callable. Its presence tells the loop to yield a :class:`CombatScreen`
        and read the response via the input source.
    ``ai``
        Calls ``fight.ai_decide(view)`` — the split-out chooser, no mutation.
    ``policy``
        Calls ``decide(view)``, a ``Callable[[CombatView], tuple[str, Any]]`` — a
        scripted or heuristic side with no client in the loop.
    ``replay``
        A declared kind, not functional (see :class:`ReplayDriver`). A replay driver
        must NOT execute (the recorded result is the thing being reproduced), which is
        precisely why every kind obeys the same "choose, return, let one place apply"
        contract.

    A base :class:`Driver` is any of these; the concrete subclasses below set ``kind``
    and, where relevant, carry the callable.
    """

    kind: str = "human"

    def decide(self, view: Any) -> tuple[str, Any]:
        """Choose ``(action, argument)`` from a read-only ``CombatView``.

        The default raises — a plain ``human`` :class:`Driver` never decides (the
        loop yields a screen for it instead), and ``replay`` is not functional.
        The ``ai``/``policy`` subclasses override this.
        """
        raise NotImplementedError(f"{type(self).__name__}(kind={self.kind!r}) cannot decide")


@dataclass(frozen=True)
class HumanDriver(Driver):
    """A client-driven side: the loop yields a :class:`CombatScreen` and prompts."""

    kind: str = "human"


@dataclass(frozen=True)
class AiDriver(Driver):
    """A CPU side: :meth:`decide` defers to the fight's own ``ai_decide`` chooser."""

    kind: str = "ai"

    def decide(self, view: Any) -> tuple[str, Any]:
        # The view knows the fight it wraps; ai_decide is the fight's split-out
        # chooser, so an AiDriver holds no state of its own.
        return view._fight.ai_decide(view)


@dataclass(frozen=True)
class PolicyDriver(Driver):
    """A side driven by a supplied ``(CombatView) -> (action, argument)`` callable."""

    kind: str = "policy"
    policy: Any = None

    def decide(self, view: Any) -> tuple[str, Any]:
        if self.policy is None:
            raise ValueError("PolicyDriver has no policy callable")
        return self.policy(view)


@dataclass(frozen=True)
class ReplayDriver(Driver):
    """A declared ``replay`` kind that is not functional; :meth:`decide` raises.

    :func:`engine.recording.replay` applies a recording's decisions itself and does not
    go through a driver, so nothing constructs a working one. :meth:`decide` raises
    with a clear message rather than guessing.
    """

    kind: str = "replay"

    def decide(self, view: Any) -> tuple[str, Any]:
        raise NotImplementedError(
            "ReplayDriver is not functional; engine.recording.replay applies recorded decisions itself"
        )


def _run_combat(
    start: "StartCombat",
    input_source: Callable[[Any], Any],
    ctx: "Ctx",
) -> "CombatResult":
    """Drive a full fight to a winner and return its :class:`~engine.combat.CombatResult`.

    The combat sub-protocol, structurally a sibling of
    :func:`engine.interactions._run_substate`: an INLINE loop over the parent's ``ctx``
    (so the fight's effects buffer into the parent action and commit or discard with
    it), never a nested :func:`engine.interactions.run` call (which would commit the
    fight independently and break that atomicity).

    Ports the activation loop ``mf-prg.bas:30100-30155`` via :func:`_drive_fight`
    (victory check, one action per activation, CPU sides decided with no prompt,
    human sides prompted with a :class:`CombatScreen`).

    **Non-cancellable.** :data:`CANCEL` — which is also how a client
    surfaces EOF — is mapped to a surrender, never to a ``Cancelled`` throw. A
    mandatory fight must not be escapable through a cancel unwind, so this loop
    deliberately does not consult the cancel path that
    :func:`engine.interactions._resolve` owns.

    The invoking handler owns the fight's ENTRY-POINT consequence ("losing a
    fight carries only the entry point's consequence": debt seizure, job pay, reward,
    etc.) — that is still on the caller. But the fight's OWN persistent side-effect on
    the roster — energy spent, fighters knocked down — is NOT entry-point-specific, it
    is true of every fight regardless of who triggered it, so this function buffers it
    directly: before returning, it diffs side 1's (the acting player's roster,
    ``mf-prg.bas:5010``'s ``ks(1)=sp`` — SpawnFighter's docstring pins this convention)
    per-fighter energy against its pre-fight snapshot and buffers one
    :class:`~engine.effects.EnergyChange` per fighter whose energy changed, in roster
    order (1:1 with ``start.sides[0]``, since :func:`engine.combat_setup.build_player_side`
    never reorders the roster). ``cap`` is set to the fighter's OWN pre/post energy
    ceiling (never a fresh regen-cap computation) so the clamp in
    ``engine.effects.EnergyChange.apply`` is a structural no-op here —
    combat only ever LOWERS energy (no mid-fight healing exists), so the
    post-fight value is by construction the correct final value, not merely a floor.
    Side 2 (the enemy party) is NPC working state, never a roster, so it is not
    persisted here. A no-op fight (no side-1 fighter's energy moved, e.g. a
    zero-activation surrender before anyone was struck) buffers nothing.
    """
    # Lazy imports keep this module's top-level import graph free of engine.state /
    # engine.combat, mirroring the commit() import in engine.interactions.run().
    from engine.combat import CombatResult
    from engine.effects import EnergyChange

    fight = _build_fight(start, rng=ctx.rng)
    drivers = _resolve_drivers(start)
    # The depleting resource is the engine's ``vitality`` SLOT — the
    # driver reads it directly and never spells this game's word for it. Every Fighter
    # carries the slot, so there is nothing to guard: a bundle-less fight (surrendered
    # without a shot) simply sees an unchanged ``vitality`` and buffers no delta.
    pre_vitality = [f.vitality for f in fight.sides[0]]

    # The activation loop is the SHARED one (:func:`_drive_fight`) so `_run_combat` and
    # `simulate` cannot drift — the same loop, whether a client is in it or not.
    winner = _drive_fight(fight, drivers, input_source)

    # Buffer the roster's persistent energy/down consequence BEFORE handing
    # the winner back, so it commits atomically with the invoking handler's own
    # entry-point effects (one shared ctx, one atomic buffer).
    for i, f in enumerate(fight.sides[0]):
        now = f.vitality
        if now == pre_vitality[i]:
            continue
        # Address the gangster this fighter IS, not the slot it happens to sit in
        # These coincide today because build_player_side maps roster
        # order onto placement order 1:1 — but a fighter without a roster entry must
        # not write to gangster 0 just because it is first.
        if f.roster_id is None:
            continue
        ctx.apply(
            EnergyChange(
                amount=now - pre_vitality[i],
                cap=max(pre_vitality[i], now),
                gangster=f.roster_id,
            )
        )
    # Hand back the winner AND the real per-side death tallies (v(1)/v(2)).
    return CombatResult(winner=winner, losses=fight.losses)


def _build_fight(start: "StartCombat", *, rng: Any) -> Any:
    """Construct the :class:`~engine.combat.CombatFight` a ``StartCombat`` describes.

    Prefers ``start.scenario`` (the whole payload in one field); when absent, falls
    back to the four separate fields (``sides``/``grid``/``rules``/``dir_memory``).
    Shared by :func:`_run_combat` and :func:`simulate`.
    """
    from engine.combat import CombatFight
    from engine.state import CombatState

    scenario = start.scenario
    if scenario is not None:
        sides = scenario.sides
        grid = scenario.grid
        rules = scenario.rules
        dir_memory = scenario.dir_memory
    else:
        sides = start.sides
        grid = start.grid
        rules = start.rules
        dir_memory = start.dir_memory
    return CombatFight(
        CombatState(
            sides=sides,
            grid=tuple(grid or ()),
            dir_memory=dict(dir_memory or {}),
        ),
        rng=rng,
        rules=rules,
    )


def _resolve_drivers(start: "StartCombat") -> "dict[int, Driver]":
    """The ``{side: Driver}`` map for a fight — explicit map OVERRIDES ``cpu_sides``.

    An explicit ``start.drivers`` wins outright (two knobs on one axis never
    merge). Otherwise the map is derived from ``cpu_sides``: an :class:`AiDriver`
    for a CPU side, a :class:`HumanDriver` for a client side. ``cpu_sides is None``
    means the default (side 2 is CPU); an explicit ``()`` means a fully hot-seat fight.
    """
    from engine.combat_ai import DEFAULT_CPU_SIDES

    if start.drivers is not None:
        return dict(start.drivers)
    cpu_sides = DEFAULT_CPU_SIDES if start.cpu_sides is None else tuple(start.cpu_sides)
    return {s: (AiDriver() if s in cpu_sides else HumanDriver()) for s in (1, 2)}


def _drive_fight(
    fight: Any,
    drivers: "Mapping[int, Driver]",
    input_source: Callable[[Any], Any],
    recorder: Any = None,
) -> int:
    """Advance a fight to a winner, one activation at a time — the SHARED loop.

    Ports the activation loop ``mf-prg.bas:30100-30155``, driver-agnostic:

    1. Victory check (``30106``) — the fight ends the moment one side has no standing
       fighter, mid-round, without finishing the current side's turn.
    2. Pick ``drivers[fight.active_side]``. If it is ``human``, yield a
       :class:`CombatScreen` and read one response via ``input_source`` (the ONLY
       suspending path — "headless" means no yield occurs on a non-human side's turn).
       Otherwise the driver ``decide``s ``(action, argument)`` with NO client in the
       loop.
    3. Apply exactly ONE action per activation through the SINGLE apply-block
       (:meth:`~engine.combat.CombatFight.apply_action`), whoever chose it — so a shot
       fires once. An illegal move or unrecognized response from a HUMAN re-prompts the
       same activation (``30145``/``30139``) rather than consuming it.
    4. Advance the cursor (``30105``), skipping downed fighters (``30109``).

    Returns the winning side. Records the fight's result via :meth:`CombatFight.finish`
    / :meth:`surrender` so ``fight.result_flag`` and ``fight.losses`` are final.

    **Non-cancellable.** :data:`CANCEL` / EOF at a human prompt is mapped to a
    surrender, never a ``Cancelled`` throw — a mandatory fight must not be escapable.

    A ``human`` driver on a side reached during a **headless** run (``input_source is
    None``) is a caller error: :func:`simulate` rejects human drivers up front, so this
    loop can assume a human side always has a usable ``input_source``.

    **Recording seam.** An optional ``recorder`` (a
    :class:`engine.recording._Recorder`) observes the loop without altering it: it marks
    the rng-log high-water mark before each activation draws, emits a ``HandoffEvent`` on
    a driver reassignment, and appends one ``ActivationEvent`` per applied action. A
    non-recording caller passes ``recorder=None`` and every hook is a no-op.

    **Observation frames.** If ``input_source`` carries a truthy
    ``observes_ai`` attribute, the loop hands it one display-only :class:`CombatScreen`
    with ``prompt=`` :data:`OBSERVE_PROMPT` after EACH non-human activation applies
    (including the one that ends the fight), AFTER the recorder has captured it. The
    response is ignored and nothing is drawn from the RNG, so the fight, its rng log, and
    its recording are identical with or without the opt-in. The opt-in lives on the input
    source (read with ``getattr``) rather than as a keyword so no signature changes:
    ``simulate``/``record_fight``'s headless ``_no_input_source`` and every wrapper
    callable (``persistence``'s chained input, ``upkeep``/``game_end`` fallbacks, test
    scripts) lack the attribute and therefore stay OFF — the faithful default, since the
    original's CPU path narrates nothing between activations (``mf-prg.bas:30110``).
    """
    message: Any = None
    observes_ai = bool(getattr(input_source, "observes_ai", False))
    while True:
        winner = fight.winner()
        if winner is not None:
            return fight.finish(winner)

        # Recording seam: observe driver reassignments (a HandoffEvent) and mark the
        # rng-log high-water mark BEFORE this activation draws, so its draws are the
        # exact slice ``rng.log[draw_start:]``. Optional — a non-recording caller passes
        # ``recorder=None`` and this block is a no-op.
        if recorder is not None:
            recorder.observe_drivers(drivers)
        draw_start = recorder.draw_mark() if recorder is not None else 0

        driver = drivers[fight.active_side]
        # Captured before the cursor advances, so a recorded event names the ACTING
        # fighter (0-based), not the one the cursor lands on next.
        acting_side = fight.active_side
        acting_fighter_index = fight.active_fighter - 1

        if driver.kind == "human":
            screen = CombatScreen(
                sides=fight.sides,
                grid=fight.grid,
                active_side=fight.active_side,
                active_fighter=fight.active_fighter,
                losses=fight.losses,
                prompt="action",
                message=message,
            )
            raw = input_source(screen)
            message = None
            action, argument = _parse_combat_response(raw)
        else:
            # ai / policy / replay — DECIDE ONLY, off a read-only view. No yield, no
            # client. The action falls into the SAME apply-block below.
            action, argument = driver.decide(fight.view())

        # The decision itself may have drawn (the AI's 30415 coin flip). Record how many
        # of this activation's draws belong to the DECISION so replay — which uses the
        # recorded decision, not a fresh one — can skip past them and align the ACTION's
        # formula with its own draws.
        decision_draw_count = (recorder.draw_mark() - draw_start) if recorder is not None else 0

        # Capture the formula inputs for a shot BEFORE it applies (the attacker's attrs
        # and equipment are read at the moment of firing), so the recorder needs no
        # second computation path.
        calc_inputs: dict = {}
        if recorder is not None and action == "shoot":
            from engine.recording import _shoot_calc_inputs

            calc_inputs = _shoot_calc_inputs(fight, argument)

        def _record(applied_action: str, result: Any, calc: dict) -> None:
            # One ActivationEvent for the action that just applied. A no-op without a
            # recorder; the invariant fields (who acted, the draw slice bounds) are the
            # same whatever the action, so only action/result/calc vary per call site.
            if recorder is not None:
                recorder.record_activation(
                    side=acting_side,
                    fighter_index=acting_fighter_index,
                    driver_kind=driver.kind,
                    action=applied_action,
                    argument=argument,
                    result=result,
                    calc_inputs=calc,
                    draw_start=draw_start,
                    decision_draw_count=decision_draw_count,
                )
            # Then the opt-in observation frame — after the recorder, so the
            # recording never sees it; display-only, response discarded, no draws.
            if observes_ai and driver.kind != "human":
                input_source(
                    CombatScreen(
                        sides=fight.sides,
                        grid=fight.grid,
                        active_side=acting_side,
                        active_fighter=acting_fighter_index + 1,
                        losses=fight.losses,
                        prompt=OBSERVE_PROMPT,
                        message=result or None,
                    )
                )

        if action == "surrender":
            return fight.surrender()
        if action == "pass":
            fight.advance_activation()
            _record("pass", {}, {})
            continue
        if action == "move":
            committed = fight.apply_action(
                "move", argument, record_dir_memory=driver.kind != "human"
            )
            if not committed:
                # 30145: illegal target. A HUMAN re-prompts the same activation; a
                # non-human driver that returns an illegal step has a broken decide
                # contract and must fail loudly rather than re-decide the same illegal
                # step forever (headless, there is no client to break the loop).
                if driver.kind == "human":
                    message = "illegal_move"
                    continue
                raise ValueError(
                    f"{driver.kind!r} driver on side {fight.active_side} returned an illegal "
                    f"move ({argument!r}); a non-human driver's chooser must pre-validate steps"
                )
            fight.advance_activation()
            _record("move", {}, {})
            continue
        if action == "shoot":
            result = fight.apply_action("shoot", argument)
            message = result
            winner = fight.winner()
            if winner is not None:
                _record("shoot", result, calc_inputs)
                return fight.finish(winner)
            fight.advance_activation()
            _record("shoot", result, calc_inputs)
            continue
        # 30139: an unrecognized key. A HUMAN falls back to the GET wait and re-prompts;
        # a non-human driver that returns an unknown action has a broken decide contract
        # and must fail loudly — re-prompting it headlessly would spin forever.
        if driver.kind == "human":
            message = "unknown_action"
            continue
        raise ValueError(
            f"{driver.kind!r} driver on side {fight.active_side} returned an unrecognized "
            f"action {action!r}; a driver's decide contract must return a known action"
        )


def simulate(
    scenario: Any,
    drivers: "Mapping[int, Driver]",
    *,
    rng: Any = None,
) -> "CombatResult":
    """Run a fight to a result HEADLESSLY, with no client and no ``GameState``.

    The programmatic sibling of the handler-driven :func:`_run_combat`: a caller hands
    it a :class:`~engine.scenario.Scenario` and an explicit ``{side: Driver}`` map and
    gets back a :class:`~engine.combat.CombatResult` (winner + real per-side losses)
    — the SAME value a fight run through a handler yields, so a caller cannot tell which
    path produced it. There is no ``cpu_sides`` sugar here; this is the explicit entry
    point, so the driver map is required and complete.

    It drives the fight through the SAME shared activation loop (:func:`_drive_fight`)
    as the handler path, so the two cannot drift. It builds no :class:`Ctx` and buffers
    no effects — a headless simulation has no roster to persist energy back onto (that
    is :func:`_run_combat`'s concern, tied to the invoking handler's shared ctx).

    ``rng`` seeds the fight's draws; when ``None`` and the scenario carries a ``seed``,
    a fresh seeded :class:`~engine.rng.Rng` is built from it, which is what makes a
    seeded simulation reproducible (same seed -> same transcript -> same result). A
    zero-variance weapon needs no rng at all.

    **Raises loudly on a human side.** A :class:`HumanDriver` cannot run headlessly (it
    would need a client to suspend to), so this raises a clear ``ValueError`` naming the
    side — a distinct failure from a scripted driver returning a bad action, and from an
    exhausted answer list (which the driver's own callable surfaces).
    """
    from engine.combat import CombatResult

    human_sides = [s for s, d in drivers.items() if getattr(d, "kind", None) == "human"]
    if human_sides:
        raise ValueError(
            f"simulate() cannot run a HumanDriver headlessly (sides {sorted(human_sides)}); "
            f"a human side needs a client — run it through a handler/`run` instead"
        )

    if rng is None and getattr(scenario, "seed", None) is not None:
        from engine.rng import Rng

        rng = Rng(seed=scenario.seed)

    fight = _build_fight(StartCombat(scenario=scenario), rng=rng)
    # No input_source: a headless run never reaches the human/yield branch (guarded
    # above), so the loop never consults it.
    winner = _drive_fight(fight, dict(drivers), _no_input_source)
    return CombatResult(winner=winner, losses=fight.losses)


def _no_input_source(interaction: Any) -> Any:
    """The input source a headless :func:`simulate` hands the loop — never called.

    :func:`simulate` rejects human drivers up front, so :func:`_drive_fight`'s only
    suspending branch is unreachable; if it is ever reached, this raises rather than
    silently returning a value that would be misread as a client answer.
    """
    raise AssertionError(
        "headless simulate() reached the client-prompt path — a human driver slipped past "
        "the up-front guard"
    )


def _parse_combat_response(raw: Any) -> tuple[str, Any]:
    """Normalize a client's combat response into ``(action, argument)``.

    Accepts the ``(action, argument)`` pair the protocol specifies, a bare action
    string for the argument-less actions, and maps :data:`CANCEL` (a client's quit /
    EOF vocabulary) to a surrender (a fight is non-cancellable). Anything else returns
    an unknown action
    so the loop re-prompts rather than guessing.
    """
    if raw is CANCEL or raw is None:
        return ("surrender", None)
    if isinstance(raw, str):
        return (raw, None)
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        return (str(raw[0]), raw[1])
    if isinstance(raw, (tuple, list)) and len(raw) == 1:
        return (str(raw[0]), None)
    return ("__unknown__", None)

"""The **guard DSL** evaluator for menu-option preconditions (docs/design/config-and-content-contract.md).

A guard is a small, nested dict — pure data — that decides whether a menu option
is *available*. This is the logic half of the **declarative shell layer**: the
YAML shell (``engine.locations``) carries the guards, this module evaluates them.
The engine holds no display text; a denied guard's message key lives on the
option (``on_denied``), never here (KTD-5).

The DSL is deliberately tiny and its constraints are a *contract* (CLAUDE.md,
docs/design/config-and-content-contract.md) — enforced here at evaluation time:

* **Two node kinds.**
  - *Leaf*: ``{"var": <name>, "op": <operator>, "value": <literal>}`` — a
    comparison. ``value`` is normally a literal; as a small extension it may be
    ``{"var": <name>}`` to resolve *another* variable on the right-hand side (so a
    variable-vs-variable comparison like ``uk(ln) = sp`` expresses cleanly —
    ``sp`` is dynamic and cannot be baked in as a literal).
  - *Connective*: ``{"and": [...]}`` or ``{"or": [...]}``.

* **Seven operators only:** ``= != >= <= > < in``. ``=`` is *equality* (``==``),
  not assignment. ``in`` is membership (left value ∈ the ``value`` collection).

* **Nesting depth ≤ 2.** A leaf, or a connective-of-leaves, is depth 1. A
  connective whose children may themselves be connectives-of-leaves is depth 2.
  A connective nested three deep is rejected with :class:`ValueError`.

* **No NOT.** There is deliberately no ``not`` node; a ``"not"`` key is rejected.
  Real guards are restructured to avoid negation.

An empty/``None`` guard means "always available" → ``True`` (options without a
guard are always shown).

``engine/`` imports nothing from ``server``/``clients``/transport.
"""

from __future__ import annotations

import operator
from typing import Any, Callable

__all__ = ["OPERATORS", "VARIABLE_RESOLVERS", "build_context", "validate", "evaluate"]

#: The seven allowed comparison operators, mapped to their Python implementations.
#: This is the *entire* operator surface — no others are accepted (an unknown
#: ``op`` raises :class:`ValueError`).
OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "=": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "in": lambda left, right: left in right,
}

#: The connective keys. A node with exactly one of these keys is a connective.
_CONNECTIVES = ("and", "or")

#: The only keys a leaf node may carry. Any extra key (a stray ``and``/``or``, a
#: typo, an injected branch) makes the node malformed — a leaf that ALSO carries a
#: connective key would otherwise be silently treated as a plain leaf, dropping the
#: extra branch (and any NOT hidden in it) past the DSL's depth/no-NOT contract.
_LEAF_KEYS = frozenset({"var", "op", "value"})

#: Maximum allowed nesting depth of connectives (contract from docs/design/config-and-content-contract.md).
_MAX_DEPTH = 2


# --- The eval context: resolvable variables --------------------------------


def _active(state):
    """The active player: ``state.players[state.clock.active_player]`` (orig ``sp``)."""
    return state.players[state.clock.active_player]


def _tenancy(state, ln):
    """``uk(ln)`` resolved for the current tile ``ln`` — 0 means unoccupied.

    A tenancy guard is meaningless without a tile context, so ``ln is None``
    raises a clear :class:`ValueError`.
    """
    if ln is None:
        raise ValueError("guard variable 'tenancy' requires a tile context (ln), but ln is None")
    return state.map.tenancy.get(ln, 0)


#: The single documented mapping of guard-variable name -> resolver ``(state, ln)``.
#: Extend the DSL's readable surface by adding an entry here. An unknown variable
#: name in a guard raises :class:`ValueError`.
VARIABLE_RESOLVERS: dict[str, Callable[[Any, Any], Any]] = {
    "rank": lambda state, ln: _active(state).rank,
    "gang_size": lambda state, ln: len(_active(state).roster),
    "ka": lambda state, ln: _active(state).ka,
    "ms": lambda state, ln: _active(state).ms,
    "po": lambda state, ln: _active(state).po,
    "gf": lambda state, ln: _active(state).gf,
    "sp": lambda state, ln: state.clock.active_player,
    "tenancy": lambda state, ln: _tenancy(state, ln),
}


class _Context:
    """Resolves guard-variable names to values against ``(state, ln)``.

    Lazy: a variable is only resolved when a guard actually references it, so a
    guard that never mentions ``tenancy`` never triggers the ``ln``-required
    check. Unknown names raise :class:`ValueError`.
    """

    def __init__(self, state, ln):
        self._state = state
        self._ln = ln

    def resolve(self, name: str) -> Any:
        resolver = VARIABLE_RESOLVERS.get(name)
        if resolver is None:
            raise ValueError(
                f"unknown guard variable {name!r}; known variables: {sorted(VARIABLE_RESOLVERS)}"
            )
        return resolver(self._state, self._ln)


def build_context(state, ln: int | None = None) -> _Context:
    """Build an evaluation context from ``(state, ln)``.

    ``ln`` is the current within-location tile index (the original per-location
    tile 1..9); ``None`` when not inside a location. Variables are resolved
    lazily on reference (see :class:`_Context`).
    """
    return _Context(state, ln)


# --- Structural validation -------------------------------------------------


def _is_leaf(node: dict) -> bool:
    return "var" in node


def _connective_key(node: dict) -> str | None:
    keys = [k for k in _CONNECTIVES if k in node]
    if len(keys) == 1:
        return keys[0]
    return None


def _check_leaf(node: dict) -> None:
    """Structural check for a leaf node, shared by validation and evaluation.

    Enforces the exact ``_LEAF_KEYS`` set so a stray connective key (or any other
    extra key) on a leaf is a hard error rather than a silently-dropped branch —
    the leaf/connective ambiguity that would otherwise let a hidden ``and``/NOT
    slip past the depth and no-NOT contract.
    """
    extra = set(node) - _LEAF_KEYS
    if extra:
        raise ValueError(
            f"leaf guard has unexpected key(s) {sorted(extra)} "
            f"(a leaf may carry only {sorted(_LEAF_KEYS)}): {node!r}"
        )
    op = node.get("op")
    if op not in OPERATORS:
        raise ValueError(f"unknown guard operator {op!r}; allowed: {sorted(OPERATORS)}")
    if "value" not in node:
        raise ValueError(f"leaf guard missing 'value': {node!r}")


def _validate_node(node: Any, depth: int) -> None:
    if not isinstance(node, dict):
        raise ValueError(f"guard node must be a dict, got {type(node).__name__}: {node!r}")

    if "not" in node:
        raise ValueError("the guard DSL has no NOT; restructure the guard to avoid negation")

    if _is_leaf(node):
        _check_leaf(node)
        return

    key = _connective_key(node)
    if key is None:
        raise ValueError(
            f"malformed guard node (not a leaf, not a single and/or connective): {node!r}"
        )
    if depth > _MAX_DEPTH:
        raise ValueError(
            f"guard nesting too deep (max depth {_MAX_DEPTH}); "
            f"a connective nested {depth} levels deep is invalid: {node!r}"
        )
    children = node[key]
    if not isinstance(children, list) or not children:
        raise ValueError(f"connective {key!r} must hold a non-empty list of nodes: {node!r}")
    for child in children:
        _validate_node(child, depth + 1)


def validate(guard: dict | None) -> None:
    """Validate a guard's structure (depth ≤ 2, no NOT, known ops).

    An empty/``None`` guard is valid (always-true). Raises :class:`ValueError`
    on any structural violation. Evaluation always validates, so calling this
    separately is optional (the loader may call it eagerly at load time).
    """
    if not guard:
        return
    _validate_node(guard, depth=1)


# --- Evaluation ------------------------------------------------------------


def _resolve_operand(value: Any, context: _Context) -> Any:
    """Resolve a leaf's ``value``: a literal, or ``{"var": name}`` on the RHS."""
    if isinstance(value, dict) and "var" in value:
        return context.resolve(value["var"])
    return value


def _eval_node(node: dict, context: _Context, depth: int) -> bool:
    if "not" in node:
        raise ValueError("the guard DSL has no NOT; restructure the guard to avoid negation")

    if _is_leaf(node):
        _check_leaf(node)  # strict leaf keys + known op + value present
        func = OPERATORS[node["op"]]
        left = context.resolve(node["var"])
        right = _resolve_operand(node["value"], context)
        try:
            return bool(func(left, right))
        except TypeError as exc:
            # Keep the module's error contract single-typed: a type-mismatched
            # comparison (e.g. rank > "x", or `in` against a non-iterable) is a
            # guard-contract violation, not an arbitrary TypeError the loader's
            # available_options must guess about. validate() can't catch this
            # (it doesn't know runtime value types), so it surfaces here.
            raise ValueError(
                f"guard comparison failed for op {node['op']!r} on {left!r} vs {right!r}: {exc}"
            ) from exc

    key = _connective_key(node)
    if key is None:
        raise ValueError(
            f"malformed guard node (not a leaf, not a single and/or connective): {node!r}"
        )
    if depth > _MAX_DEPTH:
        raise ValueError(
            f"guard nesting too deep (max depth {_MAX_DEPTH}); "
            f"a connective nested {depth} levels deep is invalid: {node!r}"
        )
    children = node[key]
    if not isinstance(children, list) or not children:
        raise ValueError(f"connective {key!r} must hold a non-empty list of nodes: {node!r}")
    results = (_eval_node(child, context, depth + 1) for child in children)
    return all(results) if key == "and" else any(results)


def evaluate(guard: dict | None, context: _Context) -> bool:
    """Evaluate a guard against a context; ``True`` = the option is available.

    A ``None``/empty guard is always ``True``. Structural violations (depth > 2,
    a ``not`` node, unknown operator/variable, malformed node) raise
    :class:`ValueError`.
    """
    if not guard:
        return True
    return _eval_node(guard, context, depth=1)

"""Restricted mathematical expressions.

A safe, deliberately small subset of arithmetic is supported:

* binary ``+ - * / **`` (a literal ``^`` is rewritten to ``**`` before parsing,
  matching how libSBML accepts ``^``),
* unary ``+ -``,
* finite numeric literals (``int`` / ``float``; booleans are rejected),
* the functions ``exp, log, ln, sqrt, sin, cos, abs, pow``.  ``log`` is the
  *natural* logarithm (identical to ``ln``); SBML's ``log`` is base 10, so both
  are emitted as ``ln``.

Everything else -- attribute access, subscripts, comprehensions, keyword calls,
calls that are not one of the whitelisted names, booleans, ``None`` -- is
rejected at parse time.  This module never calls ``eval`` / ``exec``:
:func:`evaluate` walks the already validated tree node by node.

The AST is checked *recursively*, and operator nodes are inspected directly:
in CPython the operator of a ``BinOp``/``UnaryOp`` is a plain leaf that
``ast.walk`` never yields, so a walk-based checker silently never sees it.

Name resolution is **case sensitive** (``X`` and ``x`` are different SBML
symbols).  ``time`` is the independent variable: it is never a user symbol, is
always reported by :func:`symbols`, and must be supplied explicitly in the
environment -- there is no implicit ``time = 0``.
"""

from __future__ import annotations

import ast
import math
import re

MAX_EXPR_LEN = 4096
MAX_NODES = 512

ALLOWED_FUNCTIONS = ("exp", "log", "ln", "sqrt", "sin", "cos", "abs", "pow")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: ``time`` plus the whitelisted function names.  Exposed for ``modeling``.
_RESERVED = frozenset({"time", *ALLOWED_FUNCTIONS})

_MATH = {
    "exp": math.exp,
    "log": math.log,   # natural logarithm, per our semantics
    "ln": math.log,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "abs": abs,
    "pow": math.pow,
}

# SBML/roadrunner built-ins we can emit.  ``log``/``ln`` both become ``ln``.
_SBML_FUNCTIONS = frozenset({"exp", "ln", "sqrt", "sin", "cos", "abs", "pow"})

_BIN_OPS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Pow: "^",
}
_UNARY_OPS = {ast.UAdd: "+", ast.USub: "-"}

_REJECTED = (
    ast.Attribute,
    ast.Subscript,
    ast.Starred,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.IfExp,
    ast.Lambda,
    ast.Dict,
    ast.Set,
    ast.List,
    ast.Tuple,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.NamedExpr,
    ast.Compare,
    ast.BoolOp,
    ast.Slice,
    ast.comprehension,
    ast.arg,
    ast.keyword,
    ast.arguments,
)


class ExpressionError(ValueError):
    """Raised for any rejected expression (shape, size, operator or symbol)."""


# ---------------------------------------------------------------------------
# parsing + recursive validation
# ---------------------------------------------------------------------------

def _parse(text: str) -> ast.Expression:
    if not isinstance(text, str):
        raise ExpressionError(f"expression must be str, got {type(text).__name__}")
    if not text.strip():
        raise ExpressionError("expression is empty")
    if len(text) > MAX_EXPR_LEN:
        raise ExpressionError(f"expression too long: {len(text)} > {MAX_EXPR_LEN}")
    candidate = text.replace("^", "**")
    try:
        tree = ast.parse(candidate, mode="eval")
    except (SyntaxError, ValueError) as exc:  # ValueError covers null bytes
        raise ExpressionError(f"cannot parse expression: {getattr(exc, 'msg', exc)}") from exc
    count = 0
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        count += 1
        if count > MAX_NODES:
            raise ExpressionError(f"expression too complex: more than {MAX_NODES} AST nodes")
        stack.extend(ast.iter_child_nodes(node))
    return tree


def _allowed_set(allowed_symbols: object) -> set[str] | None:
    """Normalize ``allowed_symbols`` to a case-sensitive set.

    ``None`` means "shape check only" (used by :func:`symbols`).
    """
    if allowed_symbols is None:
        return None
    if isinstance(allowed_symbols, str):
        allowed = {allowed_symbols}
    elif isinstance(allowed_symbols, (set, frozenset, list, tuple)):
        allowed = set(allowed_symbols)
    else:
        raise ExpressionError(
            "allowed_symbols must be str, iterable of str, or None, got "
            f"{type(allowed_symbols).__name__}"
        )
    for symbol in allowed:
        if not isinstance(symbol, str):
            raise ExpressionError(f"allowed_symbols entries must be str, got {symbol!r}")
    return allowed


def _check(node: ast.AST, allowed: set[str] | None) -> None:
    """Recursively validate ``node``; never relies on ``ast.walk``."""
    if isinstance(node, ast.Expression):
        _check(node.body, allowed)
        return

    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExpressionError(f"only numeric constants are allowed, got {value!r}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ExpressionError("non-finite numeric constant")
        return

    if isinstance(node, ast.Name):
        name = node.id
        if not _IDENT.match(name):
            raise ExpressionError(f"invalid identifier {name!r}")
        if name in _RESERVED:
            # a function name is only valid in call position
            if name in ALLOWED_FUNCTIONS:
                raise ExpressionError(
                    f"function {name!r} may only be used as a call, e.g. {name}(...)"
                )
            return  # ``time``
        if allowed is None:
            return
        if name not in allowed:
            raise ExpressionError(f"undeclared symbol {name!r}")
        return

    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN_OPS:
            raise ExpressionError(f"unsupported binary operator {type(node.op).__name__}")
        _check(node.left, allowed)
        _check(node.right, allowed)
        return

    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY_OPS:
            raise ExpressionError(f"unsupported unary operator {type(node.op).__name__}")
        _check(node.operand, allowed)
        return

    if isinstance(node, ast.Call):
        func = node.func
        if not isinstance(func, ast.Name) or func.id not in ALLOWED_FUNCTIONS:
            raise ExpressionError(
                f"only the functions {list(ALLOWED_FUNCTIONS)} may be called"
            )
        if node.keywords:
            raise ExpressionError("keyword arguments are not allowed in calls")
        for arg in node.args:
            _check(arg, allowed)
        return

    if isinstance(node, (ast.Load, ast.Store, ast.Del)):
        return

    if isinstance(node, _REJECTED):
        raise ExpressionError(f"unsupported construct: {type(node).__name__}")
    raise ExpressionError(f"unsupported node: {type(node).__name__}")


def parse_expression(text: str, allowed_symbols: object = None) -> ast.Expression:
    """Parse ``text`` and return a validated Python AST (``ast.Expression``)."""
    tree = _parse(text)
    _check(tree, _allowed_set(allowed_symbols))
    return tree


def symbols(text: str) -> set[str]:
    """Return the referenced identifier names (``time`` included, functions not)."""
    tree = parse_expression(text, allowed_symbols=None)
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            if node.id not in ALLOWED_FUNCTIONS:
                found.add(node.id)
            return
        if isinstance(node, ast.Call):
            for arg in node.args:
                visit(arg)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


# ---------------------------------------------------------------------------
# recursive interpretation (no eval / exec)
# ---------------------------------------------------------------------------

def _as_number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpressionError(f"{where} must map to a finite number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ExpressionError(f"{where} must be finite, got {value!r}")
    return number


def _resolve(name: str, environment: dict, where: str) -> float:
    if name not in environment:
        raise ExpressionError(f"{where}: undefined symbol {name!r}")
    return _as_number(environment[name], f"symbol {name!r}")


def _guard(value: float, op: str) -> float:
    if not math.isfinite(value):
        raise ExpressionError(f"non-finite result of {op!r}")
    return value


def _eval(node: ast.expr, environment: dict) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return _resolve(node.id, environment, "evaluate")
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, environment)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, environment)
        right = _eval(node.right, environment)
        kind = type(node.op)
        if kind is ast.Add:
            return _guard(left + right, "+")
        if kind is ast.Sub:
            return _guard(left - right, "-")
        if kind is ast.Mult:
            return _guard(left * right, "*")
        if kind is ast.Div:
            if right == 0.0:
                raise ExpressionError("division by zero")
            return _guard(left / right, "/")
        try:
            return _guard(left ** right, "^")
        except OverflowError as exc:
            raise ExpressionError(f"overflow in '^': {exc}") from exc
    if isinstance(node, ast.Call):
        name = node.func.id
        args = [_eval(arg, environment) for arg in node.args]
        try:
            return _guard(float(_MATH[name](*args)), name)
        except ExpressionError:
            raise
        except (ValueError, OverflowError, ZeroDivisionError, TypeError) as exc:
            raise ExpressionError(f"{name}{tuple(args)!r} is not defined: {exc}") from exc
    raise ExpressionError(f"unsupported node {type(node).__name__}")


def evaluate(text: str, environment: object = None) -> float:
    """Parse and recursively interpret ``text`` against ``environment``.

    ``environment`` maps names to finite numbers (case sensitive).  ``time``
    must be provided explicitly; every referenced name must resolve.
    """
    env = dict(environment) if environment is not None else {}
    tree = parse_expression(text, allowed_symbols=set(env))
    return _eval(tree.body, env)


# ---------------------------------------------------------------------------
# SBML formula generation
# ---------------------------------------------------------------------------

def to_sbml_formula(text: str) -> str:
    """Compile a restricted expression into an SBML/roadrunner formula string.

    Every binary and unary operation is parenthesized so libSBML's infix
    parser cannot re-associate the expression (``(k - d) * x`` must not become
    ``k - d * x``).  ``log``/``ln`` become ``ln``.
    """
    tree = parse_expression(text, allowed_symbols=None)

    def gen(node: ast.expr) -> str:
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                raise ExpressionError("boolean constants cannot be written as SBML")
            if isinstance(value, int):
                return str(value)
            if not math.isfinite(float(value)):
                raise ExpressionError("non-finite constant cannot be written as SBML")
            return repr(float(value))
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.UnaryOp):
            return f"({_UNARY_OPS[type(node.op)]}{gen(node.operand)})"
        if isinstance(node, ast.BinOp):
            return f"({gen(node.left)} {_BIN_OPS[type(node.op)]} {gen(node.right)})"
        if isinstance(node, ast.Call):
            name = "ln" if node.func.id in ("log", "ln") else node.func.id
            if name not in _SBML_FUNCTIONS:
                raise ExpressionError(f"function {node.func.id!r} cannot be written as SBML")
            return f"{name}({', '.join(gen(arg) for arg in node.args)})"
        raise ExpressionError(f"unsupported node {type(node).__name__}")

    return gen(tree.body)

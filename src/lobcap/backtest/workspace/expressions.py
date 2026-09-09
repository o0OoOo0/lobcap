"""Bounded, Decimal-only signal language over explicitly observable scalars."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from functools import lru_cache
import re

VERSION = "signal-v1"
OBSERVABLES = frozenset({"bid", "ask", "mid", "microprice", "tick", "imbalance",
    "spread_ticks", "volatility_bps", "trade_imbalance", "trade_volume", "inventory", "max_inventory"})
COEFFICIENTS = frozenset({"signal_strength", "inventory_skew_ticks"})
FUNCTIONS = frozenset({"abs", "min", "max", "clip"})
RESERVED = OBSERVABLES | COEFFICIENTS | FUNCTIONS


class ExpressionError(ValueError):
    """Invalid signal syntax or arithmetic; never silently substitute zero."""


class FeatureUnavailable(ExpressionError):
    """An otherwise valid expression needs a not-yet-observed feature."""


def parameter_values(values):
    pairs = list(values.items()) if isinstance(values, dict) else list(values)
    result = {}
    for name, value in pairs:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
            raise ExpressionError("Parameter names must be simple identifiers")
        if name in RESERVED or name in result:
            raise ExpressionError(f"Reserved or duplicate parameter: {name}")
        number = _finite(value)
        result[name] = number
    if len(result) > 32:
        raise ExpressionError("At most 32 signal parameters are supported")
    return tuple(sorted(result.items()))


def _finite(value):
    try:
        if isinstance(value, bool):
            raise ExpressionError("Boolean values are not signal numbers")
        number = value if isinstance(value, Decimal) else Decimal(str(value))
        if not number.is_finite() or (number and abs(number.adjusted()) > 100):
            raise ExpressionError("Signal number is nonfinite or outside exponent ±100")
        return number
    except (DecimalException, TypeError, ValueError) as exc:
        raise ExpressionError(f"Invalid signal number: {value}") from exc


@dataclass(frozen=True)
class CompiledExpression:
    source: str
    tree: ast.Expression
    names: frozenset[str]

    def evaluate(self, values):
        environment = {}
        for name in self.names:
            if name not in values or values[name] is None:
                raise FeatureUnavailable(f"feature_unavailable:{name}")
            environment[name] = _finite(values[name])

        def visit(node):
            if isinstance(node, ast.Constant):
                return Decimal(ast.get_source_segment(self.source, node).replace("_", ""))
            if isinstance(node, ast.Name):
                return environment[node.id]
            if isinstance(node, ast.UnaryOp):
                value = visit(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Add): value = left + right
                elif isinstance(node.op, ast.Sub): value = left - right
                elif isinstance(node.op, ast.Mult): value = left * right
                else: value = left / right
                return _finite(value)
            args = [visit(arg) for arg in node.args]
            if node.func.id == "abs": return abs(args[0])
            if node.func.id == "min": return min(args)
            if node.func.id == "max": return max(args)
            if args[1] > args[2]: raise ExpressionError("clip lower bound exceeds upper bound")
            return min(max(args[0], args[1]), args[2])

        try:
            with localcontext() as context:
                context.prec = 50
                return _finite(visit(self.tree.body))
        except DecimalException as exc:
            raise ExpressionError(f"Signal arithmetic failed: {type(exc).__name__}") from exc


def compile_expression(source, parameter_names=()):
    return _compile(source, tuple(sorted(parameter_names)))


@lru_cache(maxsize=256)
def _compile(source, parameter_names):
    if not isinstance(source, str) or not source.strip() or len(source) > 1000:
        raise ExpressionError("Signal must contain 1–1000 characters")
    parameter_values({name: Decimal(0) for name in parameter_names})
    source = source.strip()
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ExpressionError("Invalid signal expression syntax") from exc
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ExpressionError("Signal exceeds 128 syntax nodes")
    names = set()

    def check(node, depth=0):
        if depth > 16: raise ExpressionError("Signal exceeds nesting depth 16")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            literal = ast.get_source_segment(source, node).replace("_", "")
            if not re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", literal):
                raise ExpressionError("Only decimal numeric literals are allowed")
            number = _finite(literal)
            if len(number.as_tuple().digits) > 28: raise ExpressionError("Literal exceeds 28 significant digits")
        elif isinstance(node, ast.Name) and node.id in OBSERVABLES | COEFFICIENTS | set(parameter_names):
            names.add(node.id)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            check(node.operand, depth + 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            check(node.left, depth + 1); check(node.right, depth + 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            count = len(node.args)
            if (node.func.id == "abs" and count != 1) or (node.func.id == "clip" and count != 3) or (node.func.id in {"min", "max"} and not 2 <= count <= 16):
                raise ExpressionError("Invalid signal function argument count")
            for arg in node.args: check(arg, depth + 1)
        else:
            raise ExpressionError(f"Unsupported signal syntax or name: {ast.dump(node)[:120]}")
    check(tree.body)
    return CompiledExpression(source, tree, frozenset(names))

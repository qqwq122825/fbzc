"""Fail-closed evaluator for the proven pure subset of the Bloks Lispy DSL.

This module is intentionally offline: it has no HTTP, browser, credential or
account integration. Callers provide an already parsed AST and explicit tables.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableSequence, Sequence


@dataclass
class Ref:
    value: Any


@dataclass(frozen=True)
class Script:
    ast: Any


def truth(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def equal(left: Any, right: Any) -> bool:
    if left is right:
        return True
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    lt = left is True or (type(left) is int and left == 1)
    rt = right is True or (type(right) is int and right == 1)
    lf = left is False or (type(left) is int and left == 0)
    rf = right is False or (type(right) is int and right == 0)
    return (lt and rt) or (lf and rf)


def numeric_bools(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return [numeric_bools(item) for item in value]
    if isinstance(value, dict):
        return {key: numeric_bools(item) for key, item in value.items()}
    return value


class Evaluator:
    """Evaluate explicitly approved pure operators with hard budgets."""

    def __init__(
        self,
        variables: Mapping[str, Any] | None = None,
        scripts: Mapping[str, Script | Callable[..., Any]] | None = None,
        *,
        step_budget: int = 10_000,
        depth_budget: int = 200,
    ) -> None:
        self.variables = dict(variables or {})
        self.scripts = dict(scripts or {})
        self.frames: MutableSequence[list[Any]] = [[]]
        self.count = 0
        self.step_budget = step_budget
        self.depth_budget = depth_budget

    def _call(self, function: Any, arguments: Sequence[Any]) -> Any:
        if isinstance(function, Script):
            self.frames.append(list(arguments))
            try:
                return self.eval(function.ast)
            finally:
                self.frames.pop()
        if callable(function):
            return function(*arguments)
        raise ValueError("Apply requires a decoded pure script/function")

    def eval(self, node: Any, depth: int = 0) -> Any:
        self.count += 1
        if self.count > self.step_budget or depth > self.depth_budget:
            raise ValueError("Evaluation budget exceeded")
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            symbol = node["symbol"]
            if symbol in ("null", "true", "false"):
                return {"null": None, "true": True, "false": False}[symbol]
            if re.fullmatch(r"-?\d+(?:\.\d+)?", symbol):
                return json.loads(symbol)
            raise ValueError(f"Unsupported atom: {symbol}")
        if not isinstance(node, list) or not node or not isinstance(node[0], dict):
            raise ValueError("Unsupported AST")

        operator = node[0]["symbol"]
        arguments = node[1:]
        evaluate = lambda value: self.eval(value, depth + 1)

        if operator == "e2a":  # If
            if truth(evaluate(arguments[0])):
                return evaluate(arguments[1])
            return evaluate(arguments[2]) if len(arguments) == 3 else None
        if operator == "e1z":  # Coalesce
            for argument in arguments:
                value = evaluate(argument)
                if value is not None:
                    return value
            return None
        if operator == "e24":  # FuncConst
            if len(arguments) != 1:
                raise ValueError("FuncConst arity")

            def closure(*values: Any) -> Any:
                self.frames.append(list(values))
                try:
                    return evaluate(arguments[0])
                finally:
                    self.frames.pop()

            return closure
        if operator == "e25":  # GetArg
            index = evaluate(arguments[0])
            return self.frames[-1][index] if index < len(self.frames[-1]) else None
        if operator == "e2e":  # SetArg
            index = evaluate(arguments[0])
            value = evaluate(arguments[1])
            while len(self.frames[-1]) <= index:
                self.frames[-1].append(None)
            self.frames[-1][index] = value
            return None
        if operator == "e2c":  # Pattern
            return ("case", evaluate(arguments[0]), arguments[1])
        if operator == "e22":  # Default
            return ("default", arguments[0])
        if operator == "e2b":  # Match
            target = evaluate(arguments[0])
            cases = evaluate(arguments[1])
            default = evaluate(arguments[2]) if len(arguments) > 2 else None
            selected = None
            for _, case, body in cases:
                if equal(case, target):
                    selected = body
                    break
            if selected is None and default:
                selected = default[1]
            if not isinstance(selected, list) or selected[0] != {"symbol": "e24"}:
                raise ValueError("Match missing function branch")
            return evaluate(selected[1])

        values = [evaluate(argument) for argument in arguments]
        if operator == "dkc":  # ArrayMake
            return values
        if operator == "f4i":  # MapMake
            keys, items = values
            if len(keys) != len(items):
                raise ValueError("Mismatched map shape")
            return dict(zip(keys, items))
        if operator == "fma":  # JsonEncode
            return json.dumps(numeric_bools(values[0]), ensure_ascii=False, separators=(",", ":"))
        if operator == "fhy":  # RefMake
            return Ref(values[0])
        if operator == "fhz":  # RefRead
            if not isinstance(values[0], Ref):
                raise ValueError("RefRead needs a Ref")
            return values[0].value
        if operator == "dnt":  # GetVariable2
            if values[0] not in self.variables:
                raise ValueError("Unbound variable")
            return self.variables[values[0]]
        if operator == "dnk":  # GetScript
            if values[0] not in self.scripts:
                raise ValueError("Unbound script")
            return self.scripts[values[0]]
        if operator == "e1w":  # Apply
            return self._call(values[0], values[1:])
        if operator == "e2f":  # TakeLast
            return values[-1] if values else None
        if operator == "eu1":  # Equality
            return equal(*values)
        if operator == "fme":  # String/list length
            value = values[0]
            return len(value.encode("utf-16-le")) // 2 if isinstance(value, str) else len(value)
        if operator == "eud":  # i64ConstNumber
            value = int(str(values[0]))
            if not -(2**63) <= value < 2**63:
                raise ValueError("i64 range")
            return str(value)
        raise ValueError(f"Unapproved operator: {operator}")


def self_test() -> None:
    assert truth([]) and truth({}) and not truth("") and not truth(None)
    assert equal(None, None) and not equal(None, "") and not equal("", False)
    assert equal(0, False) and equal(1, True)
    assert numeric_bools({"a": True, "b": [False]}) == {"a": 1, "b": [0]}

    symbol = lambda name: {"symbol": name}
    evaluator = Evaluator({"x": 7})
    assert evaluator.eval([symbol("dnt"), "x"]) == 7
    assert evaluator.eval([symbol("dkc"), symbol("true"), symbol("false")]) == [True, False]
    encoded = evaluator.eval([symbol("fma"), [symbol("dkc"), symbol("true"), symbol("false")]])
    assert encoded == "[1,0]"
    try:
        evaluator.eval([symbol("network_request"), "https://example.invalid"])
    except ValueError as error:
        assert "Unapproved operator" in str(error)
    else:
        raise AssertionError("unknown operators must fail closed")


if __name__ == "__main__":
    self_test()
    print("bloks_dsl_eval: self-test passed")

"""
valpha_eval.py
---------------
Evaluates a parsed .valpha Strategy (valpha_lang.Strategy) inside the two
Python engines, so the engines no longer carry hand-written per-strategy
logic:

    evaluate_naive(strategy, close_list)        -> (columns, buy, sell)   plain lists
    evaluate_vectorized(strategy, close_series) -> (columns, buy, sell)   pandas Series

`columns` maps every user-defined series name (the indicators_signals.csv
columns, in definition order) to its values. The naive evaluator walks the
expression tree with explicit loops (no pandas/NumPy imported on that
path); the vectorized one uses pandas/NumPy bulk operations. Neither does
common-subexpression elimination or fusion on purpose -- they are the
baselines the compiled engine is measured against.

Semantics (identical in the compiled engine): missing values (None / NaN)
propagate through arithmetic, a comparison involving a missing value is
False, and division by zero yields a missing value.
"""

import indicators_naive as N
from valpha_lang import const_eval

_ARITH = {"+", "-", "*", "/"}
_CMP = {"<", ">", "<=", ">=", "==", "!="}


# ---------------------------------------------------------------------
# naive (pure python lists)
# ---------------------------------------------------------------------

def _apply(op, x, y):
    if x is None or y is None:
        return None
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    return None if y == 0 else x / y


class _NaiveEvaluator:
    def __init__(self, strategy, close):
        self.s = strategy
        self.n = len(close)
        self.env = {"close": close}

    def as_list(self, v):
        return v if isinstance(v, list) else [v] * self.n

    def numeric(self, e):
        c = const_eval(e, self.s.consts)
        if c is not None:
            return c
        kind = e[0]
        if kind == "id":
            return self.env[e[1]]
        if kind == "neg":
            return [None if v is None else -v for v in self.as_list(self.numeric(e[1]))]
        if kind == "bin" and e[1] in _ARITH:
            a, b = self.numeric(e[2]), self.numeric(e[3])
            op = e[1]
            if isinstance(a, list) and isinstance(b, list):
                return [_apply(op, x, y) for x, y in zip(a, b)]
            if isinstance(a, list):
                return [_apply(op, x, b) for x in a]
            return [_apply(op, a, y) for y in b]
        if kind == "call":
            src = self.as_list(self.numeric(e[2][0]))
            if e[1] == "ABS":
                return N.ABS(src)
            w = int(round(const_eval(e[2][1], self.s.consts)))
            return getattr(N, e[1])(src, w)
        raise ValueError(f"cannot evaluate {e!r} as a number")

    def boolean(self, e):
        kind = e[0]
        if kind == "id":
            return self.boolean(self.s.bools[e[1]])
        if kind == "call":
            a = self.as_list(self.numeric(e[2][0]))
            b = self.as_list(self.numeric(e[2][1]))
            return getattr(N, e[1])(a, b)
        op = e[1]
        if op in _CMP:
            f = N.CMP_FUNCS[op]
            a, b = self.as_list(self.numeric(e[2])), self.as_list(self.numeric(e[3]))
            return [f(x, y) for x, y in zip(a, b)]
        a, b = self.boolean(e[2]), self.boolean(e[3])
        if op == "AND":
            return [x and y for x, y in zip(a, b)]
        return [x or y for x, y in zip(a, b)]


def evaluate_naive(strategy, close):
    ev = _NaiveEvaluator(strategy, close)
    columns = {}
    for name, expr in strategy.series_defs:
        v = ev.numeric(expr)
        ev.env[name] = v
        columns[name] = v
    n = len(close)
    buy = [False] * n
    sell = [False] * n
    for kind, cond in strategy.rules:
        sig = ev.boolean(cond)
        target = buy if kind == "BUY" else sell
        for i, flag in enumerate(sig):
            if flag:
                target[i] = True
    return columns, buy, sell


# ---------------------------------------------------------------------
# vectorized (pandas / NumPy)
# ---------------------------------------------------------------------

class _VecEvaluator:
    def __init__(self, strategy, close):
        import pandas as pd
        import indicators_vectorized as V
        self.pd, self.V = pd, V
        self.s = strategy
        self.index = close.index
        self.env = {"close": close}

    def as_series(self, v):
        if isinstance(v, self.pd.Series):
            return v
        return self.pd.Series(float(v), index=self.index)

    def as_bool_series(self, v):
        if isinstance(v, self.pd.Series):
            return v.astype(bool)
        return self.pd.Series(bool(v), index=self.index)

    def numeric(self, e):
        c = const_eval(e, self.s.consts)
        if c is not None:
            return c
        kind = e[0]
        if kind == "id":
            return self.env[e[1]]
        if kind == "neg":
            return -self.numeric(e[1])
        if kind == "bin" and e[1] in _ARITH:
            a, b = self.numeric(e[2]), self.numeric(e[3])
            op = e[1]
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if isinstance(b, self.pd.Series):
                b = b.where(b != 0)          # division by zero -> missing
            return a / b
        if kind == "call":
            src = self.as_series(self.numeric(e[2][0]))
            if e[1] == "ABS":
                return self.V.ABS(src)
            w = int(round(const_eval(e[2][1], self.s.consts)))
            return getattr(self.V, e[1])(src, w)
        raise ValueError(f"cannot evaluate {e!r} as a number")

    def boolean(self, e):
        kind = e[0]
        if kind == "id":
            return self.boolean(self.s.bools[e[1]])
        if kind == "call":
            a = self.as_series(self.numeric(e[2][0]))
            b = self.as_series(self.numeric(e[2][1]))
            return getattr(self.V, e[1])(a, b)
        op = e[1]
        if op in _CMP:
            return self.as_bool_series(self.V.CMP_FUNCS[op](self.numeric(e[2]), self.numeric(e[3])))
        a, b = self.boolean(e[2]), self.boolean(e[3])
        return (a & b) if op == "AND" else (a | b)


def evaluate_vectorized(strategy, close):
    ev = _VecEvaluator(strategy, close)
    columns = {}
    for name, expr in strategy.series_defs:
        v = ev.as_series(ev.numeric(expr))
        ev.env[name] = v
        columns[name] = v
    buy = ev.pd.Series(False, index=close.index)
    sell = ev.pd.Series(False, index=close.index)
    for kind, cond in strategy.rules:
        sig = ev.boolean(cond)
        if kind == "BUY":
            buy = buy | sig
        else:
            sell = sell | sig
    return columns, buy, sell

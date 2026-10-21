"""
indicators_vectorized.py
--------------------------
Vectorized (pandas / NumPy) implementations of the same DSL indicator
functions as indicators_naive.py -- the second baseline the report
benchmarks against (Section 3.4 / Section 5 of the IO spec). No explicit
python-level for-loops over bars are used for the indicator math (the one
exception is the w-element EMA seed), everything else is bulk
pandas/NumPy.

All functions take and return a pandas.Series so they compose with the
DataFrames produced by data_loader.load_vectorized(). They follow the same
numerical contract as the naive and compiled engines (tolerant
comparisons, missing values never match, flat-window RSI = 0, EMA seeded
with the mean of its first w values) -- see indicators_naive.py.
"""

import numpy as np
import pandas as pd

EPS = 1e-9


def _tol(a, b):
    return EPS * np.maximum(1.0, np.maximum(np.abs(a), np.abs(b)))


# Tolerant comparisons; NaN (missing) compares False. a and b may be
# Series or plain numbers.
def cmp_lt(a, b):
    return (b - a) > _tol(a, b)


def cmp_gt(a, b):
    return (a - b) > _tol(a, b)


def cmp_le(a, b):
    return (a - b) <= _tol(a, b)


def cmp_ge(a, b):
    return (b - a) <= _tol(a, b)


def cmp_eq(a, b):
    return np.abs(a - b) <= _tol(a, b)


def cmp_ne(a, b):
    return np.abs(a - b) > _tol(a, b)


CMP_FUNCS = {"<": cmp_lt, ">": cmp_gt, "<=": cmp_le, ">=": cmp_ge, "==": cmp_eq, "!=": cmp_ne}


def SMA(series: pd.Series, w: int) -> pd.Series:
    return series.rolling(window=w, min_periods=w).mean()


def EMA(series: pd.Series, w: int) -> pd.Series:
    # NOTE: pandas' ewm(adjust=False) starts its recursion from the *first
    # value*, not from the mean of the first w values, so calling it directly
    # gives different numbers than the naive/compiled EMA for the first few
    # dozen bars. Build the seed explicitly (mean of the first w present
    # values, summed in order), blank everything before it, and let ewm run
    # the recursion from there.
    vals = series.to_numpy(dtype=float)
    n = len(vals)
    out = pd.Series(np.nan, index=series.index)
    present = np.flatnonzero(~np.isnan(vals))
    if len(present) < w:
        return out
    seed = 0.0
    for j in present[:w]:
        seed += vals[j]
    seed /= w
    seed_pos = int(present[w - 1])
    start = np.full(n, np.nan)
    start[seed_pos] = seed
    start[seed_pos + 1:] = vals[seed_pos + 1:]
    res = pd.Series(start, index=series.index).ewm(
        alpha=2.0 / (w + 1), adjust=False, ignore_na=True).mean()
    res[np.isnan(vals)] = np.nan
    return res


def RSI(series: pd.Series, w: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=w, min_periods=w).mean()
    avg_loss = loss.rolling(window=w, min_periods=w).mean()

    tol = EPS * series.abs().clip(lower=1.0)
    avg_loss_zero = avg_loss <= tol                       # False where missing
    flat = (avg_gain <= tol) & avg_loss_zero

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.where(~avg_loss_zero, pd.Series(np.where(flat, 0.0, 100.0), index=series.index))


def STDEV(series: pd.Series, w: int) -> pd.Series:
    return series.rolling(window=w, min_periods=w).std(ddof=0)


def LAG(series: pd.Series, n: int) -> pd.Series:
    return series.shift(n)


def HIGHEST(series: pd.Series, w: int) -> pd.Series:
    return series.rolling(window=w, min_periods=w).max()


def LOWEST(series: pd.Series, w: int) -> pd.Series:
    return series.rolling(window=w, min_periods=w).min()


def ABS(series: pd.Series) -> pd.Series:
    return series.abs()


def CROSSOVER(a: pd.Series, b: pd.Series) -> pd.Series:
    return cmp_le(a.shift(1), b.shift(1)) & cmp_gt(a, b)


def CROSSUNDER(a: pd.Series, b: pd.Series) -> pd.Series:
    return cmp_ge(a.shift(1), b.shift(1)) & cmp_lt(a, b)

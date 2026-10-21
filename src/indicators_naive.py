"""
indicators_naive.py
--------------------
Naive, pure-Python (loop-based) implementations of the DSL's built-in
indicator functions, as defined in VectorAlpha_IO_Spec.docx section 1:

    SMA(series, w)        - simple moving average
    EMA(series, w)        - exponential moving average, factor 2/(w+1),
                            seeded with the mean of the first w values
    RSI(series, w)        - relative strength index (0-100)
    STDEV(series, w)      - rolling (population) standard deviation
    LAG(series, n)        - the value n bars ago
    HIGHEST(series, w)    - rolling maximum over the last w values (incl. current)
    LOWEST(series, w)     - rolling minimum over the last w values (incl. current)
    ABS(series)           - absolute value
    CROSSOVER(a, b)       - True only where a crosses from <= b to > b
    CROSSUNDER(a, b)      - True only where a crosses from >= b to < b

No pandas / NumPy is used on purpose -- this is the "how a naive Python
backtest is normally written" baseline the report benchmarks against.
Every function takes and returns a plain python list the same length as
its input, with None marking a missing value (window not full yet, or an
upstream value was missing -- mirrors the "---" cells in the IO spec
tables). Inputs may themselves contain None, so indicators can be nested:
a windowed indicator is None until its whole window is present.

Numerical contract shared with the pandas and compiled engines
(see README, "Numerical contract"):
  * comparisons are tolerant, 1e-9 * max(1, |a|, |b|) (relative for large values,
    an absolute floor near zero): cmp_lt/cmp_gt/... below.
    Incremental and recomputed indicators may differ in the last few bits,
    and a tie in exact arithmetic must not flip a signal because of that.
  * any comparison involving a missing value is False.
  * RSI of a flat window (no gains, no losses) is 0; average loss ~ 0 with
    gains is 100 ("~ 0" is 1e-9 * max(1, |price|)).
"""

import math

EPS = 1e-9


def _tol(a, b):
    return EPS * max(1.0, abs(a), abs(b))


def cmp_lt(a, b):
    return a is not None and b is not None and (b - a) > _tol(a, b)


def cmp_gt(a, b):
    return a is not None and b is not None and (a - b) > _tol(a, b)


def cmp_le(a, b):
    return a is not None and b is not None and (a - b) <= _tol(a, b)


def cmp_ge(a, b):
    return a is not None and b is not None and (b - a) <= _tol(a, b)


def cmp_eq(a, b):
    return a is not None and b is not None and abs(a - b) <= _tol(a, b)


def cmp_ne(a, b):
    return a is not None and b is not None and abs(a - b) > _tol(a, b)


CMP_FUNCS = {"<": cmp_lt, ">": cmp_gt, "<=": cmp_le, ">=": cmp_ge, "==": cmp_eq, "!=": cmp_ne}


def SMA(series, w):
    n = len(series)
    out = [None] * n
    for i in range(w - 1, n):
        total = 0.0
        ok = True
        for j in range(i - w + 1, i + 1):
            v = series[j]
            if v is None:
                ok = False
                break
            total += v
        if ok:
            out[i] = total / w
    return out


def EMA(series, w):
    # State machine identical to the compiled kernel: seed = mean of the
    # first w present values, then value = x*alpha + prev*(1-alpha). A
    # missing value yields a missing output and leaves the state alone.
    n = len(series)
    out = [None] * n
    alpha = 2.0 / (w + 1)
    count = 0
    seed_sum = 0.0
    prev = None
    for i in range(n):
        x = series[i]
        if x is None:
            continue
        if count < w:
            seed_sum += x
            count += 1
            if count == w:
                prev = seed_sum / w
                out[i] = prev
        else:
            prev = x * alpha + prev * (1 - alpha)
            out[i] = prev
    return out


def RSI(series, w):
    n = len(series)
    out = [None] * n
    for i in range(w, n):
        gains = 0.0
        losses = 0.0
        ok = True
        for j in range(i - w + 1, i + 1):
            cur, prv = series[j], series[j - 1]
            if cur is None or prv is None:
                ok = False
                break
            change = cur - prv
            if change > 0:
                gains += change
            elif change < 0:
                losses += -change
        if not ok:
            continue
        avg_gain = gains / w
        avg_loss = losses / w
        tol = EPS * max(1.0, abs(series[i]))
        if avg_loss <= tol:
            out[i] = 0.0 if avg_gain <= tol else 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def STDEV(series, w):
    n = len(series)
    out = [None] * n
    for i in range(w - 1, n):
        window = series[i - w + 1:i + 1]
        if any(v is None for v in window):
            continue
        mean = sum(window) / w
        var = sum((x - mean) ** 2 for x in window) / w
        out[i] = math.sqrt(var)
    return out


def LAG(series, n):
    out = [None] * len(series)
    for i in range(n, len(series)):
        out[i] = series[i - n]
    return out


def _rolling_extreme(series, w, pick):
    n = len(series)
    out = [None] * n
    for i in range(w - 1, n):
        window = series[i - w + 1:i + 1]
        if any(v is None for v in window):
            continue
        out[i] = pick(window)
    return out


def HIGHEST(series, w):
    return _rolling_extreme(series, w, max)


def LOWEST(series, w):
    return _rolling_extreme(series, w, min)


def ABS(series):
    return [None if v is None else abs(v) for v in series]


def CROSSOVER(a, b):
    n = len(a)
    out = [False] * n
    for i in range(1, n):
        if cmp_le(a[i - 1], b[i - 1]) and cmp_gt(a[i], b[i]):
            out[i] = True
    return out


def CROSSUNDER(a, b):
    n = len(a)
    out = [False] * n
    for i in range(1, n):
        if cmp_ge(a[i - 1], b[i - 1]) and cmp_lt(a[i], b[i]):
            out[i] = True
    return out

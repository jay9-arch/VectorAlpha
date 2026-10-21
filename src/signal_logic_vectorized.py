"""
signal_logic_vectorized.py
-----------------------------
Vectorized (pandas/NumPy) BUY/SELL signal generation, mirroring
signal_logic_naive.py exactly in behaviour but expressed with bulk
pandas operations instead of explicit bar-by-bar loops.

Returns (buy_signals, sell_signals): two pandas boolean Series.
"""

from indicators_vectorized import CROSSOVER, CROSSUNDER


def sma_cross_rsi(ind, params):
    fast = ind[params["fast"]]
    slow = ind[params["slow"]]
    rsi = ind[params["rsi"]]
    rsi_max = params["rsi_max"]

    cross_up = CROSSOVER(fast, slow)
    cross_down = CROSSUNDER(fast, slow)

    buy = cross_up & (rsi < rsi_max)
    sell = cross_down
    return buy.fillna(False), sell.fillna(False)


def band_breakout(ind, params):
    close = ind["close"]
    ema = ind[params["ema"]]
    std = ind[params["std"]]

    upper = ema + std
    lower = ema - std
    prev_upper = upper.shift(1)
    prev_lower = lower.shift(1)
    prev_close = close.shift(1)

    buy = (prev_close <= prev_upper) & (close > upper)
    sell = (prev_close >= prev_lower) & (close < lower)
    return buy.fillna(False), sell.fillna(False)


LOGIC_FUNCS = {
    "sma_cross_rsi": sma_cross_rsi,
    "band_breakout": band_breakout,
}

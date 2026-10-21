"""
signal_logic_naive.py
-----------------------
Loop-based BUY/SELL signal generation for each strategy "logic" type
in strategies.py. Operates on plain python lists (output of
indicators_naive.py), consistent with the naive engine's no-library
philosophy.

Returns (buy_signals, sell_signals): two lists of booleans, same
length as the price series.
"""

from indicators_naive import CROSSOVER, CROSSUNDER


def sma_cross_rsi(ind, params):
    fast = ind[params["fast"]]
    slow = ind[params["slow"]]
    rsi = ind[params["rsi"]]
    rsi_max = params["rsi_max"]

    cross_up = CROSSOVER(fast, slow)
    cross_down = CROSSUNDER(fast, slow)

    n = len(fast)
    buy = [False] * n
    for i in range(n):
        if cross_up[i] and rsi[i] is not None and rsi[i] < rsi_max:
            buy[i] = True
    sell = cross_down
    return buy, sell


def band_breakout(ind, params):
    close = ind["close"]
    ema = ind[params["ema"]]
    std = ind[params["std"]]

    n = len(close)
    buy = [False] * n
    sell = [False] * n
    for i in range(1, n):
        if ema[i] is None or std[i] is None or ema[i - 1] is None or std[i - 1] is None:
            continue
        upper = ema[i] + std[i]
        lower = ema[i] - std[i]
        prev_upper = ema[i - 1] + std[i - 1]
        prev_lower = ema[i - 1] - std[i - 1]
        if close[i - 1] <= prev_upper and close[i] > upper:
            buy[i] = True
        if close[i - 1] >= prev_lower and close[i] < lower:
            sell[i] = True
    return buy, sell


LOGIC_FUNCS = {
    "sma_cross_rsi": sma_cross_rsi,
    "band_breakout": band_breakout,
}

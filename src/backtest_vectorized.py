"""
backtest_vectorized.py
------------------------
Vectorized backtest engine: indicator + signal computation is done in
bulk with pandas/NumPy (valpha_eval.evaluate_vectorized over
indicators_vectorized.py).
This is baseline (2) from the IO spec's Section 5 benchmark table
("pandas / NumPy").

The portfolio simulation step (walking the merged timeline, opening
and closing positions) is kept as a single sequential pass here too --
it is genuinely path-dependent (today's cash depends on yesterday's
trades) and pandas/NumPy give no real vectorization win over that step
without extra machinery, so a fair "pandas/NumPy baseline" still does
this part with a loop. The comparison this engine is meant to
demonstrate is specifically indicator computation, matching the IO
spec's framing ("VectorAlpha's advantage comes from domain-aware
optimizations pandas/NumPy do not perform automatically").
"""

import time

import pandas as pd

import valpha_eval


def run_backtest(strategy_cfg, tickers_data, verbose=False):
    """
    tickers_data: dict[ticker] -> pandas.DataFrame with columns [date, close]
                  (from data_loader.load_vectorized)
    Returns: (elapsed_seconds, results) -- same shape as backtest_naive.run_backtest
    """
    t0 = time.perf_counter()

    capital = strategy_cfg["initial_capital"]
    allocation = strategy_cfg["allocation"]
    spec = strategy_cfg["spec"]

    buy_signals = {}
    sell_signals = {}
    frames = {}
    indicator_cols = {}

    # --- 1 & 2: indicators + signals, per ticker, fully vectorized ---
    for tkr, df in tickers_data.items():
        df = df.copy()
        columns, buy, sell = valpha_eval.evaluate_vectorized(spec, df["close"])
        df["buy"] = buy.values
        df["sell"] = sell.values
        frames[tkr] = df
        indicator_cols[tkr] = columns
        buy_signals[tkr] = buy.tolist()
        sell_signals[tkr] = sell.tolist()

    # --- 3: sequential single-position portfolio simulation (path-dependent) ---
    # Pull each frame's date/close/buy/sell columns down to plain python
    # lists + a dict-based date->row-index map (exactly what the naive
    # engine uses). This keeps the simulation step's cost identical
    # between engines, so the benchmark isolates the thing that's
    # actually different: indicator computation, not date-lookup
    # bookkeeping.
    plain = {}
    for tkr, df in frames.items():
        dates_list = df["date"].tolist()
        plain[tkr] = {
            "dates": dates_list,
            "close": df["close"].tolist(),
            "buy": df["buy"].tolist(),
            "sell": df["sell"].tolist(),
            "index": {d: i for i, d in enumerate(dates_list)},
        }

    all_dates = sorted({d for p in plain.values() for d in p["dates"]})

    cash = capital
    open_positions = {}
    trades = []
    equity_curve = []

    for dt in all_dates:
        for tkr, p in plain.items():
            idx = p["index"].get(dt)
            if idx is None:
                continue
            price = p["close"][idx]
            if tkr not in open_positions and bool(p["buy"][idx]):
                trade_capital = allocation * capital
                shares = trade_capital / price
                open_positions[tkr] = {"entry_date": dt, "entry_px": price, "shares": shares}
                cash -= trade_capital
            elif tkr in open_positions and bool(p["sell"][idx]):
                pos = open_positions.pop(tkr)
                proceeds = pos["shares"] * price
                cash += proceeds
                cost = pos["shares"] * pos["entry_px"]
                pnl = proceeds - cost
                pnl_pct = pnl / cost * 100
                trades.append({
                    "ticker": tkr, "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
                    "exit_date": dt, "exit_px": price, "shares": pos["shares"],
                    "pnl": pnl, "pnl_pct": pnl_pct,
                })

        mtm = cash
        for tkr, pos in open_positions.items():
            idx = plain[tkr]["index"].get(dt)
            price = plain[tkr]["close"][idx] if idx is not None else pos["entry_px"]
            mtm += pos["shares"] * price
        equity_curve.append(mtm)

    final_value = equity_curve[-1] if equity_curve else capital
    elapsed = time.perf_counter() - t0

    if verbose:
        print(f"[vectorized] {len(tickers_data)} tickers, {len(trades)} trades, {elapsed:.4f}s")

    # Reshape into the same plain-dict-of-lists shape output_writer.py expects,
    # so both engines can share the exact same writer code.
    tickers_data_out = {}
    for tkr, df in frames.items():
        entry = {
            "dates": [d.strftime("%Y-%m-%d") for d in df["date"]],
            "close": df["close"].tolist(),
        }
        for out_name in strategy_cfg["indicator_names"]:
            entry[out_name] = indicator_cols[tkr][out_name].tolist()
        tickers_data_out[tkr] = entry

    equity_dates_out = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in all_dates]

    return elapsed, {
        "tickers_data": tickers_data_out,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "trades": [
            {**t, "entry_date": t["entry_date"].strftime("%Y-%m-%d") if hasattr(t["entry_date"], "strftime") else t["entry_date"],
             "exit_date": t["exit_date"].strftime("%Y-%m-%d") if hasattr(t["exit_date"], "strftime") else t["exit_date"]}
            for t in trades
        ],
        "equity_dates": equity_dates_out,
        "equity_curve": equity_curve,
        "final_value": final_value,
    }

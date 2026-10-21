"""
backtest_naive.py
-------------------
Naive, loop-based backtest engine: pure Python, no pandas/NumPy
anywhere in this file or in indicators_naive.py. This is baseline
(1) from the IO spec's Section 5 benchmark table ("Naive Python").

For each ticker:
    1. Evaluate the strategy's .valpha definitions with valpha_eval.evaluate_naive
       (indicators via indicators_naive.py, expression tree walked with plain loops)
    2. Collect the BUY/SELL signals that evaluation produces
    3. Walk the bars once, simulating a single-position-at-a-time
       portfolio: on BUY, commit `allocation * capital` worth of
       shares; on SELL, close the position and realize P&L.

Position sizing follows the IO spec worked example:
    shares = (allocation * initial_capital) / entry_price
"""

import time

import valpha_eval


def run_backtest(strategy_cfg, tickers_data, verbose=False):
    """
    tickers_data: dict[ticker] -> {"dates": [...], "close": [...]}  (from data_loader.load_naive)
    Returns: (elapsed_seconds, results) where results = {
        "tickers_data": tickers_data (mutated in place with indicator columns),
        "buy_signals": {ticker: [bool]}, "sell_signals": {ticker: [bool]},
        "trades": [...], "equity_dates": [...], "equity_curve": [...],
        "final_value": float,
    }
    """
    t0 = time.perf_counter()

    capital = strategy_cfg["initial_capital"]
    allocation = strategy_cfg["allocation"]
    spec = strategy_cfg["spec"]

    buy_signals = {}
    sell_signals = {}

    # --- 1 & 2: indicators + signals, per ticker, fully loop-based ---
    for tkr, d in tickers_data.items():
        columns, buy, sell = valpha_eval.evaluate_naive(spec, d["close"])
        d.update(columns)
        buy_signals[tkr] = buy
        sell_signals[tkr] = sell

    # --- 3: sequential single-position portfolio simulation ---
    # Merge all tickers into one chronological timeline of bars so a
    # shared cash pool is simulated across the whole universe (a
    # position is opened/closed per ticker independently, capital is
    # shared). We approximate the IO spec's single-ticker worked
    # example by tracking cash + open positions per ticker.
    all_dates = sorted({dt for d in tickers_data.values() for dt in d["dates"]})
    date_index = {tkr: {dt: i for i, dt in enumerate(d["dates"])} for tkr, d in tickers_data.items()}

    cash = capital
    open_positions = {}  # ticker -> {"entry_date", "entry_px", "shares"}
    trades = []
    equity_curve = []

    for dt in all_dates:
        # process today's signals for every ticker that has a bar today
        for tkr, d in tickers_data.items():
            idx = date_index[tkr].get(dt)
            if idx is None:
                continue
            price = d["close"][idx]
            if tkr not in open_positions and buy_signals[tkr][idx]:
                trade_capital = allocation * capital
                shares = trade_capital / price
                open_positions[tkr] = {"entry_date": dt, "entry_px": price, "shares": shares}
                cash -= trade_capital
            elif tkr in open_positions and sell_signals[tkr][idx]:
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

        # mark-to-market equity for today
        mtm = cash
        for tkr, pos in open_positions.items():
            idx = date_index[tkr].get(dt)
            price = tickers_data[tkr]["close"][idx] if idx is not None else pos["entry_px"]
            mtm += pos["shares"] * price
        equity_curve.append(mtm)

    final_value = equity_curve[-1] if equity_curve else capital

    elapsed = time.perf_counter() - t0

    if verbose:
        print(f"[naive] {len(tickers_data)} tickers, {len(trades)} trades, {elapsed:.4f}s")

    return elapsed, {
        "tickers_data": tickers_data,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "trades": trades,
        "equity_dates": all_dates,
        "equity_curve": equity_curve,
        "final_value": final_value,
    }

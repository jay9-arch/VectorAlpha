"""
output_writer.py
------------------
Writes the four output artifacts defined in VectorAlpha_IO_Spec.docx
Section 3 (indicators+signals csv, trades.csv, equity_curve.csv,
summary.txt) for a single (strategy, engine) run. Used by both the
naive and vectorized engines so the on-disk format is identical and
directly comparable.
"""

import os
import csv


def fmt(v, decimals=4):
    if v is None:
        return "---"
    try:
        if v != v:  # NaN check without importing math/numpy here
            return "---"
    except TypeError:
        pass
    return f"{v:.{decimals}f}"


def write_indicators_signals(path, tickers_data, indicator_names, buy_signals, sell_signals):
    """
    tickers_data: dict[ticker] -> {"dates": [...], "close": [...], **indicators}
    buy_signals / sell_signals: dict[ticker] -> list[bool]
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker", "date", "close"] + indicator_names + ["signal"])
        for tkr, d in tickers_data.items():
            n = len(d["dates"])
            buy = buy_signals.get(tkr, [False] * n)
            sell = sell_signals.get(tkr, [False] * n)
            for i in range(n):
                sig = "BUY" if buy[i] else ("SELL" if sell[i] else "---")
                row = [tkr, d["dates"][i], fmt(d["close"][i], 2)]
                for name in indicator_names:
                    row.append(fmt(d[name][i], 4) if name in d else "---")
                row.append(sig)
                writer.writerow(row)


def write_trades(path, trades):
    """trades: list of dicts with ticker, entry_date, entry_px, exit_date, exit_px, shares, pnl, pnl_pct"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker", "entry_date", "entry_px", "exit_date", "exit_px", "shares", "pnl", "pnl_%"])
        for t in trades:
            writer.writerow([
                t["ticker"], t["entry_date"], f'{t["entry_px"]:.2f}',
                t["exit_date"], f'{t["exit_px"]:.2f}', f'{t["shares"]:.4f}',
                f'{t["pnl"]:.2f}', f'{t["pnl_pct"]:.2f}',
            ])


def write_equity_curve(path, dates, equity):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "equity"])
        for d, e in zip(dates, equity):
            writer.writerow([d, f"{e:.2f}"])


def max_drawdown_pct(equity):
    peak = None
    worst = 0.0
    for e in equity:
        if peak is None or e > peak:
            peak = e
        dd = (e - peak) / peak * 100 if peak else 0.0
        if dd < worst:
            worst = dd
    return worst


def write_summary(path, initial_capital, final_value, trades, equity=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    closed = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = (wins / closed * 100) if closed else 0.0
    total_return = (final_value - initial_capital) / initial_capital * 100
    mdd = max_drawdown_pct(equity) if equity else 0.0

    with open(path, "w") as f:
        f.write(f"Initial capital : {initial_capital:.2f}\n")
        f.write(f"Final portfolio value : {final_value:.2f}\n")
        f.write(f"Total return : {total_return:.2f}%\n")
        f.write(f"Max drawdown : {mdd:.2f}%\n")
        f.write(f"Closed trades : {closed}\n")
        f.write(f"Win rate : {win_rate:.2f}%\n")

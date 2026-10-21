"""
demo_data.py
-------------
Generates a reproducible synthetic price dataset (yfinance-style CSVs) so the
project can be run and demoed on a machine that has no real market data.

    python src/demo_data.py                 # 40 tickers x 1500 bars -> data/synthetic_demo
    python src/demo_data.py 100 2000 out/   # 100 tickers x 2000 bars -> out/

Prices follow a random walk whose drift switches every 60-250 bars, so trend,
breakout and mean-reversion strategies all find something to trade. The seed is
fixed: the same arguments always produce the same files (and the same trades).
"""

import csv
import datetime
import math
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(PROJECT_ROOT, "data", "synthetic_demo")  # separate from your real data


def _weekdays(start, count):
    days, d = [], start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += datetime.timedelta(days=1)
    return days


def generate(out_dir=DEFAULT_DIR, n_tickers=40, bars=1500, seed=42):
    """Writes SYN001.csv ... into out_dir; returns the list of ticker names."""
    import random
    rnd = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    days = _weekdays(datetime.date(2018, 1, 1), bars)
    names = []
    for k in range(n_tickers):
        name = f"SYN{k + 1:03d}"
        px = math.exp(rnd.uniform(math.log(20), math.log(500)))
        vol = rnd.uniform(0.008, 0.03)
        drift, left = 0.0, 0
        with open(os.path.join(out_dir, f"{name}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits"])
            for d in days:
                if left <= 0:
                    drift, left = rnd.gauss(0.0004, 0.0012), rnd.randint(60, 250)
                left -= 1
                prev = px
                px = max(px * (1 + rnd.gauss(drift, vol)), 1.0)
                hi = max(prev, px) * (1 + abs(rnd.gauss(0, vol / 3)))
                lo = max(min(prev, px) * (1 - abs(rnd.gauss(0, vol / 3))), 0.5)
                w.writerow([d.isoformat(), round(prev, 2), round(hi, 2), round(lo, 2), round(px, 2),
                            rnd.randint(100_000, 5_000_000), 0.0, 0.0])
        names.append(name)
    return names


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    b = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    out = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_DIR
    tickers = generate(out, n, b)
    print(f"Wrote {len(tickers)} tickers x {b} bars to {out}")

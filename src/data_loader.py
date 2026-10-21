"""
data_loader.py
---------------
Reads all ticker CSVs from the configured data folder (src/config.py) and
exposes the data in two shapes:

    load_naive()      -> dict[ticker] = { "dates": [...], "close": [...] }
                          (plain python lists, for the loop-based engine)

    load_vectorized()  -> dict[ticker] = pandas.DataFrame indexed by date
                          (for the pandas/NumPy engine)

The CSVs are the standard yfinance download format:
    Date,Open,High,Low,Close,Volume,Dividends,Stock Splits
The ticker symbol is taken from the filename (e.g. HONA.csv -> HONA).

The active folder is the module-level DATA_DIR. It starts as whatever
src/config.py resolved, and use_data_dir() can re-point it at run time (the
demo menu uses this after generating synthetic data). Every function reads
DATA_DIR when it is *called*, not when the module is imported.
"""

import os
import csv
import glob

try:
    import pandas as pd
except ImportError:
    pd = None

import config  # noqa: E402  -- the one place the data folder is configured (src/config.py)

DATA_DIR = config.DATA_DIR


def use_data_dir(path):
    """Re-points every engine at a different data folder for the rest of this run."""
    global DATA_DIR
    DATA_DIR = path


def _resolve(data_dir):
    return DATA_DIR if data_dir is None else data_dir


def list_tickers(data_dir=None):
    """Return sorted ticker symbols available in the data folder."""
    data_dir = _resolve(data_dir)
    files = glob.glob(os.path.join(data_dir, "*.csv"))
    return sorted(os.path.splitext(os.path.basename(f))[0] for f in files)


def load_naive(tickers=None, data_dir=None):
    """
    Load closing prices as plain python lists (no pandas/numpy).
    Used by the naive, loop-based implementation so that path has
    zero vectorized-library involvement.
    """
    data_dir = _resolve(data_dir)
    if tickers is None:
        tickers = list_tickers(data_dir)

    out = {}
    for tkr in tickers:
        path = os.path.join(data_dir, f"{tkr}.csv")
        if not os.path.exists(path):
            continue
        dates, closes = [], []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    c = float(row["Close"])
                    if c != c:  # NaN: skipped by the pandas and compiled engines too
                        continue
                    closes.append(c)
                    dates.append(row["Date"][:10])  # keep YYYY-MM-DD only
                except (ValueError, KeyError):
                    continue
        if closes:
            out[tkr] = {"dates": dates, "close": closes}
    return out


def load_vectorized(tickers=None, data_dir=None):
    """
    Load closing prices as pandas DataFrames, for the vectorized
    (pandas/NumPy) implementation.
    """
    if pd is None:
        raise ImportError("pandas is required for load_vectorized(); pip install -r requirements.txt")

    data_dir = _resolve(data_dir)
    if tickers is None:
        tickers = list_tickers(data_dir)

    out = {}
    for tkr in tickers:
        path = os.path.join(data_dir, f"{tkr}.csv")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        # Keep the calendar date exactly as written in the file (first 10 chars,
        # YYYY-MM-DD), like load_naive and the compiled engine do. Converting
        # through UTC would shift bars with a non-US offset (e.g. +05:30 for NSE
        # tickers) back one day, so all three engines would disagree on dates.
        df["Date"] = pd.to_datetime(df["Date"].astype(str).str[:10])
        df = df[["Date", "Close"]].rename(columns={"Date": "date", "Close": "close"})
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        if not df.empty:
            out[tkr] = df
    return out


if __name__ == "__main__":
    tickers = list_tickers()
    print(f"Found {len(tickers)} tickers in {DATA_DIR}")
    print(tickers[:10], "..." if len(tickers) > 10 else "")

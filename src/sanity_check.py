"""
sanity_check.py
-----------------
Reproduces the "Section 2.2 / 4 worked example" from
VectorAlpha_IO_Spec.docx: writes STOCKA.csv and STOCKB.csv (the exact
hand-computed numbers from the spec) into a demo folder, runs every
available engine (naive, vectorized, and compiled if it has been built) on
them, and checks each result against the spec's answer key -- PASS/FAIL per
engine, so correctness can be shown live in front of reviewers without
trusting the 1000+ ticker run blindly.
"""

import os
import csv

import data_loader
import backtest_naive
import backtest_vectorized
from strategies import STRATEGIES

DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "demo_price_data")

STOCKA = [
    ("2024-06-03", 110.00), ("2024-06-04", 109.04), ("2024-06-05", 107.22), ("2024-06-06", 105.50),
    ("2024-06-07", 104.39), ("2024-06-10", 103.00), ("2024-06-11", 101.66), ("2024-06-12", 102.74),
    ("2024-06-13", 103.99), ("2024-06-14", 104.40), ("2024-06-17", 104.73), ("2024-06-18", 106.03),
    ("2024-06-19", 106.85), ("2024-06-20", 104.98), ("2024-06-21", 104.18), ("2024-06-24", 102.76),
    ("2024-06-25", 100.95), ("2024-06-26", 99.83), ("2024-06-27", 97.71), ("2024-06-28", 95.65),
]

STOCKB = [
    ("2024-06-03", 50.00), ("2024-06-04", 50.40), ("2024-06-05", 50.90), ("2024-06-06", 51.30),
    ("2024-06-07", 51.80), ("2024-06-10", 52.20), ("2024-06-11", 52.70), ("2024-06-12", 53.10),
    ("2024-06-13", 53.60), ("2024-06-14", 54.00), ("2024-06-17", 54.50), ("2024-06-18", 54.90),
    ("2024-06-19", 55.40), ("2024-06-20", 55.80), ("2024-06-21", 56.30), ("2024-06-24", 56.70),
    ("2024-06-25", 57.20), ("2024-06-26", 57.60), ("2024-06-27", 58.10), ("2024-06-28", 58.50),
]

EXPECTED_TRADE = {
    "ticker": "STOCKA", "entry_date": "2024-06-14", "entry_px": 104.40,
    "exit_date": "2024-06-24", "exit_px": 102.76, "shares": 478.9272,
    "pnl": -785.44, "pnl_pct": -1.57,
}
EXPECTED_SUMMARY = {
    "Final portfolio value": 99214.56, "Total return": -0.79, "Max drawdown": -1.94,
    "Closed trades": 1, "Win rate": 0.00,
}


def _write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits"])
        for date, close in rows:
            w.writerow([date, close, close, close, close, 0, 0.0, 0.0])


def _check_trade(t, label):
    """Compares one trade (dict) against the IO spec's answer key; returns a list of failures."""
    exp = EXPECTED_TRADE
    bad = []
    if t["ticker"] != exp["ticker"]:
        bad.append(f"ticker {t['ticker']} != {exp['ticker']}")
    if t["entry_date"] != exp["entry_date"] or t["exit_date"] != exp["exit_date"]:
        bad.append(f"dates {t['entry_date']}->{t['exit_date']} != {exp['entry_date']}->{exp['exit_date']}")
    for key, tol in (("entry_px", 0.005), ("exit_px", 0.005), ("shares", 0.0005), ("pnl", 0.005), ("pnl_pct", 0.005)):
        if abs(float(t[key]) - exp[key]) > tol:
            bad.append(f"{key} {float(t[key]):.4f} != {exp[key]}")
    return bad


def _run_compiled():
    """Runs the compiled engine (if it has been built) on the demo data; returns (trades, final_value) or None."""
    import subprocess
    import tempfile
    import benchmark
    binary = benchmark.compiled_binary("crossover_rsi")
    if not os.path.exists(binary):
        return None
    out_dir = tempfile.mkdtemp(prefix="valpha_sanity_")
    proc = subprocess.run([binary, DEMO_DIR, out_dir], capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout, proc.stderr)
        return None
    trades = []
    with open(os.path.join(out_dir, "trades.csv")) as f:
        for row in csv.DictReader(f):
            trades.append({"ticker": row["ticker"], "entry_date": row["entry_date"], "entry_px": row["entry_px"],
                           "exit_date": row["exit_date"], "exit_px": row["exit_px"], "shares": row["shares"],
                           "pnl": row["pnl"], "pnl_pct": row["pnl_%"]})
    final = None
    with open(os.path.join(out_dir, "summary.txt")) as f:
        for line in f:
            if line.startswith("Final portfolio value"):
                final = float(line.split(":")[1])
    return trades, final


def run(verbose=True):
    """Runs every available engine on the IO-spec worked example; returns True if all match the answer key."""
    print("Writing IO-spec worked example (STOCKA / STOCKB) into", DEMO_DIR)
    _write_csv(os.path.join(DEMO_DIR, "STOCKA.csv"), STOCKA)
    _write_csv(os.path.join(DEMO_DIR, "STOCKB.csv"), STOCKB)

    cfg = STRATEGIES["crossover_rsi"]

    naive_data = data_loader.load_naive(["STOCKA", "STOCKB"], data_dir=DEMO_DIR)
    vec_data = data_loader.load_vectorized(["STOCKA", "STOCKB"], data_dir=DEMO_DIR)

    _, naive_res = backtest_naive.run_backtest(cfg, naive_data)
    _, vec_res = backtest_vectorized.run_backtest(cfg, vec_data)

    results = [("Naive", naive_res["trades"], naive_res["final_value"]),
               ("Vectorized", vec_res["trades"], vec_res["final_value"])]
    compiled = _run_compiled()
    if compiled is not None:
        results.append(("Compiled", compiled[0], compiled[1]))
    else:
        print("\n(Compiled engine not built -- run `cd compiler && ./build_all.sh` to include it.)")

    all_ok = True
    for label, trades, final in results:
        print(f"\n=== {label} engine result ===")
        problems = []
        if len(trades) != 1:
            problems.append(f"expected exactly 1 trade, got {len(trades)}")
        else:
            t = trades[0]
            print(f"Trade: {t['ticker']} entry {t['entry_date']}@{float(t['entry_px']):.2f} "
                  f"-> exit {t['exit_date']}@{float(t['exit_px']):.2f}, shares={float(t['shares']):.4f}, "
                  f"pnl={float(t['pnl']):.2f} ({float(t['pnl_pct']):.2f}%)")
            problems += _check_trade(t, label)
        print(f"Final portfolio value: {final:.2f}  (expected {EXPECTED_SUMMARY['Final portfolio value']:.2f})")
        if abs(final - EXPECTED_SUMMARY["Final portfolio value"]) > 0.005:
            problems.append("final portfolio value mismatch")
        print("  PASS" if not problems else "  FAIL: " + "; ".join(problems))
        all_ok = all_ok and not problems
    print("\nSanity check:", "ALL ENGINES MATCH THE IO SPEC" if all_ok else "MISMATCH -- see above")
    return all_ok


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)

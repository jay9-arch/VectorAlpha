#!/usr/bin/env python3
"""
smoke_test.py -- end-to-end smoke test for VectorAlpha.

    python smoke_test.py                      # runs everything it can
    python smoke_test.py --require-compiled   # FAIL (instead of skip) if the C++ engine is not built
    python smoke_test.py --keep               # keep the temp folder so you can inspect the outputs

What it does, using a tiny HARDCODED dataset written in the same yfinance CSV
format as real data (dates like "2024-01-02 00:00:00-05:00", Dividends and
Stock Splits columns, even a blank Close cell):

  1. writes two ticker CSVs + one .valpha strategy into a temp folder
  2. loads them through the real data_loader and parses the strategy with the real front-end
  3. runs the NAIVE and VECTORIZED engines and writes the four output artifacts
  4. compiles the strategy with valphac and runs the COMPILED engine (skipped if not built)
  5. validates everything against numbers worked out by hand below:
     signals, trades, P&L, equity curve, summary, indicator columns, and that
     every engine produced identical output files

Nothing here touches your real data folder or results/ folder.

The hand-worked scenario (strategy: BUY when close crosses above 100, SELL when it crosses below):

  SMOKE1 closes: 95 98 102 105 103 99 97 101 104 106   (10 weekdays from 2024-01-02)
    BUY  on bar 3 (98 -> 102)  at 102.00
    SELL on bar 6 (103 -> 99)  at  99.00     -> closed trade, a loss
    BUY  on bar 8 (97 -> 101)  at 101.00     -> still open at the end (not a closed trade)
  SMOKE2 closes: 50 51 52 53 (blank) 55 56 57 58 59  -- never near 100, so never trades
  INITIAL_CAPITAL 100000, ALLOCATION 0.5 -> 50000 committed per trade
"""

import argparse
import csv
import datetime
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

STRATEGY_TEXT = """\
// description: smoke-test strategy (cross 100)
INITIAL_CAPITAL = 100000;
ALLOCATION = 0.5;
UNIVERSE = {};

LEVEL = close - 100;
SMA_2 = SMA(close, 2);

BUY  WHEN CROSSOVER(close, 100);
SELL WHEN CROSSUNDER(close, 100);
"""

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
        "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15"]
SMOKE1 = [95, 98, 102, 105, 103, 99, 97, 101, 104, 106]
SMOKE2 = [50, 51, 52, 53, None, 55, 56, 57, 58, 59]     # None = blank Close cell (a yfinance quirk)

CAPITAL, ALLOC = 100000.0, 0.5
# Hand-worked expectations ----------------------------------------------------
TRADE_ENTRY_DATE, TRADE_ENTRY_PX = DAYS[2], 102.0
TRADE_EXIT_DATE, TRADE_EXIT_PX = DAYS[5], 99.0
OPEN_ENTRY_DATE, OPEN_ENTRY_PX = DAYS[7], 101.0
SHARES_1 = CAPITAL * ALLOC / TRADE_ENTRY_PX            # 490.1961
SHARES_2 = CAPITAL * ALLOC / OPEN_ENTRY_PX             # 495.0495
PNL_1 = SHARES_1 * (TRADE_EXIT_PX - TRADE_ENTRY_PX)    # -1470.59
CASH_AFTER_1 = CAPITAL + PNL_1                         # 98529.41
EXPECTED_SIGNALS = {DAYS[2]: "BUY", DAYS[5]: "SELL", DAYS[7]: "BUY"}


def expected_equity():
    """Mark-to-market equity per bar, derived only from the trade list above."""
    out = []
    for i, px in enumerate(SMOKE1):
        if i < 2:
            out.append(CAPITAL)
        elif i < 5:                                          # long 490.1961 shares since bar 3
            out.append(CAPITAL - CAPITAL * ALLOC + SHARES_1 * px)
        elif i < 7:                                          # flat
            out.append(CASH_AFTER_1)
        else:                                                # long 495.0495 shares since bar 8
            out.append(CASH_AFTER_1 - CAPITAL * ALLOC + SHARES_2 * px)
    return out


def write_csv(folder, name, closes):
    with open(os.path.join(folder, f"{name}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits"])
        for day, c in zip(DAYS, closes):
            cell = "" if c is None else c
            w.writerow([f"{day} 00:00:00-05:00", cell, cell, cell, cell, 1000000, 0, 0])


# ------------------------------------------------------------------ checks

class Report:
    def __init__(self):
        self.passed = self.failed = self.skipped = 0

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}" + (f"   -> {detail}" if detail else ""))
        return ok

    def skip(self, name, why):
        self.skipped += 1
        print(f"  SKIP  {name}   ({why})")


def near(a, b, tol=0.006):
    return abs(float(a) - float(b)) <= tol


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


def read_text(path):
    # Python's csv module writes \r\n, the C++ engine writes \n: compare content, not line endings
    with open(path, newline="") as f:
        return f.read().replace("\r\n", "\n")


def validate_outputs(rep, label, out_dir):
    """Checks one engine's four output artifacts against the hand-worked numbers."""
    print(f"\n[{label}] validating output files")
    files = ("indicators_signals.csv", "trades.csv", "equity_curve.csv", "summary.txt")
    missing = [f for f in files if not os.path.exists(os.path.join(out_dir, f))]
    if not rep.check("all four output files exist", not missing, f"missing: {missing}"):
        return

    # trades.csv: exactly one CLOSED trade (the second BUY is still open)
    trades = read_csv(os.path.join(out_dir, "trades.csv"))
    rep.check("trades.csv header", trades[0] == ["ticker", "entry_date", "entry_px", "exit_date",
                                                  "exit_px", "shares", "pnl", "pnl_%"], str(trades[0]))
    rep.check("exactly one closed trade", len(trades) == 2, f"rows={len(trades) - 1}")
    if len(trades) == 2:
        t = trades[1]
        rep.check("trade ticker and dates", t[0] == "SMOKE1" and t[1] == TRADE_ENTRY_DATE and t[3] == TRADE_EXIT_DATE, str(t))
        rep.check("trade prices", near(t[2], TRADE_ENTRY_PX) and near(t[4], TRADE_EXIT_PX), str(t))
        rep.check("trade shares", near(t[5], SHARES_1, 0.0006), f"{t[5]} vs {SHARES_1:.4f}")
        rep.check("trade pnl", near(t[6], PNL_1), f"{t[6]} vs {PNL_1:.2f}")
        rep.check("trade pnl %", near(t[7], PNL_1 / (SHARES_1 * TRADE_ENTRY_PX) * 100), t[7])

    # equity_curve.csv: union of dates (10), mark-to-market values
    eq = read_csv(os.path.join(out_dir, "equity_curve.csv"))
    rep.check("equity curve has 10 dated rows", len(eq) == 11 and [r[0] for r in eq[1:]] == DAYS,
              f"rows={len(eq) - 1}")
    want = expected_equity()
    bad = [i for i, r in enumerate(eq[1:]) if i < len(want) and not near(r[1], want[i])]
    rep.check("equity values match hand calculation", not bad and len(eq) == 11,
              f"first bad bar {bad[:1]}: got {[eq[i + 1][1] for i in bad[:1]]} want {[round(want[i], 2) for i in bad[:1]]}")

    # summary.txt
    s = {}
    for line in read_text(os.path.join(out_dir, "summary.txt")).splitlines():
        k, _, v = line.partition(":")
        s[k.strip()] = v.strip()
    final = want[-1]
    rep.check("summary: initial capital", near(s.get("Initial capital", "nan"), CAPITAL), str(s))
    rep.check("summary: final value", near(s.get("Final portfolio value", "nan"), final), f"{s.get('Final portfolio value')} vs {final:.2f}")
    rep.check("summary: total return", near(s.get("Total return", "nan").rstrip("%"), (final - CAPITAL) / CAPITAL * 100), s.get("Total return"))
    rep.check("summary: closed trades = 1, win rate 0%", s.get("Closed trades") == "1" and s.get("Win rate") == "0.00%", str(s))
    peak = max(want[:5])
    worst = min((e - peak) / peak * 100 for e in want[:7] if e < peak)
    rep.check("summary: max drawdown", near(s.get("Max drawdown", "nan").rstrip("%"), worst), s.get("Max drawdown"))

    # indicators_signals.csv: columns, warm-up blanks, hand-checked values, signals
    table = read_csv(os.path.join(out_dir, "indicators_signals.csv"))
    rep.check("indicator columns", table[0] == ["ticker", "date", "close", "LEVEL", "SMA_2", "signal"], str(table[0]))
    rows1 = [r for r in table[1:] if r[0] == "SMOKE1"]
    rows2 = [r for r in table[1:] if r[0] == "SMOKE2"]
    rep.check("SMOKE1 has 10 rows, SMOKE2 has 9 (blank Close skipped)", len(rows1) == 10 and len(rows2) == 9,
              f"{len(rows1)}/{len(rows2)}")
    if len(rows1) == 10:
        rep.check("LEVEL = close - 100", all(near(r[3], c - 100, 0.00006) for r, c in zip(rows1, SMOKE1)))
        rep.check("SMA_2 warm-up is missing, then correct",
                  rows1[0][4] == "---" and near(rows1[1][4], 96.5, 0.00006) and near(rows1[9][4], 105.0, 0.00006),
                  f"{rows1[0][4]} {rows1[1][4]} {rows1[9][4]}")
        got = {r[1]: r[5] for r in rows1 if r[5] != "---"}
        rep.check("signals: BUY, SELL, BUY on the right bars only", got == EXPECTED_SIGNALS, str(got))
    rep.check("SMOKE2 never signals", all(r[5] == "---" for r in rows2))


def main():
    ap = argparse.ArgumentParser(description="VectorAlpha end-to-end smoke test")
    ap.add_argument("--require-compiled", action="store_true", help="fail if the compiled engine cannot be tested")
    ap.add_argument("--keep", action="store_true", help="keep the temp folder")
    args = ap.parse_args()

    rep = Report()
    tmp = tempfile.mkdtemp(prefix="valpha_smoke_")
    data_dir, strat_file = os.path.join(tmp, "data"), os.path.join(tmp, "smoke.valpha")
    os.makedirs(data_dir)
    print(f"VectorAlpha smoke test   (temp folder: {tmp})")
    try:
        import benchmark
        import backtest_naive
        import backtest_vectorized
        import data_loader
        import valpha_lang
    except ImportError as exc:
        print(f"Missing dependency: {exc}\nRun: pip install -r requirements.txt")
        return 2

    try:
        # 1. inputs
        print("\n[1] inputs and front-end")
        write_csv(data_dir, "SMOKE1", SMOKE1)
        write_csv(data_dir, "SMOKE2", SMOKE2)
        with open(strat_file, "w") as f:
            f.write(STRATEGY_TEXT)
        spec = valpha_lang.parse_file(strat_file)
        rep.check("strategy parses; columns are LEVEL, SMA_2", spec.output_columns == ["LEVEL", "SMA_2"], str(spec.output_columns))
        rep.check("config read: capital 100000, allocation 0.5", spec.initial_capital == CAPITAL and spec.allocation == ALLOC)
        try:
            valpha_lang.parse_text("INITIAL_CAPITAL = 1; ALLOCATION = 1; BUY WHEN close + 1;")
            rep.check("a non-boolean rule is rejected", False, "no error raised")
        except valpha_lang.ValphaError:
            rep.check("a non-boolean rule is rejected", True)
        rep.check("data_loader lists both tickers", data_loader.list_tickers(data_dir) == ["SMOKE1", "SMOKE2"])
        naive = data_loader.load_naive(None, data_dir)
        rep.check("loader strips timezone to YYYY-MM-DD and skips the blank Close",
                  naive["SMOKE1"]["dates"] == DAYS and len(naive["SMOKE2"]["close"]) == 9
                  and DAYS[4] not in naive["SMOKE2"]["dates"])

        cfg = {"initial_capital": spec.initial_capital, "allocation": spec.allocation, "spec": spec,
               "indicator_names": list(spec.output_columns), "description": spec.description}
        out_dirs = {}

        # 2. naive
        print("\n[2] naive engine")
        _, res = backtest_naive.run_backtest(cfg, naive)
        out_dirs["naive"] = os.path.join(tmp, "out_naive")
        benchmark._write_all_artifacts(out_dirs["naive"], cfg, cfg["indicator_names"], res)
        validate_outputs(rep, "naive", out_dirs["naive"])

        # 3. vectorized
        print("\n[3] vectorized engine")
        _, res = backtest_vectorized.run_backtest(cfg, data_loader.load_vectorized(None, data_dir))
        out_dirs["vectorized"] = os.path.join(tmp, "out_vectorized")
        benchmark._write_all_artifacts(out_dirs["vectorized"], cfg, cfg["indicator_names"], res)
        validate_outputs(rep, "vectorized", out_dirs["vectorized"])

        # 4. compiled
        print("\n[4] compiled engine")
        compiler_dir = os.path.join(ROOT, "compiler")
        valphac = next((p for p in (os.path.join(compiler_dir, "valphac"), os.path.join(compiler_dir, "valphac.exe"))
                        if os.path.exists(p)), None)
        if valphac is None:
            why = "compiler/valphac not built: cd compiler && make"
            if args.require_compiled:
                rep.check("compiled engine available", False, why)
            else:
                rep.skip("compiled engine", why)
        else:
            bin_dir = os.path.join(tmp, "bin")
            proc = subprocess.run([valphac, strat_file, bin_dir], capture_output=True, text=True)
            binary = next((p for p in (os.path.join(bin_dir, "smoke_runtime"), os.path.join(bin_dir, "smoke_runtime.exe"))
                           if os.path.exists(p)), None)
            if not rep.check("valphac compiles the strategy to a binary", proc.returncode == 0 and binary,
                             (proc.stdout + proc.stderr)[-400:]):
                pass
            else:
                out_dirs["compiled"] = os.path.join(tmp, "out_compiled")
                run = subprocess.run([binary, data_dir, out_dirs["compiled"]], capture_output=True, text=True)
                if rep.check("compiled binary runs and reports ELAPSED_SECONDS",
                             run.returncode == 0 and "ELAPSED_SECONDS=" in run.stdout, (run.stdout + run.stderr)[-400:]):
                    validate_outputs(rep, "compiled", out_dirs["compiled"])

        # 5. agreement
        print("\n[5] cross-engine agreement (output files must be byte-identical)")
        base = out_dirs["naive"]
        for other in ("vectorized", "compiled"):
            if other not in out_dirs:
                continue
            for fname in ("trades.csv", "equity_curve.csv", "summary.txt", "indicators_signals.csv"):
                same = read_text(os.path.join(base, fname)) == read_text(os.path.join(out_dirs[other], fname))
                rep.check(f"naive == {other}: {fname}", same)
    finally:
        if args.keep:
            print(f"\n(kept {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f" smoke test: {rep.passed} passed, {rep.failed} failed, {rep.skipped} skipped")
    print(" RESULT: " + ("OK" if rep.failed == 0 else "FAILED"))
    print("=" * 60)
    return 0 if rep.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

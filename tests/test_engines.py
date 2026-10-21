"""
Test suite for the three VectorAlpha engines and the .valpha front-ends.

    python -m unittest discover -s tests -v          (from the project root)

Needs pandas/NumPy. Tests that involve the compiled engine are skipped
automatically unless it has been built:  cd compiler && ./build_all.sh
(the arithmetic-demo test additionally compiles one strategy on the fly and
needs compiler/valphac and a C++ compiler).
"""

import csv
import datetime
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import backtest_naive          # noqa: E402
import backtest_vectorized     # noqa: E402
import data_loader             # noqa: E402
import indicators_naive as N   # noqa: E402
import indicators_vectorized as V   # noqa: E402
import valpha_eval             # noqa: E402
import valpha_lang             # noqa: E402
from strategies import STRATEGIES   # noqa: E402

COMPILER = os.path.join(ROOT, "compiler")
VALPHAC = os.path.join(COMPILER, "valphac")


def make_synthetic(dirpath, n_tickers=13, bars=420, seed=7):
    """Random-walk CSVs in yfinance format (+05:30 timestamps, like NSE data).
    Tickers 0-8 and 12 share one calendar (12 also has a flat price stretch);
    9-10 start later; 11 has an 8-day gap -- so SIMD batching, scalar leftovers,
    flat-window RSI and unaligned calendars are all exercised."""
    rnd = random.Random(seed)
    os.makedirs(dirpath, exist_ok=True)
    days, d = [], datetime.date(2022, 1, 3)
    while len(days) < bars + 40:
        if d.weekday() < 5:
            days.append(d)
        d += datetime.timedelta(days=1)
    for k in range(n_tickers):
        px = rnd.uniform(40, 400)
        drift, vol = rnd.uniform(-0.0004, 0.0009), rnd.uniform(0.006, 0.03)
        lo = 30 if k in (9, 10) else 0
        seq = list(range(lo, lo + bars))
        if k == 11:
            seq = [s for s in seq if not 150 <= s < 158]
        flat = (200, 215) if k == 12 else (-1, -1)
        rows = []
        for s in seq:
            if not flat[0] <= s - lo < flat[1]:
                px = max(px * (1 + rnd.gauss(drift, vol)), 1.0)
            rows.append((days[s].isoformat() + " 00:00:00+05:30", round(px, 2)))
        with open(os.path.join(dirpath, f"SYN{k:02d}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits"])
            for dt, c in rows:
                w.writerow([dt, c, c, c, c, 1000, 0.0, 0.0])


def is_missing(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def trade_key(t):
    return (t["ticker"], t["entry_date"], t["exit_date"], round(t["pnl"], 2))


class FrontEndTests(unittest.TestCase):
    def test_precedence_and_grouping(self):
        s = valpha_lang.parse_text(
            "INITIAL_CAPITAL = 1; ALLOCATION = 1; A = close + 2 * 3; B = (close + 2) * 3; C = -close * 2;")
        defs = dict(s.series_defs)
        self.assertEqual(valpha_lang.to_text(defs["A"]), "(close + (2 * 3))")
        self.assertEqual(valpha_lang.to_text(defs["B"]), "((close + 2) * 3)")
        self.assertEqual(valpha_lang.to_text(defs["C"]), "((-close) * 2)")

    def test_constant_window_expression(self):
        s = valpha_lang.parse_text(
            "INITIAL_CAPITAL = 1; ALLOCATION = 1; W = 2 * 10; X = SMA(close, W);")
        self.assertEqual(s.output_columns, ["X"])
        with self.assertRaises(valpha_lang.ValphaError):
            valpha_lang.parse_text("INITIAL_CAPITAL = 1; ALLOCATION = 1; X = SMA(close, 2.5);")

    def test_broken_examples_are_rejected(self):
        for name in ("bad_type_error", "bad_division_by_zero", "bad_missing_config", "bad_undefined_identifier",
                     "bad_new_ops"):
            with self.assertRaises(valpha_lang.ValphaError, msg=name):
                valpha_lang.parse_file(os.path.join(COMPILER, "examples", f"{name}.valpha"))

    def test_registry_loads_every_strategy(self):
        shipped = {"crossover_rsi", "trend_follow", "volatility_breakout", "macd_trend", "bollinger_reversion",
                   "donchian_breakout", "stochastic_pullback", "momentum_roc", "zscore_reversion",
                   "triple_ema_regime", "connors_rsi2", "bollinger_squeeze_breakout", "efficiency_trend"}
        self.assertTrue(shipped <= set(STRATEGIES), shipped - set(STRATEGIES))
        for cfg in STRATEGIES.values():
            self.assertTrue(cfg["description"])
            self.assertTrue(cfg["indicator_names"])


class NumericContractTests(unittest.TestCase):
    """The pandas and naive implementations must obey the same contract as the compiled kernel."""

    def setUp(self):
        rnd = random.Random(3)
        px, self.close = 100.0, []
        for _ in range(200):
            px *= 1 + rnd.gauss(0, 0.02)
            self.close.append(round(px, 2))
        import pandas as pd
        self.pd = pd
        self.series = pd.Series(self.close)

    def assertSeriesClose(self, naive, vec, atol=1e-9):
        for i, (a, b) in enumerate(zip(naive, vec.tolist())):
            self.assertEqual(a is None, is_missing(b), f"missing mismatch at {i}")
            if a is not None:
                self.assertAlmostEqual(a, b, delta=atol * max(1.0, abs(a)), msg=f"index {i}")

    def test_indicators_agree(self):
        for w in (3, 7, 14, 20):
            self.assertSeriesClose(N.SMA(self.close, w), V.SMA(self.series, w))
            self.assertSeriesClose(N.EMA(self.close, w), V.EMA(self.series, w))
            self.assertSeriesClose(N.RSI(self.close, w), V.RSI(self.series, w), atol=1e-7)
            self.assertSeriesClose(N.STDEV(self.close, w), V.STDEV(self.series, w), atol=1e-5)

    def test_lag_highest_lowest_abs(self):
        for n in (1, 5, 20):
            self.assertSeriesClose(N.LAG(self.close, n), V.LAG(self.series, n))
        for w in (1, 3, 20):
            self.assertSeriesClose(N.HIGHEST(self.close, w), V.HIGHEST(self.series, w))
            self.assertSeriesClose(N.LOWEST(self.close, w), V.LOWEST(self.series, w))
        mixed = [-2.0, None, 3.0, -4.5]
        self.assertEqual(N.ABS(mixed), [2.0, None, 3.0, 4.5])
        self.assertEqual(N.LAG([1.0, 2.0, 3.0], 1), [None, 1.0, 2.0])
        # a missing value anywhere in the window makes HIGHEST/LOWEST missing until it leaves
        gap = [1.0, None, 3.0, 2.0, 5.0]
        self.assertEqual(N.HIGHEST(gap, 2), [None, None, None, 3.0, 5.0])
        self.assertEqual(N.LOWEST(gap, 2), [None, None, None, 2.0, 2.0])
        vec = V.HIGHEST(self.pd.Series([1.0, float("nan"), 3.0, 2.0, 5.0]), 2).tolist()
        self.assertEqual([None if math.isnan(x) else x for x in vec], [None, None, None, 3.0, 5.0])

    def test_vectorized_ema_is_sma_seeded(self):
        # regression: raw pandas ewm(adjust=False) starts at the first value and
        # disagreed with the naive/compiled seed for the first ~dozens of bars
        w = 10
        ema = V.EMA(self.series, w)
        self.assertTrue(math.isnan(ema[w - 2]))
        self.assertAlmostEqual(ema[w - 1], sum(self.close[:w]) / w, places=9)

    def test_flat_window_rsi_is_zero_everywhere(self):
        flat = [50.0] * 6 + [51.0] + [51.0] * 12
        self.assertEqual(N.RSI(flat, 5)[5], 0.0)
        self.assertEqual(V.RSI(self.pd.Series(flat), 5)[5], 0.0)
        self.assertEqual(N.RSI(flat, 5)[-1], 0.0)      # 51 held for 12 bars: gain has left the window
        self.assertEqual(V.RSI(self.pd.Series(flat), 5).iloc[-1], 0.0)

    def test_tolerant_crossover_ignores_floating_point_noise(self):
        a, b = [1.0, 1.0 + 1e-13, 1.0], [1.0, 1.0, 1.0]
        self.assertEqual(N.CROSSOVER(a, b), [False, False, False])
        self.assertEqual(V.CROSSOVER(self.pd.Series(a), self.pd.Series(b)).tolist(), [False, False, False])
        # residue near zero: a running sum that should be exactly 0 can be left at -1e-15
        # (found via stochastic_pullback: K = 0, D = SMA(K,3) = -1e-15). Not a crossover.
        near_zero_a, near_zero_b = [5.0, 0.0], [10.0, -1e-15]
        self.assertEqual(N.CROSSOVER(near_zero_a, near_zero_b), [False, False])
        self.assertEqual(V.CROSSOVER(self.pd.Series(near_zero_a), self.pd.Series(near_zero_b)).tolist(),
                         [False, False])
        a2 = [1.0, 1.001]
        self.assertEqual(N.CROSSOVER(a2, [1.0, 1.0]), [False, True])
        self.assertEqual(V.CROSSOVER(self.pd.Series(a2), self.pd.Series([1.0, 1.0])).tolist(), [False, True])

    def test_nested_indicators_and_missing_values(self):
        src = ("INITIAL_CAPITAL = 1; ALLOCATION = 1; R = RSI(close, 5); S = SMA(R, 4); "
               "Z = close / (close - close); BUY WHEN S > 50 AND Z > 0;")
        spec = valpha_lang.parse_text(src)
        cols_n, buy_n, _ = valpha_eval.evaluate_naive(spec, self.close)
        cols_v, buy_v, _ = valpha_eval.evaluate_vectorized(spec, self.series)
        self.assertTrue(all(is_missing(x) for x in cols_n["Z"]))          # x / 0 -> missing
        self.assertTrue(all(is_missing(x) for x in cols_v["Z"].tolist()))
        self.assertEqual(buy_n, [False] * len(self.close))                # missing compares False
        self.assertFalse(buy_v.any())
        self.assertSeriesClose(cols_n["S"], cols_v["S"], atol=1e-7)
        self.assertIsNone(cols_n["S"][5])    # RSI first valid at index 5, SMA(.,4) valid from index 8
        self.assertIsNotNone(cols_n["S"][8])


class EngineAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="valpha_test_")
        cls.data = os.path.join(cls.tmp, "data")
        make_synthetic(cls.data)
        cls.tickers = data_loader.list_tickers(cls.data)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_python_engines(self, cfg):
        _, rn = backtest_naive.run_backtest(cfg, data_loader.load_naive(self.tickers, self.data))
        _, rv = backtest_vectorized.run_backtest(cfg, data_loader.load_vectorized(self.tickers, self.data))
        return rn, rv

    def test_naive_equals_vectorized(self):
        for key, cfg in STRATEGIES.items():
            with self.subTest(strategy=key):
                rn, rv = self.run_python_engines(cfg)
                self.assertGreater(len(rn["trades"]), 0)
                self.assertEqual([trade_key(t) for t in rn["trades"]], [trade_key(t) for t in rv["trades"]])
                self.assertAlmostEqual(rn["final_value"], rv["final_value"], places=4)
                self.assertEqual(rn["equity_dates"], rv["equity_dates"])   # no UTC date shift

    def test_dsl_matches_legacy_hand_written_logic(self):
        """The generic DSL evaluator must reproduce the original hand-coded strategies
        (src/signal_logic_naive.py, kept only as this independent oracle)."""
        from signal_logic_naive import LOGIC_FUNCS
        legacy = {
            "crossover_rsi": ([("SMA_3", "SMA", 3), ("SMA_7", "SMA", 7), ("RSI_5", "RSI", 5)], "sma_cross_rsi",
                              {"fast": "SMA_3", "slow": "SMA_7", "rsi": "RSI_5", "rsi_max": 70}),
            "trend_follow": ([("SMA_50", "SMA", 50), ("SMA_200", "SMA", 200), ("RSI_14", "RSI", 14)], "sma_cross_rsi",
                             {"fast": "SMA_50", "slow": "SMA_200", "rsi": "RSI_14", "rsi_max": 70}),
            "volatility_breakout": ([("EMA_20", "EMA", 20), ("STDEV_10", "STDEV", 10)], "band_breakout",
                                    {"ema": "EMA_20", "std": "STDEV_10"}),
        }
        data = data_loader.load_naive(self.tickers, self.data)
        for key, (inds, logic, params) in legacy.items():
            with self.subTest(strategy=key):
                for tkr, d in data.items():
                    ind = {"close": d["close"]}
                    for name, fn, w in inds:
                        ind[name] = getattr(N, fn)(d["close"], w)
                    want_buy, want_sell = LOGIC_FUNCS[logic](ind, params)
                    _, got_buy, got_sell = valpha_eval.evaluate_naive(STRATEGIES[key]["spec"], d["close"])
                    self.assertEqual(got_buy, [bool(x) for x in want_buy], tkr)
                    self.assertEqual(got_sell, [bool(x) for x in want_sell], tkr)

    # ----- compiled engine -----

    def compiled_output(self, binary, extra=()):
        out = os.path.join(self.tmp, "out_" + os.path.basename(binary) + "".join(extra))
        proc = subprocess.run([binary, self.data, out, *extra], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return out

    def assert_compiled_matches_naive(self, binary, cfg, spec_cols=None):
        rn, _ = self.run_python_engines(cfg)
        out = self.compiled_output(binary)
        with open(os.path.join(out, "trades.csv")) as f:
            rows = list(csv.reader(f))[1:]
        got = [(r[0], r[1], r[3], r[6]) for r in rows]
        want = [(t["ticker"], t["entry_date"], t["exit_date"], f"{t['pnl']:.2f}") for t in rn["trades"]]
        self.assertEqual(got, want)
        with open(os.path.join(out, "summary.txt")) as f:
            final = float(f.read().splitlines()[1].split(":")[1])
        self.assertAlmostEqual(final, rn["final_value"], places=2)
        # indicator columns (4 decimals in the CSV)
        with open(os.path.join(out, "indicators_signals.csv")) as f:
            table = list(csv.reader(f))
        header, body = table[0], iter(table[1:])
        cols = header[3:-1]
        self.assertEqual(cols, cfg["indicator_names"])
        for tkr, d in rn["tickers_data"].items():
            for i in range(len(d["dates"])):
                row = next(body)
                self.assertEqual((row[0], row[1]), (tkr, d["dates"][i]))
                for ci, name in enumerate(cols):
                    a, c = d[name][i], row[3 + ci]
                    self.assertEqual(a is None, c == "---", f"{tkr} {i} {name}")
                    if a is not None:
                        self.assertAlmostEqual(a, float(c), delta=6e-5 + 1e-9 * abs(a))

    def test_compiled_equals_naive_for_every_strategy(self):
        for key, cfg in STRATEGIES.items():
            binary = os.path.join(COMPILER, "bin", f"{key}_runtime")
            with self.subTest(strategy=key):
                if not os.path.exists(binary):
                    self.skipTest("compiled engine not built (cd compiler && ./build_all.sh)")
                self.assert_compiled_matches_naive(binary, cfg)

    def test_simd_and_scalar_and_thread_counts_are_bit_identical(self):
        binary = os.path.join(COMPILER, "bin", "volatility_breakout_runtime")
        if not os.path.exists(binary):
            self.skipTest("compiled engine not built")
        base = self.compiled_output(binary)
        for extra in (("--scalar",), ("--threads", "1"), ("--threads", "3"), ("--scalar", "--threads", "2")):
            other = self.compiled_output(binary, extra)
            for fname in ("trades.csv", "equity_curve.csv", "summary.txt", "indicators_signals.csv"):
                with open(os.path.join(base, fname), "rb") as a, open(os.path.join(other, fname), "rb") as b:
                    self.assertEqual(a.read(), b.read(), f"{fname} differs for {extra}")

    def test_arithmetic_demo_across_all_engines(self):
        """examples/arithmetic_demo.valpha: nested expressions, constants, named booleans, CSE,
        numeric CROSSOVER args -- naive vs pandas vs freshly compiled binary."""
        if not os.path.exists(VALPHAC):
            self.skipTest("compiler/valphac not built")
        with open(os.path.join(COMPILER, "examples", "arithmetic_demo.valpha")) as f:
            text = f.read()
        text = text.replace("UNIVERSE = { HONA, VSNT };", "UNIVERSE = {};")
        src = os.path.join(self.tmp, "arith_demo.valpha")
        with open(src, "w") as f:
            f.write(text)
        bin_dir = os.path.join(self.tmp, "bin")
        proc = subprocess.run([VALPHAC, src, bin_dir], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr)
        cfg = {"initial_capital": 250000.0, "allocation": 0.25, "spec": valpha_lang.parse_file(src)}
        cfg["indicator_names"] = list(cfg["spec"].output_columns)
        rn, rv = self.run_python_engines(cfg)
        self.assertEqual([trade_key(t) for t in rn["trades"]], [trade_key(t) for t in rv["trades"]])
        self.assert_compiled_matches_naive(os.path.join(bin_dir, "arith_demo_runtime"), cfg)

    def test_ablation_builds_match_default_build(self):
        if not os.path.exists(VALPHAC):
            self.skipTest("compiler/valphac not built")
        src = os.path.join(COMPILER, "strategies", "volatility_breakout.valpha")
        bin_dir = os.path.join(self.tmp, "abl")
        outs = {}
        for tag, flags in (("full", []), ("nofuse", ["--no-fuse"]), ("nocse", ["--no-cse"])):
            proc = subprocess.run([VALPHAC, *flags, f"--tag={tag}", src, bin_dir], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout[-1500:] + proc.stderr)
            outs[tag] = self.compiled_output(os.path.join(bin_dir, f"volatility_breakout_{tag}_runtime"))
        for tag in ("nofuse", "nocse"):
            for fname in ("trades.csv", "summary.txt", "indicators_signals.csv"):
                with open(os.path.join(outs["full"], fname), "rb") as a, open(os.path.join(outs[tag], fname), "rb") as b:
                    self.assertEqual(a.read(), b.read(), f"{fname} differs for {tag}")


class SanityCheckTests(unittest.TestCase):
    def test_io_spec_worked_example(self):
        import sanity_check
        self.assertTrue(sanity_check.run())


if __name__ == "__main__":
    unittest.main()

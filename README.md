# VectorAlpha - naive / vectorized / compiled, one strategy file for all three

Three implementations of the same backtests, benchmarked against each other: a **naive Python** loop engine, a
**vectorized pandas/NumPy** engine, and a **compiled C++** engine produced by the `.valpha` compiler in `compiler/`.
A strategy is a single `.valpha` file under `compiler/strategies/`; all three engines read that same file, read the
same CSVs and write output in the same format, so they are directly comparable. The tests verify they agree trade for trade.

## Setup

```
pip install -r requirements.txt
cd compiler && ./build_all.sh && cd ..   # builds valphac + compiles every strategy into compiler/bin/
                                          # (needed for menu option 3; options 1/2 work without it)
```
Needs g++ (or set `CXX`) for the compiled engine.

## Where the price data lives

One setting controls it for all three engines: **`DATA_DIR_SETTING` in `src/config.py`**. It currently points at
`../Trading Bot/data` (a `Trading Bot` project sitting next to this one, e.g. `VsCode/Trading Bot/data`). Edit that one line
if yours is elsewhere (an absolute path works: `"C:/Users/you/VsCode/Trading Bot/data"`), or override it for a single run:

```
VALPHA_DATA_DIR="/path/to/data" python main.py        # Windows PowerShell: $env:VALPHA_DATA_DIR="C:\path\to\data"; python main.py
```
Each ticker is one `<TICKER>.csv` with at least `Date` and `Close` columns (yfinance format). If the folder has no CSVs
but a `price_data` subfolder does, that is used. The compiled engine is handed the same folder, so nothing else needs
changing. If the folder cannot be found you get a warning with the exact path tried, and the bundled sample data
(`data/price_data`, if you still have it) is used instead. Menu option 5 shows which folder and tickers were picked up.

(`data/demo_price_data`, which the sanity check writes itself, is separate and stays inside this project.)

## Run it

```
python main.py
```
1. **Run NAIVE engine** - pure-Python loop engine.
2. **Run VECTORIZED engine** - pandas/NumPy engine.
3. **Run COMPILED engine** - the `.valpha`-compiled C++ binary (run `compiler/build_all.sh` once first).

   Run any two or three of 1/2/3 on the **same strategy and ticker count** and each run merges with the others into one
   comparison table in `results/timing_comparison.txt`.
4. **Run IO-spec sanity check** - the exact STOCKA/STOCKB worked example, checked PASS/FAIL against the spec's answer key
   for every available engine.
5. **List available tickers.**  6. **View saved timing comparison log.**  7. **Exit.**

## Strategies

Every `compiler/strategies/*.valpha` file is a strategy (the file name is its menu key): nothing is hard-coded in Python.
Drop in a new file and it appears in the menu for all three engines (rebuild the compiled engine with `build_all.sh`).
Thirteen ship:

| strategy | idea |
|---|---|
| `crossover_rsi` | SMA(3)/SMA(7) crossover filtered by RSI(5) < 70 (the IO spec's worked example) |
| `trend_follow` | SMA(50)/SMA(200) golden cross filtered by RSI(14) < 70 |
| `volatility_breakout` | close breaks above / below an EMA(20) +/- STDEV(10) band |
| `macd_trend` | MACD(12,26,9) signal-line cross while above the 100-day average (EMA *of* MACD) |
| `bollinger_reversion` | buy the bounce off the lower Bollinger band, exit at the mean |
| `bollinger_squeeze_breakout` | upper-band breakout that follows a volatility squeeze |
| `donchian_breakout` | Turtle-style 20-bar high breakout, 10-bar low exit (`HIGHEST`/`LOWEST`/`LAG`) |
| `stochastic_pullback` | stochastic %K/%D cross in an uptrend |
| `momentum_roc` | 20-day rate-of-change turning up inside a strong 60-day trend |
| `zscore_reversion` | buy the recovery from -2 sigma, exit at the mean |
| `triple_ema_regime` | pullback to the fast EMA when 8/21/55 are stacked and volatility is calm (named booleans) |
| `connors_rsi2` | buy a deep 2-day RSI dip above the 200-day average, sell the bounce |
| `efficiency_trend` | EMA(20) cross, only when Kaufman's efficiency ratio says the move is clean (`ABS`/`LAG`) |

The language (operations table, arithmetic, nesting, constants, named booleans) and the compiler are documented in
`compiler/README.md`; `compiler/examples/arithmetic_demo.valpha` is a tour.

## Tests

```
python -m unittest discover -s tests -v     # 23 tests: front-ends, numerics, config, three-engine agreement, IO-spec example
cd compiler && make test                    # 57 compiler checks
```
Compiled-engine tests skip themselves if `compiler/bin` has not been built.

## Layout

```
main.py                    demo menu
src/valpha_lang.py         Python lexer/parser/checker for .valpha (mirrors the C++ front-end)
src/valpha_eval.py         naive + pandas evaluators of a parsed strategy
src/config.py              WHERE THE PRICE DATA LIVES (DATA_DIR_SETTING) -- the one place to change
src/strategies.py          registry: loads compiler/strategies/*.valpha
src/indicators_*.py        SMA/EMA/RSI/STDEV/CROSSOVER/CROSSUNDER, loop-based and pandas-based
src/backtest_*.py          indicator -> signal -> single-position portfolio simulation
src/signal_logic_*.py      the original hand-written strategy logic, kept only as an independent test oracle
src/benchmark.py, output_writer.py, data_loader.py, sanity_check.py
compiler/                  valphac: lexer, parser, semantic, IR, optimizer, codegen (see compiler/README.md)
tests/test_engines.py      cross-engine test suite;  tests/test_config.py  data-folder configuration
```

## Performance (what we measured, and how much to trust it)

Synthetic data, 128 tickers x 2000 bars, **one CPU core**, compiled engine `--threads 1`:

| strategy | naive | pandas | compiled | speedup vs naive / pandas |
|---|---|---|---|---|
| crossover_rsi | 1.12 s | 1.34 s | 15 ms | 73x / 87x |
| trend_follow | 3.67 s | 1.35 s | 10 ms | 356x / 131x |
| volatility_breakout | 1.24 s | 1.27 s | 13 ms | 94x / 96x |

Trades were identical across the three engines (e.g. all 14,972 `crossover_rsi` trades). Ablations: the SIMD kernel alone is
~3.3x faster than the scalar kernel, but the whole compute phase (including transposing in and copying results out of the lanes) is only
~1.3-1.5x faster, and the sequential simulation is a fixed cost on top (whole run ~1.2x); fusing the bar loop adds only 0-9% here because 2000-bar series fit in cache.
These are synthetic data on a 1-core sandbox: re-measure on your real dataset and machine (multi-core threading is untested
here), and treat the ratios as indicative. With few tickers/rows, pandas can look *slower* than the naive loop (fixed per-call
overhead).

## Not built yet

`open/high/low/volume` in the DSL, stateful rules (stop-loss / take-profit), a real 1000-ticker benchmark dataset, hand-tuned intrinsics (SIMD uses portable vector
extensions), and any frontend.

## Quick start / demo

See [`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md) for the full run-and-demo guide. On a fresh clone, `python main.py` offers to generate a synthetic dataset (`src/demo_data.py`) so everything runs without real market data.

**Data location:** `DATA_DIR_SETTING` in `src/config.py` now defaults to the project's own `data` folder
(CSVs directly in it or in one subfolder). **Smoke test:** `python smoke_test.py` runs a tiny hardcoded dataset through
all three engines and validates every output against hand-calculated values.


## Web UI, UNIVERSE and run history

```
python main.py --web            # opens http://127.0.0.1:8765  (--port N, --no-browser); or menu option w
```
* **Tickers come from the strategy's `UNIVERSE`** in all three engines and in every menu/UI path; nothing asks for them.
  `UNIVERSE = { A, AA };` runs just A and AA (names are the CSV file names, case-sensitive), `UNIVERSE = {};` or no line runs every CSV in the data folder.
  This replaces the older note that the Python engines ignore `UNIVERSE`.
* **Run tab** - pick a strategy and any combination of engines. The compiled engine is (re)built automatically when its binary is missing or older than the `.valpha` file. One execution writes ONE table to `results/timing_comparison.txt`.
  The latest run shows its summary per engine, a timing comparison and one clickable button per output file (opens it in the file explorer).
* **Strategies tab** - view, edit, delete and create `.valpha` strategies (live validation, a placeholder skeleton, a sample copied from any existing strategy and a DSL quick reference).
* **Tickers tab** - every CSV in the data folder; click one to show it in the file explorer.
* **History page** (`/history`) - every execution, from the web UI or the terminal menu, stored in `results/history.json`.
The server is standard-library only, listens on 127.0.0.1 and needs no extra packages.

### Update: one entry point

`python main.py` now starts the web UI directly (there is no terminal menu any more); add `--port N` or `--no-browser` if needed.
The sidebar holds every control: Run, History (runs + timing log), Strategies, Tickers (100 at a time, "Load more" / "Load all"),
Compiler (one strategy, all outdated, or rebuild everything), Checks (IO-spec sanity check, smoke test) and Data (data folder, synthetic data).
This supersedes the `--web` flag and the menu options mentioned in the previous section and in `docs/DEMO_GUIDE.md`.

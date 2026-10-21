# VectorAlpha compiler (`valphac`)

`valphac` lexes, parses, type-checks, lowers to a three-address IR, optimizes
and compiles a `.valpha` strategy into a native, multithreaded, SIMD C++
binary. The same `.valpha` files are also what the naive and pandas Python
engines run (see the top-level `README.md`), so one file defines a strategy for
all three engines.

## Build and test

```
make                 # builds ./valphac
make test            # compiler regression tests (57 checks, needs g++)
./build_all.sh       # builds valphac, then compiles every strategies/*.valpha to bin/<name>_runtime
```
(or `cmake -B build && cmake --build build`.) Python-side tests: `python -m unittest discover -s tests`
from the project root.

## Running valphac

```
./valphac [flags] strategy.valpha                # run every phase through optimization, print each stage
./valphac [flags] strategy.valpha <output_dir>   # also generate + compile <output_dir>/<name>_runtime
```
Flags (each turns one optimization off, for ablation benchmarks):

| flag | effect |
|---|---|
| `--no-cse` | skip indicator-level common-subexpression elimination |
| `--no-fuse` | one pass per instruction over full arrays, scalar only (instead of the fused bar loop + SIMD batches) |
| `--tag=NAME` | name the binary `<name>_<NAME>_runtime` so ablation builds can sit side by side |

`CXX=clang++ ./valphac ...` overrides the compiler (default `g++`). The generated code is built with
`-O3 -march=native -ffp-contract=off -fno-math-errno` (retrying without `-march=native` if rejected).

Running a compiled strategy:

```
bin/<name>_runtime <data_dir> <results_dir> [--threads N] [--scalar]
```
`--threads N` sets the worker count (default: all cores); `--scalar` disables SIMD batching without
recompiling. It writes the same four artifacts as the Python engines (`indicators_signals.csv`,
`trades.csv`, `equity_curve.csv`, `summary.txt`) and prints `ELAPSED_SECONDS` (compute + simulate, no file
I/O) plus `COMPUTE_SECONDS` / `SIM_SECONDS`.

## The DSL (v2)

```
INITIAL_CAPITAL = 100000;          // required, > 0
ALLOCATION = 0.5;                  // required, 0 < x <= 1 (fraction of INITIAL_CAPITAL per trade)
UNIVERSE = { HONA, VSNT };         // optional; {} or absent = every CSV in the data directory

WINDOW = 20;                       // constants fold at compile time and may be used as window sizes
EMA_20   = EMA(close, WINDOW);
UPPER    = EMA_20 + 1.5 * STDEV(close, 10);       // arithmetic + - * /, unary -, ( ) grouping, nesting
PCT      = (close - SMA(close, 20)) / SMA(close, 20) * 100;
OVERSOLD = CROSSOVER(RSI(close, 14), 30);         // named booleans; CROSSOVER args may be numbers

BUY  WHEN OVERSOLD OR (CROSSOVER(close, UPPER) AND PCT < 10);
SELL WHEN CROSSUNDER(close, EMA_20 - 5);
```

### Operations available

| operation | meaning |
|---|---|
| `close` | the daily closing price series (the only price series so far) |
| `SMA(x, w)` / `EMA(x, w)` | simple / exponential moving average (EMA seeded with the mean of its first `w` values) |
| `RSI(x, w)` | relative strength index, 0-100 |
| `STDEV(x, w)` | rolling population standard deviation |
| `HIGHEST(x, w)` / `LOWEST(x, w)` | rolling max / min over the last `w` values, including the current one |
| `LAG(x, n)` | the value `n` bars ago |
| `ABS(x)` | absolute value |
| `+ - * /`, unary `-`, `( )` | arithmetic on series and numbers (`x / 0` is a missing value) |
| `< > <= >= == !=` | comparisons (tolerant, see below) |
| `CROSSOVER(a, b)` / `CROSSUNDER(a, b)` | true on the bar where `a` crosses above / below `b`; either argument may be a plain number |
| `AND`, `OR` | combine conditions |

`x` may be any series expression, so indicators nest freely (`EMA(MACD, 9)`, `SMA(ABS(close - LAG(close, 1)), 10)`).
Windows and lags (`w`, `n`) must be constant integers >= 1 (a literal, a constant like `WINDOW`, or arithmetic on constants).
`//` starts a comment; a header comment `// description: ...` supplies the text shown in the Python menu.
Rules are stateless: a `BUY`/`SELL` condition sees only the market data, not whether you hold a position, so there are
no stop-loss or take-profit orders yet. Explicit checks like `close < SMA_50 * 0.97` can approximate a stop.
Precedence, loosest to tightest: `OR`, `AND`, comparison, `+ -`, `* /`, unary `-`. Comparisons do not chain.
A header comment `// description: ...` supplies the text shown in the Python menu.

### Numerical contract (identical in all three engines)

* Comparisons and `CROSSOVER/CROSSUNDER` are **tolerant**: with `tol = 1e-9 * max(1, |a|, |b|)` (relative for large
  values, an absolute floor near zero), `a < b` means `b - a > tol`, `a <= b` means `a - b <= tol`, and so on. Incremental
  (running-sum) and recomputed indicators differ in the last few bits -- e.g. a rolling sum that should be exactly 0 can be left
  at -1e-15 -- and without this a tie in exact arithmetic could flip a signal depending on the engine.
* Anything involving a missing value (indicator warm-up, upstream `x / 0`) is missing; a comparison with a missing value is false.
* Division by zero yields a missing value. Windowed indicators are missing until their whole window is present and
  recover once a missing value leaves the window, so indicators can be nested (`SMA(RSI(close, 14), 5)`).
* `EMA` is seeded with the mean of its first `w` values. `RSI` of a flat window is 0, and "average loss ~ 0 with gains" is 100,
  where "~ 0" is `1e-9 * max(1, |price|)` (so add/remove residue cannot flip it). `STDEV` is the population standard deviation.

## Pipeline

**Phase 1 - Lexer + parser** (`token/lexer/ast/parser`). Recursive descent; grammar in `include/parser.hpp`.

**Phase 2 - Semantic analysis** (`semantic`). Symbol table, built-in signatures, SCALAR/SERIES/BOOL/SET typing,
constant folding (`try_eval_scalar`), no forward references (so no dependency cycles), config validation, literal
division-by-zero rejection.

**Phase 3 - Three-address IR** (`ir`). Every series computation is one instruction with at most two operands; nested
sub-expressions become `%tN` temporaries; scalar sub-expressions fold away; named booleans inline into rules. Each
instruction keeps the `cross_sectional` and `sliding_window` domain tags.
`UPPER = EMA(close,20) + 2*STDEV(close,10)` lowers to `%t0=EMA(close,w=20)`, `%t1=STDEV(close,w=10)`, `%t2=MUL(2,%t1)`, `UPPER=ADD(%t0,%t2)`.

**Phase 4 - Optimization passes** (`optimize`).
*CSE* merges identical `(op, operands, window)` computations however they were nested or named (commutative ops are
canonicalised; a merge keeps the user-visible name). *Incremental planning* picks the algorithm per windowed op: O(1)-per-bar running sums / recurrences for
SMA/EMA/RSI/STDEV, a ring buffer for LAG, and an O(w) ring scan (vectorized across lanes) for HIGHEST/LOWEST. *Fusion analysis* decides which values are stored (CSV columns) and which live in registers.

**Phase 5 - Code generation** (`codegen`). One self-contained C++17 file per strategy:

* **One fused bar loop per ticker** evaluates every indicator and rule in a single pass (`--no-fuse` gives the array-at-a-time layout instead).
* **SoA + SIMD:** tickers sharing a trading calendar are processed 4 at a time, one SIMD register holding the same bar of 4
  tickers. Leftover tickers use the same kernel with one scalar lane. The kernel is a single generated template instantiated for
  both lane types, so scalar and SIMD results are bit-identical by construction (verified by the tests, including `--scalar`
  and different thread counts). SIMD is GCC/Clang **vector extensions**, lowered to AVX2/AVX-512/NEON by `-march=native`;
  there are no hand-written intrinsics.
* **Threads:** batches are distributed over `std::thread` workers (`cross_sectional` licenses this).
* **Simulation:** the sequential single-position portfolio simulation, kept sequential on purpose (path-dependent), using dense date-indexed tables.

## What the tests check (`make test`, `tests/`)

Compiler: good programs accepted, each broken example rejected with the right diagnostic, precedence / constant folding /
CSE behave as documented, every strategy compiles in all three build modes. Python (`tests/test_engines.py`, `tests/test_config.py`): naive == pandas
trades on 13 synthetic tickers (flat stretch, late starts, calendar gap); compiled == naive for **every strategy in `strategies/`** (trades,
final value, every indicator value); SIMD == scalar == any thread count, byte for byte; `--no-fuse` and `--no-cse` builds match
the default build byte for byte; the DSL evaluator reproduces the original hand-coded strategies; the IO-spec worked example.

## Known limitations

* Only the `close` price is available (no `open/high/low/volume` yet, so no ATR or true Donchian/Keltner channels), and
  there is no `NOT` (`NOT (a < b)` would be true during indicator warm-up, which is a trap; use the opposite comparison).
* Rules are stateless: no stop-loss / take-profit / trailing stops, no position sizing rules beyond `ALLOCATION`.
* Window sizes must be compile-time constants.
* SIMD width is fixed at 4 doubles; tickers whose calendars differ run in scalar lanes.
* Mark-to-market for a ticker with no bar on a given date uses its *entry* price (all three engines share this quirk).
* pandas' `rolling().std()` returns tiny garbage (~1e-6) for windows of constant prices where the true value is exactly 0;
  the naive and compiled engines return 0. It does not change any signal in the tests.
* The Python engines ignore `UNIVERSE` (the menu picks tickers); the compiled engine honours it.

"""
benchmark.py
-------------
Runs the naive, vectorized and compiled engines on a strategy.

Which tickers are used is decided by the strategy itself: the UNIVERSE of its
.valpha file. `UNIVERSE = { A, AA };` runs just A and AA; `UNIVERSE = {};` (or no
UNIVERSE line) runs every CSV in the data folder. Nothing asks for tickers.

run_engines() is the entry point used by the web UI and the terminal menu. It
runs the chosen engines, writes ONE timing table to results/timing_comparison.txt
for the whole execution (not one per engine) and appends one entry to
results/history.json. Engines not run this time but run earlier on the same
(strategy, ticker count) are merged into that table; the rest show TBD.
"""

import os
import json
import shutil
import subprocess
import sys
import tempfile
import datetime

from strategies import STRATEGIES
import data_loader
import backtest_naive
import backtest_vectorized
import history
import output_writer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
PENDING_PATH = os.path.join(RESULTS_DIR, ".pending_run.json")
LOG_PATH = os.path.join(RESULTS_DIR, "timing_comparison.txt")
COMPILER_DIR = os.path.join(PROJECT_ROOT, "compiler")

ENGINE_LABELS = {
    "naive": "Naive Python",
    "vectorized": "pandas / NumPy",
    "compiled": "VectorAlpha (compiled)",
}
# Order engines should appear in printed/logged tables.
ENGINE_ORDER = ["naive", "vectorized", "compiled"]

# The four artifacts every engine writes under results/<strategy>/<engine>/.
OUTPUT_FILES = ("indicators_signals.csv", "trades.csv", "equity_curve.csv", "summary.txt")


# ------------------------------------------------------------ universe

def resolve_tickers(strategy_key, available=None):
    """
    Tickers to run for a strategy, from its UNIVERSE. Returns (tickers, missing):
      UNIVERSE empty  -> every CSV in the data folder (sorted), missing = []
      UNIVERSE = {A, AA} -> exactly those, in the order written; names with no
                            <name>.csv in the data folder are reported in `missing`
                            (with a hint when only the letter case differs).
    """
    if available is None:
        available = data_loader.list_tickers()
    universe = STRATEGIES[strategy_key]["universe"]
    if not universe:
        return list(available), []
    have = set(available)
    by_upper = {t.upper(): t for t in available}
    chosen, missing = [], []
    for name in universe:
        if name in have:
            if name not in chosen:
                chosen.append(name)
        elif name.upper() in by_upper:
            missing.append(f"{name} (found '{by_upper[name.upper()]}.csv' -- ticker names are case-sensitive)")
        else:
            missing.append(name)
    return chosen, missing


def _tickers_or_report(strategy_key, tickers=None):
    """The ticker list to run (honouring UNIVERSE), or None after printing why there is nothing to run."""
    if tickers is None:
        tickers, missing = resolve_tickers(strategy_key)
        if missing:
            print(f"Warning: UNIVERSE names with no CSV in {data_loader.DATA_DIR}: {', '.join(missing)}")
    if not tickers:
        universe = STRATEGIES[strategy_key]["universe"]
        if universe:
            print(f"None of the UNIVERSE tickers ({', '.join(universe)}) have a CSV in {data_loader.DATA_DIR}.")
        else:
            print(f"No data found in {data_loader.DATA_DIR}. Add CSV files there or fix DATA_DIR_SETTING in src/config.py.")
        return None
    return tickers


def _universe_text(strategy_key, n):
    universe = STRATEGIES[strategy_key]["universe"]
    return f"UNIVERSE = {{ {', '.join(universe)} }}" if universe else f"UNIVERSE empty -> all {n} ticker(s) in the data folder"


# ------------------------------------------------------- compiled engine

def compiled_binary(strategy_key):
    """compiler/bin/<strategy>_runtime, built by compiler/build_all.* from the
    same compiler/strategies/<strategy>.valpha file the Python engines read."""
    path = os.path.join(COMPILER_DIR, "bin", f"{strategy_key}_runtime")
    if not os.path.exists(path) and os.path.exists(path + ".exe"):   # MinGW appends .exe on Windows
        return path + ".exe"
    return path


def compiled_status(strategy_key):
    """'ok', 'missing' (never built) or 'stale' (the .valpha file is newer than its binary)."""
    binary = compiled_binary(strategy_key)
    if not os.path.exists(binary):
        return "missing"
    src = STRATEGIES.get(strategy_key, {}).get("source_path")
    if src and os.path.exists(src) and os.path.getmtime(binary) < os.path.getmtime(src):
        return "stale"
    return "ok"


def valphac_path():
    for name in ("valphac", "valphac.exe"):
        p = os.path.join(COMPILER_DIR, name)
        if os.path.exists(p):
            return p
    return None


def cxx_name():
    return (os.environ.get("CXX") or "g++").split()[0]


def _stream(cmd, cwd):
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1)
    for line in proc.stdout:
        print(line.rstrip())
    return proc.wait()


def build_everything():
    """Builds valphac and compiles every strategy (compiler/build_all.py, else build_all.sh)."""
    if shutil.which(cxx_name()) is None:
        print(f"Building needs a C++ compiler ('{cxx_name()}') on PATH (on Windows: MSYS2 mingw-w64 gcc).")
        return False
    py = os.path.join(COMPILER_DIR, "build_all.py")
    if os.path.exists(py):
        cmd = [sys.executable, py]
    elif shutil.which("bash") and shutil.which("make"):
        cmd = ["bash", "build_all.sh"]
    else:
        print("Neither compiler/build_all.py nor bash+make is available to build the compiled engine.")
        return False
    print("Building the compiler and every strategy (about a minute) ...")
    rc = _stream(cmd, COMPILER_DIR)
    print("Build finished." if rc == 0 else "Build failed -- see the messages above.")
    return rc == 0


def compile_strategy(strategy_key):
    """Compiles just this strategy with the already-built valphac. True on success."""
    vc = valphac_path()
    if vc is None:
        return False
    if shutil.which(cxx_name()) is None:
        print(f"C++ compiler '{cxx_name()}' not found on PATH.")
        return False
    bin_dir = os.path.join(COMPILER_DIR, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    old = compiled_binary(strategy_key)
    if os.path.exists(old):
        os.remove(old)
    print(f"Compiling '{strategy_key}' with valphac ...")
    proc = subprocess.run([vc, STRATEGIES[strategy_key]["source_path"], bin_dir], cwd=COMPILER_DIR,
                          capture_output=True, text=True, errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    with open(os.path.join(bin_dir, f"{strategy_key}.build.log"), "w", encoding="utf-8") as f:
        f.write(out)
    ok = proc.returncode == 0 and os.path.exists(compiled_binary(strategy_key))
    if ok:
        print(f"Compiled {strategy_key}.")
    else:
        print("Compile failed:\n" + "\n".join(out.splitlines()[-15:]))
    return ok


def compiled_at(strategy_key):
    """Modification time of the strategy's binary as an ISO string, or None."""
    binary = compiled_binary(strategy_key)
    if not os.path.exists(binary):
        return None
    return datetime.datetime.fromtimestamp(os.path.getmtime(binary)).isoformat(timespec="seconds")


def compile_one(strategy_key):
    """Compiles one strategy (builds valphac itself first if it does not exist yet)."""
    if valphac_path() is None:
        print("valphac is not built yet -- building the compiler and every strategy first.")
        return build_everything()
    return compile_strategy(strategy_key)


def compile_pending(jobs=None):
    """Compiles every strategy whose binary is missing or outdated, in parallel."""
    if valphac_path() is None:
        print("valphac is not built yet -- building the compiler and every strategy.")
        return build_everything()
    todo = [k for k in STRATEGIES if compiled_status(k) != "ok"]
    if not todo:
        print("Every strategy is already compiled and up to date.")
        return True
    from concurrent.futures import ThreadPoolExecutor
    jobs = max(1, min(jobs or os.cpu_count() or 2, len(todo)))
    print(f"Compiling {len(todo)} strategy(ies) with {jobs} parallel job(s) ...")
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(compile_strategy, todo))
    failed = [k for k, ok in zip(todo, results) if not ok]
    print(f"Done: {len(todo) - len(failed)} compiled" + (f", {len(failed)} FAILED: {', '.join(failed)}" if failed else "."))
    return not failed


def ensure_compiled(strategy_key, build=True):
    """True if the strategy's binary is present and current, building it first if needed and allowed."""
    if compiled_status(strategy_key) == "ok":
        return True
    if not build:
        return False
    if valphac_path() is not None:
        return compile_strategy(strategy_key)
    return build_everything() and compiled_status(strategy_key) != "missing"


# ---------------------------------------------------------- single engines

def run_naive_only(strategy_key, tickers=None, write_artifacts=True, log=True):
    cfg = STRATEGIES[strategy_key]
    indicator_names = cfg["indicator_names"]

    print(f"\nStrategy: {strategy_key} -- {cfg['description']}")
    tickers = _tickers_or_report(strategy_key, tickers)
    if tickers is None:
        return None
    print(_universe_text(strategy_key, len(tickers)))
    print("Loading data ...")
    naive_data = data_loader.load_naive(tickers)
    n_tickers = len(naive_data)
    print(f"Loaded {n_tickers} ticker(s).")
    if n_tickers == 0:
        print("None of the selected CSV files contained usable rows.")
        return None

    print("Running naive (pure Python loop) engine ...")
    elapsed, results = backtest_naive.run_backtest(cfg, naive_data, verbose=True)
    print(f"\nNaive Python: {elapsed:.6f}s  |  trades: {len(results['trades'])}  |  "
          f"final value: {results['final_value']:.2f}")

    if write_artifacts:
        out_dir = os.path.join(RESULTS_DIR, strategy_key, "naive")
        _write_all_artifacts(out_dir, cfg, indicator_names, results)
        print(f"Output artifacts written under: {out_dir}/")

    if log:
        _log_runs(strategy_key, n_tickers, {"naive": elapsed})
    return {"elapsed": elapsed, "n_tickers": n_tickers, "results": results}


def run_vectorized_only(strategy_key, tickers=None, write_artifacts=True, log=True):
    cfg = STRATEGIES[strategy_key]
    indicator_names = cfg["indicator_names"]

    print(f"\nStrategy: {strategy_key} -- {cfg['description']}")
    tickers = _tickers_or_report(strategy_key, tickers)
    if tickers is None:
        return None
    print(_universe_text(strategy_key, len(tickers)))
    print("Loading data ...")
    vec_data = data_loader.load_vectorized(tickers)
    n_tickers = len(vec_data)
    print(f"Loaded {n_tickers} ticker(s).")
    if n_tickers == 0:
        print("None of the selected CSV files contained usable rows.")
        return None

    print("Running vectorized (pandas/NumPy) engine ...")
    elapsed, results = backtest_vectorized.run_backtest(cfg, vec_data, verbose=True)
    print(f"\npandas / NumPy: {elapsed:.6f}s  |  trades: {len(results['trades'])}  |  "
          f"final value: {results['final_value']:.2f}")

    if write_artifacts:
        out_dir = os.path.join(RESULTS_DIR, strategy_key, "vectorized")
        _write_all_artifacts(out_dir, cfg, indicator_names, results)
        print(f"Output artifacts written under: {out_dir}/")

    if log:
        _log_runs(strategy_key, n_tickers, {"vectorized": elapsed})
    return {"elapsed": elapsed, "n_tickers": n_tickers, "results": results}


def run_compiled_only(strategy_key, tickers=None, write_artifacts=True, log=True):
    """
    Runs the compiled C++ binary valphac produced for this strategy. Stages a
    temp directory containing just the strategy's UNIVERSE tickers (every CSV when
    UNIVERSE is empty), invokes the binary and parses its `ELAPSED_SECONDS=...` line.
    """
    binary = compiled_binary(strategy_key)
    if not os.path.exists(binary):
        print(f"\nCompiled binary not found: {binary}")
        print("Build it first (web UI: 'Build compiled engine', or:  python compiler/build_all.py)")
        return None

    print(f"\nStrategy: {strategy_key} -- {STRATEGIES[strategy_key]['description']}")
    tickers = _tickers_or_report(strategy_key, tickers)
    if tickers is None:
        return None
    print(_universe_text(strategy_key, len(tickers)))
    print(f"Running compiled VectorAlpha engine on {len(tickers)} ticker(s) ...")

    tmp_dir = tempfile.mkdtemp(prefix="valpha_compiled_")
    out_dir = os.path.join(RESULTS_DIR, strategy_key, "compiled")
    try:
        found = 0
        for tkr in tickers:
            src = os.path.join(data_loader.DATA_DIR, f"{tkr}.csv")
            if os.path.exists(src):
                dst = os.path.join(tmp_dir, f"{tkr}.csv")
                try:
                    os.symlink(os.path.abspath(src), dst)
                except OSError:          # e.g. Windows without symlink privileges
                    shutil.copyfile(src, dst)
                found += 1
        if found == 0:
            print("No matching CSV files found to stage for the compiled engine.")
            return None

        if write_artifacts:
            os.makedirs(out_dir, exist_ok=True)

        proc = subprocess.run(
            [binary, tmp_dir, out_dir if write_artifacts else tmp_dir],
            capture_output=True, text=True, errors="replace",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if proc.returncode != 0:
        print("Compiled binary failed:")
        print(proc.stdout)
        print(proc.stderr)
        return None

    elapsed = None
    for line in proc.stdout.splitlines():
        print(line)
        if line.startswith("ELAPSED_SECONDS="):
            elapsed = float(line.split("=", 1)[1])

    if elapsed is None:
        print("Could not parse ELAPSED_SECONDS from the compiled binary's output.")
        return None

    if write_artifacts:
        print(f"Output artifacts written under: {out_dir}/")

    if log:
        _log_runs(strategy_key, found, {"compiled": elapsed})
    return {"elapsed": elapsed, "n_tickers": found}


def _write_all_artifacts(out_dir, cfg, indicator_names, results):
    output_writer.write_indicators_signals(
        os.path.join(out_dir, "indicators_signals.csv"),
        results["tickers_data"], indicator_names,
        results["buy_signals"], results["sell_signals"],
    )
    output_writer.write_trades(os.path.join(out_dir, "trades.csv"), results["trades"])
    output_writer.write_equity_curve(
        os.path.join(out_dir, "equity_curve.csv"),
        results["equity_dates"], results["equity_curve"],
    )
    output_writer.write_summary(
        os.path.join(out_dir, "summary.txt"),
        cfg["initial_capital"], results["final_value"], results["trades"],
        results["equity_curve"],
    )


# -------------------------------------------------- one execution, many engines

def output_dir(strategy_key, engine):
    return os.path.join(RESULTS_DIR, strategy_key, engine)


def read_summary(strategy_key, engine):
    """summary.txt of the latest (strategy, engine) run as {label: text}; {} if absent."""
    path = os.path.join(output_dir(strategy_key, engine), "summary.txt")
    out = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    out[k.strip()] = v.strip()
    return out


def run_engines(strategy_key, engines=None, source="menu"):
    """
    Runs the chosen engines (default: all three) on the strategy's UNIVERSE, writes
    ONE timing table and ONE history entry for the whole execution.
    Returns the history entry (dict), or None if nothing could be run.
    """
    if strategy_key not in STRATEGIES:
        print(f"Unknown strategy '{strategy_key}'.")
        return None
    wanted = ENGINE_ORDER if engines is None else engines
    engines = [e for e in ENGINE_ORDER if e in wanted]
    if not engines:
        print("No engine selected.")
        return None
    tickers = _tickers_or_report(strategy_key)
    if tickers is None:
        return None

    runners = {"naive": run_naive_only, "vectorized": run_vectorized_only, "compiled": run_compiled_only}
    ran = {}
    for e in engines:
        try:
            if e == "compiled" and not ensure_compiled(strategy_key):
                print("Skipping the compiled engine: it could not be built.")
                continue
            r = runners[e](strategy_key, tickers=tickers, log=False)
        except Exception as exc:  # one engine failing must not lose the others' results
            print(f"{ENGINE_LABELS[e]} engine failed: {type(exc).__name__}: {exc}")
            continue
        if r:
            ran[e] = r
    if not ran:
        return None

    n_tickers = next(iter(ran.values()))["n_tickers"]
    _log_runs(strategy_key, n_tickers, {e: r["elapsed"] for e, r in ran.items()})

    entry = history.record({
        "source": source,
        "strategy": strategy_key,
        "description": STRATEGIES[strategy_key]["description"],
        "n_tickers": n_tickers,
        "universe": list(STRATEGIES[strategy_key]["universe"]),
        "tickers": tickers[:200],
        "engines": {e: {"elapsed": r["elapsed"], "summary": read_summary(strategy_key, e)}
                    for e, r in ran.items()},
    })
    print(f"\nRecorded in history ({entry['id']}).")
    return entry


# ---- timing log: ONE table per execution, merged with earlier engine runs ----
# ---- on the same (strategy, n_tickers) via results/.pending_run.json       ----

def _load_pending():
    if os.path.exists(PENDING_PATH):
        try:
            with open(PENDING_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_pending(pending):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(PENDING_PATH, "w") as f:
        json.dump(pending, f)


def comparison_for(strategy_key, n_tickers):
    """{engine: seconds} known for this (strategy, ticker count): this execution plus earlier ones."""
    return dict(_load_pending().get(f"{strategy_key}::{n_tickers}", {}))


def forget_strategy(strategy_key):
    """Drops remembered timings for a strategy (call after it is edited or deleted)."""
    pending = _load_pending()
    kept = {k: v for k, v in pending.items() if not k.startswith(strategy_key + "::")}
    if kept != pending:
        _save_pending(kept)


def _log_runs(strategy_key, n_tickers, timings):
    """
    Appends ONE comparison table to timing_comparison.txt covering every engine run
    so far on this (strategy, n_tickers) -- `timings` are the engines of this
    execution, others come from earlier runs or show TBD. Speedup is relative to
    the naive run when one is known.
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)
    pending = _load_pending()
    key = f"{strategy_key}::{n_tickers}"
    entry = pending.get(key, {})
    entry.update(timings)
    pending[key] = entry

    naive_elapsed = entry.get("naive")

    is_new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a") as f:
        if is_new:
            f.write("VectorAlpha -- Naive Python vs pandas/NumPy vs compiled timing log\n")
            f.write("=" * 62 + "\n")
        f.write(f"\nRun at        : {datetime.datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"Strategy      : {strategy_key}\n")
        f.write(f"Tickers       : {n_tickers}\n")
        f.write(f"{'Implementation':<24}{'Time (s)':<15}{'Speedup':<20}\n")
        for e in ENGINE_ORDER:
            label = ENGINE_LABELS[e]
            if e not in entry:
                f.write(f"{label:<24}{'TBD':<15}{'TBD':<20}\n")
                continue
            t = entry[e]
            if e == "naive":
                speed_str = "1.0x (baseline)"
            elif naive_elapsed is not None and t > 0:
                speed_str = f"{naive_elapsed / t:.2f}x"
            else:
                speed_str = "(run naive to compare)"
            f.write(f"{label:<24}{t:<15.6f}{speed_str:<20}\n")
        f.write("-" * 62 + "\n")

    earlier = [e for e in ENGINE_ORDER if e in entry and e not in timings]
    if earlier:
        print(f"\nMerged with earlier run(s) for: {', '.join(ENGINE_LABELS[e] for e in earlier)} "
              f"-- full comparison logged.")
    _save_pending(pending)
    print(f"Timing appended to: {LOG_PATH}")

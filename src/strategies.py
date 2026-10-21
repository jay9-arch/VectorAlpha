"""
strategies.py
--------------
Strategy registry. Every strategy is a .valpha file under
compiler/strategies/ -- the same file the compiled engine is built from --
parsed by valpha_lang. Nothing is hand-copied into Python any more: add or
edit a .valpha file and all three engines pick it up (the compiled engine
after `cd compiler && ./build_all.sh`).

Each STRATEGIES[key] entry (key = file name without .valpha):
    description        - from the file's `// description: ...` header comment
    initial_capital    - INITIAL_CAPITAL
    allocation         - ALLOCATION (fraction of capital committed per trade)
    universe           - UNIVERSE tickers ([] = all). The compiled engine
                         honours it; the Python menu picks tickers itself.
    indicator_names    - user-defined series = indicators_signals.csv columns
    spec               - the parsed valpha_lang.Strategy the engines evaluate
    source_path        - the .valpha file
"""

import glob
import os

import valpha_lang

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRATEGY_DIR = os.path.join(PROJECT_ROOT, "compiler", "strategies")


def load_strategies(strategy_dir=STRATEGY_DIR):
    out = {}
    for path in sorted(glob.glob(os.path.join(strategy_dir, "*.valpha"))):
        spec = valpha_lang.parse_file(path)
        out[spec.name] = {
            "description": spec.description,
            "initial_capital": spec.initial_capital,
            "allocation": spec.allocation,
            "universe": spec.universe,
            "indicator_names": list(spec.output_columns),
            "spec": spec,
            "source_path": path,
        }
    return out


STRATEGIES = {}


def reload_strategies(strategy_dir=STRATEGY_DIR):
    """Re-reads compiler/strategies/*.valpha IN PLACE, so every module that did
    `from strategies import STRATEGIES` sees added / edited / deleted files."""
    fresh = load_strategies(strategy_dir)
    STRATEGIES.clear()
    STRATEGIES.update(fresh)
    return STRATEGIES


reload_strategies()

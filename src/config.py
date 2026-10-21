"""
config.py
----------
Where the price data lives. This is the ONE place to change it.

Every Python engine reads tickers through data_loader.py, which takes its
folder from here, and the compiled engine is simply handed that same folder on
its command line (benchmark.py passes it) -- so changing DATA_DIR_SETTING below
re-points all three engines at once.

Each ticker is one CSV named <TICKER>.csv with at least `Date` and `Close`
columns (the yfinance download format), e.g.  HONA.csv, VSNT.csv.

Resolution order for the data folder:
  1. the VALPHA_DATA_DIR environment variable, if set (handy for one-off runs:
        VALPHA_DATA_DIR="/path/to/data" python main.py )
  2. DATA_DIR_SETTING below.
A relative path is taken relative to this project's root folder; "~" and
environment variables are expanded; / and \\ both work. If the folder contains
no .csv files but has a `price_data` subfolder that does, the subfolder is used; failing that, the first
subfolder (alphabetically) containing .csv files is used, skipping demo*/synthetic* folders the project generates.
If the folder does not exist, a warning is printed and the sample data bundled
in this project (data/price_data) is used instead, if present.
"""

import glob
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# >>> EDIT THIS LINE <<<
# Default: the `data` folder inside this project (e.g. C:\Users\you\VsCode\VectorAlpha\data). The CSVs may sit
# directly in it, or in one subfolder of it (price_data/ or any other name). Use an absolute path if the data lives
# elsewhere, e.g.
#   DATA_DIR_SETTING = "C:/Users/you/VsCode/Trading Bot/data"
DATA_DIR_SETTING = "data"

BUNDLED_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "price_data")

# Subfolders of the data folder that this project generates itself (sanity-check data, synthetic demo data).
# They are never auto-picked as "the" data folder, so they cannot shadow your real CSVs.
GENERATED_PREFIXES = ("demo", "synthetic")


def _has_csv(folder):
    return bool(glob.glob(os.path.join(folder, "*.csv")))


def resolve_data_dir(setting=None, environ=None):
    """Returns (folder, note). `note` is None, or a message worth showing the user."""
    environ = os.environ if environ is None else environ
    raw = environ.get("VALPHA_DATA_DIR") or (DATA_DIR_SETTING if setting is None else setting)
    path = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path)
    path = os.path.normpath(path)

    if not os.path.isdir(path):
        if os.path.isdir(BUNDLED_DATA_DIR):
            return BUNDLED_DATA_DIR, (f"WARNING: data folder not found: {path}\n"
                                      f"         using the bundled sample data instead: {BUNDLED_DATA_DIR}\n"
                                      f"         (fix DATA_DIR_SETTING in src/config.py or set VALPHA_DATA_DIR)")
        return path, f"WARNING: data folder not found: {path}  (fix DATA_DIR_SETTING in src/config.py)"

    if not _has_csv(path):
        if _has_csv(os.path.join(path, "price_data")):
            return os.path.join(path, "price_data"), None
        for name in sorted(os.listdir(path)):
            sub = os.path.join(path, name)
            if os.path.isdir(sub) and not name.lower().startswith(GENERATED_PREFIXES) and _has_csv(sub):
                return sub, None
    return path, None


DATA_DIR, DATA_DIR_NOTE = resolve_data_dir()
if DATA_DIR_NOTE:
    print(DATA_DIR_NOTE, file=sys.stderr)

"""
history.py
-----------
Persistent record of every run, kept in results/history.json (a JSON list,
oldest first). One entry per execution: a run of one engine, or of several
engines together, on one strategy. Used by the web UI's History page, the
"latest run" panel and the terminal menu.

Entry shape (see benchmark.run_engines):
    id, timestamp, source ("web"|"menu"), strategy, description,
    n_tickers, universe ([] = every ticker), tickers (names, capped),
    engines: { engine: {"elapsed": seconds, "summary": {label: text}} }
"""

import datetime
import json
import os
import threading
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
HISTORY_PATH = os.path.join(RESULTS_DIR, "history.json")

_lock = threading.Lock()


def _read():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(entries):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=1)
    os.replace(tmp, HISTORY_PATH)


def load():
    """All entries, oldest first."""
    with _lock:
        return _read()


def record(entry):
    """Appends `entry` (adds id + timestamp if missing) and returns it."""
    entry = dict(entry)
    entry.setdefault("id", datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4])
    entry.setdefault("timestamp", datetime.datetime.now().isoformat(timespec="seconds"))
    with _lock:
        entries = _read()
        entries.append(entry)
        _write(entries)
    return entry


def latest():
    entries = load()
    return entries[-1] if entries else None


def clear():
    with _lock:
        _write([])

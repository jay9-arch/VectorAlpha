"""
web_ui.py
----------
Local web front-end for VectorAlpha (standard library only -- no Flask needed).

    python main.py                  # starts this UI;  --port N, --no-browser

Everything lives here: run backtests, manage strategies, browse tickers, compile
(one strategy / all), checks, data folder and synthetic data, run history and the
timing log. Views are routed client-side (#run, #history, #strategies, ...).

The server only listens on 127.0.0.1, rejects foreign Host headers, and every
state-changing request must carry an X-Valpha header (so other websites open in
your browser cannot trigger it). File-opening requests never take a path from the
browser: they name a ticker / strategy / engine / output file that is looked up here.
"""

import contextlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import benchmark
import config
import data_loader
import demo_data
import history
import strategies
import valpha_lang
from strategies import STRATEGIES

WEB_DIR = os.path.join(benchmark.PROJECT_ROOT, "web")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_TEXT = 200_000
DEFAULT_PORT = 8765

# Quick DSL reference shown beside the strategy editor.
REFERENCE = {
    "config": [
        {"code": "INITIAL_CAPITAL = 100000;", "text": "Required. Starting cash, > 0."},
        {"code": "ALLOCATION = 0.25;", "text": "Required. Fraction of INITIAL_CAPITAL committed per trade, 0 < x <= 1."},
        {"code": "UNIVERSE = { A, AA };", "text": "Optional. Tickers to run (file names without .csv). {} or omitted = every CSV in the data folder."},
        {"code": "// description: ...", "text": "Header comment: the text shown for the strategy in the menu and UI."},
    ],
    "functions": [
        {"code": "close", "text": "Daily closing price series (the only price series so far)."},
        {"code": "SMA(x, w)", "text": "Simple moving average over w bars."},
        {"code": "EMA(x, w)", "text": "Exponential moving average (seeded with the mean of the first w values)."},
        {"code": "RSI(x, w)", "text": "Relative strength index, 0-100."},
        {"code": "STDEV(x, w)", "text": "Rolling population standard deviation."},
        {"code": "HIGHEST(x, w)", "text": "Rolling maximum over the last w values (incl. current)."},
        {"code": "LOWEST(x, w)", "text": "Rolling minimum over the last w values (incl. current)."},
        {"code": "LAG(x, n)", "text": "The value n bars ago."},
        {"code": "ABS(x)", "text": "Absolute value."},
        {"code": "CROSSOVER(a, b)", "text": "True on the bar where a crosses above b (either may be a number)."},
        {"code": "CROSSUNDER(a, b)", "text": "True on the bar where a crosses below b (either may be a number)."},
    ],
    "operators": [
        {"code": "+  -  *  /  -x  ( )", "text": "Arithmetic on series and numbers. Dividing by 0 gives a missing value."},
        {"code": "<  >  <=  >=  ==  !=", "text": "Comparisons (tolerant; false if a value is missing). They do not chain."},
        {"code": "AND   OR", "text": "Combine conditions. Precedence: OR < AND < comparison < + - < * / < unary -."},
        {"code": "BUY WHEN cond;  SELL WHEN cond;", "text": "Entry / exit rules. Rules are stateless (no stop-loss yet)."},
        {"code": "NAME = expr;", "text": "Define a series, a constant (window sizes may use constants) or a named boolean."},
    ],
    "notes": "w and n must be constant integers >= 1. Indicators nest: EMA(MACD, 9), SMA(ABS(close - LAG(close, 1)), 10).",
}

SKELETON = """// description: one line shown in the strategy list
INITIAL_CAPITAL = 100000;
ALLOCATION = 0.25;
UNIVERSE = {};                      // {} = every ticker, or e.g. { A, AA }

FAST = SMA(close, 10);
SLOW = SMA(close, 30);
RSI_14 = RSI(close, 14);

BUY  WHEN CROSSOVER(FAST, SLOW) AND RSI_14 < 70;
SELL WHEN CROSSUNDER(FAST, SLOW);
"""


# ------------------------------------------------------------ jobs

class _LogWriter:
    def __init__(self, job):
        self.job, self.buf, self.lock = job, "", threading.Lock()

    def write(self, s):
        with self.lock:                      # compile jobs print from several threads
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                self.job.log.append(line)
        return len(s)

    def flush(self):
        with self.lock:
            if self.buf:
                self.job.log.append(self.buf)
                self.buf = ""


class Job:
    def __init__(self, kind):
        self.id = f"{kind}-{int(time.time() * 1000)}"
        self.kind, self.status, self.log = kind, "running", []
        self.result, self.error = None, None


_run_lock = threading.Lock()   # one job at a time (they share stdout capture and the CPU)
_jobs = {}
_active = {"id": None, "kind": None}


def start_job(kind, fn):
    """Runs fn() on a background thread, capturing what it prints. None if another job is running."""
    if not _run_lock.acquire(blocking=False):
        return None
    job = Job(kind)
    _jobs[job.id] = job
    _active.update(id=job.id, kind=kind)

    def work():
        writer = _LogWriter(job)
        try:
            with contextlib.redirect_stdout(writer):
                job.result = fn()
            writer.flush()
            if job.result is None and kind == "run":
                job.status, job.error = "error", "Nothing was run -- see the log above."
            elif job.result is False and kind == "build":
                job.status, job.error = "error", "The build failed -- see the log above."
            else:
                job.status = "done"
        except Exception as exc:
            writer.flush()
            job.log.append(traceback.format_exc())
            job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            _active.update(id=None, kind=None)
            _run_lock.release()

    threading.Thread(target=work, daemon=True).start()
    return job


# --------------------------------------------------------- helpers

class ApiError(Exception):
    def __init__(self, message, code=400, extra=None):
        super().__init__(message)
        self.code, self.extra = code, extra or {}


def reveal(path):
    """Shows a file in the OS file explorer (folders are opened). Returns an error string or None."""
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return f"does not exist: {path}"
    try:
        if sys.platform.startswith("win"):
            if os.path.isdir(path):
                os.startfile(path)
            else:
                subprocess.Popen(["explorer", "/select," + path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path] if os.path.isdir(path) else ["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def strategy_info(key, available):
    cfg = STRATEGIES[key]
    tickers, missing = benchmark.resolve_tickers(key, available)
    return {
        "key": key, "description": cfg["description"], "universe": cfg["universe"],
        "resolved": len(tickers), "missing": missing, "columns": cfg["indicator_names"],
        "initial_capital": cfg["initial_capital"], "allocation": cfg["allocation"],
        "compiled": benchmark.compiled_status(key), "compiled_at": benchmark.compiled_at(key),
        "preview": tickers[:8],
    }


def validate_text(text, name):
    try:
        spec = valpha_lang.parse_text(text, name=name or "new_strategy")
    except valpha_lang.ValphaError as exc:
        return {"ok": False, "errors": [ln for ln in str(exc).splitlines() if ln.strip()]}
    return {"ok": True, "columns": spec.output_columns, "universe": spec.universe,
            "description": spec.description}


def _clean_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ApiError("The strategy text is empty.")
    if len(text) > MAX_TEXT:
        raise ApiError("The strategy text is too large.")
    return text.replace("\r\n", "\n").rstrip() + "\n"


def _reload():
    try:
        strategies.reload_strategies()
    except Exception as exc:
        raise ApiError(f"Saved, but the strategies could not be reloaded: {exc}", 500)


def strategy_path(key):
    return os.path.join(strategies.STRATEGY_DIR, key + ".valpha")


def save_strategy(name, text, create):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ApiError("Name must start with a letter and use only letters, digits and underscores.")
    text = _clean_text(text)
    path = strategy_path(name)
    if create and (os.path.exists(path) or name in STRATEGIES):
        raise ApiError(f"A strategy named '{name}' already exists.", 409)
    if not create and not os.path.exists(path):
        raise ApiError(f"No strategy named '{name}'.", 404)
    check = validate_text(text, name)
    if not check["ok"]:
        raise ApiError("The strategy has errors.", 400, {"errors": check["errors"]})
    os.makedirs(strategies.STRATEGY_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    _reload()
    benchmark.forget_strategy(name)
    return strategy_info(name, data_loader.list_tickers())


def delete_strategy(name):
    if name not in STRATEGIES:
        raise ApiError(f"No strategy named '{name}'.", 404)
    os.remove(strategy_path(name))
    bin_dir = os.path.join(benchmark.COMPILER_DIR, "bin")
    for fname in (f"{name}_runtime", f"{name}_runtime.exe", f"{name}_runtime.cpp", f"{name}.build.log"):
        p = os.path.join(bin_dir, fname)
        if os.path.exists(p):
            with contextlib.suppress(OSError):
                os.remove(p)
    _reload()
    benchmark.forget_strategy(name)
    return {"deleted": name}


_ticker_cache = {}


def _date_of(line):
    d = line[:10]
    return d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else ""


def ticker_info(path):
    """First/last bar date and size, from the head and tail of the file only (cheap for 1000s of CSVs)."""
    st = os.stat(path)
    sig = (path, st.st_mtime, st.st_size)
    info = _ticker_cache.get(sig)
    if info is None:
        with open(path, "rb") as f:
            if st.st_size <= 16384:
                lines = f.read().decode("utf-8", "replace").splitlines()
                first = lines[1] if len(lines) > 1 else ""
            else:
                head = f.read(4096).decode("utf-8", "replace").splitlines()
                first = head[1] if len(head) > 1 else ""
                f.seek(-4096, os.SEEK_END)
                lines = f.read().decode("utf-8", "replace").splitlines()
        last = next((ln for ln in reversed(lines) if ln.strip()), "")
        info = {"first": _date_of(first), "last": _date_of(last) if last != lines[0] or st.st_size > 16384 else ""}
        _ticker_cache[sig] = info
    return {"size": st.st_size, **info}


def ticker_page(offset, limit, q):
    names = data_loader.list_tickers()
    q = (q or "").strip().lower()
    matched = [t for t in names if q in t.lower()] if q else names
    chunk = matched[offset:] if limit is None else matched[offset:offset + limit]
    rows = []
    for t in chunk:
        try:
            rows.append({"ticker": t, **ticker_info(os.path.join(data_loader.DATA_DIR, t + ".csv"))})
        except OSError:
            rows.append({"ticker": t, "size": 0, "first": "", "last": ""})
    return {"data_dir": data_loader.DATA_DIR, "total": len(names), "matched": len(matched),
            "offset": offset, "rows": rows}


_cxx_version = {}


def cxx_version():
    exe = shutil.which(benchmark.cxx_name())
    if not exe:
        return None
    if exe not in _cxx_version:
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
            _cxx_version[exe] = (out.splitlines() or [""])[0].strip()
        except Exception:
            _cxx_version[exe] = ""
    return _cxx_version[exe]


# ---- persisted UI settings (the chosen data folder survives restarts)

SETTINGS_PATH = os.path.join(benchmark.RESULTS_DIR, "settings.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    os.makedirs(benchmark.RESULTS_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def apply_startup_data_dir():
    """Saved data folder if still valid; else the synthetic demo data when the default folder is empty."""
    saved = load_settings().get("data_dir")
    if saved and data_loader.list_tickers(saved):
        data_loader.use_data_dir(saved)
    elif not data_loader.list_tickers() and data_loader.list_tickers(demo_data.DEFAULT_DIR):
        print(f"No price data in {data_loader.DATA_DIR}; using the synthetic demo data generated earlier.")
        data_loader.use_data_dir(demo_data.DEFAULT_DIR)


def set_data_folder(path, persist=True):
    data_loader.use_data_dir(path)
    if persist:
        s = load_settings()
        s["data_dir"] = path
        save_settings(s)


def api_data_folder(body):
    if body.get("reset"):
        s = load_settings()
        s.pop("data_dir", None)
        save_settings(s)
        data_loader.use_data_dir(config.DATA_DIR)
        return api_state()
    raw = body.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError("Enter a folder path.")
    folder, note = config.resolve_data_dir(raw.strip().strip('"'), environ={})
    if note:
        raise ApiError(f"Folder not found: {raw.strip()}")
    if not data_loader.list_tickers(folder):
        raise ApiError(f"No .csv files found in {folder} (or its subfolders).")
    set_data_folder(folder)
    return api_state()


def api_state():
    tickers = data_loader.list_tickers()
    built = sum(1 for k in STRATEGIES if benchmark.compiled_status(k) == "ok")
    return {
        "data_dir": data_loader.DATA_DIR, "default_data_dir": config.DATA_DIR,
        "n_tickers": len(tickers), "n_strategies": len(STRATEGIES),
        "compiler": {"valphac": benchmark.valphac_path() is not None, "cxx": benchmark.cxx_name(),
                     "cxx_found": shutil.which(benchmark.cxx_name()) is not None,
                     "cxx_version": cxx_version(), "built": built},
        "busy": _run_lock.locked(), "active": dict(_active),
        "engines": [{"key": e, "label": benchmark.ENGINE_LABELS[e]} for e in benchmark.ENGINE_ORDER],
        "output_files": list(benchmark.OUTPUT_FILES),
    }


def api_latest():
    entry = history.latest()
    if not entry:
        return {"entry": None}
    files = {}
    for eng in entry["engines"]:
        d = benchmark.output_dir(entry["strategy"], eng)
        files[eng] = [{"name": f, "exists": os.path.exists(os.path.join(d, f))} for f in benchmark.OUTPUT_FILES]
    return {"entry": entry, "files": files,
            "comparison": benchmark.comparison_for(entry["strategy"], entry["n_tickers"])}


def api_reveal(body):
    kind = body.get("kind")
    if kind == "ticker":
        t = body.get("ticker")
        if t not in data_loader.list_tickers():
            raise ApiError("Unknown ticker.", 404)
        path = os.path.join(data_loader.DATA_DIR, t + ".csv")
    elif kind == "data_dir":
        path = data_loader.DATA_DIR
    elif kind == "output":
        s, e, f = body.get("strategy"), body.get("engine"), body.get("file")
        if s not in STRATEGIES or e not in benchmark.ENGINE_ORDER or f not in benchmark.OUTPUT_FILES:
            raise ApiError("Unknown output file.", 404)
        path = os.path.join(benchmark.output_dir(s, e), f)
    elif kind == "results_dir":
        s = body.get("strategy")
        path = os.path.join(benchmark.RESULTS_DIR, s) if s in STRATEGIES else benchmark.RESULTS_DIR
        if not os.path.exists(path):
            path = benchmark.RESULTS_DIR
    elif kind == "timing_log":
        path = benchmark.LOG_PATH
    elif kind == "strategy":
        s = body.get("strategy")
        if s not in STRATEGIES:
            raise ApiError("Unknown strategy.", 404)
        path = strategy_path(s)
    else:
        raise ApiError("Unknown kind.")
    err = reveal(path)
    if err:
        raise ApiError(f"Could not open the file explorer ({err}). Path: {os.path.normpath(path)}", 500,
                       {"path": os.path.normpath(path)})
    return {"ok": True, "path": os.path.normpath(path)}


def api_run(body):
    key = body.get("strategy")
    if key not in STRATEGIES:
        raise ApiError("Pick a strategy first.", 404)
    engines = body.get("engines")
    if not isinstance(engines, list) or not engines or any(e not in benchmark.ENGINE_ORDER for e in engines):
        raise ApiError("Pick at least one engine.")
    job = start_job("run", lambda: benchmark.run_engines(key, engines, source="web"))
    if job is None:
        raise ApiError("Another run or build is still in progress.", 409)
    return {"job": job.id}


def api_job(job_id, since):
    job = _jobs.get(job_id)
    if job is None:
        raise ApiError("Unknown job.", 404)
    log = list(job.log)
    return {"id": job.id, "status": job.status, "error": job.error, "log": log[since:], "next": len(log),
            "result": job.result if job.status == "done" else None}


def api_generate(body):
    def num(v, default):
        try:
            return max(1, min(int(v), 5000))
        except (TypeError, ValueError):
            return default
    n, bars = num(body.get("n"), 40), num(body.get("bars"), 1500)
    demo_data.generate(demo_data.DEFAULT_DIR, n, bars)
    set_data_folder(demo_data.DEFAULT_DIR)
    return {"data_dir": data_loader.DATA_DIR, "n_tickers": n}


def run_sanity():
    import sanity_check
    return {"ok": bool(sanity_check.run())}


def run_smoke():
    import smoke_test
    saved, sys.argv = sys.argv, ["smoke_test.py"]     # smoke_test parses sys.argv itself
    try:
        rc = smoke_test.main()
    finally:
        sys.argv = saved
    return {"ok": rc == 0, "code": rc}


def api_compile(body):
    if body.get("rebuild"):
        fn, label = benchmark.build_everything, "rebuild"
    elif body.get("strategy"):
        key = body["strategy"]
        if key not in STRATEGIES:
            raise ApiError("Unknown strategy.", 404)
        fn, label = (lambda: benchmark.compile_one(key)), key
    else:
        fn, label = benchmark.compile_pending, "pending"
    job = start_job("build", fn)
    if job is None:
        raise ApiError("Another run or build is still in progress.", 409)
    return {"job": job.id, "label": label}


def api_check(body):
    fn = {"sanity": run_sanity, "smoke": run_smoke}.get(body.get("name"))
    if fn is None:
        raise ApiError("Unknown check.", 404)
    job = start_job("check", fn)
    if job is None:
        raise ApiError("Another run or build is still in progress.", 409)
    return {"job": job.id}


def api_timing_log():
    path = benchmark.LOG_PATH
    if not os.path.exists(path):
        return {"exists": False, "path": path, "text": ""}
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"exists": True, "path": path, "text": f.read()}


def dispatch(method, path, query, body):
    """Returns a JSON-able object; raises ApiError."""
    m = re.fullmatch(r"/api/strategies/([^/]+)", path)
    if method == "GET":
        if path == "/api/state":
            return api_state()
        if path == "/api/strategies":
            avail = data_loader.list_tickers()
            return [strategy_info(k, avail) for k in STRATEGIES]
        if m:
            key = m.group(1)
            if key not in STRATEGIES:
                raise ApiError("No such strategy.", 404)
            with open(strategy_path(key), encoding="utf-8") as f:
                return {**strategy_info(key, data_loader.list_tickers()), "text": f.read()}
        if path == "/api/reference":
            return {"reference": REFERENCE, "skeleton": SKELETON}
        if path == "/api/tickers":
            def qint(name, default):
                v = query.get(name, [default])[0]
                try:
                    return None if v == "all" else max(0, int(v))
                except ValueError:
                    return default if isinstance(default, int) else None
            return ticker_page(qint("offset", 0) or 0, qint("limit", 100), query.get("q", [""])[0])
        if path == "/api/timing_log":
            return api_timing_log()
        if path == "/api/latest":
            return api_latest()
        if path == "/api/history":
            return list(reversed(history.load()))
        if path.startswith("/api/jobs/"):
            try:
                since = int(query.get("since", ["0"])[0])
            except ValueError:
                since = 0
            return api_job(path[len("/api/jobs/"):], since)
    elif method == "POST":
        if path == "/api/validate":
            return validate_text(str(body.get("text", ""))[:MAX_TEXT], body.get("name"))
        if path == "/api/strategies":
            return save_strategy(body.get("name"), body.get("text"), create=True)
        if path == "/api/run":
            return api_run(body)
        if path == "/api/compile":
            return api_compile(body)
        if path == "/api/check":
            return api_check(body)
        if path == "/api/data/folder":
            return api_data_folder(body)
        if path == "/api/reveal":
            return api_reveal(body)
        if path == "/api/data/generate":
            return api_generate(body)
    elif method == "PUT" and m:
        return save_strategy(m.group(1), body.get("text"), create=False)
    elif method == "DELETE":
        if m:
            return delete_strategy(m.group(1))
        if path == "/api/history":
            history.clear()
            return {"cleared": True}
    raise ApiError("Not found.", 404)


# ------------------------------------------------------------ http

PAGES = {"/": "index.html", "/history": "history.html"}   # history.html just redirects to /#history


class Handler(BaseHTTPRequestHandler):
    server_version = "VectorAlphaUI"

    def log_message(self, *args):
        pass

    def _send(self, code, data, ctype):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, TimeoutError):
            pass        # the browser went away mid-response (refresh / navigation): nothing to do

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _handle(self, method):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return self._json({"error": "forbidden host"}, 403)
        url = urlparse(self.path)
        path = unquote(url.path)
        if method == "GET" and not path.startswith("/api/"):
            return self._static(path)
        if method != "GET" and self.headers.get("X-Valpha") != "1":
            return self._json({"error": "missing X-Valpha header"}, 403)
        body = {}
        if method in ("POST", "PUT"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 2_000_000:
                    return self._json({"error": "request too large"}, 413)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json({"error": "invalid JSON body"}, 400)
        try:
            self._json(dispatch(method, path, parse_qs(url.query), body))
        except ApiError as exc:
            self._json({"error": str(exc), **exc.extra}, exc.code)
        except (ConnectionError, TimeoutError):
            return
        except Exception as exc:
            traceback.print_exc()
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _static(self, path):
        name = PAGES.get(path)
        if name is None and path.startswith("/web/"):
            name = os.path.basename(path)
        fpath = os.path.join(WEB_DIR, name) if name else None
        if not fpath or not os.path.isfile(fpath):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
        with open(fpath, "rb") as f:
            self._send(200, f.read(), ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PUT(self): self._handle("PUT")
    def do_DELETE(self): self._handle("DELETE")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return      # client disconnects are normal for a browser UI
        super().handle_error(request, client_address)


def make_server(port=DEFAULT_PORT):
    last = None
    for p in range(port, port + 20):
        try:
            return Server(("127.0.0.1", p), Handler)
        except OSError as exc:
            last = exc
    raise last


def serve(port=DEFAULT_PORT, open_browser=True):
    apply_startup_data_dir()
    server = make_server(port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"VectorAlpha web UI running at {url}   (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

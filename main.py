#!/usr/bin/env python3
"""
VectorAlpha

    python main.py                 starts the web UI and opens it in your browser
    python main.py --port 9000     use another port (default 8765)
    python main.py --no-browser    start the server without opening a browser tab

All controls (running backtests, strategies, tickers, compiling, checks, data folder,
history) live in the web UI. Tickers are never asked for: each strategy's UNIVERSE
decides, and an empty UNIVERSE means every CSV in the data folder.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))


def main():
    ap = argparse.ArgumentParser(description="VectorAlpha web UI")
    ap.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = ap.parse_args()
    try:
        import web_ui
    except ImportError as exc:
        sys.exit(f"Missing dependency: {exc}\nRun:  pip install -r requirements.txt")
    except Exception as exc:  # e.g. a syntax error in a .valpha file
        sys.exit(f"Could not load the strategies in compiler/strategies/:\n  {exc}")
    web_ui.serve(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

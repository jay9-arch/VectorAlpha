#!/usr/bin/env python3
"""
build_all.py -- cross-platform replacement for build_all.sh (no make, no bash).

Builds the `valphac` compiler with direct g++ calls, then runs it on every
strategies/*.valpha file to produce one compiled runtime binary per strategy
under bin/ (bin/<name>_runtime, or bin/<name>_runtime.exe on Windows).

    python compiler/build_all.py            # build valphac + every strategy
    python compiler/build_all.py -j 4       # limit parallel jobs
    python compiler/build_all.py --clean    # delete objects/binaries first

Only a C++17 compiler is required: g++ on PATH (MSYS2 / MinGW-w64 on Windows).
Override the compiler with the CXX environment variable, flags with CXXFLAGS.

Like build_all.sh, each strategy's full compiler output goes to
bin/<name>.build.log and this script prints one line per strategy.
"""

import argparse
import concurrent.futures
import glob
import os
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""

SOURCES = ["main", "lexer", "parser", "ast", "semantic", "ir", "optimize", "codegen"]
DEFAULT_CXXFLAGS = "-std=c++17 -O2 -Wall -Wextra -Iinclude"

VALPHAC = os.path.join(HERE, "valphac" + EXE)
BIN_DIR = os.path.join(HERE, "bin")
STRATEGY_GLOB = os.path.join(HERE, "strategies", "*.valpha")


def find_compiler():
    cxx = os.environ.get("CXX") or "g++"
    # CXX may carry arguments (e.g. "ccache g++"): look only at the program.
    parts = shlex.split(cxx, posix=not IS_WINDOWS)
    if not parts or shutil.which(parts[0]) is None:
        sys.exit(
            f"error: C++ compiler '{parts[0] if parts else cxx}' not found on PATH.\n"
            "Install g++ (on Windows: MSYS2 'mingw-w64-ucrt-x86_64-gcc'), add its bin\n"
            "folder to PATH, or set the CXX environment variable."
        )
    return parts


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace", **kw)


def newest_mtime(paths):
    return max((os.path.getmtime(p) for p in paths), default=0.0)


def build_valphac(cxx, jobs, clean):
    print("== Building valphac ==")
    flags = shlex.split(os.environ.get("CXXFLAGS") or DEFAULT_CXXFLAGS, posix=not IS_WINDOWS)
    headers = glob.glob(os.path.join(HERE, "include", "*.hpp"))
    header_time = newest_mtime(headers)

    objs = {name: os.path.join(HERE, "src", name + ".o") for name in SOURCES}
    if clean:
        for p in list(objs.values()) + [VALPHAC]:
            if os.path.exists(p):
                os.remove(p)

    def compile_one(name):
        src = os.path.join("src", name + ".cpp")
        obj = objs[name]
        if (os.path.exists(obj)
                and os.path.getmtime(obj) >= max(os.path.getmtime(os.path.join(HERE, src)), header_time)):
            return name, 0, ""                      # up to date
        proc = run(cxx + flags + ["-c", "-o", obj, src])
        if proc.returncode != 0 and os.path.exists(obj):
            os.remove(obj)
        return name, proc.returncode, proc.stdout

    failed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for name, rc, out in pool.map(compile_one, SOURCES):
            if rc != 0:
                failed = True
                print(f"  FAILED  src/{name}.cpp\n{out}")
            elif out.strip():
                print(out.rstrip())                  # warnings
    if failed:
        sys.exit("error: valphac failed to compile (see messages above)")

    link = cxx + flags + ["-o", VALPHAC] + [objs[n] for n in SOURCES]
    if IS_WINDOWS:
        link.append("-static")      # no dependency on MinGW runtime DLLs being on PATH
    proc = run(link)
    if proc.returncode != 0 and IS_WINDOWS:
        proc = run(link[:-1])       # fall back to a dynamic link if static libs are missing
    if proc.returncode != 0:
        print(proc.stdout)
        sys.exit("error: linking valphac failed")
    print(f"  ok      {os.path.relpath(VALPHAC, HERE)}")


def runtime_path(name):
    return os.path.join(BIN_DIR, name + "_runtime" + EXE)


def compile_strategy(path):
    name = os.path.splitext(os.path.basename(path))[0]
    log = os.path.join(BIN_DIR, name + ".build.log")
    for stale in (runtime_path(name), runtime_path(name)[: -len(EXE)] if EXE else None):
        if stale and os.path.exists(stale):
            os.remove(stale)        # a failed rebuild must not leave an old binary looking fine
    proc = run([VALPHAC, path, BIN_DIR])
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        f.write(proc.stdout)
    built = os.path.exists(runtime_path(name)) or os.path.exists(os.path.join(BIN_DIR, name + "_runtime"))
    return name, proc.returncode == 0 and built


def main():
    ap = argparse.ArgumentParser(description="Build valphac and compile every strategy (no make/bash needed).")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 2, help="parallel jobs (default: all cores)")
    ap.add_argument("--clean", action="store_true", help="remove object files and valphac first")
    args = ap.parse_args()
    jobs = max(1, args.jobs)

    cxx = find_compiler()
    os.environ["CXX"] = " ".join(cxx)   # valphac reads CXX to compile the generated sources
    build_valphac(cxx, jobs, args.clean)

    os.makedirs(BIN_DIR, exist_ok=True)
    strategies = sorted(glob.glob(STRATEGY_GLOB))
    if not strategies:
        sys.exit(f"error: no .valpha files found in {os.path.join(HERE, 'strategies')}")

    print(f"\n== Compiling strategies ({jobs} parallel job(s)) ==")
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(compile_strategy, p) for p in strategies]
        for fut in concurrent.futures.as_completed(futures):
            name, ok = fut.result()
            if ok:
                print(f"  ok      {name}")
            else:
                failed.append(name)
                print(f"  FAILED  {name}   (see bin/{name}.build.log)")

    print()
    if not failed:
        print(f"== Done. Compiled binaries in {BIN_DIR} ==")
        return 0
    print(f"== {len(failed)} strategy build(s) FAILED -- open the bin/*.build.log files named above ==")
    return 1


if __name__ == "__main__":
    sys.exit(main())

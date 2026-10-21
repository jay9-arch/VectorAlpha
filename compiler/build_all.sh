#!/usr/bin/env bash
# Builds valphac itself, then runs it on every .valpha file in
# strategies/ to produce a compiled runtime binary per strategy under
# bin/. These .valpha files are ALSO the strategy definitions the naive
# and pandas Python engines read (src/strategies.py), so one file drives
# all three engines. benchmark.py's run_compiled_only() looks for
# bin/<strategy>_runtime -- run this once (and again after editing a
# strategies/*.valpha file) before using the "Run COMPILED engine" menu
# option in the Python demo.
#
# Strategies are compiled in parallel (one job per core) and each one's
# full compiler output goes to bin/<name>.build.log, so this prints one
# line per strategy instead of thousands of lines of token dumps.
set -e
cd "$(dirname "$0")"

echo "== Building valphac =="
make

mkdir -p bin
JOBS=$( (nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2) | head -1 )
echo
echo "== Compiling strategies ($JOBS parallel job(s)) =="
printf '%s\n' strategies/*.valpha | xargs -P "$JOBS" -I{} bash -c '
    name=$(basename "{}" .valpha)
    if ./valphac "{}" bin > "bin/$name.build.log" 2>&1; then
        echo "  ok      $name"
    else
        echo "  FAILED  $name   (see bin/$name.build.log)"
    fi'

failed=0
for f in strategies/*.valpha; do
    name=$(basename "$f" .valpha)
    if [ ! -f "bin/${name}_runtime" ] && [ ! -f "bin/${name}_runtime.exe" ]; then failed=$((failed + 1)); fi
done

echo
if [ "$failed" -eq 0 ]; then
    echo "== Done. Compiled binaries in $(pwd)/bin =="
else
    echo "== $failed strategy build(s) FAILED -- open the bin/*.build.log files named above =="
    exit 1
fi

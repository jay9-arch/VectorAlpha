#!/usr/bin/env bash
# Compiler regression tests -- no Python needed. Run via `make test`.
#   1. every good example/strategy lexes, parses, checks, lowers, optimizes
#   2. every deliberately-broken example is rejected with the expected diagnostic
#   3. parser precedence, constant folding and the CSE pass behave as documented
#   4. every strategy compiles to a native binary; the ablation build modes
#      (--no-fuse, --no-cse) are checked on three representative strategies
cd "$(dirname "$0")/.." || exit 1

make -s || { echo "build failed"; exit 1; }

pass=0
fail=0
ok()   { pass=$((pass + 1)); echo "  ok   - $1"; }
bad()  { fail=$((fail + 1)); echo "  FAIL - $1"; }

echo "== good programs are accepted"
for f in examples/strategy.valpha examples/trend_follow.valpha examples/precedence.valpha \
         examples/arithmetic_demo.valpha strategies/*.valpha; do
    if ./valphac "$f" >/dev/null 2>&1; then ok "$f"; else bad "$f should be accepted"; fi
done

echo "== broken programs are rejected with the right diagnostic"
expect_error() {  # file, substring that must appear on stderr
    out=$(./valphac "$1" 2>&1 >/dev/null); rc=$?
    if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qF -- "$2"; then ok "$1 -> $2"; else bad "$1 should fail with: $2"; fi
}
expect_error examples/bad_undefined_identifier.valpha "undefined identifier 'SMA_9'"
expect_error examples/bad_division_by_zero.valpha "division by zero"
expect_error examples/bad_missing_config.valpha "INITIAL_CAPITAL is required"
expect_error examples/bad_type_error.valpha "ALLOCATION must be greater than 0 and at most 1"
expect_error examples/bad_type_error.valpha "needs numeric operands"
expect_error examples/bad_type_error.valpha "window size) must be a constant integer >= 1"
expect_error examples/bad_type_error.valpha "BUY WHEN condition must evaluate to a boolean"
expect_error examples/bad_new_ops.valpha "LAG() expects 2 argument(s), got 1"
expect_error examples/bad_new_ops.valpha "ABS() expects 1 argument(s), got 2"
expect_error examples/bad_new_ops.valpha "HIGHEST(): argument 2 (window size) must be a constant integer >= 1"

echo "== parser precedence, constant folding, CSE"
expect_out() {  # file, substring on stdout, label
    if ./valphac "$1" 2>/dev/null | grep -qF -- "$2"; then ok "$3"; else bad "$3 (missing: $2)"; fi
}
expect_out examples/precedence.valpha 'A = (close + (2 * 3));'    "* binds tighter than +"
expect_out examples/precedence.valpha 'B = ((close + 2) * 3);'    "parentheses override precedence"
expect_out examples/precedence.valpha 'C = (((-close) * 2) + 1);' "unary minus binds tighter than *"
expect_out examples/precedence.valpha 'D = ((close - 1) - 2);'    "- is left associative"
expect_out examples/precedence.valpha 'A = ADD(close, 6)'         "2 * 3 constant-folded to 6 in the IR"
expect_out examples/arithmetic_demo.valpha "'MID2' computes the exact same thing as 'MID'" "CSE merges duplicate EMA"
expect_out examples/arithmetic_demo.valpha "'%t3' computes the exact same thing as '%t1'" "CSE merges nested duplicate SMA"
expect_out examples/arithmetic_demo.valpha "MID = EMA(close, w=20)" "WINDOW constant folded into a window size"

expect_out strategies/donchian_breakout.valpha "ring_scan" "HIGHEST/LOWEST are planned as ring scans"
expect_out strategies/donchian_breakout.valpha "ring_delay" "LAG is planned as a ring delay"
expect_out strategies/bollinger_squeeze_breakout.valpha "SQUEEZE_RATIO = DIV(BANDWIDTH" "nested LOWEST/LAG lower to three-address form"

echo "== codegen: every strategy compiles (default build); ablation modes on three representative ones"
tmp=$(mktemp -d)
for f in strategies/*.valpha; do
    if ./valphac --tag=t "$f" "$tmp" >"$tmp/log.txt" 2>&1; then ok "$f default"; else bad "$f default"; tail -5 "$tmp/log.txt"; fi
done
for f in strategies/volatility_breakout.valpha strategies/bollinger_squeeze_breakout.valpha strategies/efficiency_trend.valpha; do
    for mode in "--no-fuse" "--no-cse"; do
        if ./valphac $mode --tag=t "$f" "$tmp" >"$tmp/log.txt" 2>&1; then ok "$f $mode"; else bad "$f $mode"; tail -5 "$tmp/log.txt"; fi
    done
done
rm -rf "$tmp"

echo
echo "compiler tests: $pass passed, $fail failed"
[ $fail -eq 0 ]

#pragma once
#include <string>
#include "optimize.hpp"

// Phase 5 -- Code generation (Report 1 Section 3.3, "Modules 5-6").
//
// Emits a single self-contained C++17 program from an OptimizedIR,
// specialized to the exact strategy that was compiled: there is no
// interpreter loop walking IR at runtime, every indicator and rule
// condition becomes literal, strategy-specific C++.
//
// The generated program, end to end:
//   1. Reads ticker CSVs from a data directory given on its own command
//      line (same yfinance-style format the Python engines use),
//      restricted to UNIVERSE if non-empty, otherwise every *.csv found.
//   2. Groups tickers that share an identical trading calendar and
//      processes them in batches of 4 "lanes" (SoA layout: one SIMD
//      register holds the same bar of 4 different tickers). Leftover
//      tickers run through the same kernel with a single scalar lane.
//      The kernel is ONE generated template instantiated for both lane
//      types, so scalar and SIMD results are bit-identical by
//      construction. SIMD uses GCC/Clang vector extensions, which lower
//      to AVX2/SSE/NEON depending on -march; there are no hand-written
//      intrinsics.
//   3. Computes all indicators in ONE fused pass over the bars per
//      ticker (see optimize.hpp, pass 3), using the O(1)-per-bar
//      incremental algorithms Phase 4 planned: running sums for SMA,
//      EWMA recurrence for EMA, rolling gain/loss sums for RSI, rolling
//      sum and sum-of-squares for STDEV. Windowed ops track missing
//      values so nested indicators (SMA of an RSI, ...) are correct.
//   4. Distributes batches over std::thread workers
//      (--threads N at runtime; default hardware_concurrency) -- the
//      cross_sectional tag from Phase 3 is exactly what licenses this.
//   5. Runs the same sequential, single-position portfolio simulation
//      as the two Python engines (kept sequential on purpose: it is
//      genuinely path-dependent) and writes the same four output
//      artifacts in the same column layout.
//   6. Prints ELAPSED_SECONDS=<value> (steps 2-5, excluding CSV I/O and
//      output writing, exactly like benchmark.py times the Python
//      engines) plus COMPUTE_SECONDS / SIM_SECONDS breakdowns.
//
// Numerical contract shared with the Python engines (see README):
// comparisons and CROSSOVER/CROSSUNDER are tolerant (1e-9 * max(1, |a|, |b|)) so
// floating-point noise from incremental vs recomputed indicators cannot
// flip a signal; comparisons involving a missing value are false;
// division by zero yields a missing value.

namespace valpha {

struct CodegenOptions {
    // false (--no-fuse): materialize every instruction as a full-length
    // array and make one pass per instruction, scalar lanes only. Exists
    // so the benefit of fusion + SIMD can be measured (ablation).
    bool fuse = true;
};

// Generates the full C++ source text for `opt`. `strategy_name` is
// used as a comment header and has no effect on behavior.
std::string generate_cpp(const OptimizedIR& opt, const std::string& strategy_name,
                         const CodegenOptions& options = CodegenOptions());

}  // namespace valpha

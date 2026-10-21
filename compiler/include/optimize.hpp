#pragma once
#include <map>
#include <string>
#include <vector>
#include "ir.hpp"

// Phase 4 -- Optimization passes (Report 1 Section 3.3, the
// "novel optimization passes" the IR's domain tags exist to enable).
//
// Three passes run over the IRProgram Phase 3 produces:
//
//   1. Indicator-level CSE -- two instructions that compute the same
//      (op, operands, window) are the same computation, whatever they
//      are called or however they were nested in the source: the
//      second is removed and every reference is rewritten to the first
//      (the "canonical") name. Commutative ops (ADD, MUL) are
//      canonicalised first so `a + b` and `b + a` merge. Because
//      lowering hoists every nested sub-expression into its own
//      instruction, `SMA(close, 20)` written twice in two different
//      expressions is found and merged too. If one side of a merge is a
//      user-named series and the other a "%tN" temporary, the survivor
//      takes the user name (it is an output column).
//
//   2. Sliding-window incrementalization planning -- every windowed
//      instruction gets an IncrementalPlan describing *how* codegen
//      computes it in O(1) amortized per bar instead of O(w) per bar
//      (running sum for SMA, EWMA recurrence for EMA, rolling
//      gain/loss sums for RSI, rolling sum and sum-of-squares for STDEV).
//
//   3. Fusion analysis -- decides what the single fused per-ticker bar
//      loop looks like: every series instruction and rule is evaluated
//      in ONE pass over the bars, only the series that are CSV output
//      columns are stored as full-length arrays ("stored_columns"), and
//      every other value (all "%tN" temporaries, unused-by-output
//      intermediates) lives in a register / scalar for the current bar.
//      With --no-fuse, codegen instead materializes every instruction
//      as its own full-length array and makes one pass per instruction
//      (the classic array-at-a-time layout), so the benefit is
//      measurable.
//
// Everything here is a plan/annotation pass over the IR; no C++ is
// generated until Phase 5.

namespace valpha {

enum class IncrementalStrategy {
    None,               // not a windowed function, nothing to plan
    RunningSum,         // SMA: maintain sum over the window, add new/drop old
    EwmaRecurrence,      // EMA: value[i] = x[i]*alpha + value[i-1]*(1-alpha)
    RollingGainLossSum,  // RSI: rolling sums of positive/negative deltas
    RunningSumOfSquares, // STDEV: rolling sum and sum-of-squares
    RingDelay,           // LAG: ring buffer of the last n values, O(1) per bar
    RingScan,            // HIGHEST/LOWEST: O(w) scan of a ring buffer per bar (lane-vectorizable;
                         // a monotonic deque would be O(1) but cannot run in SIMD lockstep)
};

const char* incremental_strategy_name(IncrementalStrategy s);

struct OptimizeOptions {
    bool cse = true;  // --no-cse turns the CSE pass off (for ablation benchmarks)
};

struct OptimizationNote {
    std::string message;
};

struct FusionGroup {
    std::string name;                  // e.g. "bar_loop"
    std::vector<std::string> members;  // result names of the instructions fused together
};

struct OptimizedIR {
    IRProgram ir;  // same shape as Phase 3's IR, but CSE'd (instruction list pruned)

    // name -> canonical name: every alias (user `X = Y;` aliases and
    // every CSE-merged name) mapped, transitively flattened, to the
    // series that actually survives. Codegen must resolve every series
    // name through resolve_alias() before using it.
    std::map<std::string, std::string> cse_aliases;

    // result name -> chosen incremental strategy, for every Series
    // instruction (None for non-windowed ones).
    std::map<std::string, IncrementalStrategy> incremental_plan;

    std::vector<FusionGroup> fusion_groups;

    // Canonical series that must be stored as full-length arrays
    // because they back an output CSV column ("close" excluded).
    std::vector<std::string> stored_columns;

    std::vector<OptimizationNote> notes;  // human-readable log of what each pass did
};

// Follows `aliases` until a name that is not an alias is reached.
std::string resolve_alias(const std::string& name,
                          const std::map<std::string, std::string>& aliases);

// Runs all three passes, in order, over `ir`. `ir` should already be
// the output of a successful build_ir() call on a semantically valid
// program.
OptimizedIR optimize(const IRProgram& ir, const OptimizeOptions& opts = OptimizeOptions());

// Pretty-prints the optimization notes + the resulting instruction
// order, for Phase 4's smoke-test output.
std::string to_string(const OptimizedIR& opt);

}  // namespace valpha

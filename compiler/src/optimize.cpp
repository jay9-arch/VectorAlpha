#include "optimize.hpp"
#include <algorithm>
#include <set>
#include <sstream>

namespace valpha {

const char* incremental_strategy_name(IncrementalStrategy s) {
    switch (s) {
        case IncrementalStrategy::None: return "none";
        case IncrementalStrategy::RunningSum: return "running_sum";
        case IncrementalStrategy::EwmaRecurrence: return "ewma_recurrence";
        case IncrementalStrategy::RollingGainLossSum: return "rolling_gain_loss_sum";
        case IncrementalStrategy::RunningSumOfSquares: return "running_sum_of_squares";
        case IncrementalStrategy::RingDelay: return "ring_delay";
        case IncrementalStrategy::RingScan: return "ring_scan";
    }
    return "?";
}

std::string resolve_alias(const std::string& name,
                          const std::map<std::string, std::string>& aliases) {
    std::string cur = name;
    for (size_t guard = 0; guard <= aliases.size(); ++guard) {
        auto it = aliases.find(cur);
        if (it == aliases.end()) return cur;
        cur = it->second;
    }
    return cur;  // defensive: an alias cycle cannot occur (no forward references)
}

namespace {

std::string operand_key(const Operand& o, const std::map<std::string, std::string>& aliases) {
    if (o.is_const) return "#" + format_double(o.value);
    return resolve_alias(o.name, aliases);
}

// Canonical key for a Series instruction: identical key means
// identical computation, regardless of what the user called it or how
// it was nested. Operands are resolved through the alias map first so
// chained duplicates collapse, and commutative ops sort their operands.
std::string signature(const IRInstruction& inst,
                      const std::map<std::string, std::string>& aliases) {
    std::vector<std::string> keys;
    for (const auto& o : inst.operands) keys.push_back(operand_key(o, aliases));
    if (is_commutative_op(inst.op)) std::sort(keys.begin(), keys.end());
    std::ostringstream s;
    s << series_op_name(inst.op) << "(";
    for (size_t i = 0; i < keys.size(); ++i) {
        if (i) s << ",";
        s << keys[i];
    }
    if (inst.has_sliding_window) s << ";w=" << inst.sliding_window.window;
    s << ")";
    return s.str();
}

void rewrite_operands(IRInstruction& inst, const std::map<std::string, std::string>& aliases) {
    for (auto& o : inst.operands) {
        if (!o.is_const) o.name = resolve_alias(o.name, aliases);
    }
}

// Pass 1: indicator-level CSE.
void run_cse(IRProgram& ir, OptimizedIR& out) {
    auto& aliases = out.cse_aliases;
    std::map<std::string, size_t> sig_to_index;  // signature -> index into `kept`
    std::vector<IRInstruction> kept;

    for (auto& inst : ir.instructions) {
        if (inst.kind != IRKind::Series) {
            kept.push_back(inst);
            continue;
        }

        rewrite_operands(inst, aliases);
        std::string sig = signature(inst, aliases);
        auto found = sig_to_index.find(sig);
        if (found == sig_to_index.end()) {
            sig_to_index[sig] = kept.size();
            kept.push_back(inst);
            continue;
        }

        IRInstruction& canon = kept[found->second];
        if (!canon.user_named && inst.user_named) {
            // The survivor is a compiler temporary but this duplicate is a
            // user-visible series (an output column): the survivor takes the
            // user's name, and the old temp name becomes an alias.
            std::string old = canon.result;
            canon.result = inst.result;
            canon.user_named = true;
            for (auto& kv : aliases) {
                if (kv.second == old) kv.second = inst.result;
            }
            aliases[old] = inst.result;
            out.notes.push_back(OptimizationNote{
                "CSE: temporary '" + old + "' and series '" + inst.result +
                "' compute the exact same thing (" + sig +
                ") -- merged under the name '" + inst.result + "'"});
            // Earlier instructions may reference the old temp name; refresh
            // their operands and the signature index so later duplicates of
            // *those* are still found.
            sig_to_index.clear();
            for (size_t k = 0; k < kept.size(); ++k) {
                if (kept[k].kind != IRKind::Series) continue;
                rewrite_operands(kept[k], aliases);
                sig_to_index.emplace(signature(kept[k], aliases), k);
            }
        } else {
            aliases[inst.result] = canon.result;
            out.notes.push_back(OptimizationNote{
                "CSE: '" + inst.result + "' computes the exact same thing as '" +
                canon.result + "' (" + sig + ") -- removed, references rewritten"});
        }
    }

    // Final sweep: rewrite every surviving instruction's references
    // through the finished alias map.
    for (auto& inst : kept) {
        if (inst.kind == IRKind::Series) rewrite_operands(inst, aliases);
        for (auto& d : inst.condition_deps) d = resolve_alias(d, aliases);
    }
    ir.instructions = std::move(kept);
}

// Pass 2: pick an incremental-computation strategy for every windowed
// indicator. This is a lookup, not a rewrite -- codegen reads
// `incremental_plan` to decide which O(1)-per-bar update to emit.
void plan_incremental(const IRProgram& ir, OptimizedIR& out) {
    for (const auto& inst : ir.instructions) {
        if (inst.kind != IRKind::Series) continue;

        IncrementalStrategy strategy = IncrementalStrategy::None;
        switch (inst.op) {
            case SeriesOp::SMA: strategy = IncrementalStrategy::RunningSum; break;
            case SeriesOp::EMA: strategy = IncrementalStrategy::EwmaRecurrence; break;
            case SeriesOp::RSI: strategy = IncrementalStrategy::RollingGainLossSum; break;
            case SeriesOp::STDEV: strategy = IncrementalStrategy::RunningSumOfSquares; break;
            case SeriesOp::LAG: strategy = IncrementalStrategy::RingDelay; break;
            case SeriesOp::HIGHEST:
            case SeriesOp::LOWEST: strategy = IncrementalStrategy::RingScan; break;
            default: break;
        }
        out.incremental_plan[inst.result] = strategy;

        if (strategy != IncrementalStrategy::None) {
            out.notes.push_back(OptimizationNote{
                "Incremental plan: '" + inst.result + "' = " + series_op_name(inst.op) +
                " will use " + incremental_strategy_name(strategy) +
                (strategy == IncrementalStrategy::RingScan
                     ? " (one O(w) ring scan per bar, vectorized across lanes)"
                     : " (O(1) amortized per bar instead of O(w) recompute)")});
        }
    }
}

// Pass 3: fusion analysis. Every series instruction and every rule goes
// into one bar loop; only series that back an output column are stored.
void plan_fusion(const IRProgram& ir, OptimizedIR& out) {
    FusionGroup group;
    group.name = "bar_loop";
    size_t series_count = 0, temp_count = 0, rule_count = 0;
    for (const auto& inst : ir.instructions) {
        if (inst.kind == IRKind::Series) {
            group.members.push_back(inst.result);
            series_count++;
            if (!inst.user_named) temp_count++;
        } else if (inst.kind == IRKind::Rule) {
            group.members.push_back(inst.result);
            rule_count++;
        }
    }

    std::set<std::string> seen;
    for (const auto& col : ir.output_columns) {
        std::string target = resolve_alias(col, out.cse_aliases);
        if (target == "close") continue;  // already stored as the input array
        if (seen.insert(target).second) out.stored_columns.push_back(target);
    }

    out.fusion_groups.push_back(group);

    std::ostringstream m;
    m << "Fusion: " << series_count << " series op(s) + " << rule_count
      << " rule(s) evaluated in ONE pass over the bars per ticker; "
      << out.stored_columns.size() << " series stored as output columns, "
      << (series_count >= out.stored_columns.size() ? series_count - out.stored_columns.size() : 0)
      << " kept in registers only (" << temp_count << " compiler temporaries)";
    out.notes.push_back(OptimizationNote{m.str()});
}

}  // namespace

OptimizedIR optimize(const IRProgram& ir_in, const OptimizeOptions& opts) {
    OptimizedIR out;
    out.ir = ir_in;
    out.cse_aliases = ir_in.aliases;  // user-written `X = Y;` aliases are language semantics

    if (opts.cse) {
        run_cse(out.ir, out);
    } else {
        out.notes.push_back(OptimizationNote{"CSE: skipped (--no-cse)"});
    }

    // Flatten the alias map so every entry points straight at the survivor.
    for (auto& kv : out.cse_aliases) kv.second = resolve_alias(kv.first, out.cse_aliases);

    plan_incremental(out.ir, out);
    plan_fusion(out.ir, out);
    return out;
}

std::string to_string(const OptimizedIR& opt) {
    std::ostringstream s;
    s << "--- Optimization notes ---\n";
    if (opt.notes.empty()) {
        s << "(no optimizations applied -- nothing redundant or fusable found)\n";
    }
    for (const auto& n : opt.notes) {
        s << "  * " << n.message << "\n";
    }

    s << "\n--- Output columns stored per ticker ---\n  ";
    for (size_t i = 0; i < opt.stored_columns.size(); ++i) {
        if (i) s << ", ";
        s << opt.stored_columns[i];
    }
    s << "\n";

    s << "\n--- Optimized IR ---\n";
    s << to_string(opt.ir);
    return s.str();
}

}  // namespace valpha

#include "ir.hpp"
#include <cmath>
#include <cstdio>
#include <sstream>
#include <stdexcept>

namespace valpha {

const char* series_op_name(SeriesOp op) {
    switch (op) {
        case SeriesOp::SMA: return "SMA";
        case SeriesOp::EMA: return "EMA";
        case SeriesOp::RSI: return "RSI";
        case SeriesOp::STDEV: return "STDEV";
        case SeriesOp::LAG: return "LAG";
        case SeriesOp::HIGHEST: return "HIGHEST";
        case SeriesOp::LOWEST: return "LOWEST";
        case SeriesOp::ABS: return "ABS";
        case SeriesOp::ADD: return "ADD";
        case SeriesOp::SUB: return "SUB";
        case SeriesOp::MUL: return "MUL";
        case SeriesOp::DIV: return "DIV";
        case SeriesOp::NEG: return "NEG";
    }
    return "?";
}

bool is_windowed_op(SeriesOp op) {
    return op == SeriesOp::SMA || op == SeriesOp::EMA || op == SeriesOp::RSI ||
           op == SeriesOp::STDEV || op == SeriesOp::LAG || op == SeriesOp::HIGHEST ||
           op == SeriesOp::LOWEST;
}

bool is_commutative_op(SeriesOp op) { return op == SeriesOp::ADD || op == SeriesOp::MUL; }

std::string format_double(double v) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.17g", v);
    return buf;
}

namespace {

SeriesOp indicator_op(const std::string& name) {
    if (name == "SMA") return SeriesOp::SMA;
    if (name == "EMA") return SeriesOp::EMA;
    if (name == "RSI") return SeriesOp::RSI;
    if (name == "STDEV") return SeriesOp::STDEV;
    if (name == "LAG") return SeriesOp::LAG;
    if (name == "HIGHEST") return SeriesOp::HIGHEST;
    if (name == "LOWEST") return SeriesOp::LOWEST;
    if (name == "ABS") return SeriesOp::ABS;
    throw std::runtime_error("ir: '" + name + "' is not a series-valued built-in");
}

SeriesOp arithmetic_op(BinOp op) {
    switch (op) {
        case BinOp::ADD: return SeriesOp::ADD;
        case BinOp::SUB: return SeriesOp::SUB;
        case BinOp::MUL: return SeriesOp::MUL;
        case BinOp::DIV: return SeriesOp::DIV;
        default: break;
    }
    throw std::runtime_error("ir: not an arithmetic operator");
}

// Collects every Identifier name referenced anywhere inside `e`
// (used for a rule condition's dependency list).
void collect_identifiers(const ExprPtr& e, std::vector<std::string>& out) {
    if (!e) return;
    switch (e->kind) {
        case ExprKind::Identifier:
            out.push_back(e->name);
            return;
        case ExprKind::NumberLiteral:
        case ExprKind::SetLiteral:
            return;
        case ExprKind::FunctionCall:
            for (const auto& a : e->args) collect_identifiers(a, out);
            return;
        case ExprKind::Negate:
            collect_identifiers(e->left, out);
            return;
        case ExprKind::Comparison:
        case ExprKind::LogicalAnd:
        case ExprKind::LogicalOr:
        case ExprKind::Arithmetic:
            collect_identifiers(e->left, out);
            collect_identifiers(e->right, out);
            return;
    }
}

ExprPtr operand_expr(const Operand& o) {
    return o.is_const ? Expr::make_number(o.value) : Expr::make_identifier(o.name);
}

class Lowering {
public:
    Lowering(const SemanticResult& sem, IRProgram& ir) : sem_(sem), ir_(ir) {}

    // Lowers a numeric (SCALAR or SERIES) expression to an Operand,
    // emitting one instruction per non-trivial sub-expression, in
    // dependency order. `target`, if non-empty, names the instruction
    // that computes `e` itself (a user-visible series name); otherwise a
    // fresh "%tN" temporary is used.
    Operand lower_numeric(const ExprPtr& e, const std::string& target) {
        double folded = 0.0;
        if (try_eval_scalar(e, sem_, folded)) return Operand::constant(folded);

        switch (e->kind) {
            case ExprKind::Identifier:
                return Operand::series(e->name);

            case ExprKind::FunctionCall: {
                Operand src = lower_numeric(e->args[0], "");
                IRInstruction inst = start_series(target);
                inst.op = indicator_op(e->name);
                inst.operands = {src};
                if (is_windowed_op(inst.op)) {  // everything but ABS carries a constant window / lag
                    double w = 0.0;
                    try_eval_scalar(e->args[1], sem_, w);
                    inst.has_sliding_window = true;
                    inst.sliding_window.function = e->name;
                    inst.sliding_window.window = static_cast<int>(std::llround(w));
                }
                return emit(inst);
            }

            case ExprKind::Negate: {
                Operand a = lower_numeric(e->left, "");
                IRInstruction inst = start_series(target);
                inst.op = SeriesOp::NEG;
                inst.operands = {a};
                return emit(inst);
            }

            case ExprKind::Arithmetic: {
                Operand a = lower_numeric(e->left, "");
                Operand b = lower_numeric(e->right, "");
                IRInstruction inst = start_series(target);
                inst.op = arithmetic_op(e->op);
                inst.operands = {a, b};
                return emit(inst);
            }

            default:
                throw std::runtime_error("ir: expression '" + to_string(e) +
                                          "' cannot be lowered as a numeric value");
        }
    }

    // Lowers a boolean expression into a condition tree whose leaves'
    // operands are plain identifiers/numbers (see IRInstruction::condition).
    ExprPtr lower_condition(const ExprPtr& e) {
        switch (e->kind) {
            case ExprKind::Comparison: {
                Operand a = lower_numeric(e->left, "");
                Operand b = lower_numeric(e->right, "");
                return Expr::make_binary(ExprKind::Comparison, e->op, operand_expr(a),
                                         operand_expr(b));
            }
            case ExprKind::LogicalAnd:
            case ExprKind::LogicalOr:
                return Expr::make_binary(e->kind, e->op, lower_condition(e->left),
                                         lower_condition(e->right));
            case ExprKind::FunctionCall: {
                std::vector<ExprPtr> args;
                for (const auto& a : e->args) args.push_back(operand_expr(lower_numeric(a, "")));
                return Expr::make_call(e->name, args);
            }
            case ExprKind::Identifier: {
                // A named boolean (`GOLDEN = CROSSOVER(a, b);`): inline its definition.
                const Symbol& sym = sem_.symbols.at(e->name);
                return lower_condition(sym.definition);
            }
            default:
                throw std::runtime_error("ir: expression '" + to_string(e) +
                                          "' cannot be lowered as a condition");
        }
    }

private:
    const SemanticResult& sem_;
    IRProgram& ir_;
    int temp_counter_ = 0;

    IRInstruction start_series(const std::string& target) {
        IRInstruction inst;
        inst.kind = IRKind::Series;
        inst.cross_sectional = true;  // every series op is computed independently per ticker
        if (target.empty()) {
            inst.result = "%t" + std::to_string(temp_counter_++);
            inst.user_named = false;
        } else {
            inst.result = target;
            inst.user_named = true;
        }
        return inst;
    }

    Operand emit(const IRInstruction& inst) {
        ir_.instructions.push_back(inst);
        return Operand::series(inst.result);
    }
};

}  // namespace

IRProgram build_ir(const SemanticResult& sem) {
    IRProgram ir;
    Lowering lower(sem, ir);

    for (const std::string& name : sem.symbol_order) {
        if (name == "close") continue;  // built-in, not a user-defined instruction

        const Symbol& sym = sem.symbols.at(name);

        if (name == "INITIAL_CAPITAL" || name == "ALLOCATION") {
            double v = 0.0;
            try_eval_scalar(sym.definition, sem, v);
            if (name == "INITIAL_CAPITAL") ir.initial_capital = v;
            else ir.allocation = v;
            IRInstruction inst;
            inst.kind = IRKind::ConfigScalar;
            inst.result = name;
            inst.scalar_value = v;
            inst.cross_sectional = false;  // a single global config value, not per-ticker
            ir.instructions.push_back(inst);
        } else if (name == "UNIVERSE") {
            ir.universe = sym.definition->set_items;
            IRInstruction inst;
            inst.kind = IRKind::UniverseSet;
            inst.result = name;
            inst.universe_tickers = sym.definition->set_items;
            inst.cross_sectional = false;  // defines the ticker set itself
            ir.instructions.push_back(inst);
        } else if (sym.type == ValueType::SERIES) {
            ir.output_columns.push_back(name);
            Operand o = lower.lower_numeric(sym.definition, name);
            if (o.is_const) continue;  // cannot happen for a SERIES-typed definition
            if (o.name != name) ir.aliases[name] = o.name;  // `X = Y;` -- pure alias
        }
        // SCALAR and BOOL user symbols: no instruction, they are folded /
        // inlined at every use site (see Lowering).
    }

    int rule_index = 0;
    for (const auto& rule_stmt : sem.rules) {
        IRInstruction inst;
        inst.kind = IRKind::Rule;
        inst.result = "%rule" + std::to_string(rule_index++);
        inst.cross_sectional = true;  // BUY/SELL is evaluated independently per ticker too
        inst.rule_kind = rule_stmt->rule_kind;
        inst.condition = lower.lower_condition(rule_stmt->condition);
        collect_identifiers(inst.condition, inst.condition_deps);
        ir.instructions.push_back(inst);
    }

    return ir;
}

namespace {
std::string join(const std::vector<std::string>& items) {
    std::ostringstream out;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i) out << ", ";
        out << items[i];
    }
    return out.str();
}

std::string operand_str(const Operand& o) {
    if (o.is_const) {
        std::ostringstream s;
        s << o.value;
        return s.str();
    }
    return o.name;
}

const char* tf(bool b) { return b ? "true" : "false"; }
}  // namespace

std::string to_string(const IRProgram& ir) {
    std::ostringstream out;
    out << "INITIAL_CAPITAL = " << ir.initial_capital << "\n";
    out << "ALLOCATION      = " << ir.allocation << "\n";
    out << "UNIVERSE        = { " << join(ir.universe) << " }\n";
    for (const auto& kv : ir.aliases) {
        out << "alias " << kv.first << " -> " << kv.second << "\n";
    }
    out << "\n";

    for (const auto& inst : ir.instructions) {
        switch (inst.kind) {
            case IRKind::ConfigScalar:
                out << "config " << inst.result << " = " << inst.scalar_value
                    << "  [cross_sectional=" << tf(inst.cross_sectional) << "]\n";
                break;
            case IRKind::UniverseSet:
                out << "universe " << inst.result << " = { " << join(inst.universe_tickers)
                    << " }  [cross_sectional=" << tf(inst.cross_sectional) << "]\n";
                break;
            case IRKind::Series: {
                out << inst.result << " = " << series_op_name(inst.op) << "(";
                for (size_t i = 0; i < inst.operands.size(); ++i) {
                    if (i) out << ", ";
                    out << operand_str(inst.operands[i]);
                }
                if (inst.has_sliding_window) out << ", w=" << inst.sliding_window.window;
                out << ")  [cross_sectional=" << tf(inst.cross_sectional)
                    << ", user_named=" << tf(inst.user_named);
                if (inst.has_sliding_window) {
                    out << ", sliding_window={" << inst.sliding_window.function << "/"
                        << inst.sliding_window.window << "}";
                }
                out << "]\n";
                break;
            }
            case IRKind::Rule:
                out << "RULE " << (inst.rule_kind == RuleKind::BUY ? "BUY" : "SELL")
                    << " WHEN " << to_string(inst.condition)
                    << "  deps=[" << join(inst.condition_deps) << "]"
                    << "  [cross_sectional=" << tf(inst.cross_sectional) << "]\n";
                break;
        }
    }
    return out.str();
}

}  // namespace valpha

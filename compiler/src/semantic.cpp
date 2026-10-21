#include "semantic.hpp"
#include <cmath>
#include <sstream>

namespace valpha {

const char* value_type_name(ValueType t) {
    switch (t) {
        case ValueType::SCALAR: return "SCALAR";
        case ValueType::SERIES: return "SERIES";
        case ValueType::BOOL: return "BOOL";
        case ValueType::SET: return "SET";
        case ValueType::UNKNOWN: return "UNKNOWN";
    }
    return "?";
}

bool try_eval_scalar(const ExprPtr& e, const SemanticResult& sem, double& out) {
    if (!e) return false;
    switch (e->kind) {
        case ExprKind::NumberLiteral:
            out = e->number;
            return true;
        case ExprKind::Identifier: {
            auto it = sem.symbols.find(e->name);
            if (it == sem.symbols.end()) return false;
            if (it->second.type != ValueType::SCALAR || !it->second.definition) return false;
            return try_eval_scalar(it->second.definition, sem, out);
        }
        case ExprKind::Negate: {
            double v;
            if (!try_eval_scalar(e->left, sem, v)) return false;
            out = -v;
            return true;
        }
        case ExprKind::Arithmetic: {
            double a, b;
            if (!try_eval_scalar(e->left, sem, a) || !try_eval_scalar(e->right, sem, b)) return false;
            switch (e->op) {
                case BinOp::ADD: out = a + b; return true;
                case BinOp::SUB: out = a - b; return true;
                case BinOp::MUL: out = a * b; return true;
                case BinOp::DIV:
                    if (b == 0.0) return false;
                    out = a / b;
                    return true;
                default: return false;
            }
        }
        default:
            return false;
    }
}

namespace {

// A built-in function's argument shape.
//   SERIES  -- any expression that types as a SERIES (a price series,
//              another indicator, or arithmetic over those)
//   NUMERIC -- a SERIES or a SCALAR (CROSSOVER(RSI_14, 30) is legal)
//   WINDOW  -- a compile-time constant integer >= 1. Phase 3/4/5 rely
//              on this: window sizes become array lengths in the
//              generated code, so they cannot be series-dependent.
enum class ArgKind { SERIES, NUMERIC, WINDOW };

struct FnSig {
    std::vector<ArgKind> args;
    ValueType return_type;
};

const std::map<std::string, FnSig>& builtin_table() {
    static const std::map<std::string, FnSig> table = {
        {"SMA", {{ArgKind::SERIES, ArgKind::WINDOW}, ValueType::SERIES}},
        {"EMA", {{ArgKind::SERIES, ArgKind::WINDOW}, ValueType::SERIES}},
        {"RSI", {{ArgKind::SERIES, ArgKind::WINDOW}, ValueType::SERIES}},
        {"STDEV", {{ArgKind::SERIES, ArgKind::WINDOW}, ValueType::SERIES}},
        {"LAG", {{ArgKind::SERIES, ArgKind::WINDOW}, ValueType::SERIES}},      // x[i-n]
        {"HIGHEST", {{ArgKind::SERIES, ArgKind::WINDOW}, ValueType::SERIES}},  // rolling max
        {"LOWEST", {{ArgKind::SERIES, ArgKind::WINDOW}, ValueType::SERIES}},   // rolling min
        {"ABS", {{ArgKind::SERIES}, ValueType::SERIES}},
        {"CROSSOVER", {{ArgKind::NUMERIC, ArgKind::NUMERIC}, ValueType::BOOL}},
        {"CROSSUNDER", {{ArgKind::NUMERIC, ArgKind::NUMERIC}, ValueType::BOOL}},
    };
    return table;
}

bool is_numeric_type(ValueType t) {
    return t == ValueType::SCALAR || t == ValueType::SERIES || t == ValueType::UNKNOWN;
}

class SemanticAnalyzer {
public:
    SemanticResult run(const Program& program) {
        // "close" is the one built-in series every strategy can
        // reference without defining it itself.
        Symbol close_sym;
        close_sym.name = "close";
        close_sym.type = ValueType::SERIES;
        result_.symbols["close"] = close_sym;
        result_.symbol_order.push_back("close");

        for (const auto& stmt : program.statements) {
            if (stmt->kind == StatementKind::Assignment) {
                analyze_assignment(stmt);
            } else {
                analyze_rule(stmt);
            }
        }

        if (!result_.has_symbol("INITIAL_CAPITAL")) {
            error("INITIAL_CAPITAL is required (e.g. INITIAL_CAPITAL = 100000;)");
        }
        if (!result_.has_symbol("ALLOCATION")) {
            error("ALLOCATION is required (e.g. ALLOCATION = 0.5;)");
        }

        result_.ok = result_.errors.empty();
        return result_;
    }

private:
    SemanticResult result_;

    void error(const std::string& msg) {
        result_.errors.push_back(SemanticError{msg});
    }

    void analyze_assignment(const StatementPtr& stmt) {
        const std::string& name = stmt->target_name;

        if (result_.has_symbol(name)) {
            error("'" + name + "' is already defined (redefinition is not allowed)");
            // still analyze the RHS so later errors in it are also reported
        }

        std::vector<std::string> deps;
        ValueType type = analyze_expr(stmt->value, deps);

        if (name == "INITIAL_CAPITAL" || name == "ALLOCATION") {
            if (type != ValueType::SCALAR && type != ValueType::UNKNOWN) {
                error(name + " must be a plain number, got " + value_type_name(type));
            } else if (type == ValueType::SCALAR) {
                double v = 0.0;
                if (!try_eval_scalar(stmt->value, result_, v)) {
                    error(name + " must be a constant number");
                } else if (name == "INITIAL_CAPITAL" && !(v > 0.0)) {
                    error("INITIAL_CAPITAL must be greater than 0");
                } else if (name == "ALLOCATION" && !(v > 0.0 && v <= 1.0)) {
                    error("ALLOCATION must be greater than 0 and at most 1 (a fraction of capital per trade)");
                }
            }
        } else if (name == "UNIVERSE") {
            if (type != ValueType::SET && type != ValueType::UNKNOWN) {
                error("UNIVERSE must be a '{ TICKER, ... }' set literal, got " +
                      std::string(value_type_name(type)));
            }
        } else {
            if (type == ValueType::SET) {
                error("'" + name +
                      "': a '{ ... }' set literal may only be assigned to UNIVERSE");
            }
        }

        Symbol sym;
        sym.name = name;
        sym.type = type;
        sym.depends_on = deps;
        sym.definition = stmt->value;
        result_.symbols[name] = sym;
        result_.symbol_order.push_back(name);
    }

    void analyze_rule(const StatementPtr& stmt) {
        std::vector<std::string> deps;
        ValueType type = analyze_expr(stmt->condition, deps);
        if (type != ValueType::BOOL && type != ValueType::UNKNOWN) {
            std::string which = (stmt->rule_kind == RuleKind::BUY) ? "BUY" : "SELL";
            error(which + " WHEN condition must evaluate to a boolean (a comparison, " +
                  "CROSSOVER/CROSSUNDER call, or AND/OR of those), got " +
                  value_type_name(type));
        }
        result_.rules.push_back(stmt);
    }

    // Analyzes `e`, recording every identifier it transitively touches
    // into `deps_out` (used for the indicator dependency graph in
    // Phase 3/4), and returns the expression's inferred type.
    ValueType analyze_expr(const ExprPtr& e, std::vector<std::string>& deps_out) {
        if (!e) return ValueType::UNKNOWN;

        switch (e->kind) {
            case ExprKind::NumberLiteral:
                return ValueType::SCALAR;

            case ExprKind::SetLiteral:
                return ValueType::SET;

            case ExprKind::Identifier: {
                auto it = result_.symbols.find(e->name);
                if (it == result_.symbols.end()) {
                    error("undefined identifier '" + e->name +
                          "' (used before it was defined, or never defined)");
                    return ValueType::UNKNOWN;
                }
                deps_out.push_back(e->name);
                return it->second.type;
            }

            case ExprKind::FunctionCall:
                return analyze_call(e, deps_out);

            case ExprKind::Negate: {
                ValueType t = analyze_expr(e->left, deps_out);
                if (!is_numeric_type(t)) {
                    error("unary '-' needs a numeric operand (SCALAR or SERIES), got " +
                          std::string(value_type_name(t)) + " ('" + to_string(e->left) + "')");
                    return ValueType::UNKNOWN;
                }
                return t;
            }

            case ExprKind::Arithmetic: {
                ValueType lt = analyze_expr(e->left, deps_out);
                ValueType rt = analyze_expr(e->right, deps_out);
                if (!is_numeric_type(lt) || !is_numeric_type(rt)) {
                    error("arithmetic '" + to_string(e) + "' needs numeric operands (SCALAR or SERIES), got " +
                          value_type_name(lt) + " and " + value_type_name(rt));
                    return ValueType::UNKNOWN;
                }
                if (e->op == BinOp::DIV) {
                    double d = 0.0;
                    if (try_eval_scalar(e->right, result_, d) && d == 0.0) {
                        error("division by zero in '" + to_string(e) + "'");
                    }
                }
                if (lt == ValueType::SERIES || rt == ValueType::SERIES) return ValueType::SERIES;
                if (lt == ValueType::SCALAR && rt == ValueType::SCALAR) return ValueType::SCALAR;
                return ValueType::UNKNOWN;
            }

            case ExprKind::Comparison: {
                ValueType lt = analyze_expr(e->left, deps_out);
                ValueType rt = analyze_expr(e->right, deps_out);
                if (!is_numeric_type(lt) || !is_numeric_type(rt)) {
                    error("comparison '" + to_string(e->left) + " ... " + to_string(e->right) +
                          "' needs numeric operands (SCALAR or SERIES), got " +
                          value_type_name(lt) + " and " + value_type_name(rt));
                }
                return ValueType::BOOL;
            }

            case ExprKind::LogicalAnd:
            case ExprKind::LogicalOr: {
                ValueType lt = analyze_expr(e->left, deps_out);
                ValueType rt = analyze_expr(e->right, deps_out);
                const char* op = (e->kind == ExprKind::LogicalAnd) ? "AND" : "OR";
                if ((lt != ValueType::BOOL && lt != ValueType::UNKNOWN) ||
                    (rt != ValueType::BOOL && rt != ValueType::UNKNOWN)) {
                    error(std::string(op) + " needs boolean operands on both sides, got " +
                          value_type_name(lt) + " and " + value_type_name(rt));
                }
                return ValueType::BOOL;
            }
        }
        return ValueType::UNKNOWN;
    }

    ValueType analyze_call(const ExprPtr& e, std::vector<std::string>& deps_out) {
        const auto& table = builtin_table();
        auto it = table.find(e->name);
        if (it == table.end()) {
            error("unknown function '" + e->name + "' (built-ins: SMA, EMA, RSI, STDEV, LAG, "
                  "HIGHEST, LOWEST, ABS, CROSSOVER, CROSSUNDER)");
            // still analyze args so we surface any other errors inside them
            for (const auto& a : e->args) analyze_expr(a, deps_out);
            return ValueType::UNKNOWN;
        }

        const FnSig& sig = it->second;
        if (e->args.size() != sig.args.size()) {
            error(e->name + "() expects " + std::to_string(sig.args.size()) +
                  " argument(s), got " + std::to_string(e->args.size()));
            for (const auto& a : e->args) analyze_expr(a, deps_out);
            return sig.return_type;
        }

        size_t series_args = 0;
        size_t unknown_args = 0;
        for (size_t i = 0; i < sig.args.size(); ++i) {
            const ExprPtr& arg = e->args[i];
            if (sig.args[i] == ArgKind::WINDOW) {
                // Window args are compile-time constants, not series
                // dependencies, so they don't go into the dependency list.
                double v = 0.0;
                if (!try_eval_scalar(arg, result_, v) || v < 1.0 ||
                    std::fabs(v - std::round(v)) > 1e-9) {
                    error(e->name + "(): argument " + std::to_string(i + 1) +
                          " (window size) must be a constant integer >= 1, got '" +
                          to_string(arg) + "'");
                }
            } else if (sig.args[i] == ArgKind::NUMERIC) {
                ValueType t = analyze_expr(arg, deps_out);
                if (!is_numeric_type(t)) {
                    error(e->name + "(): argument " + std::to_string(i + 1) +
                          " must be numeric (a series or a number), got " +
                          value_type_name(t) + " ('" + to_string(arg) + "')");
                }
                if (t == ValueType::SERIES) series_args++;
                if (t == ValueType::UNKNOWN) unknown_args++;
            } else {  // ArgKind::SERIES
                ValueType t = analyze_expr(arg, deps_out);
                if (t != ValueType::SERIES && t != ValueType::UNKNOWN) {
                    error(e->name + "(): argument " + std::to_string(i + 1) +
                          " must be a series (a price series or another indicator), got " +
                          value_type_name(t) + " ('" + to_string(arg) + "')");
                }
            }
        }
        if ((e->name == "CROSSOVER" || e->name == "CROSSUNDER") && series_args == 0 &&
            unknown_args == 0) {
            error(e->name + "(): at least one argument must be a series (two constants never cross)");
        }
        return sig.return_type;
    }
};

}  // namespace

SemanticResult analyze(const Program& program) {
    SemanticAnalyzer analyzer;
    return analyzer.run(program);
}

}  // namespace valpha

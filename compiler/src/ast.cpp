#include "ast.hpp"
#include <iomanip>
#include <sstream>

namespace valpha {

namespace {
const char* binop_str(BinOp op) {
    switch (op) {
        case BinOp::LT: return "<";
        case BinOp::GT: return ">";
        case BinOp::LE: return "<=";
        case BinOp::GE: return ">=";
        case BinOp::EQ: return "==";
        case BinOp::NE: return "!=";
        case BinOp::AND: return "AND";
        case BinOp::OR: return "OR";
        case BinOp::ADD: return "+";
        case BinOp::SUB: return "-";
        case BinOp::MUL: return "*";
        case BinOp::DIV: return "/";
        case BinOp::NEG: return "-";
        case BinOp::NONE: return "?";
    }
    return "?";
}
}  // namespace

std::string to_string(const ExprPtr& e) {
    if (!e) return "<null>";
    std::ostringstream out;
    switch (e->kind) {
        case ExprKind::Identifier:
            out << e->name;
            break;
        case ExprKind::NumberLiteral:
            out << std::setprecision(12) << e->number;
            break;
        case ExprKind::FunctionCall: {
            out << e->name << "(";
            for (size_t i = 0; i < e->args.size(); ++i) {
                if (i) out << ", ";
                out << to_string(e->args[i]);
            }
            out << ")";
            break;
        }
        case ExprKind::SetLiteral: {
            out << "{ ";
            for (size_t i = 0; i < e->set_items.size(); ++i) {
                if (i) out << ", ";
                out << e->set_items[i];
            }
            out << " }";
            break;
        }
        case ExprKind::Comparison:
        case ExprKind::LogicalAnd:
        case ExprKind::LogicalOr:
        case ExprKind::Arithmetic:
            out << "(" << to_string(e->left) << " " << binop_str(e->op) << " "
                << to_string(e->right) << ")";
            break;
        case ExprKind::Negate:
            out << "(-" << to_string(e->left) << ")";
            break;
    }
    return out.str();
}

std::string to_string(const Program& program) {
    std::ostringstream out;
    for (const auto& s : program.statements) {
        if (s->kind == StatementKind::Assignment) {
            out << s->target_name << " = " << to_string(s->value) << ";\n";
        } else {
            out << (s->rule_kind == RuleKind::BUY ? "BUY" : "SELL")
                << " WHEN " << to_string(s->condition) << ";\n";
        }
    }
    return out.str();
}

}  // namespace valpha

#pragma once
#include <memory>
#include <string>
#include <vector>

// AST for the .valpha DSL (VectorAlpha_IO_Spec.docx Section 1-2).
//
// This is Phase 1's output: a plain syntax tree with no semantic
// checking yet (Phase 2) and no domain-aware IR tagging yet (Phase 3).
// It exists so the parser has something concrete to build and the
// team can eyeball-verify parsing correctness before anything else is
// layered on top.

namespace valpha {

// ---- Expressions ----

enum class ExprKind {
    Identifier,
    NumberLiteral,
    FunctionCall,
    SetLiteral,
    Comparison,   // a < b, a >= b, etc.
    LogicalAnd,
    LogicalOr,
    Arithmetic,   // a + b, a - b, a * b, a / b   (DSL v2)
    Negate,       // -a                           (DSL v2; operand in `left`)
};

// One comparison/logical/arithmetic operator. NEG is only used by
// ExprKind::Negate nodes.
enum class BinOp { LT, GT, LE, GE, EQ, NE, AND, OR, ADD, SUB, MUL, DIV, NEG, NONE };

struct Expr;
using ExprPtr = std::shared_ptr<Expr>;

struct Expr {
    ExprKind kind;

    // Identifier
    std::string name;

    // NumberLiteral
    double number = 0.0;

    // FunctionCall: name = function name, args = argument expressions
    std::vector<ExprPtr> args;

    // SetLiteral (e.g. UNIVERSE = { STOCKA, STOCKB }): each element is
    // a bare identifier, stored directly for simplicity.
    std::vector<std::string> set_items;

    // Comparison / LogicalAnd / LogicalOr / Arithmetic / Negate
    BinOp op = BinOp::NONE;
    ExprPtr left;
    ExprPtr right;

    static ExprPtr make_identifier(std::string n) {
        auto e = std::make_shared<Expr>();
        e->kind = ExprKind::Identifier;
        e->name = std::move(n);
        return e;
    }
    static ExprPtr make_number(double v) {
        auto e = std::make_shared<Expr>();
        e->kind = ExprKind::NumberLiteral;
        e->number = v;
        return e;
    }
    static ExprPtr make_call(std::string fn, std::vector<ExprPtr> a) {
        auto e = std::make_shared<Expr>();
        e->kind = ExprKind::FunctionCall;
        e->name = std::move(fn);
        e->args = std::move(a);
        return e;
    }
    static ExprPtr make_set(std::vector<std::string> items) {
        auto e = std::make_shared<Expr>();
        e->kind = ExprKind::SetLiteral;
        e->set_items = std::move(items);
        return e;
    }
    static ExprPtr make_negate(ExprPtr operand) {
        auto e = std::make_shared<Expr>();
        e->kind = ExprKind::Negate;
        e->op = BinOp::NEG;
        e->left = std::move(operand);
        return e;
    }
    static ExprPtr make_binary(ExprKind kind, BinOp op, ExprPtr l, ExprPtr r) {
        auto e = std::make_shared<Expr>();
        e->kind = kind;
        e->op = op;
        e->left = std::move(l);
        e->right = std::move(r);
        return e;
    }
};

// Renders an expression back to DSL-ish text, for debugging/verification.
std::string to_string(const ExprPtr& e);

// ---- Statements ----

enum class StatementKind {
    Assignment,   // NAME = expr ;      (covers INITIAL_CAPITAL, ALLOCATION,
                  //                     UNIVERSE, and indicator defs alike --
                  //                     Phase 2 semantic analysis is what
                  //                     tells them apart by type-checking)
    Rule,         // BUY WHEN expr ;  or  SELL WHEN expr ;
};

enum class RuleKind { BUY, SELL };

struct Statement {
    StatementKind kind;

    // Assignment
    std::string target_name;
    ExprPtr value;

    // Rule
    RuleKind rule_kind = RuleKind::BUY;
    ExprPtr condition;

    static std::shared_ptr<Statement> make_assignment(std::string name, ExprPtr value) {
        auto s = std::make_shared<Statement>();
        s->kind = StatementKind::Assignment;
        s->target_name = std::move(name);
        s->value = std::move(value);
        return s;
    }
    static std::shared_ptr<Statement> make_rule(RuleKind rk, ExprPtr cond) {
        auto s = std::make_shared<Statement>();
        s->kind = StatementKind::Rule;
        s->rule_kind = rk;
        s->condition = std::move(cond);
        return s;
    }
};
using StatementPtr = std::shared_ptr<Statement>;

struct Program {
    std::vector<StatementPtr> statements;
};

// Pretty-prints the whole AST, one statement per line, for the Phase 1
// smoke test (see src/main.cpp).
std::string to_string(const Program& program);

}  // namespace valpha

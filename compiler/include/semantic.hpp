#pragma once
#include <map>
#include <set>
#include <string>
#include <vector>
#include "ast.hpp"

// Phase 2 -- Semantic Analysis (Report 1 Section 3.3, "Module 3").
//
// Walks the Phase 1 AST and:
//   - builds a symbol table of every name bound by an assignment
//     (INITIAL_CAPITAL, ALLOCATION, UNIVERSE, and every indicator
//     definition)
//   - type-checks every expression (SCALAR / SERIES / BOOL / SET)
//   - checks built-in function calls against a fixed signature table
//     (arity, argument types, and -- since a window size must be known
//     at compile time -- validates that "w"-style arguments are
//     constant integer expressions >= 1; `WINDOW = 20;` followed by
//     `SMA(close, WINDOW)` is therefore fine)
//   - types arithmetic (+ - * / and unary minus): SCALAR op SCALAR is a
//     SCALAR (and constant-folded later), anything involving a SERIES
//     is a SERIES; a literal division by zero is rejected
//   - validates the config scalars (INITIAL_CAPITAL > 0, 0 < ALLOCATION
//     <= 1) and that both are present
//   - rejects references to identifiers that are not yet defined at
//     the point of use (the DSL has no forward references, which also
//     rules out indicator dependency cycles by construction)
//   - rejects redefinition of an already-defined name
//   - requires BUY/SELL WHEN conditions to type as BOOL
//
// This pass does not know about C++ codegen, SIMD, or threading --
// those only enter in Phase 3 (IR) and Phase 5 (codegen). Its only
// job is "is this .valpha program meaningful," producing a
// SemanticResult that Phase 3's IR builder can trust without
// re-checking anything.

namespace valpha {

enum class ValueType { SCALAR, SERIES, BOOL, SET, UNKNOWN };

const char* value_type_name(ValueType t);

struct SemanticError {
    std::string message;
};

// One entry in the symbol table: every name ever assigned to, with
// the type it resolved to and (for indicator definitions) the names
// of the other symbols it directly depends on.
struct Symbol {
    std::string name;
    ValueType type = ValueType::UNKNOWN;
    std::vector<std::string> depends_on;  // direct dependencies, in the
                                           // order they were referenced
    ExprPtr definition;                   // the RHS expression, if any
};

struct SemanticResult {
    bool ok = false;
    std::vector<SemanticError> errors;

    // Insertion-ordered symbol table (indicator definitions keep
    // program order, which Phase 3/4 rely on for topological codegen).
    std::vector<std::string> symbol_order;
    std::map<std::string, Symbol> symbols;

    std::vector<StatementPtr> rules;  // BUY/SELL WHEN statements, in order

    bool has_symbol(const std::string& name) const {
        return symbols.find(name) != symbols.end();
    }
};

// Runs semantic analysis over a full program. Never throws -- all
// problems are collected into SemanticResult::errors, and ok is false
// if the vector is non-empty.
SemanticResult analyze(const Program& program);

// If `e` is a compile-time constant scalar expression (number literals,
// user-defined scalar constants, + - * / and unary minus over those),
// evaluates it into `out` and returns true. Returns false for anything
// that involves a series/function/set, an undefined name, or a division
// by zero. Used by semantic analysis (window sizes, config validation)
// and by IR lowering (constant folding).
bool try_eval_scalar(const ExprPtr& e, const SemanticResult& sem, double& out);

}  // namespace valpha

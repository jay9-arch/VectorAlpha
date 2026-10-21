#pragma once
#include <map>
#include <string>
#include <vector>
#include "ast.hpp"
#include "semantic.hpp"

// Phase 3 -- Domain-Aware IR (Report 1 Section 3.3, "Module 4").
//
// Lowers a semantically-checked Program into a flat list of
// three-address-style IRInstructions: every SERIES-valued computation
// is ONE instruction with at most two operands, and every nested
// sub-expression becomes its own instruction writing a compiler
// temporary ("%t0", "%t1", ...). For example
//
//     UPPER = EMA(close, 20) + 2 * STDEV(close, 10);
//
// lowers to
//
//     %t0   = EMA(close, w=20)
//     %t1   = STDEV(close, w=10)
//     %t2   = MUL(2, %t1)
//     UPPER = ADD(%t0, %t2)
//
// Scalar sub-expressions are constant-folded away (an Operand is either
// a series name or a constant). User-defined scalar constants
// (`WINDOW = 20;`) and boolean definitions (`GOLDEN = CROSSOVER(a, b);`)
// do not produce instructions: scalars fold into their use sites and
// booleans are inlined into the rule conditions that mention them.
//
// Every instruction also carries the two domain tags Report 1
// Section 3.3 calls out, which a general-purpose compiler's IR does not
// carry and would have to expensively re-derive:
//
//   cross_sectional  -- the computation is independent for every ticker
//                       in UNIVERSE (true for everything in the current
//                       DSL; still tagged explicitly, so it is ready for
//                       a future cross-sectional ranking function).
//
//   sliding_window   -- for the windowed ops (SMA/EMA/RSI/STDEV/LAG/HIGHEST/
//                       LOWEST): the (function, window)
//                       pair, so Phase 4 can recognize "this becomes an
//                       O(1)-per-step incremental update" without
//                       re-parsing the call.

namespace valpha {

enum class IRKind {
    ConfigScalar,  // INITIAL_CAPITAL / ALLOCATION
    UniverseSet,   // UNIVERSE = { ... }
    Series,        // one SERIES-valued operation (indicator or arithmetic)
    Rule,          // BUY/SELL WHEN condition
};

enum class SeriesOp {
    SMA, EMA, RSI, STDEV,   // windowed indicators: operands = {source}, window in sliding_window
    LAG,                    // x[i-n]: operands = {source}, n in sliding_window.window
    HIGHEST, LOWEST,        // rolling max / min over the last w values (incl. current)
    ABS,                    // |x|: operands = {x}
    ADD, SUB, MUL, DIV,     // binary arithmetic: operands = {lhs, rhs}; DIV by 0 yields "missing"
    NEG,                    // unary minus: operands = {x}
};

const char* series_op_name(SeriesOp op);
bool is_windowed_op(SeriesOp op);
bool is_commutative_op(SeriesOp op);

// An instruction operand: either a series (by name: "close", a user
// series name, or a "%tN" temporary) or a folded numeric constant.
struct Operand {
    bool is_const = false;
    double value = 0.0;
    std::string name;

    static Operand constant(double v) {
        Operand o;
        o.is_const = true;
        o.value = v;
        return o;
    }
    static Operand series(std::string n) {
        Operand o;
        o.name = std::move(n);
        return o;
    }
};

// Present only on Series instructions whose op is one of the four
// windowed indicators (SMA/EMA/RSI/STDEV).
struct SlidingWindowTag {
    std::string function;
    int window = 0;
};

struct IRInstruction {
    IRKind kind = IRKind::Series;
    std::string result;  // symbol this instruction computes: a user name,
                          // a "%tN" temporary, or a synthetic "%ruleN"
    bool user_named = false;  // true if `result` is a name the user wrote
                               // (such series become output CSV columns)

    // Domain tags -- see the header comment above.
    bool cross_sectional = true;
    bool has_sliding_window = false;
    SlidingWindowTag sliding_window;

    // ConfigScalar
    double scalar_value = 0.0;

    // UniverseSet
    std::vector<std::string> universe_tickers;

    // Series
    SeriesOp op = SeriesOp::ADD;
    std::vector<Operand> operands;

    // Rule. `condition` is a *lowered* boolean tree: comparisons and
    // CROSSOVER/CROSSUNDER calls whose operands are always plain
    // Identifier (series name) or NumberLiteral nodes -- every series
    // sub-expression has already been hoisted into its own instruction.
    RuleKind rule_kind = RuleKind::BUY;
    ExprPtr condition;
    std::vector<std::string> condition_deps;  // every series name the condition reads
};

struct IRProgram {
    double initial_capital = 0.0;
    double allocation = 0.0;
    std::vector<std::string> universe;
    std::vector<IRInstruction> instructions;  // Series + Rule instructions, in program order
                                               // (operands always precede their users)

    // `X = Y;` where Y is already a series produces no instruction,
    // just an alias X -> Y (resolve transitively).
    std::map<std::string, std::string> aliases;

    // Every user-defined SERIES name, in definition order. These are
    // the indicator columns of indicators_signals.csv (an aliased or
    // CSE-merged name still gets its own column).
    std::vector<std::string> output_columns;
};

// Builds the IR from a semantically-checked program. Assumes
// `sem.ok` is true -- do not call this on a program with semantic
// errors, the IR builder does not re-validate anything semantic.cpp
// already checked.
IRProgram build_ir(const SemanticResult& sem);

// Pretty-prints the IR, one instruction per line, with its domain
// tags visible -- this is Phase 3's smoke-test output.
std::string to_string(const IRProgram& ir);

// Formats a double so that it round-trips exactly (%.17g), for code
// generation and signatures.
std::string format_double(double v);

}  // namespace valpha

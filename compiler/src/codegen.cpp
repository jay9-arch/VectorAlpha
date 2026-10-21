#include "codegen.hpp"
#include <cstring>
#include <set>
#include <sstream>
#include <stdexcept>

namespace valpha {

namespace {

// ---------------------------------------------------------------------
// naming
// ---------------------------------------------------------------------

std::string resolve(const std::string& name, const OptimizedIR& opt) {
    return resolve_alias(name, opt.cse_aliases);
}

// C++-safe identifier body for a series name. User names and compiler
// temporaries live in different namespaces so "t3" (user) never
// collides with "%t3" (temporary).
std::string cpp_name(const std::string& name) {
    if (name.size() > 2 && name[0] == '%' && name[1] == 't') return "t_" + name.substr(2);
    return "u_" + name;
}

// The per-bar value variable of a series (resolved through aliases).
std::string vvar(const std::string& name, const OptimizedIR& opt) {
    return "v_" + cpp_name(resolve(name, opt));
}

std::string col_member(const std::string& resolved_name) { return "col_" + cpp_name(resolved_name); }

std::string operand_ref(const Operand& o, const OptimizedIR& opt) {
    if (o.is_const) return "L::splat(" + format_double(o.value) + ")";
    return vvar(o.name, opt);
}

std::string cpp_string_literal(const std::string& s) {
    std::ostringstream out;
    out << '"';
    for (char c : s) {
        if (c == '"' || c == '\\') out << '\\';
        out << c;
    }
    out << '"';
    return out.str();
}

std::string subst(std::string t,
                  std::initializer_list<std::pair<const char*, std::string>> repl) {
    for (const auto& kv : repl) {
        size_t pos = 0;
        const size_t len = std::strlen(kv.first);
        while ((pos = t.find(kv.first, pos)) != std::string::npos) {
            t.replace(pos, len, kv.second);
            pos += kv.second.size();
        }
    }
    return t;
}

// ---------------------------------------------------------------------
// per-instruction code templates (lane-generic: L is LaneS or Lane4)
//
// Placeholders: @R@ result variable, @A@/@B@ operand expressions,
// @S@ state-variable prefix, @W@ window as a size_t expression,
// @WN@ window as a plain number.
//
// Missing values are NaN. A windowed op outputs NaN until its window is
// full AND contains no NaN (the nan counter makes a window recover once
// the NaN leaves it); arithmetic propagates NaN naturally; division by
// zero yields NaN.
// ---------------------------------------------------------------------

const char* kSmaBlock = R"CPP(
        V @R@;
        {
            const V x = @A@;
            if (i >= @W@) {
                const V old = @S@ring[@S@pos];
                const M on = L::isnan_(old);
                @S@sum = @S@sum - L::sel(on, L::zero(), old);
                @S@nanc = @S@nanc - L::sel(on, L::one(), L::zero());
            }
            const M xn = L::isnan_(x);
            @S@sum = @S@sum + L::sel(xn, L::zero(), x);
            @S@nanc = @S@nanc + L::sel(xn, L::one(), L::zero());
            @S@ring[@S@pos] = x;
            if (++@S@pos == @W@) @S@pos = 0;
            if (i + 1 >= @W@)
                @R@ = L::sel(L::gt(@S@nanc, L::splat(0.5)), L::nan_(), @S@sum / L::splat((double)@WN@));
            else
                @R@ = L::nan_();
        }
)CPP";

const char* kSmaState = R"CPP(
    std::vector<V> @S@ring(@WN@, L::zero());
    size_t @S@pos = 0;
    V @S@sum = L::zero();
    V @S@nanc = L::zero();
)CPP";

const char* kStdevBlock = R"CPP(
        V @R@;
        {
            const V x = @A@;
            if (i >= @W@) {
                const V old = @S@ring[@S@pos];
                const M on = L::isnan_(old);
                const V od = old - @S@ref;
                @S@sum = @S@sum - L::sel(on, L::zero(), od);
                @S@sumsq = @S@sumsq - L::sel(on, L::zero(), od * od);
                @S@nanc = @S@nanc - L::sel(on, L::one(), L::zero());
            }
            const M xn = L::isnan_(x);
            // Centre on the first valid value so sums track price *changes*, not
            // price level (avoids catastrophic cancellation in sumsq - mean^2).
            @S@ref = L::sel(L::and_(L::isnan_(@S@ref), L::not_(xn)), x, @S@ref);
            const V d = x - @S@ref;
            @S@sum = @S@sum + L::sel(xn, L::zero(), d);
            @S@sumsq = @S@sumsq + L::sel(xn, L::zero(), d * d);
            @S@nanc = @S@nanc + L::sel(xn, L::one(), L::zero());
            @S@ring[@S@pos] = x;
            if (++@S@pos == @W@) @S@pos = 0;
            if (i + 1 >= @W@) {
                const V mean = @S@sum / L::splat((double)@WN@);
                V var = @S@sumsq / L::splat((double)@WN@) - mean * mean;
                var = L::sel(L::lt(var, L::zero()), L::zero(), var);
                @R@ = L::sel(L::gt(@S@nanc, L::splat(0.5)), L::nan_(), L::sqrt_(var));
            } else {
                @R@ = L::nan_();
            }
        }
)CPP";

const char* kStdevState = R"CPP(
    std::vector<V> @S@ring(@WN@, L::zero());
    size_t @S@pos = 0;
    V @S@ref = L::nan_();
    V @S@sum = L::zero();
    V @S@sumsq = L::zero();
    V @S@nanc = L::zero();
)CPP";

const char* kRsiBlock = R"CPP(
        V @R@;
        {
            const V x = @A@;
            const V dl = x - @S@prev;
            @S@prev = x;
            if (i >= 1) {
                if (i > @W@) {
                    const V og = @S@gring[@S@pos];
                    const V ol = @S@lring[@S@pos];
                    const M on = L::isnan_(og);
                    @S@gsum = @S@gsum - L::sel(on, L::zero(), og);
                    @S@lsum = @S@lsum - L::sel(on, L::zero(), ol);
                    @S@nanc = @S@nanc - L::sel(on, L::one(), L::zero());
                }
                const M dn = L::isnan_(dl);
                const V g = L::sel(L::gt(dl, L::zero()), dl, L::zero());
                const V l = L::sel(L::lt(dl, L::zero()), -dl, L::zero());
                @S@gsum = @S@gsum + L::sel(dn, L::zero(), g);
                @S@lsum = @S@lsum + L::sel(dn, L::zero(), l);
                @S@nanc = @S@nanc + L::sel(dn, L::one(), L::zero());
                @S@gring[@S@pos] = L::sel(dn, L::nan_(), g);
                @S@lring[@S@pos] = L::sel(dn, L::nan_(), l);
                if (++@S@pos == @W@) @S@pos = 0;
            }
            if (i >= @W@) {
                const V ag = @S@gsum / L::splat((double)@WN@);
                const V al = @S@lsum / L::splat((double)@WN@);
                // A "flat" window (no gains, no losses) is RSI 0; average loss ~0 with
                // gains is RSI 100. "~0" is relative to the price level so floating
                // point residue left over from add/remove cannot flip the answer.
                const V tol = L::splat(1e-9) * L::sel(L::gt(L::abs_(x), L::one()), L::abs_(x), L::one());
                const M upz = L::le(al, tol);
                const M flat = L::and_(L::le(ag, tol), upz);
                const V rs = ag / al;
                V r = L::splat(100.0) - L::splat(100.0) / (L::splat(1.0) + rs);
                r = L::sel(upz, L::sel(flat, L::zero(), L::splat(100.0)), r);
                @R@ = L::sel(L::gt(@S@nanc, L::splat(0.5)), L::nan_(), r);
            } else {
                @R@ = L::nan_();
            }
        }
)CPP";

const char* kRsiState = R"CPP(
    std::vector<V> @S@gring(@WN@, L::zero());
    std::vector<V> @S@lring(@WN@, L::zero());
    size_t @S@pos = 0;
    V @S@prev = L::nan_();
    V @S@gsum = L::zero();
    V @S@lsum = L::zero();
    V @S@nanc = L::zero();
)CPP";

const char* kEmaBlock = R"CPP(
        V @R@;
        {
            const V x = @A@;
            const M xv = L::notnan(x);
            const M seeding = L::lt(@S@cnt, L::splat((double)@WN@));
            const M add = L::and_(seeding, xv);
            @S@ssum = @S@ssum + L::sel(add, x, L::zero());
            @S@cnt = @S@cnt + L::sel(add, L::one(), L::zero());
            const M just = L::and_(add, L::ge(@S@cnt, L::splat((double)@WN@)));
            const M upd = L::and_(L::not_(seeding), xv);
            const V nxt = x * L::splat(@S@alpha) + @S@prev * L::splat(1 - @S@alpha);
            @S@prev = L::sel(just, @S@ssum / L::splat((double)@WN@), L::sel(upd, nxt, @S@prev));
            @R@ = L::sel(L::or_(just, upd), @S@prev, L::nan_());
        }
)CPP";

const char* kEmaState = R"CPP(
    const double @S@alpha = 2.0 / (@WN@ + 1);
    V @S@prev = L::nan_();
    V @S@ssum = L::zero();
    V @S@cnt = L::zero();
)CPP";

const char* kLagBlock = R"CPP(
        V @R@;
        {
            const V x = @A@;
            const V old = @S@ring[@S@pos];
            @S@ring[@S@pos] = x;
            if (++@S@pos == @W@) @S@pos = 0;
            @R@ = (i >= @W@) ? old : L::nan_();
        }
)CPP";

const char* kLagState = R"CPP(
    std::vector<V> @S@ring(@WN@, L::zero());
    size_t @S@pos = 0;
)CPP";

// Rolling max (@CMP@ = gt) / min (@CMP@ = lt). A missing value anywhere in the
// window makes the output missing, like every other windowed op.
const char* kExtremeBlock = R"CPP(
        V @R@;
        {
            const V x = @A@;
            @S@ring[@S@pos] = x;
            if (++@S@pos == @W@) @S@pos = 0;
            if (i + 1 >= @W@) {
                V m = @S@ring[0];
                M bad = L::isnan_(m);
                for (size_t k = 1; k < @W@; ++k) {
                    const V r = @S@ring[k];
                    bad = L::or_(bad, L::isnan_(r));
                    m = L::sel(L::@CMP@(r, m), r, m);
                }
                @R@ = L::sel(bad, L::nan_(), m);
            } else {
                @R@ = L::nan_();
            }
        }
)CPP";

// Emits the block computing one Series instruction into variable
// `v_<result>`, and appends its state declarations to `states`.
std::string emit_series_block(const IRInstruction& inst, const OptimizedIR& opt,
                              std::string& states) {
    std::string R = "v_" + cpp_name(inst.result);
    std::string S = "st_" + cpp_name(inst.result) + "_";
    std::string A = inst.operands.size() > 0 ? operand_ref(inst.operands[0], opt) : "";
    std::string B = inst.operands.size() > 1 ? operand_ref(inst.operands[1], opt) : "";
    std::string WN = std::to_string(inst.sliding_window.window);
    std::string W = "(size_t)" + WN;

    auto fill = [&](const char* tmpl) {
        return subst(tmpl, {{"@R@", R}, {"@A@", A}, {"@B@", B}, {"@S@", S}, {"@WN@", WN}, {"@W@", W}});
    };

    switch (inst.op) {
        case SeriesOp::ADD: return "        V " + R + " = " + A + " + " + B + ";\n";
        case SeriesOp::SUB: return "        V " + R + " = " + A + " - " + B + ";\n";
        case SeriesOp::MUL: return "        V " + R + " = " + A + " * " + B + ";\n";
        case SeriesOp::DIV:
            return "        V " + R + " = L::sel(L::eq(" + B + ", L::zero()), L::nan_(), " + A + " / " + B + ");\n";
        case SeriesOp::NEG: return "        V " + R + " = -" + A + ";\n";
        case SeriesOp::ABS: return "        V " + R + " = L::abs_(" + A + ");\n";
        case SeriesOp::LAG: states += fill(kLagState); return fill(kLagBlock);
        case SeriesOp::HIGHEST:
            states += fill(kLagState);
            return subst(fill(kExtremeBlock), {{"@CMP@", "gt"}});
        case SeriesOp::LOWEST:
            states += fill(kLagState);
            return subst(fill(kExtremeBlock), {{"@CMP@", "lt"}});
        case SeriesOp::SMA: states += fill(kSmaState); return fill(kSmaBlock);
        case SeriesOp::EMA: states += fill(kEmaState); return fill(kEmaBlock);
        case SeriesOp::RSI: states += fill(kRsiState); return fill(kRsiBlock);
        case SeriesOp::STDEV: states += fill(kStdevState); return fill(kStdevBlock);
    }
    throw std::runtime_error("codegen: unhandled series op");
}

// ---------------------------------------------------------------------
// rule conditions
// ---------------------------------------------------------------------

const char* cmp_fn(BinOp op) {
    switch (op) {
        case BinOp::LT: return "lt";
        case BinOp::GT: return "gt";
        case BinOp::LE: return "le";
        case BinOp::GE: return "ge";
        case BinOp::EQ: return "eq";
        case BinOp::NE: return "ne";
        default: break;
    }
    throw std::runtime_error("codegen: unsupported comparison operator");
}

// Condition leaves are plain Identifier / NumberLiteral nodes (IR guarantee).
std::string leaf_ref(const ExprPtr& e, const OptimizedIR& opt) {
    if (e->kind == ExprKind::NumberLiteral) return "L::splat(" + format_double(e->number) + ")";
    if (e->kind == ExprKind::Identifier) return vvar(e->name, opt);
    throw std::runtime_error("codegen: unexpected node in lowered condition: " + to_string(e));
}

struct CondEmitter {
    const OptimizedIR& opt;
    std::string states;  // crossover prev-value state declarations
    std::string pre;     // statements that must run every bar (crossover updates)
    int crossings = 0;

    explicit CondEmitter(const OptimizedIR& o) : opt(o) {}

    // Returns a C++ expression of mask type M. Crossovers become
    // pre-statements (not short-circuited: their prev-value state must
    // advance on every bar even when an AND/OR would skip them).
    std::string emit(const ExprPtr& e) {
        switch (e->kind) {
            case ExprKind::Comparison:
                return std::string("Cmp<L>::") + cmp_fn(e->op) + "(" + leaf_ref(e->left, opt) + ", " +
                       leaf_ref(e->right, opt) + ")";
            case ExprKind::LogicalAnd:
                return "L::and_(" + emit(e->left) + ", " + emit(e->right) + ")";
            case ExprKind::LogicalOr:
                return "L::or_(" + emit(e->left) + ", " + emit(e->right) + ")";
            case ExprKind::FunctionCall: {
                if ((e->name != "CROSSOVER" && e->name != "CROSSUNDER") || e->args.size() != 2) {
                    throw std::runtime_error("codegen: '" + e->name +
                                              "' cannot appear inside a rule condition");
                }
                std::string k = std::to_string(crossings++);
                std::string a = leaf_ref(e->args[0], opt);
                std::string b = leaf_ref(e->args[1], opt);
                bool over = (e->name == "CROSSOVER");
                states += "    V st_cx" + k + "_pa = L::nan_();\n    V st_cx" + k + "_pb = L::nan_();\n";
                pre += "        const M cx" + k + " = L::and_(Cmp<L>::" + (over ? "le" : "ge") +
                       "(st_cx" + k + "_pa, st_cx" + k + "_pb), Cmp<L>::" + (over ? "gt" : "lt") + "(" +
                       a + ", " + b + "));\n";
                pre += "        st_cx" + k + "_pa = " + a + ";\n        st_cx" + k + "_pb = " + b + ";\n";
                return "cx" + k;
            }
            default:
                break;
        }
        throw std::runtime_error("codegen: unsupported node in rule condition: " + to_string(e));
    }
};

// ---------------------------------------------------------------------
// static program text
// ---------------------------------------------------------------------

const char* kHeader = R"CPP(
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace fs = std::filesystem;

// ---------- lanes: the kernel below is written once against this interface ----------
// LaneS: one ticker per "register" (plain doubles). Lane4: four tickers per register,
// GCC/Clang vector extensions (lowered to AVX2/SSE/NEON by the compiler). Both do the
// same IEEE operations per lane, so their results are bit-identical.

struct LaneS {
    using V = double;
    using M = bool;
    static constexpr int N = 1;
    static V splat(double x) { return x; }
    static V zero() { return 0.0; }
    static V one() { return 1.0; }
    static V nan_() { return std::numeric_limits<double>::quiet_NaN(); }
    static M isnan_(V x) { return x != x; }
    static M notnan(V x) { return x == x; }
    static V sel(M m, V a, V b) { return m ? a : b; }
    static M and_(M a, M b) { return a && b; }
    static M or_(M a, M b) { return a || b; }
    static M not_(M a) { return !a; }
    static M gt(V a, V b) { return a > b; }
    static M lt(V a, V b) { return a < b; }
    static M le(V a, V b) { return a <= b; }
    static M ge(V a, V b) { return a >= b; }
    static M eq(V a, V b) { return a == b; }
    static V abs_(V x) { return x < 0 ? -x : x; }
    static V sqrt_(V x) { return std::sqrt(x); }
    static void set(V& v, int, double x) { v = x; }
    static double get(V v, int) { return v; }
    static bool getm(M m, int) { return m; }
};

typedef double v4d __attribute__((vector_size(32)));
typedef long long v4m __attribute__((vector_size(32)));

struct Lane4 {
    using V = v4d;
    using M = v4m;
    static constexpr int N = 4;
    static V splat(double x) { return V{x, x, x, x}; }
    static V zero() { return splat(0.0); }
    static V one() { return splat(1.0); }
    static V nan_() { return splat(std::numeric_limits<double>::quiet_NaN()); }
    static M isnan_(V x) { return (M)(x != x); }
    static M notnan(V x) { return (M)(x == x); }
    static V sel(M m, V a, V b) { return (V)((m & (M)a) | (~m & (M)b)); }
    static M and_(M a, M b) { return a & b; }
    static M or_(M a, M b) { return a | b; }
    static M not_(M a) { return ~a; }
    static M gt(V a, V b) { return (M)(a > b); }
    static M lt(V a, V b) { return (M)(a < b); }
    static M le(V a, V b) { return (M)(a <= b); }
    static M ge(V a, V b) { return (M)(a >= b); }
    static M eq(V a, V b) { return (M)(a == b); }
    static V abs_(V x) { return sel(lt(x, zero()), -x, x); }
    static V sqrt_(V x) {
        V r;
        for (int l = 0; l < 4; ++l) r[l] = std::sqrt(x[l]);
        return r;
    }
    static void set(V& v, int l, double x) { v[l] = x; }
    static double get(V v, int l) { return v[l]; }
    static bool getm(M m, int l) { return m[l] != 0; }
};

// Tolerant comparisons, identical in all three engines: tolerance is
// 1e-9 * max(1, |a|, |b|) (relative for large values, an absolute floor near
// zero). A tie in exact arithmetic must be a tie here even when incremental
// and recomputed indicators differ in the last bits -- e.g. a running sum
// that returns to "0" can be left at -1e-15. Anything involving NaN
// (a missing value) is false.
template <class L>
struct Cmp {
    using V = typename L::V;
    using M = typename L::M;
    static V tol(V a, V b) {
        V aa = L::abs_(a), bb = L::abs_(b);
        V m = L::sel(L::gt(aa, bb), aa, bb);
        return L::splat(1e-9) * L::sel(L::gt(m, L::one()), m, L::one());  // 1e-9 * max(1, |a|, |b|)
    }
    static M lt(V a, V b) { return L::gt(b - a, tol(a, b)); }
    static M gt(V a, V b) { return L::gt(a - b, tol(a, b)); }
    static M le(V a, V b) { return L::le(a - b, tol(a, b)); }
    static M ge(V a, V b) { return L::le(b - a, tol(a, b)); }
    static M eq(V a, V b) { return L::le(L::abs_(a - b), tol(a, b)); }
    static M ne(V a, V b) { return L::gt(L::abs_(a - b), tol(a, b)); }
};

static std::string fmt(double v, int decimals) {
    if (std::isnan(v)) return "---";
    std::ostringstream s;
    s.setf(std::ios::fixed);
    s.precision(decimals);
    s << v;
    return s.str();
}

// ---------- CSV loading ----------

struct RawSeries {
    std::string ticker;
    std::vector<std::string> dates;
    std::vector<double> close;
};

static RawSeries load_csv(const std::string& path, const std::string& ticker) {
    RawSeries rs;
    rs.ticker = ticker;
    std::ifstream in(path);
    std::string line;
    std::getline(in, line);  // header
    if (!line.empty() && line.back() == '\r') line.pop_back();
    std::vector<std::string> header;
    {
        std::istringstream hs(line);
        std::string col;
        while (std::getline(hs, col, ',')) header.push_back(col);
    }
    int date_col = -1, close_col = -1;
    for (size_t i = 0; i < header.size(); ++i) {
        if (header[i] == "Date") date_col = (int)i;
        if (header[i] == "Close") close_col = (int)i;
    }
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        std::vector<std::string> cols;
        std::istringstream ls(line);
        std::string cell;
        while (std::getline(ls, cell, ',')) cols.push_back(cell);
        if (date_col < 0 || close_col < 0 || (size_t)date_col >= cols.size() ||
            (size_t)close_col >= cols.size())
            continue;
        try {
            double c = std::stod(cols[close_col]);
            if (std::isnan(c)) continue;
            std::string d = cols[date_col].substr(0, 10);
            rs.dates.push_back(d);
            rs.close.push_back(c);
        } catch (...) {
            continue;
        }
    }
    return rs;
}

static std::vector<std::string> list_tickers(const std::string& dir) {
    std::vector<std::string> out;
    for (const auto& entry : fs::directory_iterator(dir)) {
        if (entry.path().extension() == ".csv") {
            out.push_back(entry.path().stem().string());
        }
    }
    std::sort(out.begin(), out.end());
    return out;
}

)CPP";

// The generated driver: batching, threading, simulation, output.
// Placeholders are filled by generate_cpp.
const char* kMainTop = R"CPP(
template <class L>
static void process_batch(const RawSeries* const* rs, TickerResult* const* res) {
    using V = typename L::V;
    using M = typename L::M;
    const size_t n = rs[0]->close.size();
    std::vector<V> close_in(n);
    for (size_t i = 0; i < n; ++i) {
        V v{};
        for (int l = 0; l < L::N; ++l) L::set(v, l, rs[l]->close[i]);
        close_in[i] = v;
    }
    // One reusable buffer set per thread: the kernel overwrites every element, so
    // there is no need to allocate and zero-fill fresh vectors for every batch.
    thread_local KernelOut<L> out;
    out.init(n);
    run_kernel<L>(close_in.data(), n, out);
    for (int l = 0; l < L::N; ++l) {
        TickerResult& r = *res[l];
        r.src = rs[l];
        r.buy.assign(n, 0);
        r.sell.assign(n, 0);
@SCATTER_INIT@
        for (size_t i = 0; i < n; ++i) {
            r.buy[i] = L::getm(out.buy[i], l) ? 1 : 0;
            r.sell[i] = L::getm(out.sell[i], l) ? 1 : 0;
@SCATTER_LOOP@
        }
    }
    (void)sizeof(M);
}

struct Task {
    size_t idx[4];
    int count;
};

int main(int argc, char** argv) {
    std::vector<std::string> pos;
    unsigned threads_opt = 0;
    bool force_scalar = false;
    for (int a = 1; a < argc; ++a) {
        std::string s = argv[a];
        if (s == "--scalar") force_scalar = true;
        else if (s == "--threads" && a + 1 < argc) threads_opt = (unsigned)std::atoi(argv[++a]);
        else if (s.rfind("--threads=", 0) == 0) threads_opt = (unsigned)std::atoi(s.c_str() + 10);
        else pos.push_back(s);
    }
    if (pos.size() != 2) {
        std::cerr << "usage: " << argv[0] << " <data_dir> <results_dir> [--threads N] [--scalar]\n";
        return 1;
    }
    std::string data_dir = pos[0];
    std::string results_dir = pos[1];
    fs::create_directories(results_dir);

@CONFIG@
    if (universe.empty()) universe = list_tickers(data_dir);

    // --- load CSVs (untimed -- matches benchmark.py timing only the
    //     Python engines' indicator/signal/simulation work, not I/O) ---
    std::vector<RawSeries> raw;
    for (const auto& tkr : universe) {
        std::string path = data_dir + "/" + tkr + ".csv";
        if (!fs::exists(path)) continue;
        RawSeries rs = load_csv(path, tkr);
        if (!rs.close.empty()) raw.push_back(std::move(rs));
    }
    if (raw.empty()) {
        std::cerr << "No data found in " << data_dir << "\n";
        return 1;
    }

    auto t0 = std::chrono::steady_clock::now();

    // --- group tickers that share an identical trading calendar; each group is
    //     cut into batches of 4 SIMD lanes, leftovers run one scalar lane ---
    std::vector<std::vector<size_t>> groups;
    for (size_t idx = 0; idx < raw.size(); ++idx) {
        bool placed = false;
        for (auto& g : groups) {
            if (raw[g[0]].dates == raw[idx].dates) { g.push_back(idx); placed = true; break; }
        }
        if (!placed) groups.push_back({idx});
    }
    const bool allow_simd = kSimdOk && !force_scalar;
    std::vector<Task> tasks;
    size_t simd_batches = 0, scalar_tasks = 0;
    for (const auto& g : groups) {
        size_t k = 0;
        if (allow_simd) {
            for (; k + 4 <= g.size(); k += 4) {
                tasks.push_back(Task{{g[k], g[k + 1], g[k + 2], g[k + 3]}, 4});
                simd_batches++;
            }
        }
        for (; k < g.size(); ++k) {
            tasks.push_back(Task{{g[k], 0, 0, 0}, 1});
            scalar_tasks++;
        }
    }

    // --- indicator + signal computation, tasks spread across threads
    //     (cross_sectional=true on every instruction, per Phase 3's IR, is
    //     exactly what licenses computing tickers independently like this) ---
    std::vector<TickerResult> results(raw.size());
    unsigned hw = threads_opt ? threads_opt : std::thread::hardware_concurrency();
    if (hw == 0) hw = 1;
    size_t num_threads = std::min<size_t>(hw, tasks.size());
    std::atomic<size_t> next_task{0};
    auto worker = [&]() {
        for (;;) {
            size_t t = next_task.fetch_add(1);
            if (t >= tasks.size()) break;
            const Task& task = tasks[t];
@DISPATCH@
        }
    };
    std::vector<std::thread> workers;
    for (size_t t = 1; t < num_threads; ++t) workers.emplace_back(worker);
    worker();
    for (auto& w : workers) w.join();

    auto t1 = std::chrono::steady_clock::now();

    // --- sequential single-position portfolio simulation (path-dependent,
    //     kept single-threaded on purpose -- same rationale as the Python
    //     engines). Dense date->row tables instead of maps. ---
    const size_t T = results.size();
    std::vector<std::string> all_dates;
    for (const auto& g : groups) {  // one representative per calendar is enough
        const auto& d = raw[g[0]].dates;
        all_dates.insert(all_dates.end(), d.begin(), d.end());
    }
    std::sort(all_dates.begin(), all_dates.end());
    all_dates.erase(std::unique(all_dates.begin(), all_dates.end()), all_dates.end());
    const size_t D = all_dates.size();

    std::vector<int> row_of(T * D, -1);  // date-major: one date's lookups are contiguous
    // Tickers sharing a calendar share one date->row mapping: compute it once per
    // group (string comparisons are the expensive part), then fan it out.
    for (const auto& grp : groups) {
        const auto& d = raw[grp[0]].dates;
        std::vector<int> rows(D, -1);
        const bool sorted_dates = std::is_sorted(d.begin(), d.end());
        size_t g = 0;
        for (size_t i = 0; i < d.size(); ++i) {
            if (sorted_dates) {
                while (g < D && all_dates[g] < d[i]) ++g;
            } else {
                g = (size_t)(std::lower_bound(all_dates.begin(), all_dates.end(), d[i]) - all_dates.begin());
            }
            rows[g] = (int)i;  // a later duplicate date overrides an earlier one
        }
        for (size_t t : grp) {
            for (size_t gg = 0; gg < D; ++gg) row_of[gg * T + t] = rows[gg];
        }
    }

    struct OpenPos { std::string entry_date; double entry_px; double shares; };
    std::vector<char> is_open(T, 0);
    std::vector<OpenPos> open_pos(T);
    std::vector<size_t> open_list;  // insertion order, like the Python engines' dict
    double cash = INITIAL_CAPITAL;

    struct Trade {
        std::string ticker, entry_date, exit_date;
        double entry_px, exit_px, shares, pnl, pnl_pct;
    };
    std::vector<Trade> trades;
    std::vector<double> equity_curve;
    equity_curve.reserve(D);

    for (size_t g = 0; g < D; ++g) {
        const std::string& dt = all_dates[g];
        for (size_t t = 0; t < T; ++t) {
            int i = row_of[g * T + t];
            if (i < 0) continue;
            const TickerResult& r = results[t];
            double price = r.close()[i];
            if (!is_open[t] && r.buy[i]) {
                double trade_capital = ALLOCATION * INITIAL_CAPITAL;
                double shares = trade_capital / price;
                open_pos[t] = OpenPos{dt, price, shares};
                is_open[t] = 1;
                open_list.push_back(t);
                cash -= trade_capital;
            } else if (is_open[t] && r.sell[i]) {
                OpenPos pos = open_pos[t];
                is_open[t] = 0;
                open_list.erase(std::find(open_list.begin(), open_list.end(), t));
                double proceeds = pos.shares * price;
                cash += proceeds;
                double cost = pos.shares * pos.entry_px;
                double pnl = proceeds - cost;
                double pnl_pct = pnl / cost * 100.0;
                trades.push_back(Trade{r.ticker(), pos.entry_date, dt, pos.entry_px, price,
                                        pos.shares, pnl, pnl_pct});
            }
        }
        double mtm = cash;
        for (size_t t : open_list) {
            int i = row_of[g * T + t];
            double price = (i >= 0) ? results[t].close()[i] : open_pos[t].entry_px;
            mtm += open_pos[t].shares * price;
        }
        equity_curve.push_back(mtm);
    }
    double final_value = equity_curve.empty() ? INITIAL_CAPITAL : equity_curve.back();

    auto t2 = std::chrono::steady_clock::now();
    double compute_s = std::chrono::duration<double>(t1 - t0).count();
    double sim_s = std::chrono::duration<double>(t2 - t1).count();
    double elapsed = compute_s + sim_s;

    // --- write output artifacts (untimed, same as the Python engines) ---
)CPP";

const char* kMainBottom = R"CPP(
    {
        std::ofstream f(results_dir + "/trades.csv");
        f << "ticker,entry_date,entry_px,exit_date,exit_px,shares,pnl,pnl_%\n";
        for (auto& t : trades) {
            f << t.ticker << "," << t.entry_date << "," << fmt(t.entry_px, 2) << ","
              << t.exit_date << "," << fmt(t.exit_px, 2) << "," << fmt(t.shares, 4) << ","
              << fmt(t.pnl, 2) << "," << fmt(t.pnl_pct, 2) << "\n";
        }
    }

    {
        std::ofstream f(results_dir + "/equity_curve.csv");
        f << "date,equity\n";
        for (size_t i = 0; i < all_dates.size(); ++i) {
            f << all_dates[i] << "," << fmt(equity_curve[i], 2) << "\n";
        }
    }

    {
        double peak = -1e300, worst = 0.0;
        for (double e : equity_curve) {
            if (e > peak) peak = e;
            double dd = peak > 0 ? (e - peak) / peak * 100.0 : 0.0;
            if (dd < worst) worst = dd;
        }
        int wins = 0;
        for (auto& t : trades) if (t.pnl > 0) wins++;
        double win_rate = trades.empty() ? 0.0 : (double)wins / trades.size() * 100.0;
        double total_return = (final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100.0;

        std::ofstream f(results_dir + "/summary.txt");
        f << "Initial capital : " << fmt(INITIAL_CAPITAL, 2) << "\n";
        f << "Final portfolio value : " << fmt(final_value, 2) << "\n";
        f << "Total return : " << fmt(total_return, 2) << "%\n";
        f << "Max drawdown : " << fmt(worst, 2) << "%\n";
        f << "Closed trades : " << trades.size() << "\n";
        f << "Win rate : " << fmt(win_rate, 2) << "%\n";
    }

    std::cout << "[compiled] " << raw.size() << " tickers, " << trades.size() << " trades, "
              << elapsed << "s (compute " << compute_s << "s, simulate " << sim_s << "s; "
              << simd_batches << " SIMD batch(es) of 4, " << scalar_tasks << " scalar, "
              << num_threads << " thread(s))\n";
    std::cout << "COMPUTE_SECONDS=" << compute_s << "\n";
    std::cout << "SIM_SECONDS=" << sim_s << "\n";
    std::cout << "ELAPSED_SECONDS=" << elapsed << "\n";
    return 0;
}
)CPP";

}  // namespace

std::string generate_cpp(const OptimizedIR& opt, const std::string& strategy_name,
                         const CodegenOptions& options) {
    std::ostringstream src;

    src << "// Auto-generated by valphac (Phase 5 codegen) -- DO NOT EDIT BY HAND.\n";
    src << "// Strategy: " << strategy_name << "\n";
    src << "// Mode: " << (options.fuse ? "fused bar loop + SIMD batches" : "unfused (one pass per instruction), scalar") << "\n";
    src << "// Regenerate from the .valpha source instead of editing this file.\n";
    src << kHeader;
    src << "constexpr bool kSimdOk = " << (options.fuse ? "true" : "false") << ";\n\n";

    // ---------- per-ticker result + kernel output structs ----------
    src << "struct TickerResult {\n";
    src << "    const RawSeries* src = nullptr;  // the loaded ticker data (not copied)\n";
    src << "    const std::string& ticker() const { return src->ticker; }\n";
    src << "    const std::vector<std::string>& dates() const { return src->dates; }\n";
    src << "    const std::vector<double>& close() const { return src->close; }\n";
    for (const auto& c : opt.stored_columns) src << "    std::vector<double> " << col_member(c) << ";\n";
    src << "    std::vector<char> buy;\n";
    src << "    std::vector<char> sell;\n";
    src << "};\n\n";

    src << "template <class L>\nstruct KernelOut {\n";
    src << "    using V = typename L::V;\n    using M = typename L::M;\n";
    for (const auto& c : opt.stored_columns) src << "    std::vector<V> " << col_member(c) << ";\n";
    src << "    std::vector<M> buy, sell;\n";
    src << "    void init(size_t n) {\n";
    for (const auto& c : opt.stored_columns) src << "        " << col_member(c) << ".resize(n);\n";
    src << "        buy.resize(n);\n        sell.resize(n);\n    }\n};\n\n";

    // ---------- the kernel ----------
    std::string states;
    std::vector<const IRInstruction*> series;
    std::vector<const IRInstruction*> rules;
    for (const auto& inst : opt.ir.instructions) {
        if (inst.kind == IRKind::Series) series.push_back(&inst);
        else if (inst.kind == IRKind::Rule) rules.push_back(&inst);
    }
    std::set<std::string> stored(opt.stored_columns.begin(), opt.stored_columns.end());

    // rules: the condition code is identical in both modes
    CondEmitter cond(opt);
    std::string rule_code = "        M buy_m{};\n        M sell_m{};\n";
    for (const IRInstruction* r : rules) {
        std::string expr = cond.emit(r->condition);
        rule_code += (r->rule_kind == RuleKind::BUY) ? "        buy_m = L::or_(buy_m, " + expr + ");\n"
                                                      : "        sell_m = L::or_(sell_m, " + expr + ");\n";
    }
    rule_code = cond.pre + rule_code;  // crossover statements first (they define cxN)
    // (cond.pre already declares cxN before the or_ lines that use them)
    rule_code += "        out.buy[i] = buy_m;\n        out.sell[i] = sell_m;\n";

    std::vector<std::string> blocks;
    for (const IRInstruction* inst : series) blocks.push_back(emit_series_block(*inst, opt, states));
    states += cond.states;

    src << "template <class L>\n";
    src << "static void run_kernel(const typename L::V* close_in, size_t n, KernelOut<L>& out) {\n";
    src << "    using V = typename L::V;\n    using M = typename L::M;\n    (void)sizeof(M);\n";

    if (options.fuse) {
        src << states;
        src << "    for (size_t i = 0; i < n; ++i) {\n";
        src << "        const V v_u_close = close_in[i];\n";
        for (size_t k = 0; k < series.size(); ++k) {
            src << blocks[k];
            if (stored.count(series[k]->result))
                src << "        out." << col_member(series[k]->result) << "[i] = v_" << cpp_name(series[k]->result) << ";\n";
        }
        src << rule_code;
        src << "    }\n";
    } else {
        src << "    static_assert(L::N == 1, \"unfused mode is scalar only\");\n";
        src << states;
        for (const IRInstruction* inst : series) src << "    std::vector<V> a_" << cpp_name(inst->result) << "(n);\n";
        for (size_t k = 0; k < series.size(); ++k) {
            const IRInstruction* inst = series[k];
            src << "    for (size_t i = 0; i < n; ++i) {\n";
            std::set<std::string> loaded;
            for (const auto& o : inst->operands) {
                if (o.is_const) continue;
                std::string rn = resolve(o.name, opt);
                if (!loaded.insert(rn).second) continue;
                if (rn == "close") src << "        const V v_u_close = close_in[i];\n";
                else src << "        const V v_" << cpp_name(rn) << " = a_" << cpp_name(rn) << "[i];\n";
            }
            src << blocks[k];
            src << "        a_" << cpp_name(inst->result) << "[i] = v_" << cpp_name(inst->result) << ";\n";
            if (stored.count(inst->result))
                src << "        out." << col_member(inst->result) << "[i] = v_" << cpp_name(inst->result) << ";\n";
            src << "    }\n";
        }
        src << "    for (size_t i = 0; i < n; ++i) {\n";
        std::set<std::string> loaded;
        for (const IRInstruction* r : rules) {
            for (const auto& d : r->condition_deps) {
                std::string rn = resolve(d, opt);
                if (!loaded.insert(rn).second) continue;
                if (rn == "close") src << "        const V v_u_close = close_in[i];\n";
                else src << "        const V v_" << cpp_name(rn) << " = a_" << cpp_name(rn) << "[i];\n";
            }
        }
        src << rule_code;
        src << "    }\n";
    }
    src << "}\n\n";

    // ---------- driver ----------
    std::string scatter_init, scatter_loop;
    for (const auto& c : opt.stored_columns) {
        scatter_init += "        r." + col_member(c) + ".assign(n, 0.0);\n";
        scatter_loop += "            r." + col_member(c) + "[i] = L::get(out." + col_member(c) + "[i], l);\n";
    }

    std::ostringstream config;
    config << "    const double INITIAL_CAPITAL = " << format_double(opt.ir.initial_capital) << ";\n";
    config << "    const double ALLOCATION = " << format_double(opt.ir.allocation) << ";\n\n";
    config << "    std::vector<std::string> universe = {";
    for (size_t i = 0; i < opt.ir.universe.size(); ++i) {
        if (i) config << ", ";
        config << cpp_string_literal(opt.ir.universe[i]);
    }
    config << "};";

    std::string dispatch;
    if (options.fuse) {
        dispatch =
            "            if (task.count == 4) {\n"
            "                const RawSeries* rs[4] = {&raw[task.idx[0]], &raw[task.idx[1]], &raw[task.idx[2]], &raw[task.idx[3]]};\n"
            "                TickerResult* res[4] = {&results[task.idx[0]], &results[task.idx[1]], &results[task.idx[2]], &results[task.idx[3]]};\n"
            "                process_batch<Lane4>(rs, res);\n"
            "            } else {\n"
            "                const RawSeries* rs[1] = {&raw[task.idx[0]]};\n"
            "                TickerResult* res[1] = {&results[task.idx[0]]};\n"
            "                process_batch<LaneS>(rs, res);\n"
            "            }";
    } else {
        dispatch =
            "            {\n"
            "                const RawSeries* rs[1] = {&raw[task.idx[0]]};\n"
            "                TickerResult* res[1] = {&results[task.idx[0]]};\n"
            "                process_batch<LaneS>(rs, res);\n"
            "            }";
    }

    src << subst(kMainTop, {{"@SCATTER_INIT@", scatter_init},
                            {"@SCATTER_LOOP@", scatter_loop},
                            {"@CONFIG@", config.str()},
                            {"@DISPATCH@", dispatch}});

    // indicators_signals.csv (needs the column list, so generated here)
    src << "    {\n        std::ofstream f(results_dir + \"/indicators_signals.csv\");\n";
    src << "        f << \"ticker,date,close\"";
    for (const auto& c : opt.ir.output_columns) src << " << \",\" << " << cpp_string_literal(c);
    src << " << \",signal\\n\";\n";
    src << "        for (auto& r : results) {\n            for (size_t i = 0; i < r.dates().size(); ++i) {\n";
    src << "                f << r.ticker() << \",\" << r.dates()[i] << \",\" << fmt(r.close()[i], 2);\n";
    for (const auto& c : opt.ir.output_columns) {
        std::string target = resolve(c, opt);
        if (target == "close") src << "                f << \",\" << fmt(r.close()[i], 4);\n";
        else src << "                f << \",\" << fmt(r." << col_member(target) << "[i], 4);\n";
    }
    src << "                std::string sig = \"---\";\n";
    src << "                if (r.buy[i]) sig = \"BUY\"; else if (r.sell[i]) sig = \"SELL\";\n";
    src << "                f << \",\" << sig << \"\\n\";\n            }\n        }\n    }\n";

    src << kMainBottom;
    return src.str();
}

}  // namespace valpha

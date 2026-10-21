"""
valpha_lang.py
---------------
Python front-end for the .valpha DSL: lexer, recursive-descent parser and
semantic checker, mirroring compiler/src/{lexer,parser,semantic}.cpp
(grammar documented in compiler/include/parser.hpp). It exists so the naive
and pandas/NumPy engines read the *same* strategy file the compiled engine
does -- one source of truth, no hand-copied strategy dicts.

    strategy = parse_file("compiler/strategies/crossover_rsi.valpha")

AST nodes are plain tuples:
    ("num", float)                  ("id", name)
    ("call", name, [args])          ("set", [names])
    ("neg", expr)                   ("bin", op, left, right)
where op is one of  + - * /  < > <= >= == !=  AND  OR.

This module has no pandas/NumPy dependency (the naive engine must stay pure
Python).
"""

import os
import re

KEYWORDS = {"BUY", "SELL", "WHEN", "AND", "OR"}
SERIES_FUNCS = {"SMA", "EMA", "RSI", "STDEV",       # (series, constant window) -> series
                "LAG", "HIGHEST", "LOWEST"}         #   LAG: x[i-n]; HIGHEST/LOWEST: rolling max/min
UNARY_SERIES_FUNCS = {"ABS"}                        # (series) -> series
CROSS_FUNCS = {"CROSSOVER", "CROSSUNDER"}          # (numeric, numeric) -> bool
CMP_OPS = {"<", ">", "<=", ">=", "==", "!="}

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|==|!=|[=<>;(){},+\-*/])
    """,
    re.VERBOSE,
)


class ValphaError(Exception):
    """Lex, parse or semantic error in a .valpha program."""


# ---------------------------------------------------------------------
# lexer
# ---------------------------------------------------------------------

def tokenize(source):
    """Returns a list of (kind, text, line, col); kind in NUM/IDENT/KW/OP/EOF."""
    tokens = []
    pos, line, line_start = 0, 1, 0
    while pos < len(source):
        m = _TOKEN_RE.match(source, pos)
        if not m:
            raise ValphaError(f"{line}:{pos - line_start + 1}: unexpected character {source[pos]!r}")
        kind, text = m.lastgroup, m.group()
        col = pos - line_start + 1
        if kind == "num":
            tokens.append(("NUM", text, line, col))
        elif kind == "ident":
            tokens.append(("KW" if text in KEYWORDS else "IDENT", text, line, col))
        elif kind == "op":
            tokens.append(("OP", text, line, col))
        nl = text.count("\n")
        if nl:
            line += nl
            line_start = pos + text.rfind("\n") + 1
        pos = m.end()
    tokens.append(("EOF", "", line, pos - line_start + 1))
    return tokens


# ---------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.pos = 0

    def cur(self):
        return self.toks[self.pos]

    def is_op(self, text):
        k, t, _, _ = self.cur()
        return k == "OP" and t == text

    def is_kw(self, text):
        k, t, _, _ = self.cur()
        return k == "KW" and t == text

    def advance(self):
        tok = self.toks[self.pos]
        if self.pos + 1 < len(self.toks):
            self.pos += 1
        return tok

    def fail(self, msg):
        _, _, line, col = self.cur()
        raise ValphaError(f"{line}:{col}: parse error: {msg}")

    def expect_op(self, text):
        if not self.is_op(text):
            self.fail(f"expected '{text}' but found '{self.cur()[1]}'")
        return self.advance()

    def expect_kind(self, kind, what):
        if self.cur()[0] != kind:
            self.fail(f"expected {what} but found '{self.cur()[1]}'")
        return self.advance()

    def program(self):
        stmts = []
        while self.cur()[0] != "EOF":
            stmts.append(self.statement())
        return stmts

    def statement(self):
        if self.is_kw("BUY") or self.is_kw("SELL"):
            kind = self.advance()[1]
            if not self.is_kw("WHEN"):
                self.fail("expected 'WHEN'")
            self.advance()
            cond = self.expr()
            self.expect_op(";")
            return ("rule", kind, cond)
        if self.cur()[0] == "IDENT":
            name = self.advance()[1]
            self.expect_op("=")
            value = self.expr()
            self.expect_op(";")
            return ("assign", name, value)
        self.fail("expected an assignment (NAME = ...;) or a BUY/SELL WHEN rule")

    def expr(self):
        return self.or_expr()

    def or_expr(self):
        left = self.and_expr()
        while self.is_kw("OR"):
            self.advance()
            left = ("bin", "OR", left, self.and_expr())
        return left

    def and_expr(self):
        left = self.comparison()
        while self.is_kw("AND"):
            self.advance()
            left = ("bin", "AND", left, self.comparison())
        return left

    def comparison(self):
        left = self.additive()
        k, t, _, _ = self.cur()
        if k == "OP" and t in CMP_OPS:
            self.advance()
            return ("bin", t, left, self.additive())
        return left

    def additive(self):
        left = self.term()
        while self.is_op("+") or self.is_op("-"):
            op = self.advance()[1]
            left = ("bin", op, left, self.term())
        return left

    def term(self):
        left = self.unary()
        while self.is_op("*") or self.is_op("/"):
            op = self.advance()[1]
            left = ("bin", op, left, self.unary())
        return left

    def unary(self):
        if self.is_op("-"):
            self.advance()
            return ("neg", self.unary())
        return self.primary()

    def primary(self):
        k, t, _, _ = self.cur()
        if k == "NUM":
            self.advance()
            return ("num", float(t))
        if self.is_op("{"):
            self.advance()
            items = []
            if not self.is_op("}"):
                items.append(self.expect_kind("IDENT", "an identifier inside '{ }'")[1])
                while self.is_op(","):
                    self.advance()
                    items.append(self.expect_kind("IDENT", "an identifier inside '{ }'")[1])
            self.expect_op("}")
            return ("set", items)
        if self.is_op("("):
            self.advance()
            inner = self.expr()
            self.expect_op(")")
            return inner
        if k == "IDENT":
            self.advance()
            if self.is_op("("):
                self.advance()
                args = []
                if not self.is_op(")"):
                    args.append(self.expr())
                    while self.is_op(","):
                        self.advance()
                        args.append(self.expr())
                self.expect_op(")")
                return ("call", t, args)
            return ("id", t)
        self.fail("expected a number, identifier, function call, '( ... )' group, "
                  f"or '{{ ... }}' set literal, found '{t}'")


def to_text(e):
    """Fully parenthesised rendering, same shape as the C++ AST printer."""
    kind = e[0]
    if kind == "num":
        return f"{e[1]:.12g}"
    if kind == "id":
        return e[1]
    if kind == "call":
        return f"{e[1]}(" + ", ".join(to_text(a) for a in e[2]) + ")"
    if kind == "set":
        return "{ " + ", ".join(e[1]) + " }"
    if kind == "neg":
        return f"(-{to_text(e[1])})"
    return f"({to_text(e[2])} {e[1]} {to_text(e[3])})"


# ---------------------------------------------------------------------
# semantic analysis
# ---------------------------------------------------------------------

class Strategy:
    """A checked .valpha program, ready for the Python evaluators."""

    def __init__(self):
        self.name = ""
        self.description = ""
        self.source_path = ""
        self.initial_capital = 0.0
        self.allocation = 0.0
        self.universe = []
        self.series_defs = []      # [(name, expr)] SERIES definitions, in order
        self.output_columns = []   # names of series_defs (indicators_signals.csv columns)
        self.consts = {}           # user scalar constants: name -> expr
        self.bools = {}            # named booleans: name -> expr
        self.rules = []            # [("BUY"|"SELL", expr)]


def const_eval(e, consts):
    """Constant-folds a scalar expression; None if it is not a compile-time constant."""
    kind = e[0]
    if kind == "num":
        return e[1]
    if kind == "id":
        return const_eval(consts[e[1]], consts) if e[1] in consts else None
    if kind == "neg":
        v = const_eval(e[1], consts)
        return None if v is None else -v
    if kind == "bin" and e[1] in ("+", "-", "*", "/"):
        a, b = const_eval(e[2], consts), const_eval(e[3], consts)
        if a is None or b is None:
            return None
        if e[1] == "+":
            return a + b
        if e[1] == "-":
            return a - b
        if e[1] == "*":
            return a * b
        return None if b == 0 else a / b
    return None


def _is_numeric(t):
    return t in ("SCALAR", "SERIES", "UNKNOWN")


class _Analyzer:
    def __init__(self):
        self.types = {"close": "SERIES"}
        self.s = Strategy()
        self.errors = []

    def err(self, msg):
        self.errors.append(msg)

    def run(self, stmts):
        for st in stmts:
            if st[0] == "assign":
                self.assign(st[1], st[2])
            else:
                self.rule(st[1], st[2])
        for req in ("INITIAL_CAPITAL", "ALLOCATION"):
            if req not in self.types:
                self.err(f"{req} is required")
        if self.errors:
            raise ValphaError("\n".join(self.errors))
        return self.s

    def assign(self, name, value):
        if name in self.types:
            self.err(f"'{name}' is already defined (redefinition is not allowed)")
        t = self.check(value)
        s = self.s
        if name in ("INITIAL_CAPITAL", "ALLOCATION"):
            if t not in ("SCALAR", "UNKNOWN"):
                self.err(f"{name} must be a plain number, got {t}")
            elif t == "SCALAR":
                v = const_eval(value, s.consts)
                if v is None:
                    self.err(f"{name} must be a constant number")
                elif name == "INITIAL_CAPITAL":
                    if not v > 0:
                        self.err("INITIAL_CAPITAL must be greater than 0")
                    s.initial_capital = v
                else:
                    if not (0 < v <= 1):
                        self.err("ALLOCATION must be greater than 0 and at most 1")
                    s.allocation = v
        elif name == "UNIVERSE":
            if t not in ("SET", "UNKNOWN"):
                self.err(f"UNIVERSE must be a set literal, got {t}")
            elif t == "SET":
                s.universe = list(value[1])
        else:
            if t == "SET":
                self.err(f"'{name}': a set literal may only be assigned to UNIVERSE")
            elif t == "SERIES":
                s.series_defs.append((name, value))
                s.output_columns.append(name)
            elif t == "SCALAR":
                s.consts[name] = value
            elif t == "BOOL":
                s.bools[name] = value
        self.types[name] = t
        if name in ("INITIAL_CAPITAL", "ALLOCATION") and t == "SCALAR":
            s.consts[name] = value

    def rule(self, kind, cond):
        t = self.check(cond)
        if t not in ("BOOL", "UNKNOWN"):
            self.err(f"{kind} WHEN condition must evaluate to a boolean, got {t}")
        self.s.rules.append((kind, cond))

    def check(self, e):
        kind = e[0]
        if kind == "num":
            return "SCALAR"
        if kind == "set":
            return "SET"
        if kind == "id":
            if e[1] not in self.types:
                self.err(f"undefined identifier '{e[1]}' (used before it was defined, or never defined)")
                return "UNKNOWN"
            return self.types[e[1]]
        if kind == "neg":
            t = self.check(e[1])
            if not _is_numeric(t):
                self.err(f"unary '-' needs a numeric operand, got {t}")
                return "UNKNOWN"
            return t
        if kind == "call":
            return self.check_call(e)
        op = e[1]
        lt, rt = self.check(e[2]), self.check(e[3])
        if op in ("+", "-", "*", "/"):
            if not (_is_numeric(lt) and _is_numeric(rt)):
                self.err(f"arithmetic '{to_text(e)}' needs numeric operands, got {lt} and {rt}")
                return "UNKNOWN"
            if op == "/" and const_eval(e[3], self.s.consts) == 0:
                self.err(f"division by zero in '{to_text(e)}'")
            if "SERIES" in (lt, rt):
                return "SERIES"
            return "SCALAR" if (lt == "SCALAR" and rt == "SCALAR") else "UNKNOWN"
        if op in CMP_OPS:
            if not (_is_numeric(lt) and _is_numeric(rt)):
                self.err(f"comparison '{to_text(e)}' needs numeric operands, got {lt} and {rt}")
            return "BOOL"
        # AND / OR
        if lt not in ("BOOL", "UNKNOWN") or rt not in ("BOOL", "UNKNOWN"):
            self.err(f"{op} needs boolean operands on both sides, got {lt} and {rt}")
        return "BOOL"

    def check_call(self, e):
        name, args = e[1], e[2]
        if name in SERIES_FUNCS:
            if len(args) != 2:
                self.err(f"{name}() expects 2 argument(s), got {len(args)}")
                for a in args:
                    self.check(a)
                return "SERIES"
            t = self.check(args[0])
            if t not in ("SERIES", "UNKNOWN"):
                self.err(f"{name}(): argument 1 must be a series, got {t}")
            w = const_eval(args[1], self.s.consts)
            if w is None or w < 1 or abs(w - round(w)) > 1e-9:
                self.err(f"{name}(): argument 2 (window size) must be a constant integer >= 1, "
                         f"got '{to_text(args[1])}'")
            return "SERIES"
        if name in UNARY_SERIES_FUNCS:
            if len(args) != 1:
                self.err(f"{name}() expects 1 argument(s), got {len(args)}")
                for a in args:
                    self.check(a)
                return "SERIES"
            t = self.check(args[0])
            if t not in ("SERIES", "UNKNOWN"):
                self.err(f"{name}(): argument 1 must be a series, got {t}")
            return "SERIES"
        if name in CROSS_FUNCS:
            if len(args) != 2:
                self.err(f"{name}() expects 2 argument(s), got {len(args)}")
                for a in args:
                    self.check(a)
                return "BOOL"
            ts = [self.check(a) for a in args]
            for i, t in enumerate(ts):
                if not _is_numeric(t):
                    self.err(f"{name}(): argument {i + 1} must be numeric, got {t}")
            if "SERIES" not in ts and "UNKNOWN" not in ts:
                self.err(f"{name}(): at least one argument must be a series")
            return "BOOL"
        self.err(f"unknown function '{name}' (built-ins: SMA, EMA, RSI, STDEV, LAG, HIGHEST, LOWEST, ABS, CROSSOVER, CROSSUNDER)")
        for a in args:
            self.check(a)
        return "UNKNOWN"


def parse_text(source, name="<string>"):
    """Parses + checks .valpha source text into a Strategy (raises ValphaError)."""
    try:
        stmts = _Parser(tokenize(source)).program()
        strategy = _Analyzer().run(stmts)
    except ValphaError as exc:
        raise ValphaError(f"{name}: {exc}") from None
    strategy.name = name
    m = re.search(r"^//\s*description:\s*(.+?)\s*$", source, re.MULTILINE)
    strategy.description = m.group(1) if m else name
    return strategy


def parse_file(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    strategy = parse_text(text, name=os.path.splitext(os.path.basename(path))[0])
    strategy.source_path = path
    return strategy

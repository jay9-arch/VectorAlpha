#pragma once
#include <vector>
#include "ast.hpp"
#include "token.hpp"

namespace valpha {

struct ParseError {
    std::string message;
    int line;
    int col;
};

// Recursive-descent parser for the .valpha grammar (DSL v2):
//
//   program      := statement*
//   statement    := assignment | rule
//   assignment   := IDENT '=' expr ';'
//   rule         := ('BUY' | 'SELL') 'WHEN' expr ';'
//
//   expr         := or_expr
//   or_expr      := and_expr ('OR' and_expr)*
//   and_expr     := comparison ('AND' comparison)*
//   comparison   := additive (cmp_op additive)?
//   cmp_op       := '<' | '>' | '<=' | '>=' | '==' | '!='
//   additive     := term (('+' | '-') term)*
//   term         := unary (('*' | '/') unary)*
//   unary        := '-' unary | primary
//   primary      := NUMBER
//                 | set_literal
//                 | IDENT '(' arg_list? ')'      // function call
//                 | IDENT                        // bare identifier
//                 | '(' expr ')'                 // grouping (arithmetic or boolean)
//   set_literal  := '{' IDENT (',' IDENT)* '}'
//   arg_list     := expr (',' expr)*
//
// Precedence, loosest to tightest: OR, AND, comparison, + -, * /,
// unary minus. A comparison does not chain (a < b < c is a syntax
// error); use AND. Whether an expression is numeric or boolean is a
// Phase 2 (semantic) concern, not the parser's.
class Parser {
public:
    explicit Parser(std::vector<Token> tokens);

    // Parses the whole token stream into a Program. Throws ParseError
    // on the first syntax error encountered.
    Program parse_program();

private:
    std::vector<Token> tokens_;
    size_t pos_ = 0;

    const Token& current() const;
    const Token& peek(int offset = 1) const;
    bool check(TokenType type) const;
    Token advance();
    Token expect(TokenType type, const std::string& what);
    [[noreturn]] void error(const std::string& message) const;

    StatementPtr parse_statement();
    StatementPtr parse_assignment();
    StatementPtr parse_rule();

    ExprPtr parse_expr();
    ExprPtr parse_or();
    ExprPtr parse_and();
    ExprPtr parse_comparison();
    ExprPtr parse_additive();
    ExprPtr parse_term();
    ExprPtr parse_unary();
    ExprPtr parse_primary();
    ExprPtr parse_set_literal();
    ExprPtr parse_function_call(const std::string& name);
};

}  // namespace valpha

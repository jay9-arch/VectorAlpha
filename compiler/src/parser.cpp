#include "parser.hpp"

namespace valpha {

Parser::Parser(std::vector<Token> tokens) : tokens_(std::move(tokens)) {}

const Token& Parser::current() const { return tokens_[pos_]; }

const Token& Parser::peek(int offset) const {
    size_t p = pos_ + static_cast<size_t>(offset);
    if (p >= tokens_.size()) return tokens_.back();  // EOF
    return tokens_[p];
}

bool Parser::check(TokenType type) const { return current().type == type; }

Token Parser::advance() {
    Token t = current();
    if (pos_ + 1 < tokens_.size()) pos_++;
    return t;
}

Token Parser::expect(TokenType type, const std::string& what) {
    if (!check(type)) {
        error("expected " + what + " but found " + token_type_name(current().type) +
              " ('" + current().text + "')");
    }
    return advance();
}

void Parser::error(const std::string& message) const {
    throw ParseError{message, current().line, current().col};
}

Program Parser::parse_program() {
    Program program;
    while (!check(TokenType::END_OF_FILE)) {
        program.statements.push_back(parse_statement());
    }
    return program;
}

StatementPtr Parser::parse_statement() {
    if (check(TokenType::KW_BUY) || check(TokenType::KW_SELL)) {
        return parse_rule();
    }
    if (check(TokenType::IDENT)) {
        return parse_assignment();
    }
    error("expected an assignment (NAME = ...;) or a BUY/SELL WHEN rule");
}

StatementPtr Parser::parse_assignment() {
    Token name_tok = expect(TokenType::IDENT, "an identifier");
    expect(TokenType::ASSIGN, "'='");
    ExprPtr value = parse_expr();
    expect(TokenType::SEMI, "';'");
    return Statement::make_assignment(name_tok.text, value);
}

StatementPtr Parser::parse_rule() {
    RuleKind kind = check(TokenType::KW_BUY) ? RuleKind::BUY : RuleKind::SELL;
    advance();  // consume BUY/SELL
    expect(TokenType::KW_WHEN, "'WHEN'");
    ExprPtr cond = parse_expr();
    expect(TokenType::SEMI, "';'");
    return Statement::make_rule(kind, cond);
}

ExprPtr Parser::parse_expr() { return parse_or(); }

ExprPtr Parser::parse_or() {
    ExprPtr left = parse_and();
    while (check(TokenType::KW_OR)) {
        advance();
        ExprPtr right = parse_and();
        left = Expr::make_binary(ExprKind::LogicalOr, BinOp::OR, left, right);
    }
    return left;
}

ExprPtr Parser::parse_and() {
    ExprPtr left = parse_comparison();
    while (check(TokenType::KW_AND)) {
        advance();
        ExprPtr right = parse_comparison();
        left = Expr::make_binary(ExprKind::LogicalAnd, BinOp::AND, left, right);
    }
    return left;
}

ExprPtr Parser::parse_comparison() {
    ExprPtr left = parse_additive();

    BinOp op = BinOp::NONE;
    switch (current().type) {
        case TokenType::LT: op = BinOp::LT; break;
        case TokenType::GT: op = BinOp::GT; break;
        case TokenType::LE: op = BinOp::LE; break;
        case TokenType::GE: op = BinOp::GE; break;
        case TokenType::EQ: op = BinOp::EQ; break;
        case TokenType::NE: op = BinOp::NE; break;
        default: return left;  // no comparison operator -- bare expr (e.g. a function call)
    }
    advance();
    ExprPtr right = parse_additive();
    return Expr::make_binary(ExprKind::Comparison, op, left, right);
}

ExprPtr Parser::parse_additive() {
    ExprPtr left = parse_term();
    while (check(TokenType::PLUS) || check(TokenType::MINUS)) {
        BinOp op = check(TokenType::PLUS) ? BinOp::ADD : BinOp::SUB;
        advance();
        ExprPtr right = parse_term();
        left = Expr::make_binary(ExprKind::Arithmetic, op, left, right);
    }
    return left;
}

ExprPtr Parser::parse_term() {
    ExprPtr left = parse_unary();
    while (check(TokenType::STAR) || check(TokenType::SLASH)) {
        BinOp op = check(TokenType::STAR) ? BinOp::MUL : BinOp::DIV;
        advance();
        ExprPtr right = parse_unary();
        left = Expr::make_binary(ExprKind::Arithmetic, op, left, right);
    }
    return left;
}

ExprPtr Parser::parse_unary() {
    if (check(TokenType::MINUS)) {
        advance();
        return Expr::make_negate(parse_unary());
    }
    return parse_primary();
}

ExprPtr Parser::parse_primary() {
    if (check(TokenType::NUMBER)) {
        Token t = advance();
        return Expr::make_number(std::stod(t.text));
    }
    if (check(TokenType::LBRACE)) {
        return parse_set_literal();
    }
    if (check(TokenType::LPAREN)) {
        advance();
        ExprPtr inner = parse_expr();
        expect(TokenType::RPAREN, "')'");
        return inner;
    }
    if (check(TokenType::IDENT)) {
        Token t = advance();
        if (check(TokenType::LPAREN)) {
            return parse_function_call(t.text);
        }
        return Expr::make_identifier(t.text);
    }
    error("expected a number, identifier, function call, '( ... )' group, or '{ ... }' set literal, found " +
          std::string(token_type_name(current().type)));
}

ExprPtr Parser::parse_set_literal() {
    expect(TokenType::LBRACE, "'{'");
    std::vector<std::string> items;
    if (!check(TokenType::RBRACE)) {
        items.push_back(expect(TokenType::IDENT, "an identifier inside '{ }'").text);
        while (check(TokenType::COMMA)) {
            advance();
            items.push_back(expect(TokenType::IDENT, "an identifier inside '{ }'").text);
        }
    }
    expect(TokenType::RBRACE, "'}'");
    return Expr::make_set(items);
}

ExprPtr Parser::parse_function_call(const std::string& name) {
    expect(TokenType::LPAREN, "'('");
    std::vector<ExprPtr> args;
    if (!check(TokenType::RPAREN)) {
        args.push_back(parse_expr());
        while (check(TokenType::COMMA)) {
            advance();
            args.push_back(parse_expr());
        }
    }
    expect(TokenType::RPAREN, "')'");
    return Expr::make_call(name, args);
}

}  // namespace valpha

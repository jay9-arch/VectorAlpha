#pragma once
#include <string>

namespace valpha {

enum class TokenType {
    IDENT,
    NUMBER,
    // punctuation
    ASSIGN,     // =
    SEMI,       // ;
    LPAREN,     // (
    RPAREN,     // )
    LBRACE,     // {
    RBRACE,     // }
    COMMA,      // ,
    // arithmetic
    PLUS, MINUS, STAR, SLASH,
    // comparisons
    LT, GT, LE, GE, EQ, NE,
    // keywords
    KW_BUY, KW_SELL, KW_WHEN, KW_AND, KW_OR,
    END_OF_FILE,
    INVALID
};

struct Token {
    TokenType type;
    std::string text;   // raw lexeme (identifier name, number text, etc.)
    int line;
    int col;
};

// Human-readable name for diagnostics.
const char* token_type_name(TokenType t);

}  // namespace valpha

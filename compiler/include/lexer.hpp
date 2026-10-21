#pragma once
#include <string>
#include <vector>
#include "token.hpp"

namespace valpha {

// Thrown on malformed input (unterminated token, unexpected character).
struct LexError {
    std::string message;
    int line;
    int col;
};

// Tokenizes .valpha source text.
//
// Grammar reminder (see VectorAlpha_IO_Spec.docx Section 1-2):
//   identifiers: [A-Za-z_][A-Za-z0-9_]*
//   numbers: integer or decimal, optionally signed at the lexer level
//            is NOT handled here -- unary minus is a parser concern.
//   keywords: BUY, SELL, WHEN, AND, OR (case-sensitive, matches spec's
//             worked example exactly)
//   punctuation: = ; ( ) { } ,
//   arithmetic: + - * /   ('-' is always its own token; unary minus is a
//               parser concern, and '//' still starts a comment)
//   comparisons: < > <= >= == !=
//   comments: `//` to end of line (not in the spec, but harmless and
//             commonly wanted -- can be ignored/removed later if the
//             team wants to stay strictly spec-literal)
class Lexer {
public:
    explicit Lexer(std::string source);

    // Tokenizes the entire source and returns all tokens, including a
    // trailing END_OF_FILE token. Throws LexError on invalid input.
    std::vector<Token> tokenize();

private:
    std::string src_;
    size_t pos_ = 0;
    int line_ = 1;
    int col_ = 1;

    bool at_end() const;
    char peek(int offset = 0) const;
    char advance();
    void skip_whitespace_and_comments();
    Token make_token(TokenType type, const std::string& text, int line, int col);
    Token lex_identifier_or_keyword();
    Token lex_number();
    Token lex_operator_or_punct();
};

}  // namespace valpha

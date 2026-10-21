#include "lexer.hpp"
#include <cctype>
#include <unordered_map>

namespace valpha {

namespace {
const std::unordered_map<std::string, TokenType> kKeywords = {
    {"BUY", TokenType::KW_BUY},
    {"SELL", TokenType::KW_SELL},
    {"WHEN", TokenType::KW_WHEN},
    {"AND", TokenType::KW_AND},
    {"OR", TokenType::KW_OR},
};
}  // namespace

const char* token_type_name(TokenType t) {
    switch (t) {
        case TokenType::IDENT: return "IDENT";
        case TokenType::NUMBER: return "NUMBER";
        case TokenType::ASSIGN: return "'='";
        case TokenType::SEMI: return "';'";
        case TokenType::LPAREN: return "'('";
        case TokenType::RPAREN: return "')'";
        case TokenType::LBRACE: return "'{'";
        case TokenType::RBRACE: return "'}'";
        case TokenType::COMMA: return "','";
        case TokenType::PLUS: return "'+'";
        case TokenType::MINUS: return "'-'";
        case TokenType::STAR: return "'*'";
        case TokenType::SLASH: return "'/'";
        case TokenType::LT: return "'<'";
        case TokenType::GT: return "'>'";
        case TokenType::LE: return "'<='";
        case TokenType::GE: return "'>='";
        case TokenType::EQ: return "'=='";
        case TokenType::NE: return "'!='";
        case TokenType::KW_BUY: return "BUY";
        case TokenType::KW_SELL: return "SELL";
        case TokenType::KW_WHEN: return "WHEN";
        case TokenType::KW_AND: return "AND";
        case TokenType::KW_OR: return "OR";
        case TokenType::END_OF_FILE: return "EOF";
        case TokenType::INVALID: return "INVALID";
    }
    return "?";
}

Lexer::Lexer(std::string source) : src_(std::move(source)) {}

bool Lexer::at_end() const { return pos_ >= src_.size(); }

char Lexer::peek(int offset) const {
    size_t p = pos_ + static_cast<size_t>(offset);
    if (p >= src_.size()) return '\0';
    return src_[p];
}

char Lexer::advance() {
    char c = src_[pos_++];
    if (c == '\n') {
        line_++;
        col_ = 1;
    } else {
        col_++;
    }
    return c;
}

void Lexer::skip_whitespace_and_comments() {
    for (;;) {
        char c = peek();
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            advance();
            continue;
        }
        if (c == '/' && peek(1) == '/') {
            while (!at_end() && peek() != '\n') advance();
            continue;
        }
        break;
    }
}

Token Lexer::make_token(TokenType type, const std::string& text, int line, int col) {
    return Token{type, text, line, col};
}

Token Lexer::lex_identifier_or_keyword() {
    int line = line_, col = col_;
    std::string text;
    while (!at_end() && (std::isalnum(static_cast<unsigned char>(peek())) || peek() == '_')) {
        text += advance();
    }
    auto it = kKeywords.find(text);
    if (it != kKeywords.end()) {
        return make_token(it->second, text, line, col);
    }
    return make_token(TokenType::IDENT, text, line, col);
}

Token Lexer::lex_number() {
    int line = line_, col = col_;
    std::string text;
    while (!at_end() && std::isdigit(static_cast<unsigned char>(peek()))) {
        text += advance();
    }
    if (peek() == '.' && std::isdigit(static_cast<unsigned char>(peek(1)))) {
        text += advance();  // consume '.'
        while (!at_end() && std::isdigit(static_cast<unsigned char>(peek()))) {
            text += advance();
        }
    }
    return make_token(TokenType::NUMBER, text, line, col);
}

Token Lexer::lex_operator_or_punct() {
    int line = line_, col = col_;
    char c = advance();
    switch (c) {
        case '=':
            if (peek() == '=') { advance(); return make_token(TokenType::EQ, "==", line, col); }
            return make_token(TokenType::ASSIGN, "=", line, col);
        case ';': return make_token(TokenType::SEMI, ";", line, col);
        case '(': return make_token(TokenType::LPAREN, "(", line, col);
        case ')': return make_token(TokenType::RPAREN, ")", line, col);
        case '{': return make_token(TokenType::LBRACE, "{", line, col);
        case '}': return make_token(TokenType::RBRACE, "}", line, col);
        case ',': return make_token(TokenType::COMMA, ",", line, col);
        case '+': return make_token(TokenType::PLUS, "+", line, col);
        case '-': return make_token(TokenType::MINUS, "-", line, col);
        case '*': return make_token(TokenType::STAR, "*", line, col);
        case '/': return make_token(TokenType::SLASH, "/", line, col);  // '//' was already consumed as a comment
        case '<':
            if (peek() == '=') { advance(); return make_token(TokenType::LE, "<=", line, col); }
            return make_token(TokenType::LT, "<", line, col);
        case '>':
            if (peek() == '=') { advance(); return make_token(TokenType::GE, ">=", line, col); }
            return make_token(TokenType::GT, ">", line, col);
        case '!':
            if (peek() == '=') { advance(); return make_token(TokenType::NE, "!=", line, col); }
            throw LexError{"unexpected character '!' (did you mean '!='?)", line, col};
        default:
            throw LexError{std::string("unexpected character '") + c + "'", line, col};
    }
}

std::vector<Token> Lexer::tokenize() {
    std::vector<Token> tokens;
    for (;;) {
        skip_whitespace_and_comments();
        if (at_end()) {
            tokens.push_back(make_token(TokenType::END_OF_FILE, "", line_, col_));
            break;
        }
        char c = peek();
        if (std::isalpha(static_cast<unsigned char>(c)) || c == '_') {
            tokens.push_back(lex_identifier_or_keyword());
        } else if (std::isdigit(static_cast<unsigned char>(c))) {
            tokens.push_back(lex_number());
        } else {
            tokens.push_back(lex_operator_or_punct());
        }
    }
    return tokens;
}

}  // namespace valpha

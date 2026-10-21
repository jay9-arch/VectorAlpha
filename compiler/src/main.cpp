// valphac driver -- runs every phase currently implemented:
//   lex -> parse -> Phase 2 (semantic analysis) -> Phase 3 (IR)
//   -> Phase 4 (optimization passes) -> Phase 5 (C++ codegen + g++)
//
// Usage:
//   ./valphac [flags] <path-to-strategy.valpha>
//       Lexes/parses/checks/lowers/optimizes and prints every stage's
//       output. Does not generate or compile anything.
//
//   ./valphac [flags] <path-to-strategy.valpha> <output_dir>
//       Does all of the above, then also writes
//       <output_dir>/<name>_runtime.cpp (where <name> is the input
//       file's stem) and compiles it with g++ into
//       <output_dir>/<name>_runtime. The resulting binary is run as:
//         <output_dir>/<name>_runtime <data_dir> <results_dir> [--threads N] [--scalar]
//
// Flags (for ablation benchmarks -- each turns one optimization off):
//   --no-cse        skip indicator-level common-subexpression elimination
//   --no-fuse       one pass per instruction over full arrays (scalar only)
//                   instead of the fused bar loop + SIMD batches
//   --tag=NAME      name the binary <name>_<NAME>_runtime so ablation
//                   builds can live side by side in the same directory
//
// Environment: CXX overrides the C++ compiler used (default g++).

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "ast.hpp"
#include "codegen.hpp"
#include "ir.hpp"
#include "lexer.hpp"
#include "optimize.hpp"
#include "parser.hpp"
#include "semantic.hpp"

static std::string read_file(const std::string& path) {
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("could not open file: " + path);
    }
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

static std::string file_stem(const std::string& path) {
    size_t slash = path.find_last_of("/\\");
    std::string base = (slash == std::string::npos) ? path : path.substr(slash + 1);
    size_t dot = base.find_last_of('.');
    return (dot == std::string::npos) ? base : base.substr(0, dot);
}

int main(int argc, char** argv) {
    valpha::OptimizeOptions opt_options;
    valpha::CodegenOptions cg_options;
    std::string tag;
    std::vector<std::string> positional;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--no-cse") opt_options.cse = false;
        else if (a == "--no-fuse") cg_options.fuse = false;
        else if (a.rfind("--tag=", 0) == 0) tag = a.substr(6);
        else if (a.rfind("--", 0) == 0) {
            std::cerr << "unknown flag: " << a << "\n";
            return 1;
        } else positional.push_back(a);
    }
    if (positional.size() != 1 && positional.size() != 2) {
        std::cerr << "usage: " << argv[0]
                  << " [--no-cse] [--no-fuse] [--tag=NAME] <path-to-strategy.valpha> [output_dir]\n";
        return 1;
    }

    std::string path = positional[0];
    bool do_codegen = (positional.size() == 2);
    std::string output_dir = do_codegen ? positional[1] : "";
    std::string source;
    try {
        source = read_file(path);
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }

    std::vector<valpha::Token> tokens;
    try {
        valpha::Lexer lexer(source);
        tokens = lexer.tokenize();
    } catch (const valpha::LexError& e) {
        std::cerr << path << ":" << e.line << ":" << e.col
                  << ": lex error: " << e.message << "\n";
        return 1;
    }

    std::cout << "=== Tokens (" << tokens.size() << ") ===\n";
    for (const auto& t : tokens) {
        if (t.type == valpha::TokenType::END_OF_FILE) continue;
        std::cout << "  [" << t.line << ":" << t.col << "] "
                  << valpha::token_type_name(t.type);
        if (t.type == valpha::TokenType::IDENT || t.type == valpha::TokenType::NUMBER) {
            std::cout << " \"" << t.text << "\"";
        }
        std::cout << "\n";
    }

    valpha::Program program;
    try {
        valpha::Parser parser(tokens);
        program = parser.parse_program();
    } catch (const valpha::ParseError& e) {
        std::cerr << path << ":" << e.line << ":" << e.col
                  << ": parse error: " << e.message << "\n";
        return 1;
    }

    std::cout << "\n=== Parsed AST (" << program.statements.size() << " statements) ===\n";
    std::cout << valpha::to_string(program);

    std::cout << "\n=== Phase 2: Semantic analysis ===\n";
    valpha::SemanticResult sem = valpha::analyze(program);
    if (!sem.ok) {
        for (const auto& err : sem.errors) {
            std::cerr << path << ": semantic error: " << err.message << "\n";
        }
        std::cerr << "\nFAILED: " << sem.errors.size() << " semantic error(s).\n";
        return 1;
    }
    std::cout << "OK: " << (sem.symbol_order.size() - 1) << " symbol(s) defined, "
               << sem.rules.size() << " rule(s), no semantic errors.\n";

    std::cout << "\n=== Phase 3: Domain-aware IR ===\n";
    valpha::IRProgram ir = valpha::build_ir(sem);
    std::cout << valpha::to_string(ir);

    std::cout << "\n=== Phase 4: Optimization passes ===\n";
    valpha::OptimizedIR opt = valpha::optimize(ir, opt_options);
    std::cout << valpha::to_string(opt);

    if (!do_codegen) {
        std::cout << "\nOK: lexed, parsed, checked, lowered, and optimized successfully.\n";
        std::cout << "(pass an output directory as a 2nd argument to also generate + compile C++)\n";
        return 0;
    }

    std::cout << "\n=== Phase 5: Code generation ===\n";
    std::string name = file_stem(path);
    if (!tag.empty()) name += "_" + tag;
    std::string cpp_path = output_dir + "/" + name + "_runtime.cpp";
    std::string bin_path = output_dir + "/" + name + "_runtime";

    std::string cpp_source;
    try {
        cpp_source = valpha::generate_cpp(opt, name, cg_options);
    } catch (const std::exception& e) {
        std::cerr << "codegen error: " << e.what() << "\n";
        std::cerr << "\nFAILED: this strategy uses a construct Phase 5's codegen doesn't "
                     "support yet (see compiler/README.md's known limitations).\n";
        return 1;
    }

    {
        std::error_code mkdir_ec;
        std::filesystem::create_directories(output_dir, mkdir_ec);
        if (mkdir_ec) {
            std::cerr << "error: could not create output directory " << output_dir << ": "
                      << mkdir_ec.message() << "\n";
            return 1;
        }
        std::ofstream out(cpp_path);
        if (!out) {
            std::cerr << "error: could not write " << cpp_path << "\n";
            return 1;
        }
        out << cpp_source;
    }
    std::cout << "Generated: " << cpp_path << "\n";

    // -ffp-contract=off keeps scalar and SIMD lanes (and the Python engines)
    // bit-compatible: no fused multiply-add may silently change a rounding.
    // -march=native lets the vector extensions lower to AVX2/AVX-512/NEON;
    // if the compiler rejects it (e.g. older Apple clang), retry without.
    const char* cxx_env = std::getenv("CXX");
    std::string cxx = (cxx_env && *cxx_env) ? cxx_env : "g++";
    std::string base_flags = " -std=c++17 -O3 -ffp-contract=off -fno-math-errno -pthread"
                             " -Wno-ignored-attributes";
    std::string tail = " -o \"" + bin_path + "\" \"" + cpp_path + "\" 2>&1";
    std::string compile_cmd = cxx + base_flags + " -march=native" + tail;
    std::cout << "Compiling: " << compile_cmd << "\n";
    int rc = std::system(compile_cmd.c_str());
    if (rc != 0) {
        std::string fallback = cxx + base_flags + tail;
        std::cout << "Retrying without -march=native: " << fallback << "\n";
        rc = std::system(fallback.c_str());
    }
    if (rc != 0) {
        std::cerr << "\nFAILED: " << cxx << " could not compile the generated source "
                     "(see compiler output above).\n";
        return 1;
    }

    std::cout << "\nOK: compiled successfully -> " << bin_path << "\n";
    std::cout << "Run it with: " << bin_path << " <data_dir> <results_dir> [--threads N] [--scalar]\n";
    return 0;
}

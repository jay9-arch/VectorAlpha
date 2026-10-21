cmake_minimum_required(VERSION 3.10)
project(valphac CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_executable(valphac
    src/main.cpp
    src/lexer.cpp
    src/parser.cpp
    src/ast.cpp
    src/semantic.cpp
    src/ir.cpp
    src/optimize.cpp
    src/codegen.cpp
)

target_include_directories(valphac PRIVATE include)

CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Iinclude

SRCS := src/main.cpp src/lexer.cpp src/parser.cpp src/ast.cpp src/semantic.cpp src/ir.cpp src/optimize.cpp src/codegen.cpp
OBJS := $(SRCS:.cpp=.o)
TARGET := valphac

.PHONY: all clean test

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^

%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<

test: $(TARGET)
	./tests/run_tests.sh

clean:
	rm -f $(OBJS) $(TARGET)

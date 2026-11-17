CXX ?= g++
CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -Werror -pedantic
LDLIBS ?= -pthread

all: vendarix-hw-adapter

vendarix-hw-adapter: hardware_adapter.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f vendarix-hw-adapter

version: vendarix-hw-adapter
	./vendarix-hw-adapter --version

.PHONY: all clean version

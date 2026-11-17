CXX ?= g++
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra -Werror -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE
LDFLAGS := -pie -Wl,-z,relro,-z,now

# Cross-compilation support: make ARCH=arm64
ifeq ($(ARCH),arm64)
	CXX := aarch64-linux-gnu-g++
endif

TARGET ?= build/vendarix-hw-adapter

.PHONY: all test clean release

all: $(TARGET)

$(TARGET): hardware_adapter.cpp
	@mkdir -p $(dir $(TARGET))
	$(CXX) $(CXXFLAGS) $< -o $@ $(LDFLAGS)

test: $(TARGET)
	./$(TARGET) --version
	python3 -m pytest test_integration.py -q

release: $(TARGET)
	@if [ "$(ARCH)" = "arm64" ]; then \
		aarch64-linux-gnu-strip $(TARGET); \
	else \
		strip $(TARGET); \
	fi

clean:
	rm -rf build

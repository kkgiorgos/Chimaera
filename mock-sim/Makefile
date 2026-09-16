BUILD_DIR ?= build
ENDPOINT ?= /tmp/mock-sim-demo

.PHONY: build clean

build:
	@cmake -S . -B "$(BUILD_DIR)"
	@cmake --build "$(BUILD_DIR)" --parallel

clean:
	@cmake -E rm -rf "$(BUILD_DIR)"

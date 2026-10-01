BUILD_DIR ?= build
BUILD_TYPE ?= Debug
JOBS ?= 4
GEM5_ROOT ?= ../gem5
CMAKE_ARGS ?=

.PHONY: build configure guest clean

build: configure
	cmake --build "$(BUILD_DIR)" --parallel "$(JOBS)"

configure:
	cmake -S . -B "$(BUILD_DIR)" -DCMAKE_BUILD_TYPE="$(BUILD_TYPE)" -DGEM5_ROOT="$(abspath $(GEM5_ROOT))" $(CMAKE_ARGS)

guest: configure
	cmake --build "$(BUILD_DIR)" --target gem5_guest gem5_guest_controller --parallel "$(JOBS)"

clean:
	cmake -E rm -rf "$(BUILD_DIR)"

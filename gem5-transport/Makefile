BUILD_DIR ?= build
BUILD_TYPE ?= Debug
JOBS ?= 4
GEM5_ROOT ?= ../gem5
CMAKE_ARGS ?=
IMAGE ?= $(GEM5_ROOT)/resources/x86-ubuntu-22.04-ros-humble.img
PARTITION ?= 2
GUEST_PATH ?= /usr/local/bin/gem5_transport_guest

.PHONY: build configure guest deploy controller-guest deploy-controller clean

build: configure
	cmake --build "$(BUILD_DIR)" --parallel "$(JOBS)"

configure:
	cmake -S . -B "$(BUILD_DIR)" -DCMAKE_BUILD_TYPE="$(BUILD_TYPE)" -DGEM5_ROOT="$(abspath $(GEM5_ROOT))" $(CMAKE_ARGS)

guest: configure
	cmake --build "$(BUILD_DIR)" --target gem5_transport_guest --parallel "$(JOBS)"

deploy: guest
	bash scripts/edit_disk.sh "$(IMAGE)" "$(BUILD_DIR)/gem5_transport_guest" "$(PARTITION)" "$(GUEST_PATH)"

controller-guest: configure
	cmake --build "$(BUILD_DIR)" --target gem5_controller_guest --parallel "$(JOBS)"

deploy-controller: controller-guest
	bash scripts/edit_disk.sh "$(IMAGE)" "$(BUILD_DIR)/gem5_controller_guest" "$(PARTITION)" /usr/local/bin/gem5_controller_guest

clean:
	cmake -E rm -rf "$(BUILD_DIR)"

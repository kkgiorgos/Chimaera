BUILD_DIR ?= build
ENDPOINT ?= /tmp/mock-sim-demo
CONTROLLER_ENDPOINT ?= /tmp/mock-controller-demo

.PHONY: build clean clean-sockets demo-host demo-guest

build:
	@cmake -S . -B "$(BUILD_DIR)"
	@cmake --build "$(BUILD_DIR)" --parallel

clean:
	@cmake -E rm -rf "$(BUILD_DIR)"

demo-host: build
	@"$(BUILD_DIR)/mock_controller_host" "$(CONTROLLER_ENDPOINT)"

demo-guest: build
	@"$(BUILD_DIR)/mock_controller_guest" "$(CONTROLLER_ENDPOINT)"

clean-sockets:
	@sh scripts/clean-sockets.sh "$(ENDPOINT)" "$(CONTROLLER_ENDPOINT)"

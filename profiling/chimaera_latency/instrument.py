#!/usr/bin/env python3
"""Instrument a NEW copy of the deployed ROS/transport sources; fail on drift."""
import argparse
import json
from pathlib import Path
import shutil


def replace(path, before, after, count=1):
    text = path.read_text()
    actual = text.count(before)
    if actual != count:
        raise RuntimeError(f'{path}: expected {count} anchors, found {actual}: {before!r}')
    path.write_text(text.replace(before, after))


def instrument(source, output):
    if output == source or source in output.parents:
        raise ValueError('The instrumented output must be a new directory outside the source tree')
    capabilities = source / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge/benchmark/latency_capabilities.json'
    if json.loads(capabilities.read_text()).get('version') != 4:
        raise ValueError('This overlay requires the deployed version-4 latency benchmark sources')
    output.mkdir(parents=True, exist_ok=False)
    for part in ('gem5-transport', 'ros/chimaera_ros_ws/src/chimaera_ros_bridge'):
        shutil.copytree(source / part, output / part, ignore=shutil.ignore_patterns('__pycache__'))
    transport = output / 'gem5-transport'
    package = output / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge'
    shutil.copyfile(Path(__file__).with_name('profile.hpp'), transport / 'include/chimaera/profile.hpp')
    paths = [transport / 'src/gem5_guest_controller.cpp', transport / 'src/gem5_host_controller.cpp',
             package / 'include/chimaera_ros_bridge/bridge.hpp',
             package / 'include/chimaera_ros_bridge/gazebo_timing_controller.hpp',
             package / 'src/guest_bridge.cpp', package / 'benchmark/driver.cpp']
    for path in paths:
        path.write_text('#include <chimaera/profile.hpp>\n' + path.read_text())

    path = transport / 'src/gem5_guest_controller.cpp'
    replace(path, 'impl_->outgoing.submit(std::move(data), key);',
            'profile::frame("controller_submit", data);\n    impl_->outgoing.submit(std::move(data), key);')
    replace(path, 'std::this_thread::sleep_for(s.poll);',
            'profile::Scope span("guest_poll_sleep");\n            std::this_thread::sleep_for(s.poll);')
    replace(path, 'control::send(s.transport, control::encode(s.outgoing.drain()));\n        s.incoming.append(control::decode(control::receive(s.transport)));', '''auto batch = s.outgoing.drain();
        profile::frames("guest_batch_send", batch);
        Message encoded;
        { profile::Scope span("guest_batch_encode"); encoded = control::encode(batch); }
        { profile::Scope span("guest_m5_send"); control::send(s.transport, encoded); }
        profile::frames("guest_batch_sent", batch);
        Message reply;
        { profile::Scope span("guest_m5_receive"); reply = control::receive(s.transport); }
        control::Batch received;
        { profile::Scope span("guest_batch_decode"); received = control::decode(reply); }
        profile::frames("guest_batch_received", received);
        s.incoming.append(std::move(received));''')

    path = transport / 'src/gem5_host_controller.cpp'
    replace(path, 'auto request = control::decode(control::receive(transport));', '''auto packet = control::receive(transport);
                control::Batch request;
                { profile::Scope span("host_batch_decode"); request = control::decode(packet); }
                profile::frames("host_worker_received", request);''')
    replace(path, 'const auto reply = control::encode(outgoing_batch);', '''profile::frames("host_worker_send", outgoing_batch);
                Message reply;
                { profile::Scope span("host_batch_encode"); reply = control::encode(outgoing_batch); }''')
    replace(path, 'control::send(transport, reply);',
            '{ profile::Scope span("host_socket_reply"); control::send(transport, reply); }')
    replace(path, 's.outgoing.submit(std::move(data), key);',
            'profile::frame("controller_submit", data);\n    s.outgoing.submit(std::move(data), key);')
    replace(path, 's.ready.append(std::move(received));',
            'profile::frames("host_controller_ready", received);\n        s.ready.append(std::move(received));')

    path = package / 'include/chimaera_ros_bridge/bridge.hpp'
    replace(path, 'if (message->size() > byte_limit_) {', '''if (key.find("/latency/guest_events") != std::string::npos &&
                std::string_view(chimaera::profile::state().side) == "guest")
              chimaera::profile::finish();
            const auto & raw = message->get_rcl_serialized_message();
            chimaera::profile::payload("bridge_callback", key,
              {reinterpret_cast<const std::byte *>(raw.buffer), raw.buffer_length});
            if (message->size() > byte_limit_) {''')
    replace(path, 'if (group.empty()) {', '''chimaera::profile::frame("bridge_enqueued", bytes);
            if (group.empty()) {''')
    replace(path, 'if (on_receive) on_receive(bytes);',
            'chimaera::profile::frame("bridge_receive", bytes);\n    if (on_receive) on_receive(bytes);')
    replace(path, 'stale_image();\n        return;',
            'chimaera::profile::frame("bridge_stale", bytes);\n        stale_image();\n        return;', count=2)
    replace(path, 'auto left_message = serialized(left);', '''chimaera::profile::frame("bridge_copy_begin", bytes);
      auto left_message = serialized(left);''')
    replace(path, 'auto right_message = serialized(right);', '''auto right_message = serialized(right);
      chimaera::profile::frame("bridge_copy_end", bytes);''')
    replace(path, 'publishers_.at(left.key)->publish(left_message);\n      publishers_.at(right.key)->publish(right_message);', '''chimaera::profile::payload("bridge_publish_begin", left.key, {left.data, left.size});
      publishers_.at(left.key)->publish(left_message);
      chimaera::profile::payload("bridge_publish_end", left.key, {left.data, left.size});
      chimaera::profile::payload("bridge_publish_begin", right.key, {right.data, right.size});
      publishers_.at(right.key)->publish(right_message);
      chimaera::profile::payload("bridge_publish_end", right.key, {right.data, right.size});''')
    replace(path, 'auto message = serialized(value);\n    publishers_.at(value.key)->publish(message);', '''chimaera::profile::frame("bridge_copy_begin", bytes);
    auto message = serialized(value);
    chimaera::profile::frame("bridge_copy_end", bytes);
    chimaera::profile::frame("bridge_publish_begin", bytes);
    publishers_.at(value.key)->publish(message);
    chimaera::profile::frame("bridge_publish_end", bytes);''')
    replace(path, '++transmitted_;\n      controller.submit',
            '++transmitted_;\n      chimaera::profile::frame("bridge_dequeue", entry->data);\n      controller.submit')

    path = package / 'src/guest_bridge.cpp'
    replace(path, 'rclcpp::init(argc, argv);',
            'chimaera::profile::initialize("guest");\n  rclcpp::init(argc, argv);')
    replace(path, 'chimaera::Gem5GuestController controller(', '''chimaera::profile::state().writer = [ops](const std::string & data) {
      const auto write = ops == "instruction" ? m5_write_file : m5_write_file_addr;
      if (write(const_cast<char *>(data.data()), data.size(), 0, "chimaera-profile-guest.csv") != data.size())
        throw std::runtime_error("Incomplete guest profile export");
    };
    chimaera::Gem5GuestController controller(''')
    replace(path, 'time_ready = true;', '''chimaera::profile::state().clock = [origin, ops] {
          const auto current = ops == "instruction" ? m5_rpns() : m5_rpns_addr();
          return current >= origin ? current - origin : chimaera::profile::unset;
        };
        time_ready = true;''')

    path = package / 'include/chimaera_ros_bridge/gazebo_timing_controller.hpp'
    replace(path, 'bridge_.exchange(host_);\n    const auto result = host_.step(interval_);', '''{ chimaera::profile::Scope span("host_boundary_ingress"); bridge_.exchange(host_); }
    chimaera::ControllerResult result;
    { chimaera::profile::Scope span("gem5_phase"); result = host_.step(interval_); }''')
    replace(path, 'bridge_.exchange(host_);\n    // DDS',
            '{ chimaera::profile::Scope span("host_boundary_egress"); bridge_.exchange(host_); }\n    // DDS')
    replace(path, 'settle();\n    advance_physics();',
            '{ chimaera::profile::Scope span("settle_before_physics"); settle(); }\n    advance_physics();')
    replace(path, 'void advance_physics()\n  {',
            'void advance_physics()\n  {\n    chimaera::profile::Scope span("gazebo_request");')
    replace(path, 'std::unique_lock lock(mutex_);\n    const auto deadline = std::chrono::steady_clock::now() + timeout_;',
            'chimaera::profile::record("gazebo_wait", \'B\');\n    std::unique_lock lock(mutex_);\n    const auto deadline = std::chrono::steady_clock::now() + timeout_;')
    replace(path, 'settle();\n    pending_ = false;', '''chimaera::profile::record("gazebo_wait", 'E');
    { chimaera::profile::Scope span("settle_after_physics"); settle(); }
    pending_ = false;''')

    path = package / 'benchmark/driver.cpp'
    replace(path, 'rclcpp::init(argc, argv);',
            'chimaera::profile::initialize("host");\n  rclcpp::init(argc, argv);')
    replace(path, 'uint64_t origin_fraction = 0;', '''chimaera::profile::state().clock = [&logical] { return logical.now(); };
      uint64_t origin_fraction = 0;''')
    replace(path, 'const auto result = timing.step(std::chrono::microseconds(interval));',
            'chimaera::profile::Scope span("whole_step");\n          const auto result = timing.step(std::chrono::microseconds(interval));', count=2)
    replace(path, '} else {\n      NativeWorld gazebo(world);', '''chimaera::profile::finish();
    } else {
      chimaera::profile::state().clock = [&physical] { return physical.now(); };
      NativeWorld gazebo(world);''')
    replace(path, 'gazebo.control(true);', 'gazebo.control(true);\n      chimaera::profile::finish();')
    replace(path, 'void step(uint64_t interval, uint64_t physics_step, Clock::time_point deadline)\n  {',
            'void step(uint64_t interval, uint64_t physics_step, Clock::time_point deadline)\n  {\n    chimaera::profile::Scope span("native_whole_step");')
    replace(path, 'std::this_thread::sleep_for(std::chrono::milliseconds(20));', '''{ chimaera::profile::Scope span("native_settle");
      std::this_thread::sleep_for(std::chrono::milliseconds(20)); }''', count=2)
    replace(path, 'control(true, interval / physics_step);', '''chimaera::profile::record("native_gazebo_phase", 'B');
    control(true, interval / physics_step);''')
    replace(path, 'lock.unlock();\n    { chimaera::profile::Scope', '''lock.unlock();
    chimaera::profile::record("native_gazebo_phase", 'E');
    { chimaera::profile::Scope''')
    replace(path, 'status = 1;', 'chimaera::profile::finish();\n    status = 1;')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    instrument(args.source.resolve(), args.output.resolve())

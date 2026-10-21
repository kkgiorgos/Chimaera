#!/usr/bin/env python3
"""Create an isolated diagnostic source overlay for DDS image attribution."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def replace(path, old, new, count=1):
    value = path.read_text()
    if value.count(old) != count:
        raise ValueError(f'{path}: expected {count} occurrences of {old!r}')
    path.write_text(value.replace(old, new))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--rmw-source', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    bridge = output / 'bridge'
    dds = output / 'dds'
    transport = output / 'transport'
    rmw = output / 'rmw'
    for source, target in ((ROOT / 'ros/chimaera_ros_ws/src/chimaera_ros_bridge', bridge),
                           (ROOT / 'ros/chimaera_ros_ws/src/chimaera_dds_transport', dds),
                           (ROOT / 'gem5-transport', transport), (args.rmw_source, rmw)):
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(
            'build', '__pycache__', '.pytest_cache'))
    for name in ('pipeline_trace.hpp', 'pipeline_trace.cpp'):
        shutil.copy2(Path(__file__).with_name(name), dds / 'src' / name)
    # Publish the diagnostic API with the existing transport library so every
    # linked RMW, transport and application stage shares exactly one recorder.
    shutil.copy2(Path(__file__).with_name('pipeline_trace.hpp'),
                 dds / 'include/chimaera_dds_transport/pipeline_trace.hpp')
    replace(dds / 'CMakeLists.txt', 'src/configure.cpp)', 'src/configure.cpp src/pipeline_trace.cpp)')
    replace(bridge / 'CMakeLists.txt', 'target_link_libraries(latency_endpoint PRIVATE ${CMAKE_DL_LIBS})',
            'target_link_libraries(latency_endpoint PRIVATE ${CMAKE_DL_LIBS} '
            'chimaera_dds_transport::chimaera_dds_transport)')
    replace(bridge / 'CMakeLists.txt', 'add_subdirectory("${GEM5_TRANSPORT_ROOT}"',
            'include_directories("${CMAKE_CURRENT_SOURCE_DIR}/../dds/include")\n'
            'add_subdirectory("${GEM5_TRANSPORT_ROOT}"')
    def include(path):
        path.write_text('#include <chimaera_dds_transport/pipeline_trace.hpp>\n' + path.read_text())
    endpoint = bridge / 'benchmark/endpoint.cpp'
    include(endpoint)
    replace(endpoint, 'if (library_) dlclose(library_);', 'pipeline::flush();\n    if (library_) dlclose(library_);')
    replace(endpoint, 'if (time == SharedClock::unset) return;',
            '''if (time == SharedClock::unset) return;
    pipeline::initialize([this] { return now(); }, library_ ?
      std::function<void(const std::string&, const std::string&)>([this](const auto& name, const auto& data) {
        auto write = reinterpret_cast<uint64_t (*)(void*, uint64_t, uint64_t, const char*)>(
          dlsym(library_, "chimaera_gem5_write_file"));
        if (!write || write(const_cast<char*>(data.data()), data.size(), 0, name.c_str()) != data.size())
          throw std::runtime_error("pipeline guest export failed");
      }) : std::function<void(const std::string&, const std::string&)>{});''')
    replace(endpoint, 'frozen_ = true;', 'frozen_ = true;\n      pipeline::flush();')
    replace(endpoint, 'stream.publish = [this, publisher, message](uint64_t scheduled)',
            'stream.publish = [this, id, publisher, message](uint64_t scheduled)')
    replace(endpoint, 'publisher->publish(*message);',
            'if (id <= 2) pipeline::event("publish_begin", id, scheduled, message_bytes(*message));\n'
            '        publisher->publish(*message);\n'
            '        if (id <= 2) pipeline::event("publish_end", id, scheduled);')
    replace(endpoint, 'const auto & stream = streams_.at(id);',
            'if (id <= 2) pipeline::event("callback_begin", id, capture);\n'
            '          const auto & stream = streams_.at(id);')
    replace(endpoint, 'if (events_.size() >= 4000000)\n',
            'if (id <= 2) pipeline::event("verify_end", id, capture);\n'
            '          if (events_.size() >= 4000000)\n')
    replace(endpoint, 'private:\n  static uint64_t release',
            'private:\n  template<class M> static size_t message_bytes(const M& message) {\n'
            '    if constexpr (std::is_same_v<M, sensor_msgs::msg::Image>) return message.data.size();\n'
            '    else return 0;\n  }\n  static uint64_t release')
    clock = bridge / 'src/gem5_clock.cpp'
    clock.write_text(clock.read_text() + '''
extern "C" uint64_t chimaera_gem5_write_file(void* data, uint64_t size, uint64_t offset, const char* name) {
#ifdef CHIMAERA_CLOCK_ADDRESS
  ensure_mapping();
  return m5_write_file_addr(data, size, offset, name);
#else
  return m5_write_file(data, size, offset, name);
#endif
}
''')
    driver = bridge / 'benchmark/driver.cpp'
    include(driver)
    replace(driver, 'uint64_t origin_fraction = 0;',
            'pipeline::initialize([&logical] { return logical.now(); });\n'
            '      uint64_t origin_fraction = 0;')
    replace(driver, 'if (reuse_guest) {', 'pipeline::flush();\n      if (reuse_guest) {')
    guest = bridge / 'src/guest_bridge.cpp'
    include(guest)
    replace(guest, 'bool time_ready = false;', '''
    pipeline::initialize([]() -> uint64_t {
      static std::optional<uint64_t> origin;
      if (!origin) {
        std::ifstream input("/tmp/chimaera-sim-time-origin.ns");
        uint64_t value;
        if (!(input >> value)) return pipeline::unset;
        origin = value;
      }
      return m5_rpns_addr() - *origin;
    }, [](const auto& name, const auto& data) {
      if (m5_write_file_addr(const_cast<char*>(data.data()), data.size(), 0, name.c_str()) != data.size())
        throw std::runtime_error("pipeline bridge export failed");
    });
    bool time_ready = false;''')
    replace(guest, '++iteration;', '++iteration;\n'
            '      if (std::getenv("CHIMAERA_PIPELINE") && time_ready && node->simulation_time().value_or(0) >= '
            'std::stoll(std::getenv("CHIMAERA_PIPELINE_CUTOFF_NS") ? '
            'std::getenv("CHIMAERA_PIPELINE_CUTOFF_NS") : "1250000000")) pipeline::flush();')
    tx = dds / 'src/transport.cpp'
    include(tx)
    replace(tx, 'std::lock_guard lock(send_mutex_);',
            'pipeline::image("transport_send_begin", packet.data(), packet.size());\n'
            '    std::lock_guard lock(send_mutex_);')
    replace(tx, 'if (wire::send_all(fd_, packet.data(), packet.size())) return true;',
            'if (wire::send_all(fd_, packet.data(), packet.size())) {\n'
            '      pipeline::image("transport_send_end", packet.data(), packet.size());\n'
            '      return true;\n    }')
    replace(tx, 'const auto destination = wire::locator(packet.data() + 28);',
            'pipeline::image("transport_receive", packet.data(), packet.size());\n'
            '        const auto destination = wire::locator(packet.data() + 28);')
    replace(tx, 'if (channel->receiver)\n', 'if (channel->receiver) {\n')
    replace(tx, 'channel->locator, wire::locator(packet.data() + 8));',
            'channel->locator, wire::locator(packet.data() + 8));\n'
            '          pipeline::image("dds_receive_end", packet.data(), packet.size());\n        }')
    router = dds / 'src/router.cpp'
    include(router)
    replace(router, 'const auto source = wire::locator(packet.data() + 8);',
            'pipeline::image("router_accept", packet.data(), packet.size());\n'
            '    const auto source = wire::locator(packet.data() + 8);')
    replace(router, 'auto packet = std::move(impl_->outgoing.front());',
            'pipeline::image("router_take", impl_->outgoing.front().data(), impl_->outgoing.front().size());\n'
            '  auto packet = std::move(impl_->outgoing.front());')
    replace(router, 'auto bytes = static_cast<const uint8_t *>(data);',
            'pipeline::image("router_receive", data, size);\n'
            '  auto bytes = static_cast<const uint8_t *>(data);')
    replace(router, '          queued -= packet.size();',
            '          pipeline::image("router_send_end", packet.data(), packet.size());\n'
            '          queued -= packet.size();')
    host_dma = transport / 'src/dma_host_controller.cpp'
    include(host_dma)
    replace(host_dma, 's.outgoing.submit(std::move(data), key);',
            'pipeline::image("dma_host_submit", data.data(), data.size());\n'
            '    s.outgoing.submit(std::move(data), key);')
    replace(host_dma, 's.h2g.commit(layout.size());',
            'pipeline::image("mailbox_commit", output, layout.size());\n'
            '            s.h2g.commit(layout.size());')
    guest_dma = transport / 'src/dma_guest_controller.cpp'
    include(guest_dma)
    replace(guest_dma, 'events.push_back(completion);',
            'if (completion.events == CHDMA_IRQ_RX && completion.slot < pool->info.rx_slots)\n'
            '                          pipeline::image("dma_completion_read", pool->buffers[completion.slot], completion.length);\n'
            '                        events.push_back(completion);')
    replace(guest_dma, 'incoming.append(dma::decode(std::move(bytes)));',
            'pipeline::image("dma_guest_service", bytes.data(), bytes.size());\n'
            '                    incoming.append(dma::decode(std::move(bytes)));')
    ts = rmw / 'rmw_fastrtps_shared_cpp/src/TypeSupport_impl.cpp'
    include(ts)
    replace(ts, 'if (this->serializeROSmessage(ser_data->data, ser, ser_data->impl)) {',
            'const auto start = std::strstr(getName(), "Image_") ? pipeline::stamp() : pipeline::Stamp{pipeline::unset, pipeline::unset, pipeline::unset};\n'
            '    if (this->serializeROSmessage(ser_data->data, ser, ser_data->impl)) {')
    replace(ts, 'payload->length = (uint32_t)ser.getSerializedDataLength();',
            'payload->length = (uint32_t)ser.getSerializedDataLength();\n'
            '      pipeline::image("serialize_begin", payload->data, payload->length, start);\n'
            '      pipeline::image("serialize_end", payload->data, payload->length);')
    replace(ts, 'return deserializeROSmessage(deser, ser_data->data, ser_data->impl);',
            'pipeline::image("deserialize_begin", payload->data, payload->length);\n'
            '  const bool result = deserializeROSmessage(deser, ser_data->data, ser_data->impl);\n'
            '  pipeline::image("deserialize_end", payload->data, payload->length);\n'
            '  return result;')
    print(output)


if __name__ == '__main__':
    main()

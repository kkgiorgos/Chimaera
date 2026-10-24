#include <chimaera_dds_transport/transport.hpp>
#include <fastdds/rtps/common/LocatorSelector.hpp>
#include <iostream>
#include <memory>
#include <stdexcept>

#include "protocol.hpp"

using namespace eprosima::fastrtps::rtps;
void require(bool value, const char * reason)
{
  if (!value) throw std::runtime_error(reason);
}
int main()
{
  try {
    chimaera_dds::Descriptor descriptor("/tmp/unused-chimaera-selector", 1);
    std::unique_ptr<eprosima::fastdds::rtps::TransportInterface> transport(
      descriptor.create_transport());
    for (bool multicast : {false, true}) {
      LocatorSelector selector(
        eprosima::fastrtps::ResourceLimitedContainerConfig::fixed_size_configuration(8));
      std::vector<std::unique_ptr<LocatorSelectorEntry>> entries;
      const auto shared = chimaera_dds::wire::local(2, 0x20000001);
      auto other_process = shared;
      ++other_process.address[4];
      auto other_port = shared;
      ++other_port.port;
      auto add = [&](const Locator_t & locator, bool enabled = true) {
        auto entry = std::make_unique<LocatorSelectorEntry>(2, 2);
        entry->remote_guid.guidPrefix.value[0] = uint8_t(entries.size() + 1);
        (multicast ? entry->multicast : entry->unicast).push_back(locator);
        // A duplicate destination must not trigger a fallback send.
        if (!entries.empty() && locator == shared && !multicast)
          entry->multicast.push_back(other_process);
        entry->enabled = enabled;
        require(selector.add_entry(entry.get()), "selector allocation failed");
        entries.push_back(std::move(entry));
      };
      add(shared);
      add(shared);
      add(other_process);
      add(other_port);
      auto disabled = shared;
      disabled.port += 2;
      add(disabled, false);
      selector.selection_start();
      transport->select_locators(selector);
      std::vector<Locator_t> selected;
      selector.for_each([&](const Locator_t & locator) { selected.push_back(locator); });
      require(selected.size() == 3, "duplicate reader locator selected or destination lost");
      require(
        selected[0] == shared && selected[1] == other_process && selected[2] == other_port,
        "distinct participant/port destinations changed");
      // Reader-specific retransmissions must still select the shared locator.
      selector.reset(false);
      selector.enable(entries[1]->remote_guid);
      selector.selection_start();
      transport->select_locators(selector);
      require(selector.selected_size() == 1, "single-reader retransmission suppressed");
    }
    std::cout << "Shared reader destinations, separate processes/ports and reselection passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}

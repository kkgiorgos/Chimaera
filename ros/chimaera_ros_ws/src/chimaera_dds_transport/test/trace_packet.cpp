#include <iostream>
#include <stdexcept>

#include "rtps_trace.hpp"

using chimaera_dds::trace::parts;
void require(bool value, const char * reason)
{
  if (!value) throw std::runtime_error(reason);
}
std::vector<uint8_t> frame(
  bool fragment, bool little, uint32_t first = 1, uint32_t count = 1, uint32_t total = 3)
{
  std::vector<uint8_t> bytes(24 + (fragment ? 32 + 16 * count : 24));
  std::memcpy(bytes.data(), "RTPS", 4);
  for (unsigned i = 0; i < 12; ++i) bytes[8 + i] = i;
  bytes[20] = fragment ? 0x16 : 0x15;
  bytes[21] = unsigned(little) | (fragment ? 0 : 4);
  const auto number = [&](unsigned offset, uint32_t value, unsigned size) {
    for (unsigned i = 0; i < size; ++i)
      bytes[offset + i] = uint8_t(value >> (8 * (little ? i : size - i - 1)));
  };
  number(22, bytes.size() - 24, 2);
  number(26, fragment ? 28 : 16, 2);
  bytes[32] = 1;
  bytes[33] = 2;
  bytes[34] = 3;
  bytes[35] = 3;  // User data writer.
  number(40, 123, 4);
  if (fragment) {
    number(44, first, 4);
    number(48, count, 2);
    number(50, 16, 2);
    number(52, total * 16, 4);
  }
  return bytes;
}
int main()
{
  try {
    std::string identity;
    for (bool little : {false, true}) {
      auto bytes = frame(false, little);
      auto rows = parts(bytes.data(), bytes.size());
      require(rows.size() == 1 && rows[0].total == 1, "DATA metadata missing");
      if (identity.empty()) identity = rows[0].id;
      require(
        rows[0].id == identity && identity.find(":0:123") != std::string::npos,
        "DDS sample identity or endian mismatch");
      bytes[22] = bytes[23] = 0;
      require(parts(bytes.data(), bytes.size()).size() == 1, "zero-length DATA rejected");
      bytes = frame(true, little, 2, 2, 3);
      rows = parts(bytes.data(), bytes.size());
      require(
        rows.size() == 1 && rows[0].id == identity && rows[0].first == 2 && rows[0].count == 2 &&
          rows[0].total == 3,
        "DATA_FRAG range mismatch");
      bytes = frame(true, little, 3, 2, 3);
      require(parts(bytes.data(), bytes.size()).empty(), "impossible fragment range accepted");
      bytes = frame(false, little);
      bytes[35] = 0xc2;
      require(
        parts(bytes.data(), bytes.size()).empty(), "discovery was traced as application data");
      bytes = frame(false, little);
      bytes[21] &= ~4;
      require(parts(bytes.data(), bytes.size()).empty(), "dispose-only DATA was traced");
      bytes = frame(false, little);
      bytes.pop_back();
      require(parts(bytes.data(), bytes.size()).empty(), "truncated DATA accepted");
      bytes = frame(false, little);
      bytes.push_back(0x15);
      require(parts(bytes.data(), bytes.size()).empty(), "partial datagram emitted trace metadata");
    }
    auto bytes = frame(false, true);
    bytes.insert(bytes.begin() + 20, 24, 0);
    bytes[20] = 0x0c;
    bytes[21] = 1;
    bytes[22] = 20;
    std::fill(bytes.begin() + 32, bytes.begin() + 44, 0x55);
    const auto rows = parts(bytes.data(), bytes.size());
    require(
      rows.size() == 1 && rows[0].id != identity && rows[0].id.find("55.55") == 0,
      "INFO_SRC prefix was ignored");
    std::cout << "RTPS DATA, fragment, source, endian and malformed metadata checks passed\n";
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}

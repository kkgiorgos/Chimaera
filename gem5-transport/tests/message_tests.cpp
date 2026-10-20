#include <chimaera/message.hpp>
#include <span>
#include <stdexcept>
#include <utility>

using chimaera::Message;
void expect(bool condition) {
    if (!condition) throw std::runtime_error("message ownership/initialization failure");
}
int main() {
    Message empty;
    expect(empty.empty() && empty.begin() == empty.end());
    Message copied_empty(empty);
    expect(copied_empty == empty);
    empty.reserve(32);
    empty.resize(16);
    for (auto value : empty) expect(value == std::byte{});
    empty[3] = std::byte{9};
    empty.reserve(64);
    expect(empty[3] == std::byte{9});
    empty.clear();
    empty.resize(16);
    for (auto value : empty) expect(value == std::byte{});
    Message image = Message::for_overwrite(640 * 480 * 3);
    // Initialize every byte before observing or exposing the receive buffer.
    for (std::size_t i = 0; i < image.size(); ++i) image[i] = static_cast<std::byte>(i % 251);
    const auto* storage = image.data();
    Message moved(std::move(image));
    expect(moved.data() == storage && image.empty() && image.capacity() == 0);
    Message copy = moved;
    expect(copy == moved && copy.data() != moved.data());
    copy[0] = std::byte{255};
    expect(copy != moved && moved[0] == std::byte{});
    copy = moved;
    expect(copy == moved);
    moved = std::move(moved); // Valid self-move retains owning storage.
    expect(moved.data() == storage && moved == copy);
    const Message tail{std::byte{1}, std::byte{2}};
    Message packet;
    packet.insert(packet.end(), tail.begin(), tail.end());
    packet.insert(packet.begin() + 1, tail.begin(), tail.end());
    expect(packet == Message({std::byte{1}, std::byte{1}, std::byte{2}, std::byte{2}}));
    packet.push_back(std::byte{3});
    packet.pop_back();
    packet.resize(2);
    packet.resize(4, std::byte{8});
    expect(packet.back() == std::byte{8} && packet.front() == std::byte{1});
    expect(std::span<const std::byte>(packet).size() == packet.size());
    packet.insert(packet.end(), empty.end(), empty.end());
    expect(packet.size() == 4);
    return 0;
}

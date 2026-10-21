#pragma once

// Diagnostic overlay. One shared recorder per process; no wire-format changes.
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

namespace pipeline {
constexpr uint64_t unset = UINT64_MAX;
struct Stamp { uint64_t sim, wall, cpu; };
void initialize(std::function<uint64_t()> clock,
                std::function<void(const std::string&, const std::string&)> writer = {});
Stamp stamp();
void event(const char* stage, unsigned stream, uint64_t capture, size_t bytes = 0);
void image(const char* stage, const void* data, size_t size, Stamp at = {unset, unset, unset});
void flush();
}

#pragma once

// An optional guest-only preload supplies this hook. Ordinary runtimes retain
// native middleware memory work. The scope never extends into user callbacks.
extern "C" void chimaera_middleware_copy_scope(int delta) __attribute__((weak));

namespace chimaera_dds
{
class FunctionalCopyScope
{
public:
  FunctionalCopyScope()
  {
    if (chimaera_middleware_copy_scope) chimaera_middleware_copy_scope(1);
  }
  ~FunctionalCopyScope()
  {
    if (chimaera_middleware_copy_scope) chimaera_middleware_copy_scope(-1);
  }
  FunctionalCopyScope(const FunctionalCopyScope &) = delete;
  FunctionalCopyScope & operator=(const FunctionalCopyScope &) = delete;
};
}  // namespace chimaera_dds

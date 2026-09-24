#pragma once

#include <chimaera/controller.hpp>
#include <stdexcept>

namespace chimaera::controller_example {

inline void require(chimaera::ControllerResult result) {
    if (result.state == chimaera::ControllerState::failed)
        throw std::runtime_error(result.message);
}

} // namespace chimaera::controller_example

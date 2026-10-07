# SPDX-License-Identifier: MIT
# Adapted from CMU-SAFARI/ramulator2; see LICENSE.

from m5.objects.AbstractMemory import AbstractMemory
from m5.params import Param, ResponsePort


class Ramulator2(AbstractMemory):
    type = "Ramulator2"
    cxx_class = "gem5::memory::Ramulator2"
    cxx_header = "mem/ramulator2/ramulator2.hh"

    port = ResponsePort("Memory request/response port")
    ramulator_config = Param.String("Expanded Ramulator 2.1 YAML/JSON")
    max_outstanding = Param.Unsigned(
        256, "Maximum pending transactions and responses"
    )

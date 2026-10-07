# SPDX-License-Identifier: MIT
# Adapted from CMU-SAFARI/ramulator2; see LICENSE.

from m5.objects.AbstractMemory import AbstractMemory
from m5.params import Param, VectorParam, VectorResponsePort


class Ramulator2VectorPorts(AbstractMemory):
    type = "Ramulator2VectorPorts"
    cxx_class = "gem5::memory::Ramulator2VectorPorts"
    cxx_header = "mem/ramulator2/ramulator2_vector_ports.hh"

    ports = VectorResponsePort("Memory request/response ports")
    port_ranges = VectorParam.AddrRange([], "Range for each port")
    ramulator_config = Param.String("Expanded Ramulator 2.1 YAML/JSON")
    max_outstanding = Param.Unsigned(
        256, "Maximum pending transactions and responses"
    )

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
    aggressor_pattern = Param.String("none", "none, stream, stride, or random")
    aggressor_interval = Param.Unsigned(4, "DRAM cycles between offered requests")
    aggressor_stride = Param.Unsigned(4096, "Stride in bytes")
    aggressor_read_percent = Param.Unsigned(80, "Read percentage, remaining writes")
    aggressor_max_pending = Param.Unsigned(128, "Independent accelerator queue cap")
    aggressor_window = Param.Unsigned(268435456, "Address window above exposed guest RAM")
    aggressor_seed = Param.Unsigned(1, "Deterministic random seed")
    aggressor_duty_percent = Param.Unsigned(100, "Active fraction of burst period")
    aggressor_period = Param.Unsigned(12000, "Burst period in DRAM cycles")

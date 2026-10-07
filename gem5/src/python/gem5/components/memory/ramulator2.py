"""Optional Ramulator 2.1 memory using an exported JSON configuration."""

import json
from math import prod
from pathlib import Path
from typing import List, Sequence, Tuple

from m5.objects import AbstractMemory
from m5.params import AddrRange, Port
from m5.util.convert import toMemorySize

from ..boards.abstract_board import AbstractBoard
from .abstract_memory_system import AbstractMemorySystem


class Ramulator2Memory(AbstractMemorySystem):
    """One gem5 port backed by one or more Ramulator DRAM channels.

    ``configuration`` is a fully expanded Ramulator 2.1 JSON export, not a
    Ramulator 2.0 YAML preset. ``size`` selects the exposed backing-store size
    and must not exceed the DRAM capacity. No Ramulator Python bindings are
    needed at runtime. Atomic/KVM accesses bypass DRAM timing.
    """

    def __init__(
        self, configuration: str, size: str, max_outstanding: int = 256
    ) -> None:
        super().__init__()
        try:
            from m5.objects import Ramulator2
        except ImportError as error:
            raise RuntimeError(
                "Build gem5 with RAMULATOR2_ROOT; see "
                "gem5/ext/ramulator2/README.md"
            ) from error

        self._size = toMemorySize(size)
        if self._size <= 0 or max_outstanding < 2:
            raise ValueError(
                "Memory size must be positive; max_outstanding must be >= 2"
            )
        config = json.loads(Path(configuration).read_text())
        if config.get("frontend", {}).get("impl") != "External":
            raise ValueError("Ramulator2 requires the External frontend")
        controllers = config["memory_system"]["controllers"]
        if not controllers:
            raise ValueError("Ramulator2 requires at least one controller")
        capacity = 0
        for controller in controllers:
            dram = controller["dram"]
            counts = dram["org"]["count"]
            if not counts or any(count <= 0 for count in counts):
                raise ValueError("DRAM organization counts must be positive")
            capacity += prod(counts) * dram["channel_width"] // 8
        if self._size > capacity:
            raise ValueError(
                f"Exposed memory ({self._size}) exceeds DRAM capacity "
                f"({capacity})"
            )
        self.mem_ctrl = Ramulator2(
            ramulator_config=json.dumps(config),
            max_outstanding=max_outstanding,
        )

    def incorporate_memory(self, board: AbstractBoard) -> None:
        pass

    def get_mem_ports(self) -> Sequence[Tuple[AddrRange, Port]]:
        return [(self.mem_ctrl.range, self.mem_ctrl.port)]

    def get_memory_controllers(self) -> List[AbstractMemory]:
        return [self.mem_ctrl]

    def get_mem_interfaces(self) -> List[AbstractMemory]:
        return [self.mem_ctrl]

    def get_size(self) -> int:
        return self._size

    def set_memory_range(self, ranges: List[AddrRange]) -> None:
        if (
            len(ranges) != 1
            or ranges[0].size() != self._size
            or ranges[0].intlvBits
        ):
            raise ValueError(
                "Ramulator2 requires one contiguous range matching its size"
            )
        self.mem_ctrl.range = ranges[0]

    def get_uninterleaved_range(self) -> List[AddrRange]:
        return [self.mem_ctrl.range]

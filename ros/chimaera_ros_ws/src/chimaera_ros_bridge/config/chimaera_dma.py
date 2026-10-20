"""Coherent DMA wiring and paused-boundary admission for fresh-boot sessions."""
from m5.objects import ChimaeraDma, X86IntelMPIOIntAssignment
from gem5.components.cachehierarchies.ruby.mesi_two_level_cache_hierarchy import MESITwoLevelCacheHierarchy
from chimaera_gem5 import TimingServer


class DmaCacheHierarchy(MESITwoLevelCacheHierarchy):
    def incorporate_cache(self, board):
        super().incorporate_cache(board)
        for controller in self._dma_controllers:
            controller.dma_sequencer.max_outstanding_requests = 64


def attach_dma(board, h2g='/tmp/chimaera_dma_h2g', g2h='/tmp/chimaera_dma_g2h'):
    # Call after set_kernel_disk_workload has constructed the PC platform.
    board.chimaera_dma = ChimaeraDma(
        pci_dev=5, pci_func=0, upstream=board.pc.pci_host,
        h2g_mailbox=h2g, g2h_mailbox=g2h, mailbox_slots=16)
    board.pc.attachPciDevice(board.chimaera_dma)
    assignment = X86IntelMPIOIntAssignment(
        interrupt_type='INT', polarity='ConformPolarity', trigger='ConformTrigger',
        source_bus_id=0, source_bus_irq=5 << 2,
        dest_io_apic_id=int(board.pc.south_bridge.io_apic.apic_id), dest_io_apic_intin=17)
    board.chimaera_mp_irq = assignment
    board.workload.intel_mp_table.base_entries = [
        *board.workload.intel_mp_table.base_entries, assignment]


class DmaTimingServer(TimingServer):
    def __init__(self, board, **kwargs):
        super().__init__(**kwargs)
        self.board = board

    def handle_command(self, line):
        if line == 'CHIMAERA_INGRESS':
            if not self.roi_started or self.finished:
                raise ValueError('DMA ingress requires a live, paused ROI')
            self.board.chimaera_dma.ingress()
            return 'OK INGRESS'
        return super().handle_command(line)

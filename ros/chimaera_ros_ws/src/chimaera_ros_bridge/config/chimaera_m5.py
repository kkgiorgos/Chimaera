"""Functional DDS payload transport with interrupt-only Linux notifications."""
from m5.objects import ChimaeraM5, X86IntelMPIOIntAssignment
from chimaera_gem5 import TimingServer


def attach_m5(board, h2g='/tmp/chimaera_dma_h2g', g2h='/tmp/chimaera_dma_g2h'):
    board.chimaera_m5 = ChimaeraM5(
        pci_dev=5, pci_func=0, upstream=board.pc.pci_host,
        h2g_mailbox=h2g, g2h_mailbox=g2h, mailbox_slots=16)
    board.pc.attachPciDevice(board.chimaera_m5)
    board.chimaera_mp_irq = X86IntelMPIOIntAssignment(
        interrupt_type='INT', polarity='ConformPolarity', trigger='ConformTrigger',
        source_bus_id=0, source_bus_irq=5 << 2,
        dest_io_apic_id=int(board.pc.south_bridge.io_apic.apic_id), dest_io_apic_intin=17)
    board.workload.intel_mp_table.base_entries = [
        *board.workload.intel_mp_table.base_entries, board.chimaera_mp_irq]


class M5TimingServer(TimingServer):
    def __init__(self, board, **kwargs):
        super().__init__(**kwargs)
        self.board = board

    def handle_command(self, line):
        if line == 'CHIMAERA_EGRESS':
            if not self.roi_started or self.finished:
                raise ValueError('m5ops egress requires a live, paused ROI')
            return 'EGRESS ' + str(self.board.chimaera_m5.flush_egress())
        if line == 'CHIMAERA_INGRESS':
            if not self.roi_started or self.finished:
                raise ValueError('m5ops ingress requires a live, paused ROI')
            self.board.chimaera_m5.ingress()
            return 'OK INGRESS'
        return super().handle_command(line)

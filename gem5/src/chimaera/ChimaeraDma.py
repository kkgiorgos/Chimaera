from m5.objects.PciDevice import PciEndpoint, PciMemBar
from m5.params import *
from m5.SimObject import PyBindMethod


class ChimaeraDma(PciEndpoint):
    type = "ChimaeraDma"
    cxx_class = "gem5::chimaera::ChimaeraDma"
    cxx_header = "chimaera/dma_device.hh"

    VendorID = 0x1AF4
    DeviceID = 0xC101
    SubsystemVendorID = 0x1AF4
    SubsystemID = 0xC101
    ClassCode = 0xFF
    BAR0 = PciMemBar(size="4KiB")
    InterruptPin = 1
    InterruptLine = 17

    loopback = Param.Bool(False, "Echo TX into RX for device validation")
    h2g_mailbox = Param.String("/tmp/chimaera_dma_h2g", "Host input mailbox")
    g2h_mailbox = Param.String("/tmp/chimaera_dma_g2h", "Host output mailbox")
    max_pending = Param.Unsigned(64, "Maximum queued receive descriptors")
    mailbox_slots = Param.Unsigned(2, "Slots in each host mailbox (must match host controller)")

    cxx_exports = [PyBindMethod("ingress")]

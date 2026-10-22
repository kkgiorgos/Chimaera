from m5.objects.PciDevice import PciEndpoint, PciMemBar
from m5.params import *
from m5.SimObject import PyBindMethod


class ChimaeraM5(PciEndpoint):
    type = "ChimaeraM5"
    cxx_class = "gem5::chimaera::ChimaeraM5"
    cxx_header = "chimaera/m5_device.hh"

    VendorID = 0x1AF4
    DeviceID = 0xC102
    SubsystemVendorID = 0x1AF4
    SubsystemID = 0xC102
    ClassCode = 0xFF
    BAR0 = PciMemBar(size="16KiB")
    InterruptPin = 1
    InterruptLine = 17

    h2g_mailbox = Param.String("/tmp/chimaera_m5_h2g", "Raw CDDS input mailbox base")
    g2h_mailbox = Param.String("/tmp/chimaera_m5_g2h", "Raw CDDS output mailbox base")
    mailbox_slots = Param.Unsigned(16, "Slots per lane; must match host")
    max_pending = Param.Unsigned(64, "Maximum pending records per context/lane queue")
    max_pending_bytes = Param.MemorySize("64MiB", "Maximum queued bytes per context/lane")
    ingress_records = Param.Unsigned(64, "Maximum records admitted per lane per boundary")
    cxx_exports = [PyBindMethod("ingress"), PyBindMethod("flush_egress"),
                   PyBindMethod("diagnostics")]

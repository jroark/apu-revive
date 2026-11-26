"""USB access to the APU via libusb (pyusb). HARDWARE-UNTESTED as of writing."""
import time

import usb.core
import usb.util

from . import firmware, protocol

VID, PID = 0x0799, 0x7651
CPUCS = 0x7F92           # EZ-USB CPU control/status register
REQ_FW_LOAD = 0xA0       # EZ-USB built-in "firmware load" vendor request

# Endpoint numbers. The firmware supplies no descriptors, so the chip uses the
# EZ-USB default ones; the Windows client uses ezusb.sys pipe indices
# 1/3/5 (IN) and 2/4 (OUT). These addresses are the expected mapping and are
# checked against the real descriptors in Apu.open().
EP_CMD_OUT, EP_CMD_IN = 0x02, 0x82
EP_CTL_OUT, EP_CTL_IN = 0x04, 0x84
TIMEOUT_MS = 5000


class ApuError(Exception):
    pass


class Apu:
    def __init__(self, dev):
        self.dev = dev

    @classmethod
    def open(cls):
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise ApuError("APU (0799:7651) not found on USB")
        dev.set_configuration()
        cfg = dev.get_active_configuration()
        want = {EP_CMD_OUT, EP_CMD_IN, EP_CTL_OUT, EP_CTL_IN}
        for intf in cfg:
            have = {ep.bEndpointAddress for ep in intf}
            if want <= have:
                dev.set_interface_altsetting(intf.bInterfaceNumber, intf.bAlternateSetting)
                # SET_INTERFACE resets the host's data toggles but not the EZ-USB's,
                # so after a previous session OUT packets are silently dropped.
                # CLEAR_FEATURE(ENDPOINT_HALT) resets the device side too.
                for ep in sorted(want):
                    dev.ctrl_transfer(0x02, 0x01, 0, ep, None, TIMEOUT_MS)
                return cls(dev)
        raise ApuError("expected endpoints not present; run `apu info` and compare")

    # --- EZ-USB firmware -----------------------------------------------------
    def _cpu_reset(self, hold: bool):
        self.dev.ctrl_transfer(0x40, REQ_FW_LOAD, CPUCS, 0, bytes([1 if hold else 0]), TIMEOUT_MS)

    def load_firmware(self, drv: bytes):
        self._cpu_reset(True)
        for addr, data in firmware.records(drv):
            self.dev.ctrl_transfer(0x40, REQ_FW_LOAD, addr, 0, data, TIMEOUT_MS)
        self._cpu_reset(False)
        time.sleep(0.2)

    # --- control pipe ----------------------------------------------------------
    def ctl(self, cmd: int) -> int:
        self.dev.write(EP_CTL_OUT, bytes((0x00, cmd)), TIMEOUT_MS)
        return self.dev.read(EP_CTL_IN, 1, TIMEOUT_MS)[0]

    def has_9v(self) -> bool:
        """The Windows client's external-supply check: status bit 0x40 of ctl(0x94)."""
        return bool(self.ctl(protocol.CTL_CONFIG_END) & 0x40)

    # --- FPGA ------------------------------------------------------------------
    def configure_fpga(self, rbf: bytes):
        if not self.has_9v():
            raise ApuError("9 V supply not detected; not configuring the FPGA")
        if self.ctl(protocol.CTL_CONFIG_START) & protocol.ST_MASK:
            raise ApuError("FPGA did not enter configuration (is 9 V power connected?)")
        for chunk in protocol.rbf_chunks(rbf):
            if self.ctl(protocol.CTL_CONFIG_DATA) & protocol.ST_MASK != 0x08:
                raise ApuError("FPGA not ready for configuration data")
            self.dev.write(EP_CMD_OUT, chunk, TIMEOUT_MS)
        if self.ctl(protocol.CTL_CONFIG_END) & protocol.ST_MASK != protocol.ST_MASK:
            raise ApuError("FPGA configuration failed (CONF_DONE not seen)")

    # --- port I/O --------------------------------------------------------------
    def xfer(self, words) -> bytes:
        wire, reads = protocol.encode(words)
        if not reads:
            self.dev.write(EP_CMD_OUT, protocol.pad(wire), TIMEOUT_MS)
            return b""
        self.dev.write(EP_CMD_OUT, protocol.pad_with_reads(wire, reads), TIMEOUT_MS)
        return bytes(self.dev.read(EP_CMD_IN, reads, TIMEOUT_MS))

    def write_port(self, addr: int, data: int):
        self.xfer([protocol.word(protocol.OP_WRITE, addr, data)])

    def read_port(self, addr: int) -> int:
        return self.xfer([protocol.word(protocol.OP_READ, addr)])[0]

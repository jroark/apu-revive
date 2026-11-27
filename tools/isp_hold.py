#!/usr/bin/env python3
"""Hold the APU's ISP (JTAG) lines at static levels for measuring at the socket.

    python tools/isp_hold.py [PART]   (default EPM7064SLC44-10; run with the socket empty)

Sets the unit up as Altera's module does for the part's ISP path (pgd_setup(34, 1), select,
pgd_program_setup(0, nominal VCC)), prints the socket-supply ADC (channel 4, 255 = 5 V), then:
  state A: TMS, TDI high (port 05 bits 5, 0), TCK high (port 0B bit 3; also port 08 = 07)   until $APU_HOLD_DIR/apu_isp_B exists
  state B: all three low   until $APU_HOLD_DIR/apu_isp_done exists
Ports from the module's JTAG pin table for adapter 9 (PLMJ7000-44, table at 0x1003aa60: APU pins
TDI 16, TCK 43, TMS 21, TDO 48; port = 3 + pin / 8 below pin 40, else 6 + pin / 8), after the
module's socket preamble. Nothing is
shifted into a device (no IR/DR scans), so nothing is erased or programmed.
"""
import os
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402

ADC = 0x10007ad0
_DIR = os.environ.get("APU_HOLD_DIR", ".")
B, DONE = os.path.join(_DIR, "apu_isp_B"), os.path.join(_DIR, "apu_isp_done")


OPCODE = {"W": 0x00, "W5": 0x40, "W8": 0xC0}
# the module's socket preamble before its first TCK pulse, as captured on the real unit
# (pgd_read_silicon_id with the ISP flag): pin drivers on for ports 03-07, 0B, 0C (bit 0 =
# TDO stays an input), 0D; then the W8 / 20-38 writes, which look like the FPGA's pin matrix
PREAMBLE = [("W", 0x00, 0x10),
            ("W5", 0x03, 0xFF), ("W5", 0x04, 0xFF), ("W5", 0x05, 0xFF), ("W5", 0x06, 0xFF),
            ("W5", 0x07, 0xFF), ("W5", 0x0B, 0xFF), ("W5", 0x0C, 0xFF), ("W5", 0x0D, 0xFF),
            ("W5", 0x0C, 0xFE), ("W", 0x03, 0x01),
            ("W8", 0x00, 0x00), ("W8", 0x01, 0x00), ("W8", 0x02, 0x00), ("W8", 0x03, 0x00),
            ("W", 0x20, 0x00), ("W", 0x28, 0x00), ("W", 0x30, 0x00), ("W", 0x38, 0x00),
            ("W8", 0x00, 0x58)]


def port_ops(h, ops):
    body = b"".join(bytes([OPCODE[k] | a, v]) for k, a, v in ops)
    if len(body) % 64 == 62:
        body += bytes([0x40, 0xFF])                       # padding op the module also uses
    h.device.write(2, body + b"\0\0")                    # read count 0


def port_write(h, addr, value):
    port_ops(h, [("W", addr, value)])


def wait_for(path):
    while not os.path.exists(path):
        time.sleep(0.2)


def main():
    for p in (B, DONE):
        if os.path.exists(p):
            os.remove(p)
    path, part_id = lookup_part(sys.argv[1] if len(sys.argv) > 1 else "EPM7064SLC44-10")
    pcf = parse_pcf(path)
    h = harness.Harness(V + "/pgdmpuz.drv", verbose=False)
    h.device = harness.UsbApu(h.log)
    h.realtime = True
    h.dllmain()
    h.call("pgd_setup", 12, 0x44, h.alloc(1024))
    if h.call("pgd_setup", 1, 0x280, h.put((V + "/pgdmpuz.rbf").encode())) != 1:
        sys.exit("init failed")
    try:
        apply_calibration(h)
        print("isp ->", h.call("pgd_setup", 34, 1, 0))
        print("select ->", h.call("pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16))
        print("program_setup(0, vcc %d) ->" % pcf["nominal_vcc"], h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0))
        print("ADC (ch 4 = socket supply):", [h.call(ADC, c) for c in range(8)], flush=True)
        h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"])
        port_ops(h, PREAMBLE)
        port_ops(h, [("W", 0x05, 0x21), ("W", 0x0B, 0x08), ("W", 0x08, 0x07)])
        print("STATE A: TMS, TDI, TCK high", flush=True)
        wait_for(B)
        print("ADC:", [h.call(ADC, c) for c in range(8)], flush=True)
        port_ops(h, [("W", 0x05, 0x00), ("W", 0x0B, 0x00), ("W", 0x08, 0x03)])
        print("STATE B: TMS, TDI, TCK low", flush=True)
        wait_for(DONE)
        print("ADC:", [h.call(ADC, c) for c in range(8)], flush=True)
    finally:
        h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Read a MAX 7000S chip's JTAG IDCODE in the APU socket by driving the JTAG pins directly (read-only).

    python tools/apu_jtag_id.py [PART]       default EPM7064SLC44-10, PLMJ7000-44 adapter (ID 9)

The vendor module is used only to bring the unit up for the part (FPGA, calibration, socket
supply: pgd_setup(34, 1), select, pgd_program_setup(0) and (1)) and for its socket preamble (pin
drivers, pin matrix; tools/isp_hold.py). The JTAG pins are then the ones the module's own pin table
(0x1003aa60) gives for adapter 9: TDI = port 05 bit 0, TMS = port 05 bit 5, TCK = port 0B bit 3,
TDO = port 0C bit 0 (measured at the socket on 2026-11-26: PLCC pins 7, 13 and 32, 3.3 V logic).
Only Test-Logic-Reset and a 32-bit DR shift (the IDCODE register after reset) are clocked.
"""
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from isp_hold import PREAMBLE, port_ops  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402

from apu import protocol  # noqa: E402

TMS, TDI = 0x20, 0x01        # port 05
TCK = 0x08                   # port 0B
W, R = protocol.OP_WRITE, protocol.OP_READ


def clock(words, tms, tdi=1, read=False):
    """One TCK cycle: set TMS/TDI, sample TDO (before the rising edge), TCK high, TCK low."""
    words.append(protocol.word(W, 0x05, (TMS if tms else 0) | (TDI if tdi else 0)))
    if read:
        words.append(protocol.word(R, 0x0C))
    words.append(protocol.word(W, 0x0B, TCK))
    words.append(protocol.word(W, 0x0B, 0))


def read_idcode(apu, repeat=1):
    words = []
    for _ in range(6):
        clock(words, 1)                                # Test-Logic-Reset
    for tms in (0, 1, 0, 0):                           # Idle, Select-DR, Capture-DR, Shift-DR
        clock(words, tms)
    for i in range(32 * repeat):
        clock(words, i == 32 * repeat - 1, 1, read=True)
    clock(words, 1)                                    # Update-DR
    clock(words, 0)                                    # Idle
    data = apu.xfer(words)
    bits = [b & 1 for b in data]
    return [sum(bits[32 * k + i] << i for i in range(32)) for k in range(repeat)], data


def main():
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
        h.call("pgd_setup", 34, 1, 0)
        h.call("pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16)
        h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0)
        h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"])
        port_ops(h, PREAMBLE)
        print("socket supply ADC ch4:", h.call(0x10007ad0, 4))
        codes, raw = read_idcode(h.device.apu, repeat=2)
        print("raw port 0C bytes:", raw[:8].hex(), "... distinct:", sorted(set(raw)))
        for c in codes:
            print("IDCODE %08X: version %X, part %04X, manufacturer %03X (Altera = 06E), marker %d"
                  % (c, c >> 28, (c >> 12) & 0xFFFF, (c >> 1) & 0x7FF, c & 1))
    finally:
        h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))


if __name__ == "__main__":
    main()

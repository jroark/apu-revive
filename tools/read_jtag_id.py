#!/usr/bin/env python3
"""Read a MAX 7000S device's JTAG IDCODE through Altera's module on the real unit (read-only).

usage: read_jtag_id.py PART_NAME [--setup1] [--isp]      e.g. EPM7064SLC44-10
After a Test-Logic-Reset the IDCODE register is selected (IEEE 1149.1), so: TMS high five
times, then Run-Test/Idle, Select-DR, Capture-DR, Shift-DR, and 32 bits out on TDO (LSB first),
using the module's pgd_jtag_io(tms, tdi, read_tdo), which clocks one bit and returns TDO.
"""
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402


def main():
    path, part_id = lookup_part(sys.argv[1])
    pcf = parse_pcf(path)
    h = harness.Harness(V + "/pgdmpuz.drv", verbose=False)
    h.device = harness.UsbApu(h.log)
    h.realtime = True
    h.dllmain()
    h.call("pgd_setup", 12, 0x44, h.alloc(1024))
    if h.call("pgd_setup", 1, 0x280, h.put((V + "/pgdmpuz.rbf").encode())) != 1:
        sys.exit("init failed")
    try:
        apply_calibration(h, announce=True)
        if "--isp" in sys.argv:
            print("isp mode ->", h.call("pgd_setup", 34, 1, 0))     # the module's ISP flag (0x1003ba1c)
        print("select ->", h.call("pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16), "type", hex(h.u32(0x1003B9D0)))
        print("program_setup(0, vcc) ->", h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0))
        if "--setup1" in sys.argv:
            print("program_setup(1) ->", h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"]))
        io = lambda tms, tdi=0, read=0: h.call("pgd_jtag_io", tms, tdi, read)
        for _ in range(6):
            io(1)                                         # Test-Logic-Reset
        for tms in (0, 1, 0, 0):                          # Idle, Select-DR, Capture-DR, Shift-DR
            io(tms)
        bits = [io(1 if i == 31 else 0, 1, 1) & 1 for i in range(32)]
        io(1), io(0)                                      # Update-DR, Idle
        for _ in range(6):
            io(1)
        idcode = sum(b << i for i, b in enumerate(bits))
        print("TDO bits (LSB first):", "".join(map(str, bits)))
        print("IDCODE %08X: version %X, part %04X, manufacturer %03X (Altera = 06E), marker bit %d" % (
            idcode, idcode >> 28, (idcode >> 12) & 0xFFFF, (idcode >> 1) & 0x7FF, idcode & 1))
    finally:
        if "--isp" in sys.argv:
            h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))


if __name__ == "__main__":
    main()

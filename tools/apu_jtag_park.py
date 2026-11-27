#!/usr/bin/env python3
"""Park a MAX 7000S chip in the APU socket in Shift-DR with TDI = 1, so a responding chip drives
TDO (PLCC pin 38) steadily high; read ports 03-0F and hold until $APU_HOLD_DIR/apu_isp_done
exists (read-only: no instruction is loaded; the IDCODE register just fills with ones)."""
import os
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from apu_jtag_id import clock  # noqa: E402
from isp_hold import DONE, PREAMBLE, port_ops  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402

from apu import protocol  # noqa: E402


def main():
    if os.path.exists(DONE):
        os.remove(DONE)
    path, part_id = lookup_part("EPM7064SLC44-10")
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
        apu = h.device.apu
        reads = lambda: list(apu.xfer([protocol.word(protocol.OP_READ, a) for a in range(3, 16)]))
        print("ports 03-0F before:", " ".join("%02x" % v for v in reads()))
        words = []
        for _ in range(6):
            clock(words, 1)
        for tms in (0, 1, 0, 0):
            clock(words, tms)
        for _ in range(40):
            clock(words, 0, 1)                 # stay in Shift-DR, shifting ones
        apu.xfer(words)
        print("ports 03-0F parked:", " ".join("%02x" % v for v in reads()), flush=True)
        print("PARKED", flush=True)
        while not os.path.exists(DONE):
            time.sleep(0.2)
        words = []
        for _ in range(6):
            clock(words, 1)                    # back to Test-Logic-Reset
        apu.xfer(words)
    finally:
        h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0), flush=True)


if __name__ == "__main__":
    main()

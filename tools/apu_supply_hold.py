#!/usr/bin/env python3
"""Power the socket exactly as pgm.dll does before an ID read and hold it, for measuring the
supply and ground pins (socket EMPTY).  python tools/apu_supply_hold.py [PART] [VARIANT]
Calls: pgd_setup(6, part, vcc << 16 | variant), pgd_program_setup(0, 0, vcc, 0, 0, 0),
pgd_program_setup(1, 0, <the variant's ID voltages>); holds until $APU_HOLD_DIR/apu_isp_done."""
import os
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from isp_hold import DONE  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402


def main():
    part = sys.argv[1] if len(sys.argv) > 1 else "EPM7064SLC44-10"
    if os.path.exists(DONE):
        os.remove(DONE)
    path, part_id = lookup_part(part)
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
        vcc = pcf["nominal_vcc"]
        h.call("pgd_setup", 6, part_id, vcc << 16)
        h.call("pgd_program_setup", 0, 0, vcc, 0, 0, 0)
        h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"])
        print("HOLDING; ADC:", [h.call(0x10007ad0, c) for c in range(8)], flush=True)
        while not os.path.exists(DONE):
            time.sleep(0.2)
    finally:
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0), flush=True)


if __name__ == "__main__":
    main()

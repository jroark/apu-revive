#!/usr/bin/env python3
"""Drive chosen APU port bits at the socket and hold them, for finding which socket pin a port bit
reaches with a meter (socket EMPTY).   python tools/apu_pin_hold.py 0C=08 [05=00 ...]
Same set-up as tools/isp_hold.py (part EPM7064SLC44-10, socket supply on, the module's socket
preamble: pin drivers on for ports 03-07, 0B, 0C (bit 0 input), 0D), then the given port values;
holds until $APU_HOLD_DIR/apu_isp_done exists, then sets those ports back to 0 and powers down."""
import os
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from isp_hold import DONE, PREAMBLE, port_ops  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402


def main():
    values = [(int(a, 16), int(b, 16)) for a, b in (x.split("=") for x in sys.argv[1:])]
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
        port_ops(h, PREAMBLE + [("W", a, v) for a, v in values])
        print("HOLDING", " ".join("%02X=%02X" % av for av in values), flush=True)
        while not os.path.exists(DONE):
            time.sleep(0.2)
        port_ops(h, [("W", a, 0) for a, _ in values])
    finally:
        h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0), flush=True)


if __name__ == "__main__":
    main()

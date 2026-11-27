#!/usr/bin/env python3
"""Hand-held loop-back test (socket EMPTY): for SECONDS (default 60) toggle a driver pin (default
TMS, PLCC pin 13, port 05 bit 5; --tdi: TDI, PLCC pin 7, port 05 bit 0) every 0.25 s and read ports 03-0F after each change; at the end report every port
bit that followed TMS for a while, so a resistor held across the driver pin and the pin under test by hand is enough.
--chip-inputs: after the preamble, set the drive enables the module uses for the EPM7064S ID read
(0B = F7, 0C = 07), so PLCC 32 (0B bit 3) and 41 (0C bit 3) are inputs as in that read."""
import collections
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from apu_jtag_id import TMS  # noqa: E402
from isp_hold import PREAMBLE, port_ops  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402

from apu import protocol  # noqa: E402

PORTS = list(range(3, 16))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    seconds = float(args[0]) if args else 60
    drv = 0x01 if "--tdi" in sys.argv else TMS
    extra = [("W5", 0x0B, 0xF7), ("W5", 0x0C, 0x07)] if "--chip-inputs" in sys.argv else []
    path, part_id = lookup_part("EPM7064SLC44-10")
    pcf = parse_pcf(path)
    h = harness.Harness(V + "/pgdmpuz.drv", verbose=False)
    h.device = harness.UsbApu(h.log)
    h.realtime = True
    h.dllmain()
    h.call("pgd_setup", 12, 0x44, h.alloc(1024))
    if h.call("pgd_setup", 1, 0x280, h.put((V + "/pgdmpuz.rbf").encode())) != 1:
        sys.exit("init failed")
    follow = collections.Counter()
    samples = 0
    try:
        apply_calibration(h)
        h.call("pgd_setup", 34, 1, 0)
        h.call("pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16)
        h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0)
        h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"])
        port_ops(h, PREAMBLE + extra)
        apu = h.device.apu
        print("WATCHING for %d s: hold the resistor now" % seconds, flush=True)
        end, v = time.time() + seconds, 0
        while time.time() < end:
            v ^= 1
            data = apu.xfer([protocol.word(protocol.OP_WRITE, 0x05, drv if v else 0), protocol.word(protocol.OP_NOP)]
                            + [protocol.word(protocol.OP_READ, p) for p in PORTS])
            samples += 1
            for pi, p in enumerate(PORTS):
                for b in range(8):
                    if not (p == 0x05 and (1 << b) == drv) and ((data[pi] >> b) & 1) == v:
                        follow[(p, b)] += 1
            time.sleep(0.25)
        port_ops(h, [("W", 0x05, 0x00)])
    finally:
        h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))
    # a bit stuck at one level matches half the samples; one that followed TMS matches more
    print("samples:", samples)
    for (p, b), n in sorted(follow.items(), key=lambda x: -x[1])[:8]:
        print("   port %02X bit %d matched TMS in %d of %d samples" % (p, b, n, samples))


if __name__ == "__main__":
    main()

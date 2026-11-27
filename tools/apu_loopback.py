#!/usr/bin/env python3
"""Loop-back test of the APU's socket JTAG path: with the socket EMPTY and PLCC pin 13 (TMS)
linked to pin 38 (TDO) through ~1 kohm, toggle TMS (port 05 bit 5) and report which port bits
(03-0F) follow it, with port 0C's driver enable as the vendor sets it (FE) and released (00)."""
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from apu_jtag_id import TMS  # noqa: E402
from isp_hold import PREAMBLE, port_ops  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402

from apu import protocol  # noqa: E402

PORTS = list(range(3, 0x40)) if "--wide" in sys.argv else list(range(3, 16))
PATTERN = [0, 1, 1, 0, 1, 0, 0, 1, 0, 1]


def main():
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
        for name, extra in (("0C enable FE (vendor)", []), ("0C enable 00", [("W5", 0x0C, 0x00)])):
            port_ops(h, PREAMBLE + extra)
            words = []
            for v in PATTERN:
                words.append(protocol.word(protocol.OP_WRITE, 0x05, TMS if v else 0))
                words.append(protocol.word(protocol.OP_NOP))
                words.extend(protocol.word(protocol.OP_READ, p) for p in PORTS)
            data = h.device.apu.xfer(words)
            rows = [data[k * len(PORTS):(k + 1) * len(PORTS)] for k in range(len(PATTERN))]
            print("==", name)
            for pi, p in enumerate(PORTS):
                for b in range(8):
                    seq = [(r[pi] >> b) & 1 for r in rows]
                    if len(set(seq)) > 1:
                        tag = "follows TMS" if seq == PATTERN else "inverted" if seq == [1 - x for x in PATTERN] else "changes"
                        print("   port %02X bit %d %s: %s" % (p, b, tag, "".join(map(str, seq))))
            port_ops(h, [("W", 0x05, 0x00)])
    finally:
        h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Look for a MAX 7000S chip's TDO in the APU socket under several pin-driver settings (read-only).

For each setting: Test-Logic-Reset, Shift-DR, 64 clocks with TDI = 1 (IDCODE then ones), reading
ports 03-0F before every rising edge; any port bit whose sequence holds 0x070640DD is reported.
"""
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from apu_jtag_id import TCK, TDI, TMS  # noqa: E402
from isp_hold import PREAMBLE, port_ops  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402

from apu import protocol  # noqa: E402

PORTS = list(range(3, 16))
VARIANTS = {
    "vendor drive (0C = FE)": [],
    "port 0C released (W5 0C = 00)": [("W5", 0x0C, 0x00)],
    "only TCK/TMS/TDI driven": [("W5", p, 0x00) for p in (3, 4, 6, 7, 0x0C, 0x0D)]
                               + [("W5", 0x05, TMS | TDI), ("W5", 0x0B, TCK)],
}


def scan(apu):
    W, R = protocol.OP_WRITE, protocol.OP_READ
    words, n = [], 0

    def clock(tms, tdi=1, read=False):
        nonlocal n
        words.append(protocol.word(W, 0x05, (TMS if tms else 0) | (TDI if tdi else 0)))
        if read:
            words.extend(protocol.word(R, p) for p in PORTS)
            n += 1
        words.append(protocol.word(W, 0x0B, TCK))
        words.append(protocol.word(W, 0x0B, 0))
    for _ in range(6):
        clock(1)
    for tms in (0, 1, 0, 0):
        clock(tms)
    for i in range(64):
        clock(i == 63, 1, read=True)
    clock(1), clock(0)
    for _ in range(6):
        clock(1)
    data = apu.xfer(words)
    return [data[k * len(PORTS):(k + 1) * len(PORTS)] for k in range(n)]


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
        print("socket supply ADC ch4:", h.call(0x10007ad0, 4))
        for name, extra in VARIANTS.items():
            port_ops(h, PREAMBLE + extra)
            rows = scan(h.device.apu)
            print("==", name)
            found = False
            for pi, p in enumerate(PORTS):
                for b in range(8):
                    seq = [(r[pi] >> b) & 1 for r in rows]
                    if len(set(seq)) > 1:
                        v = sum(seq[i] << i for i in range(32))
                        found = True
                        print("   port %02X bit %d toggles: first 32 bits -> %08X, rest %s" % (
                            p, b, v, "".join(map(str, seq[32:]))))
            if not found:
                print("   no port bit changes; constant reads:", " ".join("%02x" % v for v in rows[0]))
    finally:
        h.call("pgd_setup", 34, 0, 0)
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))


if __name__ == "__main__":
    main()

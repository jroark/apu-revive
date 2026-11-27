#!/usr/bin/env python3
"""Try reading a MAX 7000S IDCODE in the APU socket under several set-ups (read-only).
Each variant powers up from scratch: supply on (pgd_program_setup(0)), optionally the part's
silicon-ID voltages (pgd_program_setup(1)), optionally 5 V pin levels (pgd_setup(13, 5000)),
the socket preamble, then Test-Logic-Reset and a 64-bit DR shift (IDCODE, then TDI's ones).
Run one variant per process: python tools/apu_jtag_variants.py 0 (or 1, 2, 3)."""
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness  # noqa: E402
from apu_jtag_id import TCK, TDI, TMS  # noqa: E402
from isp_hold import PREAMBLE, port_ops  # noqa: E402
from read_siid import V, apply_calibration, lookup_part, parse_pcf  # noqa: E402

from apu import protocol  # noqa: E402

VARIANTS = [  # (name, program_setup(1), 5 V pin level, NOPs per half clock)
    ("no programming voltages", False, False, 0),
    ("no programming voltages, 5 V pin levels", False, True, 0),
    ("no programming voltages, 5 V levels, slow TCK", False, True, 20),
    ("as before (with program_setup(1))", True, False, 0),
]


def idcode(apu, nops):
    W, R, N = protocol.OP_WRITE, protocol.OP_READ, protocol.OP_NOP
    words = []

    def clock(tms, tdi=1, read=False):
        words.append(protocol.word(W, 0x05, (TMS if tms else 0) | (TDI if tdi else 0)))
        words.extend([protocol.word(N)] * nops)
        if read:
            words.append(protocol.word(R, 0x0C))
        words.append(protocol.word(W, 0x0B, TCK))
        words.extend([protocol.word(N)] * nops)
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
    bits = [b & 1 for b in apu.xfer(words)]
    return sum(bits[i] << i for i in range(32)), "".join(map(str, bits[32:]))


def main():
    path, part_id = lookup_part("EPM7064SLC44-10")
    pcf = parse_pcf(path)
    picks = [int(a) for a in sys.argv[1:]] or range(len(VARIANTS))
    for name, setup1, level5, nops in [VARIANTS[i] for i in picks]:
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
            if level5:
                h.call("pgd_setup", 13, 5000, 0)
            h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0)
            if setup1:
                h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"])
            port_ops(h, PREAMBLE)
            code, rest = idcode(h.device.apu, nops)
            print("%-48s ADC ch4 %3d  IDCODE %08X  then %s%s" % (
                name, h.call(0x10007ad0, 4), code, rest, "   <-- EPM7064S" if code == 0x070640DD else ""), flush=True)
        finally:
            h.call("pgd_setup", 34, 0, 0)
            h.call("pgd_setup", 3, 0, 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Probe pgd_read_bytes_on_column: pgm.dll calls pgd_set_column_address(R) then
pgd_read_bytes_on_column(start, count, buf1, buf2). (Altera's "column" is what the notes call a row.)
usage: bulk_probe.py PART ROW START COUNT [--real] [--fill N]
"""
import struct
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness
import read_siid as rs
from apu.pcf import Pcf


def open_device(part, real):
    path, pid = rs.lookup_part(part)
    p = Pcf.load(path)
    if real:
        h = harness.Harness(rs.V + "/pgdmpuz.drv", verbose=False)
        h.device = harness.UsbApu(lambda m: None)
        h.realtime = True
        h.dllmain()
        h.call("pgd_setup", 12, 0x44, h.alloc(1024))
        if h.call("pgd_setup", 1, 0x280, h.put((rs.V + "/pgdmpuz.rbf").encode())) != 1:
            sys.exit("init failed")
        rs.apply_calibration(h)
    else:
        h = harness.boot_sim()
    h.log = lambda m: None
    assert h.call("pgd_setup", 6, pid, p.nominal_vcc() << 16) == 1
    assert h.call("pgd_program_setup", 0, 0, p.nominal_vcc(), 0, 0, 0) == 1
    assert h.call("pgd_program_setup", 1, 0, *p.siid_volts()) == 1
    return h, p


def bulk_read(h, row, start, count, fill=0):
    n = count + 8
    b1 = h.alloc(4 * n); b2 = h.alloc(4 * n)
    h.uc.mem_write(b1, struct.pack("<I", fill) * n)
    h.uc.mem_write(b2, struct.pack("<I", 0xDEADBEEF) * n)
    h.call("pgd_set_column_address", row)
    r = h.call("pgd_read_bytes_on_column", start, count, b1, b2)
    return r, struct.unpack(f"<{n}I", h.uc.mem_read(b1, 4 * n)), struct.unpack(f"<{n}I", h.uc.mem_read(b2, 4 * n))


if __name__ == "__main__":
    part, row, start, count = sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0), int(sys.argv[4], 0)
    fill = int(sys.argv[sys.argv.index("--fill") + 1], 0) if "--fill" in sys.argv else 0
    h, p = open_device(part, "--real" in sys.argv)
    try:
        r, b1, b2 = bulk_read(h, row, start, count, fill)
        print("ret", r)
        print("buf1:", " ".join("%x" % v for v in b1))
        print("buf2:", " ".join("%x" % v for v in b2))
    finally:
        print("shutdown", h.call("pgd_setup", 3, 0, 0))

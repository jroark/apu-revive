#!/usr/bin/env python3
"""Write a read-back image (read_bulk.py JSON) into a blank chip, line by line. EXPERIMENTAL.

usage: program_image.py PART_NAME IMAGE.json --yes-really-program
1. pgm.dll's own ID check must pass; it also reads the chip's Vpp / Tprogram values.
2. Voltage set-up as in pgm.dll's row write: program_setup(3, Tprogram, min(vcc, 4750), 2000, Vpp, 0).
3. For every line with data: pgd_set_column_address(row);
   pgd_program_bytes_on_column(0, width, mask[], data[], 0, 0). Bits that are 1 are left alone.
4. Module shutdown. Verify afterwards with read_bulk.py.
"""
import json
import struct
import sys
import time

sys.path.insert(0, "tools")
from host import Host


def main():
    if "--yes-really-program" not in sys.argv:
        sys.exit(__doc__)
    part, image = sys.argv[1], json.load(open(sys.argv[2]))
    host = Host(part, real=True)
    h = host.h
    h.call_timeout_s = 120
    done = 0
    try:
        dev = host.load_device()
        if host.call(0x1003D4BD) != 1:
            sys.exit("ID check failed; not programming")
        vcc, vpp, tprog = host.g(0x1006E480), host.g(0x1006E484), host.g(0x1006E488)
        print("ID ok, variant %d; chip parameters: vcc %d, Vpp %d, Tprogram %d; calibration %d" % (
            host.u32(dev + 0x478), vcc, vpp, tprog, h.u32(0x1003B988)), flush=True)
        assert 11000 <= vpp <= 13500 and 0 < tprog <= 500, "implausible parameters"
        assert h.call("pgd_program_setup", 3, tprog, min(vcc, 4750), 2000, vpp, 0) == 1
        t0 = time.time()
        for key in sorted(image["rows"], key=lambda k: int(k, 16)):
            r = image["rows"][key]
            data, mask, n = r["data"], r["mask"], len(r["data"])
            if all(v & mask == mask for v in data):
                continue
            mbuf, dbuf = h.alloc(4 * (n + 8)), h.alloc(4 * (n + 8))
            h.uc.mem_write(mbuf, struct.pack(f"<{n + 8}I", *([mask] * (n + 8))))
            h.uc.mem_write(dbuf, struct.pack(f"<{n + 8}I", *(data + [mask] * 8)))
            h.call("pgd_set_column_address", int(key, 16))
            if h.call("pgd_program_bytes_on_column", 0, n, mbuf, dbuf, 0, 0) != 1:
                sys.exit(f"programming line {key} failed")
            done += 1
            if done % 20 == 0:
                print("  %d lines written, %.0f s" % (done, time.time() - t0), flush=True)
        print("wrote %d lines in %.0f s" % (done, time.time() - t0))
    finally:
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0), flush=True)


if __name__ == "__main__":
    main()

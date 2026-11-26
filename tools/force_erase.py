#!/usr/bin/env python3
"""REAL bulk erase of a chip that does not identify, with erase parameters supplied by hand.

usage: force_erase.py PART_NAME VCC_MV VPP_MV TPROGRAM TERASE --yes-really-erase
This is outside Altera's flow (pgm.dll would erase with zero voltage on this branch). It runs
pgm.dll's ID check with "continue anyway", writes the four erase parameters pgm.dll would have
read from a responsive chip, and runs pgm.dll's bulk-erase routine with ONLY pgd_bulk_erase
unblocked. Program calls stay blocked. Ends with the module's shutdown call.
"""
import sys

sys.path.insert(0, "tools")
from host import Host


def main():
    if "--yes-really-erase" not in sys.argv:
        sys.exit(__doc__)
    part = sys.argv[1]
    vcc, vpp, tprog, terase = (int(x, 0) for x in sys.argv[2:6])
    host = Host(part, real=True)
    host.h.call_timeout_s = 120

    def show(n):
        for m in host.messages[n:]:
            if "read_silicon_id" in m:
                continue
            if m.startswith(("MODULE", "BLOCKED", "PGM")):
                print("  ", m[:150], flush=True)

    try:
        dev = host.load_device()
        print("delay calibration in use:", host.h.u32(0x1003B988))
        host.setg(0x1006E3EC, 1)
        host.prompt_answer = 1
        n = len(host.messages)
        r = host.call(0x1003D4BD)
        print("ID check ->", r, "| variant", host.u32(dev + 0x478))
        show(n)
        if r != 1:
            sys.exit("ID check did not return success; not erasing")
        for addr, val in ((0x1006E480, vcc), (0x1006E484, vpp), (0x1006E488, tprog), (0x1006E48C, terase)):
            host.setg(addr, val)
        print("erase parameters set by hand:", vcc, vpp, tprog, terase)
        host.blocked.discard("bulk_erase")
        n = len(host.messages)
        r = host.call(0x10016E38, 0, 100)
        print("bulk-erase routine ->", r)
        show(n)
    finally:
        host.blocked.add("bulk_erase")
        print("shutdown ->", host.h.call("pgd_setup", 3, 0, 0), flush=True)


if __name__ == "__main__":
    main()

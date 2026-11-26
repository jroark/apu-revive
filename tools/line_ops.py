#!/usr/bin/env python3
"""Capture the module's port operations for one line read (simulation) and show their structure."""
import sys
sys.path.insert(0, "tools")
from harness import boot_sim


def capture(part_id, row, setup=(5000, 0, 12000, 0), mode=1):
    h = boot_sim()
    h.call("pgd_setup", 6, part_id, (5000 << 16) | 0)
    h.call("pgd_program_setup", 0, 0, 5000, 0, 0, 0)
    h.call("pgd_program_setup", mode, 0, *setup)
    n = len(h.trace)
    h.call("pgd_read_silicon_id", row, 0)
    ops = []
    for line in h.trace[n:]:
        if line.startswith("  "):
            p = line.split()
            ops.append((p[0], int(p[2], 16), int(p[4], 16) if len(p) > 4 else None))
    return ops


def fmt(ops):
    return " ".join("%s%02x%s" % (k, p, "" if v is None else "=%02x" % v) for k, p, v in ops)


if __name__ == "__main__":
    rows = [int(x, 0) for x in sys.argv[1:]] or [0, 1, 5]
    for row in rows:
        ops = capture(59, row)
        first = next(i for i, o in enumerate(ops) if o[1] == 0x0d)
        addr, cells = ops[:first], ops[first:]
        print("row %#x: %d ops, address phase %d, cell phase %d" % (row, len(ops), len(addr), len(cells)))
        pos = [(i, o) for i, o in enumerate(addr) if not (o[0] == "W" and o[1] == 3)]
        print("  address phase non-clock ops:", [(i, fmt([o])) for i, o in pos])
        print("  port-3 values:", sorted(set(o[2] for o in addr if o[1] == 3)))
        # split the cell phase at each 'W0d=00 W0d=00' boundary guess: find period
        s = cells
        reads = [i for i, o in enumerate(s) if o[0] == "R"]
        print("  first reads at", reads[:8], "total", len(reads))
        per = reads[7] - reads[0] if len(reads) > 7 else None
        print("  head:", fmt(s[:reads[0]]))
        print("  unit:", fmt(s[reads[0]:reads[7]]) if per else "")
        units = [tuple(s[reads[i]:reads[i + 7]]) for i in range(0, len(reads) - 7, 7)]
        print("  distinct units:", len(set(units)), "tail:", fmt(s[reads[-1]:]))

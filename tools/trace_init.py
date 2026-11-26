#!/usr/bin/env python3
"""Golden trace: initialise the APU module in the emulator and exercise simple commands."""
import sys
sys.path.insert(0, "tools")
from harness import Harness

V = "vendor/maxplus2-10.2/MAX+plus_II_BASELINE"


def boot(p1, verbose=True):
    h = Harness(V + "/pgdmpuz.drv", verbose=False)
    h.dllmain()
    h.call("pgd_setup", 12, 0x44, h.alloc(1024))
    h.verbose = verbose
    r = h.call("pgd_setup", 1, p1, h.put((V + "/pgdmpuz.rbf").encode()))
    return h, r


if __name__ == "__main__":
    p1 = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x280
    h, r = boot(p1)
    print("setup(1) ->", r, "base", hex(h.u32(0x1003b94c)))
    for n in (1, 2, 0):
        print("--- LED", n, "->", h.call("pgd_setup", 8, n, 0))

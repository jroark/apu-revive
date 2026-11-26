#!/usr/bin/env python3
"""Run module calls in simulation (no device) and return each call's traffic as tokens, delays
kept. The delay calibration is forced to the value measured on the real unit so delay lengths
match hardware."""
import struct
import sys

sys.path.insert(0, "tools")
from harness import boot_sim
import capture_setup as cs

NS_PER_CMD = 16500


class Sim:
    def __init__(self):
        self.h = boot_sim()
        self.h.uc.mem_write(0x1003B988, struct.pack("<I", NS_PER_CMD))
        self.events = []
        dev = self.h.device
        ow, orr = dev.write, dev.read
        dev.write = lambda p, d: (self.events.append(("out" if p == 2 else "ctl", bytes(d))), ow(p, d))[1]

        def rd(p, n):
            d = orr(p, n)
            if p == 1:
                self.events.append(("in", bytes(d)))
            return d
        dev.read = rd

    def call(self, *args):
        n = len(self.events)
        r = self.h.call(*args)
        return r, cs.tokens(self.events[n:])

    def ints(self, values):
        buf = self.h.alloc(4 * len(values))
        self.h.uc.mem_write(buf, struct.pack("<%dI" % len(values), *values))
        return buf

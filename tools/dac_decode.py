#!/usr/bin/env python3
"""Decode the port-1 serial stream the module sends to the TLC5620 DACs into commands.

Framing recovered from traces: each data bit is two writes, 0x82|bit then 0x80|bit (clock high,
clock low, data on bit 0); 11 bits (A1 A0 RNG D7..D0) are followed by a load strobe write
(bit 3 = 0x08 or bit 2 = 0x04 selects the DAC chip). Other bits of port 1 are reported raw.
"""
import sys

sys.path.insert(0, "tools")


def decode(ops):
    """ops: list of trace strings like 'W  port 0x01 = 0x82'. Returns list of events."""
    events, bits, i = [], [], 0
    vals = []
    for o in ops:
        o = o.strip()
        if o.startswith("W ") and "port 0x01" in o:
            vals.append(("w", int(o.split("= ")[1], 16)))
        elif o.startswith("R ") and "port 0x01" in o:
            vals.append(("r", None))
        elif o.startswith(("W", "R")):
            vals.append(("x", o))
    prev = None
    for kind, v in vals:
        if kind == "x":
            events.append(("other", v)); continue
        if kind == "r":
            events.append(("read1", None)); continue
        if prev is not None and (prev & 0xFE) == (v | 0x02) & 0xFE and prev & 0x02 and not v & 0x02 and (prev & 1) == (v & 1) and v & 0x80:
            bits.append(v & 1)                      # falling clock edge with stable data
        elif v & 0x0C and len(bits) >= 11:
            b = bits[-11:]
            code = int("".join(map(str, b[3:])), 2)
            events.append(("dac", "A" if v & 0x08 else "B", b[0] * 2 + b[1], b[2], code))
            bits = []
        prev = v
    return events


if __name__ == "__main__":
    from harness import boot_sim
    volts = [int(x, 0) for x in sys.argv[1:7]] if len(sys.argv) > 6 else [1, 0, 5000, 0, 12000, 0]
    h = boot_sim()
    h.call("pgd_setup", 6, 59, 5000 << 16)
    n0 = len(h.trace)
    r = h.call("pgd_program_setup", *volts)
    ev = decode(h.trace[n0:])
    dacs = [e for e in ev if e[0] == "dac"]
    print("program_setup%s -> %d; %d DAC commands, %d port-1 reads, %d other ops" % (
        tuple(volts), r, len(dacs), sum(1 for e in ev if e[0] == "read1"), sum(1 for e in ev if e[0] == "other")))
    last = {}
    for _, chip, ch, rng, code in dacs:
        last[(chip, ch)] = (rng, code)
    print("final DAC settings:", {f"{c}{ch}": v for (c, ch), v in sorted(last.items())})

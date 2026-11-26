#!/usr/bin/env python3
"""Record, on the real unit, everything the vendor module sends and receives for device select
and voltage set-up (delays kept). Output: pickle of {phase: [("out", bytes) | ("in", bytes) |
("ctl", bytes)]}.   usage: capture_setup.py PART OUT.pkl"""
import pickle
import sys

sys.path.insert(0, "tools")
import harness
import read_siid as rs


def tokens(events):
    """Flatten events to tokens: ('W'|'W5'|'W8', port, value), ('R', port, value_read), ('N', count)."""
    out, pending = [], []
    for kind, data in events:
        if kind == "out":
            body = data if len(data) % 64 == 0 else data[:-2]
            i = 0
            while i < len(body):
                b = body[i]
                if b & 0xC0 == 0x80:
                    out.append(["R", b & 0x3F, None]); pending.append(out[-1]); i += 1
                elif body[i:i + 2] == b"\x40\xff":
                    if out and out[-1][0] == "N":
                        out[-1][1] += 1
                    else:
                        out.append(["N", 1, None])
                    i += 2
                else:
                    out.append([{0: "W", 0x40: "W5", 0xC0: "W8"}[b & 0xC0], b & 0x3F, body[i + 1]]); i += 2
        elif kind == "in":
            for v in data:
                pending.pop(0)[2] = v
        else:
            out.append(["CTL", data[1], None])
    return [tuple(t) for t in out]


def main():
    pcf_name, part_id = rs.lookup_part(sys.argv[1])
    pcf = rs.parse_pcf(pcf_name, 0)
    h = harness.Harness(rs.V + "/pgdmpuz.drv", verbose=False)
    dev = harness.UsbApu(lambda m: None)
    h.device, h.log, h.realtime = dev, (lambda m: None), True
    h.dllmain()
    h.call("pgd_setup", 12, 0x44, h.alloc(1024))
    events = []
    ow, orr = dev.write, dev.read
    dev.write = lambda p, d: (events.append(("out" if p == 2 else "ctl", bytes(d))), ow(p, d))[1]

    def rd(p, n):
        d = orr(p, n)
        if p == 1:
            events.append(("in", bytes(d)))
        return d
    dev.read = rd
    phases = {}

    def phase(name, *call):
        n = len(events)
        r = h.call(*call)
        phases[name] = events[n:]
        print(name, "->", r, "events", len(events) - n)
    phase("open", "pgd_setup", 1, 0x280, h.put((rs.V + "/pgdmpuz.rbf").encode()))
    try:
        rs.apply_calibration(h)
        phase("select", "pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16)
        phase("supply", "pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0)
        if "--examine" in sys.argv:
            phase("examine", "pgd_program_setup", 3, 0, 5000, 2000, 12000, 0)
        else:
            phase("siid", "pgd_program_setup", 1, 0, *pcf["siid_volts"])
        phase("read", "pgd_read_silicon_id", 0, 0)
    finally:
        phase("shutdown", "pgd_setup", 3, 0, 0)
    pickle.dump(phases, open(sys.argv[2], "wb"))


if __name__ == "__main__":
    main()

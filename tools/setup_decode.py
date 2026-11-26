#!/usr/bin/env python3
"""Condense a captured set-up phase into high-level steps: DAC commands, ADC reads, delays, and
the remaining port writes.   usage: setup_decode.py CAPTURE.pkl PHASE"""
import pickle
import sys

sys.path.insert(0, "tools")
import capture_setup as cs


def condense(t):
    out, i = [], 0
    while i < len(t):
        k, p, v = t[i]
        # DAC frame: 01=c0, 22 clock writes, load, 00
        if k == "W" and p == 1 and v == 0xC0 and i + 24 < len(t) and all(x[0] == "W" and x[1] == 1 for x in t[i:i + 25]) \
                and t[i + 23][2] in (0x88, 0x84) and (t[i + 1][2] & 0xFE) == 0x82:
            bits = [t[i + 1 + 2 * j][2] & 1 for j in range(11)]
            code = int("".join(map(str, bits[3:])), 2)
            out.append("DAC %s%d=%d" % ("A" if t[i + 23][2] == 0x88 else "B", bits[0] * 2 + bits[1], code))
            i += 25
            continue
        # delay: 01=c0, N, 01=00
        if k == "W" and p == 1 and v == 0xC0 and i + 2 < len(t) and t[i + 1][0] == "N" and t[i + 2][:3] == ("W", 1, 0):
            out.append("DELAY %d" % t[i + 1][1]); i += 3
            continue
        # ADC: eight 01=90 then address
        if k == "W" and p == 1 and v == 0x90 and all(x[:3] == ("W", 1, 0x90) for x in t[i:i + 8]):
            j = i + 8
            addr = 0
            for b in range(4):
                addr = addr << 1 | (t[j + 2 * b][2] & 1)
            # consume until the terminating write of 00/c0 to port 1 after the sampled pass
            vals, passes = [], 0
            while j < len(t) and t[j][1] == 1 and not (t[j][0] == "W" and t[j][2] in (0x00, 0xC0)):
                if t[j][0] == "R":
                    vals.append(t[j][2])
                j += 1
            samples = [x for x in vals if x is not None][-8:]
            val = 0
            for s_ in samples:
                val = val << 1 | (s_ & 1)
            out.append("ADC ch%d -> %d" % (addr, val)); i = j + 1
            continue
        if k == "N":
            out.append("N%d" % p)
        elif k == "R":
            out.append("R%02x->%02x" % (p, v if v is not None else -1))
        else:
            out.append("%s%02x=%02x" % (k, p, v))
        i += 1
    return out


def rle(items):
    out = []
    for x in items:
        if out and out[-1][0] == x:
            out[-1][1] += 1
        else:
            out.append([x, 1])
    return " ".join(x if n == 1 else "%s x%d" % (x, n) for x, n in out)


if __name__ == "__main__":
    ph = pickle.load(open(sys.argv[1], "rb"))
    t = cs.tokens(ph[sys.argv[2]])
    c = condense(t)
    print(len(t), "tokens ->", len(c), "steps")
    print(rle(c))

#!/usr/bin/env python3
"""Reference check for native code: run a module call in the emulator against the replay device
and return the port operations it issues, in the same (kind, port, value) form that
`ops_of_words` gives for a native word list. A native routine is correct when the two match."""
import sys

sys.path.insert(0, "tools")
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from apu import protocol
from harness import boot_sim, decode_wire


def module_ops(call, *args, setup=()):
    h = boot_sim()
    for s in setup:
        h.call(*s)
    n0 = len(h.trace)
    h.call(call, *args)
    ops = []
    for line in h.trace[n0:]:
        if not line.startswith("  "):
            continue
        p = line.split()
        kind, port = p[0], int(p[2], 16)
        ops.append((kind, port, int(p[4], 16) if len(p) > 4 else None))
    return ops


def ops_of_words(words):
    wire, _ = protocol.encode(words)
    return decode_wire(wire)


if __name__ == "__main__":
    from apu import board
    ok = True
    for state in (1, 2, 0):
        ref = module_ops("pgd_setup", 8, state, 0)
        mine = ops_of_words(board.led_words(state))
        # the module issues the F7 write once per call sequence; compare the tail it shares
        match = ref[-len(mine):] == mine
        ok &= match
        print("LED state %d: module %s | native %s | %s" % (state, ref, mine, "MATCH" if match else "DIFFERENT"))
    sys.exit(0 if ok else 1)

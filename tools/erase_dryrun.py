#!/usr/bin/env python3
"""DRY RUN of pgm.dll's bulk erase on a real chip.

usage: erase_dryrun.py PART_NAME
1. Runs pgm.dll's own silicon-ID check (0x1003d4bd) on the chip. This only reads; it also runs
   the routine that chooses the erase voltage and timing from data in the device.
2. Prints the values it chose.
3. Runs the bulk-erase routine (0x10016e38) with every chip-changing module call BLOCKED
   (bulk_erase, bulk_program, program_byte, program_bytes_on_column are logged, report success
   to pgm.dll, and never reach the module). Reads and voltage set-up do run.
4. Always ends with the module's shutdown call.
"""
import sys

sys.path.insert(0, "tools")
from host import DESTRUCTIVE, Host


def show(host, start):
    for m in host.messages[start:]:
        if m.startswith(("MODULE pgd_read_silicon_id", "MODULE pgd_read_byte")):
            continue
        if m.startswith(("MODULE", "BLOCKED", "PGM")):
            print("  ", m[:160])


def main():
    host = Host(sys.argv[1], real="--sim" not in sys.argv)
    assert set(DESTRUCTIVE) <= host.blocked
    host.h.call_timeout_s = 90
    try:
        dev = host.load_device()
        host.setg(0x1006E35C, 1)
        n = len(host.messages)
        r = host.call(0x1003D4BD)
        print("silicon-ID check ->", r, "| variant", host.u32(dev + 0x478), "| part type", host.u32(dev + 0x44C))
        show(host, n)
        print("erase parameters chosen by pgm.dll:", {hex(a): host.g(a) for a in (0x1006E480, 0x1006E484, 0x1006E488, 0x1006E48C)})
        if r != 1:
            print("ID check failed; not continuing")
            return
        n = len(host.messages)
        r = host.call(0x10016E38, 0, 100)
        print("bulk-erase routine (chip-changing calls blocked) ->", r)
        show(host, n)
    finally:
        print("shutdown ->", host.h.call("pgd_setup", 3, 0, 0))


if __name__ == "__main__":
    main()

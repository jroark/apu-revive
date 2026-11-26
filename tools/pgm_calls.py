#!/usr/bin/env python3
"""List, in order, the hardware-module calls pgm.dll makes in an address range.
usage: pgm_calls.py START END [--all]
The slot table (base 0x1006fc90) was recovered from phm.dll's loader.
"""
import re
import sys

import capstone
import pefile

DLL = "vendor/maxplus2-10.2/MAX+plus_II_BASELINE/pgm.dll"
SLOTS = {0x1006FC90 + off: name for off, name in {
    4: "program_byte", 8: "program_bytes_on_column", 12: "program_setup", 16: "read_byte",
    20: "read_bytes_on_column", 24: "read_chgrp", 28: "read_silicon_id", 32: "bulk_erase",
    36: "bulk_program", 40: "set_chgrp_dr", 44: "set_chgrp_io", 48: "set_column_address",
    52: "set_row_address", 56: "pgd_setup", 60: "jtag_io", 64: "jtag_drscan", 68: "jtag_irscan"}.items()}
KNOWN = {0x1003ECEB: "pgm_set_voltages", 0x1003D4BD: "check_silicon_id", 0x1003E820: "select_device",
         0x1001E48E: "read_rows", 0x1003A3FD: "read_in_silicon_id", 0x10006D40 - 0: "?"}


def main():
    start, end = int(sys.argv[1], 16), int(sys.argv[2], 16)
    show_all = "--all" in sys.argv
    pe = pefile.PE(DLL)
    base = pe.OPTIONAL_HEADER.ImageBase
    img = pe.get_memory_mapped_image()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    def cstr(a):
        try:
            o = a - base
            s = img[o:img.index(b"\0", o)].decode("latin1")
            return s if 3 < len(s) < 70 and s.isprintable() else None
        except Exception:
            return None

    recent, last_w = [], {}
    for i in md.disasm(img[start - base:end - base], start):
        t = i.op_str
        m = re.match(r"(\w+), (?:d?word) ptr \[(e\w\w) \+ (0x[0-9a-f]+|\d+)\]", t) if i.mnemonic in ("mov", "movzx") else None
        if m and m.group(2) not in ("ebp", "esp"):
            last_w[m.group(1)[-2:]] = "rec+" + hex(int(m.group(3), 0))
        if i.mnemonic == "push":
            if t.startswith("0x1005") and cstr(int(t, 16)):
                recent.append(repr(cstr(int(t, 16))))
            elif t in ("eax", "ecx", "edx", "ebx", "esi", "edi"):
                recent.append(last_w.pop(t[-2:], t))
            else:
                recent.append(t.replace("dword ptr ", ""))
        if i.mnemonic == "call":
            m = re.search(r"\[(0x1006fc[0-9a-f]+)\]", t)
            name = SLOTS.get(int(m.group(1), 16), t) if m else KNOWN.get(int(t, 16)) if t.startswith("0x") else None
            if name or show_all:
                print(hex(i.address), name or t, list(reversed(recent[-8:])))
            recent, last_w = [], {}


if __name__ == "__main__":
    main()

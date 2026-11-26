#!/usr/bin/env python3
"""Outline a routine of gio.dll (the vendor's schematic reader): its calls with the constants
pushed before them, with import thunks and exports named.   usage: gio_outline.py ADDR [SIZE]"""
import re
import struct
import sys

import capstone
import pefile

PE = pefile.PE("vendor/maxplus2-10.2/MAX+plus_II_BASELINE/gio.dll")
BASE = PE.OPTIONAL_HEADER.ImageBase
IMG = PE.get_memory_mapped_image()
MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
EXPORTS = {BASE + e.address: e.name.decode() for e in PE.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
IMPORTS = {}
for entry in PE.DIRECTORY_ENTRY_IMPORT:
    for imp in entry.imports:
        IMPORTS[imp.address] = (imp.name or b"?").decode()


def name_of(target):
    if target in EXPORTS:
        return EXPORTS[target]
    off = target - BASE
    if IMG[off:off + 2] == b"\xff\x25":                      # jmp [import]
        return IMPORTS.get(struct.unpack_from("<I", IMG, off + 2)[0], hex(target))
    return hex(target)


def outline(addr, size=0x400):
    pushes = []
    for i in MD.disasm(IMG[addr - BASE:addr - BASE + size], addr):
        t = i.op_str
        if i.mnemonic == "push":
            pushes.append(t)
        elif i.mnemonic == "call":
            m = re.search(r"\[0x([0-9a-f]+)\]", t)
            target = IMPORTS.get(int(m.group(1), 16), t) if m else name_of(int(t, 16)) if t.startswith("0x") else t
            consts = [p for p in pushes[-5:] if re.match(r"^(0x[0-9a-f]+|\d+)$", p)]
            print("%#x call %-34s consts %s" % (i.address, target, consts[::-1]))
            pushes = []
        elif i.mnemonic == "ret":
            break


if __name__ == "__main__":
    outline(int(sys.argv[1], 16), int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x400)

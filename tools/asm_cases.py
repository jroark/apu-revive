"""Disassemble cases of asm.dll's per-element routine (0x10006880): its switch over element
arch types uses index bytes at 0x1000e309 (type - 1) and a target table at 0x1000e195.

    python tools/asm_cases.py vendor/.../asm.dll 31 6 29
"""
import struct
import sys

import capstone


class Pe:
    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt = struct.unpack_from("<H", data, pe + 20)[0]
        self.base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
        self.sections = []
        for i in range(nsec):
            o = pe + 24 + opt + 40 * i
            name = data[o:o + 8].rstrip(b"\0").decode()
            vsize, va, rsize, raw = struct.unpack_from("<IIII", data, o + 8)
            self.sections.append((name, self.base + va, max(vsize, rsize), raw))

    def offset(self, va):
        for _, start, size, raw in self.sections:
            if start <= va < start + size:
                return raw + va - start
        raise ValueError(hex(va))

    def u8(self, va):
        return self.data[self.offset(va)]

    def u32(self, va):
        return struct.unpack_from("<I", self.data, self.offset(va))[0]


def case_target(pe, etype):
    return pe.u32(0x1000E195 + 4 * pe.u8(0x1000E309 + etype - 1))


def disassemble(pe, start, limit=120):
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    out, va = [], start
    code = pe.data[pe.offset(start):pe.offset(start) + 4096]
    for ins in md.disasm(code, start):
        out.append("%08x  %-7s %s" % (ins.address, ins.mnemonic, ins.op_str))
        if len(out) >= limit or ins.mnemonic in ("ret",) or (ins.mnemonic == "jmp" and "0x10006" in ins.op_str
                                                              and int(ins.op_str, 16) < start):
            break
    return out


if __name__ == "__main__":
    pe = Pe(open(sys.argv[1], "rb").read())
    for t in map(int, sys.argv[2:]):
        target = case_target(pe, t)
        print("== element type %d -> %08x" % (t, target))
        print("\n".join(disassemble(pe, target)))

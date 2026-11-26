#!/usr/bin/env python3
"""Convert a read_back.py dump (JSON of cells) to a JEDEC fuse file using the .pcf fuse map.

usage: dump_to_jed.py DUMP.json OUT.jed
Fuse value = the cell bit as read. The polarity relative to Altera's own JEDEC output is
NOT verified.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from apu.pcf import Pcf


def fuse_bits(dump, pcf):
    cells = {}
    for key, r in dump["rows"].items():
        for i, v in enumerate(r["data"]):
            cells[(int(key, 16), r["col"] + i)] = v
    return [1 if cells[(row, col)] & mask else 0 for _, row, col, mask in pcf.fuses()]


def jedec_text(bits, title, pins=84):
    body = [f"{title}*", f"QF{len(bits)}*", f"QP{pins}*", "F0*"]
    for a in range(0, len(bits), 64):
        body.append("L%06d %s*" % (a, "".join(map(str, bits[a:a + 64]))))
    fuse_sum = 0
    for i in range(0, len(bits), 8):
        fuse_sum += sum(b << k for k, b in enumerate(bits[i:i + 8]))
    body.append("C%04X*" % (fuse_sum & 0xFFFF))
    text = "\x02" + "\r\n".join(body) + "\r\n\x03"
    return text + "%04X" % (sum(text.encode()) & 0xFFFF)


def main():
    dump = json.load(open(sys.argv[1]))
    pcf = Pcf.load(dump["pcf"])
    if dump.get("bits_per_call", 8) < max(r.mask for r in pcf.jedec_map).bit_length():
        sys.exit("refusing: this dump was read 8 bits per cell but the device's fuse map uses "
                 "wider cells, so some fuses were never read")
    bits = fuse_bits(dump, pcf)
    open(sys.argv[2], "w", newline="").write(jedec_text(bits, "Read from %s by apu read_back" % dump["part"]))
    print("%d fuses, %d set (%.1f%%) -> %s" % (len(bits), sum(bits), 100 * sum(bits) / len(bits), sys.argv[2]))


if __name__ == "__main__":
    main()

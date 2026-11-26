#!/usr/bin/env python3
"""Staged signature-row ("MS row") work on an EPM7160E, outside Altera's normal flow.

usage: ms_row.py read
       ms_row.py erase --yes-really-erase          manufacturer's erase (bulk_erase flag 1)
       ms_row.py write ROW_JSON --yes-really-write  program row 0 from a captured row
Sequences follow pgm.dll's manufacturer's-erase path (0x10017a50): erase voltages
program_setup(3, Tprogram, 4750, 2000, Vpp, 0); bulk_erase(Terase, 1, 0x1000a, 0);
then program voltages via the same set-up and program_bytes_on_column(0, 80, mask, data, 0, 0).
Parameters are the working sibling's: Vpp 12250 mV, Tprogram 60, Terase 200.
"""
import json
import struct
import sys
import time

sys.path.insert(0, "tools")
import bulk_probe as bp

PART, ROW, WIDTH, MASK = "EPM7160ELC84-15", 0, 80, 0x3FF
VPP, TPROG, TERASE = 12250, 60, 200


def read_row(h, row=ROW):
    h.call("pgd_program_setup", 3, 0, 5000, 2000, 12000, 0)
    bp.bulk_read(h, row, 0, WIDTH, MASK)
    a = list(bp.bulk_read(h, row, 0, WIDTH, MASK)[2][:WIDTH])
    b = list(bp.bulk_read(h, row, 0, WIDTH, MASK)[2][:WIDTH])
    return a, a == b


def show(label, cells):
    data = [(i, "%x" % (v & 0x1FF)) for i, v in enumerate(cells) if (v & 0x1FF) != 0x1FF]
    print(f"{label}: {len(data)} cells with data (low 9 bits): {data}")


def main():
    stage = sys.argv[1]
    h, p = bp.open_device(PART, True)
    h.call_timeout_s = 120
    try:
        cells, stable = read_row(h)
        show("row 0 now" + ("" if stable else " (UNSTABLE)"), cells)
        if stage == "erase":
            if "--yes-really-erase" not in sys.argv:
                sys.exit("refusing without --yes-really-erase")
            print("program_setup ->", h.call("pgd_program_setup", 3, TPROG, 4750, 2000, VPP, 0))
            print("pgd_bulk_erase(%d, 1, 0x1000a, 0) ->" % TERASE, h.call("pgd_bulk_erase", TERASE, 1, 0x1000A, 0))
            time.sleep(0.05)
            cells, stable = read_row(h)
            show("row 0 after manufacturer's erase" + ("" if stable else " (UNSTABLE)"), cells)
        elif stage == "write":
            if "--yes-really-write" not in sys.argv:
                sys.exit("refusing without --yes-really-write")
            src = json.load(open(sys.argv[2]))["cells"]
            data = [(v & 0xFF) | 0x300 for v in src]            # never program bits 8-9
            want = [(i, "%x" % (v & 0x1FF)) for i, v in enumerate(data) if (v & 0xFF) != 0xFF]
            print("writing low bytes:", want)
            n = WIDTH + 8
            mask_buf, data_buf = h.alloc(4 * n), h.alloc(4 * n)
            h.uc.mem_write(mask_buf, struct.pack(f"<{n}I", *([MASK] * n)))
            h.uc.mem_write(data_buf, struct.pack(f"<{n}I", *(data + [MASK] * 8)))
            print("program_setup ->", h.call("pgd_program_setup", 3, TPROG, 4750, 2000, VPP, 0))
            h.call("pgd_set_column_address", ROW)
            print("pgd_program_bytes_on_column ->", h.call("pgd_program_bytes_on_column", 0, WIDTH, mask_buf, data_buf, 0, 0))
            cells, stable = read_row(h)
            show("row 0 after write" + ("" if stable else " (UNSTABLE)"), cells)
            ok = all((c & 0xFF) == (d & 0xFF) for c, d in zip(cells, data))
            print("low bytes match the source row:", ok)
    finally:
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))


if __name__ == "__main__":
    main()

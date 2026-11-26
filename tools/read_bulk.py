#!/usr/bin/env python3
"""Read back a device with the programmer's own bulk call, one line at a time.

usage: read_bulk.py PART_NAME OUT.json [--real] [--volts siid|nomargin|examine]
  examine (default): mode 3 with the record's examine set (vmarg_examine)
  vmarg=N: mode 3 with margin N mV (e.g. the low/high verify levels)
  nomargin: mode 1 with margin = VCC; NOT a valid data read on the EPM7160E (981 cells change)
  siid: the silicon-ID set (margin 0), as used for the first images
For every row in the .pcf regions: pgd_set_column_address(row), then
pgd_read_bytes_on_column(0, width, mask[], out[]) with width = the row's declared width
(80 or 16 on the MAX 7000 parts seen). The module's line routine (0x1000ca70) only handles a
start of 0 and treats width 16 differently from 80, so the width must be the real one.
Silicon-ID rows are read with pgd_read_silicon_id instead.
"""
import json
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import bulk_probe as bp
from apu.pcf import KIND_ID


def main():
    name, out = sys.argv[1], sys.argv[2]
    h, p = bp.open_device(name, "--real" in sys.argv)
    volts = sys.argv[sys.argv.index("--volts") + 1] if "--volts" in sys.argv else "examine"
    vcc = p.nominal_vcc()
    if volts == "nomargin":
        setup = (1, 0, vcc, vcc, p.field("vpd_examine"), p.field("vpg_examine"))
    elif volts == "examine":
        setup = (3, 0, p.field("vcc_examine") or vcc, p.field("vmarg_examine"), p.field("vpd_examine"), p.field("vpg_examine"))
    elif volts.startswith("vmarg="):          # mode 3 with an explicit margin voltage in mV
        setup = (3, 0, vcc, int(volts[6:]), p.field("vpd_examine"), p.field("vpg_examine"))
    else:
        setup = (1, 0) + tuple(p.siid_volts())
    assert h.call("pgd_program_setup", *setup) == 1
    width, mask, kind = {}, {}, {}
    for r in p.regions:
        if r.kind in (0x100, KIND_ID):
            continue
        for i in range(r.n):
            row = r.row + i
            width[row] = max(width.get(row, 0), r.col + r.count)
            mask[row] = mask.get(row, 0) | r.mask
            kind[row] = r.kind
    rows = {}
    t0 = time.time()
    try:
        order = sorted(width)
        # pgm.dll's reader walks its row table from row 0 upward, so on parts whose ID row is
        # row 0 (EPM7160E) that row is bulk-read first. Skipping it leaves the top data bit
        # (0x200) reading 0 on every later line; reading it first keeps that bit live.
        # On parts with the ID row elsewhere this is just a discarded warm-up read.
        id_rows = sorted({r.row for r in p.regions if r.kind == KIND_ID})
        warm = id_rows[0] if id_rows and id_rows[0] < order[0] else order[0]
        bp.bulk_read(h, warm, 0, 80, max(mask.values()))
        for row in order:
            ret, _, data = bp.bulk_read(h, row, 0, width[row], mask[row])
            rows["%#04x" % row] = {"kind": kind[row], "col": 0, "mask": mask[row], "data": list(data[:width[row]])}
        unstable = []
        for row in order[:3] + order[-2:]:
            again = list(bp.bulk_read(h, row, 0, width[row], mask[row])[2][:width[row]])
            if again != rows["%#04x" % row]["data"]:
                unstable.append(hex(row))
        if unstable:
            print("WARNING: lines changed on re-read:", unstable)
        ident = []
        for r in p.regions:
            if r.kind == KIND_ID:
                ident += [h.call("pgd_read_silicon_id", r.row, r.col + i) & r.mask for i in range(r.count)]
    finally:
        h.call("pgd_setup", 3, 0, 0)
    import read_siid as rs
    path, _ = rs.lookup_part(name)
    json.dump({"part": name, "pcf": path, "method": "bulk", "bits_per_call": 16, "program_setup": list(setup),
               "silicon_id": ident, "rows": rows}, open(out, "w"), indent=1)
    text = "".join(chr(v) if 32 <= v < 127 else "." for v in ident)
    print("%d lines in %.0f s; silicon ID %r -> %s" % (len(rows), time.time() - t0, text, out))


if __name__ == "__main__":
    main()

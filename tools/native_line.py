#!/usr/bin/env python3
"""Hybrid check on hardware: the vendor module (emulated) selects the device and sets the
voltages; the line read and its decoding are native. Compares with the module's own ID read.

usage: native_line.py PART [row ...]      (reads only; nothing is written to the chip)"""
import sys

sys.path.insert(0, "tools")
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import harness
import read_siid as rs
from apu import max7000, protocol


def native_line(apu, row):
    reads = apu.xfer(max7000.words(max7000.line_read_ops(row)))
    return max7000.cells_from_reads(reads)


def main():
    part = sys.argv[1]
    rows = [int(x, 0) for x in sys.argv[2:]] or [0]
    pcf_name, part_id = rs.lookup_part(part)
    pcf = rs.parse_pcf(pcf_name, 0)
    h = harness.Harness(rs.V + "/pgdmpuz.drv", verbose=False)
    h.device = harness.UsbApu(lambda m: None)
    h.log = lambda m: None
    h.realtime = True
    h.dllmain()
    h.call("pgd_setup", 12, 0x44, h.alloc(1024))
    if h.call("pgd_setup", 1, 0x280, h.put((rs.V + "/pgdmpuz.rbf").encode())) != 1:
        sys.exit("open failed")
    try:
        rs.apply_calibration(h)
        h.call("pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16)
        h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0)
        h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"])
        for row in rows:
            cells = native_line(h.device.apu, row)
            print("row %#x native cells: %s" % (row, " ".join("%03x" % c for c in cells)))
            if row == 0:
                mine = [max7000.signature_byte(cells, k) for k in range(12)]
                theirs = [h.call("pgd_read_silicon_id", 0, k) & 0xFF for k in range(12)]
                print("native  ID bytes:", bytes(mine[:8]), [hex(v) for v in mine[8:]])
                print("module  ID bytes:", bytes(theirs[:8]), [hex(v) for v in theirs[8:]])
                print("MATCH" if mine == theirs else "DIFFERENT")
    finally:
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))


if __name__ == "__main__":
    main()

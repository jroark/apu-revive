#!/usr/bin/env python3
"""Read back a device's EEPROM array cell by cell through Altera's module (emulated).

usage: read_back.py PART_NAME OUT.json [--real] [--rows A:B]
Uses the same proven path as the silicon-ID read (pgd_read_silicon_id(row, col) under the
silicon-ID voltages), applied to every region in the .pcf. Non-destructive.
"""
import json
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness
import read_siid as rs


def main():
    name, out = sys.argv[1], sys.argv[2]
    real = "--real" in sys.argv
    lim = None
    if "--rows" in sys.argv:
        a, b = sys.argv[sys.argv.index("--rows") + 1].split(":")
        lim = (int(a, 0), int(b, 0))
    path, part_id = rs.lookup_part(name)
    pcf = rs.parse_pcf(path)
    if real:
        h = harness.Harness(rs.V + "/pgdmpuz.drv", verbose=False)
        h.device = harness.UsbApu(lambda m: None)
        h.log = lambda m: None            # full logging is too slow for bulk reads
        h.realtime = True
        h.dllmain()
        h.call("pgd_setup", 12, 0x44, h.alloc(1024))
        if h.call("pgd_setup", 1, 0x280, h.put((rs.V + "/pgdmpuz.rbf").encode())) != 1:
            sys.exit("init failed")
        rs.apply_calibration(h)
    else:
        h = harness.boot_sim()
        h.log = lambda m: None
    rows = {}
    t0 = time.time()
    try:
        assert h.call("pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16) == 1
        assert h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0) == 1
        assert h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"]) == 1
        for r in pcf["regions"]:
            if r["kind"] == 0x100:
                continue
            for i in range(r["n"]):
                row = r["row"] + i
                if lim and not (lim[0] <= row < lim[1]):
                    continue
                vals = [h.call("pgd_read_silicon_id", row, r["col"] + c) & r["mask"] for c in range(r["count"])]
                rows.setdefault("%#04x" % row, {"kind": r["kind"], "col": r["col"], "mask": r["mask"], "data": []})["data"] += vals
                print("row %#04x kind %#x: %s" % (row, r["kind"], "".join("%x" % v if r["mask"] <= 0xF else "%02x " % v for v in vals)), flush=True)
    finally:
        h.call("pgd_setup", 3, 0, 0)
        json.dump({"part": name, "pcf": path, "rows": rows}, open(out, "w"), indent=1)
        print("shutdown done; %d rows in %.1f s -> %s" % (len(rows), time.time() - t0, out))


if __name__ == "__main__":
    main()

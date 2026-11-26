#!/usr/bin/env python3
"""Read a device's silicon ID using Altera's module under emulation.

usage: read_siid.py PART_NAME [--variant N] [--real]     e.g. EPM7160ELC84-15
Without --real the simulated (replay) device is used.
Sequence (from pgm.dll's pgm_read_in_silicon_id and the .pcf):
  pgd_setup(6, part_id, nominal_vcc << 16 | variant);   part_id = column 6 of partinfo.crf pgd_program_setup(1, 0, v[0x0a], v[0x0c], v[0x0e], v[0x10]);
  pgd_read_silicon_id(row, col + i) for each type-0x20 / 0x10 region; pgd_setup(3) to shut down.
"""
import struct
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import harness

V = "vendor/maxplus2-10.2/MAX+plus_II_BASELINE"
# pgm.dll packs the nominal VCC (voltage record +4) into the high word of the device-selection
# argument; the low word is the variant index (0 before the ID is known). It then calls
# pgd_program_setup(0, 0, vcc, 0, 0, 0) before the silicon-ID read.


NS_PER_CMD = int(__import__("os").environ.get("APU_NS_PER_CMD", "0"))


def apply_calibration(h, announce=False):
    """APU_NS_PER_CMD > 0 forces the module's delay calibration (ns per stream command);
    APU_NS_PER_CMD = 0 leaves the value the module measured itself at start-up."""
    measured = h.u32(0x1003B988)
    if NS_PER_CMD > 0:
        h.uc.mem_write(0x1003B988, struct.pack("<I", NS_PER_CMD))
    if announce:
        print("delay calibration: measured %d, using %d" % (measured, h.u32(0x1003B988)))
    return measured


HDR = 8  # section offsets in the header are relative to the end of the 8-byte file header


def parse_pcf(path, variant=0):
    d = open(path, "rb").read()
    assert d[:3] == b"PCF"
    family, nvar = struct.unpack_from("<HH", d, 0x0C)
    offs = [o + HDR for o in struct.unpack_from("<5I", d, 0x1C)]
    regions = []
    for o in range(offs[0], offs[1], 20):
        row, col, n, count, mask, kind, _ = struct.unpack_from("<HHHHIII", d, o)
        regions.append(dict(kind=kind, row=row, col=col, n=n, count=count, mask=mask))
    rec = offs[2] + 0xEC * variant                  # voltage record for this variant
    nominal_vcc = struct.unpack_from("<H", d, rec + 4)[0]
    volts = struct.unpack_from("<4H", d, rec + 0x0A)
    ids = d[offs[1] : offs[2]].decode("utf-16-le", "replace")
    return dict(family=family, variants=nvar, regions=regions, siid_volts=volts,
                nominal_vcc=nominal_vcc, id_text=ids)


def lookup_part(name):
    """Return (pcf_path, part_id) from partinfo.crf: column 2 names the .pcf, column 6 is the
    part ID that pgm.dll passes to pgd_setup(6, ...) (GE.dll's "generic part type")."""
    import glob
    for line in open(V + "/partinfo.crf", encoding="latin1"):
        f = line.split()
        if f and f[0].upper() == name.upper():
            hits = glob.glob(f"vendor/maxplus2-10.2/*/{f[1]}.pcf")
            if not hits:
                sys.exit(f"no {f[1]}.pcf found")
            return hits[0], int(f[5])
    sys.exit(f"{name} not in partinfo.crf")


def main():
    variant = int(sys.argv[sys.argv.index("--variant") + 1]) if "--variant" in sys.argv else 0
    path, part_id = lookup_part(sys.argv[1])
    pcf = parse_pcf(path, variant)
    if variant >= pcf["variants"]:
        sys.exit("no such variant")
    real = "--real" in sys.argv
    print("%s: %s, part id %d, %d variants, nominal vcc %d, siid volts %s, expected IDs %r" % (
        sys.argv[1], path.split("/")[-1], part_id, pcf["variants"], pcf["nominal_vcc"],
        pcf["siid_volts"], pcf["id_text"]))
    if real:
        h = harness.Harness(V + "/pgdmpuz.drv", verbose=False)
        h.device = harness.UsbApu(h.log)
        h.realtime = True
        h.dllmain()
        h.call("pgd_setup", 12, 0x44, h.alloc(1024))
        if h.call("pgd_setup", 1, 0x280, h.put((V + "/pgdmpuz.rbf").encode())) != 1:
            sys.exit("init failed")
    else:
        h = harness.boot_sim()
    try:
        # The module pads the command stream with NOPs for short delays, scaled by a start-up
        # calibration (ns per stream command) that it measures itself over the real USB link.
        # Use that measured value. Forcing 1000 (as this tool did at first) makes every delay
        # ~16x longer; forcing 200 makes a known-good chip misread.
        apply_calibration(h, announce=True)
        print("select ->", h.call("pgd_setup", 6, part_id, pcf["nominal_vcc"] << 16 | variant), "type", hex(h.u32(0x1003B9D0)))
        h.log("## program_setup")
        print("program_setup(0, vcc) ->", h.call("pgd_program_setup", 0, 0, pcf["nominal_vcc"], 0, 0, 0))
        print("program_setup(1, siid) ->", h.call("pgd_program_setup", 1, 0, *pcf["siid_volts"]))
        for r in pcf["regions"]:
            if r["kind"] != 0x10:
                continue
            vals = []
            for i in range(r["count"]):
                h.log("## pgd_read_silicon_id(%#x, %d)" % (r["row"], r["col"] + i))
                vals.append(h.call("pgd_read_silicon_id", r["row"], r["col"] + i) & r["mask"])
                h.log("## -> %#x" % vals[-1])
            text = "".join(chr(v) if 32 <= v < 127 else "." for v in vals)
            print("region kind %#x row %#x col %#x: %s  %r" % (r["kind"], r["row"], r["col"], [hex(v) for v in vals], text))
    finally:
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0))
        print("log lines:", len(h.trace))
        if "--trace" in sys.argv:
            out = sys.argv[sys.argv.index("--trace") + 1]
            open(out, "w").write("\n".join(h.trace) + "\n")
            print("trace written to", out)


if __name__ == "__main__":
    main()

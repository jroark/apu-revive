"""Co-simulate a MAX 7000S device's Quartus-compiled probes against their AHDL sources.

    python tools/s7k_cosim.py DEVICE [KINDS] [-v]       e.g. EPM7128S xzoe
Uses the tables in apu/data/<tag>.json (tools/s7k_derive.py --write).
"""
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from apu import s7k  # noqa: E402
from fusesim_check import cosim  # noqa: E402
from s7k_derive import designs  # noqa: E402
from s7k_probes import device  # noqa: E402


def main():
    dev = device(sys.argv[1])
    kinds = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "xzoe"
    fm = s7k.FuseMap(dev["fm"], s7k.Tables.load(dev["tag"]))
    total = collections.Counter()
    for kind in kinds:
        for d in designs(dev, kind):
            try:
                checks, mism = cosim(d["name"], d["tdf"], None, fm, d["fuses"], d["pins"], steps=600, verbose=False)
            except Exception as e:
                print("%-12s error %s: %s" % (d["name"], type(e).__name__, str(e)[:100]))
                total["errors"] += 1
                continue
            late = [m for m in mism if m[0] >= 300]
            total["checks"] += checks
            total["mismatches"] += len(late)
            total["designs"] += 1
            total["bad designs"] += bool(late)
            if late and "-v" in sys.argv:
                print("%-12s %d checks, %d mismatches after warm-up %s" % (d["name"], checks, len(late), late[:2]))
    print(dev["name"], kinds, dict(total))


if __name__ == "__main__":
    main()

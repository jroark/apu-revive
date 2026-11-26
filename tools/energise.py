#!/usr/bin/env python3
"""Set a part's rails and HOLD them for measurement, then run the module's shutdown call.
Run with NO CHIP fitted (and, to reach the regulators, with the adapter removed).

usage: energise.py PART_NAME VCC VMARG VPD VPG HOLD_SECONDS [--sim]
Voltages in millivolts, e.g. the EPM7096 silicon-ID set: 5000 5000 9000 12000.
"""
import sys
import time

sys.path.insert(0, "tools")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import bulk_probe as bp


def main():
    part = sys.argv[1]
    vcc, vmarg, vpd, vpg, hold = (int(x, 0) for x in sys.argv[2:7])
    h, p = bp.open_device(part, "--sim" not in sys.argv)   # select + supply-on + silicon-ID set
    try:
        r = h.call("pgd_program_setup", 1, 0, vcc, vmarg, vpd, vpg)
        print(f"program_setup(1, 0, {vcc}, {vmarg}, {vpd}, {vpg}) -> {r}", flush=True)
        print(f"RAILS HELD for {hold} s from {time.strftime('%H:%M:%S')}", flush=True)
        time.sleep(hold)
    finally:
        print("shutdown ->", h.call("pgd_setup", 3, 0, 0), time.strftime("%H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()

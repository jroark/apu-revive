"""Play a read-only SVF (Quartus's verify file) through openocd and record what the chip returns.

    python tools/jtag_svf_read.py verify.svf out.json [--tap epm7064s.tap --irlen 10 --id 0x070640dd]

Instead of comparing TDO with the file's expected values, every SDR's captured bits are kept,
together with the expected TDO and MASK (if any), so a chip can be dumped whatever it holds and
the file's expectations tell where each POF fuse appears in the read-out. The script refuses an
SVF containing any instruction outside READ_ONLY (the EPM7000S verify set), so it cannot be used
to erase or program by accident.

openocd runs as a server (Tcl port 6666) with the cable's interface config; commands go over the
Tcl socket: irscan / drscan / runtest.
"""
import argparse
import json
import re
import socket
import subprocess
import sys
import time

# instructions in Quartus 13.0's EPM7000S verify SVF; erase/program use 006, 00A, 00E, 032, 036
READ_ONLY = {0x071, 0x079, 0x03E, 0x01E, 0x012, 0x016, 0x01A, 0x03A, 0x022, 0x026}


def parse_svf(text):
    text = re.sub(r"!.*|//.*", "", text)
    out = []
    for stmt in text.split(";"):
        words = stmt.split()
        if not words:
            continue
        cmd = words[0].upper()
        if cmd in ("SIR", "SDR"):
            n = int(words[1])
            fields = dict(re.findall(r"(TDI|TDO|MASK|SMASK)\s*\(\s*([0-9A-Fa-f\s]+)\)", stmt))
            out.append((cmd, n, {k: int(v.replace(" ", "").replace("\n", ""), 16) for k, v in fields.items()}))
        elif cmd == "RUNTEST":
            m = re.search(r"(\d+)\s+TCK", stmt)
            out.append(("RUNTEST", int(m.group(1)) if m else 0, {}))
        elif cmd in ("FREQUENCY", "TRST", "ENDDR", "ENDIR", "STATE", "HDR", "HIR", "TDR", "TIR"):
            out.append((cmd, 0, {"text": stmt.strip()}))
        else:
            raise ValueError("unsupported SVF statement: %s" % stmt.strip()[:60])
    return out


class Openocd:
    def __init__(self, interface, tap, irlen, idcode):
        self.tap = tap
        args = ["openocd", "-f", interface, "-c", "tcl_port 6666", "-c", "telnet_port disabled",
                "-c", "gdb_port disabled",
                "-c", "jtag newtap %s tap -irlen %d -expected-id %s" % (tap.split(".")[0], irlen, idcode),
                "-c", "init"]
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(50):
            try:
                self.sock = socket.create_connection(("127.0.0.1", 6666))
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.proc.kill()
            raise RuntimeError("openocd did not start: " + self.proc.stdout.read())

    def cmd(self, line):
        self.sock.sendall(line.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("openocd closed the connection")
            buf += chunk
        return buf[:-1].decode().strip()

    def close(self):
        try:
            self.cmd("shutdown")
        except Exception:
            pass
        self.proc.wait(timeout=10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svf")
    ap.add_argument("out")
    ap.add_argument("--interface", default="interface/altera-usb-blaster.cfg")
    ap.add_argument("--tap", default="epm7064s.tap")
    ap.add_argument("--irlen", type=int, default=10)
    ap.add_argument("--id", default="0x070640dd")
    a = ap.parse_args()
    prog = parse_svf(open(a.svf).read())
    bad = sorted({hex(f["TDI"]) for c, n, f in prog if c == "SIR" and f.get("TDI") not in READ_ONLY})
    if bad:
        sys.exit("refusing: the SVF uses instructions outside the read-only set: %s" % ", ".join(bad))
    ocd = Openocd(a.interface, a.tap, a.irlen, a.id)
    records, mismatches = [], 0
    try:
        for cmd, n, f in prog:
            if cmd == "SIR":
                ocd.cmd("irscan %s 0x%x" % (a.tap, f["TDI"]))
            elif cmd == "RUNTEST":
                ocd.cmd("runtest %d" % n)
            elif cmd == "SDR":
                got = int(ocd.cmd("drscan %s %d 0x%x" % (a.tap, n, f.get("TDI", 0))) or "0", 16)
                if "TDO" in f:
                    mask = f.get("MASK", (1 << n) - 1)
                    if (got ^ f["TDO"]) & mask:
                        mismatches += 1
                    records.append({"bits": n, "tdi": "%x" % f.get("TDI", 0), "got": "%x" % got,
                                    "tdo": "%x" % f["TDO"], "mask": "%x" % mask})
    finally:
        ocd.close()
    json.dump(records, open(a.out, "w"))
    print("%d reads recorded, %d differ from the file's expectations" % (len(records), mismatches))


if __name__ == "__main__":
    main()

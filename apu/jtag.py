"""EPM7064S over JTAG with a USB-Blaster, through openocd (no vendor software).

    idcode()                  -> the chip's 32-bit IDCODE
    read_fuses()              -> the chip's 30609 fuses (read-only: ISP mode, reads, exit)
    verify(fuses)             -> list of mismatching (row, half, phase) reads
    program(fuses)            -> erase, program and verify (changes the chip)

openocd runs as a child process with its Tcl port; statements of an SVF made by apu.svf7064s
are sent as irscan / drscan / runtest, so every captured word is available. Reading uses the
verify sequence (instructions 071 01E 012 016 01A 03A 022 026 079 03E only); program() is the
only function that sends erase / program instructions.
"""
import re
import socket
import subprocess
import time

from . import svf7064s

IDCODE_EPM7064S = 0x070640DD
# device -> (IDCODE, SVF template tag or None for the hand-modelled EPM7064S writer)
DEVICES = {"EPM7064S": (0x070640DD, None), "EPM7128S": (0x071280DD, "7128s")}
READ_ONLY = {0x071, 0x079, 0x03E, 0x01E, 0x012, 0x016, 0x01A, 0x03A, 0x022, 0x026}
TAP = "epm7064s.tap"


class JtagError(Exception):
    pass


class Openocd:
    def __init__(self, interface="interface/altera-usb-blaster.cfg", port=6666, idcode=IDCODE_EPM7064S):
        args = ["openocd", "-f", interface, "-c", "tcl_port %d" % port, "-c", "telnet_port disabled",
                "-c", "gdb_port disabled",
                "-c", "jtag newtap epm7064s tap -irlen 10 -expected-id 0x%08x" % idcode, "-c", "init"]
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            raise JtagError("openocd is not installed (brew install open-ocd)")
        for _ in range(100):
            if self.proc.poll() is not None:
                raise JtagError("openocd stopped: " + self.proc.stdout.read()[-600:])
            try:
                self.sock = socket.create_connection(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
        self.proc.kill()
        raise JtagError("openocd did not start")

    def cmd(self, line):
        self.sock.sendall(line.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            chunk = self.sock.recv(65536)
            if not chunk:
                raise JtagError("openocd closed the connection")
            buf += chunk
        return buf[:-1].decode().strip()

    def close(self):
        try:
            self.cmd("shutdown")
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _statements(text):
    """(command, bits, fields) for each SVF statement (comments dropped)."""
    text = re.sub(r"!.*", "", text)
    for stmt in text.split(";"):
        w = stmt.split()
        if not w:
            continue
        if w[0] in ("SIR", "SDR"):
            fields = {k: int(v.replace(" ", "").replace("\n", ""), 16)
                      for k, v in re.findall(r"(TDI|TDO|MASK)\s*\(\s*([0-9A-Fa-f\s]+)\)", stmt)}
            yield w[0], int(w[1]), fields
        elif w[0] == "RUNTEST":
            m = re.search(r"(\d+)\s+TCK", stmt)
            yield "RUNTEST", int(m.group(1)) if m else 0, {}
        else:
            yield w[0], 0, {"text": stmt.strip()}


def play(ocd, text, allow_write=False):
    """Run an SVF; returns [(TDI, captured TDO, expected TDO, mask)] for every compared SDR."""
    out = []
    for cmd, n, f in _statements(text):
        if cmd == "SIR":
            if not allow_write and f["TDI"] not in READ_ONLY:
                raise JtagError("refusing instruction %03X in a read-only sequence" % f["TDI"])
            ocd.cmd("irscan %s 0x%x" % (TAP, f["TDI"]))
        elif cmd == "RUNTEST":
            ocd.cmd("runtest %d" % n)
        elif cmd == "SDR":
            got = int(ocd.cmd("drscan %s %d 0x%x" % (TAP, n, f.get("TDI", 0))) or "0", 16)
            if "TDO" in f:
                out.append((f.get("TDI", 0), got, f["TDO"], f.get("MASK", (1 << n) - 1)))
    return out


def idcode(ocd):
    """After Test-Logic-Reset the IDCODE register is selected (IEEE 1149.1)."""
    ocd.cmd("pathmove RESET IDLE")
    return int(ocd.cmd("drscan %s 32 0" % TAP) or "0", 16)


def _reads_in_order():
    """(row index, half, phase) of every verify read, in svf7064s.write's order."""
    order = []
    for i in range(len(svf7064s.ROWS)):
        for half in (0, 1):
            for p in ((4, 3, 2, 1, 0) if i < 88 else (4,)):
                order.append((i, half, p))
    return order


def read_fuses(ocd):
    """The chip's fuses, read through the verify sequence (fuse 30608 is not readable: 1)."""
    return decode_reads([got for _, got, _, _ in play(ocd, svf7064s.write([1] * svf7064s.FUSES, "verify"))])


def decode_reads(words):
    """Fuses from the 32-bit words a verify sequence returns (ID-check reads first)."""
    captured = [(None, w, None, None) for w in words]
    id_reads = len([s for s in svf7064s._DATA["id_check"] if s[0] == "read"])
    if (captured[0][1] & 0xFFFFFFFF) != 0x5445414C:
        raise JtagError("silicon ID word reads %08X, not 5445414C" % captured[0][1])
    fuses = [1] * svf7064s.FUSES
    for (row, half, p), (_, got, _, _) in zip(_reads_in_order(), captured[id_reads:]):
        bits = svf7064s.ROWS[row][1 + half]
        for j in range(32):
            src = bits[5 * j + p]
            if not isinstance(src, tuple):
                fuses[src] = (got >> j) & 1
    return fuses


def verify(ocd, fuses):
    captured = play(ocd, svf7064s.write(fuses, "verify"))
    return [i for i, (_, got, want, mask) in enumerate(captured) if (got ^ want) & mask]


def program(ocd, fuses, jtag_enabled=True, svf_path=None):
    """Erase, program and verify the chip with openocd's own SVF player (it follows the file's
    pause states and timing and stops at the first TDO mismatch). Returns openocd's report;
    raises JtagError if it reports a failure."""
    import os
    import tempfile
    text = svf7064s.write(fuses, "program", jtag_enabled)
    path = svf_path or os.path.join(tempfile.mkdtemp(prefix="apu-svf-"), "program.svf")
    with open(path, "w") as f:
        f.write(text)
    report = ocd.cmd("svf -tap %s %s progress" % (TAP, path))
    if re.search(r"(?i)error|fail|mismatch", report):
        raise JtagError("openocd svf: " + report[-800:])
    return report


# --- table-described devices (apu.svf_template) -----------------------------------------------------
def _svf(device, fuses, operation, jtag_enabled=True):
    tag = DEVICES[device][1]
    if tag is None:
        return svf7064s.write(fuses, operation, jtag_enabled)
    from . import svf_template
    return svf_template.write(tag, fuses, operation, jtag_enabled)


def decode_reads_template(tag, words):
    """Fuses from the words a verify sequence returns, by the template's TDO bit map."""
    from . import svf_template
    t = svf_template.template(tag)
    fuses = [1] * t["fuses"]
    it = iter(words)
    for st in t["verify"]:
        if isinstance(st, dict) and "TDO" in st:
            got = next(it)
            if isinstance(st["TDO"], list):
                b = 0
                for start, step, count in st["TDO"]:
                    if start >= 0:
                        for k in range(count):
                            fuses[start + step * k] = (got >> (b + k)) & 1
                    b += count
    return fuses


def read_device(ocd, device):
    tag = DEVICES[device][1]
    if tag is None:
        return read_fuses(ocd)
    from . import svf_template
    blank = [1] * svf_template.template(tag)["fuses"]
    words = [got for _, got, _, _ in play(ocd, svf_template.write(tag, blank, "verify"))]
    if (words[0] & 0xFFFFFFFF) != 0x5445414C:
        raise JtagError("silicon ID word reads %08X, not 5445414C" % words[0])
    return decode_reads_template(tag, words)


def verify_device(ocd, device, fuses):
    captured = play(ocd, _svf(device, fuses, "verify"))
    return [i for i, (_, got, want, mask) in enumerate(captured) if (got ^ want) & mask]


def program_device(ocd, device, fuses, jtag_enabled=True):
    """Erase, program and verify with openocd's SVF player (changes the chip)."""
    import os
    import tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="apu-svf-"), "program.svf")
    with open(path, "w") as f:
        f.write(_svf(device, fuses, "program", jtag_enabled))
    report = ocd.cmd("svf -tap %s %s progress" % (TAP, path))
    if re.search(r"(?i)error|fail|mismatch", report):
        raise JtagError("openocd svf: " + report[-800:])
    return report

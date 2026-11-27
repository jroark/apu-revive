"""JTAG programming files (SVF) for the EPM7064S, written from its fuses with no vendor software.

    svf7064s.write(fuses, operation="program")   -> SVF text (erase, program, verify)
    svf7064s.write(fuses, operation="verify")    -> SVF text (read back and compare only)

The algorithm and layout were recovered by converting index-coded POFs with Quartus II 13.0
SP1's quartus_cpf (re/protocol.md, 2026-11-27); the output is checked statement by statement
against quartus_cpf's in tests/test_svf7064s.py.

Instructions (10-bit IR): 071 enter ISP, 079 leave ISP, 03E return to user mode, 01E shift a
253-bit row address, 012 (before data and after pulses), 016 / 01A shift the 160-bit data /
column-select registers of the two halves, 006 / 00E / 00A program and erase pulses, 03A a
normal read, 036 / 032 the two margin reads, 022 / 026 shift out 32 bits of the 016 / 01A half.

Rows: k = 1-88 are product-term rows, address bits 107 - k and 252 - k; data bit b of the 016
word is term 79 - b (b < 80) or 239 - b, of the 01A word term 319 - b, column (k - 1) XOR 7, so
fuse = term * 88 + column. Then 55 further rows (options, routing, pins) whose layout is in
apu/data/epm7064s_isp.json. A read of phase p (select pattern: every fifth bit from p) returns
bit 5j + p of the half's data word as bit j.
"""
import json
import pathlib

FUSES = 30609
_DATA = json.loads((pathlib.Path(__file__).parent / "data" / "epm7064s_isp.json").read_text())


def _rows():
    """[(address bits, [w016 bit -> fuse or ('c', v)] , [w01a ...])] in programming order."""
    rows = []
    for k in range(1, 89):
        col = (k - 1) ^ 7
        w0 = [((79 - b) if b < 80 else (239 - b)) * 88 + col for b in range(160)]
        w1 = [(319 - b) * 88 + col for b in range(160)]
        rows.append(((107 - k, 252 - k), w0, w1))

    def dec(s):
        out = []
        for i in range(0, len(s), 3):
            v = int(s[i:i + 3], 16)
            out.append(("c", v - 0xFFE) if v >= 0xFFE else v + 28160)
        return out
    for r in _DATA["rows"]:
        rows.append((tuple(r["addr"]), dec(r["w016"]), dec(r["w01a"])))
    return rows


ROWS = _rows()
# Row 128, 01A word, bit 64 is not taken from the fuses: quartus_cpf sets it 1 for every design
# Quartus compiles (JTAG left enabled, its default) and 0 for a POF without Quartus's option
# packets. Taken to be the JTAG-enable bit: programming it 0 could turn the JTAG pins into I/O
# and lock out in-system programming, so it is 1 unless asked otherwise.
JTAG_ENABLE = ((128,), 2, 64)
# reads whose MASK leaves out bit 14 (single-bit rows 134 and 139-143): Quartus does not compare it
_MASKED = {(143,), (142,), (141,), (140,), (139,), (134,)}


def _word(fuses, bits):
    v = 0
    for b, src in enumerate(bits):
        bit = src[1] if isinstance(src, tuple) else fuses[src]
        v |= bit << b
    return v


def _phase(p):
    return sum(1 << i for i in range(p, 160, 5))


def _read(word, p):
    return sum(((word >> (5 * j + p)) & 1) << j for j in range(32))


class _Out:
    def __init__(self):
        self.lines = []

    def __call__(self, s):
        self.lines.append(s)

    def sir(self, ir, wait=8, state=None):
        self("SIR 10 TDI (%03X);" % ir)
        if wait is not None:
            self("RUNTEST %s%d TCK%s;" % ("%s " % state[0] if state else "", wait, " ENDSTATE %s" % state[1] if state else ""))

    def sdr(self, n, tdi, tdo=None, mask=None):
        digits = (n + 3) // 4
        s = "SDR %d TDI (%0*X)" % (n, digits, tdi)
        if tdo is not None:
            s += " TDO (%0*X) MASK (%0*X)" % (digits, tdo, digits, (1 << n) - 1 if mask is None else mask)
        self(s + ";")

    def address(self, bits):
        self.sir(0x01E)
        self.sdr(253, sum(1 << b for b in bits))


def write(fuses, operation="program", jtag_enabled=True):
    if len(fuses) not in (FUSES, FUSES - 1):
        raise ValueError("an EPM7064S has %d fuses" % FUSES)
    rows = []
    for addr, w0, w1 in ROWS:
        if tuple(addr) == JTAG_ENABLE[0]:
            w1 = list(w1)
            w1[JTAG_ENABLE[2]] = ("c", 1 if jtag_enabled else 0)
        rows.append((addr, w0, w1))
    o = _Out()
    o("!apu-revive EPM7064S %s" % operation)
    o("FREQUENCY 1.00E+06 HZ;")
    o("TRST ABSENT;")
    o("ENDDR IDLE;")
    o("ENDIR IRPAUSE;")
    o("STATE IDLE;")
    o.sir(0x071, 1003, ("IDLE", "IDLE"))
    # silicon ID: the header row, read as the vendor's file does (expects "ALTE" first)
    o("!CHECKING SILICON ID")
    o.address((107, 252))
    o.sir(0x012)
    for step in _DATA["id_check"]:
        if step[0] == "sel":
            o.sir(int(step[1], 16))
            o.sdr(160, int(step[2], 16))
        else:
            o.sir(0x03A, 18)
            o.sir(int(step[1], 16))
            o.sdr(32, 0xFFFFFFFF, int(step[2], 16), int(step[3], 16))
    if operation == "program":
        o("!BULK ERASE")
        o.sir(0x012)
        o.address(range(253))
        o.sir(0x006)
        o.sir(0x00E)
        o.sir(0x00A, 100003)
        _pulse_end(o, first=True)
        o.address(())
        o("!PROGRAM")
        for addr, w0, w1 in rows:
            o.address(addr)
            o.sir(0x012)
            o.sir(0x016)
            o.sdr(160, _word(fuses, w0))
            o.sir(0x01A)
            o.sdr(160, _word(fuses, w1))
            o.sir(0x006)
            o.sir(0x00A, 30003)
            _pulse_end(o)
    o("!VERIFY")
    for i, (addr, w0, w1) in enumerate(rows):
        d0, d1 = _word(fuses, w0), _word(fuses, w1)
        o.address(addr)
        o.sir(0x012)
        o.sir(0x016)
        o.sdr(160, _phase(4))
        o.sir(0x01A)
        o.sdr(160, _phase(4))
        phases = (4, 3, 2, 1, 0) if i < 88 else (4,)
        for half, (sel, out, word) in enumerate(((0x016, 0x022, d0), (0x01A, 0x026, d1))):
            for p in phases:
                if p != 4:
                    o.sir(sel)
                    o.sdr(160, _phase(p))
                mask = 0xFFFFBFFF if (half == 1 and tuple(addr) in _MASKED) else 0xFFFFFFFF
                # after programming, both margin reads; a plain verify, the normal read
                for margin in ((0x036, 0x032) if operation == "program" else (0x03A,)):
                    o.sir(margin, 18)
                    o.sir(out)
                    o.sdr(32, 0xFFFFFFFF, _read(word, p), mask)
    o.sir(0x079, 1003)
    o.sir(0x03E, 1000)
    o("STATE IDLE;")
    return "\n".join(o.lines) + "\n"


def _pulse_end(o, first=False):
    """After a program or erase pulse: one DR bit in DRPAUSE, then back (as the vendor's file)."""
    o("ENDDR DRPAUSE;")
    o.sdr(1, 1)
    o("RUNTEST DRPAUSE 50 TCK ENDSTATE DRPAUSE;")
    o("ENDDR IDLE;")
    o("SIR 10 TDI (012);")
    if first:
        o("RUNTEST IRPAUSE 50 TCK ENDSTATE IDLE;")
        o("RUNTEST IDLE 8 TCK;")
    else:
        o("RUNTEST IDLE 8 TCK ENDSTATE IDLE;")

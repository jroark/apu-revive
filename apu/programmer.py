"""Native programmer sequences (no Altera code at run time).

Transcribed from the traffic the vendor module produces on a real unit (tools/capture_setup.py,
tools/setup_decode.py) and from its code where a rule is involved (the supply trim). So far this
covers what a silicon-ID read of an EPM7160E on the PLMJ7000-84 needs.
"""
from . import board, max7000, protocol
from .device import Apu

DELAY_SETTLE = 749      # no-op commands, about 12 ms at the unit's stream rate
DELAY_RAMP = 5

# Opcode-5 writes select which socket lines are driven (1 = released, as far as observed).
ALL_RELEASED = [(p, 0x00) for p in (0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13)]
# EPM7160E (module type 0x3b) on the PLMJ7000-84, read set-up
DRIVE_EPM7160E = [(0x03, 0xD9), (0x04, 0xFF), (0x05, 0xFF), (0x06, 0x7A), (0x07, 0xF5), (0x0B, 0xFF),
                  (0x0C, 0x9C), (0x0D, 0xF7), (0x0E, 0xFF), (0x0F, 0xFF), (0x10, 0xAF), (0x11, 0x7C),
                  (0x12, 0xF5), (0x13, 0xFF), (0x14, 0xEF), (0x15, 0xE3)]
DATA_PORTS = (0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15)

VCC_ADC_CHANNEL = 4     # socket supply, 5 V full scale (module 0x10006b80)
VCC_TRIM_LIMIT = 4      # this code never raises the supply DAC more than this above nominal


def _send(apu: Apu, ops):
    return apu.xfer(max7000.words(ops))


def _w(port, *values):
    return [("W", port, v) for v in values]


def _w5(pairs):
    return [("W5", p, v) for p, v in pairs]


def _delay(n=DELAY_SETTLE):
    return [("W", 0x01, 0xC0), ("N", n, None), ("W", 0x01, 0x00)]


def _dac(apu, chip, channel, code):
    board.set_dac(apu, chip, channel, code)


def trim_vcc(apu: Apu, millivolts: int) -> int:
    """Set the supply DAC and step it up until the ADC reads the target (module 0x10006b80:
    start code mV*255/10000, target reading min(mV*255/5000, 255)). Returns the final code."""
    code = millivolts * 255 // 10000
    target = min(millivolts * 255 // 5000, 255)
    for step in range(VCC_TRIM_LIMIT + 1):
        _dac(apu, "B", 3, code + step)
        _send(apu, _delay())
        if board.adc_read(apu, VCC_ADC_CHANNEL) >= target:
            return code + step
    raise RuntimeError("supply did not reach %d mV within %d DAC steps" % (millivolts, VCC_TRIM_LIMIT))


def _rails(apu, vcc_code, vpd_code, vmarg_code=0):
    board.adapter_id(apu)
    _dac(apu, "A", 0, 45)
    _dac(apu, "B", 3, vcc_code)
    _send(apu, _delay())
    _dac(apu, "B", 0, vpd_code)
    _dac(apu, "B", 1, 0)
    _dac(apu, "B", 2, vmarg_code)
    _send(apu, _delay())


def supply_on(apu: Apu, vcc_mv: int = 5000, profile=None) -> int:
    """Bring the socket supply up: ramp the DAC one code at a time, then trim against the ADC."""
    code = vcc_mv * 255 // 10000
    _send(apu, [("W5", 0x00, 0xF7)])
    _rails(apu, code, 0)
    _send(apu, _w(0x09, 0x7E, 0x00))
    _dac(apu, "B", 3, 0)
    _send(apu, _w(0x00, 0x10))
    for c in range(code + 1):
        _dac(apu, "B", 3, c)
        _send(apu, _delay(DELAY_RAMP))
    if profile is not None and profile.small:
        _send(apu, _w(0x00, 0x10) + [("W", p, 0x00) for p in profile.clear] + _w5(profile.released))
    else:
        _send(apu, _w(0x00, 0x10) + _w5([(0x03, 0x80)] + ALL_RELEASED + [(0x14, 0x1C)]
                                         + [(p, 0x00) for p in (0x15, 0x16, 0x17, 0x18, 0x19, 0x1A)]))
    final = trim_vcc(apu, vcc_mv)
    _send(apu, [("W5", 0x00, 0xF7)] + _w(0x00, 0x90))
    return final


class Epm7160eReader:
    """Line reads for the EPM7160E family, tracking whether a read has happened since set-up."""

    def __init__(self):
        self.first = True

    def read(self, apu, row, cells=max7000.LINE_CELLS, signature=False):
        reads = bytes(_send(apu, max7000.line_read_ops(row, self.first, cells)))
        self.first = False
        return max7000.cells_from_reads(reads, cells)


class Epm7064Reader:
    def __init__(self):
        self.first, self.hi15 = True, 0

    def read(self, apu, row, cells=max7000.LINE_CELLS, signature=False):
        ops = max7000.line_read_ops_7064(row, self.first, cells, self.hi15, signature)
        reads = bytes(_send(apu, ops))
        self.first, self.hi15 = False, max7000.hi15_after_7064(row, self.hi15)
        return max7000.cells_from_reads_7064(reads, cells)


class Epm7032Reader:
    def __init__(self):
        self.previous = None

    def read(self, apu, row, cells=max7000.LINE_CELLS, signature=False):
        ops = max7000.line_read_ops_7032(row, self.previous is None, cells, self.previous or 0, signature)
        reads = bytes(_send(apu, ops))
        self.previous = row
        return max7000.cells_from_reads_7032(reads, cells)

    def release_ops(self):
        a0c = max7000.row_address_7032(self.previous or 0)[0]
        return _w(0x05, 0x00) + _w(0x0C, a0c) + _w(0x06, 0x00)


class Profile:
    """What differs between device types in the read path."""

    def __init__(self, name, adapters, drive, setup_tail, release, reader, signature, verified,
                 clear=None, released=None, small=False):
        self.name, self.adapters, self.drive, self.setup_tail = name, adapters, drive, setup_tail
        self.release, self.reader, self.signature, self.verified = release, reader, signature, verified
        # `small` marks the 44-pin adapter's layout: fewer ports, cleared before being released,
        # and no separate port 0x14 handling
        self.clear, self.released, self.small = clear, released, small


DRIVE_EPM7064_84 = [(0x03, 0xDF), (0x04, 0xFF), (0x05, 0xFF), (0x06, 0xFB), (0x07, 0xEF), (0x0B, 0xFF),
                    (0x0C, 0xF6), (0x0D, 0xDF), (0x0E, 0xFF), (0x0F, 0xFF), (0x10, 0xF7), (0x11, 0xFF),
                    (0x12, 0xFD), (0x13, 0xFF), (0x14, 0xEF), (0x15, 0xFF)]

PROFILES = {
    "EPM7160E": Profile(
        "EPM7160E", (55,), DRIVE_EPM7160E,
        (_w(0x0D, 0x00, 0x00) + _w(0x10, 0x00) + _w(0x15, 0x00) + _w(0x11, 0x00, 0x00) + _w(0x06, 0x00)
         + _w(0x07, 0x00) + _w(0x0D, 0x00) + _w(0x0C, 0x00) + _w(0x0C, 0x10, 0x00) * 120),
        list(max7000.LINE_RELEASE), Epm7160eReader,
        lambda cells: bytes(max7000.signature_byte(cells, k) for k in range(12)), verified=True),
    # Checked against the module in simulation only; not yet run on a chip.
    "EPM7064": Profile(
        "EPM7064", (55,), DRIVE_EPM7064_84,
        _w(0x10, 0x00) + _w(0x0B, 0x00, 0x00) + _w(0x07, 0x00) + _w(0x0B, 0x10, 0x00) * 120,
        list(max7000.LINE_RELEASE_7064), Epm7064Reader,
        lambda cells: bytes(max7000.signature_byte_7064(cells, k) for k in range(10)), verified=False),
    # PLMJ7000-44. Checked against the module in simulation only; not yet run on a chip.
    "EPM7032": Profile(
        "EPM7032", (9,), [(0x05, 0xFF), (0x06, 0x71), (0x07, 0xA5), (0x0B, 0xFF), (0x0C, 0x0F)],
        _w(0x06, 0x00) + _w(0x0C, 0x00) + _w(0x06, 0x10, 0x00) * 120,
        _w(0x05, 0x00) + _w(0x0C, 0x00) + _w(0x06, 0x00), Epm7032Reader,
        lambda cells: bytes(max7000.signature_byte_7064(cells, k) for k in range(2, 10)), verified=False,
        clear=(0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D),
        released=[(0x03, 0x83), (0x04, 0x00), (0x05, 0x00), (0x06, 0x80), (0x07, 0x00), (0x0B, 0x00),
                  (0x0C, 0x00), (0x0D, 0x00)], small=True),
}


def read_setup_ops(profile):
    """The pin and line set-up between the two supply trims of a read set-up."""
    if profile.small:
        ops = [("W", p, 0x02 if p == 0x03 else 0x00) for p in profile.clear]
        ops += _w5(profile.released) + _w(0x0A, 0x01) + _delay() + _w(0x0A, 0x81) + _delay()
        return ops + _w5(profile.drive) + list(profile.setup_tail) + _delay()
    ops = [("W", p, 0x0C if p == 0x14 else 0x00) for p in DATA_PORTS]
    ops += _w5([(0x03, 0x80)] + ALL_RELEASED + [(0x14, 0x1C), (0x15, 0x00)])
    ops += _w(0x0A, 0x01) + _delay() + _w(0x0A, 0x81) + _w5([(0x14, 0x0C)]) + _delay()
    ops += _w5(profile.drive) + list(profile.setup_tail) + _delay()
    return ops


def read_setup(apu: Apu, profile, vcc_mv: int = 5000, vpd_mv: int = 12000, vmarg_mv: int = 0) -> int:
    """Voltages and line drivers for reading. vmarg 0 is the module's ID set-up (program_setup
    mode 1); a margin voltage gives its examine set-up (mode 3), which on the wire differs only
    in the margin DAC."""
    _send(apu, _delay())
    _rails(apu, vcc_mv * 255 // 10000, board.vpd_code(vpd_mv), vmarg_mv * 255 // 10000)
    _send(apu, _w(0x00, 0x90))
    trim_vcc(apu, vcc_mv)
    _send(apu, read_setup_ops(profile))
    final = trim_vcc(apu, vcc_mv)
    _send(apu, [("W5", 0x00, 0xF7)] + _w(0x00, 0x90))
    return final


def read_setup_epm7160e(apu: Apu, vcc_mv: int = 5000, vpd_mv: int = 12000, vmarg_mv: int = 0) -> int:
    return read_setup(apu, PROFILES["EPM7160E"], vcc_mv, vpd_mv, vmarg_mv)


def shutdown(apu: Apu, profile=None, reader=None):
    """Remove all voltages and release every line (the module's pgd_setup 3)."""
    release = list((profile or PROFILES["EPM7160E"]).release)
    if reader is not None and hasattr(reader, "release_ops"):
        release = reader.release_ops()
    _send(apu, release + _w(0x08, 0x03, 0x03, 0x03))
    _dac(apu, "B", 0, 0)
    _send(apu, _delay() + _w(0x0A, 0x80) + _w(0x02, 0x01) + _delay() + _w(0x0A, 0x00) + _w(0x02, 0x81)
          + _delay() + _w(0x00, 0x80) + _w5([(p, 0xFF) for p in (0x01, 0x02, 0x08, 0x09, 0x0A)])
          + _w(0x00, 0x80) + _w(0x02, 0x80) + _w(0x09, 0x01, 0x7F) + _w(0x02, 0x00))
    reset(apu)


def reset(apu: Apu):
    """Idle state: rails at zero, every data port cleared and released. The module does this at
    the end of its initialisation and of its shutdown."""
    _dac(apu, "A", 0, 45)
    _dac(apu, "A", 0, 45)
    _dac(apu, "B", 3, 127)
    _dac(apu, "A", 1, 255)
    board.adc_read(apu, VCC_ADC_CHANNEL)
    for channel in (0, 1, 2):
        _dac(apu, "B", channel, 0)
    ports = [3, 4, 5, 6, 7] + list(range(0x0B, 0x40))     # 8, 9 and 0x0A are control ports
    ops = []
    for p, q in zip(ports[0::2], ports[1::2]):
        ops += _w(p, 0x00) + _w(q, 0x00) + _w5([(p, 0xFF), (q, 0xFF)])
    _send(apu, ops + _w(0x00, 0x00) + _delay() + _w(0x00, 0x00) + _delay())


def initialise(apu: Apu):
    """After FPGA configuration: start-up writes, then the idle state. (The module also runs a
    walking-ones self-test of the data ports here, which is omitted.)"""
    board.startup(apu)
    reset(apu)


def data_rows(pcf):
    """[(row, width)] for every data row a device file describes (its signature row excluded)."""
    rows = {}
    for region in pcf.regions:
        if region.kind in (0x100, 0x10):
            continue
        for row in range(region.row, region.row + region.n):
            rows[row] = max(rows.get(row, 0), region.col + region.count)
    return sorted(rows.items())


def signature_row(pcf) -> int:
    for region in pcf.regions:
        if region.kind == 0x10:
            return region.row
    raise ValueError("device file has no signature row")


def read_back(apu: Apu, profile, pcf, vmarg_mv: int = 2000, progress=None):
    """Full native read-back. Returns (signature bytes, {row: [cells]}). The signature row is
    read first, as the vendor flow does; it is returned under its own row number."""
    id_row = signature_row(pcf)
    reader = profile.reader()
    try:
        supply_on(apu, profile=profile)
        read_setup(apu, profile, vmarg_mv=vmarg_mv)
        rows = {id_row: reader.read(apu, id_row, signature=True)}
        for row, width in data_rows(pcf):
            if row == id_row:
                continue
            rows[row] = reader.read(apu, row, width)
            if progress:
                progress(row)
    finally:
        shutdown(apu, profile, reader)
    return profile.signature(rows[id_row]), rows


def silicon_id(apu: Apu, profile, pcf):
    """Native ID read. Returns the signature bytes (ID text first)."""
    reader = profile.reader()
    try:
        supply_on(apu, profile=profile)
        read_setup(apu, profile)
        cells = reader.read(apu, signature_row(pcf), signature=True)
    finally:
        shutdown(apu, profile, reader)
    return profile.signature(cells)


def read_back_epm7160e(apu: Apu, rows_and_widths, vmarg_mv: int = 2000, progress=None):
    """Kept for callers that pass an explicit row list."""
    reader = Epm7160eReader()
    try:
        supply_on(apu)
        read_setup_epm7160e(apu, vmarg_mv=vmarg_mv)
        rows = {0: reader.read(apu, 0)}
        for row, width in rows_and_widths:
            rows[row] = reader.read(apu, row, width)
            if progress:
                progress(row)
    finally:
        shutdown(apu)
    return max7000.silicon_id(rows[0]), rows


def silicon_id_epm7160e(apu: Apu):
    """Full native ID read. Returns (id text, parameter bytes 8..11, final supply DAC code)."""
    try:
        supply_on(apu)
        code = read_setup_epm7160e(apu)
        cells = Epm7160eReader().read(apu, 0)
    finally:
        shutdown(apu)
    return max7000.silicon_id(cells), [max7000.signature_byte(cells, k) for k in range(8, 12)], code


# --- writing (decoded and checked in simulation; NOT yet exercised on a chip) --------------------
def erase_epm7160e(apu: Apu, terase_ms: int, vpp_mv: int, vcc_mv: int = 4750, signature_row: bool = False,
                   sleep=None):
    """Bulk erase, following pgm.dll's standard path: set-up with VCC at most 4.75 V, 2 V margin and
    the chip's Vpp; pulse for the chip's erase time. The supply must already be on (supply_on)
    and the caller shuts down afterwards."""
    import time
    read_setup_epm7160e(apu, vcc_mv=min(vcc_mv, 4750), vpd_mv=min(vpp_mv, 12500), vmarg_mv=2000)
    _send(apu, max7000.erase_start_ops(signature_row))
    (sleep or time.sleep)(terase_ms / 1000.0)
    _send(apu, max7000.erase_end_ops())


def program_epm7160e(apu: Apu, rows, tprog_ms: int, vpp_mv: int, vcc_mv: int = 5000, sleep=None, progress=None):
    """Program lines. `rows` is {row: [cells]} with 16 or 80 cells each; 0 bits are programmed, 1
    bits left alone. Set-up as pgm.dll's row programmer (VCC 5 V, no margin, the chip's Vpp); each
    line gets one pulse of the chip's programming time, ended explicitly here (the vendor module
    leaves the end of the pulse to its next call)."""
    import time
    read_setup_epm7160e(apu, vcc_mv=vcc_mv, vpd_mv=vpp_mv, vmarg_mv=0)
    writer = max7000.LineWriter()
    for row in sorted(rows):
        _send(apu, writer.load_ops(row, rows[row]))
        (sleep or time.sleep)(tprog_ms / 1000.0)
        _send(apu, writer.end_pulse_ops())
        if progress:
            progress(row)


def differences(want, got, mask=0x3FF):
    """Cells where a read-back differs from the intended image: [(row, cell, want, got)]."""
    return [(row, i, a, b) for row in sorted(want) for i, (a, b) in enumerate(zip(want[row], got.get(row, [])))
            if (a ^ b) & mask]


def vpp_from_signature(sig: bytes) -> int:
    """Programming voltage in mV encoded in signature byte 9 for the EPM7160E group (pgm.dll
    0x1003cd1a, 0x1003cdc9): 11250 + 250 * (byte >> 5 & 7). The pulse-time codes in byte 8 are
    not decoded with confidence (their units are unresolved), so times are never taken from the
    chip by this code."""
    return 11250 + 250 * (sig[9] >> 5 & 7)


def cell_masks(pcf):
    """{row: [mask per cell]} of the bits the device file says exist."""
    masks = {}
    for r in pcf.regions:
        if r.kind in (0x100, 0x10):
            continue
        for row in range(r.row, r.row + r.n):
            line = masks.setdefault(row, [])
            line.extend([0] * (r.col + r.count - len(line)))
            for c in range(r.col, r.col + r.count):
                line[c] |= r.mask
    return masks


def plan_program(pcf, image):
    """Lines to write for an image {row: [cells]}: every data line that has at least one 0 bit
    inside the device's masks, with bits outside the masks forced to 1. Returns {row: [cells]}."""
    plan = {}
    for row, masks in sorted(cell_masks(pcf).items()):
        if row not in image:
            raise ValueError("image has no line %d" % row)
        if len(image[row]) != len(masks):
            raise ValueError("image line %d has %d cells, device has %d" % (row, len(image[row]), len(masks)))
        cells = [(v & m) | (max7000.BLANK & ~m) for v, m in zip(image[row], masks)]
        if any(c != max7000.BLANK for c in cells):
            plan[row] = cells
    return plan


def verify(pcf, image, got):
    """Cells whose masked bits differ between an image and a read-back: [(row, cell, want, got)]."""
    out = []
    for row, masks in sorted(cell_masks(pcf).items()):
        for i, m in enumerate(masks):
            if (image[row][i] ^ got[row][i]) & m:
                out.append((row, i, image[row][i] & m, got[row][i] & m))
    return out

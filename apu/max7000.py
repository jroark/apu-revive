"""Native device-level knowledge for MAX 7000 parts (no Altera code at run time)."""


def signature_byte(cells, k: int, cell_width: int = 10) -> int:
    """Byte `k` of the signature ("MS") row, from that row's cells as a line read returns them.

    From pgdmpuz.drv 0x10003a1c (the EPM7160E family of module types): the byte is one bit plane
    across eight cells spaced five apart, most significant bit first.
        first cell = k // (2 * width) + (40 if k is odd else 0)
        bit        = (k >> 1) % width
    Bytes 0-7 are the silicon ID; later bytes carry the device's voltage and timing codes.
    """
    base = k // (2 * cell_width) + (40 if k & 1 else 0)
    bit = (k >> 1) % cell_width
    value = 0
    for j in range(8):
        value = (value << 1) | ((cells[base + 5 * j] >> bit) & 1)
    return value


def silicon_id(cells, cell_width: int = 10) -> str:
    return bytes(signature_byte(cells, k, cell_width) for k in range(8)).decode("latin1")


# --- line read, EPM7160E family (module type 0x3b) -------------------------------------------
# Transcribed from the port operations pgdmpuz.drv issues for a line read (tools/line_ops.py)
# and checked against them op for op. Operations are (kind, port, value): "W" write, "R" read.
LINE_CELLS = 80
READ_PORTS = (0x03, 0x06, 0x07, 0x0C, 0x10, 0x12, 0x15)
# cell bit <- (index into READ_PORTS, port bit), most significant cell bit first
CELL_BITS = ((3, 0), (3, 5), (2, 3), (1, 0), (1, 7), (6, 4), (5, 3), (4, 4), (0, 1), (0, 5))

_W = lambda port, *values: [("W", port, v) for v in values]
# A delay is written as port 1 = C0, a run of no-op commands, port 1 = 00. From the module's delay
# routine (0x10006d40) the run is 750 commands per millisecond, so the unit executes its command
# stream at 750 kHz: 749 no-ops is 1 ms, 74 is 0.1 ms. Waits longer than 1 ms are host-side.
DELAY_LONG, DELAY_SHORT = 749, 74


def _delay(n):
    return [("W", 0x01, 0xC0), ("N", n, None), ("W", 0x01, 0x00)]


_WAIT = _delay(DELAY_SHORT)
_CLOCK = _W(0x03, 0x01, 0x00)


def _precharge(first: bool):
    return (_W(0x10, 0x00) + _W(0x15, 0x00) + _W(0x11, 0x00 if first else 0x20, 0x00)
            + _W(0x06, 0x00) + _W(0x07, 0x00) + _W(0x0D, 0x00)
            + _W(0x0C, 0x00, 0x10, 0x00, 0x10, 0x00, 0x10, 0x00, 0x10, 0x00)
            + _W(0x0D, 0x20, 0xA0) + _W(0x10, 0x02) + _W(0x15, 0x20) + _W(0x11, 0x40, 0x60)
            + _W(0x06, 0x40) + _W(0x07, 0x40) + _W(0x0D, 0xA1) + _W(0x0C, 0x08, 0x18, 0x08)
            + _W(0x0D, 0x81, 0x01))


LINE_RELEASE = _W(0x14, 0x0C) + _W(0x07, 0x00) + _W(0x03, 0x00) + _W(0x10, 0x00)


def line_read_ops(row: int, first: bool = True, cells: int = LINE_CELLS):
    """Port operations that select `row` and read its 80 cells (7 port reads per cell).
    ("N", count, None) entries are delays: that many no-op commands."""
    b = 0x08 if row == 0 else 0x00          # row 0 (signature row) is read with port 3 bit 3 up
    # The first read after a voltage set-up starts with a long wait; later ones start by
    # releasing the lines the previous read left driven.
    ops = (_delay(DELAY_LONG) if first else list(LINE_RELEASE)) + _W(0x11, 0x00) + _W(0x15, 0x00)
    ops += _CLOCK * 110                      # flush the row-select shift register
    # one marker bit, into the chain for rows 0-127 or the one for rows 128 and up
    marker = _W(0x11, 0x04) + _W(0x15, 0x02) if row < 128 else _W(0x11, 0x10) + _W(0x15, 0x00)
    ops += marker + _CLOCK + _W(0x11, 0x00) + _W(0x15, 0x00)
    ops += _CLOCK * (row % 128)              # walk it to the row
    ops += _W(0x0D, 0x00, 0x00)
    for i in range(16):
        ops += _precharge(i == 0)
    ops += (_W(0x10, 0x00) + _W(0x15, 0x00) + _W(0x11, 0x20, 0x00) + _W(0x06, 0x00)
            + _W(0x07, 0x00) + _W(0x0D, 0x00) + _W(0x0C, 0x00))
    if row == 0:
        ops += _W(0x03, 0x08) + _W(0x10, 0x20) + _W(0x03, 0x88)
    else:
        ops += _W(0x07, 0x04) + _W(0x03, 0x80)
    ops += _WAIT + _W(0x14, 0x0D) + _WAIT + _W(0x03, b)
    for cell in range(cells):
        ops += [("R", p, None) for p in READ_PORTS]
        if cell == cells - 1:
            break
        if cell % 16 == 15:
            ops += (_W(0x14, 0x0C) + _WAIT + _W(0x0C, 0x10, 0x00) + _WAIT
                    + _W(0x03, 0x80 | b) + _WAIT + _W(0x14, 0x0D) + _WAIT + _W(0x03, b))
        else:
            ops += _W(0x03, 0x80 | b, b)
    return ops


def cells_from_reads(reads, count: int = LINE_CELLS):
    """Turn the 7 bytes read per clock into the line's cell values.

    Clock s yields the cell at index count + s // 16 - 5 * (s % 16 + 1) for an 80-cell line and
    15 - s for a 16-cell line (module 0x1000ca70)."""
    cells = [0] * count
    for s in range(count):
        chunk = reads[7 * s:7 * s + 7]
        value = 0
        for idx, bit in CELL_BITS:
            value = (value << 1) | ((chunk[idx] >> bit) & 1)
        cells[15 - s if count == 16 else count + s // 16 - 5 * (s % 16 + 1)] = value
    return cells


def words(ops):
    """Command words for a list of operations."""
    from . import protocol
    out = []
    for kind, port, value in ops:
        if kind == "N":
            out += [protocol.word(protocol.OP_NOP)] * port
        elif kind == "R":
            out.append(protocol.word(protocol.OP_READ, port))
        elif kind == "W5":
            out.append(protocol.word(protocol.OP_WRITE5, port, value))
        else:
            out.append(protocol.word(protocol.OP_WRITE, port, value))
    return out


# --- line write, EPM7160E family ---------------------------------------------------------------
# Decoded from the module's traffic in simulation (tools/sim_capture.py). The module keeps a
# shadow of each port and changes one line at a time, so every step below is a whole-port write.
# cell bit i -> (port, bit) of the line that carries it while the cell is clocked in
WRITE_BITS = ((0x0D, 5), (0x0D, 7), (0x10, 1), (0x15, 5), (0x11, 6), (0x11, 5), (0x06, 6), (0x07, 6),
              (0x0D, 0), (0x0C, 3))
PROGRAM_STROBE = (0x0D, 4)
BLANK = 0x3FF


class LineWriter:
    """Builds the operations for successive line writes, tracking the port state as the module
    does. `state` is "setup" straight after a voltage set-up, "pulse" while a programming pulse
    is running, "read" after a line read."""

    def __init__(self):
        self.shadow = {}
        self.state = "setup"

    def _set(self, ops, port, bit, value):
        v = self.shadow.get(port, 0)
        v = v | (1 << bit) if value else v & ~(1 << bit)
        self.shadow[port] = v
        ops.append(("W", port, v))

    def end_pulse_ops(self):
        """Stop a running programming pulse."""
        ops = [("W", 0x14, 0x0D)]
        self._set(ops, *PROGRAM_STROBE, 0)
        ops += [("W", 0x14, 0x0C), ("W", 0x0C, 0x18), ("W", 0x0C, 0x08)]
        self.shadow[0x0C] = 0x08
        self.state = "idle"
        return ops

    def load_ops(self, row: int, cells):
        """Select `row`, shift its cells in (80 or 16 values; 0 bits get programmed) and start the
        programming pulse. The caller waits the pulse time, then sends end_pulse_ops() or the
        next load_ops(), which ends the pulse first."""
        if len(cells) not in (16, LINE_CELLS):
            raise ValueError("a line is 16 or 80 cells")
        if self.state == "setup":
            ops = _delay(DELAY_LONG)
        elif self.state == "pulse":
            ops = self.end_pulse_ops()
        elif self.state == "read":
            ops = _W(0x14, 0x0C) + _W(0x07, 0x00) + _W(0x03, 0x00) + _W(0x10, 0x00)
            self.shadow.update({0x07: 0, 0x10: 0, 0x11: 0, 0x15: 0, 0x0D: 0, 0x06: 0, 0x0C: 0})
        else:
            ops = []
        # clear both marker lines, flush the chains, clock one marker bit in, walk it to the row
        v11 = self.shadow.get(0x11, 0) & ~0x14
        v15 = self.shadow.get(0x15, 0) & ~0x02
        self.shadow[0x11], self.shadow[0x15] = v11, v15
        ops += _W(0x11, v11) + _W(0x15, v15) + _CLOCK * 110
        ops += _W(0x11, v11 | (0x04 if row < 128 else 0x10)) + _W(0x15, v15 | (0x02 if row < 128 else 0))
        ops += _CLOCK + _W(0x11, v11) + _W(0x15, v15) + _CLOCK * (row % 128)
        if len(cells) == 16:
            line = [BLANK] * LINE_CELLS
            for k in range(16):
                line[5 * k + 4] = cells[15 - k]
        else:
            line = list(reversed(cells))
        for value in line:
            for i, (port, bit) in enumerate(WRITE_BITS[:9]):
                self._set(ops, port, bit, (value >> i) & 1)
            d = ((value >> 9) & 1) << 3
            ops += _W(0x0C, d, d | 0x10, d)
            self.shadow[0x0C] = d
        ops.append(("W", 0x14, 0x0D))
        self._set(ops, *PROGRAM_STROBE, 1)
        ops.append(("W", 0x14, 0x09))
        self.state = "pulse"
        return ops


# --- bulk erase, EPM7160E family -----------------------------------------------------------------
def erase_start_ops(signature_row: bool = False):
    """Start the erase pulse (module pgd_bulk_erase, straight after a voltage set-up).
    signature_row=True is the manufacturer's erase, which also clears the signature row and so
    the chip's ID and its voltage and timing codes."""
    return (_delay(DELAY_LONG) + _W(0x0D, 0x10, 0x12) + _W(0x0C, 0x80 if signature_row else 0x00)
            + _W(0x01, 0xC0, 0x00) + _W(0x14, 0x08))


def erase_end_ops():
    """End the erase pulse (what the module sends at the start of its next call, for the pulse
    argument 0x1000A that pgm.dll uses)."""
    return (_W(0x14, 0x0C) + _delay(6) + _W(0x07, 0x04) + _W(0x01, 0xC0, 0x00) + _W(0x0D, 0x10)
            + _W(0x0C, 0x00) + _W(0x01, 0xC0, 0x00) + _W(0x0D, 0x00) + _W(0x07, 0x00))


# --- EPM7064 family (module type 0x0d): line read ------------------------------------------------
# From the module's traffic in simulation. Four-bit cells; the row address is presented in
# parallel instead of being clocked into a chain.
READ_PORTS_7064 = (0x03, 0x10, 0x07, 0x0C)
CELL_BITS_7064 = ((3, 0), (2, 4), (1, 3), (0, 5))          # most significant cell bit first
# row address bit -> (port, bit)
ROW_ADDRESS_7064 = ((0x15, 3), (0x15, 2), (0x15, 1), (0x11, 6), (0x11, 5), (0x11, 2), (0x11, 1), (0x11, 0))
_PRECHARGE_7064 = (_W(0x07, 0x00) + _W(0x0B, 0x10, 0x00) * 4 + _W(0x10, 0x40) + _W(0x0B, 0x20, 0x60)
                   + _W(0x07, 0x02) + _W(0x0B, 0x70, 0x60) + _W(0x10, 0x00) + _W(0x0B, 0x40, 0x00))
LINE_RELEASE_7064 = _W(0x0C, 0x00) + _W(0x0D, 0x00) + _W(0x06, 0x00)


def line_read_ops_7064(row: int, first: bool = True, cells: int = LINE_CELLS, hi15: int = 0,
                       signature: bool = False):
    """Port operations that select `row` and read its cells, four port reads per cell.

    Rows 128 and up set port 0x11 bit 0 and carry address bits 5 and 6 on port 0x15 bits 7 and
    5, written in a second step; the module leaves those two lines as they were for later
    lower rows, so the caller passes the current value as `hi15` (see hi15_after_7064).
    `signature` selects the signature row's read mode (port 6 = 0x50 in place of port 0x0D = 2).
    """
    if not 0 <= row < 256:
        raise ValueError("row out of range")
    low = sum(1 << bit for i, (port, bit) in enumerate(ROW_ADDRESS_7064[:3]) if row >> i & 1)
    a11 = sum(1 << bit for i, (port, bit) in enumerate(ROW_ADDRESS_7064) if port == 0x11 and row >> i & 1)
    short = _delay(4)
    ops = _delay(DELAY_LONG) if first else list(LINE_RELEASE_7064)
    if row < 128:
        ops += _W(0x15, low | hi15) + _W(0x11, a11)
    else:
        ops += (_W(0x15, low | hi15) + _W(0x11, 0x01 | (a11 & 0x60))
                + _W(0x15, low | hi15_after_7064(row, hi15)))
    ops += _W(0x10, 0x00) + _W(0x0B, 0x00, 0x00)
    ops += _PRECHARGE_7064 * 16
    ops += _W(0x07, 0x00) + (_W(0x06, 0x50) if signature else _W(0x0D, 0x02))
    ops += _W(0x03, 0x80) + short + _W(0x0C, 0x04) + short + _W(0x03, 0x00)
    for cell in range(cells):
        ops += [("R", p, None) for p in READ_PORTS_7064]
        if cell == cells - 1:
            break
        if cell % 16 == 15:
            ops += (_W(0x0C, 0x00) + short + _W(0x0B, 0x10, 0x00) + short + _W(0x03, 0x80) + short
                    + _W(0x0C, 0x04) + short + _W(0x03, 0x00))
        else:
            ops += _W(0x03, 0x80, 0x00)
    return ops


def hi15_after_7064(row: int, hi15: int) -> int:
    """State of the two upper address lines on port 0x15 after reading `row`."""
    if row < 128:
        return hi15
    return (0x80 if row & 0x20 else 0) | (0x20 if row & 0x40 else 0)


def cells_from_reads_7064(reads, count: int = LINE_CELLS):
    cells = [0] * count
    for s in range(count):
        chunk = reads[4 * s:4 * s + 4]
        value = 0
        for idx, bit in CELL_BITS_7064:
            value = (value << 1) | ((chunk[idx] >> bit) & 1)
        cells[15 - s if count == 16 else count + s // 16 - 5 * (s % 16 + 1)] = value
    return cells


def signature_byte_7064(cells, k: int) -> int:
    """Signature byte k for the older device group (module 0x10003938): bit k // 10 of the eight
    cells 8k .. 8k + 7, the highest-numbered cell first."""
    bit = k // 10
    value = 0
    for j in range(7, -1, -1):
        value = (value << 1) | ((cells[8 * k + j] >> bit) & 1)
    return value


# --- EPM7032 family (module type 0x0a): line read ------------------------------------------------
# From the module's traffic in simulation. Two-bit cells read from one port.
READ_PORT_7032 = 0x07
CELL_BITS_7032 = (4, 3)                        # port bits, most significant cell bit first
_CLOCK_7032 = (_W(0x02, 0x40) + [("W5", 0x03, 0x03)] + _W(0x03, 0x82) + [("W5", 0x03, 0x83)] + _W(0x02, 0x00)
               + _W(0x09, 0x40) + [("W5", 0x03, 0x03)] + _W(0x03, 0x02) + [("W5", 0x03, 0x83)] + _W(0x09, 0x00))


def row_address_7032(row: int):
    """(port 0x0C value, port 0x0B value) for a row: bits 0, 1 on 0C.1, 0C.0 and bits 2-6 on
    0B.7 down to 0B.3."""
    if not 0 <= row < 128:
        raise ValueError("row out of range")
    a0c = (row & 1) << 1 | (row >> 1 & 1)
    a0b = sum(0x80 >> i for i in range(5) if row >> (2 + i) & 1)
    return a0c, a0b


def line_read_ops_7032(row: int, first: bool = True, cells: int = LINE_CELLS, previous_row: int = 0,
                       signature: bool = False):
    """Port operations that select `row` and read its cells (one read of port 7 per cell). On a
    repeat read the module first rewrites port 0x0C with the previous row's address bits, so the
    caller passes `previous_row`. `signature` selects the signature row's read mode (port 6 = 1
    in place of raising port 0x0C bit 2)."""
    a0c, a0b = row_address_7032(row)
    short = _delay(4)
    if first:
        ops = _delay(DELAY_LONG)
    else:
        ops = _W(0x05, 0x00) + _W(0x0C, row_address_7032(previous_row)[0]) + _W(0x06, 0x00)
    ops += _W(0x0C, a0c) + _W(0x0B, a0b) + _W(0x06, 0x00) + _W(0x0C, a0c)
    for pulses in ([4] * 16 if cells == LINE_CELLS else [0, 8] * 8):
        ops += _W(0x06, 0x10, 0x00) * pulses
        ops += _W(0x06, 0x20) + _W(0x0C, a0c | 8) + _W(0x06, 0x30, 0x20, 0x00) + _W(0x0C, a0c)
    ops += (_W(0x06, 0x01) if signature else _W(0x0C, a0c | 4)) + _CLOCK_7032 + short + _W(0x05, 0x20)
    for cell in range(cells):
        ops.append(("R", READ_PORT_7032, None))
        if cell == cells - 1:
            break
        if cell % 16 == 15:
            keep = 0x01 if signature else 0x00          # the signature-mode line stays up
            ops += (_W(0x05, 0x00) + short + _W(0x06, 0x10 | keep, keep) + short + _CLOCK_7032 + short
                    + _W(0x05, 0x20))
        else:
            ops += _CLOCK_7032
    return ops


def cells_from_reads_7032(reads, count: int = LINE_CELLS):
    cells = [0] * count
    for s in range(count):
        value = (reads[s] >> CELL_BITS_7032[0] & 1) << 1 | (reads[s] >> CELL_BITS_7032[1] & 1)
        cells[15 - s if count == 16 else count + s // 16 - 5 * (s % 16 + 1)] = value
    return cells

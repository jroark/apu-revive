"""Altera Programmer Object File (.pof) reader and writer.

Layout recovered from MAX+PLUS II 10.2's p2j.dll (POF to JEDEC converter) and GE.dll:
    "POF\\0", u32 version, u32 packet count, then packets: u16 tag, u32 length, `length` bytes.
Tags (the converter dispatches on tag - 1):
     1 Creator_Id (text)          2 Device_Name (text)        3 Comment_Text (text)
     4 Turbo_Bit (u16)            5 Security_Bit (u16)
     6 logical address and data, 16-bit: u16 start_address, representation_width,
       address_count, flags; data
     7 electrical address and data: u16 start_col, start_row, col_count, row_count, mask,
       flags; data  (the row / column / mask addressing the programmer itself uses)
     8 terminator: u16 CRC         9 Symbol_Table             10 Test_Vectors
    17 logical address and data, 32-bit: u32 start_address, u16 representation_width,
       u32 address_count, u16 flags; data
    18 a further u16 value (name not recovered)
CRC (GE.dll ge_crc_*): 16-bit, initial 0xFFFF, reflected polynomial 0x8408, inverted at the end
(CRC-16/X.25).

Confirmed from the code: the header is the 6-byte magic "POF\\0\\0\\0" and a u16 version that
must be 1 (GE.dll's file-type table; read here as one u32, 0x00010000), and the converter runs
the CRC over every byte it reads, from offset 0 through the terminator packet's tag and length.
Not confirmed (no POF ships with the package to test against): how electrical data bytes are
packed into cells.
"""
import struct
from dataclasses import dataclass, field

MAGIC = b"POF\0"
TAG_CREATOR, TAG_DEVICE, TAG_COMMENT, TAG_TURBO, TAG_SECURITY = 1, 2, 3, 4, 5
TAG_LOGICAL16, TAG_ELECTRICAL, TAG_TERMINATOR, TAG_SYMBOLS, TAG_VECTORS, TAG_LOGICAL32 = 6, 7, 8, 9, 10, 17
TAG_NAMES = {1: "Creator_Id", 2: "Device_Name", 3: "Comment_Text", 4: "Turbo_Bit", 5: "Security_Bit",
             6: "Logical_Address_and_Data_16", 7: "Electrical_Address_and_Data", 8: "Terminator",
             9: "Symbol_Table", 10: "Test_Vectors", 17: "Logical_Address_and_Data_32"}
DEFAULT_VERSION = 0x00010000     # bytes 00 00 01 00: magic padding, then version 1


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """Running CRC as GE.dll keeps it; call finish_crc() for the stored value."""
    for byte in data:
        for _ in range(8):
            bit = (crc ^ byte) & 1
            crc >>= 1
            if bit:
                crc ^= 0x8408
            byte >>= 1
    return crc


def finish_crc(crc: int) -> int:
    return ~crc & 0xFFFF


@dataclass
class Electrical:
    start_col: int
    start_row: int
    col_count: int
    row_count: int
    mask: int
    flags: int
    data: bytes

    def pack(self) -> bytes:
        return struct.pack("<6H", self.start_col, self.start_row, self.col_count, self.row_count,
                           self.mask, self.flags) + self.data


@dataclass
class Logical:
    start_address: int
    representation_width: int
    address_count: int
    flags: int
    data: bytes
    wide: bool = False

    def pack(self) -> bytes:
        if self.wide:
            return struct.pack("<IHIH", self.start_address, self.representation_width,
                               self.address_count, self.flags) + self.data
        return struct.pack("<4H", self.start_address, self.representation_width, self.address_count,
                           self.flags) + self.data


@dataclass
class Pof:
    version: int = DEFAULT_VERSION
    packets: list = field(default_factory=list)       # [(tag, payload bytes)] without the terminator
    stored_crc: int = None
    crc_ok: bool = None

    # -- access ------------------------------------------------------------------------------
    def _text(self, tag):
        for t, payload in self.packets:
            if t == tag:
                return payload.split(b"\0")[0].decode("latin1")
        return None

    creator = property(lambda self: self._text(TAG_CREATOR))
    device = property(lambda self: self._text(TAG_DEVICE))
    comment = property(lambda self: self._text(TAG_COMMENT))

    def flag(self, tag):
        for t, payload in self.packets:
            if t == tag and len(payload) >= 2:
                return struct.unpack_from("<H", payload)[0]
        return None

    def electrical(self):
        return [Electrical(*struct.unpack_from("<6H", p), p[12:]) for t, p in self.packets if t == TAG_ELECTRICAL]

    def logical(self):
        out = []
        for t, p in self.packets:
            if t == TAG_LOGICAL16:
                out.append(Logical(*struct.unpack_from("<4H", p), p[8:]))
            elif t == TAG_LOGICAL32:
                out.append(Logical(*struct.unpack_from("<IHIH", p), p[12:], wide=True))
        return out

    # -- building ----------------------------------------------------------------------------
    def add_text(self, tag, text):
        self.packets.append((tag, text.encode("latin1") + b"\0"))

    def add_flag(self, tag, value):
        self.packets.append((tag, struct.pack("<H", value)))

    def add(self, record):
        tag = TAG_ELECTRICAL if isinstance(record, Electrical) else (TAG_LOGICAL32 if record.wide else TAG_LOGICAL16)
        self.packets.append((tag, record.pack()))

    def to_bytes(self) -> bytes:
        out = bytearray(MAGIC + struct.pack("<II", self.version, len(self.packets) + 1))
        for tag, payload in self.packets:
            out += struct.pack("<HI", tag, len(payload)) + payload
        out += struct.pack("<HI", TAG_TERMINATOR, 2)
        return bytes(out) + struct.pack("<H", finish_crc(crc16(bytes(out))))

    @classmethod
    def from_bytes(cls, data: bytes):
        if data[:4] != MAGIC:
            raise ValueError("not a POF file")
        version, count = struct.unpack_from("<II", data, 4)
        pof, off = cls(version=version), 12
        for _ in range(count):
            if off + 6 > len(data):
                raise ValueError("truncated POF")
            tag, length = struct.unpack_from("<HI", data, off)
            payload = data[off + 6:off + 6 + length]
            if len(payload) != length:
                raise ValueError("truncated POF packet")
            if tag == TAG_TERMINATOR:
                pof.stored_crc = struct.unpack_from("<H", payload)[0] if length >= 2 else None
                pof.crc_ok = pof.stored_crc == finish_crc(crc16(data[:off + 6]))
                break
            pof.packets.append((tag, payload))
            off += 6 + length
        return pof


# --- electrical data <-> read-back image ----------------------------------------------------
# p2j.dll (0x100028f1, 0x10002760) pairs each electrical packet with one 20-byte region record of
# the device file by comparing all six header fields, then copies the payload byte for byte:
# line after line, cell after cell, a fixed number of bytes per cell. Altera calls the line
# address the "column" and the position along the line the "row", so
#     start_col = first line, start_row = first cell, col_count = lines, row_count = cells per
#     line, mask = valid cell bits, flags = the region's kind.
# Assumed, not confirmed: two bytes per cell for these devices, low byte first.
CELL_BYTES = 2
IMAGE_KINDS = (0x200, 0x20)        # data regions; which kinds a real POF carries is unconfirmed


def image_to_pof(regions, rows, device: str, creator: str = "apu-revive", kinds=IMAGE_KINDS) -> Pof:
    """Build a POF from a read-back image. `regions` are apu.pcf Region records, `rows` is
    {line: [cells]}."""
    out = Pof()
    out.add_text(TAG_CREATOR, creator)
    out.add_text(TAG_DEVICE, device)
    for r in regions:
        if r.kind not in kinds:
            continue
        data = bytearray()
        for line in range(r.row, r.row + r.n):
            cells = rows[line][r.col:r.col + r.count]
            if len(cells) != r.count:
                raise ValueError("image line %d is too short for the device's region" % line)
            for value in cells:
                data += struct.pack("<H", value & r.mask)
        out.add(Electrical(r.row, r.col, r.n, r.count, r.mask & 0xFFFF, r.kind & 0xFFFF, bytes(data)))
    return out


def pof_to_image(pof: Pof, blank: int = 0x3FF):
    """{line: [cells]} from a POF's electrical packets. Cells no packet covers are `blank`;
    where packets overlap on a line, each contributes only the bits in its mask."""
    rows = {}
    for e in pof.electrical():
        if len(e.data) != e.col_count * e.row_count * CELL_BYTES:
            raise ValueError("electrical packet payload has an unexpected size")
        values = struct.unpack("<%dH" % (len(e.data) // 2), e.data)
        for i in range(e.col_count):
            line = rows.setdefault(e.start_col + i, [])
            need = e.start_row + e.row_count
            line.extend([blank] * (need - len(line)))
            for j in range(e.row_count):
                k = e.start_row + j
                line[k] = (line[k] & ~e.mask) | (values[i * e.row_count + j] & e.mask)
    return rows

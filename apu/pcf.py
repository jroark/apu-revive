"""Parser for MAX+PLUS II programmer device files (.pcf), as far as decoded.

Layout (see re/protocol.md): 8-byte file header 'PCF\\0' + version; section offsets at 0x1c
are relative to the end of that header.
  section 0: 20-byte region records
  section 1: expected silicon IDs (UTF-16)
  section 2: one 0xEC-byte voltage record per variant (16-bit millivolt fields)
  section 3: not decoded (empty for the EPM7064)
  section 4: 16-byte JEDEC map records, count at 0x14. The kind-0x100 region record holds
             the total fuse count as a 32-bit value.
"""
import struct
from dataclasses import dataclass

HDR = 8
VOLT_REC = 0xEC
KIND_ID = 0x10

# Byte offsets of the 16-bit fields in a voltage/algorithm record, with the names pgm.dll uses
# for them (recovered from its parameter-dump routine at 0x1000a94c). Voltages are millivolts.
FIELDS = {
    "vcc": 0x04, "vpd": 0x06, "vpg": 0x08,
    "vcc_siid": 0x0A, "vmarg_siid": 0x0C, "vpd_siid": 0x0E, "vpg_siid": 0x10,
    "vcc_examine": 0x12, "vmarg_examine": 0x14, "vpd_examine": 0x16, "vpg_examine": 0x18,
    "vcc_low_verify": 0x1A, "vmarg_low_verify": 0x1C, "vmarg_low_verify2": 0x1E, "vpd_low_verify": 0x20, "vpg_low_verify": 0x22,
    "vcc_high_verify": 0x24, "vmarg_high_verify": 0x26, "vmarg_high_verify2": 0x28, "vpd_high_verify": 0x2A, "vpg_high_verify": 0x2C,
    "vcc_blank_check": 0x2E, "vmarg_blank_check": 0x30, "vmarg_blank_check2": 0x32, "vpd_blank_check": 0x34, "vpg_blank_check": 0x36,
    "vcc_final_verify": 0x80, "vmarg_final_verify": 0x82, "vmarg_final_verify2": 0x84, "vpd_final_verify": 0x86, "vpg_final_verify": 0x88,
    "prog1_pulse_length": 0x8A, "prog1_min_pulses": 0x8C, "prog1_max_pulses": 0x8E,
    "prog_passes": 0xC2, "max_remedial_passes": 0xC4,
    "security_pulse_length": 0xC6, "security_pulse_count": 0xC8, "security_type": 0xCA,
    "turbo_detect": 0xD4, "margin_mode_type": 0xD6, "jedec_table": 0xD8, "exception_table": 0xDA,
    "device_technology": 0xDC,
    "bulk_prog_pulse_length": 0xDE, "bulk_prog_max_pulses": 0xE0, "bulk_prog_abort_on_fail": 0xE2,
    "bulk_erase_pulse_length": 0xE4, "bulk_erase_max_pulses": 0xE6, "bulk_erase_abort_on_fail": 0xE8,
}
# Program/challenge voltage sets: vcc, vmarg, vpd, vpg at 0x38 + 8*k for
# prog1_aim, prog1_challenge, prog2_aim, prog2_challenge, prog3_aim, prog3_challenge,
# reprog_aim, reprog_challenge, last_chance (k = 0..8).


@dataclass(frozen=True)
class Region:
    kind: int
    row: int      # first row
    col: int      # first column
    n: int        # number of rows
    count: int    # number of columns
    mask: int     # valid data bits per cell


@dataclass(frozen=True)
class JedecRun:
    row: int      # row of the first fuse; following fuses are on following rows
    col: int
    count: int
    mask: int     # bit within the cell
    flags: int    # 0, 2 or 4 seen; meaning unknown


class Pcf:
    """One variant block of a .pcf (see `load_all`). `Pcf.load(path)` returns the first block,
    which is variant 0."""

    def __init__(self, data: bytes, base: int = HDR):
        if data[:3] != b"PCF":
            raise ValueError("not a .pcf file")
        self.data = data
        self.base = base                                   # file offset of this block's header
        self.block_len = struct.unpack_from("<I", data, base)[0]
        self.variants = struct.unpack_from("<H", data, base + 6)[0]   # field at +6; see load_all
        self.offs = [o + base for o in struct.unpack_from("<5I", data, base + 0x14)]
        o = self.offs
        self.regions = [
            Region(kind, row, col, n, count, mask)
            for row, col, n, count, mask, kind, _ in struct.iter_unpack("<HHHHIII", data[o[0]:o[1]])
        ]
        self.id_text = data[o[1]:o[2]].decode("utf-16-le", "replace")
        njed = struct.unpack_from("<H", data, base + 0x0C)[0]
        self.jedec_map = [
            JedecRun(row, col, count, mask, flags)
            for row, col, count, _, mask, flags in struct.iter_unpack("<HHHHII", data[o[4]:o[4] + 16 * njed])
        ]
        self.fuse_count = sum(r.count for r in self.jedec_map)
        self.declared_fuses = next((r.n | r.count << 16 for r in self.regions if r.kind == 0x100), None)

    @classmethod
    def load_all(cls, path):
        """All variant blocks in the file, in order (index = pgm.dll's variant number).

        A .pcf is an 8-byte file header followed by blocks, each starting with its own length.
        pgm.dll's loader walks them the same way (EPM7064: blocks at 8, 11788, 23526, 38086).
        """
        with open(path, "rb") as f:
            data = f.read()
        blocks, pos = [], HDR
        while pos + 0x30 <= len(data):
            blk = cls(data, pos)
            if blk.block_len < 0x30:
                break
            blocks.append(blk)
            pos += blk.block_len
        return blocks

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())

    def volts(self, variant: int, offset: int, n: int = 4):
        """n consecutive millivolt fields at byte offset `offset` of a variant's voltage record."""
        return struct.unpack_from(f"<{n}H", self.data, self.offs[2] + VOLT_REC * variant + offset)

    def field(self, name: str, variant: int = 0) -> int:
        return self.volts(variant, FIELDS[name], 1)[0]

    def nominal_vcc(self, variant=0):
        return self.volts(variant, 4, 1)[0]

    def siid_volts(self, variant=0):
        return self.volts(variant, 0x0A)

    def fuses(self):
        """Yield (fuse_number, row, col, mask) for every JEDEC fuse, in order."""
        n = 0
        for run in self.jedec_map:
            for i in range(run.count):
                yield n, run.row + i, run.col, run.mask
                n += 1

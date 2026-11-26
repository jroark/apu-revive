"""Reader for MAX+PLUS II device assembler files (.daf) — the fuse-level device description.

Container layout from GE.dll's loader (0x10017f76); the meaning of most fields is not yet known.
    +0     "DAF\\0\\0\\0", u16 version
    +8     0x8C-byte header of 35 u32 fields (HEADER_NAMES; also reachable by byte offset)
    then   h[0x28] + 1 element records, then h[0x2C] + 1 more, each:
               nine u32 fields f0..f8
               f8 + 1 u32                       ("a")
               f5 + 1 entries of four u32       ("b")
               f6 + 1 rows of f7 + 1 u32        ("c")
    then   seventeen counted tables whose lengths come from header fields (see TAIL below)
"""
import struct
from dataclasses import dataclass, field

HEADER_BYTES = 0x8C
# Names from asm.dll's own ASCII dump routine ("ascidaf.dmp"), which prints every field of the
# structure, and the order from where asm.dll keeps each field (it copies the header to globals
# at 0x10023720; the dump prints them in a slightly different order). "wl" is a word line: an
# input column of the logic array. A product term's fuses start at
#   (block - 1) * lab_offset + (mc_pterm[macrocell] + term * mc_pt_offset) * pt_offset
# and the fuse for word line w is w * wl_offset further on.
HEADER_NAMES = ("total_bits", "wl_offset", "pia_wl_offset", "pt_offset", "mc_pt_offset", "lab_offset",
                "pia_offset", "lab_pia_wls", "pia_wls", "pt_wls", "arch1_bits", "arch2_bits", "n_pins",
                "n_mcs", "n_labs", "n_data_bits", "n_default_groups", "n_jedec_groups", "n_vpterms",
                "n_jedec_bits", "pterms_per_mc", "exp_per_lab", "exp_per_mc", "lab_clocks", "lab_clears",
                "initial_arr_val", "first_pia_addr", "por_wl_1", "por_wl_2", "assemble_mux",
                "assemble_por", "jedec_defined", "bit_matches_jed", "bit_shift", "bit_mask")
# Element ("arch_hdr") fields, and the three arrays that follow them: select[], arch_vpterm[]
# (input_eq_type, vpterm, condition, setting) and arch_addr[][] (fuse addresses).
ELEMENT_NAMES = ("arch_type", "arch_value", "num_bits", "offset", "index", "num_vpterm", "addr_rows",
                 "addr_columns", "num_select")
# (name, header offset giving the count, entry size in u32, +1?) in file order.
TAIL = (("default_group", 0x40, 4, 1), ("bit_jed_conv", 0x44, 8, 1), ("oe_pin", None, 2, 0),
        ("io_wl", 0x30, 1, 1), ("mc_wl", 0x34, 1, 1), ("exp_wl", 0x54, 1, 1),
        ("pia_wl", 0x1C, 1, 1), ("io_pia_wl", 0x30, 1, 1), ("mc_pia_wl", 0x34, 1, 1),
        ("mc_lab", 0x34, 1, 1), ("mc_data_bit", 0x34, 1, 1), ("pia_data_bit", 0x38, 1, 1),
        ("pia_addr", 0x1C, 1, 1), ("pia_wl_polarity", 0x20, 1, 0), ("vpterm_array", 0x48, 2, 1),
        ("mc_pterm", 0x34, 1, 1), ("exp_pterm", 0x54, 1, 1), ("exp_data_bit", 0x54, 1, 1),
        ("pin_mc", 0x30, 1, 1), ("output_mc", 0x34, 1, 1), ("bit_tab", 0x3C, 1, 0))


@dataclass
class Element:
    fields: tuple
    a: tuple
    b: list
    c: list


@dataclass
class Daf:
    version: int
    header: tuple                       # 35 u32; header[i] is the field at byte offset 4 * i
    group1: list = field(default_factory=list)
    group2: list = field(default_factory=list)
    tables: dict = field(default_factory=dict)
    trailing: int = 0                   # bytes left after the last table (0 when fully consumed)

    def h(self, offset: int) -> int:
        return self.header[offset // 4]

    @property
    def fuse_count(self) -> int:
        return self.h(0)

    def named(self) -> dict:
        return dict(zip(HEADER_NAMES, self.header))


class _Reader:
    def __init__(self, data, pos):
        self.data, self.pos = data, pos

    def u32(self, n):
        if n < 0 or self.pos + 4 * n > len(self.data):
            raise ValueError("DAF structure runs past the end of the file")
        out = struct.unpack_from("<%dI" % n, self.data, self.pos)
        self.pos += 4 * n
        return out


def _element(r):
    f = r.u32(9)
    a = r.u32(f[8] + 1)
    b = [r.u32(4) for _ in range(f[5] + 1)]
    c = [r.u32(f[7] + 1) for _ in range(f[6] + 1)]
    return Element(f, a, b, c)


def parse(data: bytes) -> Daf:
    if data[:4] != b"DAF\0":
        raise ValueError("not a DAF file")
    version = struct.unpack_from("<H", data, 6)[0]
    r = _Reader(data, 8)
    daf = Daf(version, r.u32(HEADER_BYTES // 4))
    daf.group1 = [_element(r) for _ in range(daf.h(0x28) + 1)]
    daf.group2 = [_element(r) for _ in range(daf.h(0x2C) + 1)]
    for name, offset, width, plus in TAIL:
        count = 1 if offset is None else daf.h(offset) + plus
        if name in ("pia_wl_polarity",) and daf.h(offset) <= 0:
            count = 0
        daf.tables[name] = [r.u32(width) if width > 1 else r.u32(1)[0] for _ in range(count)]
    daf.trailing = len(data) - r.pos
    return daf


def fuse_groups(daf: Daf):
    """Per-macrocell option fuses: [(element id, fuses per macrocell, {macrocell: (fuse, ...)})]
    for every group-1 element that has a fuse matrix. Macrocells are numbered from 1."""
    out = []
    for e in daf.group1:
        rows = {m: tuple(v for v in row[1:] if v != 0xFFFFFFFF) for m, row in enumerate(e.c) if m}
        rows = {m: v for m, v in rows.items() if v}
        if rows:
            out.append((e.fields[0], e.fields[7], rows))
    return out


def summary(daf: Daf) -> dict:
    n = daf.named()
    return {"fuses": n["total_bits"], "pins": n["n_pins"], "macrocells": n["n_mcs"], "labs": n["n_labs"],
            "pterms_per_mc": n["pterms_per_mc"], "pt_wls": n["pt_wls"], "lab_offset": n["lab_offset"],
            "exp_per_lab": n["exp_per_lab"], "option_elements": [(eid, w) for eid, w, _ in fuse_groups(daf)]}
